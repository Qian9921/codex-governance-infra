"""Strict, privacy-safe readiness gate for the mandatory Codex toolchain.

The lightweight health report in :mod:`codex.v16.tool_routing` answers whether
tools appear to be present.  This module answers the stronger question required
before formal repository work: are CodeGraph, Semble, and rtk functional for
this exact repository identity, and is the Codex MCP configuration present?

The doctor is deliberately read-only.  It never installs a package, changes
Codex configuration, builds/synchronizes an index, or writes a cache.  Reports
contain hashes and normalized reason codes rather than command output, absolute
paths, environment variables, prompts, or credentials.
"""

from __future__ import annotations

import hashlib
import json
import os
import pathlib
import re
import shutil
import subprocess
from collections.abc import Callable, Mapping, Sequence
from typing import Any


SCHEMA = "tool-preflight.v16"
MANDATORY_TOOLS = ("codegraph", "semble", "rtk")
STATUSES = frozenset({"pass", "fail"})
REPORT_FIELDS = frozenset(
    {
        "schema",
        "status",
        "strict",
        "repo_identity",
        "config_identity",
        "tools",
        "counts",
        "denominator",
        "denominator_known",
        "cache",
        "mutations",
    }
)
TOOL_FIELDS = frozenset(
    {"tool", "status", "reason_code", "version", "checks", "evidence_sha256"}
)
CHECK_FIELDS = frozenset({"name", "status", "reason_code"})
COUNT_FIELDS = frozenset(
    {"total", "ran", "passed", "failed", "skipped", "xfail", "unknown"}
)
REPO_FIELDS = frozenset(
    {"root_sha256", "head_sha", "dirty", "worktree_sha256"}
)
CONFIG_FIELDS = frozenset(
    {"path_sha256", "content_sha256", "present"}
)
CACHE_FIELDS = frozenset(
    {"key_sha256", "invalidated_by"}
)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_HEAD = re.compile(r"^[0-9a-f]{40}$")


class PreflightError(ValueError):
    """Raised when a preflight report or input violates the contract."""


Runner = Callable[[Sequence[str], pathlib.Path, float], subprocess.CompletedProcess[str]]


def _sha256(value: bytes | str) -> str:
    if isinstance(value, str):
        value = value.encode("utf-8")
    return hashlib.sha256(value).hexdigest()


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _default_runner(
    argv: Sequence[str], cwd: pathlib.Path, timeout_sec: float
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(argv),
        cwd=os.fspath(cwd),
        text=True,
        capture_output=True,
        check=False,
        timeout=timeout_sec,
    )


def _run(
    runner: Runner,
    argv: Sequence[str],
    cwd: pathlib.Path,
    timeout_sec: float,
) -> subprocess.CompletedProcess[str]:
    try:
        return runner(tuple(argv), cwd, timeout_sec)
    except (OSError, subprocess.SubprocessError) as exc:
        return subprocess.CompletedProcess(
            list(argv),
            126,
            "",
            f"{type(exc).__name__}",
        )


def _check(name: str, passed: bool, ok: str, failed: str) -> dict[str, str]:
    return {
        "name": name,
        "status": "pass" if passed else "fail",
        "reason_code": ok if passed else failed,
    }


def _safe_relative_path(value: Any, repo: pathlib.Path) -> pathlib.Path | None:
    if not isinstance(value, str) or not value or "\\" in value:
        return None
    candidate = pathlib.PurePosixPath(value)
    if candidate.is_absolute() or any(part in {"", ".", ".."} for part in candidate.parts):
        return None
    resolved = (repo / pathlib.Path(*candidate.parts)).resolve(strict=False)
    try:
        resolved.relative_to(repo)
    except ValueError:
        return None
    return resolved


def _version_line(value: str) -> str | None:
    for line in value.splitlines():
        line = line.strip()
        if line and len(line) <= 120:
            return line
    return None


def _toml_section(text: str, name: str) -> str | None:
    """Return one exact TOML table body without parsing unrelated secrets."""

    target = f"[{name}]"
    lines = text.splitlines()
    start = None
    for index, line in enumerate(lines):
        if line.strip() == target:
            start = index + 1
            break
    if start is None:
        return None
    body: list[str] = []
    for line in lines[start:]:
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            break
        body.append(line)
    return "\n".join(body)


def _config_state(config_path: pathlib.Path) -> tuple[dict[str, Any], str, str | None]:
    path_hash = _sha256(os.fspath(config_path.resolve(strict=False)))
    try:
        content = config_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return (
            {
                "path_sha256": path_hash,
                "content_sha256": _sha256(b"<missing>"),
                "present": False,
            },
            "",
            None,
        )
    return (
        {
            "path_sha256": path_hash,
            "content_sha256": _sha256(content),
            "present": True,
        },
        content,
        None,
    )


def _mcp_configured(config_text: str, tool: str) -> bool:
    body = _toml_section(config_text, f"mcp_servers.{tool}")
    if body is None:
        return False
    command = re.search(r'(?m)^\s*command\s*=\s*"([^"]+)"\s*$', body)
    if command is None:
        return False
    lowered = body.lower()
    return tool in lowered or tool in command.group(1).lower()


def _git_identity(
    repo: pathlib.Path, runner: Runner, timeout_sec: float
) -> tuple[dict[str, Any], list[dict[str, str]], list[str]]:
    evidence: list[str] = []
    head_result = _run(runner, ("git", "rev-parse", "HEAD"), repo, timeout_sec)
    head = head_result.stdout.strip().lower()
    head_ok = head_result.returncode == 0 and _HEAD.fullmatch(head) is not None
    evidence.extend((head_result.stdout, head_result.stderr))

    status_result = _run(
        runner, ("git", "status", "--porcelain=v1", "-z"), repo, timeout_sec
    )
    status_ok = status_result.returncode == 0
    evidence.extend((status_result.stdout, status_result.stderr))
    status_bytes = status_result.stdout.encode("utf-8", errors="surrogatepass")
    identity = {
        "root_sha256": _sha256(os.fspath(repo)),
        "head_sha": head if head_ok else "0" * 40,
        "dirty": bool(status_result.stdout) if status_ok else True,
        "worktree_sha256": _sha256(status_bytes),
    }
    checks = [
        _check("git_head", head_ok, "GIT_HEAD_BOUND", "GIT_HEAD_UNAVAILABLE"),
        _check(
            "git_worktree",
            status_ok,
            "GIT_WORKTREE_BOUND",
            "GIT_WORKTREE_UNAVAILABLE",
        ),
    ]
    return identity, checks, evidence


def _codegraph_probe(
    repo: pathlib.Path,
    expected_path: str,
    config_text: str,
    runner: Runner,
    which: Callable[[str], str | None],
    timeout_sec: float,
) -> dict[str, Any]:
    checks: list[dict[str, str]] = []
    evidence: list[str] = []
    binary = which("codegraph")
    checks.append(
        _check(
            "binary",
            binary is not None,
            "CODEGRAPH_BINARY_FOUND",
            "CODEGRAPH_NOT_FOUND",
        )
    )
    checks.append(
        _check(
            "codex_mcp_config",
            _mcp_configured(config_text, "codegraph"),
            "CODEGRAPH_MCP_CONFIGURED",
            "CODEGRAPH_MCP_NOT_CONFIGURED",
        )
    )
    version = None
    if binary:
        version_result = _run(runner, (binary, "version"), repo, timeout_sec)
        evidence.extend((version_result.stdout, version_result.stderr))
        version = _version_line(version_result.stdout)
        checks.append(
            _check(
                "version",
                version_result.returncode == 0 and version is not None,
                "CODEGRAPH_VERSION_OK",
                "CODEGRAPH_VERSION_FAILED",
            )
        )

        status_result = _run(
            runner, (binary, "status", "--json", os.fspath(repo)), repo, timeout_sec
        )
        evidence.extend((status_result.stdout, status_result.stderr))
        try:
            status = json.loads(status_result.stdout)
        except (json.JSONDecodeError, TypeError):
            status = None
        status_mapping = status if isinstance(status, Mapping) else {}
        project_path = status_mapping.get("projectPath")
        project_ok = (
            isinstance(project_path, str)
            and pathlib.Path(project_path).resolve(strict=False) == repo
        )
        pending = status_mapping.get("pendingChanges")
        pending_ok = (
            isinstance(pending, Mapping)
            and set(pending) >= {"added", "modified", "removed"}
            and all(pending.get(name) == 0 for name in ("added", "modified", "removed"))
        )
        index = status_mapping.get("index")
        index_ok = (
            status_result.returncode == 0
            and status_mapping.get("initialized") is True
            and status_mapping.get("worktreeMismatch") is None
            and isinstance(index, Mapping)
            and index.get("state") == "complete"
            and index.get("reindexRecommended") is False
        )
        checks.extend(
            [
                _check(
                    "project_scope",
                    project_ok,
                    "CODEGRAPH_PROJECT_MATCH",
                    "CODEGRAPH_WRONG_PROJECT",
                ),
                _check(
                    "index_state",
                    index_ok,
                    "CODEGRAPH_INDEX_COMPLETE",
                    "CODEGRAPH_INDEX_INVALID",
                ),
                _check(
                    "revision_freshness",
                    pending_ok,
                    "CODEGRAPH_REVISION_MATCH",
                    "CODEGRAPH_STALE",
                ),
            ]
        )

        files_result = _run(
            runner,
            (binary, "files", "--path", os.fspath(repo), "--format", "flat", "--json"),
            repo,
            timeout_sec,
        )
        evidence.extend((files_result.stdout, files_result.stderr))
        try:
            files = json.loads(files_result.stdout)
        except (json.JSONDecodeError, TypeError):
            files = None
        safe_files = (
            isinstance(files, list)
            and bool(files)
            and all(
                isinstance(item, Mapping)
                and _safe_relative_path(item.get("path"), repo) is not None
                and _safe_relative_path(item.get("path"), repo).is_file()  # type: ignore[union-attr]
                for item in files
            )
        )
        checks.append(
            _check(
                "indexed_files",
                files_result.returncode == 0 and safe_files,
                "CODEGRAPH_FILES_CURRENT",
                "CODEGRAPH_FILES_INVALID",
            )
        )

        expected = _safe_relative_path(expected_path, repo)
        query_term = pathlib.PurePosixPath(expected_path).stem if expected else ""
        query_result = _run(
            runner,
            (binary, "query", "--path", os.fspath(repo), query_term, "--json"),
            repo,
            timeout_sec,
        )
        evidence.extend((query_result.stdout, query_result.stderr))
        try:
            query = json.loads(query_result.stdout)
        except (json.JSONDecodeError, TypeError):
            query = None
        query_paths = {
            item.get("node", {}).get("filePath")
            for item in query
            if isinstance(item, Mapping) and isinstance(item.get("node"), Mapping)
        } if isinstance(query, list) else set()
        sentinel_ok = (
            expected is not None
            and expected.is_file()
            and query_result.returncode == 0
            and expected_path in query_paths
        )
        checks.append(
            _check(
                "sentinel_query",
                sentinel_ok,
                "CODEGRAPH_SENTINEL_MATCH",
                "CODEGRAPH_SENTINEL_MISMATCH",
            )
        )

    passed = all(item["status"] == "pass" for item in checks)
    return {
        "tool": "codegraph",
        "status": "pass" if passed else "fail",
        "reason_code": "CODEGRAPH_READY" if passed else next(
            item["reason_code"] for item in checks if item["status"] == "fail"
        ),
        "version": version,
        "checks": checks,
        "evidence_sha256": _sha256("\n".join(evidence)),
    }


def _semble_probe(
    repo: pathlib.Path,
    semantic_query: str,
    expected_path: str,
    config_text: str,
    runner: Runner,
    which: Callable[[str], str | None],
    timeout_sec: float,
) -> dict[str, Any]:
    checks: list[dict[str, str]] = []
    evidence: list[str] = []
    binary = which("semble")
    checks.append(
        _check("binary", binary is not None, "SEMBLE_BINARY_FOUND", "SEMBLE_NOT_FOUND")
    )
    checks.append(
        _check(
            "codex_mcp_config",
            _mcp_configured(config_text, "semble"),
            "SEMBLE_MCP_CONFIGURED",
            "SEMBLE_MCP_NOT_CONFIGURED",
        )
    )
    query_ok = isinstance(semantic_query, str) and bool(semantic_query.strip())
    expected = _safe_relative_path(expected_path, repo)
    expected_ok = expected is not None and expected.is_file()
    checks.extend(
        [
            _check(
                "semantic_query",
                query_ok,
                "SEMBLE_QUERY_DECLARED",
                "SEMBLE_QUERY_REQUIRED",
            ),
            _check(
                "expected_path",
                expected_ok,
                "SEMBLE_EXPECTED_PATH_BOUND",
                "SEMBLE_EXPECTED_PATH_INVALID",
            ),
        ]
    )
    version = None
    if binary:
        help_result = _run(runner, (binary, "--help"), repo, timeout_sec)
        evidence.extend((help_result.stdout, help_result.stderr))
        version = _version_line(help_result.stdout)
        checks.append(
            _check(
                "command_surface",
                help_result.returncode == 0 and "search" in help_result.stdout,
                "SEMBLE_COMMAND_SURFACE_OK",
                "SEMBLE_COMMAND_SURFACE_FAILED",
            )
        )
        if query_ok and expected_ok:
            search_result = _run(
                runner,
                (
                    binary,
                    "search",
                    semantic_query.strip(),
                    os.fspath(repo),
                    "-k",
                    "5",
                    "--max-snippet-lines",
                    "0",
                ),
                repo,
                timeout_sec,
            )
            evidence.extend((search_result.stdout, search_result.stderr))
            try:
                payload = json.loads(search_result.stdout)
            except (json.JSONDecodeError, TypeError):
                payload = None
            results = payload.get("results") if isinstance(payload, Mapping) else None
            safe_results = (
                isinstance(results, list)
                and bool(results)
                and all(
                    isinstance(item, Mapping)
                    and _safe_relative_path(item.get("file_path"), repo) is not None
                    for item in results
                )
            )
            paths = {
                item.get("file_path")
                for item in results
                if isinstance(item, Mapping)
            } if isinstance(results, list) else set()
            sentinel_ok = (
                search_result.returncode == 0
                and safe_results
                and expected_path in paths
            )
            checks.extend(
                [
                    _check(
                        "repo_scope",
                        safe_results,
                        "SEMBLE_REPO_SCOPE_MATCH",
                        "SEMBLE_SCOPE_CONTAMINATION",
                    ),
                    _check(
                        "sentinel_query",
                        sentinel_ok,
                        "SEMBLE_SENTINEL_MATCH",
                        "SEMBLE_SENTINEL_MISMATCH",
                    ),
                ]
            )

    passed = all(item["status"] == "pass" for item in checks)
    return {
        "tool": "semble",
        "status": "pass" if passed else "fail",
        "reason_code": "SEMBLE_READY" if passed else next(
            item["reason_code"] for item in checks if item["status"] == "fail"
        ),
        "version": version,
        "checks": checks,
        "evidence_sha256": _sha256("\n".join(evidence)),
    }


def _rtk_probe(
    repo: pathlib.Path,
    expected_head: str,
    runner: Runner,
    which: Callable[[str], str | None],
    timeout_sec: float,
) -> dict[str, Any]:
    checks: list[dict[str, str]] = []
    evidence: list[str] = []
    binary = which("rtk")
    checks.append(_check("binary", binary is not None, "RTK_BINARY_FOUND", "RTK_NOT_FOUND"))
    version = None
    if binary:
        version_result = _run(runner, (binary, "--version"), repo, timeout_sec)
        evidence.extend((version_result.stdout, version_result.stderr))
        version = _version_line(version_result.stdout)
        checks.append(
            _check(
                "version",
                version_result.returncode == 0 and version is not None,
                "RTK_VERSION_OK",
                "RTK_VERSION_FAILED",
            )
        )
        positive = _run(
            runner, (binary, "git", "rev-parse", "HEAD"), repo, timeout_sec
        )
        evidence.extend((positive.stdout, positive.stderr))
        checks.append(
            _check(
                "positive_command",
                positive.returncode == 0 and positive.stdout.strip().lower() == expected_head,
                "RTK_OUTPUT_MATCH",
                "RTK_OUTPUT_MISMATCH",
            )
        )
        negative = _run(
            runner,
            (
                binary,
                "git",
                "rev-parse",
                "--verify",
                "refs/heads/__codex_toolchain_missing__",
            ),
            repo,
            timeout_sec,
        )
        evidence.extend((negative.stdout, negative.stderr))
        checks.append(
            _check(
                "failure_exit_status",
                negative.returncode != 0,
                "RTK_FAILURE_PRESERVED",
                "RTK_FALSE_GREEN",
            )
        )
        status_result = _run(
            runner, (binary, "git", "status", "--short"), repo, timeout_sec
        )
        evidence.extend((status_result.stdout, status_result.stderr))
        checks.append(
            _check(
                "repository_command",
                status_result.returncode == 0,
                "RTK_REPO_COMMAND_OK",
                "RTK_REPO_COMMAND_FAILED",
            )
        )

    passed = all(item["status"] == "pass" for item in checks)
    return {
        "tool": "rtk",
        "status": "pass" if passed else "fail",
        "reason_code": "RTK_READY" if passed else next(
            item["reason_code"] for item in checks if item["status"] == "fail"
        ),
        "version": version,
        "checks": checks,
        "evidence_sha256": _sha256("\n".join(evidence)),
    }


def run_preflight(
    repo: str | os.PathLike[str],
    *,
    semantic_query: str,
    expected_path: str,
    config_path: str | os.PathLike[str] | None = None,
    strict: bool = True,
    timeout_sec: float = 20.0,
    runner: Runner | None = None,
    which: Callable[[str], str | None] = shutil.which,
) -> dict[str, Any]:
    """Run the read-only deep toolchain preflight for one exact repository."""

    if type(strict) is not bool:
        raise PreflightError("strict must be boolean")
    if not isinstance(timeout_sec, (int, float)) or timeout_sec <= 0:
        raise PreflightError("timeout_sec must be positive")
    root = pathlib.Path(repo).expanduser().resolve(strict=True)
    if not root.is_dir():
        raise PreflightError("repo must be a directory")
    selected_runner = runner or _default_runner
    if config_path is None:
        codex_home = pathlib.Path(
            os.environ.get("CODEX_HOME", pathlib.Path.home() / ".codex")
        ).expanduser()
        selected_config = codex_home / "config.toml"
    else:
        selected_config = pathlib.Path(config_path).expanduser()

    config_identity, config_text, _ = _config_state(selected_config)
    repo_identity, git_checks, git_evidence = _git_identity(
        root, selected_runner, float(timeout_sec)
    )
    if strict and any(item["status"] == "fail" for item in git_checks):
        raise PreflightError("strict preflight requires a valid Git repository identity")

    tools = [
        _codegraph_probe(
            root,
            expected_path,
            config_text,
            selected_runner,
            which,
            float(timeout_sec),
        ),
        _semble_probe(
            root,
            semantic_query,
            expected_path,
            config_text,
            selected_runner,
            which,
            float(timeout_sec),
        ),
        _rtk_probe(
            root,
            repo_identity["head_sha"],
            selected_runner,
            which,
            float(timeout_sec),
        ),
    ]
    passed = sum(item["status"] == "pass" for item in tools)
    failed = len(tools) - passed
    counts = {
        "total": len(tools),
        "ran": len(tools),
        "passed": passed,
        "failed": failed,
        "skipped": 0,
        "xfail": 0,
        "unknown": 0,
    }
    status = "ready" if failed == 0 else ("blocked" if strict else "degraded")
    cache_basis = {
        "schema": SCHEMA,
        "repo_identity": repo_identity,
        "config_identity": config_identity,
        "tool_versions": {item["tool"]: item["version"] for item in tools},
        "tool_evidence": {item["tool"]: item["evidence_sha256"] for item in tools},
    }
    report = {
        "schema": SCHEMA,
        "status": status,
        "strict": strict,
        "repo_identity": repo_identity,
        "config_identity": config_identity,
        "tools": tools,
        "counts": counts,
        "denominator": len(tools),
        "denominator_known": True,
        "cache": {
            "key_sha256": _sha256(_canonical_json(cache_basis)),
            "invalidated_by": [
                "host_or_runtime_change",
                "tool_version_change",
                "codex_config_change",
                "repo_root_change",
                "git_head_change",
                "worktree_change",
                "codegraph_index_change",
                "sentinel_change",
            ],
        },
        "mutations": [],
    }
    # Bind Git probe output without persisting it.  This keeps the cache key
    # sensitive to command behavior while the public report remains private.
    report["cache"]["key_sha256"] = _sha256(
        report["cache"]["key_sha256"] + _sha256("\n".join(git_evidence))
    )
    return validate_preflight(report)


def _require_sha(value: Any, field: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise PreflightError(f"{field} must be a SHA-256 digest")
    return value


def validate_preflight(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate exact fields, arithmetic, privacy shape, and readiness logic."""

    if not isinstance(value, Mapping) or set(value) != REPORT_FIELDS:
        raise PreflightError("preflight fields must match the exact v16 schema")
    result = dict(value)
    if result["schema"] != SCHEMA:
        raise PreflightError("preflight schema mismatch")
    if result["status"] not in {"ready", "degraded", "blocked"}:
        raise PreflightError("invalid preflight status")
    if type(result["strict"]) is not bool:
        raise PreflightError("strict must be boolean")
    if result["strict"] and result["status"] == "degraded":
        raise PreflightError("strict preflight cannot be degraded")

    repo = result["repo_identity"]
    if not isinstance(repo, Mapping) or set(repo) != REPO_FIELDS:
        raise PreflightError("repo_identity fields mismatch")
    _require_sha(repo["root_sha256"], "repo_identity.root_sha256")
    if not isinstance(repo["head_sha"], str) or _HEAD.fullmatch(repo["head_sha"]) is None:
        raise PreflightError("repo_identity.head_sha must be a full Git SHA")
    if type(repo["dirty"]) is not bool:
        raise PreflightError("repo_identity.dirty must be boolean")
    _require_sha(repo["worktree_sha256"], "repo_identity.worktree_sha256")

    config = result["config_identity"]
    if not isinstance(config, Mapping) or set(config) != CONFIG_FIELDS:
        raise PreflightError("config_identity fields mismatch")
    _require_sha(config["path_sha256"], "config_identity.path_sha256")
    _require_sha(config["content_sha256"], "config_identity.content_sha256")
    if type(config["present"]) is not bool:
        raise PreflightError("config_identity.present must be boolean")

    tools = result["tools"]
    if not isinstance(tools, list) or len(tools) != len(MANDATORY_TOOLS):
        raise PreflightError("tools denominator mismatch")
    passed = failed = 0
    for expected, tool in zip(MANDATORY_TOOLS, tools):
        if not isinstance(tool, Mapping) or set(tool) != TOOL_FIELDS:
            raise PreflightError("tool fields mismatch")
        if tool["tool"] != expected or tool["status"] not in STATUSES:
            raise PreflightError("tool order/status mismatch")
        if not isinstance(tool["reason_code"], str) or not tool["reason_code"]:
            raise PreflightError("tool reason_code required")
        if tool["version"] is not None and (
            not isinstance(tool["version"], str) or not tool["version"]
        ):
            raise PreflightError("tool version must be null or non-empty")
        _require_sha(tool["evidence_sha256"], "tool.evidence_sha256")
        checks = tool["checks"]
        if not isinstance(checks, list) or not checks:
            raise PreflightError("tool checks must be non-empty")
        check_pass = True
        for check in checks:
            if not isinstance(check, Mapping) or set(check) != CHECK_FIELDS:
                raise PreflightError("check fields mismatch")
            if (
                not isinstance(check["name"], str)
                or not check["name"]
                or check["status"] not in STATUSES
                or not isinstance(check["reason_code"], str)
                or not check["reason_code"]
            ):
                raise PreflightError("invalid check")
            check_pass = check_pass and check["status"] == "pass"
        if (tool["status"] == "pass") != check_pass:
            raise PreflightError("tool status/check mismatch")
        if tool["status"] == "pass":
            passed += 1
        else:
            failed += 1

    counts = result["counts"]
    if not isinstance(counts, Mapping) or set(counts) != COUNT_FIELDS:
        raise PreflightError("counts fields mismatch")
    if any(type(item) is not int or item < 0 for item in counts.values()):
        raise PreflightError("counts must be non-negative integers")
    if counts["total"] != (
        counts["passed"]
        + counts["failed"]
        + counts["skipped"]
        + counts["xfail"]
        + counts["unknown"]
    ):
        raise PreflightError("counts total arithmetic mismatch")
    if counts["ran"] != counts["passed"] + counts["failed"]:
        raise PreflightError("counts ran arithmetic mismatch")
    if (
        counts["total"] != len(MANDATORY_TOOLS)
        or counts["passed"] != passed
        or counts["failed"] != failed
        or counts["skipped"]
        or counts["xfail"]
        or counts["unknown"]
    ):
        raise PreflightError("counts/tool mismatch")
    if result["denominator"] != len(MANDATORY_TOOLS):
        raise PreflightError("denominator mismatch")
    if result["denominator_known"] is not True:
        raise PreflightError("denominator must be known")
    expected_status = "ready" if failed == 0 else (
        "blocked" if result["strict"] else "degraded"
    )
    if result["status"] != expected_status:
        raise PreflightError("status/count mismatch")

    cache = result["cache"]
    if not isinstance(cache, Mapping) or set(cache) != CACHE_FIELDS:
        raise PreflightError("cache fields mismatch")
    _require_sha(cache["key_sha256"], "cache.key_sha256")
    invalidated_by = cache["invalidated_by"]
    if (
        not isinstance(invalidated_by, list)
        or not invalidated_by
        or any(not isinstance(item, str) or not item for item in invalidated_by)
        or len(set(invalidated_by)) != len(invalidated_by)
    ):
        raise PreflightError("cache invalidation list invalid")
    if result["mutations"] != []:
        raise PreflightError("preflight must be read-only")
    return result


__all__ = [
    "MANDATORY_TOOLS",
    "PreflightError",
    "REPORT_FIELDS",
    "SCHEMA",
    "run_preflight",
    "validate_preflight",
]

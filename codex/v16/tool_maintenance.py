"""Bounded, model-agnostic toolchain self-healing for one owning repository.

The strict doctor remains read-only.  This module wraps it with an explicit
maintenance lane that may initialize or synchronize only the exact repository's
CodeGraph index.  It never installs packages, edits Codex configuration, clears
global caches, or retries indefinitely.
"""

from __future__ import annotations

import hashlib
import json
import os
import pathlib
import re
import stat
import subprocess
import time
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from .tool_preflight import run_preflight, validate_preflight


SCHEMA = "tool-maintenance.v16"
REPAIR_BUDGET = 1
AUTO_CODEGRAPH_REASONS = frozenset({
    "CODEGRAPH_STALE",
    "CODEGRAPH_INDEX_INVALID",
    "CODEGRAPH_SENTINEL_MISMATCH",
    "CODEGRAPH_WRONG_PROJECT",
})
EXTERNAL_REASONS = frozenset({
    "CODEGRAPH_NOT_FOUND",
    "CODEGRAPH_MCP_NOT_CONFIGURED",
    "CODEGRAPH_VERSION_FAILED",
    "CODEGRAPH_FILES_INVALID",
    "SEMBLE_NOT_FOUND",
    "SEMBLE_MCP_NOT_CONFIGURED",
    "SEMBLE_COMMAND_SURFACE_FAILED",
    "SEMBLE_SCOPE_CONTAMINATION",
    "SEMBLE_SENTINEL_MISMATCH",
    "RTK_NOT_FOUND",
    "RTK_VERSION_FAILED",
    "RTK_OUTPUT_MISMATCH",
    "RTK_FALSE_GREEN",
    "RTK_REPO_COMMAND_FAILED",
})
REPORT_FIELDS = frozenset({
    "schema", "status", "terminal_reason_code", "repair_owner_role",
    "repair_budget", "repair_attempts", "initial_preflight_sha256",
    "final_preflight_sha256", "initial_status", "final_status",
    "initial_reason_codes", "final_reason_codes", "actions", "counts",
    "denominator", "denominator_known", "mutations",
    "failure_fingerprint_sha256", "circuit_state",
})
ACTION_FIELDS = frozenset({
    "tool", "action", "status", "reason_code", "argv_sha256",
    "evidence_sha256", "returncode",
})
COUNT_FIELDS = frozenset(
    {"total", "ran", "passed", "failed", "skipped", "xfail", "unknown"}
)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class ToolMaintenanceError(ValueError):
    """Raised when the bounded maintenance contract is invalid."""


PreflightRunner = Callable[..., dict[str, Any]]
CommandRunner = Callable[
    [Sequence[str], pathlib.Path, float], subprocess.CompletedProcess[str]
]


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _sha256(value: str | bytes) -> str:
    if isinstance(value, str):
        value = value.encode("utf-8")
    return hashlib.sha256(value).hexdigest()


def _default_command_runner(
    argv: Sequence[str], cwd: pathlib.Path, timeout_sec: float
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(argv), cwd=os.fspath(cwd), text=True, capture_output=True,
        check=False, timeout=timeout_sec,
    )


def _reason_codes(report: Mapping[str, Any]) -> list[str]:
    return sorted({
        str(check["reason_code"])
        for tool in report.get("tools", [])
        if isinstance(tool, Mapping)
        for check in tool.get("checks", [])
        if isinstance(check, Mapping) and check.get("status") == "fail"
    })


def _report_sha256(report: Mapping[str, Any]) -> str:
    return _sha256(_canonical_json(report))


def _failure_fingerprint(
    report: Mapping[str, Any], semantic_query: str, expected_path: str
) -> str:
    """Bind retry suppression to stable, privacy-safe failure inputs."""

    return _sha256(_canonical_json({
        "repo_identity": report.get("repo_identity"),
        "config_identity": report.get("config_identity"),
        "reason_codes": _reason_codes(report),
        "semantic_query_sha256": _sha256(semantic_query),
        "expected_path_sha256": _sha256(expected_path),
    }))


def _safe_state_dir(value: str | os.PathLike[str] | None) -> pathlib.Path:
    root = pathlib.Path(
        value
        if value is not None
        else os.environ.get(
            "CODEX_TOOL_STATE_DIR",
            os.fspath(
                pathlib.Path(
                    os.environ.get(
                        "CODEX_HOME",
                        os.fspath(pathlib.Path.home() / ".codex"),
                    )
                ).expanduser() / "tool-state"
            ),
        )
    ).expanduser()
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    metadata = root.lstat()
    if root.is_symlink() or not root.is_dir() or metadata.st_uid != os.geteuid():
        raise ToolMaintenanceError("tool state directory ownership/type invalid")
    if stat.S_IMODE(metadata.st_mode) != 0o700:
        os.chmod(root, 0o700)
    return root


def _acquire_lock(root: pathlib.Path, repo: pathlib.Path) -> pathlib.Path | None:
    name = _sha256(os.fspath(repo)) + ".lock"
    lock = root / name
    try:
        descriptor = os.open(
            lock,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
    except FileExistsError:
        try:
            age = time.time() - lock.lstat().st_mtime
        except OSError:
            return None
        if age <= 900:
            return None
        try:
            lock.unlink()
        except OSError:
            return None
        return _acquire_lock(root, repo)
    except OSError as exc:
        raise ToolMaintenanceError("tool maintenance lock unavailable") from exc
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        stream.write(str(os.getpid()))
        stream.flush()
        os.fsync(stream.fileno())
    return lock


def _circuit_path(root: pathlib.Path, repo: pathlib.Path) -> pathlib.Path:
    return root / (_sha256(os.fspath(repo)) + ".circuit.json")


def _read_circuit(path: pathlib.Path) -> str | None:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return None
    except OSError:
        return None
    if path.is_symlink() or not path.is_file() or metadata.st_uid != os.geteuid():
        raise ToolMaintenanceError("tool maintenance circuit ownership/type invalid")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ToolMaintenanceError("tool maintenance circuit unreadable") from exc
    fingerprint = payload.get("failure_fingerprint_sha256") if isinstance(payload, Mapping) else None
    if not isinstance(fingerprint, str) or _SHA256.fullmatch(fingerprint) is None:
        raise ToolMaintenanceError("tool maintenance circuit fingerprint invalid")
    return fingerprint


def _write_circuit(path: pathlib.Path, fingerprint: str, reason_code: str) -> None:
    if path.exists() and path.is_symlink():
        raise ToolMaintenanceError("tool maintenance circuit symlink rejected")
    temporary = path.with_name(path.name + f".{os.getpid()}.tmp")
    payload = _canonical_json({
        "schema": "tool-maintenance-circuit.v16",
        "failure_fingerprint_sha256": fingerprint,
        "terminal_reason_code": reason_code,
    })
    descriptor = os.open(
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _clear_circuit(path: pathlib.Path) -> None:
    try:
        if path.is_symlink():
            raise ToolMaintenanceError("tool maintenance circuit symlink rejected")
        path.unlink()
    except FileNotFoundError:
        pass


def _select_codegraph_action(
    repo: pathlib.Path,
    runner: CommandRunner,
    timeout_sec: float,
) -> tuple[str, tuple[str, ...]]:
    status = runner(("codegraph", "status", "--json", os.fspath(repo)), repo, timeout_sec)
    try:
        payload = json.loads(status.stdout)
    except (json.JSONDecodeError, TypeError):
        payload = None
    initialized = isinstance(payload, Mapping) and payload.get("initialized") is True
    action = "sync" if initialized else "init"
    return action, ("codegraph", action, os.fspath(repo))


def _action_record(
    action: str,
    argv: Sequence[str],
    result: subprocess.CompletedProcess[str],
) -> dict[str, Any]:
    return {
        "tool": "codegraph",
        "action": action,
        "status": "success" if result.returncode == 0 else "failure",
        "reason_code": (
            "CODEGRAPH_REPO_INDEX_REPAIRED"
            if result.returncode == 0
            else "CODEGRAPH_REPO_INDEX_REPAIR_FAILED"
        ),
        "argv_sha256": _sha256(_canonical_json(list(argv))),
        "evidence_sha256": _sha256(
            _canonical_json({
                "returncode": result.returncode,
                "stdout_sha256": _sha256(result.stdout or ""),
                "stderr_sha256": _sha256(result.stderr or ""),
            })
        ),
        "returncode": int(result.returncode),
    }


def maintain_toolchain(
    repo: str | os.PathLike[str],
    *,
    semantic_query: str,
    expected_path: str,
    config_path: str | os.PathLike[str] | None = None,
    timeout_sec: float = 30.0,
    repair: bool = True,
    allow_repo_index_mutation: bool = True,
    state_dir: str | os.PathLike[str] | None = None,
    preflight_runner: PreflightRunner = run_preflight,
    command_runner: CommandRunner = _default_command_runner,
) -> dict[str, Any]:
    """Check, repair once when safe, then re-check the exact repository.

    The current assigned execution agent owns this lane regardless of model
    name.  Package installation, user-level Codex configuration, global cache
    clearing, and a second repair attempt are intentionally out of scope.
    """

    if type(repair) is not bool or type(allow_repo_index_mutation) is not bool:
        raise ToolMaintenanceError("repair policy flags must be boolean")
    if not isinstance(timeout_sec, (int, float)) or timeout_sec <= 0:
        raise ToolMaintenanceError("timeout_sec must be positive")
    root = pathlib.Path(repo).expanduser().resolve(strict=True)
    if not root.is_dir():
        raise ToolMaintenanceError("repo must be a directory")
    kwargs = {
        "semantic_query": semantic_query,
        "expected_path": expected_path,
        "config_path": config_path,
        "strict": True,
        "timeout_sec": float(timeout_sec),
    }
    initial = validate_preflight(preflight_runner(root, **kwargs))
    initial_reasons = _reason_codes(initial)
    failure_fingerprint = _failure_fingerprint(initial, semantic_query, expected_path)
    final = initial
    actions: list[dict[str, Any]] = []
    terminal = "TOOLS_READY"
    status = "ready"
    circuit_state = "closed"

    auto_needed = bool(set(initial_reasons) & AUTO_CODEGRAPH_REASONS)
    external_needed = bool(set(initial_reasons) & EXTERNAL_REASONS)
    if initial["status"] != "ready":
        if not auto_needed:
            status = "external_action_required"
            terminal = "EXTERNAL_TOOL_REPAIR_REQUIRED"
            circuit_state = "not_applicable"
        elif not repair or not allow_repo_index_mutation:
            status = "maintenance_required"
            terminal = "REPO_INDEX_REPAIR_NOT_AUTHORIZED"
            circuit_state = "not_applicable"
        else:
            state = _safe_state_dir(state_dir)
            circuit = _circuit_path(state, root)
            if _read_circuit(circuit) == failure_fingerprint:
                status = "maintenance_required"
                terminal = "AUTO_REPAIR_CIRCUIT_OPEN"
                circuit_state = "open"
            else:
                lock = _acquire_lock(state, root)
                if lock is None:
                    status = "maintenance_required"
                    terminal = "TOOL_MAINTENANCE_LOCK_HELD"
                else:
                    try:
                        action, argv = _select_codegraph_action(
                            root, command_runner, float(timeout_sec)
                        )
                        result = command_runner(argv, root, float(timeout_sec))
                        actions.append(_action_record(action, argv, result))
                        if result.returncode == 0:
                            final = validate_preflight(preflight_runner(root, **kwargs))
                        final_reasons_now = _reason_codes(final)
                        if final["status"] == "ready":
                            status = "ready"
                            terminal = "REPAIRED_AND_READY"
                            _clear_circuit(circuit)
                        elif result.returncode != 0:
                            status = "maintenance_required"
                            terminal = "AUTO_REPAIR_COMMAND_FAILED"
                        elif final_reasons_now == initial_reasons:
                            status = "maintenance_required"
                            terminal = "AUTO_REPAIR_NO_PROGRESS"
                        elif set(final_reasons_now) & EXTERNAL_REASONS or external_needed:
                            status = "external_action_required"
                            terminal = "EXTERNAL_TOOL_REPAIR_REQUIRED"
                        else:
                            status = "maintenance_required"
                            terminal = "REPAIR_BUDGET_EXHAUSTED"
                        if status != "ready":
                            circuit_state = "open"
                            _write_circuit(circuit, failure_fingerprint, terminal)
                    finally:
                        try:
                            lock.unlink()
                        except OSError:
                            pass

    final_reasons = _reason_codes(final)
    report = {
        "schema": SCHEMA,
        "status": status,
        "terminal_reason_code": terminal,
        "repair_owner_role": "assigned_execution_agent:tool_maintainer",
        "repair_budget": REPAIR_BUDGET,
        "repair_attempts": len(actions),
        "initial_preflight_sha256": _report_sha256(initial),
        "final_preflight_sha256": _report_sha256(final),
        "initial_status": initial["status"],
        "final_status": final["status"],
        "initial_reason_codes": initial_reasons,
        "final_reason_codes": final_reasons,
        "actions": actions,
        "counts": dict(final["counts"]),
        "denominator": final["denominator"],
        "denominator_known": final["denominator_known"],
        "mutations": [action["action"] for action in actions if action["status"] == "success"],
        "failure_fingerprint_sha256": failure_fingerprint,
        "circuit_state": circuit_state,
    }
    return validate_maintenance_report(report)


def validate_maintenance_report(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != REPORT_FIELDS:
        raise ToolMaintenanceError("maintenance report fields must match v16 schema")
    result = dict(value)
    if result["schema"] != SCHEMA:
        raise ToolMaintenanceError("maintenance schema mismatch")
    if result["status"] not in {
        "ready", "maintenance_required", "external_action_required", "blocked"
    }:
        raise ToolMaintenanceError("maintenance status invalid")
    if result["repair_owner_role"] != "assigned_execution_agent:tool_maintainer":
        raise ToolMaintenanceError("maintenance owner role mismatch")
    if result["repair_budget"] != REPAIR_BUDGET:
        raise ToolMaintenanceError("maintenance repair budget mismatch")
    if type(result["repair_attempts"]) is not int or not 0 <= result["repair_attempts"] <= REPAIR_BUDGET:
        raise ToolMaintenanceError("maintenance attempts exceed budget")
    for field in ("initial_preflight_sha256", "final_preflight_sha256"):
        if not isinstance(result[field], str) or _SHA256.fullmatch(result[field]) is None:
            raise ToolMaintenanceError(f"{field} invalid")
    if not isinstance(result["failure_fingerprint_sha256"], str) or _SHA256.fullmatch(result["failure_fingerprint_sha256"]) is None:
        raise ToolMaintenanceError("failure fingerprint invalid")
    if result["circuit_state"] not in {"closed", "open", "not_applicable"}:
        raise ToolMaintenanceError("maintenance circuit state invalid")
    if result["initial_status"] not in {"ready", "blocked"} or result["final_status"] not in {"ready", "blocked"}:
        raise ToolMaintenanceError("preflight status invalid")
    for field in ("initial_reason_codes", "final_reason_codes", "mutations"):
        if not isinstance(result[field], list) or any(not isinstance(item, str) for item in result[field]):
            raise ToolMaintenanceError(f"{field} must be a string list")
    actions = result["actions"]
    if not isinstance(actions, list) or len(actions) != result["repair_attempts"]:
        raise ToolMaintenanceError("maintenance action denominator mismatch")
    for action in actions:
        if not isinstance(action, Mapping) or set(action) != ACTION_FIELDS:
            raise ToolMaintenanceError("maintenance action fields mismatch")
        if action["tool"] != "codegraph" or action["action"] not in {"init", "sync"}:
            raise ToolMaintenanceError("maintenance action is not allowlisted")
        if action["status"] not in {"success", "failure"}:
            raise ToolMaintenanceError("maintenance action status invalid")
        if type(action["returncode"]) is not int:
            raise ToolMaintenanceError("maintenance action returncode invalid")
        for field in ("argv_sha256", "evidence_sha256"):
            if not isinstance(action[field], str) or _SHA256.fullmatch(action[field]) is None:
                raise ToolMaintenanceError(f"maintenance action {field} invalid")
    counts = result["counts"]
    if not isinstance(counts, Mapping) or set(counts) != COUNT_FIELDS:
        raise ToolMaintenanceError("maintenance counts fields mismatch")
    if any(type(item) is not int or item < 0 for item in counts.values()):
        raise ToolMaintenanceError("maintenance counts invalid")
    if counts["total"] != counts["passed"] + counts["failed"]:
        raise ToolMaintenanceError("maintenance counts arithmetic mismatch")
    if counts["ran"] != counts["passed"] + counts["failed"]:
        raise ToolMaintenanceError("maintenance ran arithmetic mismatch")
    if any(counts[name] for name in ("skipped", "xfail", "unknown")):
        raise ToolMaintenanceError("maintenance denominator cannot be unknown or skipped")
    if result["denominator"] != 3 or result["denominator_known"] is not True:
        raise ToolMaintenanceError("maintenance tool denominator mismatch")
    if result["status"] == "ready" and (
        result["final_status"] != "ready" or counts["passed"] != result["denominator"]
    ):
        raise ToolMaintenanceError("ready maintenance report lacks 3/3 final preflight")
    if result["status"] != "ready" and result["final_status"] == "ready":
        raise ToolMaintenanceError("non-ready maintenance report contradicts final preflight")
    if result["terminal_reason_code"] == "AUTO_REPAIR_CIRCUIT_OPEN" and (
        result["circuit_state"] != "open" or result["repair_attempts"] != 0
    ):
        raise ToolMaintenanceError("open circuit must suppress the retry")
    return result


__all__ = [
    "AUTO_CODEGRAPH_REASONS", "EXTERNAL_REASONS", "REPAIR_BUDGET", "SCHEMA",
    "ToolMaintenanceError", "maintain_toolchain", "validate_maintenance_report",
]

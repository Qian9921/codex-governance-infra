"""Adaptive, evidence-producing recovery for the exact repository toolchain.

``tool-maintenance.v16`` is a strict, single-attempt proof artifact and must
remain stable for release and hook consumers.  This module is deliberately a
separate, non-contract recovery lane: it has a finite list of local CodeGraph
strategies, rechecks health after each one, and records enough hashed lineage
to avoid replaying a strategy that already made no progress.

It does not install tools, fetch data, alter Codex configuration, clear global
caches, or handle credentials.  Rebuilding an existing local index first makes
a private backup under the owner-only state directory.
"""

from __future__ import annotations

import json
import os
import pathlib
import shutil
import stat
import subprocess
import time
from collections.abc import Mapping, Sequence
from typing import Any

from .tool_maintenance import (
    AUTO_CODEGRAPH_REASONS,
    CommandRunner,
    PreflightRunner,
    ToolMaintenanceError,
    _SHA256,
    _acquire_lock,
    _canonical_json,
    _reason_codes,
    _report_sha256,
    _safe_state_dir,
    _select_codegraph_action,
    _sha256,
    _default_command_runner,
)
from .tool_preflight import run_preflight, validate_preflight


SCHEMA = "tool-recovery.v1"
RECOVERY_BUDGET = 2
BACKUP_LIMIT = 3
HISTORY_LIMIT = 32
PROVEN_EXTERNAL_REASONS = frozenset()
_RECOVERY_STATUSES = frozenset({
    "ready", "recovering", "degraded", "external_wait",
    "user_action_required", "unrecoverable",
})
_REPORT_FIELDS = frozenset({
    "schema", "status", "terminal_reason_code", "repair_owner_role",
    "strategy_budget", "strategy_attempts", "initial_preflight_sha256",
    "final_preflight_sha256", "initial_status", "final_status",
    "initial_reason_codes", "final_reason_codes", "strategies", "counts",
    "denominator", "denominator_known", "mutations",
    "failure_fingerprint_sha256", "recovery_state", "continuation_owner",
    "recheck_after_sec",
})
_STRATEGY_FIELDS = frozenset({
    "strategy_id", "tool", "action", "status", "reason_code",
    "argv_sha256", "evidence_sha256", "returncode",
    "input_preflight_sha256", "output_preflight_sha256",
    "lineage_parent_sha256", "backup_evidence_sha256",
    "health_after_action_sha256", "rollback_status",
})
_STATE_SCHEMA = "tool-recovery-state.v1"


def _recovery_path(state: pathlib.Path, repo: pathlib.Path) -> pathlib.Path:
    return state / (_sha256(os.fspath(repo)) + ".recovery.json")


def _read_history(path: pathlib.Path) -> list[str]:
    """Read only a private, hash-only history of no-progress strategies."""

    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return []
    except OSError as exc:
        raise ToolMaintenanceError("tool recovery state unavailable") from exc
    if path.is_symlink() or not path.is_file() or metadata.st_uid != os.geteuid():
        raise ToolMaintenanceError("tool recovery state ownership/type invalid")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ToolMaintenanceError("tool recovery state unreadable") from exc
    if not isinstance(payload, Mapping) or set(payload) != {"schema", "no_progress"}:
        raise ToolMaintenanceError("tool recovery state schema invalid")
    values = payload.get("no_progress")
    if payload.get("schema") != _STATE_SCHEMA or not isinstance(values, list):
        raise ToolMaintenanceError("tool recovery state values invalid")
    if (
        len(values) != len(set(values))
        or any(not isinstance(value, str) or _SHA256.fullmatch(value) is None for value in values)
    ):
        raise ToolMaintenanceError("tool recovery state fingerprint invalid")
    return list(values[-HISTORY_LIMIT:])


def _write_history(path: pathlib.Path, history: Sequence[str]) -> None:
    if len(history) > HISTORY_LIMIT or len(history) != len(set(history)):
        raise ToolMaintenanceError("tool recovery history limit invalid")
    if path.exists() and path.is_symlink():
        raise ToolMaintenanceError("tool recovery state symlink rejected")
    temporary = path.with_name(path.name + f".{os.getpid()}.tmp")
    descriptor = os.open(
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    payload = _canonical_json({
        "schema": _STATE_SCHEMA,
        "no_progress": list(history),
    })
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


def _remember_no_progress(history: Sequence[str], key: str) -> list[str]:
    """Keep an ordered, bounded recent history while retaining ``key``."""

    return [
        *[item for item in history if item != key][-(HISTORY_LIMIT - 1):],
        key,
    ]


def _strategy_key(strategy_id: str, fingerprint: str) -> str:
    return _sha256(_canonical_json({
        "strategy_id": strategy_id,
        "failure_fingerprint_sha256": fingerprint,
    }))


def _recovery_fingerprint(
    report: Mapping[str, Any], semantic_query: str, expected_path: str
) -> str:
    """Include actual tool version and probe state in retry suppression.

    A changed CodeGraph binary or probe evidence is new recovery input, not a
    reason to retain an old no-progress circuit indefinitely.
    """

    tool_state = []
    for tool in report.get("tools", []):
        if isinstance(tool, Mapping) and tool.get("tool") == "codegraph":
            tool_state.append({
                "status": tool.get("status"),
                "version": tool.get("version"),
                "checks": [
                    {
                        "name": check.get("name"),
                        "status": check.get("status"),
                        "reason_code": check.get("reason_code"),
                    }
                    for check in tool.get("checks", [])
                    if isinstance(check, Mapping)
                ],
            })
    return _sha256(_canonical_json({
        "repo_identity": report.get("repo_identity"),
        "config_identity": report.get("config_identity"),
        "reason_codes": _reason_codes(report),
        "semantic_query_sha256": _sha256(semantic_query),
        "expected_path_sha256": _sha256(expected_path),
        "codegraph_probe_state": tool_state,
    }))


def _backup_directory(state: pathlib.Path, repo: pathlib.Path) -> pathlib.Path:
    directory = state / (_sha256(os.fspath(repo)) + ".backups")
    directory.mkdir(mode=0o700, exist_ok=True)
    metadata = directory.lstat()
    if directory.is_symlink() or not directory.is_dir() or metadata.st_uid != os.geteuid():
        raise ToolMaintenanceError("tool recovery backup directory ownership/type invalid")
    if stat.S_IMODE(metadata.st_mode) != 0o700:
        os.chmod(directory, 0o700)
    return directory


def _reject_symlinks(source: pathlib.Path) -> None:
    for current, directories, files in os.walk(source, followlinks=False):
        for name in [*directories, *files]:
            candidate = pathlib.Path(current) / name
            if candidate.is_symlink():
                raise ToolMaintenanceError("tool recovery index backup symlink rejected")


def _prune_backups(directory: pathlib.Path, *, preserve: pathlib.Path) -> None:
    backups = []
    for candidate in directory.iterdir():
        if (
            candidate.is_symlink()
            or not candidate.is_dir()
            or not candidate.name.endswith(".codegraph")
            or _SHA256.fullmatch(candidate.name.removesuffix(".codegraph")) is None
        ):
            raise ToolMaintenanceError("tool recovery backup entry invalid")
        metadata = candidate.lstat()
        if metadata.st_uid != os.geteuid():
            raise ToolMaintenanceError("tool recovery backup ownership invalid")
        backups.append((metadata.st_mtime_ns, candidate))
    removals_needed = max(0, len(backups) - BACKUP_LIMIT)
    for _timestamp, candidate in sorted(backups, key=lambda row: (row[0], row[1].name)):
        if removals_needed == 0:
            break
        if candidate == preserve:
            continue
        _reject_symlinks(candidate)
        shutil.rmtree(candidate)
        removals_needed -= 1


def _backup_index(
    state: pathlib.Path, repo: pathlib.Path, fingerprint: str
) -> tuple[pathlib.Path | None, str | None]:
    """Make a private rollback copy, returning only a privacy-safe digest."""

    source = repo / ".codegraph"
    if source.is_symlink() or (source.exists() and not source.is_dir()):
        raise ToolMaintenanceError("tool recovery index source type invalid")
    if not source.exists():
        return None, None
    _reject_symlinks(source)
    backup_directory = _backup_directory(state, repo)
    destination = backup_directory / (
        _sha256(fingerprint + str(time.time_ns())) + ".codegraph"
    )
    shutil.copytree(source, destination, copy_function=shutil.copy2)
    for current, directories, files in os.walk(destination, followlinks=False):
        os.chmod(current, 0o700)
        for name in files:
            os.chmod(pathlib.Path(current) / name, 0o600)
        for name in directories:
            os.chmod(pathlib.Path(current) / name, 0o700)
    _prune_backups(backup_directory, preserve=destination)
    return destination, _sha256(_canonical_json({
        "strategy": "codegraph.backup_index_rebuild",
        "source_sha256": _sha256(os.fspath(source)),
        "backup_name_sha256": _sha256(destination.name),
    }))


def _restore_index(backup: pathlib.Path, repo: pathlib.Path) -> None:
    """Restore the exact private backup after an unhealthy rebuild."""

    source = repo / ".codegraph"
    if source.is_symlink() or (source.exists() and not source.is_dir()):
        raise ToolMaintenanceError("tool recovery index restore target invalid")
    _reject_symlinks(backup)
    if source.exists():
        _reject_symlinks(source)
        shutil.rmtree(source)
    shutil.copytree(backup, source, copy_function=shutil.copy2)


def _strategy_record(
    strategy_id: str,
    action: str,
    argv: Sequence[str],
    result: Any,
    before: Mapping[str, Any],
    after: Mapping[str, Any],
    parent: str | None,
    backup_evidence: str | None,
    health_after_action: Mapping[str, Any],
    rollback_status: str,
) -> dict[str, Any]:
    return {
        "strategy_id": strategy_id,
        "tool": "codegraph",
        "action": action,
        "status": "success" if result.returncode == 0 else "failure",
        "reason_code": (
            "CODEGRAPH_RECOVERY_ACTION_SUCCEEDED"
            if result.returncode == 0
            else "CODEGRAPH_RECOVERY_ACTION_FAILED"
        ),
        "argv_sha256": _sha256(_canonical_json(list(argv))),
        "evidence_sha256": _sha256(_canonical_json({
            "returncode": int(result.returncode),
            "stdout_sha256": _sha256(result.stdout or ""),
            "stderr_sha256": _sha256(result.stderr or ""),
        })),
        "returncode": int(result.returncode),
        "input_preflight_sha256": _report_sha256(before),
        "output_preflight_sha256": _report_sha256(after),
        "lineage_parent_sha256": parent,
        "backup_evidence_sha256": backup_evidence,
        "health_after_action_sha256": _report_sha256(health_after_action),
        "rollback_status": rollback_status,
    }


def _run_recovery_command(
    runner: CommandRunner,
    argv: Sequence[str],
    repo: pathlib.Path,
    timeout_sec: float,
) -> subprocess.CompletedProcess[str]:
    """Turn ordinary command failures into privacy-safe strategy evidence."""

    try:
        return runner(argv, repo, timeout_sec)
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(argv, 124, "", "")
    except (subprocess.SubprocessError, OSError):
        return subprocess.CompletedProcess(argv, 1, "", "")


def _final_status(reasons: list[str]) -> tuple[str, str, str, str, int | None]:
    if set(reasons) & PROVEN_EXTERNAL_REASONS:
        return (
            "external_wait", "EXTERNAL_TOOL_OR_CONFIGURATION_WAIT",
            "outside_dependency_wait", "machine", 300,
        )
    return (
        "recovering", "MACHINE_OWNED_DIAGNOSTIC_RECHECK",
        "machine_recheck", "machine", 300,
    )


def _codegraph_healthy(report: Mapping[str, Any]) -> bool:
    """Return CodeGraph probe health independently of other required tools."""

    for tool in report.get("tools", []):
        if isinstance(tool, Mapping) and tool.get("tool") == "codegraph":
            checks = tool.get("checks")
            return (
                tool.get("status") == "pass"
                and isinstance(checks, list)
                and all(
                    isinstance(check, Mapping) and check.get("status") == "pass"
                    for check in checks
                )
            )
    return False


def recover_toolchain(
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
    """Autonomously attempt finite local recovery strategies until healthy.

    The strategies are capability-specific: use CodeGraph's normal ``init`` or
    ``sync`` first, then make a private backup and rebuild only the same exact
    repository index.  Every action has an input/output preflight hash; an
    unchanged failure records a persistent strategy-specific circuit entry.
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
    final = initial
    initial_reasons = _reason_codes(initial)
    strategies: list[dict[str, Any]] = []
    status = "ready"
    terminal = "TOOLS_READY"
    recovery_state = "not_needed"
    continuation_owner = "none"
    recheck_after_sec: int | None = None

    if initial["status"] != "ready":
        if not set(initial_reasons) & AUTO_CODEGRAPH_REASONS:
            (
                status, terminal, recovery_state, continuation_owner,
                recheck_after_sec,
            ) = _final_status(initial_reasons)
        elif not repair or not allow_repo_index_mutation:
            status = "recovering"
            terminal = "NON_MUTATING_CHECK_COMPLETE"
            recovery_state = "repair_deferred"
            continuation_owner = "machine"
            recheck_after_sec = 300
        else:
            state = _safe_state_dir(state_dir)
            lock = _acquire_lock(state, root)
            if lock is None:
                status = "recovering"
                terminal = "RECOVERY_IN_PROGRESS"
                recovery_state = "single_flight_wait"
                continuation_owner = "machine"
                recheck_after_sec = 15
            else:
                try:
                    history_path = _recovery_path(state, root)
                    history = _read_history(history_path)
                    recovery_state = "running"
                    parent_sha: str | None = None
                    try:
                        candidate_action, candidate_argv = _select_codegraph_action(
                            root, command_runner, float(timeout_sec)
                        )
                    except (subprocess.SubprocessError, OSError):
                        # Match the existing invalid-status fallback without
                        # publishing host exception details.
                        candidate_action = "init"
                        candidate_argv = ("codegraph", "init", os.fspath(root))
                    candidates = [
                        (f"codegraph.{candidate_action}", candidate_action, candidate_argv, False),
                        ("codegraph.backup_index_rebuild", "index", (
                            "codegraph", "index", os.fspath(root),
                        ), True),
                    ]
                    for strategy_id, action, argv, needs_backup in candidates:
                        if final["status"] == "ready":
                            break
                        reasons = _reason_codes(final)
                        if not set(reasons) & AUTO_CODEGRAPH_REASONS:
                            break
                        fingerprint = _recovery_fingerprint(
                            final, semantic_query, expected_path
                        )
                        key = _strategy_key(strategy_id, fingerprint)
                        if key in history:
                            continue
                        before = final
                        backup, backup_evidence = (
                            _backup_index(state, root, fingerprint)
                            if needs_backup else (None, None)
                        )
                        result = _run_recovery_command(
                            command_runner, argv, root, float(timeout_sec)
                        )
                        # Read health even after a nonzero command: readiness, not
                        # command success alone, is the safe close condition.
                        health_after_action = validate_preflight(
                            preflight_runner(root, **kwargs)
                        )
                        rollback_status = "not_needed"
                        final = health_after_action
                        if backup is not None and not _codegraph_healthy(health_after_action):
                            _restore_index(backup, root)
                            final = validate_preflight(preflight_runner(root, **kwargs))
                            rollback_status = "restored"
                        record = _strategy_record(
                            strategy_id, action, argv, result, before, final,
                            parent_sha, backup_evidence, health_after_action,
                            rollback_status,
                        )
                        strategies.append(record)
                        parent_sha = record["evidence_sha256"]
                        if _recovery_fingerprint(final, semantic_query, expected_path) == fingerprint:
                            history = _remember_no_progress(history, key)
                            _write_history(history_path, history)
                    final_reasons_now = _reason_codes(final)
                    if final["status"] == "ready":
                        status = "ready"
                        terminal = "RECOVERED_AND_READY"
                        recovery_state = "closed"
                    else:
                        (
                            status, terminal, recovery_state, continuation_owner,
                            recheck_after_sec,
                        ) = _final_status(final_reasons_now)
                        if not strategies and any(
                            _strategy_key(strategy_id, _recovery_fingerprint(
                                final, semantic_query, expected_path
                            )) in history
                            for strategy_id, _action, _argv, _backup in candidates
                        ):
                            terminal = "NO_SAFE_UNTRIED_RECOVERY_STRATEGY"
                finally:
                    try:
                        lock.unlink()
                    except OSError:
                        pass

    report = {
        "schema": SCHEMA,
        "status": status,
        "terminal_reason_code": terminal,
        "repair_owner_role": "assigned_execution_agent:tool_recovery",
        "strategy_budget": RECOVERY_BUDGET,
        "strategy_attempts": len(strategies),
        "initial_preflight_sha256": _report_sha256(initial),
        "final_preflight_sha256": _report_sha256(final),
        "initial_status": initial["status"],
        "final_status": final["status"],
        "initial_reason_codes": initial_reasons,
        "final_reason_codes": _reason_codes(final),
        "strategies": strategies,
        "counts": dict(final["counts"]),
        "denominator": final["denominator"],
        "denominator_known": final["denominator_known"],
        "mutations": [
            strategy["action"] for strategy in strategies
            if strategy["status"] == "success"
        ],
        "failure_fingerprint_sha256": _recovery_fingerprint(
            initial, semantic_query, expected_path
        ),
        "recovery_state": recovery_state,
        "continuation_owner": continuation_owner,
        "recheck_after_sec": recheck_after_sec,
    }
    return validate_recovery_report(report)


def validate_recovery_report(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate the private adaptive artifact without changing V16 contracts."""

    if not isinstance(value, Mapping) or set(value) != _REPORT_FIELDS:
        raise ToolMaintenanceError("recovery report fields must match schema")
    result = dict(value)
    if result["schema"] != SCHEMA or result["status"] not in _RECOVERY_STATUSES:
        raise ToolMaintenanceError("recovery report status/schema invalid")
    if result["repair_owner_role"] != "assigned_execution_agent:tool_recovery":
        raise ToolMaintenanceError("recovery owner role mismatch")
    if result["strategy_budget"] != RECOVERY_BUDGET:
        raise ToolMaintenanceError("recovery strategy budget mismatch")
    if type(result["strategy_attempts"]) is not int or not 0 <= result["strategy_attempts"] <= RECOVERY_BUDGET:
        raise ToolMaintenanceError("recovery strategy attempts invalid")
    if result["recovery_state"] not in {
        "not_needed", "not_applicable", "awaiting_authorization",
        "single_flight_wait", "running", "closed", "exhausted",
        "outside_dependency_wait", "machine_recheck", "repair_deferred",
    }:
        raise ToolMaintenanceError("recovery state invalid")
    if result["continuation_owner"] not in {"machine", "none", "user"}:
        raise ToolMaintenanceError("recovery continuation owner invalid")
    recheck_after_sec = result["recheck_after_sec"]
    if recheck_after_sec is not None and (
        type(recheck_after_sec) is not int or not 1 <= recheck_after_sec <= 3600
    ):
        raise ToolMaintenanceError("recovery recheck interval invalid")
    if result["status"] in {"external_wait", "recovering"} and (
        result["continuation_owner"] != "machine" or recheck_after_sec is None
    ):
        raise ToolMaintenanceError("recovering/external wait requires machine recheck")
    if result["status"] == "degraded" and result["continuation_owner"] == "machine" and recheck_after_sec is None:
        raise ToolMaintenanceError("machine-owned degradation requires recheck")
    if result["status"] == "user_action_required" and (
        result["continuation_owner"] != "user" or recheck_after_sec is not None
    ):
        raise ToolMaintenanceError("user action status requires an authorization boundary")
    if result["status"] == "ready" and (
        result["continuation_owner"] != "none" or recheck_after_sec is not None
    ):
        raise ToolMaintenanceError("ready recovery cannot schedule continuation")
    if result["initial_status"] not in {"ready", "blocked"} or result["final_status"] not in {"ready", "blocked"}:
        raise ToolMaintenanceError("recovery preflight status invalid")
    for field in (
        "initial_preflight_sha256", "final_preflight_sha256",
        "failure_fingerprint_sha256",
    ):
        if not isinstance(result[field], str) or _SHA256.fullmatch(result[field]) is None:
            raise ToolMaintenanceError(f"recovery {field} invalid")
    for field in ("initial_reason_codes", "final_reason_codes", "mutations"):
        if not isinstance(result[field], list) or any(not isinstance(item, str) for item in result[field]):
            raise ToolMaintenanceError(f"recovery {field} must be a string list")
    strategies = result["strategies"]
    if not isinstance(strategies, list) or len(strategies) != result["strategy_attempts"]:
        raise ToolMaintenanceError("recovery strategy denominator mismatch")
    seen: set[str] = set()
    for strategy in strategies:
        if not isinstance(strategy, Mapping) or set(strategy) != _STRATEGY_FIELDS:
            raise ToolMaintenanceError("recovery strategy fields invalid")
        if strategy["strategy_id"] not in {
            "codegraph.init", "codegraph.sync", "codegraph.backup_index_rebuild",
        } or strategy["action"] not in {"init", "sync", "index"}:
            raise ToolMaintenanceError("recovery strategy not allowlisted")
        if strategy["strategy_id"] in seen:
            raise ToolMaintenanceError("recovery strategy repeated")
        seen.add(strategy["strategy_id"])
        if strategy["tool"] != "codegraph" or strategy["status"] not in {"success", "failure"}:
            raise ToolMaintenanceError("recovery strategy status invalid")
        if type(strategy["returncode"]) is not int:
            raise ToolMaintenanceError("recovery strategy return code invalid")
        for field in (
            "argv_sha256", "evidence_sha256", "input_preflight_sha256",
            "output_preflight_sha256", "health_after_action_sha256",
        ):
            if not isinstance(strategy[field], str) or _SHA256.fullmatch(strategy[field]) is None:
                raise ToolMaintenanceError(f"recovery strategy {field} invalid")
        if strategy["rollback_status"] not in {"not_needed", "restored"}:
            raise ToolMaintenanceError("recovery strategy rollback status invalid")
        for field in ("lineage_parent_sha256", "backup_evidence_sha256"):
            if strategy[field] is not None and (
                not isinstance(strategy[field], str) or _SHA256.fullmatch(strategy[field]) is None
            ):
                raise ToolMaintenanceError(f"recovery strategy {field} invalid")
    counts = result["counts"]
    expected_count_fields = {"total", "ran", "passed", "failed", "skipped", "xfail", "unknown"}
    if not isinstance(counts, Mapping) or set(counts) != expected_count_fields:
        raise ToolMaintenanceError("recovery counts fields invalid")
    if any(type(item) is not int or item < 0 for item in counts.values()):
        raise ToolMaintenanceError("recovery counts invalid")
    if counts["total"] != counts["passed"] + counts["failed"] or counts["ran"] != counts["total"]:
        raise ToolMaintenanceError("recovery count arithmetic invalid")
    if any(counts[name] for name in ("skipped", "xfail", "unknown")):
        raise ToolMaintenanceError("recovery denominator cannot be partial")
    if result["denominator"] != 3 or result["denominator_known"] is not True:
        raise ToolMaintenanceError("recovery denominator invalid")
    if result["status"] == "ready" and result["final_status"] != "ready":
        raise ToolMaintenanceError("ready recovery lacks final health check")
    if result["status"] != "ready" and result["final_status"] == "ready":
        raise ToolMaintenanceError("non-ready recovery contradicts final health")
    return result


__all__ = [
    "RECOVERY_BUDGET", "SCHEMA", "recover_toolchain", "validate_recovery_report",
]

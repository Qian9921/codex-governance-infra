#!/usr/bin/env python3
"""Privacy-safe, best-effort hook receipt helper.

Receipts contain only normalized metadata and hashes.  The writer drops any
unknown fields before serialization, so accidentally passing a hook payload
cannot persist prompts, raw arguments, working directories, token material, or
credentials.  A write failure is reported in the record/return value and is
never raised as a policy decision.
"""

from __future__ import annotations

import hashlib
import json
import os
import pathlib
import re
import stat
import time
from collections.abc import Mapping
from typing import Any


_LABEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,95}$")
_HASH = re.compile(r"^[0-9a-f]{64}$")
_SENSITIVE_LABEL = re.compile(
    r"(?:gh[pso]_|sk-|xox[baprs]-|akia[0-9a-z]|bearer|credential|password|prompt|secret|token)",
    re.IGNORECASE,
)
_ROUTE_CODES = frozenset({
    "preflight", "maintenance", "codegraph", "semble", "rtk", "rg",
    "contract", "unspecified",
})
_ROUTE_ALIASES = {"CodeGraph": "codegraph", "Semble": "semble"}
_DECISIONS = frozenset({"allow", "deny"})
_RECEIPT_STATUSES = frozenset({"not_written", "written", "write_failed"})
_RESPONSE_ENVELOPES = frozenset({
    "mapping", "json_string", "scalar_string", "null", "sequence", "scalar",
    "private_execution_state",
})
_RESPONSE_SHAPES = frozenset({
    "codex_app_exec", "mcp_result", "declared_result", "unknown",
    "pretool_wrapped_bash",
})
_RESPONSE_STATUSES = frozenset({"success", "failure", "contradictory", "incomplete", "unknown"})
_RESPONSE_TYPES = frozenset({"null", "boolean", "integer", "number", "string", "object", "array"})
_RESPONSE_KEYS = frozenset({
    "_meta", "chunk_id", "content", "exitCode", "exit_code", "isError",
    "is_error", "original_token_count", "output", "returncode", "session_id",
    "status", "stderr", "stdout", "structuredContent", "success",
    "wall_time_seconds",
})
SCHEMA_VERSION = "hook-receipt.v16"
_PACKAGE_ROOT = pathlib.Path(__file__).resolve().parent.parent
DEFAULT_CODEX_HOME = pathlib.Path(
    os.environ.get("CODEX_HOME", os.fspath(pathlib.Path.home() / ".codex"))
).expanduser()
DEFAULT_RECEIPT_DIR = DEFAULT_CODEX_HOME / "hook-receipts"
SNAPSHOT_FILES = (
    _PACKAGE_ROOT / "AGENTS.md",
    _PACKAGE_ROOT / "BRIEF-TEMPLATES.md",
    _PACKAGE_ROOT / "hooks.json",
    _PACKAGE_ROOT / "hooks" / "hooks.json",
    _PACKAGE_ROOT / "hooks" / "session_context.py",
    _PACKAGE_ROOT / "hooks" / "pre_tool_use_policy.py",
    _PACKAGE_ROOT / "hooks" / "post_tool_use_receipt.py",
    _PACKAGE_ROOT / "hooks" / "tool_execution_status.py",
    _PACKAGE_ROOT / "hooks" / "stop_tool_enforcement.py",
    _PACKAGE_ROOT / "hooks" / "hook_receipt.py",
    _PACKAGE_ROOT / "hooks" / "hooks_contract_test.py",
    _PACKAGE_ROOT / "v16" / "tool_preflight.py",
    _PACKAGE_ROOT / "v16" / "tool_routing.py",
    _PACKAGE_ROOT / "v16" / "tool_runtime.py",
    _PACKAGE_ROOT / "v16" / "tool_maintenance.py",
)
_SAFE_FIELDS = frozenset(
    {
        "schema",
        "schema_version",
        "utc",
        "event",
        "model",
        "tool_name",
        "decision",
        "reason",
        "reason_code",
        "route",
        "route_code",
        "snapshot_sha256",
        "identifiers_sha256",
        "session_id_sha256",
        "turn_id_sha256",
        "tool_call_id_sha256",
        "intake_id_sha256",
        "parent_intake_id_sha256",
        "agent_id_sha256",
        "response_diagnostics",
        "source",
        "pid",
        "ppid",
        "receipt_status",
    }
)


def _snapshot_sha256() -> str:
    """Hash the declared governance files without including receipt output."""
    entries: list[bytes] = []
    for path in SNAPSHOT_FILES:
        try:
            content_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        except (OSError, ValueError):
            content_hash = hashlib.sha256(b"<missing>").hexdigest()
        entries.append(path.as_posix().encode("utf-8") + b"\0" + content_hash.encode("ascii"))
    return hashlib.sha256(b"\n".join(entries)).hexdigest()


def _test_receipt_dir() -> pathlib.Path | None:
    if os.environ.get("CODEX_HOOK_SOURCE") != "test":
        return None
    override = os.environ.get("CODEX_HOOK_RECEIPT_DIR")
    return pathlib.Path(override) if override else None


def _default_receipt_path() -> pathlib.Path:
    directory = _test_receipt_dir() or DEFAULT_RECEIPT_DIR
    day = time.strftime("%Y-%m-%d", time.gmtime())
    return directory / f"{day}.jsonl"


def _identifiers(value: Mapping[str, Any] | None) -> dict[str, str]:
    value = value if isinstance(value, Mapping) else {}
    output: dict[str, str] = {}
    aliases = {
        "session_id_sha256": ("session_id", "sessionId"),
        "turn_id_sha256": ("turn_id", "turnId"),
        "tool_call_id_sha256": (
            "tool_use_id", "toolUseId", "tool_call_id", "toolCallId",
        ),
    }
    for target, names in aliases.items():
        for name in names:
            identifier = value.get(name)
            if isinstance(identifier, (str, int)) and str(identifier):
                output[target] = safe_hash(identifier) or ""
                break
    return output


def safe_hash(value: Any) -> str | None:
    """Hash an identifier without returning the identifier itself."""

    if value is None:
        return None
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()


def _label(value: Any, fallback: str) -> str:
    return (
        value
        if isinstance(value, str) and _LABEL.fullmatch(value) and not _SENSITIVE_LABEL.search(value)
        else fallback
    )


def _hash_label(value: Any) -> str | None:
    return value if isinstance(value, str) and _HASH.fullmatch(value) else None


def _route_code(value: Any) -> str:
    if not isinstance(value, str):
        return "unspecified"
    value = _ROUTE_ALIASES.get(value, value)
    value = value.lower()
    return value if value in _ROUTE_CODES else "unspecified"


def _reason_code(value: Any) -> str:
    normalized = _label(value, "unspecified_reason")
    return normalized.lower()


def _response_diagnostics(value: Any) -> dict[str, Any] | None:
    """Normalize a fixed structural diagnostic schema; raw values never pass."""

    if not isinstance(value, Mapping):
        return None
    if value.get("schema") != "tool-response-diagnostics.v16":
        return None
    envelope = value.get("envelope_type")
    shape = value.get("normalized_shape")
    status = value.get("normalized_status")
    keys = value.get("known_keys")
    types = value.get("key_types")
    unknown_count = value.get("unknown_key_count")
    if (
        envelope not in _RESPONSE_ENVELOPES
        or shape not in _RESPONSE_SHAPES
        or status not in _RESPONSE_STATUSES
        or not isinstance(keys, list)
        or any(key not in _RESPONSE_KEYS for key in keys)
        or keys != sorted(set(keys))
        or not isinstance(types, Mapping)
        or set(types) != set(keys)
        or any(kind not in _RESPONSE_TYPES for kind in types.values())
        or type(unknown_count) is not int
        or unknown_count < 0
    ):
        return None
    return {
        "schema": "tool-response-diagnostics.v16",
        "envelope_type": envelope,
        "normalized_shape": shape,
        "normalized_status": status,
        "known_keys": list(keys),
        "key_types": {key: types[key] for key in keys},
        "unknown_key_count": unknown_count,
    }


def receipt(
    event: Any,
    model: Any,
    tool: Any = None,
    decision: Any = None,
    reason: Any = None,
    snapshot_sha256: Any = None,
    *,
    route_code: Any = None,
    reason_code: Any = None,
    route: Any = None,
    identifiers: Mapping[str, Any] | None = None,
    task_id_sha256: Any = None,
    intake_id_sha256: Any = None,
    parent_intake_id_sha256: Any = None,
    agent_id_sha256: Any = None,
    response_diagnostics: Any = None,
) -> dict[str, Any]:
    """Create a normalized in-memory receipt; no file is written here."""

    task_id = os.environ.get("CODEX_TASK_ID")
    normalized_decision = decision if isinstance(decision, str) and decision in _DECISIONS else None
    normalized_route = _route_code(route_code if route_code is not None else route)
    normalized_reason = reason_code if reason_code is not None else reason
    value = {
        "schema": SCHEMA_VERSION,
        "schema_version": SCHEMA_VERSION,
        "utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "event": _label(event, "unknown_event"),
        "model": _label(model, "unknown_model"),
        "tool_name": _label(tool, "unknown_tool") if tool is not None else None,
        "decision": normalized_decision,
        "reason": _reason_code(normalized_reason),
        "reason_code": _reason_code(normalized_reason),
        "route": normalized_route,
        "route_code": normalized_route,
        "snapshot_sha256": _hash_label(snapshot_sha256) or _snapshot_sha256(),
        # The public Codex hook wire does not guarantee CODEX_TASK_ID.  Once
        # session_context has created a validated intake, its pre-hashed task
        # identity is the authoritative lifecycle key.  Keep the environment
        # variable only as a compatibility fallback for older/manual callers.
        "identifiers_sha256": (
            _hash_label(task_id_sha256)
            or (safe_hash(task_id) if task_id else None)
        ),
        "intake_id_sha256": _hash_label(intake_id_sha256),
        "parent_intake_id_sha256": _hash_label(parent_intake_id_sha256),
        "agent_id_sha256": _hash_label(agent_id_sha256),
        "response_diagnostics": _response_diagnostics(response_diagnostics),
        "source": "test" if os.environ.get("CODEX_HOOK_SOURCE") == "test" else "runtime",
        "pid": os.getpid(),
        "ppid": os.getppid(),
        "receipt_status": "not_written",
    }
    value.update(_identifiers(identifiers))
    return value


def has_receipt(value: Any) -> bool:
    """Return whether a receipt was successfully persisted by this helper."""

    return isinstance(value, Mapping) and value.get("receipt_status") == "written"


def _portable_record(value: Mapping[str, Any]) -> dict[str, Any]:
    """Select and normalize the allowlisted fields before serialization."""

    result = {key: value[key] for key in _SAFE_FIELDS if key in value}
    result["schema"] = SCHEMA_VERSION
    result["schema_version"] = SCHEMA_VERSION
    result["utc"] = _label(result.get("utc"), "unknown_time")
    result["event"] = _label(result.get("event"), "unknown_event")
    result["model"] = _label(result.get("model"), "unknown_model")
    result["tool_name"] = (
        _label(result.get("tool_name"), "unknown_tool")
        if result.get("tool_name") is not None
        else None
    )
    result["reason_code"] = _reason_code(result.get("reason_code"))
    result["reason"] = _reason_code(result.get("reason", result.get("reason_code")))
    result["route"] = _route_code(result.get("route", result.get("route_code")))
    result["route_code"] = _route_code(result.get("route_code"))
    decision = result.get("decision")
    result["decision"] = decision if isinstance(decision, str) and decision in _DECISIONS else None
    result["snapshot_sha256"] = _hash_label(result.get("snapshot_sha256")) or _snapshot_sha256()
    result["identifiers_sha256"] = _hash_label(result.get("identifiers_sha256"))
    for key in (
        "session_id_sha256", "turn_id_sha256", "tool_call_id_sha256",
        "intake_id_sha256", "parent_intake_id_sha256", "agent_id_sha256",
    ):
        result[key] = _hash_label(result.get(key))
    result["response_diagnostics"] = _response_diagnostics(
        result.get("response_diagnostics")
    )
    result["source"] = _label(
        result.get("source"),
        "test" if os.environ.get("CODEX_HOOK_SOURCE") == "test" else "runtime",
    )
    result["pid"] = result.get("pid") if isinstance(result.get("pid"), int) else os.getpid()
    result["ppid"] = result.get("ppid") if isinstance(result.get("ppid"), int) else os.getppid()
    status = result.get("receipt_status", "not_written")
    result["receipt_status"] = status if isinstance(status, str) and status in _RECEIPT_STATUSES else "not_written"
    return result


def write_receipt(value: Mapping[str, Any], path: str | os.PathLike[str] | None = None) -> bool:
    """Append one sanitized receipt and return ``False`` when persistence fails.

    The destination is explicit, supplied by ``CODEX_RECEIPT_PATH``, or the
    UTC-daily file under ``~/.codex/hook-receipts``.  Test runs may opt into a
    temporary directory with ``CODEX_HOOK_SOURCE=test`` and
    ``CODEX_HOOK_RECEIPT_DIR``.
    """

    if not isinstance(value, Mapping):
        return False
    if path is not None:
        destination = pathlib.Path(path)
    elif _test_receipt_dir() is not None:
        destination = _default_receipt_path()
    else:
        destination = pathlib.Path(os.environ.get("CODEX_RECEIPT_PATH") or _default_receipt_path())
    directory = destination.parent
    try:
        if directory.exists() and directory.is_symlink():
            raise OSError("receipt directory is a symlink")
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        directory.chmod(0o700)
        info = directory.lstat()
        if not stat.S_ISDIR(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o700:
            raise OSError("receipt directory permissions/type invalid")
    except (OSError, ValueError):
        try:
            value["receipt_status"] = "write_failed"  # type: ignore[index]
        except (TypeError, KeyError):
            pass
        return False
    payload = _portable_record(value)
    payload["receipt_status"] = "written"
    line = (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    fd = -1
    try:
        flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        fd = os.open(os.fspath(destination), flags, 0o600)
        os.fchmod(fd, 0o600)
        view = memoryview(line)
        offset = 0
        while offset < len(view):
            count = os.write(fd, view[offset:])
            remaining = len(view) - offset
            if not isinstance(count, int) or count <= 0 or count > remaining:
                raise OSError("receipt short/zero write")
            offset += count
    except (OSError, TypeError, ValueError):
        try:
            value["receipt_status"] = "write_failed"  # type: ignore[index]
        except (TypeError, KeyError):
            pass
        return False
    finally:
        if fd >= 0:
            try:
                os.close(fd)
            except OSError:
                pass
    try:
        value["receipt_status"] = "written"  # type: ignore[index]
    except (TypeError, KeyError):
        pass
    return True


if __name__ == "__main__":
    print(json.dumps(receipt("manual", "unknown"), sort_keys=True))

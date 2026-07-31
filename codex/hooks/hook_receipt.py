#!/usr/bin/env python3
"""Privacy-safe, append-only receipts for governance hook decisions.

The receipt deliberately stores hashes for platform identifiers and one combined
hash for the hook snapshot.  It never serializes prompts, tool arguments,
working directories, credentials, or environment contents.
"""

from __future__ import annotations

import datetime as _datetime
import hashlib
import json
import os
import pathlib
import stat
from typing import Any, Mapping


SCHEMA_VERSION = "hook-receipt.v1"
DEFAULT_RECEIPT_DIR = pathlib.Path(os.environ.get("CODEX_HOME", "~/.codex")).expanduser() / "hook-receipts"
SNAPSHOT_FILES = (
    pathlib.Path(os.environ.get("CODEX_HOME", "~/.codex")).expanduser() / "AGENTS.md",
    pathlib.Path(os.environ.get("CODEX_HOME", "~/.codex")).expanduser() / "BRIEF-TEMPLATES.md",
    pathlib.Path(os.environ.get("CODEX_HOME", "~/.codex")).expanduser() / "hooks.json",
    pathlib.Path(__file__).with_name("session_context.py"),
    pathlib.Path(__file__).with_name("pre_tool_use_policy.py"),
    pathlib.Path(__file__).with_name("hook_receipt.py"),
    pathlib.Path(__file__).with_name("hooks_contract_test.py"),
)
_IDENTIFIER_ALIASES = {
    "session_id": ("session_id", "sessionId"),
    "turn_id": ("turn_id", "turnId"),
    "tool_call_id": ("tool_call_id", "toolCallId"),
}


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _safe_snapshot_id() -> str:
    """Hash exact production inputs without including receipt output."""
    entries: list[bytes] = []
    for path in SNAPSHOT_FILES:
        try:
            data = path.read_bytes()
        except (OSError, ValueError):
            data = b"<missing>"
        entries.append(str(path).encode("utf-8") + b"\0" + _sha256_bytes(data).encode("ascii"))
    return _sha256_bytes(b"\n".join(entries))


def _identifier_hashes(payload: Mapping[str, Any]) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for output_name, aliases in _IDENTIFIER_ALIASES.items():
        for key in aliases:
            value = payload.get(key)
            if isinstance(value, (str, int)) and str(value):
                hashes[f"{output_name}_sha256"] = _sha256_bytes(str(value).encode("utf-8"))
                break
    return hashes


def _receipt_dir() -> pathlib.Path:
    # A test directory is accepted only with an explicit test source marker.
    if os.environ.get("CODEX_HOOK_SOURCE") == "test":
        override = os.environ.get("CODEX_HOOK_RECEIPT_DIR")
        if override:
            return pathlib.Path(override)
    return DEFAULT_RECEIPT_DIR


def _prepare_directory(directory: pathlib.Path) -> bool:
    try:
        if directory.exists() and directory.is_symlink():
            return False
        directory.mkdir(mode=0o700, parents=False, exist_ok=True)
        directory.chmod(0o700)
        # Path.stat(follow_symlinks=...) is unavailable on Python 3.9;
        # lstat() preserves the no-symlink-follow invariant across versions.
        info = directory.lstat()
        return stat.S_ISDIR(info.st_mode) and stat.S_IMODE(info.st_mode) == 0o700
    except (OSError, ValueError):
        return False


def _write_all(descriptor: int, data: bytes) -> bool:
    """Write every byte, rejecting errors and impossible progress.

    The receipt invariant is all-or-failure: a successful return means the
    complete JSONL line was handed to ``os.write``.  Short writes are normal
    on some descriptors, while zero, negative, non-integral, or overlong
    counts cannot make progress and therefore fail closed.
    """
    view = memoryview(data)
    offset = 0
    try:
        while offset < len(view):
            written = os.write(descriptor, view[offset:])
            remaining = len(view) - offset
            if not isinstance(written, int) or written <= 0 or written > remaining:
                return False
            offset += written
    except (OSError, TypeError, ValueError):
        return False
    return True


def _append_jsonl(directory: pathlib.Path, line: bytes, day: str) -> bool:
    if not _prepare_directory(directory):
        return False
    target = directory / f"{day}.jsonl"
    flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = -1
    try:
        descriptor = os.open(target, flags, 0o600)
        os.fchmod(descriptor, 0o600)
        return _write_all(descriptor, line)
    except (OSError, ValueError):
        return False
    finally:
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError:
                pass


def record_receipt(
    event: str,
    payload: Mapping[str, Any] | None = None,
    *,
    model: str | None = None,
    tool_name: str | None = None,
    decision: str = "allow",
    reason_code: str = "policy_allow",
) -> bool:
    """Best-effort receipt write; never raises into the policy decision path."""
    try:
        payload = payload if isinstance(payload, Mapping) else {}
        now = _datetime.datetime.now(_datetime.timezone.utc)
        source = "test" if os.environ.get("CODEX_HOOK_SOURCE") == "test" else "runtime"
        record: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "utc": now.isoformat().replace("+00:00", "Z"),
            "event": event or "unknown",
            "model": model if isinstance(model, str) else "unknown",
            "decision": decision,
            "reason_code": reason_code,
            "hook_snapshot_sha256": _safe_snapshot_id(),
            "source": source,
            "pid": os.getpid(),
            "ppid": os.getppid(),
        }
        if isinstance(tool_name, str) and tool_name:
            record["tool_name"] = tool_name
        record.update(_identifier_hashes(payload))
        encoded = (json.dumps(record, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n").encode(
            "utf-8"
        )
        return _append_jsonl(_receipt_dir(), encoded, now.date().isoformat())
    except Exception:
        # Receipt failures are evidence failures, not permission-policy failures.
        return False


__all__ = ["record_receipt", "SCHEMA_VERSION", "SNAPSHOT_FILES"]

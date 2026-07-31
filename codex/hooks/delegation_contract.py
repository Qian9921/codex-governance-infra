"""Atomic, descriptor-relative Luna -> Spark delegation contract.

The packet and state are one locked transaction: packet bytes are opened/read once
under the state lock, the raw packet hash and canonical mission hash are derived from
those exact bytes, and every transition revalidates repository HEAD, lease, packet,
mission and the versioned state shape before publishing state atomically.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import pathlib
import re
import stat
import subprocess
import tempfile
from dataclasses import dataclass
from typing import Any

try:
    import fcntl
except ImportError:  # pragma: no cover - unsupported platform is rejected
    fcntl = None


class ContractError(ValueError):
    pass

REQUIRED_PACKET = {
    "schema", "repo_root", "repo_snapshot", "parent_task_id", "child_task_id", "assigned_model", "role",
    "max_depth", "depth", "permissions", "forbidden_permissions", "lease", "retry_budget",
    "active_mission_lock", "plugin_inventory", "result_schema",
}
REQUIRED_RESULT = {
    "schema", "parent_task_id", "child_task_id", "assigned_model", "task_id", "depth", "attempt_id",
    "changed_paths", "counts", "retry_used", "retry_transcript", "contamination", "status",
    "artifact_sha256", "evidence_id",
}
SAFE_PERMISSIONS = {"read", "write_paths", "test", "inspect", "evidence"}
FORBIDDEN_CANONICAL = {
    "git", "github", "review", "approve", "merge", "shell", "bash", "git_push", "github_api",
    "reviewer", "approver", "merger",
}
STATUSES = {"complete", "blocked", "failed", "rejected"}
STATE_SCHEMA = "delegation-state.v3"
STATE_KEYS = {"schema", "delegations", "packets", "active"}
PACKET_RECORD_KEYS = {
    "packet_sha256", "mission_hash", "phase", "child_task_id", "assigned_model", "depth", "lease", "attempts", "attempt_id",
}
LEDGER_KEYS = {"attempts", "phase"}
ACTIVE_KEYS = {"task_id", "lease", "key"}
PHASES = {"REGISTERED", "STARTED", "RETRY_AVAILABLE", "ACCEPTED", "TERMINAL_REJECTED"}
LEASE_KEYS = {"paths"}
RETRY_KEYS = {"semantic_contamination"}
COUNT_KEYS = {"total", "ran", "passed", "failed", "skipped", "unknown"}
TRANSCRIPT_KEYS = {"attempt_id", "status", "reason"}
_ALLOWED_MODELS = {"gpt-5.6-luna", "gpt-5.3-codex-spark"}

O_DIRECTORY = getattr(os, "O_DIRECTORY", 0)
O_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
O_CLOEXEC = getattr(os, "O_CLOEXEC", 0)


def _id(value: Any) -> bool:
    return isinstance(value, str) and bool(re.fullmatch(r"[A-Za-z0-9_.:/-]+", value))


def _positive_id(value: Any) -> bool:
    return _id(value) and not value.startswith("-")


def _not_bool_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _sha256(value: Any) -> bool:
    return isinstance(value, str) and bool(re.fullmatch(r"[0-9a-f]{64}", value))


def _lstat(path: pathlib.Path):
    try:
        return os.lstat(path)
    except FileNotFoundError:
        return None


def _absolute(path: str | pathlib.Path) -> pathlib.Path:
    return pathlib.Path(os.path.abspath(os.fspath(path)))


def _open_dir_chain(path: str | pathlib.Path, *, create: bool = False, mode: int = 0o700) -> int:
    p = _absolute(path)
    fd = os.open(p.anchor or "/", os.O_RDONLY | O_DIRECTORY | O_CLOEXEC)
    try:
        for part in [x for x in p.parts if x not in (p.anchor, "")]:
            try:
                nxt = os.open(part, os.O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC, dir_fd=fd)
            except FileNotFoundError:
                if not create:
                    raise ContractError("missing state/repository ancestor")
                os.mkdir(part, mode=mode, dir_fd=fd)
                nxt = os.open(part, os.O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC, dir_fd=fd)
            os.close(fd); fd = nxt
        return fd
    except Exception:
        os.close(fd)
        raise


def _open_parent(path: str | pathlib.Path) -> tuple[int, str]:
    p = _absolute(path)
    return _open_dir_chain(p.parent, create=False), p.name


def _read_regular(path: str | pathlib.Path) -> tuple[bytes, os.stat_result]:
    parent, name = _open_parent(path)
    try:
        fd = os.open(name, os.O_RDONLY | O_NOFOLLOW | O_CLOEXEC, dir_fd=parent)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode):
                raise ContractError("packet/result must be regular file")
            chunks: list[bytes] = []
            while True:
                chunk = os.read(fd, 1024 * 1024)
                if not chunk:
                    break
                chunks.append(chunk)
            return b"".join(chunks), info
        finally:
            os.close(fd)
    finally:
        os.close(parent)


def _fsync(fd: int) -> None:
    try:
        os.fsync(fd)
    except OSError:
        pass


def _write_atomic_fd(root_fd: int, name: str, data: bytes) -> None:
    tmp_name = f".{name}.{os.getpid()}.{next(tempfile._get_candidate_names())}.tmp"
    fd = -1
    try:
        fd = os.open(tmp_name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | O_NOFOLLOW | O_CLOEXEC, 0o600, dir_fd=root_fd)
        view = memoryview(data)
        while view:
            n = os.write(fd, view)
            if n <= 0:
                raise ContractError("short state write")
            view = view[n:]
        os.fchmod(fd, 0o600); _fsync(fd); os.close(fd); fd = -1
        os.replace(tmp_name, name, src_dir_fd=root_fd, dst_dir_fd=root_fd)
        _fsync(root_fd)
    finally:
        if fd >= 0:
            os.close(fd)
        try:
            os.unlink(tmp_name, dir_fd=root_fd)
        except FileNotFoundError:
            pass


def normalize_path(value: Any) -> str:
    if not isinstance(value, str) or not value or "\\" in value or value.startswith("/") or re.match(r"^[A-Za-z]:", value):
        raise ContractError("non-relative path")
    parts = value.split("/")
    if any(x in {"", ".", ".."} for x in parts):
        raise ContractError("noncanonical path")
    return "/".join(parts)


def _paths(paths: Any) -> list[str]:
    if not isinstance(paths, list) or not paths:
        raise ContractError("lease paths")
    out = [normalize_path(x) for x in paths]
    if len(set(out)) != len(out):
        raise ContractError("duplicate lease")
    for i, a in enumerate(out):
        for b in out[i + 1:]:
            if a == b or a.startswith(b + "/") or b.startswith(a + "/"):
                raise ContractError("overlapping lease")
    return out


def _exact_dict(value: Any, keys: set[str], label: str) -> dict:
    if not isinstance(value, dict) or set(value) != keys:
        raise ContractError(label)
    return value


def _mission_hash(packet: dict) -> str:
    return hashlib.sha256(json.dumps(packet, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _json_loads_exact(raw: bytes) -> Any:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in items:
            if key in value:
                raise ContractError("duplicate JSON key")
            value[key] = item
        return value
    return json.loads(raw.decode("utf-8"), object_pairs_hook=pairs)


def _validate_lease(lease: Any) -> list[str]:
    obj = _exact_dict(lease, LEASE_KEYS, "lease schema")
    return _paths(obj["paths"])


def _validate_retry_budget(budget: Any) -> None:
    obj = _exact_dict(budget, RETRY_KEYS, "retry budget schema")
    if not _not_bool_int(obj["semantic_contamination"]) or obj["semantic_contamination"] != 1:
        raise ContractError("retry budget")


def _validate_state_shape(state: Any) -> None:
    if not isinstance(state, dict) or set(state) != STATE_KEYS or state.get("schema") != STATE_SCHEMA:
        raise ContractError("state schema")
    if not isinstance(state["delegations"], dict) or not isinstance(state["packets"], dict) or not isinstance(state["active"], list):
        raise ContractError("state containers")
    for key, rec in state["packets"].items():
        if not _sha256(key) or not isinstance(rec, dict) or set(rec) != PACKET_RECORD_KEYS:
            raise ContractError("packet state schema")
        if not _sha256(rec["packet_sha256"]) or not _sha256(rec["mission_hash"]):
            raise ContractError("packet state hashes")
        if rec["phase"] not in PHASES or not _id(rec["child_task_id"]) or rec["assigned_model"] not in _ALLOWED_MODELS or rec["depth"] != 1:
            raise ContractError("packet state identity")
        _validate_lease({"paths": rec["lease"]})
        if not isinstance(rec["attempts"], list) or any(not _id(x) for x in rec["attempts"]) or len(set(rec["attempts"])) != len(rec["attempts"]):
            raise ContractError("packet state attempts")
        if rec["attempt_id"] is not None and not _id(rec["attempt_id"]):
            raise ContractError("packet state attempt")
        if len(rec["attempts"]) > 2:
            raise ContractError("packet state retry overflow")
        if rec["phase"] in {"REGISTERED", "STARTED"} and rec["attempts"]:
            raise ContractError("unexpected attempts before contamination")
        if rec["phase"] == "RETRY_AVAILABLE" and len(rec["attempts"]) != 1:
            raise ContractError("retry phase attempt cardinality")
        if rec["phase"] in {"ACCEPTED", "TERMINAL_REJECTED"} and not rec["attempts"]:
            raise ContractError("terminal phase attempt cardinality")
    for key, rec in state["delegations"].items():
        if not _sha256(key) or not isinstance(rec, dict) or set(rec) != LEDGER_KEYS or rec["phase"] not in PHASES:
            raise ContractError("delegation state schema")
        if not isinstance(rec["attempts"], list) or any(not _id(x) for x in rec["attempts"]) or len(set(rec["attempts"])) != len(rec["attempts"]):
            raise ContractError("delegation state attempts")
        if len(rec["attempts"]) > 2 or key not in state["packets"]:
            raise ContractError("orphan delegation ledger")
    for rec in state["active"]:
        if not isinstance(rec, dict) or set(rec) != ACTIVE_KEYS or not _id(rec["task_id"]) or not _sha256(rec["key"]):
            raise ContractError("active lease schema")
        paths = _paths(rec["lease"])
        packet = state["packets"].get(rec["key"])
        if packet is None or rec["task_id"] != packet["child_task_id"] or paths != packet["lease"] or packet["phase"] not in {"REGISTERED", "STARTED", "RETRY_AVAILABLE"}:
            raise ContractError("active lease identity")
    active_keys = {x["key"] for x in state["active"]}
    if len(active_keys) != len(state["active"]):
        raise ContractError("duplicate active lease")
    for key, rec in state["packets"].items():
        led = state["delegations"].get(key)
        if led is None or led["attempts"] != rec["attempts"] or led["phase"] != rec["phase"]:
            raise ContractError("packet ledger mismatch")
    if not active_keys.issubset(state["packets"]):
        raise ContractError("active packet missing")


def _git_head(root: pathlib.Path) -> str | None:
    try:
        proc = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True, text=True, check=False)
    except OSError:
        return None
    value = proc.stdout.strip()
    return value if proc.returncode == 0 and re.fullmatch(r"[0-9a-f]{40}", value) else None


def _validate_repo_and_lease(packet: dict, verify_snapshot: bool = False) -> list[str]:
    root_raw = packet.get("repo_root")
    if not isinstance(root_raw, str) or not os.path.isabs(root_raw):
        raise ContractError("repo root")
    root = _absolute(root_raw)
    # Canonical root is checked with no-follow descriptors before git invocation.
    root_fd = _open_dir_chain(root, create=False)
    try:
        info = os.fstat(root_fd)
        if not stat.S_ISDIR(info.st_mode):
            raise ContractError("repo root canonical")
        if pathlib.Path(os.path.realpath(root)) != root:
            raise ContractError("repo root canonical")
        snapshot = packet.get("repo_snapshot")
        if not isinstance(snapshot, str) or not re.fullmatch(r"[0-9a-f]{40}", snapshot) or int(snapshot, 16) == 0:
            raise ContractError("repo snapshot")
        head = _git_head(root)
        if head is None or head != snapshot:
            raise ContractError("repo snapshot mismatch")
        paths = _validate_lease(packet.get("lease"))
        for rel in paths:
            cur = root
            for i, part in enumerate(rel.split("/")):
                cur /= part
                child = _lstat(cur)
                if child is None:
                    if i != len(rel.split("/")) - 1:
                        raise ContractError("missing lease ancestor")
                    continue
                if stat.S_ISLNK(child.st_mode):
                    raise ContractError("symlink lease path")
                if i != len(rel.split("/")) - 1 and not stat.S_ISDIR(child.st_mode):
                    raise ContractError("lease ancestor not directory")
        return paths
    finally:
        os.close(root_fd)


def validate_packet(packet: Any, parent_task_id: str | None = None, active_leases: list[list[str]] | None = None, *, verify_snapshot: bool = False) -> bool:
    if not isinstance(packet, dict) or set(packet) != REQUIRED_PACKET:
        raise ContractError("packet schema fields")
    if packet["schema"] != "delegation.v1" or packet["result_schema"] != "delegation-result.v1":
        raise ContractError("schema")
    _validate_repo_and_lease(packet, verify_snapshot=verify_snapshot)
    if parent_task_id is not None and packet["parent_task_id"] != parent_task_id:
        raise ContractError("parent mismatch")
    if not _positive_id(packet["parent_task_id"]) or not _positive_id(packet["child_task_id"]):
        raise ContractError("task identity")
    if packet["assigned_model"] not in _ALLOWED_MODELS or packet["role"] != "specialist":
        raise ContractError("model or role")
    if not _not_bool_int(packet["max_depth"]) or not _not_bool_int(packet["depth"]) or packet["max_depth"] != 1 or packet["depth"] != 1:
        raise ContractError("depth")
    if packet["active_mission_lock"] is not True or packet["plugin_inventory"] != "informational":
        raise ContractError("mission lock")
    perms = packet["permissions"]
    if not isinstance(perms, list) or len(perms) != len(set(perms)) or any(not isinstance(x, str) or x.lower() != x or x not in SAFE_PERMISSIONS for x in perms):
        raise ContractError("permission allowlist")
    forbidden = packet["forbidden_permissions"]
    if not isinstance(forbidden, list) or len(forbidden) != len(set(forbidden)) or set(forbidden) != FORBIDDEN_CANONICAL or any(not isinstance(x, str) for x in forbidden):
        raise ContractError("canonical forbidden set")
    if any(x in FORBIDDEN_CANONICAL or any(t in x for t in ("git", "github", "shell", "bash", "review", "approv", "merge")) for x in perms):
        raise ContractError("forbidden child permission")
    _validate_retry_budget(packet["retry_budget"])
    lease = packet["lease"]["paths"]
    if active_leases:
        for other in active_leases:
            for a in _paths(lease):
                for b in _paths(other):
                    if a == b or a.startswith(b + "/") or b.startswith(a + "/"):
                        raise ContractError("sibling lease overlap")
    return True


def _validate_transcript(transcript: Any, prior_attempts: list[str], current_attempt: str, *, expected_retry: int | None = None) -> None:
    if not isinstance(transcript, list) or len(transcript) > 1:
        raise ContractError("retry transcript type")
    seen: set[str] = set()
    for rec in transcript:
        if not isinstance(rec, dict) or set(rec) != TRANSCRIPT_KEYS:
            raise ContractError("retry transcript record")
        aid = rec["attempt_id"]
        if not _id(aid) or aid in seen or aid == current_attempt or (prior_attempts and aid not in prior_attempts):
            raise ContractError("retry transcript correlation")
        if rec["status"] != "contaminated" or not isinstance(rec["reason"], str) or not rec["reason"]:
            raise ContractError("retry transcript status")
        seen.add(aid)
    if expected_retry is not None and len(transcript) != expected_retry:
        raise ContractError("retry transcript/retry mismatch")


def _validate_counts(counts: Any) -> dict:
    if not isinstance(counts, dict) or set(counts) != COUNT_KEYS:
        raise ContractError("counts fields")
    if any(not _not_bool_int(counts[k]) or counts[k] < 0 for k in COUNT_KEYS):
        raise ContractError("counts types")
    if counts["total"] != counts["passed"] + counts["failed"] + counts["skipped"]:
        raise ContractError("count arithmetic")
    if counts["ran"] != counts["passed"] + counts["failed"] or counts["unknown"] != 0:
        raise ContractError("count arithmetic")
    return counts


def validate_result(result: Any, packet: dict, state: dict | None = None, *, allow_contamination: bool = False, record_state: bool = True) -> bool:
    expected_fields = set(REQUIRED_RESULT) | {"result_schema"}
    if not isinstance(result, dict) or set(result) != expected_fields:
        raise ContractError("result schema fields")
    if result["schema"] != "delegation-result.v1" or result["result_schema"] != "delegation-result.v1":
        raise ContractError("result schema")
    if result["parent_task_id"] != packet["parent_task_id"] or result["child_task_id"] != packet["child_task_id"] or result["task_id"] != packet["child_task_id"] or result["assigned_model"] != packet["assigned_model"]:
        raise ContractError("result identity")
    if not _not_bool_int(result["depth"]) or result["depth"] != packet["depth"] or not _id(result["attempt_id"]):
        raise ContractError("result attempt/depth")
    status = result["status"]
    if status not in STATUSES:
        raise ContractError("status")
    paths = result["changed_paths"]
    if not isinstance(paths, list) or any(not isinstance(x, str) for x in paths):
        raise ContractError("changed paths type")
    normalized = [normalize_path(x) for x in paths]
    if len(set(normalized)) != len(normalized):
        raise ContractError("duplicate changed path")
    lease = _paths(packet["lease"]["paths"])
    root = _absolute(packet["repo_root"])
    for rel in normalized:
        if not any(rel == p or rel.startswith(p + "/") for p in lease):
            raise ContractError("changed path outside lease")
        cur = root
        for i, part in enumerate(rel.split("/")):
            cur /= part
            info = _lstat(cur)
            if info is not None and stat.S_ISLNK(info.st_mode):
                raise ContractError("symlink changed path")
            if info is not None and i < len(rel.split("/")) - 1 and not stat.S_ISDIR(info.st_mode):
                raise ContractError("changed ancestor not directory")
    counts = _validate_counts(result["counts"])
    retry_used = result["retry_used"]
    if not _not_bool_int(retry_used) or retry_used not in (0, 1):
        raise ContractError("retry overflow")
    contamination = result["contamination"]
    if type(contamination) is not bool:
        raise ContractError("contamination type")
    prior: list[str] = []
    if isinstance(state, dict):
        try:
            key = state_key(packet)
            prior = list(state.get("delegations", {}).get(key, {}).get("attempts", []))
        except Exception:
            prior = []
    transcript = result["retry_transcript"]
    _validate_transcript(transcript, prior, result["attempt_id"], expected_retry=retry_used)
    if retry_used == 1 and not prior:
        raise ContractError("retry transcript requires persisted prior attempt")
    if not _sha256(result["artifact_sha256"]) or not _id(result["evidence_id"]):
        raise ContractError("evidence identity")
    legacy_partial_state = contamination and status == "complete" and isinstance(state, dict) and "packets" not in state
    if status == "complete":
        if counts["total"] <= 0 or counts["passed"] != counts["total"] or counts["failed"] or counts["skipped"] or counts["unknown"] or (contamination and not legacy_partial_state):
            raise ContractError("incomplete evidence")
    elif status == "blocked":
        if counts["total"] <= 0 or counts["skipped"] <= 0 or counts["ran"] != 0 or counts["passed"] or counts["failed"] or contamination:
            raise ContractError("blocked evidence")
    elif status == "failed":
        if counts["failed"] <= 0 or contamination:
            raise ContractError("failed evidence")
    elif status == "rejected":
        if counts["failed"] <= 0:
            raise ContractError("rejected evidence")
        if contamination and retry_used not in (0, 1):
            raise ContractError("contamination retry")
    if contamination and status != "rejected" and not legacy_partial_state:
        raise ContractError("contamination status")
    if state is not None and record_state:
        existing = state.get("delegations", {}).get(state_key(packet), {}).get("attempts", [])
        if result["attempt_id"] in existing:
            raise ContractError("attempt replay")
    # Preserve the historical in-memory validator contract: when a caller passes a
    # state object, the first fully validated contamination is recorded even though
    # the public result is rejected.  The production CLI passes record_state=False
    # and performs this mutation only in its locked transition.
    if contamination and state is not None and record_state:
        key = state_key(packet)
        ledgers = state.setdefault("delegations", {})
        rec = ledgers.setdefault(key, {"attempts": [], "phase": "STARTED"})
        if rec["attempts"] and result["attempt_id"] in rec["attempts"]:
            raise ContractError("attempt replay")
        if len(rec["attempts"]) >= 1:
            raise ContractError("contamination terminal")
        rec["attempts"].append(result["attempt_id"]); rec["phase"] = "RETRY_AVAILABLE"
    if contamination and not allow_contamination:
        raise ContractError("contaminated result; retry available")
    return True


def state_key(packet: dict) -> str:
    selected = {k: packet[k] for k in ("repo_root", "repo_snapshot", "parent_task_id", "child_task_id", "assigned_model", "depth", "lease")}
    return hashlib.sha256(json.dumps(selected, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _state_paths(root: str | pathlib.Path) -> tuple[pathlib.Path, pathlib.Path, pathlib.Path]:
    value = _absolute(root)
    return value, value / "delegation-state.json", value / ".delegation.lock"


def _default_state() -> dict:
    return {"schema": STATE_SCHEMA, "delegations": {}, "packets": {}, "active": []}


def _load_fd(root_fd: int) -> tuple[dict, bytes | None]:
    try:
        fd = os.open("delegation-state.json", os.O_RDONLY | O_NOFOLLOW | O_CLOEXEC, dir_fd=root_fd)
    except FileNotFoundError:
        state = _default_state(); _validate_state_shape(state); return state, None
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise ContractError("unsafe state file")
        os.fchmod(fd, 0o600)
        chunks: list[bytes] = []
        while True:
            b = os.read(fd, 1024 * 1024)
            if not b: break
            chunks.append(b)
        raw = b"".join(chunks)
    finally:
        os.close(fd)
    try:
        state = _json_loads_exact(raw)
    except Exception as exc:
        raise ContractError("state parse") from exc
    _validate_state_shape(state)
    return state, raw


def _save_fd(root_fd: int, state: dict) -> None:
    _validate_state_shape(state)
    data = (json.dumps(state, sort_keys=True, separators=(",", ":")) + "\n").encode()
    _write_atomic_fd(root_fd, "delegation-state.json", data)


@dataclass
class LockedSnapshot:
    root: pathlib.Path
    packet_path: pathlib.Path
    root_fd: int
    lock_fd: int
    packet: dict
    packet_bytes: bytes
    packet_sha256: str
    mission_hash: str
    state: dict

    @property
    def state_path(self) -> pathlib.Path:
        return self.root / "delegation-state.json"

    def save(self) -> None:
        _save_fd(self.root_fd, self.state)

    def revalidate(self, *, expected_phase: str | None = None) -> None:
        # The same locked packet bytes and state snapshot are the identity for this
        # transition; revalidate canonical repository/head and exact ledger relation.
        validate_packet(self.packet, verify_snapshot=True)
        key = state_key(self.packet)
        rec = self.state.get("packets", {}).get(key)
        if rec is None or rec["packet_sha256"] != self.packet_sha256 or rec["mission_hash"] != self.mission_hash:
            raise ContractError("packet ledger identity mismatch")
        if expected_phase is not None and rec.get("phase") != expected_phase:
            raise ContractError("unexpected transition phase")
        _validate_state_shape(self.state)


@contextlib.contextmanager
def locked_snapshot(state_root: str | pathlib.Path, packet_path: str | pathlib.Path):
    """Acquire lock before reading packet/state and yield a single read snapshot."""
    root = _absolute(state_root); packet_file = _absolute(packet_path)
    if fcntl is None:
        raise ContractError("unsupported fcntl platform")
    root_fd = _open_dir_chain(root, create=True, mode=0o700)
    lock_fd = -1
    try:
        lock_fd = os.open(".delegation.lock", os.O_RDWR | os.O_CREAT | O_NOFOLLOW | O_CLOEXEC, 0o600, dir_fd=root_fd)
        info = os.fstat(lock_fd)
        if not stat.S_ISREG(info.st_mode):
            raise ContractError("unsafe lock file")
        os.fchmod(lock_fd, 0o600)
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        packet_bytes, packet_info = _read_regular(packet_file)
        packet_sha = hashlib.sha256(packet_bytes).hexdigest()
        try:
            packet = _json_loads_exact(packet_bytes)
        except Exception as exc:
            raise ContractError("packet parse") from exc
        state, _ = _load_fd(root_fd)
        tx = LockedSnapshot(root, packet_file, root_fd, lock_fd, packet, packet_bytes, packet_sha, _mission_hash(packet), state)
        yield tx
    finally:
        if lock_fd >= 0:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
            finally:
                os.close(lock_fd)
        os.close(root_fd)


@contextlib.contextmanager
def state_lock(root: str | pathlib.Path):
    """Compatibility lock API; no caller may read state outside this lock."""
    if fcntl is None:
        raise ContractError("unsupported fcntl platform")
    root_path = _absolute(root); fd = _open_dir_chain(root_path, create=True, mode=0o700); lock_fd = -1
    try:
        lock_fd = os.open(".delegation.lock", os.O_RDWR | os.O_CREAT | O_NOFOLLOW | O_CLOEXEC, 0o600, dir_fd=fd)
        os.fchmod(lock_fd, 0o600); fcntl.flock(lock_fd, fcntl.LOCK_EX); yield fd
    finally:
        if lock_fd >= 0:
            fcntl.flock(lock_fd, fcntl.LOCK_UN); os.close(lock_fd)
        os.close(fd)


def _load(root: str | pathlib.Path) -> tuple[dict, pathlib.Path]:
    root_path = _absolute(root)
    with state_lock(root_path) as fd:
        state, _ = _load_fd(fd)
    return state, root_path / "delegation-state.json"


def _save(state: dict, path: pathlib.Path) -> None:
    root = _absolute(path).parent
    with state_lock(root) as fd:
        _save_fd(fd, state)


def _verify_registered(packet: dict, packet_path: pathlib.Path, expected: str, rec: dict, state: dict, *, mission: str | None = None) -> None:
    if not isinstance(rec, dict) or rec.get("packet_sha256") != expected:
        raise ContractError("packet identity mismatch")
    if rec.get("mission_hash") != (mission or _mission_hash(packet)):
        raise ContractError("mission hash mismatch")
    validate_packet(packet, verify_snapshot=True)
    _validate_state_shape(state)


def _active_leases(state: dict) -> list[list[str]]:
    return [x["lease"] for x in state.get("active", [])]


def _release(state: dict, key: str) -> None:
    state["active"] = [x for x in state.get("active", []) if x.get("key") != key]


def _ingest(tx: LockedSnapshot, result: dict) -> tuple[str, str]:
    key = state_key(tx.packet); rec = tx.state["packets"].get(key); ledger = tx.state["delegations"].get(key)
    if not rec or not ledger:
        raise ContractError("result without active packet")
    tx.revalidate()
    phase = rec["phase"]
    if phase not in {"STARTED", "RETRY_AVAILABLE"}:
        raise ContractError("result without active started record")
    expected_retry = 0 if phase == "STARTED" else 1
    if result.get("retry_used") != expected_retry:
        raise ContractError("retry state mismatch")
    # Validate before touching ledger.  Prior attempts are passed only for transcript
    # correlation; malformed second contamination therefore leaves bytes untouched.
    validate_result(result, tx.packet, tx.state, allow_contamination=True, record_state=False)
    attempts = ledger["attempts"]
    aid = result["attempt_id"]
    if aid in attempts:
        raise ContractError("attempt replay")
    if result["contamination"]:
        if phase == "STARTED":
            if attempts or result["retry_used"] != 0 or result["retry_transcript"]:
                raise ContractError("first contamination correlation")
            attempts.append(aid); ledger["phase"] = rec["phase"] = "RETRY_AVAILABLE"; rec["attempts"] = list(attempts)
            tx.save()
            raise ContractError("contaminated result; retry available")
        if phase == "RETRY_AVAILABLE":
            if len(attempts) != 1 or result["retry_used"] != 1 or len(result["retry_transcript"]) != 1 or result["retry_transcript"][0]["attempt_id"] != attempts[0]:
                raise ContractError("second contamination correlation")
            attempts.append(aid); ledger["phase"] = rec["phase"] = "TERMINAL_REJECTED"; rec["attempts"] = list(attempts); rec["attempt_id"] = aid; _release(tx.state, key); tx.save()
            raise ContractError("contamination terminal")
        raise ContractError("contamination terminal")
    if phase == "RETRY_AVAILABLE" and (len(attempts) != 1 or result["retry_used"] != 1 or len(result["retry_transcript"]) != 1 or result["retry_transcript"][0]["attempt_id"] != attempts[0]):
        raise ContractError("retry transcript correlation")
    attempts.append(aid); rec["attempts"] = list(attempts); rec["attempt_id"] = aid
    final = "ACCEPTED" if result["status"] == "complete" else "TERMINAL_REJECTED"
    ledger["phase"] = rec["phase"] = final; _release(tx.state, key); tx.save()
    return ("accept" if final == "ACCEPTED" else "reject", aid)


def cli() -> int:
    ap = argparse.ArgumentParser(); sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("pre-dispatch", "subagent-start", "ingest-result"):
        q = sub.add_parser(name); q.add_argument("--packet", required=True); q.add_argument("--state-root", required=True); q.add_argument("--result")
    args = ap.parse_args()
    with locked_snapshot(args.state_root, args.packet) as tx:
        key = state_key(tx.packet)
        if args.cmd == "pre-dispatch":
            validate_packet(tx.packet, active_leases=_active_leases(tx.state), verify_snapshot=True)
            if key in tx.state["packets"]:
                raise ContractError("packet already registered")
            rec = {
                "packet_sha256": tx.packet_sha256, "mission_hash": tx.mission_hash, "phase": "REGISTERED",
                "child_task_id": tx.packet["child_task_id"], "assigned_model": tx.packet["assigned_model"],
                "depth": tx.packet["depth"], "lease": tx.packet["lease"]["paths"], "attempts": [], "attempt_id": None,
            }
            tx.state["packets"][key] = rec; tx.state["delegations"][key] = {"attempts": [], "phase": "REGISTERED"}
            tx.state["active"].append({"task_id": tx.packet["child_task_id"], "lease": tx.packet["lease"]["paths"], "key": key})
            tx.save(); print(json.dumps({"decision": "allow", "mission_hash": tx.mission_hash})); return 0
        rec = tx.state["packets"].get(key)
        if args.cmd == "subagent-start":
            event = os.environ.get("CODEX_DELEGATION_EVENT"); model = os.environ.get("CODEX_DELEGATION_MODEL"); task = os.environ.get("CODEX_DELEGATION_TASK_ID")
            if event != "SubagentStart" or model != tx.packet["assigned_model"] or task != tx.packet["child_task_id"] or os.environ.get("CODEX_DELEGATION_PACKET_SHA256") != tx.packet_sha256 or not rec or rec.get("phase") != "REGISTERED":
                raise ContractError("missing, wrong, unregistered, duplicate, or mismatched SubagentStart")
            _verify_registered(tx.packet, tx.packet_path, tx.packet_sha256, rec, tx.state, mission=tx.mission_hash)
            rec["phase"] = tx.state["delegations"][key]["phase"] = "STARTED"; tx.save()
            print(json.dumps({"decision": "allow", "packet_sha256": tx.packet_sha256, "mission_hash": tx.mission_hash})); return 0
        if not args.result or not rec:
            raise ContractError("result without active started record")
        result_bytes, _ = _read_regular(args.result)
        try: result = _json_loads_exact(result_bytes)
        except Exception as exc: raise ContractError("result parse") from exc
        decision, aid = _ingest(tx, result)
        print(json.dumps({"decision": decision, "attempt_id": aid})); return 0


if __name__ == "__main__":
    try:
        raise SystemExit(cli())
    except (ContractError, OSError, json.JSONDecodeError) as exc:
        print(json.dumps({"decision": "reject", "reason": str(exc)})); raise SystemExit(2)

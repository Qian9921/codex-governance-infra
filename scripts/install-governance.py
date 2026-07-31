#!/usr/bin/env python3
"""Install the managed Codex package with descriptor-relative no-follow transactions.

The installer deliberately treats every path as untrusted.  Existing ancestors are
opened one component at a time with ``O_DIRECTORY|O_NOFOLLOW`` and all mutations use
those directory descriptors.  A path is only used for diagnostics and source
enumeration; it is never re-resolved between validation and a mutation.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import re
import shlex
import stat
import tempfile
from typing import Iterable

FORBIDDEN = ("sessions", "hook-receipts", "plugins", "connections", "models_cache.json", ".env")
SCHEMA = "install-transaction.v3"
STATE_KEYS = {"schema", "destination", "managed", "created_dirs", "backup"}
RECORD_KEYS = {
    "path", "exists", "sha256", "mode", "type", "source_sha256", "installed_sha256", "installed_mode"
}
O_DIRECTORY = getattr(os, "O_DIRECTORY", 0)
O_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
O_CLOEXEC = getattr(os, "O_CLOEXEC", 0)


class InstallError(RuntimeError):
    """An unsafe object or incomplete transaction."""


def normalize_rel(value: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or "\\" in value
        or value.startswith("/")
        or re.match(r"^[A-Za-z]:", value)
    ):
        raise InstallError("noncanonical managed path")
    parts = value.split("/")
    if any(x in {"", ".", ".."} for x in parts):
        raise InstallError("noncanonical managed path")
    return "/".join(parts)


def _valid_hash(value, *, nullable: bool = False) -> bool:
    return value is None if nullable else isinstance(value, str) and bool(re.fullmatch(r"[0-9a-f]{64}", value))


def validate_transaction(record: dict, dest: pathlib.Path, backup: pathlib.Path, *, allow_uninstalled: bool = False) -> None:
    """Validate the complete ledger before any transaction mutation.

    Ledger paths are canonical, unique, and manifest-owned.  We intentionally do
    not call ``Path.resolve`` here: resolving would follow an attacker-controlled
    symlink.  Descriptor checks happen at each use site.
    """
    if not isinstance(record, dict) or set(record) != STATE_KEYS or record.get("schema") != SCHEMA:
        raise InstallError("invalid transaction state schema")
    if record.get("destination") != str(dest) or record.get("backup") != str(backup):
        raise InstallError("transaction destination mismatch")
    managed = record.get("managed")
    created = record.get("created_dirs")
    if not isinstance(managed, list) or not managed or not isinstance(created, list):
        raise InstallError("invalid transaction ledger")
    for rel in created:
        if not isinstance(rel, str):
            raise InstallError("invalid created directory")
        if rel:
            normalize_rel(rel)
    if len(set(created)) != len(created):
        raise InstallError("duplicate created directory")
    paths: list[str] = []
    for rec in managed:
        if not isinstance(rec, dict) or set(rec) != RECORD_KEYS:
            raise InstallError("invalid managed record schema")
        rel = normalize_rel(rec["path"])
        paths.append(rel)
        if not isinstance(rec["exists"], bool) or rec["type"] not in {"file", "missing"}:
            raise InstallError("invalid managed record type")
        if rec["exists"]:
            if (
                rec["type"] != "file"
                or not _valid_hash(rec["sha256"])
                or not isinstance(rec["mode"], int)
                or isinstance(rec["mode"], bool)
                or not 0 <= rec["mode"] <= 0o7777
            ):
                raise InstallError("invalid prior object")
        elif rec["type"] != "missing" or rec["sha256"] is not None or rec["mode"] is not None:
            raise InstallError("invalid missing object")
        if not _valid_hash(rec["source_sha256"]):
            raise InstallError("invalid source object")
        if allow_uninstalled and rec["installed_sha256"] is None and rec["installed_mode"] is None:
            continue
        if (
            not _valid_hash(rec["installed_sha256"])
            or not isinstance(rec["installed_mode"], int)
            or isinstance(rec["installed_mode"], bool)
            or not 0 <= rec["installed_mode"] <= 0o7777
        ):
            raise InstallError("invalid installed object")
    if len(paths) != len(set(paths)):
        raise InstallError("duplicate managed path")
    created_set = {x for x in created if x}
    if set(paths) & created_set:
        raise InstallError("managed/created path overlap")
    for rel in created_set:
        if not any(p.startswith(rel + "/") for p in paths):
            raise InstallError("undeclared created directory")


def digest(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _digest_fd(fd: int) -> str:
    h = hashlib.sha256()
    os.lseek(fd, 0, os.SEEK_SET)
    while True:
        chunk = os.read(fd, 1024 * 1024)
        if not chunk:
            break
        h.update(chunk)
    os.lseek(fd, 0, os.SEEK_SET)
    return h.hexdigest()


def lexists(path: pathlib.Path) -> bool:
    return os.path.lexists(os.fspath(path))


def lstat_or_none(path: pathlib.Path):
    try:
        return os.lstat(path)
    except FileNotFoundError:
        return None


def _absolute(path: str | pathlib.Path) -> pathlib.Path:
    return pathlib.Path(os.path.abspath(os.fspath(path)))


def check_chain(path: pathlib.Path, *, allow_missing_final: bool = True) -> list[pathlib.Path]:
    """Read-only no-follow check retained for callers and diagnostics."""
    path = _absolute(path)
    missing: list[pathlib.Path] = []
    cur = pathlib.Path(path.anchor)
    parts = path.parts[1:]
    for i, part in enumerate(parts):
        cur /= part
        info = lstat_or_none(cur)
        if info is None:
            missing.append(cur)
            continue
        if stat.S_ISLNK(info.st_mode):
            raise InstallError(f"symlink path component refused: {cur}")
        if not stat.S_ISDIR(info.st_mode) and cur != path:
            raise InstallError(f"non-directory ancestor refused: {cur}")
    if not allow_missing_final and lstat_or_none(path) is None:
        raise InstallError(f"missing required path: {path}")
    return missing


def _parts_abs(path: pathlib.Path) -> list[str]:
    p = _absolute(path)
    if not p.is_absolute():
        raise InstallError("absolute path required")
    return [x for x in p.parts if x not in (p.anchor, "")]


def _open_dir_chain(path: pathlib.Path, *, create: bool = False, mode: int = 0o700) -> int:
    """Open an absolute directory one component at a time without following links."""
    p = _absolute(path)
    fd = os.open(p.anchor or "/", os.O_RDONLY | O_DIRECTORY | O_CLOEXEC)
    try:
        for part in _parts_abs(p):
            try:
                nxt = os.open(part, os.O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC, dir_fd=fd)
            except FileNotFoundError:
                if not create:
                    raise
                os.mkdir(part, mode=mode, dir_fd=fd)
                _fsync_fd(fd)
                nxt = os.open(part, os.O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC, dir_fd=fd)
            os.close(fd)
            fd = nxt
        return fd
    except Exception:
        os.close(fd)
        raise


def _open_parent_dir(path: pathlib.Path) -> tuple[int, str]:
    p = _absolute(path)
    return _open_dir_chain(p.parent, create=False), p.name


def _open_regular_at(parent_fd: int, name: str, *, writable: bool = False) -> tuple[int, os.stat_result]:
    flags = (os.O_RDWR if writable else os.O_RDONLY) | O_NOFOLLOW | O_CLOEXEC
    fd = os.open(name, flags, dir_fd=parent_fd)
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode):
        os.close(fd)
        raise InstallError(f"managed object must be regular file: {name}")
    return fd, info


def _read_bytes_at(parent_fd: int, name: str) -> tuple[bytes, os.stat_result]:
    fd, info = _open_regular_at(parent_fd, name)
    try:
        os.lseek(fd, 0, os.SEEK_SET)
        data = bytearray()
        while True:
            chunk = os.read(fd, 1024 * 1024)
            if not chunk:
                break
            data.extend(chunk)
        return bytes(data), info
    finally:
        os.close(fd)


def _fsync_fd(fd: int) -> None:
    try:
        os.fsync(fd)
    except OSError:
        pass


def _fsync_dir_fd(fd: int) -> None:
    _fsync_fd(fd)


def _fsync_dir(path: pathlib.Path) -> None:
    try:
        fd = _open_dir_chain(path, create=False)
    except OSError:
        return
    try:
        _fsync_dir_fd(fd)
    finally:
        os.close(fd)


def _failpoint(counter: list[int], label: str) -> None:
    counter[0] += 1
    raw = os.environ.get("CODEX_INSTALL_FAIL_AFTER", "")
    if raw:
        try:
            if int(raw) == counter[0]:
                raise InstallError(f"injected failure at mutation {counter[0]} ({label})")
        except ValueError as exc:
            raise InstallError("CODEX_INSTALL_FAIL_AFTER must be an integer") from exc


def _atomic_write_at(parent_fd: int, name: str, data: bytes, mode: int, counter: list[int], label: str) -> None:
    """Write and publish a regular file relative to an already-open directory."""
    tmp_name = f".{name}.{os.getpid()}.{next(tempfile._get_candidate_names())}.tmp"
    fd = -1
    try:
        fd = os.open(tmp_name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | O_NOFOLLOW | O_CLOEXEC, 0o600, dir_fd=parent_fd)
        os.fchmod(fd, mode)
        view = memoryview(data)
        while view:
            n = os.write(fd, view)
            if n <= 0:
                raise InstallError("short atomic write")
            view = view[n:]
        _fsync_fd(fd)
        os.close(fd); fd = -1
        _failpoint(counter, label + ":temp")
        # renameat2 is not required: replacing a final symlink replaces the link,
        # never follows it.  Ancestors remain pinned by parent_fd.
        os.replace(tmp_name, name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
        _fsync_dir_fd(parent_fd)
        _failpoint(counter, label + ":replace")
    finally:
        if fd >= 0:
            os.close(fd)
        try:
            os.unlink(tmp_name, dir_fd=parent_fd)
        except FileNotFoundError:
            pass


def _atomic_copy_at(src_parent_fd: int, src_name: str, dst_parent_fd: int, dst_name: str, mode: int, counter: list[int], label: str) -> None:
    src_fd, info = _open_regular_at(src_parent_fd, src_name)
    try:
        data = bytearray()
        while True:
            chunk = os.read(src_fd, 1024 * 1024)
            if not chunk:
                break
            data.extend(chunk)
    finally:
        os.close(src_fd)
    _atomic_write_at(dst_parent_fd, dst_name, bytes(data), mode, counter, label)


def _atomic_copy(src: pathlib.Path, dst: pathlib.Path, mode: int, counter: list[int], label: str) -> None:
    """Compatibility wrapper using descriptor-relative operations."""
    src_parent, src_name = _open_parent_dir(src)
    dst_parent, dst_name = _open_parent_dir(dst)
    try:
        _atomic_copy_at(src_parent, src_name, dst_parent, dst_name, mode, counter, label)
    finally:
        os.close(src_parent); os.close(dst_parent)


def _write_bytes(dst: pathlib.Path, data: bytes, mode: int, counter: list[int], label: str) -> None:
    parent, name = _open_parent_dir(dst)
    try:
        _atomic_write_at(parent, name, data, mode, counter, label)
    finally:
        os.close(parent)


def _mkdirs_at(root_fd: int, rel_parent: str, created_dirs: list[str]) -> int:
    """Return an fd for ``rel_parent``, creating only missing regular directories."""
    current_fd = os.dup(root_fd)
    if rel_parent:
        components = rel_parent.split("/")
        walked: list[str] = []
        try:
            for component in components:
                walked.append(component)
                try:
                    nxt = os.open(component, os.O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC, dir_fd=current_fd)
                except FileNotFoundError:
                    os.mkdir(component, mode=0o700, dir_fd=current_fd)
                    _fsync_dir_fd(current_fd)
                    created_dirs.append("/".join(walked))
                    _failpoint(_MUTATION_COUNTER, "directory-mkdir")
                    nxt = os.open(component, os.O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC, dir_fd=current_fd)
                os.close(current_fd); current_fd = nxt
            return current_fd
        except Exception:
            os.close(current_fd)
            raise
    return current_fd


# Set only for the one transaction invocation; this avoids threading mutable counters
# through every directory helper while preserving dynamic failpoint coverage.
_MUTATION_COUNTER: list[int] = [0]


def _safe_entries_walk(root: pathlib.Path) -> list[tuple[str, pathlib.Path]]:
    """Enumerate package files with lstat and no-follow directory descriptors."""
    source = _absolute(root); codex = source / "codex"
    codex_fd = _open_dir_chain(codex, create=False)
    out: list[tuple[str, pathlib.Path]] = []
    try:
        def walk(fd: int, rel_prefix: str) -> None:
            names = sorted(os.listdir(fd))
            for name in names:
                if name in {".", ".."}:
                    continue
                rel = f"{rel_prefix}/{name}" if rel_prefix else name
                if "__pycache__" in rel.split("/") or rel.endswith(".pyc"):
                    continue
                try:
                    child_fd = os.open(name, os.O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC, dir_fd=fd)
                except NotADirectoryError:
                    child_fd = -1
                except FileNotFoundError:
                    continue
                if child_fd >= 0:
                    try:
                        walk(child_fd, rel)
                    finally:
                        os.close(child_fd)
                    continue
                file_fd, info = _open_regular_at(fd, name)
                os.close(file_fd)
                if not (rel.startswith("hooks/") or rel.startswith("contracts/") or rel in {"AGENTS.md", "BRIEF-TEMPLATES.md", "hooks.json", "V15_RELEASE.json"}):
                    raise InstallError(f"outside install allowlist: {rel}")
                if any(part in rel.lower() for part in FORBIDDEN):
                    raise InstallError(f"forbidden managed path: {rel}")
                out.append((rel, codex / rel))
        walk(codex_fd, "")
    finally:
        os.close(codex_fd)
    if not out:
        raise InstallError("source codex package is empty")
    return out


def safe_entries(source: pathlib.Path) -> list[tuple[str, pathlib.Path]]:
    return _safe_entries_walk(source)


def _read_source_rel(source: pathlib.Path, rel: str) -> bytes:
    root_fd = _open_dir_chain(_absolute(source) / "codex", create=False)
    try:
        parent_fd = root_fd
        opened: list[int] = []
        parts = rel.split("/")
        try:
            for part in parts[:-1]:
                nxt = os.open(part, os.O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC, dir_fd=parent_fd)
                opened.append(nxt); parent_fd = nxt
            data, _ = _read_bytes_at(parent_fd, parts[-1])
            return data
        finally:
            for fd in reversed(opened): os.close(fd)
    finally:
        os.close(root_fd)


def transformed_bytes(source: pathlib.Path, target: str, dest: pathlib.Path, raw: bytes | None = None) -> bytes:
    data = raw if raw is not None else _read_source_rel(source, target)
    if target != "hooks.json":
        return data
    obj = json.loads(data.decode("utf-8"))
    quoted = shlex.quote(os.fspath(dest))
    for groups in obj.get("hooks", {}).values():
        for matcher in groups:
            for hook in matcher.get("hooks", []):
                command = hook.get("command")
                if isinstance(command, str):
                    command = re.sub(r'"\$CODEX_HOME/([^"\s]+)"', lambda m: shlex.quote(os.fspath(dest / m.group(1))), command)
                    command = re.sub(r"\$CODEX_HOME/([^\s'\"]+)", lambda m: shlex.quote(os.fspath(dest / m.group(1))), command)
                    hook["command"] = command.replace("$CODEX_HOME", quoted)
    return (json.dumps(obj, sort_keys=True, indent=2) + "\n").encode("utf-8")


def _object_record(path: pathlib.Path, root: pathlib.Path) -> dict:
    info = lstat_or_none(path)
    if info is None:
        return {"path": path.relative_to(root).as_posix(), "exists": False, "sha256": None, "mode": None, "type": "missing"}
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise InstallError(f"managed object must be regular file: {path}")
    return {"path": path.relative_to(root).as_posix(), "exists": True, "sha256": digest(path), "mode": stat.S_IMODE(info.st_mode), "type": "file"}


def _remove_tree_fd(parent_fd: int, name: str) -> None:
    """Remove a tree without following any symlink; used only after exact restore."""
    try:
        fd = os.open(name, os.O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC, dir_fd=parent_fd)
    except FileNotFoundError:
        return
    try:
        for entry in list(os.listdir(fd)):
            try:
                child = os.open(entry, os.O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC, dir_fd=fd)
            except NotADirectoryError:
                os.unlink(entry, dir_fd=fd)
            except FileNotFoundError:
                continue
            else:
                os.close(child)
                _remove_tree_fd(fd, entry)
        os.rmdir(name, dir_fd=parent_fd)
    finally:
        os.close(fd)


def _remove_empty_dirs_at(root_fd: int, paths: Iterable[str]) -> None:
    for rel in sorted({p for p in paths if p}, key=lambda x: len(x.split("/")), reverse=True):
        parts = rel.split("/"); parent_fd = os.dup(root_fd)
        try:
            for part in parts[:-1]:
                nxt = os.open(part, os.O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC, dir_fd=parent_fd)
                os.close(parent_fd); parent_fd = nxt
            try: os.rmdir(parts[-1], dir_fd=parent_fd)
            except (FileNotFoundError, OSError): pass
        finally: os.close(parent_fd)


def _open_rel_parent(root_fd: int, rel: str) -> tuple[int, str]:
    parts = rel.split("/"); parent_fd = os.dup(root_fd)
    try:
        for part in parts[:-1]:
            nxt = os.open(part, os.O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC, dir_fd=parent_fd)
            os.close(parent_fd); parent_fd = nxt
        return parent_fd, parts[-1]
    except Exception:
        os.close(parent_fd); raise


def _target_snapshot(root_fd: int, rel: str) -> tuple[bytes | None, int | None, str]:
    try:
        parent, name = _open_rel_parent(root_fd, rel)
    except FileNotFoundError:
        return None, None, "missing"
    try:
        try:
            fd, info = _open_regular_at(parent, name)
        except FileNotFoundError:
            return None, None, "missing"
        try:
            data = os.read(fd, 0)  # ensure fd is live before snapshot
            os.lseek(fd, 0, os.SEEK_SET)
            chunks = []
            while True:
                b = os.read(fd, 1024 * 1024)
                if not b: break
                chunks.append(b)
            return b"".join(chunks), stat.S_IMODE(info.st_mode), "file"
        finally: os.close(fd)
    finally: os.close(parent)


def _rollback_records(dest: pathlib.Path, backup: pathlib.Path, records: list[dict], created_dirs: list[str], *, require_current: bool = False) -> None:
    """Validate all backups and installed modes before mutation, then restore safely."""
    validate_transaction({"schema": SCHEMA, "destination": str(dest), "managed": records, "created_dirs": created_dirs, "backup": str(backup)}, dest, backup, allow_uninstalled=True)
    dest_fd = _open_dir_chain(dest, create=False)
    backup_fd = _open_dir_chain(backup, create=False)
    snapshots: dict[str, tuple[bytes | None, int | None, str]] = {}
    plan: list[tuple[dict, str]] = []
    try:
        # Complete preflight: no target or backup is touched until every type/hash/mode
        # and every ancestor has been checked through O_NOFOLLOW descriptors.
        for rec in records:
            rel = rec["path"]
            current = _target_snapshot(dest_fd, rel)
            snapshots[rel] = current
            data, mode, kind = current
            if require_current:
                if kind != "file" or hashlib.sha256(data or b"").hexdigest() != rec["installed_sha256"] or mode != rec["installed_mode"]:
                    raise InstallError("rollback refused: managed target changed:" + rel)
            if rec["exists"]:
                try:
                    parent, name = _open_rel_parent(backup_fd, rel)
                    try:
                        bfd, binfo = _open_regular_at(parent, name)
                        try:
                            if _digest_fd(bfd) != rec["sha256"] or stat.S_IMODE(binfo.st_mode) != rec["mode"]:
                                raise InstallError("corrupt transaction backup:" + rel)
                        finally: os.close(bfd)
                    finally: os.close(parent)
                except FileNotFoundError:
                    # A failpoint may fire before this record is copied.  It is
                    # safe to treat an untouched pre-existing object as a no-op;
                    # a changed/missing object still requires a complete backup.
                    if kind == "file" and hashlib.sha256(data or b"").hexdigest() == rec["sha256"] and mode == rec["mode"]:
                        plan.append((rec, "noop")); continue
                    if kind == "missing":
                        plan.append((rec, "noop")); continue
                    raise InstallError("missing transaction backup:" + rel)
                plan.append((rec, "restore"))
            else:
                if kind not in {"missing", "file"}:
                    raise InstallError("rollback encountered unexpected managed object:" + rel)
                plan.append((rec, "remove" if kind == "file" else "noop"))
        for rec, action in reversed(plan):
            rel = rec["path"]
            if action == "noop":
                continue
            parent, name = _open_rel_parent(dest_fd, rel)
            try:
                if action == "restore":
                    src_parent, src_name = _open_rel_parent(backup_fd, rel)
                    try:
                        _atomic_copy_at(src_parent, src_name, parent, name, int(rec["mode"]), _MUTATION_COUNTER, "rollback")
                    finally: os.close(src_parent)
                elif action == "remove":
                    try: os.unlink(name, dir_fd=parent)
                    except FileNotFoundError: pass
                    _fsync_dir_fd(parent); _failpoint(_MUTATION_COUNTER, "rollback-unlink")
            finally: os.close(parent)
        _remove_empty_dirs_at(dest_fd, created_dirs)
    except Exception:
        # Roll-forward to the exact installed snapshot from memory.  If this itself
        # fails, caller deliberately leaves state and backup as recovery assets.
        try:
            for rel, (data, mode, kind) in snapshots.items():
                parent, name = _open_rel_parent(dest_fd, rel)
                try:
                    if kind == "file" and data is not None and mode is not None:
                        _atomic_write_at(parent, name, data, mode, _MUTATION_COUNTER, "rollback-recover")
                    elif kind == "missing":
                        try: os.unlink(name, dir_fd=parent)
                        except FileNotFoundError: pass
                finally: os.close(parent)
        except Exception:
            raise
        raise
    finally:
        os.close(dest_fd); os.close(backup_fd)


def _load_state_at(dest: pathlib.Path, state: pathlib.Path) -> dict:
    dest_fd = _open_dir_chain(dest, create=False)
    try:
        data, info = _read_bytes_at(dest_fd, state.name)
        if stat.S_IMODE(info.st_mode) != 0o600:
            raise InstallError("unsafe state mode")
        return json.loads(data.decode("utf-8"))
    finally:
        os.close(dest_fd)


def main() -> int:
    global _MUTATION_COUNTER
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default=".")
    ap.add_argument("--codex-home", required=True)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--rollback", action="store_true")
    args = ap.parse_args()
    source = _absolute(args.source); dest = _absolute(args.codex_home)
    # Open the destination parent first.  Existing ancestor symlinks are rejected;
    # a missing final home may be created later under this pinned parent.
    check_chain(dest.parent, allow_missing_final=False)
    dest_exists = lexists(dest)
    if dest_exists:
        info = lstat_or_none(dest)
        if info is None or stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise SystemExit("destination root must be a directory")
    state = dest / ".codex-governance-v15-state.json"; backup = dest.parent / (dest.name + ".v15-managed-backup")
    for special in (state, backup):
        if lexists(special) and stat.S_ISLNK(lstat_or_none(special).st_mode):
            raise SystemExit("broken or live state/backup symlink collision")

    if args.rollback:
        if not dest_exists or not lexists(state) or not lexists(backup):
            raise SystemExit("no managed transaction state")
        record = _load_state_at(dest, state)
        validate_transaction(record, dest, backup)
        _MUTATION_COUNTER = [0]
        try:
            _rollback_records(dest, backup, record["managed"], record["created_dirs"], require_current=True)
        except Exception:
            # Do not destroy recovery assets on any failure.  The state remains an
            # actionable, hash-validated restore plan for a later operator.
            raise
        # Only after exact restore succeeds remove state and backup via descriptors.
        dest_fd = _open_dir_chain(dest, create=False); parent_fd = _open_dir_chain(dest.parent, create=False)
        try:
            os.unlink(state.name, dir_fd=dest_fd); _fsync_dir_fd(dest_fd)
            if "" in record.get("created_dirs", []):
                # The destination root itself was transaction-owned; all nested
                # managed paths are gone, so remove it only after state publication
                # and backup deletion have succeeded.
                _remove_tree_fd(parent_fd, dest.name)
            _remove_tree_fd(parent_fd, backup.name); _fsync_dir_fd(parent_fd)
        finally:
            os.close(dest_fd); os.close(parent_fd)
        print(json.dumps({"status": "ROLLED_BACK", "files": len(record["managed"])}, sort_keys=True)); return 0

    entries = safe_entries(source)
    if lexists(state) or lexists(backup):
        raise SystemExit("unowned backup/state collision")
    records: list[dict] = []
    # Snapshot destination objects with no-follow descriptor reads.  A missing root
    # is represented by missing records and will be created atomically below.
    if dest_exists:
        dest_fd = _open_dir_chain(dest, create=False)
    else:
        dest_fd = None
    try:
        for rel, src in entries:
            if dest_fd is None:
                rec = {"path": rel, "exists": False, "sha256": None, "mode": None, "type": "missing"}
            else:
                try:
                    parent, name = _open_rel_parent(dest_fd, rel)
                    try:
                        try:
                            fd, info = _open_regular_at(parent, name)
                        except FileNotFoundError:
                            rec = {"path": rel, "exists": False, "sha256": None, "mode": None, "type": "missing"}
                        else:
                            try: rec = {"path": rel, "exists": True, "sha256": _digest_fd(fd), "mode": stat.S_IMODE(info.st_mode), "type": "file"}
                            finally: os.close(fd)
                    finally: os.close(parent)
                except FileNotFoundError:
                    rec = {"path": rel, "exists": False, "sha256": None, "mode": None, "type": "missing"}
            raw = _read_source_rel(source, rel)
            rec.update({"source_sha256": hashlib.sha256(raw).hexdigest(), "installed_sha256": None, "installed_mode": None})
            records.append(rec)
    finally:
        if dest_fd is not None: os.close(dest_fd)
    hashes = {rel: rec["source_sha256"] for (rel, _), rec in zip(entries, records)}
    print(json.dumps({"status": "DRY_RUN" if args.dry_run else "READY", "files": len(entries), "destination": "$CODEX_HOME" if args.dry_run else str(dest), "hashes": hashes}, sort_keys=True))
    if args.dry_run: return 0

    validate_transaction({"schema": SCHEMA, "destination": str(dest), "managed": records, "created_dirs": [], "backup": str(backup)}, dest, backup, allow_uninstalled=True)
    _MUTATION_COUNTER = [0]
    counter = _MUTATION_COUNTER; created_dirs: list[str] = []
    backup_parent_fd = _open_dir_chain(dest.parent, create=False)
    dest_created = not dest_exists
    dest_fd = None
    backup_fd = None
    try:
        if dest_created:
            os.mkdir(dest.name, mode=0o700, dir_fd=backup_parent_fd); _fsync_dir_fd(backup_parent_fd); created_dirs.append(""); _failpoint(counter, "directory-mkdir-root")
        dest_fd = os.open(dest.name, os.O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC, dir_fd=backup_parent_fd)
        try:
            os.mkdir(backup.name, mode=0o700, dir_fd=backup_parent_fd); _fsync_dir_fd(backup_parent_fd); _failpoint(counter, "backup-mkdir")
        except FileExistsError:
            raise InstallError("unowned backup/state collision")
        backup_fd = os.open(backup.name, os.O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC, dir_fd=backup_parent_fd)
        source_map = {rel: src for rel, src in entries}
        for rec in records:
            rel = rec["path"]; parent_rel = "/".join(rel.split("/")[:-1]); name = rel.split("/")[-1]
            parent_fd = _mkdirs_at(dest_fd, parent_rel, created_dirs)
            bparent_fd = _mkdirs_at(backup_fd, parent_rel, [])
            try:
                if rec["exists"]:
                    src_parent, src_name = _open_rel_parent(dest_fd, rel)
                    try: _atomic_copy_at(src_parent, src_name, bparent_fd, name, int(rec["mode"]), counter, "backup-copy")
                    finally: os.close(src_parent)
                raw = _read_source_rel(source, rel)
                data = transformed_bytes(source, rel, dest, raw=raw)
                mode = 0o600 if rel.endswith(".json") else 0o644
                _atomic_write_at(parent_fd, name, data, mode, counter, "managed-write")
                # Validate bytes and installed mode from the descriptor itself.
                fd, info = _open_regular_at(parent_fd, name)
                try:
                    rec["installed_sha256"] = _digest_fd(fd); rec["installed_mode"] = stat.S_IMODE(info.st_mode)
                finally: os.close(fd)
            finally:
                os.close(parent_fd); os.close(bparent_fd)
        state_record = {"schema": SCHEMA, "destination": str(dest), "managed": records, "created_dirs": created_dirs, "backup": str(backup)}
        validate_transaction(state_record, dest, backup)
        data = (json.dumps(state_record, sort_keys=True, indent=2) + "\n").encode()
        _atomic_write_at(dest_fd, state.name, data, 0o600, counter, "state-write")
    except Exception:
        try:
            if backup_fd is not None:
                _rollback_records(dest, backup, records, created_dirs)
            elif dest_created:
                # Failure before backup creation: the newly created empty root is
                # the only transaction mutation and can be removed safely.
                try: _remove_tree_fd(backup_parent_fd, dest.name)
                except OSError: pass
        except Exception:
            # Leave both assets and the in-flight destination for manual recovery;
            # deleting them would turn a transient failure into irreversible loss.
            raise
        else:
            # Exact rollback succeeded, so cleanup is safe and descriptor-relative.
            try:
                if lexists(backup):
                    _remove_tree_fd(backup_parent_fd, backup.name)
                if dest_fd is not None:
                    try: os.unlink(state.name, dir_fd=dest_fd)
                    except FileNotFoundError: pass
                if dest_created:
                    _remove_tree_fd(backup_parent_fd, dest.name)
            finally: _fsync_dir_fd(backup_parent_fd)
        raise
    finally:
        if backup_fd is not None: os.close(backup_fd)
        os.close(backup_parent_fd)
        if dest_fd is not None: os.close(dest_fd)
    print(json.dumps({"status": "INSTALLED", "files": len(records), "mutations": counter[0]}, sort_keys=True)); return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (InstallError, OSError, json.JSONDecodeError) as exc:
        raise SystemExit(str(exc))

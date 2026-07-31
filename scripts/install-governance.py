#!/usr/bin/env python3
"""Install the managed Codex package with a no-follow transaction ledger."""
from __future__ import annotations
import argparse, hashlib, json, os, pathlib, shutil, stat, tempfile, shlex, re
from typing import Iterable

FORBIDDEN = ("sessions", "hook-receipts", "plugins", "connections", "models_cache.json", ".env")
SCHEMA = "install-transaction.v2"


def digest(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
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
    """No-follow check every existing ancestor; return missing components."""
    path = _absolute(path)
    missing: list[pathlib.Path] = []
    cur = pathlib.Path(path.anchor)
    parts = path.parts[1:]
    for part in parts:
        cur = cur / part
        info = lstat_or_none(cur)
        if info is None:
            missing.append(cur)
            continue
        if stat.S_ISLNK(info.st_mode):
            raise RuntimeError(f"symlink path component refused: {cur}")
        if not stat.S_ISDIR(info.st_mode) and cur != path:
            raise RuntimeError(f"non-directory ancestor refused: {cur}")
    if not allow_missing_final and lstat_or_none(path) is None:
        raise RuntimeError(f"missing required path: {path}")
    return missing


def safe_entries(source: pathlib.Path) -> list[tuple[str, pathlib.Path]]:
    source = _absolute(source)
    codex = source / "codex"
    check_chain(codex, allow_missing_final=False)
    out: list[tuple[str, pathlib.Path]] = []
    for p in sorted(codex.rglob("*")):
        info = lstat_or_none(p)
        if info is None:
            continue
        if "__pycache__" in p.parts or p.suffix == ".pyc":
            continue
        if stat.S_ISLNK(info.st_mode):
            raise RuntimeError(f"non-regular artifact: {p}")
        if stat.S_ISDIR(info.st_mode):
            continue
        if not stat.S_ISREG(info.st_mode):
            raise RuntimeError(f"non-regular artifact: {p}")
        rel = p.relative_to(codex).as_posix()
        if not (rel.startswith("hooks/") or rel.startswith("contracts/") or rel in {
            "AGENTS.md", "BRIEF-TEMPLATES.md", "hooks.json", "V15_RELEASE.json"
        }):
            raise RuntimeError(f"outside install allowlist: {rel}")
        if any(part in rel.lower() for part in FORBIDDEN):
            raise RuntimeError(f"forbidden managed path: {rel}")
        # Verify source ancestors as well; rglob normally follows no links, but this is explicit.
        check_chain(p, allow_missing_final=False)
        out.append((rel, p))
    if not out:
        raise RuntimeError("source codex package is empty")
    return out


def state_path(dest: pathlib.Path) -> pathlib.Path:
    return dest / ".codex-governance-v15-state.json"


def backup_path(dest: pathlib.Path) -> pathlib.Path:
    return dest.parent / (dest.name + ".v15-managed-backup")


def _failpoint(counter: list[int], label: str) -> None:
    counter[0] += 1
    raw = os.environ.get("CODEX_INSTALL_FAIL_AFTER", "")
    if raw:
        try:
            if int(raw) == counter[0]:
                raise RuntimeError(f"injected failure at mutation {counter[0]} ({label})")
        except ValueError:
            raise RuntimeError("CODEX_INSTALL_FAIL_AFTER must be an integer")


def _fsync_dir(path: pathlib.Path) -> None:
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    except OSError:
        return
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _atomic_copy(src: pathlib.Path, dst: pathlib.Path, mode: int, counter: list[int], label: str) -> None:
    check_chain(dst.parent)
    fd, name = tempfile.mkstemp(prefix=f".{dst.name}.", suffix=".tmp", dir=os.fspath(dst.parent))
    tmp = pathlib.Path(name)
    try:
        with os.fdopen(fd, "wb") as out, src.open("rb") as inp:
            shutil.copyfileobj(inp, out)
            out.flush(); os.fsync(out.fileno())
        os.chmod(tmp, mode)
        _failpoint(counter, label + ":temp")
        os.replace(tmp, dst)
        _fsync_dir(dst.parent)
        _failpoint(counter, label + ":replace")
    finally:
        try: tmp.unlink()
        except FileNotFoundError: pass


def _write_bytes(dst: pathlib.Path, data: bytes, mode: int, counter: list[int], label: str) -> None:
    check_chain(dst.parent)
    fd, name = tempfile.mkstemp(prefix=f".{dst.name}.", suffix=".tmp", dir=os.fspath(dst.parent))
    tmp = pathlib.Path(name)
    try:
        with os.fdopen(fd, "wb") as out:
            out.write(data); out.flush(); os.fsync(out.fileno())
        os.chmod(tmp, mode)
        _failpoint(counter, label + ":temp")
        os.replace(tmp, dst); _fsync_dir(dst.parent)
        _failpoint(counter, label + ":replace")
    finally:
        try: tmp.unlink()
        except FileNotFoundError: pass


def transformed_bytes(source: pathlib.Path, target: str, dest: pathlib.Path) -> bytes:
    data = source.read_bytes()
    if target != "hooks.json":
        return data
    obj = json.loads(data.decode("utf-8"))
    quoted = shlex.quote(os.fspath(dest))
    for groups in obj.get("hooks", {}).values():
        for matcher in groups:
            for hook in matcher.get("hooks", []):
                command = hook.get("command")
                if isinstance(command, str):
                    # Replace the complete quoted path token so no original quote remains.
                    command = re.sub(r'"\$CODEX_HOME/([^"\s]+)"', lambda m: shlex.quote(os.fspath(dest / m.group(1))), command)
                    command = re.sub(r"\$CODEX_HOME/([^\s'\"]+)", lambda m: shlex.quote(os.fspath(dest / m.group(1))), command)
                    command = command.replace("$CODEX_HOME", quoted)
                    hook["command"] = command
    return (json.dumps(obj, sort_keys=True, indent=2) + "\n").encode("utf-8")


def _object_record(path: pathlib.Path, root: pathlib.Path) -> dict:
    info = lstat_or_none(path)
    if info is None:
        return {"path": path.relative_to(root).as_posix(), "exists": False}
    if stat.S_ISLNK(info.st_mode):
        raise RuntimeError(f"managed symlink refused: {path}")
    if not stat.S_ISREG(info.st_mode):
        raise RuntimeError(f"managed object must be regular file: {path}")
    return {"path": path.relative_to(root).as_posix(), "exists": True,
            "sha256": digest(path), "mode": stat.S_IMODE(info.st_mode), "type": "file"}


def _remove_empty_dirs(paths: Iterable[pathlib.Path]) -> None:
    for p in sorted(set(paths), key=lambda q: len(q.parts), reverse=True):
        try:
            p.rmdir()
        except (FileNotFoundError, OSError):
            pass


def _rollback_records(dest: pathlib.Path, backup: pathlib.Path, records: list[dict], created_dirs: list[str], *, require_current: bool = False) -> None:
    """Validate the complete restore plan before mutation, then recover on restore failure."""
    plan=[]
    check_chain(dest)
    for rel in created_dirs:
        if rel:
            check_chain(dest / rel)
    for rec in records:
        target=dest / rec["path"]; check_chain(target.parent); info=lstat_or_none(target)
        if require_current:
            if info is None or stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode) or digest(target) != rec.get("installed_sha256"):
                raise RuntimeError("rollback refused: managed target changed:" + rec["path"])
        if rec.get("exists"):
            source=backup / rec["path"]; check_chain(source.parent)
            binfo=lstat_or_none(source)
            if binfo is None or stat.S_ISLNK(binfo.st_mode) or not stat.S_ISREG(binfo.st_mode):
                if not require_current and info is not None and stat.S_ISREG(info.st_mode) and digest(target)==rec.get("sha256"):
                    plan.append((rec,target,None,"noop")); continue
                raise RuntimeError("missing transaction backup:" + rec["path"])
            if digest(source) != rec.get("sha256") or not isinstance(rec.get("mode"),int) or not 0 <= rec["mode"] <= 0o7777:
                raise RuntimeError("corrupt transaction backup:" + rec["path"])
            plan.append((rec,target,source,"restore"))
        else:
            if info is not None and (stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode)):
                raise RuntimeError("rollback encountered unexpected managed object:" + rec["path"])
            plan.append((rec,target,None,"remove" if info is not None else "noop"))
    # Snapshot every installed object before any mutation, allowing roll-forward if restore itself fails.
    snapshot=pathlib.Path(tempfile.mkdtemp(prefix=".v15-rollback-",dir=backup.parent)); snapmap=[]
    try:
        for rec,target,source,action in plan:
            info=lstat_or_none(target)
            if info is not None and stat.S_ISREG(info.st_mode):
                snap=snapshot / rec["path"]; snap.parent.mkdir(parents=True,exist_ok=True); shutil.copy2(target,snap); snapmap.append((target,snap,stat.S_IMODE(info.st_mode)))
        old_fail=os.environ.pop("CODEX_INSTALL_FAIL_AFTER",None)
        try:
            for rec,target,source,action in reversed(plan):
                if action=="restore":
                    target.parent.mkdir(parents=True,exist_ok=True); _atomic_copy(source,target,int(rec["mode"]),[0],"rollback")
                elif action=="remove":
                    target.unlink(); _fsync_dir(target.parent)
            _remove_empty_dirs([dest / p for p in created_dirs])
        except Exception:
            # Roll forward to the exact installed snapshot; failure is surfaced to caller.
            for target,snap,mode in snapmap:
                target.parent.mkdir(parents=True,exist_ok=True); _atomic_copy(snap,target,mode,[0],"rollback-recover")
            raise
        finally:
            if old_fail is not None: os.environ["CODEX_INSTALL_FAIL_AFTER"] = old_fail
    finally:
        shutil.rmtree(snapshot,ignore_errors=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default=".")
    ap.add_argument("--codex-home", required=True)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--rollback", action="store_true")
    args = ap.parse_args()
    source = _absolute(args.source)
    dest = _absolute(args.codex_home)
    # Do not resolve symlinks: all checks are no-follow and destination stays literal.
    check_chain(dest.parent)
    if lexists(dest) and not stat.S_ISDIR(os.lstat(dest).st_mode):
        raise SystemExit("destination root must be a directory")
    state = state_path(dest); backup = backup_path(dest)
    for special in (state, backup):
        if lexists(special) and stat.S_ISLNK(os.lstat(special).st_mode):
            raise SystemExit("broken or live state/backup symlink collision")
    if args.rollback:
        if not lexists(state) or not lexists(backup):
            raise SystemExit("no managed transaction state")
        if stat.S_ISLNK(os.lstat(state).st_mode) or stat.S_ISLNK(os.lstat(backup).st_mode):
            raise SystemExit("unsafe state/backup")
        record = json.loads(state.read_text())
        if record.get("schema") != SCHEMA:
            raise SystemExit("invalid transaction state")
        _rollback_records(dest, backup, record.get("managed", []), record.get("created_dirs", []), require_current=True)
        shutil.rmtree(backup); state.unlink(); _fsync_dir(dest)
        print(json.dumps({"status": "ROLLED_BACK", "files": len(record.get("managed", []))}, sort_keys=True)); return 0

    entries = safe_entries(source)
    if lexists(state) or lexists(backup):
        raise SystemExit("unowned backup/state collision")
    records: list[dict] = []
    for target, src in entries:
        q = dest / target
        check_chain(q.parent)
        info = lstat_or_none(q)
        if info is not None and (stat.S_ISLNK(info.st_mode) or stat.S_ISDIR(info.st_mode) or not stat.S_ISREG(info.st_mode)):
            raise SystemExit("unsafe pre-existing managed object:" + target)
        rec = _object_record(q, dest)
        rec.update({"source_sha256": digest(src), "installed_sha256": None, "installed_mode": None})
        records.append(rec)
    hashes = {target: digest(src) for target, src in entries}
    print(json.dumps({"status": "DRY_RUN" if args.dry_run else "READY", "files": len(entries),
                      "destination": "$CODEX_HOME" if args.dry_run else str(dest), "hashes": hashes}, sort_keys=True))
    if args.dry_run: return 0

    counter = [0]; created_dirs: list[str] = []
    try:
        if not lexists(dest):
            dest.mkdir(mode=0o700)
            created_dirs.append(""); _fsync_dir(dest.parent); _failpoint(counter, "destination-mkdir")
        backup.mkdir(mode=0o700); _fsync_dir(backup.parent); _failpoint(counter, "backup-mkdir")
        mapping = dict(entries)
        for rec in records:
            target = dest / rec["path"]
            # Make only missing ancestors, recording every created directory.
            missing = check_chain(target.parent)
            for d in missing:
                if not lexists(d):
                    d.mkdir(mode=0o700); created_dirs.append(d.relative_to(dest).as_posix()); _fsync_dir(d.parent); _failpoint(counter, "directory-mkdir")
            if rec.get("exists"):
                b = backup / rec["path"]
                b.parent.mkdir(parents=True, exist_ok=True)
                _atomic_copy(target, b, int(rec.get("mode", 0o644)), counter, "backup-copy")
            data = transformed_bytes(mapping[rec["path"]], rec["path"], dest)
            mode = 0o600 if rec["path"].endswith(".json") else 0o644
            _write_bytes(target, data, mode, counter, "managed-write")
            rec["installed_sha256"] = digest(target); rec["installed_mode"] = stat.S_IMODE(os.lstat(target).st_mode)
        state_record = {"schema": SCHEMA, "destination": str(dest), "managed": records,
                        "created_dirs": created_dirs, "backup": str(backup)}
        _write_bytes(state, json.dumps(state_record, sort_keys=True, indent=2).encode() + b"\n", 0o600, counter, "state-write")
    except Exception:
        try:
            _rollback_records(dest, backup, records, created_dirs)
        finally:
            if lexists(backup) and not stat.S_ISLNK(os.lstat(backup).st_mode): shutil.rmtree(backup, ignore_errors=True)
            if lexists(state) and not stat.S_ISLNK(os.lstat(state).st_mode): state.unlink(missing_ok=True)
        raise
    print(json.dumps({"status": "INSTALLED", "files": len(records), "mutations": counter[0]}, sort_keys=True)); return 0


if __name__ == "__main__":
    try: raise SystemExit(main())
    except (RuntimeError, OSError, json.JSONDecodeError) as exc:
        raise SystemExit(str(exc))

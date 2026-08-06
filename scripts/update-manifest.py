#!/usr/bin/env python3
"""Atomically refresh manifest hashes from the Git index."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import stat
import subprocess
import tempfile


def _tracked(root: pathlib.Path) -> list[str]:
    output = subprocess.check_output(["git", "ls-files", "-z"], cwd=root)
    paths: list[str] = []
    for item in output.split(b"\0"):
        if not item or item == b"manifest.json":
            continue
        value = item.decode("utf-8")
        relative = pathlib.PurePosixPath(value)
        if relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
            raise ValueError(f"unsafe tracked path: {value!r}")
        paths.append(value)
    return sorted(paths)


def _sha256(path: pathlib.Path) -> str:
    metadata = path.lstat()
    if path.is_symlink() or not stat.S_ISREG(metadata.st_mode):
        raise ValueError(f"manifest source must be a regular file: {path.name}")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def refreshed(root: pathlib.Path) -> dict[str, object]:
    manifest_path = root / "manifest.json"
    value = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or not isinstance(value.get("files"), dict):
        raise ValueError("manifest files map is unavailable")
    tracked = _tracked(root)
    value["files"] = {path: _sha256(root / path) for path in tracked}
    value["allowlist"] = tracked
    return value


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", default=".")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    root = pathlib.Path(args.repo).resolve(strict=True)
    manifest_path = root / "manifest.json"
    manifest_mode = stat.S_IMODE(manifest_path.stat().st_mode)
    value = refreshed(root)
    rendered = json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n"
    if args.check:
        return 0 if manifest_path.read_text(encoding="utf-8") == rendered else 1
    descriptor, temporary = tempfile.mkstemp(prefix=".manifest-", dir=root)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(rendered)
            handle.flush()
            os.fsync(handle.fileno())
            os.fchmod(handle.fileno(), manifest_mode)
        os.replace(temporary, manifest_path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

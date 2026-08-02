#!/usr/bin/env python3
"""Manifest-bound, non-destructive Codex governance overlay installer."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import shutil
import tempfile
from typing import Any


FORBIDDEN = ("sessions", "hook-receipts", "plugins", "connections", "models_cache.json", ".env")
BACKUP_NAME = ".governance-v16-backup"


def sha(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _canonical_relative(value: str) -> pathlib.PurePosixPath:
    path = pathlib.PurePosixPath(value)
    if (
        "\\" in value
        or path.is_absolute()
        or not path.parts
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise SystemExit("noncanonical manifest path:" + value)
    return path


def collect(src: pathlib.Path) -> list[tuple[str, pathlib.Path]]:
    package = src / "codex"
    if not package.is_dir():
        raise SystemExit("missing codex package")
    package_root = package.resolve(strict=True)
    try:
        declared = json.loads((src / "manifest.json").read_text(encoding="utf-8"))["files"]
    except (OSError, KeyError, TypeError, json.JSONDecodeError):
        raise SystemExit("invalid manifest")
    if not isinstance(declared, dict):
        raise SystemExit("invalid manifest files")
    output: list[tuple[str, pathlib.Path]] = []
    for source_rel, expected_hash in sorted(declared.items()):
        if not isinstance(source_rel, str) or not source_rel.startswith("codex/"):
            continue
        source_path = _canonical_relative(source_rel)
        if len(source_path.parts) < 2 or source_path.parts[0] != "codex":
            raise SystemExit("noncanonical manifest path:" + source_rel)
        relative = pathlib.PurePosixPath(*source_path.parts[1:])
        path = package.joinpath(*relative.parts)
        try:
            path.resolve(strict=True).relative_to(package_root)
        except (FileNotFoundError, RuntimeError, ValueError):
            raise SystemExit("source escape:" + source_rel)
        if not path.is_file() or path.is_symlink() or sha(path) != expected_hash:
            raise SystemExit("manifest mismatch:" + source_rel)
        output.append((relative.as_posix(), path))
    if not output:
        raise SystemExit("empty codex package")
    return output


def _target(destination: pathlib.Path, relative: str) -> pathlib.Path:
    target = destination.joinpath(*_canonical_relative(relative).parts)
    try:
        target.resolve(strict=False).relative_to(destination.resolve(strict=True))
    except (RuntimeError, ValueError):
        raise SystemExit("destination escape:" + relative)
    return target


def _write_json(path: pathlib.Path, value: Any) -> None:
    path.write_text(json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    os.chmod(path, 0o600)


def install(entries: list[tuple[str, pathlib.Path]], destination: pathlib.Path) -> None:
    destination.mkdir(mode=0o700, parents=True, exist_ok=True)
    backup = destination / BACKUP_NAME
    targets = [(relative, source, _target(destination, relative)) for relative, source in entries]
    for relative, _source, target in targets:
        if target.exists() and (target.is_symlink() or not target.is_file()):
            raise SystemExit("unsafe existing target:" + relative)
    if backup.exists() and (backup.is_symlink() or not backup.is_dir()):
        raise SystemExit("unsafe backup target")
    temporary = pathlib.Path(tempfile.mkdtemp(prefix="governance-v16-", dir=destination))
    previous: list[str] = []
    try:
        files_backup = temporary / "files"
        for relative, _source, target in targets:
            if target.is_file():
                previous.append(relative)
                saved = files_backup.joinpath(*pathlib.PurePosixPath(relative).parts)
                saved.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(target, saved)
        _write_json(temporary / "metadata.json", {
            "schema": "governance-overlay-backup.v16",
            "managed": [relative for relative, _source, _target_path in targets],
            "previous": previous,
        })
        if backup.exists():
            shutil.rmtree(backup)
        temporary.rename(backup)
        for relative, source, target in targets:
            target.parent.mkdir(parents=True, exist_ok=True)
            staged = target.with_name(target.name + ".governance-v16.tmp")
            if staged.exists():
                if staged.is_symlink() or not staged.is_file():
                    raise SystemExit("unsafe staged target:" + relative)
                staged.unlink()
            shutil.copy2(source, staged)
            os.chmod(staged, 0o600 if staged.suffix == ".json" else 0o644)
            os.replace(staged, target)
    except BaseException:
        if temporary.exists():
            shutil.rmtree(temporary, ignore_errors=True)
        if backup.exists():
            try:
                rollback(destination)
            except BaseException as rollback_error:
                raise SystemExit("install failed and rollback failed") from rollback_error
        raise


def rollback(destination: pathlib.Path) -> None:
    backup = destination / BACKUP_NAME
    metadata_path = backup / "metadata.json"
    if backup.is_symlink() or not metadata_path.is_file() or metadata_path.is_symlink():
        raise SystemExit("no valid backup")
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        managed = metadata["managed"]
        previous = set(metadata["previous"])
    except (OSError, KeyError, TypeError, json.JSONDecodeError):
        raise SystemExit("invalid backup metadata")
    if (
        metadata.get("schema") != "governance-overlay-backup.v16"
        or not isinstance(managed, list)
        or not all(isinstance(item, str) for item in managed)
        or not all(isinstance(item, str) for item in previous)
        or not previous.issubset(set(managed))
    ):
        raise SystemExit("invalid backup metadata")
    for relative in managed:
        target = _target(destination, relative)
        if target.exists() and (target.is_symlink() or not target.is_file()):
            raise SystemExit("unsafe rollback target:" + relative)
    for relative in managed:
        target = _target(destination, relative)
        if relative in previous:
            source = backup / "files" / pathlib.PurePosixPath(relative)
            if source.is_symlink() or not source.is_file():
                raise SystemExit("missing backup file:" + relative)
            target.parent.mkdir(parents=True, exist_ok=True)
            os.replace(source, target)
        elif target.exists():
            target.unlink()
    shutil.rmtree(backup)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", default=".")
    parser.add_argument("--codex-home", required=True)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--rollback", action="store_true")
    args = parser.parse_args()
    source = pathlib.Path(args.source).resolve()
    destination = pathlib.Path(args.codex_home).expanduser().resolve()
    if args.rollback:
        rollback(destination)
        print(json.dumps({"status": "ROLLED_BACK", "destination": str(destination)}))
        return 0
    entries = collect(source)
    bad = [relative for relative, _source in entries if any(item in relative for item in FORBIDDEN)]
    if bad:
        raise SystemExit("forbidden:" + ",".join(bad))
    result = {
        "status": "DRY_RUN" if args.dry_run else "READY",
        "mode": "managed-overlay",
        "files": len(entries),
        "destination": "$CODEX_HOME" if args.dry_run else str(destination),
        "hashes": {relative: sha(path) for relative, path in entries},
    }
    print(json.dumps(result, sort_keys=True))
    if not args.dry_run:
        install(entries, destination)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

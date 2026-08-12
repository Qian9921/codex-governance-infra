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

from public_content import scan_path
from public_manifest import validate_manifest_metadata


FORBIDDEN = ("sessions", "hook-receipts", "plugins", "connections", "models_cache.json", ".env")
BACKUP_NAME = ".governance-v16-backup"
PREVIOUS_BACKUP_NAME = BACKUP_NAME + ".previous"
AGENTS_ROOT_PREFIX = "@agents"


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
        manifest = json.loads((src / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, KeyError, TypeError, json.JSONDecodeError):
        raise SystemExit("invalid manifest")
    metadata_errors = validate_manifest_metadata(manifest)
    if metadata_errors:
        raise SystemExit("invalid manifest metadata:" + ",".join(metadata_errors))
    declared = manifest["files"]
    if not isinstance(declared, dict):
        raise SystemExit("invalid manifest files")
    output: list[tuple[str, pathlib.Path]] = []
    for source_rel, expected_hash in sorted(declared.items()):
        if not isinstance(source_rel, str) or not source_rel.startswith("codex/"):
            continue
        source_path = _canonical_relative(source_rel)
        if len(source_path.parts) < 2 or source_path.parts[0] != "codex":
            raise SystemExit("noncanonical manifest path:" + source_rel)
        package_relative = pathlib.PurePosixPath(*source_path.parts[1:])
        path = package.joinpath(*package_relative.parts)
        try:
            path.resolve(strict=True).relative_to(package_root)
        except (FileNotFoundError, RuntimeError, ValueError):
            raise SystemExit("source escape:" + source_rel)
        if not path.is_file() or path.is_symlink() or sha(path) != expected_hash:
            raise SystemExit("manifest mismatch:" + source_rel)
        content_errors = scan_path(src, source_rel)
        if content_errors:
            raise SystemExit("forbidden content:" + ",".join(content_errors))
        if package_relative.parts[0] == "skills":
            if len(package_relative.parts) < 3:
                raise SystemExit("invalid personal skill path:" + source_rel)
            relative = pathlib.PurePosixPath(
                AGENTS_ROOT_PREFIX, "skills", *package_relative.parts[1:]
            )
        else:
            relative = package_relative
        output.append((relative.as_posix(), path))
    if not output:
        raise SystemExit("empty codex package")
    return output


def _target(
    destination: pathlib.Path, agents_home: pathlib.Path, relative: str
) -> pathlib.Path:
    canonical = _canonical_relative(relative)
    if canonical.parts[0] == AGENTS_ROOT_PREFIX:
        if len(canonical.parts) < 3 or canonical.parts[1] != "skills":
            raise SystemExit("invalid agents-home target:" + relative)
        root = agents_home / "skills"
        if root.is_symlink():
            raise SystemExit("unsafe agents skills root")
        target = root.joinpath(*canonical.parts[2:])
    else:
        root = destination
        target = root.joinpath(*canonical.parts)
    try:
        target.resolve(strict=False).relative_to(root.resolve(strict=False))
    except (RuntimeError, ValueError):
        raise SystemExit("destination escape:" + relative)
    return target


def _backup_source(backup: pathlib.Path, relative: str) -> pathlib.Path:
    """Resolve one backup file without trusting metadata or nested symlinks."""

    canonical = _canonical_relative(relative)
    root = backup / "files"
    source = root.joinpath(*canonical.parts)
    try:
        source.resolve(strict=True).relative_to(root.resolve(strict=True))
    except (FileNotFoundError, RuntimeError, ValueError):
        raise SystemExit("backup source escape:" + relative)
    if source.is_symlink() or not source.is_file():
        raise SystemExit("missing backup file:" + relative)
    return source


def _prune_empty_parents(path: pathlib.Path, root: pathlib.Path) -> None:
    """Remove empty installer-created directories without removing the root."""

    resolved_root = root.resolve(strict=False)
    current = path
    while current.resolve(strict=False) != resolved_root:
        try:
            current.rmdir()
        except (FileNotFoundError, OSError):
            return
        current = current.parent


def _write_json(path: pathlib.Path, value: Any) -> None:
    path.write_text(json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    os.chmod(path, 0o600)


def _replace_json(path: pathlib.Path, value: Any) -> None:
    staged = path.with_name(path.name + ".tmp")
    _write_json(staged, value)
    os.replace(staged, path)


def _backup_metadata(backup: pathlib.Path) -> dict[str, Any]:
    metadata_path = backup / "metadata.json"
    if backup.is_symlink() or not backup.is_dir() or metadata_path.is_symlink():
        raise SystemExit("no valid backup")
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        managed = metadata["managed"]
        previous = set(metadata["previous"])
    except (OSError, KeyError, TypeError, json.JSONDecodeError):
        raise SystemExit("invalid backup metadata")
    schema = metadata.get("schema")
    roots = metadata.get("roots")
    if (
        schema not in {"governance-overlay-backup.v16", "governance-overlay-backup.v19"}
        or not isinstance(managed, list)
        or not all(isinstance(item, str) for item in managed)
        or not all(isinstance(item, str) for item in previous)
        or not previous.issubset(set(managed))
        or (
            "installed" in metadata
            and (
                not isinstance(metadata["installed"], dict)
                or set(metadata["installed"]) != set(managed)
                or not all(isinstance(value, str) for value in metadata["installed"].values())
            )
        )
        or ("committed" in metadata and type(metadata["committed"]) is not bool)
        or (
            schema == "governance-overlay-backup.v19"
            and (
                not isinstance(roots, dict)
                or set(roots) != {"codex_home", "agents_home"}
                or not all(isinstance(value, str) for value in roots.values())
            )
        )
        or (
            schema == "governance-overlay-backup.v16"
            and any(item.startswith(AGENTS_ROOT_PREFIX + "/") for item in managed)
        )
    ):
        raise SystemExit("invalid backup metadata")
    return metadata


def _assert_backup_roots(
    metadata: dict[str, Any], destination: pathlib.Path, agents_home: pathlib.Path
) -> None:
    """Fail closed when a V19 transaction is opened with different roots."""

    if metadata["schema"] == "governance-overlay-backup.v16":
        return
    expected = {
        "codex_home": str(destination.resolve(strict=False)),
        "agents_home": str(agents_home.resolve(strict=False)),
    }
    if metadata["roots"] != expected:
        raise SystemExit("backup root mismatch")


def _backup_committed(backup: pathlib.Path) -> bool:
    metadata = _backup_metadata(backup)
    return metadata.get("committed", True) is True


def _apply_backup(
    destination: pathlib.Path, agents_home: pathlib.Path, backup: pathlib.Path
) -> None:
    metadata = _backup_metadata(backup)
    _assert_backup_roots(metadata, destination, agents_home)
    managed = metadata["managed"]
    previous = set(metadata["previous"])
    for relative in managed:
        target = _target(destination, agents_home, relative)
        if target.exists() and (target.is_symlink() or not target.is_file()):
            raise SystemExit("unsafe rollback target:" + relative)
        if relative in previous:
            _backup_source(backup, relative)
    for relative in managed:
        target = _target(destination, agents_home, relative)
        if relative in previous:
            source = _backup_source(backup, relative)
            target.parent.mkdir(parents=True, exist_ok=True)
            staged = target.with_name(target.name + ".governance-v16.rollback.tmp")
            shutil.copy2(source, staged)
            os.replace(staged, target)
        elif target.exists():
            target.unlink()
            root = agents_home if relative.startswith(AGENTS_ROOT_PREFIX + "/") else destination
            _prune_empty_parents(target.parent, root)
    shutil.rmtree(backup)


def _recover_interrupted_rotation(
    destination: pathlib.Path, agents_home: pathlib.Path
) -> None:
    backup = destination / BACKUP_NAME
    previous = destination / PREVIOUS_BACKUP_NAME
    for path in (backup, previous):
        if path.exists() and (path.is_symlink() or not path.is_dir()):
            raise SystemExit("unsafe backup target")
        if path.exists():
            _assert_backup_roots(_backup_metadata(path), destination, agents_home)
    if previous.exists() and not backup.exists():
        previous.rename(backup)
    elif previous.exists() and backup.exists():
        if _backup_committed(backup):
            shutil.rmtree(previous)
        else:
            _apply_backup(destination, agents_home, backup)
            previous.rename(backup)
    elif backup.exists() and not _backup_committed(backup):
        _apply_backup(destination, agents_home, backup)


def install(
    entries: list[tuple[str, pathlib.Path]],
    destination: pathlib.Path,
    agents_home: pathlib.Path,
) -> None:
    destination.mkdir(mode=0o700, parents=True, exist_ok=True)
    _recover_interrupted_rotation(destination, agents_home)
    backup = destination / BACKUP_NAME
    previous_backup = destination / PREVIOUS_BACKUP_NAME
    targets = [
        (relative, source, _target(destination, agents_home, relative))
        for relative, source in entries
    ]
    for relative, _source, target in targets:
        if target.exists() and (target.is_symlink() or not target.is_file()):
            raise SystemExit("unsafe existing target:" + relative)
    temporary = pathlib.Path(tempfile.mkdtemp(prefix="governance-v16-", dir=destination))
    previous: list[str] = []
    rotated = published = False
    try:
        files_backup = temporary / "files"
        for relative, _source, target in targets:
            if target.is_file():
                previous.append(relative)
                saved = files_backup.joinpath(*pathlib.PurePosixPath(relative).parts)
                saved.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(target, saved)
        _write_json(temporary / "metadata.json", {
            "schema": "governance-overlay-backup.v19",
            "roots": {
                "codex_home": str(destination.resolve(strict=False)),
                "agents_home": str(agents_home.resolve(strict=False)),
            },
            "managed": [relative for relative, _source, _target_path in targets],
            "previous": previous,
            "installed": {relative: sha(source) for relative, source, _target in targets},
            "committed": False,
        })
        if backup.exists():
            backup.rename(previous_backup)
            rotated = True
        temporary.rename(backup)
        published = True
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
        metadata = _backup_metadata(backup)
        metadata["committed"] = True
        _replace_json(backup / "metadata.json", metadata)
        if previous_backup.exists():
            shutil.rmtree(previous_backup)
    except BaseException:
        if temporary.exists():
            shutil.rmtree(temporary, ignore_errors=True)
        if published and backup.exists():
            try:
                _apply_backup(destination, agents_home, backup)
            except BaseException as rollback_error:
                raise SystemExit("install failed and rollback failed") from rollback_error
        if rotated and previous_backup.exists() and not backup.exists():
            previous_backup.rename(backup)
        raise


def rollback(destination: pathlib.Path, agents_home: pathlib.Path) -> None:
    backup = destination / BACKUP_NAME
    previous = destination / PREVIOUS_BACKUP_NAME
    if previous.exists() and not backup.exists():
        previous.rename(backup)
    if not backup.exists():
        raise SystemExit("no valid backup")
    _apply_backup(destination, agents_home, backup)
    if previous.exists():
        previous.rename(backup)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", default=".")
    parser.add_argument("--codex-home", required=True)
    parser.add_argument(
        "--agents-home",
        help="personal .agents root; defaults to a .agents sibling of CODEX_HOME",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--rollback", action="store_true")
    args = parser.parse_args()
    source = pathlib.Path(args.source).resolve()
    destination = pathlib.Path(args.codex_home).expanduser().resolve()
    agents_home = (
        pathlib.Path(args.agents_home).expanduser().resolve()
        if args.agents_home
        else (destination.parent / ".agents").resolve()
    )
    if agents_home == destination or agents_home in destination.parents or destination in agents_home.parents:
        raise SystemExit("CODEX_HOME and agents home must be disjoint")
    if args.rollback:
        rollback(destination, agents_home)
        print(json.dumps({"status": "ROLLED_BACK", "destination": str(destination)}))
        return 0
    entries = collect(source)
    bad = [relative for relative, _source in entries if any(item in relative for item in FORBIDDEN)]
    if bad:
        raise SystemExit("forbidden:" + ",".join(bad))
    result = {
        "status": "DRY_RUN" if args.dry_run else "READY",
        "mode": "managed-overlay",
        "package": "Codex Governance Infra",
        "version": "21.1.0",
        "files": len(entries),
        "destination": "$CODEX_HOME" if args.dry_run else str(destination),
        "agents_destination": "$HOME/.agents" if args.dry_run else str(agents_home),
        "hashes": {relative: sha(path) for relative, path in entries},
    }
    print(json.dumps(result, sort_keys=True))
    if not args.dry_run:
        install(entries, destination, agents_home)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Read-only exact package path/hash and privacy verifier."""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import re
import subprocess
import sys
from typing import Any, Mapping

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from public_content import FORBIDDEN_PARTS, FORBIDDEN_RE, scan_path
from public_manifest import validate_manifest_metadata
REQUIRED_PATHS = (
    "codex/AGENTS.md", "codex/BRIEF-TEMPLATES.md", "codex/hooks/hooks.json",
    "codex/bin/refresh-model-catalog.py", "scripts/configure-model-routing.py",
    "codex/v16/contracts/README.md", "codex/v16/contracts/schema_registry.v16.json",
    "codex/v16/contracts/v16_dispatch_transcript.json", "scripts/install-governance.py",
    "scripts/presubmit.py", "manifest.json",
)
def sha(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _tracked_paths(root: pathlib.Path) -> set[str]:
    try:
        output = subprocess.check_output(["git", "ls-files", "-z"], cwd=str(root))
    except (OSError, subprocess.CalledProcessError):
        return {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file() and ".git" not in p.parts and p.name != "manifest.json"}
    return {item.decode("utf-8") for item in output.split(b"\0") if item and item.decode("utf-8") != "manifest.json"}


def scan(root: pathlib.Path, *, expected_paths: set[str] | None = None) -> tuple[list[str], list[str]]:
    paths = sorted(expected_paths if expected_paths is not None else _tracked_paths(root))
    errors: list[str] = []
    for rel in paths:
        path = root / rel
        if not path.is_file() or path.is_symlink():
            errors.append(f"missing/nonregular:{rel}")
            continue
        errors.extend(scan_path(root, rel))
    return paths, errors


def verify_manifest_exact(root: pathlib.Path | str, manifest: Mapping[str, Any]) -> dict[str, Any]:
    root_path = pathlib.Path(root).resolve()
    errors: list[str] = []
    errors.extend(f"manifest metadata:{item}" for item in validate_manifest_metadata(manifest))
    files = manifest.get("files") if isinstance(manifest, Mapping) else None
    if not isinstance(files, dict):
        errors.append("manifest.files must be an object")
        files = {}
    tracked = _tracked_paths(root_path)
    allowlist = manifest.get("allowlist") if isinstance(manifest, Mapping) else None
    if not isinstance(allowlist, list) or set(allowlist) != tracked:
        errors.append("manifest allowlist must equal tracked public file set")
    declared = set(files)
    if declared != tracked:
        for rel in sorted(tracked - declared): errors.append(f"manifest missing:{rel}")
        for rel in sorted(declared - tracked): errors.append(f"manifest extra:{rel}")
    for rel in sorted(tracked):
        path = root_path / rel
        expected = files.get(rel)
        if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected):
            errors.append(f"manifest hash missing:{rel}")
        elif path.is_file() and sha(path) != expected:
            errors.append(f"hash mismatch:{rel}")
    for req in REQUIRED_PATHS:
        if req != "manifest.json" and not (root_path / req).is_file():
            errors.append(f"missing:{req}")
    _, scan_errors = scan(root_path, expected_paths=tracked)
    errors.extend(scan_errors)
    return {"repo": str(root_path), "files": len(tracked), "errors": sorted(set(errors)), "status": "GREEN" if not errors else "RED"}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(); ap.add_argument("--repo", default="."); args = ap.parse_args(argv)
    root = pathlib.Path(args.repo).resolve()
    manifest_path = root / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        result = verify_manifest_exact(root, manifest)
    except Exception as exc:
        result = {"repo": str(root), "files": 0, "errors": [f"verifier:{type(exc).__name__}:{exc}"], "status": "RED"}
    print(json.dumps(result, sort_keys=True))
    return 0 if result["status"] == "GREEN" else 1


if __name__ == "__main__":
    sys.exit(main())

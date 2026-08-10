#!/usr/bin/env python3
"""Build a stable Codex model catalog with native multi-agent V2 routing.

The upstream catalog remains the source of truth for every field except the
multi-agent backend selector of explicitly allowlisted worker models.  The
result is published atomically and a previously valid catalog remains usable
when a refresh is temporarily unavailable.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
from typing import Any


REQUIRED_MODELS = ("gpt-5.6-luna",)
OPTIONAL_MODELS = ("gpt-5.3-codex-spark",)
TARGET_VERSION = "v2"


class CatalogError(RuntimeError):
    """Raised when a catalog cannot safely drive the routing overlay."""


def _codex_argv(codex_bin: pathlib.Path) -> list[str]:
    """Build a shell-free discovery argv for native Windows command forms."""

    if not codex_bin.is_file():
        raise CatalogError("Codex binary is unavailable")
    command = [str(codex_bin)]
    if sys.platform == "win32":
        suffix = codex_bin.suffix.lower()
        if suffix in {".exe", ".cmd", ".bat"}:
            pass
        elif suffix == ".ps1":
            powershell = shutil.which("pwsh") or shutil.which("powershell")
            if powershell is None or pathlib.Path(powershell).suffix.lower() != ".exe":
                raise CatalogError(
                    "PowerShell interpreter is unavailable for Codex .ps1 command"
                )
            command = [powershell, "-NoProfile", "-NonInteractive", "-File", str(codex_bin)]
        else:
            raise CatalogError(
                "unsupported Windows Codex command; use .exe, .cmd, .bat, or .ps1"
            )
    return command + ["debug", "models"]


def _read_catalog(path: pathlib.Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CatalogError("catalog is unreadable") from exc
    if not isinstance(value, dict) or not isinstance(value.get("models"), list):
        raise CatalogError("catalog must contain a models array")
    return value


def _model_map(catalog: dict[str, Any]) -> dict[str, dict[str, Any]]:
    models: dict[str, dict[str, Any]] = {}
    for item in catalog["models"]:
        if not isinstance(item, dict) or not isinstance(item.get("slug"), str):
            raise CatalogError("catalog contains an invalid model entry")
        slug = item["slug"]
        if slug in models:
            raise CatalogError(f"catalog contains duplicate model: {slug}")
        models[slug] = item
    return models


def normalize(catalog: dict[str, Any]) -> tuple[dict[str, Any], list[str], list[str]]:
    models = _model_map(catalog)
    missing = [slug for slug in REQUIRED_MODELS if slug not in models]
    if missing:
        raise CatalogError("required model is unavailable: " + ",".join(missing))

    patched: list[str] = []
    absent_optional: list[str] = []
    for slug in (*REQUIRED_MODELS, *OPTIONAL_MODELS):
        model = models.get(slug)
        if model is None:
            absent_optional.append(slug)
            continue
        model["multi_agent_version"] = TARGET_VERSION
        patched.append(slug)
    return catalog, patched, absent_optional


def validate_overlay(path: pathlib.Path) -> dict[str, Any]:
    catalog = _read_catalog(path)
    models = _model_map(catalog)
    for slug in REQUIRED_MODELS:
        if models.get(slug, {}).get("multi_agent_version") != TARGET_VERSION:
            raise CatalogError(f"required model is not routed through {TARGET_VERSION}: {slug}")
    return catalog


def _catalog_from_codex(codex_bin: pathlib.Path, codex_home: pathlib.Path) -> dict[str, Any]:
    command = _codex_argv(codex_bin)
    auth = codex_home / "auth.json"
    with tempfile.TemporaryDirectory(prefix="codex-model-catalog-") as temporary:
        isolated_home = pathlib.Path(temporary)
        os.chmod(isolated_home, 0o700)
        if auth.is_file() and not auth.is_symlink():
            isolated_auth = isolated_home / "auth.json"
            if sys.platform == "win32":
                # Windows commonly denies unprivileged symlink creation.  A
                # private, short-lived copy keeps discovery isolated while
                # allowing the Codex binary to authenticate normally.
                shutil.copy2(auth, isolated_auth)
                try:
                    os.chmod(isolated_auth, 0o600)
                except OSError:
                    pass
            else:
                isolated_auth.symlink_to(auth)
        environment = os.environ.copy()
        environment["CODEX_HOME"] = str(isolated_home)
        try:
            result = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
                timeout=60,
                env=environment,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise CatalogError("Codex model discovery failed") from exc
    if result.returncode != 0:
        raise CatalogError("Codex model discovery failed")
    try:
        value = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise CatalogError("Codex returned an invalid model catalog") from exc
    if not isinstance(value, dict):
        raise CatalogError("Codex returned an invalid model catalog")
    return value


def _publish(path: pathlib.Path, catalog: dict[str, Any]) -> None:
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise CatalogError("catalog output target is unsafe")
    if path.parent.is_symlink() or (
        path.parent.exists() and not path.parent.is_dir()
    ):
        raise CatalogError("catalog output directory is unsafe")
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(catalog, handle, sort_keys=True, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
            try:
                os.fchmod(handle.fileno(), 0o600)
            except (AttributeError, NotImplementedError):
                # Atomic replacement is available on Windows even when the
                # POSIX-only descriptor mode operation is not.
                os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def refresh(codex_bin: pathlib.Path, codex_home: pathlib.Path, output: pathlib.Path) -> dict[str, Any]:
    try:
        catalog, patched, absent_optional = normalize(_catalog_from_codex(codex_bin, codex_home))
        _publish(output, catalog)
        validate_overlay(output)
        return {
            "status": "READY",
            "catalog": str(output),
            "patched": patched,
            "optional_unavailable": absent_optional,
            "model_count": len(catalog["models"]),
        }
    except CatalogError:
        try:
            catalog = validate_overlay(output)
        except CatalogError:
            raise
        return {
            "status": "READY_LAST_KNOWN_GOOD",
            "catalog": str(output),
            "patched": list(REQUIRED_MODELS),
            "optional_unavailable": [],
            "model_count": len(catalog["models"]),
        }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--codex-home", required=True)
    parser.add_argument("--codex-bin", required=True)
    parser.add_argument("--output")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    codex_home = pathlib.Path(args.codex_home).expanduser().resolve(strict=True)
    output = (
        pathlib.Path(args.output).expanduser().absolute()
        if args.output
        else codex_home / "model-catalogs" / "multi-agent-v2.json"
    )
    if args.check:
        catalog = validate_overlay(output)
        result = {"status": "READY", "catalog": str(output), "model_count": len(catalog["models"])}
    else:
        result = refresh(pathlib.Path(args.codex_bin).expanduser().resolve(), codex_home, output)
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except CatalogError as exc:
        print(json.dumps({"status": "BLOCKED", "reason": str(exc)}, sort_keys=True))
        raise SystemExit(1)

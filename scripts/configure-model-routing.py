#!/usr/bin/env python3
"""Configure the persistent Codex multi-agent model catalog overlay."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.9-3.10 compatibility.
    try:
        import tomli as tomllib
    except ModuleNotFoundError:  # Keep the stdlib-only presubmit usable offline.
        class _TomlCompat:
            class TOMLDecodeError(ValueError):
                pass

            @staticmethod
            def loads(document: str) -> dict[str, object]:
                result: dict[str, object] = {}
                section: dict[str, object] = result
                for raw_line in document.splitlines():
                    line = raw_line.split("#", 1)[0].strip()
                    if not line:
                        continue
                    if line.startswith("[") and line.endswith("]"):
                        name = line[1:-1].strip()
                        if not name or any(char in name for char in "[]"):
                            raise _TomlCompat.TOMLDecodeError("invalid table")
                        section = result.setdefault(name, {})
                        if not isinstance(section, dict):
                            raise _TomlCompat.TOMLDecodeError("table collision")
                        continue
                    if "=" not in line:
                        raise _TomlCompat.TOMLDecodeError("invalid assignment")
                    key, raw_value = (part.strip() for part in line.split("=", 1))
                    if not key or not raw_value:
                        raise _TomlCompat.TOMLDecodeError("invalid assignment")
                    if raw_value.startswith('"') and raw_value.endswith('"'):
                        value: object = json.loads(raw_value)
                    elif raw_value in {"true", "false"}:
                        value = raw_value == "true"
                    else:
                        try:
                            value = int(raw_value)
                        except ValueError as exc:
                            raise _TomlCompat.TOMLDecodeError("unsupported value") from exc
                    if key.startswith('"') and key.endswith('"'):
                        key = json.loads(key)
                    section[key] = value
                return result

        tomllib = _TomlCompat


STATE_DIR = "model-routing-state"
STATE_SCHEMA = "codex-model-routing-state.v19"
CATALOG_RELATIVE = pathlib.PurePosixPath("model-catalogs/multi-agent-v2.json")
REFRESHER_RELATIVE = pathlib.PurePosixPath("bin/refresh-model-catalog.py")
DROPIN_NAME = "20-model-catalog-overlay.conf"
CATALOG_KEY = "model_catalog_json"
CATALOG_ASSIGNMENT = re.compile(
    r'^\s*(?:model_catalog_json|"model_catalog_json"|\'model_catalog_json\')\s*='
)


class ConfigureError(RuntimeError):
    """Raised when configuration cannot be changed or rolled back safely."""


def _sha(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _atomic_write(path: pathlib.Path, content: str, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
            os.fchmod(handle.fileno(), mode)
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def _catalog_assignment(config: str) -> tuple[list[str], int | None, str | None]:
    try:
        parsed = tomllib.loads(config)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigureError("Codex config.toml is invalid") from exc
    lines = config.splitlines(keepends=True)
    table_start = next(
        (index for index, line in enumerate(lines) if line.lstrip().startswith("[")),
        len(lines),
    )
    matches = [index for index, line in enumerate(lines[:table_start]) if CATALOG_ASSIGNMENT.match(line)]
    value = parsed.get(CATALOG_KEY)
    if value is not None and not isinstance(value, str):
        raise ConfigureError("top-level model_catalog_json must be a string")
    if value is None and matches:
        raise ConfigureError("model_catalog_json assignment is not top-level")
    if value is not None and len(matches) != 1:
        raise ConfigureError("unsupported top-level model_catalog_json spelling")
    if len(matches) > 1:
        raise ConfigureError("multiple top-level model_catalog_json settings")
    if matches:
        try:
            single = tomllib.loads(lines[matches[0]])
        except tomllib.TOMLDecodeError as exc:
            raise ConfigureError("model_catalog_json must use a single-line assignment") from exc
        if single.get(CATALOG_KEY) != value or len(single) != 1:
            raise ConfigureError("model_catalog_json must use a single-line assignment")
    return lines, matches[0] if matches else None, value


def _set_top_level_catalog(config: str, catalog: pathlib.Path) -> str:
    lines, match, _value = _catalog_assignment(config)
    table_start = next(
        (index for index, line in enumerate(lines) if line.lstrip().startswith("[")),
        len(lines),
    )
    setting = f"model_catalog_json = {json.dumps(str(catalog))}\n"
    if match is not None:
        lines[match] = setting
    else:
        insert_at = next(
            (index + 1 for index, line in enumerate(lines[:table_start]) if line.startswith("model_reasoning_effort")),
            0,
        )
        lines.insert(insert_at, setting)
    updated = "".join(lines)
    _catalog_assignment(updated)
    return updated


def _restore_catalog_setting(current: str, original: str, expected: pathlib.Path) -> str:
    current_lines, current_match, current_value = _catalog_assignment(current)
    original_lines, original_match, original_value = _catalog_assignment(original)
    if current_match is None or current_value != str(expected):
        raise ConfigureError("owned model_catalog_json setting has drifted")
    if original_match is None:
        del current_lines[current_match]
    else:
        current_lines[current_match] = original_lines[original_match]
    restored = "".join(current_lines)
    _lines, _match, restored_value = _catalog_assignment(restored)
    if restored_value != original_value:
        raise ConfigureError("model_catalog_json rollback validation failed")
    return restored


def _systemd_quote(value: pathlib.Path | str) -> str:
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'


def _dropin(
    codex_home: pathlib.Path,
    codex_bin: pathlib.Path,
    exec_wrapper: pathlib.Path | None,
) -> str:
    command = []
    if exec_wrapper is not None:
        command.append(exec_wrapper)
    command.extend(
        [
            pathlib.Path(sys.executable),
            codex_home / REFRESHER_RELATIVE,
            pathlib.Path("--codex-home"),
            codex_home,
            pathlib.Path("--codex-bin"),
            codex_bin,
            pathlib.Path("--output"),
            codex_home / CATALOG_RELATIVE,
        ]
    )
    rendered = " ".join(_systemd_quote(item) for item in command)
    return "[Service]\nExecStartPre=" + rendered + "\n"


def _run_refresh(codex_home: pathlib.Path, codex_bin: pathlib.Path) -> dict[str, Any]:
    refresher = codex_home / REFRESHER_RELATIVE
    if not refresher.is_file() or refresher.is_symlink():
        raise ConfigureError("installed model catalog refresher is unavailable")
    result = subprocess.run(
        [
            sys.executable,
            str(refresher),
            "--codex-home",
            str(codex_home),
            "--codex-bin",
            str(codex_bin),
            "--output",
            str(codex_home / CATALOG_RELATIVE),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=90,
    )
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ConfigureError("model catalog refresher returned invalid output") from exc
    if result.returncode != 0 or payload.get("status") not in {"READY", "READY_LAST_KNOWN_GOOD"}:
        raise ConfigureError("model catalog refresher did not produce a usable catalog")
    return payload


def configure(
    codex_home: pathlib.Path,
    codex_bin: pathlib.Path,
    systemd_user_dir: pathlib.Path,
    service_name: str,
    exec_wrapper: pathlib.Path | None,
) -> dict[str, Any]:
    config = codex_home / "config.toml"
    if not config.is_file() or config.is_symlink():
        raise ConfigureError("Codex config.toml is unavailable or unsafe")
    if not codex_bin.is_file():
        raise ConfigureError("Codex binary is unavailable")
    if exec_wrapper is not None and not exec_wrapper.is_file():
        raise ConfigureError("execution wrapper is unavailable")

    state = codex_home / STATE_DIR
    metadata_path = state / "metadata.json"
    dropin = systemd_user_dir / f"{service_name}.d" / DROPIN_NAME
    catalog = codex_home / CATALOG_RELATIVE
    if state.exists() and not metadata_path.exists():
        if state.is_symlink() or not state.is_dir() or any(state.iterdir()):
            raise ConfigureError("model routing state is incomplete or unsafe")
        state.rmdir()
    if dropin.exists() and (dropin.is_symlink() or not dropin.is_file()):
        raise ConfigureError("existing systemd drop-in is unsafe")
    if catalog.exists() and (catalog.is_symlink() or not catalog.is_file()):
        raise ConfigureError("existing model catalog is unsafe")
    if catalog.parent.exists() and (catalog.parent.is_symlink() or not catalog.parent.is_dir()):
        raise ConfigureError("model catalog directory is unsafe")
    _catalog_assignment(config.read_text(encoding="utf-8"))

    first_install = not metadata_path.exists()
    if first_install:
        temporary_state = pathlib.Path(tempfile.mkdtemp(prefix=STATE_DIR + ".", dir=codex_home))
        os.chmod(temporary_state, 0o700)
        try:
            shutil.copy2(config, temporary_state / "config.toml.before")
            dropin_existed = dropin.is_file()
            if dropin_existed:
                shutil.copy2(dropin, temporary_state / "dropin.before")
            catalog_existed = catalog.is_file()
            if catalog_existed:
                shutil.copy2(catalog, temporary_state / "catalog.before")
            metadata = {
                "schema": STATE_SCHEMA,
                "config_sha256": _sha(temporary_state / "config.toml.before"),
                "dropin_existed": dropin_existed,
                "dropin_sha256": (
                    _sha(temporary_state / "dropin.before") if dropin_existed else None
                ),
                "catalog_existed": catalog_existed,
                "catalog_sha256": (
                    _sha(temporary_state / "catalog.before") if catalog_existed else None
                ),
                "committed": False,
            }
            _atomic_write(
                temporary_state / "metadata.json",
                json.dumps(metadata, sort_keys=True) + "\n",
                0o600,
            )
            os.replace(temporary_state, state)
        finally:
            if temporary_state.exists():
                shutil.rmtree(temporary_state)
    else:
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ConfigureError("model routing state is invalid") from exc
        if metadata.get("schema") != STATE_SCHEMA:
            raise ConfigureError("model routing state has an unsupported schema")
        if "catalog_sha256" not in metadata:
            metadata["catalog_existed"] = False
            metadata["catalog_sha256"] = None
            metadata["committed"] = True
            _atomic_write(metadata_path, json.dumps(metadata, sort_keys=True) + "\n", 0o600)

    try:
        refresh = _run_refresh(codex_home, codex_bin)
        original = config.read_text(encoding="utf-8")
        updated = _set_top_level_catalog(original, catalog)
        _atomic_write(config, updated, 0o600)
        _atomic_write(dropin, _dropin(codex_home, codex_bin, exec_wrapper), 0o644)
        metadata["committed"] = True
        _atomic_write(metadata_path, json.dumps(metadata, sort_keys=True) + "\n", 0o600)
    except BaseException:
        if first_install:
            shutil.copy2(state / "config.toml.before", config)
            if metadata["dropin_existed"]:
                dropin.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(state / "dropin.before", dropin)
            elif dropin.is_file() and not dropin.is_symlink():
                dropin.unlink()
            if metadata["catalog_existed"]:
                catalog.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(state / "catalog.before", catalog)
            elif catalog.is_file() and not catalog.is_symlink():
                catalog.unlink()
            shutil.rmtree(state)
        raise
    return {
        "status": "READY",
        "catalog": str(codex_home / CATALOG_RELATIVE),
        "dropin": str(dropin),
        "refresh_status": refresh["status"],
    }


def rollback(codex_home: pathlib.Path, systemd_user_dir: pathlib.Path, service_name: str) -> dict[str, Any]:
    state = codex_home / STATE_DIR
    metadata_path = state / "metadata.json"
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigureError("no valid model routing state") from exc
    if metadata.get("schema") != STATE_SCHEMA:
        raise ConfigureError("model routing state has an unsupported schema")

    config_backup = state / "config.toml.before"
    if _sha(config_backup) != metadata.get("config_sha256"):
        raise ConfigureError("config backup integrity check failed")
    config = codex_home / "config.toml"
    current_config = config.read_text(encoding="utf-8")
    original_config = config_backup.read_text(encoding="utf-8")
    restored_config = _restore_catalog_setting(
        current_config,
        original_config,
        codex_home / CATALOG_RELATIVE,
    )

    dropin = systemd_user_dir / f"{service_name}.d" / DROPIN_NAME
    if metadata.get("dropin_existed"):
        dropin_backup = state / "dropin.before"
        if _sha(dropin_backup) != metadata.get("dropin_sha256"):
            raise ConfigureError("drop-in backup integrity check failed")
    elif dropin.exists() and (dropin.is_symlink() or not dropin.is_file()):
        raise ConfigureError("systemd drop-in rollback target is unsafe")

    catalog = codex_home / CATALOG_RELATIVE
    if metadata.get("catalog_existed"):
        catalog_backup = state / "catalog.before"
        if _sha(catalog_backup) != metadata.get("catalog_sha256"):
            raise ConfigureError("catalog backup integrity check failed")
    elif catalog.exists() and (catalog.is_symlink() or not catalog.is_file()):
        raise ConfigureError("catalog rollback target is unsafe")

    _atomic_write(config, restored_config, 0o600)
    if metadata.get("dropin_existed"):
        dropin.parent.mkdir(parents=True, exist_ok=True)
        staged = dropin.with_name(dropin.name + ".model-routing-rollback")
        shutil.copy2(dropin_backup, staged)
        os.replace(staged, dropin)
    elif dropin.exists():
        dropin.unlink()
    if metadata.get("catalog_existed"):
        catalog.parent.mkdir(parents=True, exist_ok=True)
        staged = catalog.with_name(catalog.name + ".model-routing-rollback")
        shutil.copy2(catalog_backup, staged)
        os.replace(staged, catalog)
    elif catalog.exists():
        catalog.unlink()
    shutil.rmtree(state)
    return {"status": "ROLLED_BACK", "dropin": str(dropin)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--codex-home", required=True)
    parser.add_argument("--codex-bin", required=True)
    parser.add_argument("--systemd-user-dir", required=True)
    parser.add_argument("--service-name", default="codex-app-server.service")
    parser.add_argument("--exec-wrapper")
    parser.add_argument("--rollback", action="store_true")
    args = parser.parse_args()

    codex_home = pathlib.Path(args.codex_home).expanduser().resolve(strict=True)
    systemd_user_dir = pathlib.Path(args.systemd_user_dir).expanduser().resolve()
    if args.rollback:
        result = rollback(codex_home, systemd_user_dir, args.service_name)
    else:
        result = configure(
            codex_home,
            pathlib.Path(args.codex_bin).expanduser().absolute(),
            systemd_user_dir,
            args.service_name,
            pathlib.Path(args.exec_wrapper).expanduser().absolute() if args.exec_wrapper else None,
        )
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ConfigureError as exc:
        print(json.dumps({"status": "BLOCKED", "reason": str(exc)}, sort_keys=True))
        raise SystemExit(1)

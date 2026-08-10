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
CLEANUP_DIR = "model-routing-state.cleanup"
COMPLETION_MARKER = "model-routing-state.complete"
STATE_SCHEMA = "codex-model-routing-state.v19"
COMPLETION_SCHEMA = "codex-model-routing-completion.v19"
ROLLBACK_TARGETS = ("config", "dropin", "catalog", "complete")
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


def _sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _atomic_write(path: pathlib.Path, content: str, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        # Open the descriptor in binary mode so Windows cannot translate LF
        # into CRLF after hashes have been computed over the UTF-8 payload.
        payload = content.encode("utf-8")
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
            # ``fchmod`` is not exposed by every supported Windows Python
            # build.  Atomic replacement is the portability invariant; file
            # mode preservation is best effort on platforms without it.
            try:
                os.fchmod(handle.fileno(), mode)
            except (AttributeError, NotImplementedError):
                os.chmod(temporary, mode)
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


def _dropin_path(
    systemd_user_dir: pathlib.Path | None, service_name: str
) -> pathlib.Path | None:
    """Return the optional systemd target; ``None`` means on-demand mode."""

    if systemd_user_dir is None:
        return None
    return systemd_user_dir / f"{service_name}.d" / DROPIN_NAME


def _normalize_systemd_user_dir(
    systemd_user_dir: pathlib.Path | None,
) -> pathlib.Path | None:
    if systemd_user_dir is None:
        return None
    return systemd_user_dir.expanduser().resolve(strict=False)


def _validate_state_binding(
    metadata: dict[str, Any],
    systemd_user_dir: pathlib.Path | None,
    service_name: str,
) -> None:
    required = {"systemd_enabled", "systemd_user_dir", "service_name"}
    if not required.issubset(metadata):
        raise ConfigureError(
            "legacy model routing state lacks exact systemd binding; "
            "rollback is fail-closed and requires a fresh install"
        )
    if not isinstance(metadata["systemd_enabled"], bool):
        raise ConfigureError("model routing state has an invalid systemd binding")
    if metadata["systemd_user_dir"] is not None and not isinstance(
        metadata["systemd_user_dir"], str
    ):
        raise ConfigureError("model routing state has an invalid systemd directory binding")
    if not isinstance(metadata["service_name"], str):
        raise ConfigureError("model routing state has an invalid service binding")
    expected_enabled = systemd_user_dir is not None
    expected_directory = str(systemd_user_dir) if systemd_user_dir is not None else None
    if metadata["systemd_enabled"] != expected_enabled:
        raise ConfigureError("systemd mode must match the existing routing state")
    if metadata["systemd_user_dir"] != expected_directory:
        raise ConfigureError("systemd user directory must match the existing routing state")
    if metadata["service_name"] != service_name:
        raise ConfigureError("service name must match the existing routing state")


def _read_state_metadata(state: pathlib.Path, error: str) -> tuple[pathlib.Path, dict[str, Any]]:
    if state.is_symlink() or not state.is_dir():
        raise ConfigureError(error)
    metadata_path = state / "metadata.json"
    if metadata_path.is_symlink() or not metadata_path.is_file():
        raise ConfigureError(error)
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigureError(error) from exc
    if not isinstance(metadata, dict):
        raise ConfigureError(error)
    return metadata_path, metadata


def _metadata_sha256(metadata: dict[str, Any]) -> str:
    canonical = json.dumps(metadata, sort_keys=True, separators=(",", ":"))
    return _sha_bytes(canonical.encode("utf-8"))


def _path_binding(path: pathlib.Path | None) -> str | None:
    return str(path.resolve(strict=False)) if path is not None else None


def _validate_target_binding(
    metadata: dict[str, Any],
    config: pathlib.Path,
    catalog: pathlib.Path,
    dropin: pathlib.Path | None,
) -> None:
    expected = {
        "config_path": _path_binding(config),
        "catalog_path": _path_binding(catalog),
        "dropin_path": _path_binding(dropin),
    }
    if any(key not in metadata for key in expected):
        raise ConfigureError(
            "model routing state lacks exact target binding; rollback is fail-closed"
        )
    if any(metadata.get(key) != value for key, value in expected.items()):
        raise ConfigureError("model routing target path binding does not match existing state")


def _is_sha256(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(
        character in "0123456789abcdef" for character in value
    )


def _validate_restored_file(
    path: pathlib.Path,
    existed: object,
    expected_sha256: object,
    label: str,
) -> None:
    if not isinstance(existed, bool):
        raise ConfigureError("model routing completion marker has invalid target metadata")
    if existed:
        if not _is_sha256(expected_sha256):
            raise ConfigureError("model routing completion marker has invalid target hash")
        if path.is_symlink() or not path.is_file() or _sha(path) != expected_sha256:
            raise ConfigureError(f"{label} rollback target drifted after restore")
    else:
        if expected_sha256 is not None:
            raise ConfigureError("model routing completion marker has invalid target metadata")
        if path.is_symlink() or path.exists():
            raise ConfigureError(f"{label} rollback target drifted after restore")


def _validate_restored_targets(
    metadata: dict[str, Any],
    config: pathlib.Path,
    catalog: pathlib.Path,
    dropin: pathlib.Path | None,
) -> None:
    rollback_config_sha256 = metadata.get("rollback_config_sha256")
    if not _is_sha256(rollback_config_sha256):
        raise ConfigureError("model routing completion marker has invalid config hash")
    if config.is_symlink() or not config.is_file() or _sha(config) != rollback_config_sha256:
        raise ConfigureError("config rollback target drifted after restore")

    dropin_existed = metadata.get("dropin_existed")
    if not isinstance(dropin_existed, bool):
        raise ConfigureError("model routing completion marker has invalid target metadata")
    if dropin is None and (
        dropin_existed or metadata.get("dropin_sha256") is not None
    ):
        raise ConfigureError("model routing completion marker has invalid drop-in binding")
    if dropin is not None:
        _validate_restored_file(
            dropin,
            dropin_existed,
            metadata.get("dropin_sha256"),
            "drop-in",
        )
    _validate_restored_file(
        catalog,
        metadata.get("catalog_existed"),
        metadata.get("catalog_sha256"),
        "catalog",
    )


def _read_completion_marker(
    marker: pathlib.Path,
    systemd_user_dir: pathlib.Path | None,
    service_name: str,
    config: pathlib.Path,
    catalog: pathlib.Path,
    dropin: pathlib.Path | None,
) -> dict[str, Any] | None:
    if marker.is_symlink() or (marker.exists() and not marker.is_file()):
        raise ConfigureError("model routing completion marker is unsafe")
    if not marker.exists():
        return None
    try:
        payload = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigureError("model routing completion marker is invalid") from exc
    if not isinstance(payload, dict) or payload.get("schema") != COMPLETION_SCHEMA:
        raise ConfigureError("model routing completion marker has an unsupported schema")
    metadata = payload.get("metadata")
    if not isinstance(metadata, dict) or payload.get("metadata_sha256") != _metadata_sha256(metadata):
        raise ConfigureError("model routing completion marker integrity check failed")
    if metadata.get("schema") != STATE_SCHEMA:
        raise ConfigureError("model routing completion marker has an invalid state schema")
    _validate_state_binding(metadata, systemd_user_dir, service_name)
    progress = metadata.get("rollback_progress")
    if not isinstance(progress, dict) or any(
        progress.get(target) != "restored" for target in ROLLBACK_TARGETS
    ):
        raise ConfigureError("model routing completion marker is incomplete")
    _validate_target_binding(metadata, config, catalog, dropin)
    return metadata


def _publish_completion_marker(marker: pathlib.Path, metadata: dict[str, Any]) -> None:
    if marker.is_symlink() or marker.exists():
        raise ConfigureError("model routing completion marker collision")
    payload = {
        "schema": COMPLETION_SCHEMA,
        "metadata": metadata,
        "metadata_sha256": _metadata_sha256(metadata),
    }
    try:
        _atomic_write(marker, json.dumps(payload, sort_keys=True) + "\n", 0o600)
    except OSError as exc:
        raise ConfigureError(
            "model routing completion marker could not be published; retry rollback"
        ) from exc


def _remove_cleanup_tombstone(cleanup: pathlib.Path) -> None:
    if cleanup.is_symlink() or (cleanup.exists() and not cleanup.is_dir()):
        raise ConfigureError("model routing cleanup tombstone is unsafe")
    if not cleanup.exists():
        return
    try:
        shutil.rmtree(cleanup)
    except OSError as exc:
        raise ConfigureError("model routing cleanup incomplete; retry rollback") from exc


def _consume_completion_marker(marker: pathlib.Path) -> None:
    if marker.is_symlink() or not marker.is_file():
        raise ConfigureError("model routing completion marker is unsafe")
    try:
        marker.unlink()
    except OSError as exc:
        raise ConfigureError("model routing cleanup incomplete; retry rollback") from exc


def _resume_completed_cleanup(
    marker: pathlib.Path,
    marker_metadata: dict[str, Any],
    state: pathlib.Path,
    cleanup: pathlib.Path,
    config: pathlib.Path,
    catalog: pathlib.Path,
    dropin: pathlib.Path | None,
) -> dict[str, Any]:
    _validate_restored_targets(marker_metadata, config, catalog, dropin)
    if state.is_symlink() or (state.exists() and not state.is_dir()):
        raise ConfigureError("model routing state is unsafe")
    if cleanup.is_symlink() or (cleanup.exists() and not cleanup.is_dir()):
        raise ConfigureError("model routing cleanup tombstone is unsafe")
    if state.exists() and cleanup.exists():
        raise ConfigureError("model routing completion marker collides with active state")
    if state.exists():
        _metadata_path, state_metadata = _read_state_metadata(
            state, "model routing state is invalid"
        )
        if _metadata_sha256(state_metadata) != _metadata_sha256(marker_metadata):
            raise ConfigureError("model routing completion marker binding mismatch")
        try:
            os.replace(state, cleanup)
        except OSError as exc:
            raise ConfigureError(
                "model routing cleanup tombstone could not be published; retry rollback"
            ) from exc
    _remove_cleanup_tombstone(cleanup)
    try:
        _consume_completion_marker(marker)
    except OSError as exc:
        raise ConfigureError("model routing cleanup incomplete; retry rollback") from exc
    return {"status": "ROLLED_BACK", "dropin": str(dropin) if dropin is not None else None}


def _consume_completed_state(
    state: pathlib.Path,
    cleanup: pathlib.Path,
    marker: pathlib.Path,
    metadata: dict[str, Any],
    config: pathlib.Path,
    catalog: pathlib.Path,
    dropin: pathlib.Path | None,
) -> dict[str, Any]:
    _publish_completion_marker(marker, metadata)
    return _resume_completed_cleanup(marker, metadata, state, cleanup, config, catalog, dropin)


def _rollback_progress(metadata: dict[str, Any]) -> dict[str, str]:
    progress = metadata.setdefault(
        "rollback_progress",
        {target: "pending" for target in ROLLBACK_TARGETS[:-1]},
    )
    if not isinstance(progress, dict):
        raise ConfigureError("model routing rollback progress is invalid")
    for target in ROLLBACK_TARGETS[:-1]:
        if progress.get(target) not in {"pending", "restored"}:
            raise ConfigureError("model routing rollback progress is invalid")
    if "complete" in progress and progress["complete"] not in {"pending", "restored"}:
        raise ConfigureError("model routing rollback progress is invalid")
    return progress


def _mark_rollback_progress(
    metadata_path: pathlib.Path,
    metadata: dict[str, Any],
    target: str,
) -> None:
    if target not in ROLLBACK_TARGETS:
        raise ConfigureError("unknown model routing rollback target")
    progress = _rollback_progress(metadata)
    progress[target] = "restored"
    _atomic_write(metadata_path, json.dumps(metadata, sort_keys=True) + "\n", 0o600)


def _config_is_restored(
    current: str,
    original: str,
    config: pathlib.Path,
    expected_sha256: str | None = None,
) -> bool:
    if config.is_symlink():
        return False
    if expected_sha256 is not None and _sha_bytes(current.encode("utf-8")) != expected_sha256:
        return False
    _original_lines, original_match, original_value = _catalog_assignment(original)
    _current_lines, current_match, current_value = _catalog_assignment(current)
    if original_match is None:
        return current_match is None
    return current_match is not None and current_value == original_value


def _target_is_restored(
    path: pathlib.Path,
    existed: bool,
    backup: pathlib.Path | None,
    backup_sha256: str | None,
) -> bool:
    if path.is_symlink():
        return False
    if not existed:
        return not path.exists()
    return (
        backup is not None
        and backup_sha256 is not None
        and path.is_file()
        and _sha(path) == backup_sha256
    )


def _restore_backup(path: pathlib.Path, backup: pathlib.Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    staged = path.with_name(path.name + ".model-routing-rollback")
    shutil.copy2(backup, staged)
    os.replace(staged, path)


def _catalog_path(codex_home: pathlib.Path) -> pathlib.Path:
    """Resolve the managed relative path with the host platform's semantics."""

    return codex_home.joinpath(*CATALOG_RELATIVE.parts)


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
            _catalog_path(codex_home),
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
            str(_catalog_path(codex_home)),
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
    systemd_user_dir: pathlib.Path | None,
    service_name: str,
    exec_wrapper: pathlib.Path | None,
) -> dict[str, Any]:
    systemd_user_dir = _normalize_systemd_user_dir(systemd_user_dir)
    config = codex_home / "config.toml"
    if not config.is_file() or config.is_symlink():
        raise ConfigureError("Codex config.toml is unavailable or unsafe")
    if not codex_bin.is_file():
        raise ConfigureError("Codex binary is unavailable")
    if exec_wrapper is not None and not exec_wrapper.is_file():
        raise ConfigureError("execution wrapper is unavailable")

    state = codex_home / STATE_DIR
    cleanup = codex_home / CLEANUP_DIR
    marker = codex_home / COMPLETION_MARKER
    if cleanup.is_symlink() or (cleanup.exists() and not cleanup.is_dir()):
        raise ConfigureError("model routing cleanup tombstone is unsafe")
    if cleanup.exists():
        raise ConfigureError("model routing cleanup is incomplete; run rollback")
    if marker.is_symlink() or (marker.exists() and not marker.is_file()):
        raise ConfigureError("model routing completion marker is unsafe")
    if marker.exists():
        raise ConfigureError("model routing cleanup is incomplete; run rollback")
    metadata_path = state / "metadata.json"
    if systemd_user_dir is not None and sys.platform != "linux":
        raise ConfigureError(
            "systemd drop-ins are supported only on Linux; omit --systemd-user-dir"
        )
    dropin = _dropin_path(systemd_user_dir, service_name)
    catalog = _catalog_path(codex_home)
    if state.exists() and not metadata_path.exists():
        if state.is_symlink() or not state.is_dir() or any(state.iterdir()):
            raise ConfigureError("model routing state is incomplete or unsafe")
        state.rmdir()
    if dropin is not None and (dropin.is_symlink() or (dropin.exists() and not dropin.is_file())):
        raise ConfigureError("existing systemd drop-in is unsafe")
    if catalog.exists() and (catalog.is_symlink() or not catalog.is_file()):
        raise ConfigureError("existing model catalog is unsafe")
    if catalog.parent.exists() and (catalog.parent.is_symlink() or not catalog.parent.is_dir()):
        raise ConfigureError("model catalog directory is unsafe")
    if systemd_user_dir is not None:
        if systemd_user_dir.is_symlink() or (
            systemd_user_dir.exists() and not systemd_user_dir.is_dir()
        ):
            raise ConfigureError("systemd user directory is unsafe")
        service_dir = systemd_user_dir / f"{service_name}.d"
        if service_dir.is_symlink() or (service_dir.exists() and not service_dir.is_dir()):
            raise ConfigureError("systemd service drop-in directory is unsafe")
    _catalog_assignment(config.read_text(encoding="utf-8"))

    first_install = not metadata_path.exists()
    if first_install:
        temporary_state = pathlib.Path(tempfile.mkdtemp(prefix=STATE_DIR + ".", dir=codex_home))
        os.chmod(temporary_state, 0o700)
        try:
            shutil.copy2(config, temporary_state / "config.toml.before")
            dropin_existed = dropin is not None and dropin.is_file()
            if dropin_existed:
                shutil.copy2(dropin, temporary_state / "dropin.before")
            catalog_existed = catalog.is_file()
            if catalog_existed:
                shutil.copy2(catalog, temporary_state / "catalog.before")
            metadata = {
                "schema": STATE_SCHEMA,
                "config_path": _path_binding(config),
                "catalog_path": _path_binding(catalog),
                "dropin_path": _path_binding(dropin),
                "config_sha256": _sha(temporary_state / "config.toml.before"),
                "dropin_existed": dropin_existed,
                "dropin_sha256": (
                    _sha(temporary_state / "dropin.before") if dropin_existed else None
                ),
                "catalog_existed": catalog_existed,
                "catalog_sha256": (
                    _sha(temporary_state / "catalog.before") if catalog_existed else None
                ),
                "systemd_enabled": systemd_user_dir is not None,
                "systemd_user_dir": str(systemd_user_dir) if systemd_user_dir else None,
                "service_name": service_name,
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
        _validate_state_binding(metadata, systemd_user_dir, service_name)
        _validate_target_binding(metadata, config, catalog, dropin)
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
        if dropin is not None:
            _atomic_write(dropin, _dropin(codex_home, codex_bin, exec_wrapper), 0o644)
        metadata["committed"] = True
        _atomic_write(metadata_path, json.dumps(metadata, sort_keys=True) + "\n", 0o600)
    except BaseException:
        if first_install:
            shutil.copy2(state / "config.toml.before", config)
            if metadata["dropin_existed"] and dropin is not None:
                dropin.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(state / "dropin.before", dropin)
            elif dropin is not None and dropin.is_file() and not dropin.is_symlink():
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
        "catalog": str(_catalog_path(codex_home)),
        "dropin": str(dropin) if dropin is not None else None,
        "refresh_status": refresh["status"],
    }


def rollback(
    codex_home: pathlib.Path,
    systemd_user_dir: pathlib.Path | None,
    service_name: str,
) -> dict[str, Any]:
    systemd_user_dir = _normalize_systemd_user_dir(systemd_user_dir)
    state = codex_home / STATE_DIR
    cleanup = codex_home / CLEANUP_DIR
    marker = codex_home / COMPLETION_MARKER
    if systemd_user_dir is not None and sys.platform != "linux":
        raise ConfigureError(
            "systemd drop-ins are supported only on Linux; omit --systemd-user-dir"
        )
    dropin = _dropin_path(systemd_user_dir, service_name)
    config = codex_home / "config.toml"
    catalog = _catalog_path(codex_home)
    marker_metadata = _read_completion_marker(
        marker, systemd_user_dir, service_name, config, catalog, dropin
    )
    if marker_metadata is not None:
        return _resume_completed_cleanup(
            marker, marker_metadata, state, cleanup, config, catalog, dropin
        )
    if cleanup.is_symlink() or (cleanup.exists() and not cleanup.is_dir()):
        raise ConfigureError("model routing cleanup tombstone is unsafe")
    if cleanup.exists():
        raise ConfigureError("model routing cleanup tombstone lacks completion marker")
    metadata_path, metadata = _read_state_metadata(
        state, "no valid model routing state"
    )
    if metadata.get("schema") != STATE_SCHEMA:
        raise ConfigureError("model routing state has an unsupported schema")
    _validate_state_binding(metadata, systemd_user_dir, service_name)
    _validate_target_binding(metadata, config, catalog, dropin)
    progress = _rollback_progress(metadata)

    config_backup = state / "config.toml.before"
    if _sha(config_backup) != metadata.get("config_sha256"):
        raise ConfigureError("config backup integrity check failed")
    original_config = config_backup.read_text(encoding="utf-8")
    if not config.is_file() or config.is_symlink():
        raise ConfigureError("config rollback target is unsafe")

    dropin_backup = state / "dropin.before"
    if dropin is not None and metadata.get("dropin_existed"):
        if _sha(dropin_backup) != metadata.get("dropin_sha256"):
            raise ConfigureError("drop-in backup integrity check failed")
    if dropin is not None and dropin.is_symlink():
        raise ConfigureError("systemd drop-in rollback target is unsafe")
    if dropin is not None and dropin.exists() and not dropin.is_file():
        raise ConfigureError("systemd drop-in rollback target is unsafe")

    catalog_backup = state / "catalog.before"
    if metadata.get("catalog_existed"):
        if _sha(catalog_backup) != metadata.get("catalog_sha256"):
            raise ConfigureError("catalog backup integrity check failed")
    if catalog.is_symlink() or (catalog.exists() and not catalog.is_file()):
        raise ConfigureError("catalog rollback target is unsafe")

    if progress["config"] == "restored":
        if not _config_is_restored(
            config.read_text(encoding="utf-8"),
            original_config,
            config,
            metadata.get("rollback_config_sha256"),
        ):
            raise ConfigureError("config rollback target drifted after restore")
    else:
        current_config = config.read_text(encoding="utf-8")
        if not _config_is_restored(
            current_config,
            original_config,
            config,
            metadata.get("rollback_config_sha256"),
        ):
            restored_config = _restore_catalog_setting(
                current_config,
                original_config,
                catalog,
            )
            metadata["rollback_config_sha256"] = _sha_bytes(
                restored_config.encode("utf-8")
            )
            _atomic_write(metadata_path, json.dumps(metadata, sort_keys=True) + "\n", 0o600)
            _atomic_write(config, restored_config, 0o600)
        elif metadata.get("rollback_config_sha256") is None:
            metadata["rollback_config_sha256"] = _sha_bytes(current_config.encode("utf-8"))
        if not _config_is_restored(
            config.read_text(encoding="utf-8"),
            original_config,
            config,
            metadata.get("rollback_config_sha256"),
        ):
            raise ConfigureError("config rollback validation failed")
        _mark_rollback_progress(metadata_path, metadata, "config")

    if progress["dropin"] == "restored":
        if dropin is not None and not _target_is_restored(
            dropin,
            bool(metadata.get("dropin_existed")),
            dropin_backup if metadata.get("dropin_existed") else None,
            metadata.get("dropin_sha256"),
        ):
            raise ConfigureError("drop-in rollback target drifted after restore")
    else:
        if dropin is not None and metadata.get("dropin_existed"):
            if not _target_is_restored(
                dropin, True, dropin_backup, metadata.get("dropin_sha256")
            ):
                _restore_backup(dropin, dropin_backup)
        elif dropin is not None and dropin.exists():
            dropin.unlink()
        if dropin is not None and not _target_is_restored(
            dropin,
            bool(metadata.get("dropin_existed")),
            dropin_backup if metadata.get("dropin_existed") else None,
            metadata.get("dropin_sha256"),
        ):
            raise ConfigureError("drop-in rollback validation failed")
        _mark_rollback_progress(metadata_path, metadata, "dropin")

    if progress["catalog"] == "restored":
        if not _target_is_restored(
            catalog,
            bool(metadata.get("catalog_existed")),
            catalog_backup if metadata.get("catalog_existed") else None,
            metadata.get("catalog_sha256"),
        ):
            raise ConfigureError("catalog rollback target drifted after restore")
    else:
        if metadata.get("catalog_existed"):
            if not _target_is_restored(catalog, True, catalog_backup, metadata.get("catalog_sha256")):
                _restore_backup(catalog, catalog_backup)
        elif catalog.exists():
            catalog.unlink()
        if not _target_is_restored(
            catalog,
            bool(metadata.get("catalog_existed")),
            catalog_backup if metadata.get("catalog_existed") else None,
            metadata.get("catalog_sha256"),
        ):
            raise ConfigureError("catalog rollback validation failed")
        _mark_rollback_progress(metadata_path, metadata, "catalog")

    _mark_rollback_progress(metadata_path, metadata, "complete")
    return _consume_completed_state(
        state, cleanup, marker, metadata, config, catalog, dropin
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--codex-home", required=True)
    parser.add_argument("--codex-bin", required=True)
    parser.add_argument(
        "--systemd-user-dir",
        help="optional user-systemd directory; omit for on-demand mode",
    )
    parser.add_argument("--service-name", default="codex-app-server.service")
    parser.add_argument("--exec-wrapper")
    parser.add_argument("--rollback", action="store_true")
    args = parser.parse_args()

    codex_home = pathlib.Path(args.codex_home).expanduser().resolve(strict=True)
    systemd_user_dir = (
        pathlib.Path(args.systemd_user_dir).expanduser().resolve()
        if args.systemd_user_dir
        else None
    )
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

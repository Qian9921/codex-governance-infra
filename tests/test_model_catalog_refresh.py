import importlib.util
import json
import pathlib
import tempfile
import unittest
from unittest import mock


ROOT = pathlib.Path(__file__).parents[1]
MODULE_PATH = ROOT / "codex" / "bin" / "refresh-model-catalog.py"
SPEC = importlib.util.spec_from_file_location("refresh_model_catalog", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def catalog(*entries):
    return {"models": [dict(entry) for entry in entries]}


class ModelCatalogRefresh(unittest.TestCase):
    def test_windows_native_and_command_shim_argv_is_shell_free(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            for suffix in (".exe", ".cmd", ".bat"):
                command = root / ("codex" + suffix)
                command.write_text("placeholder", encoding="utf-8")
                with mock.patch.object(MODULE.sys, "platform", "win32"):
                    self.assertEqual(
                        MODULE._codex_argv(command),
                        [str(command), "debug", "models"],
                    )

    def test_windows_powershell_shim_uses_resolved_interpreter_without_shell(self):
        with tempfile.TemporaryDirectory() as directory:
            command = pathlib.Path(directory) / "codex.ps1"
            command.write_text("Write-Output test", encoding="utf-8")
            with mock.patch.object(MODULE.sys, "platform", "win32"):
                with mock.patch.object(MODULE.shutil, "which", return_value="pwsh.exe"):
                    self.assertEqual(
                        MODULE._codex_argv(command),
                        [
                            "pwsh.exe",
                            "-NoProfile",
                            "-NonInteractive",
                            "-File",
                            str(command),
                            "debug",
                            "models",
                        ],
                    )

    def test_windows_unsupported_command_suffix_fails_clearly(self):
        with tempfile.TemporaryDirectory() as directory:
            command = pathlib.Path(directory) / "codex.sh"
            command.write_text("placeholder", encoding="utf-8")
            with mock.patch.object(MODULE.sys, "platform", "win32"):
                with self.assertRaisesRegex(MODULE.CatalogError, "unsupported Windows"):
                    MODULE._codex_argv(command)

    def test_windows_powershell_discovery_passes_shell_free_argv_to_subprocess(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            home = root / "codex-home"
            home.mkdir()
            command = root / "codex.ps1"
            command.write_text("Write-Output test", encoding="utf-8")
            result = mock.Mock(
                returncode=0,
                stdout=json.dumps({"models": [{"slug": "gpt-5.6-luna"}]}),
            )
            with mock.patch.object(MODULE.sys, "platform", "win32"):
                with mock.patch.object(MODULE.shutil, "which", return_value="pwsh.exe"):
                    with mock.patch.object(MODULE.subprocess, "run", return_value=result) as run:
                        MODULE._catalog_from_codex(command, home)
            argv = run.call_args.args[0]
            self.assertEqual(argv[:4], ["pwsh.exe", "-NoProfile", "-NonInteractive", "-File"])
            self.assertEqual(argv[4:], [str(command), "debug", "models"])

    def test_normalize_routes_luna_and_entitled_spark_to_v2(self):
        value, patched, absent = MODULE.normalize(
            catalog(
                {"slug": "gpt-5.6-sol", "multi_agent_version": "v2"},
                {"slug": "gpt-5.6-luna", "multi_agent_version": "v1"},
                {"slug": "gpt-5.3-codex-spark", "multi_agent_version": None},
            )
        )
        models = {item["slug"]: item for item in value["models"]}
        self.assertEqual(models["gpt-5.6-luna"]["multi_agent_version"], "v2")
        self.assertEqual(models["gpt-5.3-codex-spark"]["multi_agent_version"], "v2")
        self.assertEqual(patched, ["gpt-5.6-luna", "gpt-5.3-codex-spark"])
        self.assertEqual(absent, [])

    def test_missing_luna_is_rejected(self):
        with self.assertRaisesRegex(MODULE.CatalogError, "gpt-5.6-luna"):
            MODULE.normalize(catalog({"slug": "gpt-5.6-sol", "multi_agent_version": "v2"}))

    def test_optional_spark_absence_does_not_block_luna(self):
        _value, patched, absent = MODULE.normalize(
            catalog({"slug": "gpt-5.6-luna", "multi_agent_version": "v1"})
        )
        self.assertEqual(patched, ["gpt-5.6-luna"])
        self.assertEqual(absent, ["gpt-5.3-codex-spark"])

    def test_publish_is_private_and_validated(self):
        with tempfile.TemporaryDirectory() as directory:
            output = pathlib.Path(directory) / "catalog.json"
            value, _patched, _absent = MODULE.normalize(
                catalog({"slug": "gpt-5.6-luna", "multi_agent_version": "v1"})
            )
            MODULE._publish(output, value)
            MODULE.validate_overlay(output)
            self.assertEqual(output.stat().st_mode & 0o777, 0o600)
            self.assertEqual(json.loads(output.read_text(encoding="utf-8")), value)

    def test_last_known_good_survives_refresh_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            output = root / "catalog.json"
            value, _patched, _absent = MODULE.normalize(
                catalog({"slug": "gpt-5.6-luna", "multi_agent_version": "v1"})
            )
            MODULE._publish(output, value)
            result = MODULE.refresh(root / "missing-codex", root, output)
            self.assertEqual(result["status"], "READY_LAST_KNOWN_GOOD")
            MODULE.validate_overlay(output)

    def test_publish_rejects_symlink_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            outside = root / "outside.json"
            outside.write_text("keep", encoding="utf-8")
            output = root / "catalog.json"
            output.symlink_to(outside)
            value, _patched, _absent = MODULE.normalize(
                catalog({"slug": "gpt-5.6-luna", "multi_agent_version": "v1"})
            )
            with self.assertRaisesRegex(MODULE.CatalogError, "unsafe"):
                MODULE._publish(output, value)
            self.assertEqual(outside.read_text(encoding="utf-8"), "keep")

    def test_publish_simulates_missing_fchmod_with_atomic_private_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            output = pathlib.Path(directory) / "catalog.json"
            value, _patched, _absent = MODULE.normalize(
                catalog({"slug": "gpt-5.6-luna", "multi_agent_version": "v1"})
            )
            with mock.patch.object(MODULE.os, "fchmod", side_effect=AttributeError):
                MODULE._publish(output, value)
            MODULE.validate_overlay(output)
            self.assertEqual(output.stat().st_mode & 0o777, 0o600)

    def test_windows_mode_simulation_stages_auth_without_symlink(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            home = root / "codex-home"
            home.mkdir()
            (home / "auth.json").write_text('{"token":"test-only"}\n', encoding="utf-8")
            codex = root / "codex.exe"
            codex.write_text("placeholder", encoding="utf-8")
            observed_home = []

            def discover(_command, **kwargs):
                isolated = pathlib.Path(kwargs["env"]["CODEX_HOME"])
                staged_auth = isolated / "auth.json"
                observed_home.append(isolated)
                self.assertTrue(staged_auth.is_file())
                self.assertFalse(staged_auth.is_symlink())
                self.assertEqual(staged_auth.read_text(encoding="utf-8"), '{"token":"test-only"}\n')
                return mock.Mock(
                    returncode=0,
                    stdout=json.dumps({"models": [{"slug": "gpt-5.6-luna"}]}),
                )

            with mock.patch.object(MODULE.sys, "platform", "win32"):
                with mock.patch.object(MODULE.subprocess, "run", side_effect=discover):
                    result = MODULE._catalog_from_codex(codex, home)
            self.assertEqual(result["models"][0]["slug"], "gpt-5.6-luna")
            self.assertFalse(observed_home[0].exists())


if __name__ == "__main__":
    unittest.main()

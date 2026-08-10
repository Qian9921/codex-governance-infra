import importlib.util
import json
import pathlib
import shutil
import tempfile
import unittest
from unittest import mock


ROOT = pathlib.Path(__file__).parents[1]
MODULE_PATH = ROOT / "scripts" / "configure-model-routing.py"
SPEC = importlib.util.spec_from_file_location("configure_model_routing", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


class ConfigureModelRouting(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.temporary.name)
        self.home = self.root / "codex-home"
        self.home.mkdir()
        (self.home / "config.toml").write_text(
            'model = "gpt-5.6-sol"\nmodel_reasoning_effort = "medium"\n\n[agents]\nmax_threads = 4\n',
            encoding="utf-8",
        )
        (self.home / "bin").mkdir()
        shutil.copy2(ROOT / "codex" / "bin" / "refresh-model-catalog.py", self.home / "bin")
        self.codex = self.root / "codex"
        self.codex.write_text(
            "#!/usr/bin/env python3\n"
            "import json\n"
            "print(json.dumps({'models':["
            "{'slug':'gpt-5.6-sol','multi_agent_version':'v2'},"
            "{'slug':'gpt-5.6-luna','multi_agent_version':'v1'},"
            "{'slug':'gpt-5.3-codex-spark','multi_agent_version':None}]}))\n",
            encoding="utf-8",
        )
        self.codex.chmod(0o755)
        self.systemd = self.root / "systemd"

    def _new_home(self, name):
        home = self.root / name
        home.mkdir()
        (home / "config.toml").write_text(
            'model = "gpt-5.6-sol"\nmodel_reasoning_effort = "medium"\n\n[agents]\nmax_threads = 4\n',
            encoding="utf-8",
        )
        (home / "bin").mkdir()
        shutil.copy2(ROOT / "codex" / "bin" / "refresh-model-catalog.py", home / "bin")
        return home

    def _leave_completed_cleanup_after_fault(self):
        MODULE.configure(self.home, self.codex, self.systemd, "codex-app-server.service", None)
        cleanup = self.home / MODULE.CLEANUP_DIR
        marker = self.home / MODULE.COMPLETION_MARKER
        unrelated = self.root / "unrelated-after-cleanup-fault"
        unrelated.write_text("keep", encoding="utf-8")
        original_rmtree = MODULE.shutil.rmtree

        def fail_cleanup(path, *args, **kwargs):
            if pathlib.Path(path) == cleanup:
                raise OSError("injected cleanup fault")
            return original_rmtree(path, *args, **kwargs)

        with mock.patch.object(MODULE.shutil, "rmtree", side_effect=fail_cleanup):
            with self.assertRaisesRegex(
                MODULE.ConfigureError, "cleanup incomplete; retry rollback"
            ):
                MODULE.rollback(self.home, self.systemd, "codex-app-server.service")
        self.assertTrue(cleanup.is_dir())
        self.assertTrue(marker.is_file())
        return cleanup, marker, unrelated

    def tearDown(self):
        self.temporary.cleanup()

    def test_configure_is_idempotent_and_rollback_restores_original(self):
        original = (self.home / "config.toml").read_text(encoding="utf-8")
        first = MODULE.configure(self.home, self.codex, self.systemd, "codex-app-server.service", None)
        second = MODULE.configure(self.home, self.codex, self.systemd, "codex-app-server.service", None)
        self.assertEqual(first["status"], "READY")
        self.assertEqual(second["status"], "READY")
        configured = (self.home / "config.toml").read_text(encoding="utf-8")
        self.assertEqual(configured.count("model_catalog_json"), 1)
        catalog = json.loads((self.home / "model-catalogs" / "multi-agent-v2.json").read_text())
        models = {item["slug"]: item for item in catalog["models"]}
        self.assertEqual(models["gpt-5.6-luna"]["multi_agent_version"], "v2")
        self.assertEqual(models["gpt-5.3-codex-spark"]["multi_agent_version"], "v2")
        dropin = self.systemd / "codex-app-server.service.d" / MODULE.DROPIN_NAME
        self.assertIn("ExecStartPre=", dropin.read_text(encoding="utf-8"))
        metadata = json.loads(
            (self.home / MODULE.STATE_DIR / "metadata.json").read_text(encoding="utf-8")
        )
        self.assertTrue(metadata["systemd_enabled"])
        self.assertEqual(metadata["systemd_user_dir"], str(self.systemd.resolve()))
        self.assertEqual(metadata["service_name"], "codex-app-server.service")
        result = MODULE.rollback(self.home, self.systemd, "codex-app-server.service")
        self.assertEqual(result["status"], "ROLLED_BACK")
        self.assertEqual((self.home / "config.toml").read_text(encoding="utf-8"), original)
        self.assertFalse(dropin.exists())

    def test_existing_dropin_is_restored(self):
        dropin = self.systemd / "codex-app-server.service.d" / MODULE.DROPIN_NAME
        dropin.parent.mkdir(parents=True)
        dropin.write_text("[Service]\nEnvironment=KEEP=1\n", encoding="utf-8")
        MODULE.configure(self.home, self.codex, self.systemd, "codex-app-server.service", None)
        MODULE.rollback(self.home, self.systemd, "codex-app-server.service")
        self.assertEqual(dropin.read_text(encoding="utf-8"), "[Service]\nEnvironment=KEEP=1\n")

    def test_rollback_preserves_unrelated_config_added_after_install(self):
        MODULE.configure(self.home, self.codex, self.systemd, "codex-app-server.service", None)
        config = self.home / "config.toml"
        config.write_text(
            config.read_text(encoding="utf-8") + "\n[features]\nhooks = true\n",
            encoding="utf-8",
        )
        MODULE.rollback(self.home, self.systemd, "codex-app-server.service")
        restored = config.read_text(encoding="utf-8")
        self.assertNotIn("model_catalog_json", restored)
        self.assertIn("[features]\nhooks = true", restored)

    def test_quoted_top_level_catalog_key_is_replaced_and_restored(self):
        config = self.home / "config.toml"
        original = config.read_text(encoding="utf-8").replace(
            'model = "gpt-5.6-sol"\n',
            'model = "gpt-5.6-sol"\n"model_catalog_json" = "/previous.json"\n',
        )
        config.write_text(original, encoding="utf-8")
        MODULE.configure(self.home, self.codex, self.systemd, "codex-app-server.service", None)
        configured = config.read_text(encoding="utf-8")
        self.assertEqual(configured.count("model_catalog_json"), 1)
        MODULE.rollback(self.home, self.systemd, "codex-app-server.service")
        self.assertEqual(config.read_text(encoding="utf-8"), original)

    def test_unsafe_dropin_is_rejected_without_partial_state(self):
        dropin = self.systemd / "codex-app-server.service.d" / MODULE.DROPIN_NAME
        dropin.parent.mkdir(parents=True)
        target = self.root / "outside"
        target.write_text("keep", encoding="utf-8")
        dropin.symlink_to(target)
        with self.assertRaisesRegex(MODULE.ConfigureError, "drop-in is unsafe"):
            MODULE.configure(self.home, self.codex, self.systemd, "codex-app-server.service", None)
        self.assertFalse((self.home / MODULE.STATE_DIR).exists())
        self.assertFalse((self.home / MODULE.CATALOG_RELATIVE).exists())
        self.assertEqual(target.read_text(encoding="utf-8"), "keep")

    def test_unsupported_escaped_quoted_key_is_rejected_before_mutation(self):
        config = self.home / "config.toml"
        config.write_text(
            '"model_catalog_\\u006ason" = "/previous.json"\nmodel = "gpt-5.6-sol"\n',
            encoding="utf-8",
        )
        with self.assertRaisesRegex(MODULE.ConfigureError, "unsupported"):
            MODULE.configure(self.home, self.codex, self.systemd, "codex-app-server.service", None)
        self.assertFalse((self.home / MODULE.STATE_DIR).exists())
        self.assertFalse((self.home / MODULE.CATALOG_RELATIVE).exists())

    def test_metadata_publication_failure_leaves_retryable_state(self):
        with mock.patch.object(MODULE, "_atomic_write", side_effect=OSError("injected")):
            with self.assertRaisesRegex(OSError, "injected"):
                MODULE.configure(self.home, self.codex, self.systemd, "codex-app-server.service", None)
        self.assertFalse((self.home / MODULE.STATE_DIR).exists())
        self.assertFalse((self.home / MODULE.CATALOG_RELATIVE).exists())
        result = MODULE.configure(self.home, self.codex, self.systemd, "codex-app-server.service", None)
        self.assertEqual(result["status"], "READY")

    def test_on_demand_mode_is_portable_and_rolls_back_on_all_supported_platforms(self):
        for platform in ("linux", "darwin", "win32"):
            home = self._new_home("codex-home-" + platform)
            original = (home / "config.toml").read_text(encoding="utf-8")
            unrelated_startup = self.root / ("startup-" + platform + ".txt")
            unrelated_startup.write_text("keep", encoding="utf-8")
            with mock.patch.object(MODULE.sys, "platform", platform):
                first = MODULE.configure(home, self.codex, None, "codex-app-server.service", None)
                second = MODULE.configure(home, self.codex, None, "codex-app-server.service", None)
                self.assertEqual(first["status"], "READY")
                self.assertEqual(second["status"], "READY")
                self.assertIsNone(first["dropin"])
                self.assertFalse((self.root / "systemd" / "codex-app-server.service.d").exists())
                metadata = json.loads(
                    (home / MODULE.STATE_DIR / "metadata.json").read_text(encoding="utf-8")
                )
                self.assertFalse(metadata["systemd_enabled"])
                result = MODULE.rollback(home, None, "codex-app-server.service")
            self.assertEqual(result["status"], "ROLLED_BACK")
            self.assertIsNone(result["dropin"])
            self.assertEqual((home / "config.toml").read_text(encoding="utf-8"), original)
            self.assertFalse((home / MODULE.STATE_DIR).exists())
            self.assertEqual(unrelated_startup.read_text(encoding="utf-8"), "keep")

    def test_cli_allows_on_demand_configuration_and_rollback_without_systemd_argument(self):
        command = [
            "configure-model-routing.py",
            "--codex-home",
            str(self.home),
            "--codex-bin",
            str(self.codex),
        ]
        with mock.patch.object(MODULE.sys, "argv", command):
            self.assertEqual(MODULE.main(), 0)
        self.assertTrue((self.home / MODULE.STATE_DIR / "metadata.json").is_file())
        with mock.patch.object(MODULE.sys, "argv", command + ["--rollback"]):
            self.assertEqual(MODULE.main(), 0)
        self.assertFalse((self.home / MODULE.STATE_DIR).exists())

    def test_non_linux_systemd_argument_is_rejected_before_mutation(self):
        home = self._new_home("codex-home-windows-systemd")
        with mock.patch.object(MODULE.sys, "platform", "win32"):
            with self.assertRaisesRegex(MODULE.ConfigureError, "only on Linux"):
                MODULE.configure(home, self.codex, self.systemd, "codex-app-server.service", None)
        self.assertFalse((home / MODULE.STATE_DIR).exists())
        self.assertFalse((home / MODULE.CATALOG_RELATIVE).exists())

    def test_systemd_directory_and_service_bindings_reject_drift_before_mutation(self):
        MODULE.configure(self.home, self.codex, self.systemd, "codex-app-server.service", None)
        config = self.home / "config.toml"
        dropin = self.systemd / "codex-app-server.service.d" / MODULE.DROPIN_NAME
        original_config = config.read_bytes()
        original_dropin = dropin.read_bytes()
        other_systemd = self.root / "other-systemd"

        with self.assertRaisesRegex(MODULE.ConfigureError, "user directory"):
            MODULE.configure(self.home, self.codex, other_systemd, "codex-app-server.service", None)
        with self.assertRaisesRegex(MODULE.ConfigureError, "service name"):
            MODULE.configure(self.home, self.codex, self.systemd, "other.service", None)
        with self.assertRaisesRegex(MODULE.ConfigureError, "user directory"):
            MODULE.rollback(self.home, other_systemd, "codex-app-server.service")
        with self.assertRaisesRegex(MODULE.ConfigureError, "service name"):
            MODULE.rollback(self.home, self.systemd, "other.service")

        self.assertEqual(config.read_bytes(), original_config)
        self.assertEqual(dropin.read_bytes(), original_dropin)
        self.assertFalse((other_systemd / "codex-app-server.service.d").exists())
        self.assertTrue((self.home / MODULE.STATE_DIR / "metadata.json").is_file())

    def test_legacy_state_without_exact_binding_fails_closed(self):
        MODULE.configure(self.home, self.codex, self.systemd, "codex-app-server.service", None)
        config = self.home / "config.toml"
        before = config.read_bytes()
        metadata_path = self.home / MODULE.STATE_DIR / "metadata.json"
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        metadata.pop("systemd_user_dir")
        metadata.pop("service_name")
        metadata_path.write_text(json.dumps(metadata), encoding="utf-8")

        with self.assertRaisesRegex(MODULE.ConfigureError, "legacy model routing state"):
            MODULE.rollback(self.home, self.systemd, "codex-app-server.service")
        self.assertEqual(config.read_bytes(), before)
        self.assertTrue(metadata_path.is_file())

    def test_rollback_resumes_after_fault_immediately_after_config_restore(self):
        MODULE.configure(self.home, self.codex, self.systemd, "codex-app-server.service", None)
        unrelated = self.root / "unrelated-startup-state"
        unrelated.write_text("keep", encoding="utf-8")
        original_mark = MODULE._mark_rollback_progress

        def fail_after_config(path, metadata, target):
            if target == "config":
                raise OSError("injected after config restore")
            return original_mark(path, metadata, target)

        with mock.patch.object(MODULE, "_mark_rollback_progress", side_effect=fail_after_config):
            with self.assertRaisesRegex(OSError, "after config restore"):
                MODULE.rollback(self.home, self.systemd, "codex-app-server.service")
        self.assertTrue((self.home / MODULE.STATE_DIR).is_dir())
        self.assertNotIn("model_catalog_json", (self.home / "config.toml").read_text())
        self.assertEqual(unrelated.read_text(encoding="utf-8"), "keep")

        result = MODULE.rollback(self.home, self.systemd, "codex-app-server.service")
        self.assertEqual(result["status"], "ROLLED_BACK")
        self.assertFalse((self.home / MODULE.STATE_DIR).exists())
        self.assertFalse((self.home / "model-catalogs" / "multi-agent-v2.json").exists())
        self.assertEqual(unrelated.read_text(encoding="utf-8"), "keep")

    def test_rollback_resumes_after_fault_immediately_after_dropin_restore(self):
        dropin = self.systemd / "codex-app-server.service.d" / MODULE.DROPIN_NAME
        dropin.parent.mkdir(parents=True)
        dropin.write_text("[Service]\nEnvironment=KEEP=1\n", encoding="utf-8")
        MODULE.configure(self.home, self.codex, self.systemd, "codex-app-server.service", None)
        unrelated = self.root / "unrelated-startup-state"
        unrelated.write_text("keep", encoding="utf-8")
        original_mark = MODULE._mark_rollback_progress

        def fail_after_dropin(path, metadata, target):
            if target == "dropin":
                raise OSError("injected after drop-in restore")
            return original_mark(path, metadata, target)

        with mock.patch.object(MODULE, "_mark_rollback_progress", side_effect=fail_after_dropin):
            with self.assertRaisesRegex(OSError, "after drop-in restore"):
                MODULE.rollback(self.home, self.systemd, "codex-app-server.service")
        self.assertTrue((self.home / MODULE.STATE_DIR).is_dir())
        self.assertEqual(dropin.read_text(encoding="utf-8"), "[Service]\nEnvironment=KEEP=1\n")
        self.assertEqual(unrelated.read_text(encoding="utf-8"), "keep")

        result = MODULE.rollback(self.home, self.systemd, "codex-app-server.service")
        self.assertEqual(result["status"], "ROLLED_BACK")
        self.assertFalse((self.home / MODULE.STATE_DIR).exists())
        self.assertEqual(dropin.read_text(encoding="utf-8"), "[Service]\nEnvironment=KEEP=1\n")
        self.assertEqual(unrelated.read_text(encoding="utf-8"), "keep")

    def test_rollback_retries_completed_cleanup_after_partial_final_cleanup(self):
        dropin = self.systemd / "codex-app-server.service.d" / MODULE.DROPIN_NAME
        dropin.parent.mkdir(parents=True)
        original_dropin = "[Service]\nEnvironment=KEEP=1\n"
        dropin.write_text(original_dropin, encoding="utf-8")
        catalog = self.home / MODULE.CATALOG_RELATIVE
        catalog.parent.mkdir(parents=True)
        original_catalog = '{"models": [{"slug": "before"}]}\n'
        catalog.write_text(original_catalog, encoding="utf-8")
        original_config = (self.home / "config.toml").read_text(encoding="utf-8")
        MODULE.configure(self.home, self.codex, self.systemd, "codex-app-server.service", None)
        unrelated = self.root / "unrelated-startup-state"
        unrelated.write_text("keep", encoding="utf-8")
        state = self.home / MODULE.STATE_DIR
        cleanup = self.home / MODULE.CLEANUP_DIR
        marker = self.home / MODULE.COMPLETION_MARKER
        original_rmtree = MODULE.shutil.rmtree

        def fail_after_backup_deletion(path, *args, **kwargs):
            if pathlib.Path(path) == cleanup:
                (cleanup / "config.toml.before").unlink()
                raise OSError("injected after config backup deletion")
            return original_rmtree(path, *args, **kwargs)

        with mock.patch.object(
            MODULE.shutil, "rmtree", side_effect=fail_after_backup_deletion
        ):
            with self.assertRaisesRegex(
                MODULE.ConfigureError, "cleanup incomplete; retry rollback"
            ):
                MODULE.rollback(self.home, self.systemd, "codex-app-server.service")

        self.assertFalse(state.exists())
        self.assertTrue(cleanup.is_dir())
        self.assertTrue(marker.is_file())
        self.assertFalse((cleanup / "config.toml.before").exists())
        self.assertEqual((self.home / "config.toml").read_text(encoding="utf-8"), original_config)
        self.assertEqual(dropin.read_text(encoding="utf-8"), original_dropin)
        self.assertEqual(catalog.read_text(encoding="utf-8"), original_catalog)
        self.assertEqual(unrelated.read_text(encoding="utf-8"), "keep")

        result = MODULE.rollback(self.home, self.systemd, "codex-app-server.service")
        self.assertEqual(result["status"], "ROLLED_BACK")
        self.assertFalse(state.exists())
        self.assertFalse(cleanup.exists())
        self.assertFalse(marker.exists())
        self.assertEqual((self.home / "config.toml").read_text(encoding="utf-8"), original_config)
        self.assertEqual(dropin.read_text(encoding="utf-8"), original_dropin)
        self.assertEqual(catalog.read_text(encoding="utf-8"), original_catalog)
        self.assertEqual(unrelated.read_text(encoding="utf-8"), "keep")

    def test_rollback_retries_after_all_cleanup_children_are_deleted(self):
        dropin = self.systemd / "codex-app-server.service.d" / MODULE.DROPIN_NAME
        dropin.parent.mkdir(parents=True)
        original_dropin = "[Service]\nEnvironment=KEEP=1\n"
        dropin.write_text(original_dropin, encoding="utf-8")
        catalog = self.home / MODULE.CATALOG_RELATIVE
        catalog.parent.mkdir(parents=True)
        original_catalog = '{"models": [{"slug": "before"}]}\n'
        catalog.write_text(original_catalog, encoding="utf-8")
        original_config = (self.home / "config.toml").read_text(encoding="utf-8")
        MODULE.configure(self.home, self.codex, self.systemd, "codex-app-server.service", None)
        unrelated = self.root / "unrelated-startup-state"
        unrelated.write_text("keep", encoding="utf-8")
        cleanup = self.home / MODULE.CLEANUP_DIR
        marker = self.home / MODULE.COMPLETION_MARKER
        original_rmtree = MODULE.shutil.rmtree

        def fail_after_all_children(path, *args, **kwargs):
            if pathlib.Path(path) == cleanup:
                for child in list(cleanup.iterdir()):
                    if child.is_dir() and not child.is_symlink():
                        original_rmtree(child)
                    else:
                        child.unlink()
                raise OSError("injected after tombstone children deletion")
            return original_rmtree(path, *args, **kwargs)

        with mock.patch.object(MODULE.shutil, "rmtree", side_effect=fail_after_all_children):
            with self.assertRaisesRegex(
                MODULE.ConfigureError, "cleanup incomplete; retry rollback"
            ):
                MODULE.rollback(self.home, self.systemd, "codex-app-server.service")

        self.assertTrue(cleanup.is_dir())
        self.assertEqual(list(cleanup.iterdir()), [])
        self.assertTrue(marker.is_file())
        self.assertEqual((self.home / "config.toml").read_text(encoding="utf-8"), original_config)
        self.assertEqual(dropin.read_text(encoding="utf-8"), original_dropin)
        self.assertEqual(catalog.read_text(encoding="utf-8"), original_catalog)
        self.assertEqual(unrelated.read_text(encoding="utf-8"), "keep")

        result = MODULE.rollback(self.home, self.systemd, "codex-app-server.service")
        self.assertEqual(result["status"], "ROLLED_BACK")
        self.assertFalse(cleanup.exists())
        self.assertFalse(marker.exists())
        self.assertEqual((self.home / "config.toml").read_text(encoding="utf-8"), original_config)
        self.assertEqual(dropin.read_text(encoding="utf-8"), original_dropin)
        self.assertEqual(catalog.read_text(encoding="utf-8"), original_catalog)
        self.assertEqual(unrelated.read_text(encoding="utf-8"), "keep")

    def test_rollback_retries_after_completion_marker_removal_failure(self):
        original_config = (self.home / "config.toml").read_text(encoding="utf-8")
        MODULE.configure(self.home, self.codex, self.systemd, "codex-app-server.service", None)
        unrelated = self.root / "unrelated-startup-state"
        unrelated.write_text("keep", encoding="utf-8")
        cleanup = self.home / MODULE.CLEANUP_DIR
        marker = self.home / MODULE.COMPLETION_MARKER

        def fail_marker_removal(_marker):
            raise OSError("injected completion marker removal failure")

        with mock.patch.object(
            MODULE, "_consume_completion_marker", side_effect=fail_marker_removal
        ):
            with self.assertRaisesRegex(
                MODULE.ConfigureError, "cleanup incomplete; retry rollback"
            ):
                MODULE.rollback(self.home, self.systemd, "codex-app-server.service")

        self.assertFalse(cleanup.exists())
        self.assertTrue(marker.is_file())
        self.assertEqual((self.home / "config.toml").read_text(encoding="utf-8"), original_config)
        self.assertEqual(unrelated.read_text(encoding="utf-8"), "keep")

        result = MODULE.rollback(self.home, self.systemd, "codex-app-server.service")
        self.assertEqual(result["status"], "ROLLED_BACK")
        self.assertFalse(cleanup.exists())
        self.assertFalse(marker.exists())
        self.assertEqual((self.home / "config.toml").read_text(encoding="utf-8"), original_config)
        self.assertEqual(unrelated.read_text(encoding="utf-8"), "keep")

    def test_atomic_write_keeps_utf8_lf_bytes_on_windows_boundary(self):
        target = self.root / "atomic-write.txt"
        with mock.patch.object(MODULE.sys, "platform", "win32"):
            with mock.patch.object(MODULE.os, "fdopen", wraps=MODULE.os.fdopen) as fdopen:
                MODULE._atomic_write(target, "first\nsecond\n", 0o600)
        self.assertEqual(target.read_bytes(), b"first\nsecond\n")
        self.assertEqual(fdopen.call_args.args[1], "wb")

    def test_windows_crlf_rollback_and_marker_retry_are_hash_stable(self):
        home = self._new_home("codex-home-windows-crlf")
        config = home / "config.toml"
        config.write_bytes(config.read_bytes().replace(b"\n", b"\r\n"))
        unrelated = self.root / "windows-unrelated-startup-state"
        unrelated.write_text("keep", encoding="utf-8")

        with mock.patch.object(MODULE.sys, "platform", "win32"):
            MODULE.configure(home, self.codex, None, "codex-app-server.service", None)
            result = MODULE.rollback(home, None, "codex-app-server.service")
        self.assertEqual(result["status"], "ROLLED_BACK")
        self.assertFalse((home / MODULE.STATE_DIR).exists())
        self.assertFalse((home / MODULE.COMPLETION_MARKER).exists())
        self.assertEqual(config.read_bytes(), config.read_bytes().replace(b"\r\n", b"\n"))
        self.assertEqual(unrelated.read_text(encoding="utf-8"), "keep")

        retry_home = self._new_home("codex-home-windows-crlf-marker")
        retry_config = retry_home / "config.toml"
        retry_config.write_bytes(retry_config.read_bytes().replace(b"\n", b"\r\n"))
        with mock.patch.object(MODULE.sys, "platform", "win32"):
            MODULE.configure(retry_home, self.codex, None, "codex-app-server.service", None)

            def fail_marker_once(marker):
                raise OSError("injected marker removal failure")

            with mock.patch.object(
                MODULE, "_consume_completion_marker", side_effect=fail_marker_once
            ):
                with self.assertRaisesRegex(
                    MODULE.ConfigureError, "cleanup incomplete; retry rollback"
                ):
                    MODULE.rollback(retry_home, None, "codex-app-server.service")
            self.assertTrue((retry_home / MODULE.COMPLETION_MARKER).is_file())
            self.assertFalse((retry_home / MODULE.STATE_DIR).exists())
            self.assertEqual(
                MODULE.rollback(retry_home, None, "codex-app-server.service")["status"],
                "ROLLED_BACK",
            )
            self.assertFalse((retry_home / MODULE.COMPLETION_MARKER).exists())

        drift_home = self._new_home("codex-home-windows-crlf-drift")
        drift_config = drift_home / "config.toml"
        drift_config.write_bytes(drift_config.read_bytes().replace(b"\n", b"\r\n"))
        with mock.patch.object(MODULE.sys, "platform", "win32"):
            MODULE.configure(drift_home, self.codex, None, "codex-app-server.service", None)
            with mock.patch.object(
                MODULE, "_consume_completion_marker", side_effect=OSError("marker fault")
            ):
                with self.assertRaises(MODULE.ConfigureError):
                    MODULE.rollback(drift_home, None, "codex-app-server.service")
            drift_config.write_bytes(drift_config.read_bytes() + b"# drift\n")
            with self.assertRaisesRegex(
                MODULE.ConfigureError, "config rollback target drifted after restore"
            ):
                MODULE.rollback(drift_home, None, "codex-app-server.service")

    def test_completed_cleanup_retry_rejects_config_drift(self):
        cleanup, marker, unrelated = self._leave_completed_cleanup_after_fault()
        config = self.home / "config.toml"
        config.write_text(config.read_text(encoding="utf-8") + "\n# drift\n", encoding="utf-8")

        with self.assertRaisesRegex(
            MODULE.ConfigureError, "config rollback target drifted after restore"
        ):
            MODULE.rollback(self.home, self.systemd, "codex-app-server.service")

        self.assertTrue(cleanup.is_dir())
        self.assertTrue(marker.is_file())
        self.assertIn("# drift", config.read_text(encoding="utf-8"))
        self.assertEqual(unrelated.read_text(encoding="utf-8"), "keep")

    def test_completed_cleanup_retry_rejects_dropin_content_drift(self):
        dropin = self.systemd / "codex-app-server.service.d" / MODULE.DROPIN_NAME
        dropin.parent.mkdir(parents=True)
        dropin.write_text("[Service]\nEnvironment=KEEP=1\n", encoding="utf-8")
        cleanup, marker, unrelated = self._leave_completed_cleanup_after_fault()
        dropin.write_text("[Service]\nEnvironment=DRIFT=1\n", encoding="utf-8")

        with self.assertRaisesRegex(
            MODULE.ConfigureError, "drop-in rollback target drifted after restore"
        ):
            MODULE.rollback(self.home, self.systemd, "codex-app-server.service")

        self.assertTrue(cleanup.is_dir())
        self.assertTrue(marker.is_file())
        self.assertIn("DRIFT=1", dropin.read_text(encoding="utf-8"))
        self.assertEqual(unrelated.read_text(encoding="utf-8"), "keep")

    def test_completed_cleanup_retry_rejects_dropin_that_reappears(self):
        cleanup, marker, unrelated = self._leave_completed_cleanup_after_fault()
        dropin = self.systemd / "codex-app-server.service.d" / MODULE.DROPIN_NAME
        dropin.parent.mkdir(parents=True, exist_ok=True)
        dropin.write_text("[Service]\nEnvironment=EXTRA=1\n", encoding="utf-8")

        with self.assertRaisesRegex(
            MODULE.ConfigureError, "drop-in rollback target drifted after restore"
        ):
            MODULE.rollback(self.home, self.systemd, "codex-app-server.service")

        self.assertTrue(cleanup.is_dir())
        self.assertTrue(marker.is_file())
        self.assertTrue(dropin.is_file())
        self.assertEqual(unrelated.read_text(encoding="utf-8"), "keep")

    def test_completed_cleanup_retry_rejects_catalog_content_drift(self):
        catalog = self.home / MODULE.CATALOG_RELATIVE
        catalog.parent.mkdir(parents=True, exist_ok=True)
        catalog.write_text('{"models": [{"slug": "before"}]}\n', encoding="utf-8")
        cleanup, marker, unrelated = self._leave_completed_cleanup_after_fault()
        catalog.write_text('{"models": [{"slug": "drift"}]}\n', encoding="utf-8")

        with self.assertRaisesRegex(
            MODULE.ConfigureError, "catalog rollback target drifted after restore"
        ):
            MODULE.rollback(self.home, self.systemd, "codex-app-server.service")

        self.assertTrue(cleanup.is_dir())
        self.assertTrue(marker.is_file())
        self.assertIn("drift", catalog.read_text(encoding="utf-8"))
        self.assertEqual(unrelated.read_text(encoding="utf-8"), "keep")

    def test_completed_cleanup_retry_rejects_catalog_that_reappears(self):
        cleanup, marker, unrelated = self._leave_completed_cleanup_after_fault()
        catalog = self.home / MODULE.CATALOG_RELATIVE
        catalog.parent.mkdir(parents=True, exist_ok=True)
        catalog.write_text('{"models": [{"slug": "extra"}]}\n', encoding="utf-8")

        with self.assertRaisesRegex(
            MODULE.ConfigureError, "catalog rollback target drifted after restore"
        ):
            MODULE.rollback(self.home, self.systemd, "codex-app-server.service")

        self.assertTrue(cleanup.is_dir())
        self.assertTrue(marker.is_file())
        self.assertTrue(catalog.is_file())
        self.assertEqual(unrelated.read_text(encoding="utf-8"), "keep")

    def test_invalid_completion_marker_fails_closed(self):
        MODULE.configure(self.home, self.codex, self.systemd, "codex-app-server.service", None)
        marker = self.home / MODULE.COMPLETION_MARKER
        config = self.home / "config.toml"
        configured = config.read_text(encoding="utf-8")
        marker.write_text("not-json", encoding="utf-8")
        with self.assertRaisesRegex(MODULE.ConfigureError, "completion marker is invalid"):
            MODULE.rollback(self.home, self.systemd, "codex-app-server.service")
        self.assertEqual(config.read_text(encoding="utf-8"), configured)
        self.assertTrue((self.home / MODULE.STATE_DIR).is_dir())

        marker.write_text(json.dumps({"schema": "foreign"}), encoding="utf-8")
        with self.assertRaisesRegex(
            MODULE.ConfigureError, "completion marker has an unsupported schema"
        ):
            MODULE.rollback(self.home, self.systemd, "codex-app-server.service")
        self.assertEqual(config.read_text(encoding="utf-8"), configured)
        self.assertTrue((self.home / MODULE.STATE_DIR).is_dir())


if __name__ == "__main__":
    unittest.main()

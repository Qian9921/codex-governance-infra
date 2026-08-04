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


if __name__ == "__main__":
    unittest.main()

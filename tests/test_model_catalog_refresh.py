import importlib.util
import json
import pathlib
import tempfile
import unittest


ROOT = pathlib.Path(__file__).parents[1]
MODULE_PATH = ROOT / "codex" / "bin" / "refresh-model-catalog.py"
SPEC = importlib.util.spec_from_file_location("refresh_model_catalog", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def catalog(*entries):
    return {"models": [dict(entry) for entry in entries]}


class ModelCatalogRefresh(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()

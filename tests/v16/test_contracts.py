import copy
import json
import pathlib
import unittest

from codex.v16.contracts import ContractError, canonical_sha256, validate_mission, validate_schema_document

ROOT = pathlib.Path(__file__).parents[2]
FIXTURES = ROOT / "codex" / "v16" / "fixtures"


class V16ContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mission = json.loads((FIXTURES / "mission.valid.json").read_text(encoding="utf-8"))

    def test_positive_known_answer_and_canonical_hash(self):
        result = validate_schema_document(self.mission, "mission.v16")
        self.assertEqual(result["schema"], "mission.v16")
        self.assertEqual(len(result["counterexamples"]), 3)
        self.assertEqual(canonical_sha256(result), canonical_sha256(result))

    def test_missing_and_extra_fields_red(self):
        for name in ("mission.invalid.missing.json", "mission.invalid.extra.json"):
            with self.subTest(name=name):
                value = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
                with self.assertRaises(ContractError):
                    validate_schema_document(value)

    def test_bool_is_not_integer(self):
        value = json.loads((FIXTURES / "mission.invalid.bool-int.json").read_text(encoding="utf-8"))
        with self.assertRaises(ContractError):
            validate_schema_document(value)

    def test_standalone_schema_discriminator_is_strict(self):
        standalone = {"schema": "counterexample.v16", "id": "CE-1", "semantics": "standalone", "description": "x", "entrypoint_id": "EP-1", "gate_id": "G-1", "why_red": "x", "cost": "tiny", "denominator": 1, "expected": "GREEN"}
        self.assertEqual(validate_schema_document(standalone)["schema"], "counterexample.v16")

    def test_cycle_and_linkage_red(self):
        value = json.loads((FIXTURES / "mission.invalid.cycle.json").read_text(encoding="utf-8"))
        with self.assertRaisesRegex(ContractError, "cyclic"):
            validate_schema_document(value)

    def test_duplicate_semantics_red(self):
        value = copy.deepcopy(self.mission)
        value["counterexamples"][1]["semantics"] = value["counterexamples"][0]["semantics"]
        with self.assertRaisesRegex(ContractError, "duplicate"):
            validate_mission(value)

    def test_uncovered_counterexample_red(self):
        value = copy.deepcopy(self.mission)
        value["counterexamples"].append({
            "id": "CE-UNCOVERED", "semantics": "uncovered", "description": "x",
            "entrypoint_id": "EP-UNIT", "gate_id": "G-TARGETED", "why_red": "x",
            "cost": "tiny", "denominator": 1, "expected": "RED",
        })
        with self.assertRaisesRegex(ContractError, "not covered"):
            validate_schema_document(value)

    def test_privacy_sensitive_id_red(self):
        value = copy.deepcopy(self.mission)
        value["mission_id"] = "prompt-id"
        with self.assertRaises(ContractError):
            validate_schema_document(value)


if __name__ == "__main__":
    unittest.main()

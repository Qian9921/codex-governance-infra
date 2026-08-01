import copy
import json
import pathlib
import unittest

from codex.v16.contracts import (
    ContractError,
    build_pre_execution_closure_authority,
    canonical_sha256,
    counterexample_sha256,
    validate_closure_binding_receipt,
    validate_mission,
    validate_pre_execution_closure_authority,
    validate_schema_document,
)

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

    def test_writer_model_is_task_selected_not_slug_banned(self):
        value = copy.deepcopy(self.mission)
        value["assigned_model"] = "gpt-5.6-terra"
        self.assertEqual(validate_mission(value)["assigned_model"], "gpt-5.6-terra")

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

    def test_counterexample_hash_allows_domain_words_but_rejects_private_artifacts(self):
        digest = counterexample_sha256(
            'The first token is "sh"; the dispatch transcript remains public.'
        )
        self.assertEqual(len(digest), 64)
        for value in (
            "/" + "home/alice/private/result.json",
            "/" + "Users/alice/private/result.json",
            "gh" + "p_12345678901234567890",
        ):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ContractError, "privacy-sensitive"):
                    counterexample_sha256(value)

    def test_zero_inner_audits_is_valid_for_low_risk_mission(self):
        value = copy.deepcopy(self.mission)
        value["spark_audits"] = []
        self.assertEqual(validate_mission(value)["spark_audits"], [])

    def test_closure_binding_receipt_is_strict_and_caller_bound(self):
        binding = {
            "finding_id": "P1-A",
            "counterexample_id": "CE-GATE",
            "executable_counterexample_id": "NF-001",
            "counterexample_sha256": "a" * 64,
            "gate_id": "G-TARGETED",
            "stage": "targeted",
            "evidence_row_id": "EVID-G-TARGETED-EP-UNIT",
            "entrypoint_id": "EP-UNIT",
            "binding_sha256": "",
        }
        binding["binding_sha256"] = canonical_sha256(binding)
        receipt = {
            "schema": "closure-binding-receipt.v16",
            "mission_id": "V16-PRODUCTIVITY",
            "compiled_plan_sha256": "b" * 64,
            "closure_plan_sha256": "c" * 64,
            "closure_plan_file_sha256": "d" * 64,
            "dispatch_transcript_file_sha256": "e" * 64,
            "normalized_source_artifacts": [
                {
                    "audit_id": "SPARK-A",
                    "artifact_path": "codex/v16/contracts/spark_result_A.v16.json",
                    "artifact_sha256": "f" * 64,
                },
            ],
            "finding_count": 1,
            "bindings": [binding],
            "receipt_sha256": "",
        }
        receipt["receipt_sha256"] = canonical_sha256(receipt)
        checked = validate_closure_binding_receipt(
            receipt,
            expected_compiled_plan_sha256=receipt["compiled_plan_sha256"],
            expected_closure_plan_sha256=receipt["closure_plan_sha256"],
            expected_receipt_sha256=receipt["receipt_sha256"],
        )
        self.assertEqual(checked["bindings"], [binding])

        wrong_plan = copy.deepcopy(receipt)
        wrong_plan["closure_plan_sha256"] = "0" * 64
        wrong_plan["receipt_sha256"] = ""
        wrong_plan["receipt_sha256"] = canonical_sha256(wrong_plan)
        with self.assertRaisesRegex(ContractError, "caller-bound"):
            validate_closure_binding_receipt(
                wrong_plan,
                expected_closure_plan_sha256=receipt["closure_plan_sha256"],
            )
        missing = copy.deepcopy(receipt)
        del missing["bindings"][0]["entrypoint_id"]
        with self.assertRaisesRegex(ContractError, "missing field"):
            validate_closure_binding_receipt(missing)

        authority = build_pre_execution_closure_authority(receipt)
        checked_authority = validate_pre_execution_closure_authority(
            authority,
            closure_binding_receipt=receipt,
            expected_authority_sha256=authority["authority_sha256"],
        )
        self.assertEqual(
            checked_authority["closure_binding_receipt_sha256"],
            receipt["receipt_sha256"],
        )
        self.assertEqual(
            checked_authority["bindings_sha256"],
            canonical_sha256(receipt["bindings"]),
        )

        partial_authority = copy.deepcopy(authority)
        del partial_authority["closure_plan_file_sha256"]
        with self.assertRaisesRegex(ContractError, "missing field"):
            validate_pre_execution_closure_authority(partial_authority)

        wrong_authority_hash = copy.deepcopy(authority)
        wrong_authority_hash["authority_sha256"] = "0" * 64
        with self.assertRaisesRegex(ContractError, "authority digest"):
            validate_pre_execution_closure_authority(wrong_authority_hash)

        resealed_stale_source = copy.deepcopy(authority)
        resealed_stale_source["normalized_source_artifacts"][0][
            "artifact_sha256"
        ] = "1" * 64
        resealed_stale_source["authority_sha256"] = ""
        resealed_stale_source["authority_sha256"] = canonical_sha256(
            resealed_stale_source
        )
        with self.assertRaisesRegex(ContractError, "authority/receipt mismatch"):
            validate_pre_execution_closure_authority(
                resealed_stale_source,
                closure_binding_receipt=receipt,
            )


if __name__ == "__main__":
    unittest.main()

import json
import pathlib
import unittest

from codex.v16.contracts import SCHEMA_REGISTRY
from codex.v16.tool_preflight import REPORT_FIELDS as PREFLIGHT_FIELDS
from codex.v16.tool_routing import HEALTH_FIELDS, ROUTE_FIELDS, USAGE_FIELDS


ROOT = pathlib.Path(__file__).parents[2]
REGISTRY_PATH = ROOT / "codex" / "v16" / "contracts" / "schema_registry.v16.json"


class SchemaRegistryExtensionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.document = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))

    def test_python_and_json_inventories_are_identical(self):
        self.assertEqual(self.document["schema"], "schema-registry.v16")
        self.assertFalse(self.document["additionalProperties"])
        self.assertEqual(self.document["schemas"], SCHEMA_REGISTRY)

    def test_policy_inventory_records_conditional_requirements(self):
        policy = SCHEMA_REGISTRY["review-policy.v16"]
        self.assertEqual(policy["required"], ["review_risk"])
        self.assertEqual(
            policy["conditional_required"],
            {"low_or_medium": ["reasons", "classifier_identity"], "high": ["high_risk_triggers"]},
        )
        self.assertEqual(policy["validation_mode"], "standalone")

    def test_review_packet_and_independent_artifact_inventory_is_exact(self):
        packet = SCHEMA_REGISTRY["review-packet.v16"]
        self.assertIn("decision_basis", packet["optional"])
        self.assertEqual(
            packet["decision_basis_required"],
            [
                "acceptance_envelope_sha256",
                "diff_sha256",
                "reviewed_dependency_scope_sha256",
                "evidence_bundle_sha256",
                "evidence_denominator",
                "review_risk",
                "reviewer_route",
                "reviewer_model",
                "reasoning_effort",
                "required_stages",
                "classifier_identity",
                "high_risk_triggers",
                "review_policy_sha256",
                "reference_identity_sha256",
                "operating_domain_sha256",
                "acceptance_thresholds_sha256",
                "invariants_sha256",
                "non_goals_sha256",
                "identity_mode",
                "snapshot_sha256",
                "prior_snapshot_sha256",
            ],
        )
        self.assertIn(
            "closure_authority",
            packet["decision_basis_optional"],
        )
        self.assertIn(
            "authority_sha256",
            packet["decision_basis_conditional_required"][
                "closure_authority"
            ],
        )
        artifact = SCHEMA_REGISTRY["independent-review.v16"]
        self.assertEqual(artifact["validation_mode"], "caller-bound")
        self.assertIn("escalation_evidence_ref", artifact["required"])
        self.assertIn("expected_review_packet_sha256", artifact["external_inputs"])
        self.assertIn("closure_binding_receipt", artifact["external_inputs"])
        self.assertIn(
            "expected_closure_binding_receipt_sha256",
            artifact["external_inputs"],
        )
        self.assertIn("expected_closure_plan_sha256", artifact["external_inputs"])
        self.assertIn("decision_basis", artifact["external_inputs"])
        self.assertIn(
            "pre-execution-closure-authority.v16",
            artifact["external_inputs"],
        )
        self.assertEqual(
            artifact["compatibility_only_inputs"],
            [
                "expected_closure_binding_receipt_sha256",
                "expected_closure_plan_sha256",
            ],
        )
        self.assertIn("dispatch_lineage", artifact["required"])

    def test_source_bound_entries_cannot_claim_standalone_validation(self):
        for schema in ("metrics.v16", "review-efficiency.v16"):
            with self.subTest(schema=schema):
                entry = SCHEMA_REGISTRY[schema]
                self.assertEqual(entry["validation_mode"], "source-bound")
                self.assertEqual(entry["external_inputs"], ["source_bundle"])

    def test_review_runtime_entries_are_caller_bound_and_exact(self):
        runtime = SCHEMA_REGISTRY["review-runtime.v16"]
        self.assertEqual(
            runtime["validator"], "review_runtime.validate_review_runtime"
        )
        self.assertEqual(runtime["validation_mode"], "caller-bound")
        self.assertEqual(
            runtime["external_inputs"],
            ["review-policy.v16", "runtime-expectations"],
        )
        self.assertIn("review_identity_sha256", runtime["required"])
        self.assertIn("prior_review_artifact_sha256", runtime["required"])
        self.assertIn("reviewer_continuity_id", runtime["required"])
        self.assertIn("soft_deadline_sec", runtime["required"])
        self.assertIn("hard_deadline_sec", runtime["required"])
        self.assertIn("max_tool_calls", runtime["required"])
        self.assertIn("duplicate_full_scope_reviews", runtime["required"])

        progress = SCHEMA_REGISTRY["review-runtime-progress.v16"]
        self.assertEqual(
            progress["validator"], "review_runtime.validate_review_progress"
        )
        self.assertEqual(progress["validation_mode"], "caller-bound")
        self.assertEqual(
            progress["external_inputs"],
            [
                "review-runtime.v16",
                "review-policy.v16",
                "runtime-expectations",
            ],
        )
        self.assertIn("approval_eligible", progress["required"])
        self.assertIn("context_chars", progress["required"])
        self.assertIn("review_calls", progress["required"])
        self.assertIn("duplicate_full_scope_reviews", progress["required"])

    def test_evidence_inventory_requires_canonical_snapshot_identity(self):
        evidence = SCHEMA_REGISTRY["evidence-envelope.v16"]
        self.assertIn("identity_mode", evidence["required"])
        self.assertIn("snapshot_sha256", evidence["required"])
        self.assertIn("expected_identity_mode", evidence["external_inputs"])
        self.assertIn("expected_snapshot_sha256", evidence["external_inputs"])
        self.assertIn("closure_binding_receipt_sha256", evidence["optional"])
        self.assertIn("closure_plan_sha256", evidence["optional"])
        self.assertIn("closure-binding-receipt.v16", evidence["external_inputs"])

        receipt = SCHEMA_REGISTRY["closure-binding-receipt.v16"]
        self.assertEqual(
            receipt["validator"],
            "contracts.validate_closure_binding_receipt",
        )
        self.assertEqual(receipt["validation_mode"], "caller-bound")
        self.assertIn("compiled_plan_sha256", receipt["required"])
        self.assertIn("closure_plan_file_sha256", receipt["required"])
        self.assertIn("receipt_sha256", receipt["required"])

        authority = SCHEMA_REGISTRY[
            "pre-execution-closure-authority.v16"
        ]
        self.assertEqual(
            authority["validator"],
            "contracts.validate_pre_execution_closure_authority",
        )
        self.assertEqual(authority["validation_mode"], "caller-bound")
        self.assertIn("bindings_sha256", authority["required"])
        self.assertIn("authority_sha256", authority["required"])

    def test_tool_routing_split_uses_public_validators_and_injected_observations(self):
        route = SCHEMA_REGISTRY["tool-route-decision.v16"]
        self.assertEqual(route["validator"], "tool_routing.validate_route_decision")
        self.assertEqual(route["validation_mode"], "standalone")
        self.assertEqual(route["observation_mode"], "injected-observation")
        self.assertEqual(route["injected_inputs"], ["observations"])
        self.assertEqual(set(route["required"]), set(ROUTE_FIELDS))

        health = SCHEMA_REGISTRY["tool-health.v16"]
        self.assertEqual(health["validator"], "tool_routing.validate_health_report")
        self.assertEqual(health["validation_mode"], "standalone")
        self.assertEqual(health["observation_mode"], "injected-observation")
        self.assertEqual(health["injected_inputs"], ["observations"])
        self.assertEqual(set(health["required"]), set(HEALTH_FIELDS))

        preflight = SCHEMA_REGISTRY["tool-preflight.v16"]
        self.assertEqual(preflight["validator"], "tool_preflight.validate_preflight")
        self.assertEqual(preflight["validation_mode"], "source-bound")
        self.assertEqual(set(preflight["required"]), set(PREFLIGHT_FIELDS))

        usage = SCHEMA_REGISTRY["tool-usage.v16"]
        self.assertEqual(usage["validator"], "tool_routing.validate_usage_report")
        self.assertEqual(usage["validation_mode"], "caller-bound")
        self.assertEqual(set(usage["required"]), set(USAGE_FIELDS))

    def test_readiness_inventory_carries_approval_and_policy_bindings(self):
        state = SCHEMA_REGISTRY["readiness-state.v16"]
        self.assertEqual(
            state["approval_required"],
            ["approved_review_artifact_sha256", "approved_review_packet_sha256"],
        )
        self.assertEqual(
            state["policy_binding"],
            [
                "required_stages",
                "reviewer_model",
                "reasoning_effort",
                "review_risk",
                "reviewer_route",
                "classifier_identity",
                "high_risk_triggers",
                "review_policy_sha256",
                "identity_mode",
                "snapshot_sha256",
                "prior_snapshot_sha256",
                "delta_sha256",
            ],
        )
        self.assertEqual(state["validation_mode"], "caller-bound")

    def test_registry_required_and_optional_fields_do_not_overlap(self):
        for schema, entry in SCHEMA_REGISTRY.items():
            with self.subTest(schema=schema):
                self.assertEqual(len(entry["required"]), len(set(entry["required"])))
                self.assertEqual(len(entry["optional"]), len(set(entry["optional"])))
                self.assertTrue(set(entry["required"]).isdisjoint(entry["optional"]))
                self.assertIn(entry["validation_mode"], {"standalone", "source-bound", "caller-bound"})


if __name__ == "__main__":
    unittest.main()

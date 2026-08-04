import copy
import json
import pathlib
import unittest

from codex.v16.compiler import CompileError, compile_mission
from codex.v16.contracts import ContractError, validate_mission
from codex.v16.review_policy import (
    context_mode_is_gating,
    resolve_review_policy,
    validate_review_policy,
)


ROOT = pathlib.Path(__file__).parents[2]
FIXTURE = ROOT / "codex/v16/fixtures/mission.valid.json"


class ReviewPolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mission = json.loads(FIXTURE.read_text(encoding="utf-8"))

    def test_legacy_missing_policy_falls_back_high(self):
        policy = resolve_review_policy(self.mission)
        self.assertEqual(policy["review_risk"], "high")
        self.assertTrue(policy["legacy_fallback"])
        self.assertEqual(policy["required_stages"], ["targeted", "full", "fresh"])
        self.assertEqual(policy["context_mode"], "independent_clean_room")
        self.assertEqual(policy["reviewer_model"], "gpt-5.6-sol")
        self.assertEqual(policy["reasoning_effort"], "xhigh")
        self.assertEqual(policy["fork_turns"], "none")
        self.assertTrue(policy["report_only"])

    def test_low_policy_and_targeted_only_compiler_route(self):
        mission = copy.deepcopy(self.mission)
        mission["spark_audits"] = []
        mission["review_policy"] = {
            "review_risk": "low",
            "reasons": ["documentation-only change"],
            "classifier_identity": "classifier-v1",
        }
        mission["reviewer_separation"]["independent_model"] = "gpt-5.6-sol"
        mission["gates"] = [mission["gates"][0]]
        for item in mission["counterexamples"]:
            item["gate_id"] = "G-TARGETED"
        for item in mission["acceptance"]:
            item["gate_id"] = "G-TARGETED"
        normalized = validate_mission(mission)
        self.assertEqual(normalized["review_policy"]["review_risk"], "low")
        plan = compile_mission(mission)
        self.assertEqual(plan["review_policy"]["required_stages"], ["targeted"])
        self.assertEqual(plan["gate_order"], ["G-TARGETED"])

    def test_medium_requires_targeted_and_full(self):
        mission = copy.deepcopy(self.mission)
        mission["spark_audits"] = []
        mission["review_policy"] = {
            "review_risk": "medium",
            "reasons": ["bounded internal behavior change"],
            "classifier": "classifier-v1",
        }
        mission["reviewer_separation"]["independent_model"] = "gpt-5.6-sol"
        mission["gates"] = mission["gates"][:2]
        plan = compile_mission(mission)
        self.assertEqual(plan["review_policy"]["required_stages"], ["targeted", "full"])
        self.assertEqual(plan["gate_order"], ["G-TARGETED", "G-FULL"])

    def test_high_requires_trigger_and_uses_sol(self):
        policy = validate_review_policy(
            {
                "review_risk": "high",
                "high_risk_triggers": ["exact_parity"],
                "required_stages": ["targeted"],
            }
        )
        self.assertEqual(policy["reviewer_model"], "gpt-5.6-sol")
        self.assertEqual(policy["reasoning_effort"], "xhigh")
        self.assertEqual(policy["required_stages"], ["targeted"])

    def test_explicit_stage_prefix_is_orthogonal_to_risk(self):
        low_all = validate_review_policy(
            {
                "review_risk": "low",
                "reasons": ["broad but non-sensitive review"],
                "classifier_identity": "classifier-v1",
                "required_stages": ["targeted", "full", "fresh"],
            }
        )
        self.assertEqual(low_all["required_stages"], ["targeted", "full", "fresh"])
        self.assertEqual(low_all["reviewer_model"], "gpt-5.6-sol")

    def test_malformed_stage_routes_rejected(self):
        base = {"review_risk": "high", "high_risk_triggers": ["security"]}
        for stages in ([], ["full"], ["targeted", "fresh"], ["targeted", "targeted"], ["targeted", "unknown"]):
            with self.subTest(stages=stages):
                with self.assertRaises(Exception):
                    validate_review_policy({**base, "required_stages": stages})

    def test_underclassification_and_unknown_trigger_rejected(self):
        with self.assertRaises(ContractError):
            validate_mission(
                {**self.mission, "review_policy": {
                    "review_risk": "low",
                    "reasons": ["too optimistic"],
                    "classifier_identity": "classifier-v1",
                    "high_risk_triggers": ["security"],
                }}
            )
        with self.assertRaises(ContractError):
            validate_mission(
                {**self.mission, "review_policy": {
                    "review_risk": "high",
                    "high_risk_triggers": ["not-a-trigger"],
                }}
            )

    def test_invalid_explicit_policy_resolves_fail_closed(self):
        policy = resolve_review_policy({"review_policy": {"review_risk": "medium"}})
        self.assertEqual(policy["review_risk"], "high")
        self.assertEqual(policy["resolution"], "invalid")
        self.assertEqual(policy["required_stages"], ["targeted", "full", "fresh"])

    def test_author_contextual_is_never_a_gate(self):
        self.assertFalse(context_mode_is_gating("author_contextual"))
        self.assertTrue(context_mode_is_gating("independent_clean_room"))
        with self.assertRaises(Exception):
            context_mode_is_gating("unknown")

    def test_non_initial_context_modes_rejected_in_mission_policy(self):
        for mode in ("author_contextual", "delta_continuation", "escalated_fresh"):
            with self.subTest(mode=mode):
                policy = {
                    "review_risk": "high",
                    "high_risk_triggers": ["security"],
                    "context_mode": mode,
                }
                with self.assertRaises(ContractError):
                    validate_mission({**self.mission, "review_policy": policy})

    def test_compiler_rejects_missing_required_stage(self):
        mission = copy.deepcopy(self.mission)
        mission["review_policy"] = {
            "review_risk": "high",
            "high_risk_triggers": ["production_runtime"],
        }
        mission["gates"] = mission["gates"][:2]
        with self.assertRaises(CompileError):
            compile_mission(mission)

    def test_compiler_honors_high_targeted_prefix_when_all_blockers_are_affected(self):
        mission = copy.deepcopy(self.mission)
        mission["review_policy"] = {
            "review_risk": "high",
            "high_risk_triggers": ["exact_parity"],
            "required_stages": ["targeted"],
        }
        mission["gates"] = [mission["gates"][0]]
        for item in mission["counterexamples"]:
            item["gate_id"] = "G-TARGETED"
        for item in mission["acceptance"]:
            item["gate_id"] = "G-TARGETED"
        plan = compile_mission(mission)
        self.assertEqual(plan["review_policy"]["required_stages"], ["targeted"])

    def test_compiler_rejects_blocker_outside_explicit_route(self):
        mission = copy.deepcopy(self.mission)
        mission["review_policy"] = {
            "review_risk": "high",
            "high_risk_triggers": ["exact_parity"],
            "required_stages": ["targeted"],
        }
        with self.assertRaises(CompileError):
            compile_mission(mission)

    def test_compiler_honors_low_all_prefix(self):
        mission = copy.deepcopy(self.mission)
        mission["review_policy"] = {
            "review_risk": "low",
            "reasons": ["broad internal review"],
            "classifier_identity": "classifier-v1",
            "required_stages": ["targeted", "full", "fresh"],
        }
        mission["reviewer_separation"]["independent_model"] = "gpt-5.6-sol"
        plan = compile_mission(mission)
        self.assertEqual(plan["review_policy"]["required_stages"], ["targeted", "full", "fresh"])


if __name__ == "__main__":
    unittest.main()

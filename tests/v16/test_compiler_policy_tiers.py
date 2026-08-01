from __future__ import annotations

import unittest

from codex.v16.compiler import CompileError, gate_ids_for_tier


def plan_for(risk: str, *, stages: tuple[str, ...] | None = None) -> dict:
    """Build a small ordered gate plan with the requested policy route."""
    route = {
        "low": ("targeted",),
        "medium": ("targeted", "full"),
        "high": ("targeted", "full", "fresh"),
    }[risk]
    present = route if stages is None else stages
    gates = []
    previous = None
    for stage in present:
        gate_id = f"G-{stage.upper()}"
        gate = {"id": gate_id, "stage": stage, "depends_on": [] if previous is None else [previous]}
        gates.append(gate)
        previous = gate_id
    return {
        "schema": "compiled-plan.v16",
        "gate_order": [gate["id"] for gate in gates],
        "gates": gates,
        "review_policy": {"review_risk": risk, "required_stages": list(route)},
    }


class CompilerPolicyTierTests(unittest.TestCase):
    def test_low_policy_never_runs_full_or_fresh(self):
        plan = plan_for("low")
        self.assertEqual(gate_ids_for_tier(plan, "FAST"), ["G-TARGETED"])
        self.assertEqual(gate_ids_for_tier(plan, "CANDIDATE"), ["G-TARGETED"])
        self.assertEqual(gate_ids_for_tier(plan, "FINAL"), ["G-TARGETED"])

    def test_medium_policy_never_runs_fresh(self):
        plan = plan_for("medium")
        self.assertEqual(gate_ids_for_tier(plan, "FAST"), ["G-TARGETED"])
        self.assertEqual(gate_ids_for_tier(plan, "CANDIDATE"), ["G-TARGETED", "G-FULL"])
        self.assertEqual(gate_ids_for_tier(plan, "FINAL"), ["G-TARGETED", "G-FULL"])

    def test_high_policy_is_staged_and_legacy_route_remains_full(self):
        plan = plan_for("high")
        self.assertEqual(gate_ids_for_tier(plan, "FAST"), ["G-TARGETED"])
        self.assertEqual(gate_ids_for_tier(plan, "CANDIDATE"), ["G-TARGETED", "G-FULL"])
        self.assertEqual(gate_ids_for_tier(plan, "FINAL"), ["G-TARGETED", "G-FULL", "G-FRESH"])

        legacy = plan_for("high")
        legacy["review_policy"]["legacy_fallback"] = True
        self.assertEqual(gate_ids_for_tier(legacy, "FINAL"), ["G-TARGETED", "G-FULL", "G-FRESH"])

    def test_malformed_policy_stage_set_fails_closed(self):
        for malformed in (
            {"review_risk": "medium"},
            {"review_risk": "medium", "required_stages": "targeted,full"},
            {"review_risk": "medium", "required_stages": ["targeted", "fresh"]},
            {"review_risk": "unknown", "required_stages": ["targeted"]},
        ):
            with self.subTest(policy=malformed):
                plan = plan_for("medium")
                plan["review_policy"] = malformed
                with self.assertRaises(CompileError):
                    gate_ids_for_tier(plan, "FINAL")

        missing = plan_for("medium")
        del missing["review_policy"]
        with self.assertRaises(CompileError):
            gate_ids_for_tier(missing, "FAST")

    def test_explicit_frozen_stages_are_not_rederived_from_risk(self):
        plan = plan_for("high", stages=("targeted",))
        plan["review_policy"]["required_stages"] = ["targeted"]
        self.assertEqual(gate_ids_for_tier(plan, "FINAL"), ["G-TARGETED"])

    def test_missing_required_gate_stage_fails_closed(self):
        medium = plan_for("medium", stages=("targeted",))
        with self.assertRaises(CompileError):
            gate_ids_for_tier(medium, "CANDIDATE")

        high = plan_for("high", stages=("targeted", "full"))
        with self.assertRaises(CompileError):
            gate_ids_for_tier(high, "FINAL")

    def test_selected_gates_keep_order_and_dependencies(self):
        plan = plan_for("high")
        self.assertEqual(gate_ids_for_tier(plan, "CANDIDATE"), plan["gate_order"][:2])

        broken = plan_for("medium")
        broken["gates"][1]["depends_on"] = ["G-MISSING"]
        with self.assertRaises(CompileError):
            gate_ids_for_tier(broken, "CANDIDATE")


if __name__ == "__main__":
    unittest.main()

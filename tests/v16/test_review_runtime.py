import copy
import hashlib
import json
import unittest

from codex.v16.review_runtime import (
    ReviewRuntimeError,
    compile_review_runtime,
    review_progress_decision,
    validate_review_progress,
    validate_review_runtime,
)


HIGH_POLICY = {
    "review_risk": "high",
    "high_risk_triggers": ["hook_reviewer_model_routing"],
}
MEDIUM_POLICY = {
    "review_risk": "medium",
    "reasons": ["bounded internal change"],
    "classifier_identity": "classifier-v1",
}


class ReviewRuntimeTests(unittest.TestCase):
    def test_initial_high_risk_stays_fresh_sol_xhigh(self):
        runtime = compile_review_runtime(
            HIGH_POLICY,
            context_mode="independent_clean_room",
            changed_files=20,
            changed_lines=4000,
        )
        self.assertEqual(runtime["reviewer_model"], "gpt-5.6-sol")
        self.assertEqual(runtime["reasoning_effort"], "xhigh")
        self.assertTrue(runtime["fresh_reviewer"])
        self.assertFalse(runtime["delta_only"])
        self.assertEqual(
            validate_review_runtime(runtime, policy_or_mission=HIGH_POLICY), runtime
        )

    def test_medium_initial_uses_terra_high(self):
        runtime = compile_review_runtime(
            MEDIUM_POLICY,
            context_mode="independent_clean_room",
            changed_files=4,
            changed_lines=100,
        )
        self.assertEqual(
            (runtime["reviewer_model"], runtime["reasoning_effort"]),
            ("gpt-5.6-terra", "high"),
        )

    def test_small_high_risk_delta_reuses_sol_at_high_effort(self):
        runtime = compile_review_runtime(
            HIGH_POLICY,
            context_mode="delta_continuation",
            changed_files=5,
            changed_lines=81,
            prior_coverage_status="COMPLETE",
            prior_unreviewed_count=0,
            same_reviewer_available=True,
        )
        self.assertEqual(runtime["reviewer_model"], "gpt-5.6-sol")
        self.assertEqual(runtime["reasoning_effort"], "high")
        self.assertFalse(runtime["fresh_reviewer"])
        self.assertTrue(runtime["reuse_prior_reviewer"])
        self.assertTrue(runtime["delta_only"])
        self.assertEqual(runtime["soft_deadline_sec"], 90)
        self.assertEqual(runtime["hard_deadline_sec"], 240)
        self.assertEqual(runtime["max_review_calls"], 1)
        self.assertEqual(runtime["duplicate_full_scope_reviews"], 0)

    def test_delta_requires_complete_prior_and_same_reviewer(self):
        kwargs = {
            "context_mode": "delta_continuation",
            "changed_files": 1,
            "changed_lines": 1,
            "prior_coverage_status": "PARTIAL",
            "same_reviewer_available": True,
        }
        with self.assertRaisesRegex(ReviewRuntimeError, "COMPLETE prior coverage"):
            compile_review_runtime(HIGH_POLICY, **kwargs)
        kwargs["prior_coverage_status"] = "COMPLETE"
        kwargs["same_reviewer_available"] = False
        with self.assertRaisesRegex(ReviewRuntimeError, "reviewer continuity"):
            compile_review_runtime(HIGH_POLICY, **kwargs)

    def test_delta_contract_drift_or_large_change_escalates(self):
        base = {
            "context_mode": "delta_continuation",
            "changed_files": 1,
            "changed_lines": 1,
            "prior_coverage_status": "COMPLETE",
            "same_reviewer_available": True,
        }
        with self.assertRaisesRegex(ReviewRuntimeError, "escalated_fresh"):
            compile_review_runtime(HIGH_POLICY, contract_drift=True, **base)
        large = {**base, "changed_files": 13}
        with self.assertRaisesRegex(ReviewRuntimeError, "escalated_fresh"):
            compile_review_runtime(HIGH_POLICY, **large)

    def test_escalated_fresh_requires_trigger_and_keeps_xhigh(self):
        with self.assertRaisesRegex(ReviewRuntimeError, "named trigger"):
            compile_review_runtime(
                HIGH_POLICY,
                context_mode="escalated_fresh",
                changed_files=2,
                changed_lines=20,
            )
        runtime = compile_review_runtime(
            HIGH_POLICY,
            context_mode="escalated_fresh",
            changed_files=2,
            changed_lines=20,
            contract_drift=True,
            escalation_triggers=["REVIEW_RUNTIME_GOVERNANCE_CHANGE"],
        )
        self.assertTrue(runtime["fresh_reviewer"])
        self.assertEqual(runtime["reasoning_effort"], "xhigh")

    def test_progress_requests_report_then_interrupts(self):
        runtime = compile_review_runtime(
            HIGH_POLICY,
            context_mode="delta_continuation",
            changed_files=5,
            changed_lines=81,
            prior_coverage_status="COMPLETE",
            same_reviewer_available=True,
        )
        soft = review_progress_decision(
            runtime,
            elapsed_sec=90,
            tool_calls=4,
            files_read=5,
            verdict_present=False,
            coverage_complete=False,
            unreviewed_count=1,
        )
        self.assertEqual(soft["action"], "REQUEST_REPORT")
        hard = review_progress_decision(
            runtime,
            elapsed_sec=240,
            tool_calls=4,
            files_read=5,
            verdict_present=False,
            coverage_complete=False,
            unreviewed_count=1,
        )
        self.assertEqual(hard["action"], "INTERRUPT_REPLAN")
        self.assertFalse(hard["approval_eligible"])

    def test_progress_never_approves_partial_or_over_budget(self):
        runtime = compile_review_runtime(
            MEDIUM_POLICY,
            context_mode="independent_clean_room",
            changed_files=4,
            changed_lines=100,
        )
        partial = review_progress_decision(
            runtime,
            elapsed_sec=10,
            tool_calls=2,
            files_read=4,
            verdict_present=True,
            coverage_complete=False,
            unreviewed_count=1,
        )
        self.assertEqual(partial["action"], "RETURN_PARTIAL")
        self.assertFalse(partial["approval_eligible"])
        exceeded = review_progress_decision(
            runtime,
            elapsed_sec=10,
            tool_calls=runtime["max_tool_calls"] + 1,
            files_read=4,
            verdict_present=True,
            coverage_complete=True,
            unreviewed_count=0,
        )
        self.assertEqual(exceeded["action"], "INTERRUPT_REPLAN")
        self.assertFalse(exceeded["approval_eligible"])

    def test_complete_report_is_runtime_eligible_not_auto_approved(self):
        runtime = compile_review_runtime(
            MEDIUM_POLICY,
            context_mode="independent_clean_room",
            changed_files=4,
            changed_lines=100,
        )
        decision = review_progress_decision(
            runtime,
            elapsed_sec=10,
            tool_calls=2,
            files_read=4,
            verdict_present=True,
            coverage_complete=True,
            unreviewed_count=0,
        )
        self.assertEqual(decision["action"], "ACCEPT_REPORT")
        self.assertTrue(decision["approval_eligible"])
        self.assertEqual(
            validate_review_progress(decision, runtime=runtime), decision
        )
        forged = {**decision, "approval_eligible": False}
        with self.assertRaisesRegex(ReviewRuntimeError, "does not match"):
            validate_review_progress(forged, runtime=runtime)

    def test_delta_new_evidence_escalates_and_scope_roam_stops(self):
        runtime = compile_review_runtime(
            HIGH_POLICY,
            context_mode="delta_continuation",
            changed_files=2,
            changed_lines=20,
            prior_coverage_status="COMPLETE",
            same_reviewer_available=True,
        )
        escalated = review_progress_decision(
            runtime,
            elapsed_sec=5,
            tool_calls=1,
            files_read=2,
            verdict_present=False,
            coverage_complete=False,
            unreviewed_count=0,
            scope_expansion_requested=True,
            new_falsifiable_evidence=True,
        )
        self.assertEqual(escalated["action"], "ESCALATE_FRESH")
        stopped = review_progress_decision(
            runtime,
            elapsed_sec=5,
            tool_calls=1,
            files_read=2,
            verdict_present=False,
            coverage_complete=False,
            unreviewed_count=0,
            scope_expansion_requested=True,
            new_falsifiable_evidence=False,
        )
        self.assertEqual(stopped["action"], "STOP_SCOPE_EXPANSION")

    def test_hash_and_bool_forgery_are_rejected(self):
        runtime = compile_review_runtime(
            MEDIUM_POLICY,
            context_mode="independent_clean_room",
            changed_files=1,
            changed_lines=1,
        )
        forged = copy.deepcopy(runtime)
        forged["soft_deadline_sec"] = 1
        with self.assertRaisesRegex(ReviewRuntimeError, "profile mismatch|hash mismatch"):
            validate_review_runtime(forged)
        with self.assertRaisesRegex(ReviewRuntimeError, "does not match review policy"):
            validate_review_runtime(runtime, policy_or_mission=HIGH_POLICY)
        with self.assertRaisesRegex(ReviewRuntimeError, "integer"):
            compile_review_runtime(
                MEDIUM_POLICY,
                context_mode="independent_clean_room",
                changed_files=True,
                changed_lines=1,
            )

    def test_rehashed_semantic_weakening_is_rejected(self):
        runtime = compile_review_runtime(
            MEDIUM_POLICY,
            context_mode="independent_clean_room",
            changed_files=1,
            changed_lines=1,
        )
        for field, value, message in (
            ("timeout_action", "approve-on-timeout", "cannot be weakened"),
            ("max_tool_calls", 999, "profile mismatch"),
            ("scope_expansion_policy", "unbounded", "does not match"),
        ):
            forged = {**runtime, field: value, "contract_sha256": ""}
            forged["contract_sha256"] = hashlib.sha256(
                json.dumps(
                    forged,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()
            with self.assertRaisesRegex(ReviewRuntimeError, message):
                validate_review_runtime(forged)

    def test_hard_budget_preempts_scope_or_escalation_actions(self):
        runtime = compile_review_runtime(
            HIGH_POLICY,
            context_mode="delta_continuation",
            changed_files=1,
            changed_lines=1,
            prior_coverage_status="COMPLETE",
            same_reviewer_available=True,
        )
        decision = review_progress_decision(
            runtime,
            elapsed_sec=runtime["hard_deadline_sec"],
            tool_calls=1,
            files_read=1,
            verdict_present=False,
            coverage_complete=False,
            unreviewed_count=1,
            scope_expansion_requested=True,
            new_falsifiable_evidence=True,
        )
        self.assertEqual(decision["action"], "INTERRUPT_REPLAN")
        self.assertFalse(decision["approval_eligible"])


if __name__ == "__main__":
    unittest.main()

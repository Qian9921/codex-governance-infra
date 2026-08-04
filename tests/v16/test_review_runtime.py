import copy
import hashlib
import json
import unittest

from codex.v16.review_runtime import (
    ReviewRuntimeError,
    compile_review_runtime as _compile_review_runtime,
    review_progress_decision as _review_progress_decision,
    validate_review_progress as _validate_review_progress,
    validate_review_runtime as _validate_review_runtime,
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
REVIEW_IDENTITY = "1" * 64
PRIOR_REVIEW_ARTIFACT = "2" * 64
REVIEWER_CONTINUITY = "3" * 64


def _expectations(runtime):
    return {
        "context_mode": runtime["context_mode"],
        "changed_files": runtime["changed_files"],
        "changed_lines": runtime["changed_lines"],
        "review_identity_sha256": runtime["review_identity_sha256"],
        "prior_review_artifact_sha256": runtime[
            "prior_review_artifact_sha256"
        ],
        "reviewer_continuity_id": runtime["reviewer_continuity_id"],
    }


def compile_review_runtime(policy, **kwargs):
    mode = kwargs["context_mode"]
    kwargs.setdefault("review_identity_sha256", REVIEW_IDENTITY)
    if mode in {"delta_continuation", "escalated_fresh"}:
        kwargs.setdefault(
            "prior_review_artifact_sha256", PRIOR_REVIEW_ARTIFACT
        )
    if mode == "delta_continuation":
        kwargs.setdefault("reviewer_continuity_id", REVIEWER_CONTINUITY)
    return _compile_review_runtime(policy, **kwargs)


def validate_review_runtime(runtime, *, policy_or_mission):
    return _validate_review_runtime(
        runtime,
        policy_or_mission=policy_or_mission,
        expectations=_expectations(runtime),
    )


def review_progress_decision(runtime, *, policy_or_mission, **kwargs):
    return _review_progress_decision(
        runtime,
        policy_or_mission=policy_or_mission,
        runtime_expectations=_expectations(runtime),
        **kwargs,
    )


def validate_review_progress(
    value, *, runtime, policy_or_mission
):
    return _validate_review_progress(
        value,
        runtime=runtime,
        policy_or_mission=policy_or_mission,
        runtime_expectations=_expectations(runtime),
    )


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
            ("gpt-5.6-sol", "high"),
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

    def test_medium_delta_preserves_terra_at_high_effort(self):
        runtime = compile_review_runtime(
            MEDIUM_POLICY,
            context_mode="delta_continuation",
            changed_files=2,
            changed_lines=20,
            prior_coverage_status="COMPLETE",
            prior_unreviewed_count=0,
            same_reviewer_available=True,
        )
        self.assertEqual(
            (runtime["reviewer_model"], runtime["reasoning_effort"]),
            ("gpt-5.6-sol", "high"),
        )
        self.assertTrue(runtime["reuse_prior_reviewer"])

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
            policy_or_mission=HIGH_POLICY,
            elapsed_sec=90,
            tool_calls=4,
            files_read=5,
            context_chars=4000,
            review_calls=1,
            duplicate_full_scope_reviews=0,
            verdict_present=False,
            coverage_complete=False,
            unreviewed_count=1,
        )
        self.assertEqual(soft["action"], "REQUEST_REPORT")
        hard = review_progress_decision(
            runtime,
            policy_or_mission=HIGH_POLICY,
            elapsed_sec=240,
            tool_calls=4,
            files_read=5,
            context_chars=4000,
            review_calls=1,
            duplicate_full_scope_reviews=0,
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
            policy_or_mission=MEDIUM_POLICY,
            elapsed_sec=10,
            tool_calls=2,
            files_read=4,
            context_chars=2000,
            review_calls=1,
            duplicate_full_scope_reviews=0,
            verdict_present=True,
            coverage_complete=False,
            unreviewed_count=1,
        )
        self.assertEqual(partial["action"], "RETURN_PARTIAL")
        self.assertFalse(partial["approval_eligible"])
        exceeded = review_progress_decision(
            runtime,
            policy_or_mission=MEDIUM_POLICY,
            elapsed_sec=10,
            tool_calls=runtime["max_tool_calls"] + 1,
            files_read=4,
            context_chars=2000,
            review_calls=1,
            duplicate_full_scope_reviews=0,
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
            policy_or_mission=MEDIUM_POLICY,
            elapsed_sec=10,
            tool_calls=2,
            files_read=4,
            context_chars=2000,
            review_calls=1,
            duplicate_full_scope_reviews=0,
            verdict_present=True,
            coverage_complete=True,
            unreviewed_count=0,
        )
        self.assertEqual(decision["action"], "ACCEPT_REPORT")
        self.assertTrue(decision["approval_eligible"])
        self.assertEqual(
            validate_review_progress(
                decision, runtime=runtime, policy_or_mission=MEDIUM_POLICY
            ),
            decision,
        )
        forged = {**decision, "approval_eligible": False}
        with self.assertRaisesRegex(ReviewRuntimeError, "does not match"):
            validate_review_progress(
                forged, runtime=runtime, policy_or_mission=MEDIUM_POLICY
            )

    def test_complete_report_requires_exactly_one_formal_review_call(self):
        runtime = compile_review_runtime(
            MEDIUM_POLICY,
            context_mode="independent_clean_room",
            changed_files=4,
            changed_lines=100,
        )
        decision = review_progress_decision(
            runtime,
            policy_or_mission=MEDIUM_POLICY,
            elapsed_sec=10,
            tool_calls=2,
            files_read=4,
            context_chars=2000,
            review_calls=0,
            duplicate_full_scope_reviews=0,
            verdict_present=True,
            coverage_complete=True,
            unreviewed_count=0,
        )
        self.assertEqual(decision["action"], "INTERRUPT_REPLAN")
        self.assertTrue(decision["budget_exceeded"])
        self.assertFalse(decision["approval_eligible"])

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
            policy_or_mission=HIGH_POLICY,
            elapsed_sec=5,
            tool_calls=1,
            files_read=2,
            context_chars=1000,
            review_calls=1,
            duplicate_full_scope_reviews=0,
            verdict_present=False,
            coverage_complete=False,
            unreviewed_count=0,
            scope_expansion_requested=True,
            new_falsifiable_evidence=True,
        )
        self.assertEqual(escalated["action"], "ESCALATE_FRESH")
        stopped = review_progress_decision(
            runtime,
            policy_or_mission=HIGH_POLICY,
            elapsed_sec=5,
            tool_calls=1,
            files_read=2,
            context_chars=1000,
            review_calls=1,
            duplicate_full_scope_reviews=0,
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
            validate_review_runtime(forged, policy_or_mission=MEDIUM_POLICY)
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
                validate_review_runtime(
                    forged, policy_or_mission=MEDIUM_POLICY
                )

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
            policy_or_mission=HIGH_POLICY,
            elapsed_sec=runtime["hard_deadline_sec"],
            tool_calls=1,
            files_read=1,
            context_chars=1000,
            review_calls=1,
            duplicate_full_scope_reviews=0,
            verdict_present=False,
            coverage_complete=False,
            unreviewed_count=1,
            scope_expansion_requested=True,
            new_falsifiable_evidence=True,
        )
        self.assertEqual(decision["action"], "INTERRUPT_REPLAN")
        self.assertFalse(decision["approval_eligible"])

    def test_progress_rejects_coherent_route_rewrite_against_frozen_policy(self):
        forged_route = compile_review_runtime(
            MEDIUM_POLICY,
            context_mode="independent_clean_room",
            changed_files=1,
            changed_lines=1,
        )
        with self.assertRaisesRegex(
            ReviewRuntimeError, "does not match review policy"
        ):
            review_progress_decision(
                forged_route,
                policy_or_mission=HIGH_POLICY,
                elapsed_sec=1,
                tool_calls=1,
                files_read=1,
                context_chars=100,
                review_calls=1,
                duplicate_full_scope_reviews=0,
                verdict_present=True,
                coverage_complete=True,
                unreviewed_count=0,
            )

    def test_progress_rejects_underreported_delta_and_identity(self):
        runtime = compile_review_runtime(
            HIGH_POLICY,
            context_mode="delta_continuation",
            changed_files=1,
            changed_lines=1,
            prior_coverage_status="COMPLETE",
            same_reviewer_available=True,
        )
        base_observations = {
            "policy_or_mission": HIGH_POLICY,
            "elapsed_sec": 1,
            "tool_calls": 1,
            "files_read": 1,
            "context_chars": 100,
            "review_calls": 1,
            "duplicate_full_scope_reviews": 0,
            "verdict_present": True,
            "coverage_complete": True,
            "unreviewed_count": 0,
        }
        for changed_expectation in (
            {"changed_files": 13},
            {"changed_lines": 801},
            {"review_identity_sha256": "4" * 64},
            {"prior_review_artifact_sha256": "5" * 64},
            {"reviewer_continuity_id": "6" * 64},
        ):
            expectations = {**_expectations(runtime), **changed_expectation}
            with self.assertRaisesRegex(
                ReviewRuntimeError, "identity/delta expectations"
            ):
                _review_progress_decision(
                    runtime,
                    runtime_expectations=expectations,
                    **base_observations,
                )

    def test_context_and_review_call_budgets_are_observed(self):
        runtime = compile_review_runtime(
            MEDIUM_POLICY,
            context_mode="independent_clean_room",
            changed_files=1,
            changed_lines=1,
        )
        for overrides in (
            {"context_chars": runtime["max_context_chars"] + 1},
            {"review_calls": runtime["max_review_calls"] + 1},
            {"duplicate_full_scope_reviews": 1},
        ):
            observations = {
                "policy_or_mission": MEDIUM_POLICY,
                "elapsed_sec": 1,
                "tool_calls": 1,
                "files_read": 1,
                "context_chars": 100,
                "review_calls": 1,
                "duplicate_full_scope_reviews": 0,
                "verdict_present": True,
                "coverage_complete": True,
                "unreviewed_count": 0,
                **overrides,
            }
            decision = review_progress_decision(runtime, **observations)
            self.assertEqual(decision["action"], "INTERRUPT_REPLAN")
            self.assertTrue(decision["budget_exceeded"])
            self.assertFalse(decision["approval_eligible"])


if __name__ == "__main__":
    unittest.main()

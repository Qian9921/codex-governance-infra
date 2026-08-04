import copy
import json
import pathlib
import tempfile
import unittest

from codex.v16.compiler import CompileError, compile_mission
from codex.v16.contracts import ContractError, canonical_sha256
from codex.v16.state import ReadinessError, StateStore, initial_state, transition, validate_state
from codex.v16.trace import render_pr_trace

ROOT = pathlib.Path(__file__).parents[2]
FIXTURE = ROOT / "codex/v16/fixtures/mission.valid.json"
BASE = "e18439c8dfe01d901895efd09b8b73b6842327a9"
TREE = "1de79a7c48e6c66f167be54ca9cf387310149f80"
CANDIDATE = "0123456789abcdef0123456789abcdef01234567"


class CompilerStateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mission = json.loads(FIXTURE.read_text(encoding="utf-8"))

    def test_compiled_plan_is_deterministic_and_nonexecuting(self):
        plan = compile_mission(self.mission)
        self.assertEqual(plan["schema"], "compiled-plan.v16")
        self.assertEqual(plan["gate_order"], ["G-TARGETED", "G-FULL", "G-FRESH"])
        self.assertFalse(plan["execution"]["shell"])

    def test_stage_jump_rejected(self):
        mission = copy.deepcopy(self.mission)
        mission["gates"][1]["depends_on"] = []
        with self.assertRaises((CompileError, ContractError)):
            compile_mission(mission)

    def test_low_risk_mission_can_compile_without_inner_audits(self):
        mission = copy.deepcopy(self.mission)
        mission["spark_audits"] = []
        self.assertEqual(compile_mission(mission)["spark_audit_ids"], [])

    def test_unknown_dependency_rejected(self):
        mission = copy.deepcopy(self.mission)
        mission["gates"][0]["depends_on"] = ["G-NOPE"]
        with self.assertRaises((CompileError, ContractError)):
            compile_mission(mission)

    def test_shell_forms_rejected(self):
        mission = copy.deepcopy(self.mission)
        mission["entrypoints"][0]["argv"] = ["sh", "-c", "echo unsafe"]
        with self.assertRaises(CompileError):
            compile_mission(mission)
        mission["entrypoints"][0]["argv"] = ["echo unsafe"]
        with self.assertRaises(CompileError):
            compile_mission(mission)

    def test_readiness_no_jump_or_backdating(self):
        state = initial_state("V16-PRODUCTIVITY", BASE, TREE)
        frozen = transition(state, "COUNTEREXAMPLES_FROZEN", base_sha=BASE, head_sha=BASE, tree_sha=TREE, counterexample_ids=["CE-SCHEMA", "CE-GATE", "CE-EVIDENCE"], updated_at="9999-12-31T00:00:01Z")
        with self.assertRaises(ReadinessError):
            transition(frozen, "LOCAL_READY", base_sha=BASE, head_sha=CANDIDATE, evidence_ids=["E1"], updated_at="9999-12-31T00:00:02Z")
        with self.assertRaises(ReadinessError):
            transition(frozen, "BASELINE_REPRODUCED", base_sha=BASE, head_sha=BASE, red_counterexamples=["CE-SCHEMA"], updated_at="0001-01-01T00:00:00Z")

    def test_baseline_and_spark_disposition_requirements(self):
        state = initial_state("V16-PRODUCTIVITY", BASE, TREE)
        frozen = transition(state, "COUNTEREXAMPLES_FROZEN", base_sha=BASE, head_sha=BASE, tree_sha=TREE, counterexample_ids=["CE-SCHEMA"], updated_at="9999-12-31T00:00:01Z")
        baseline = transition(frozen, "BASELINE_REPRODUCED", base_sha=BASE, head_sha=BASE, red_counterexamples=["CE-SCHEMA"], updated_at="9999-12-31T00:00:02Z")
        implementing = transition(baseline, "IMPLEMENTING", base_sha=BASE, head_sha=CANDIDATE, updated_at="9999-12-31T00:00:03Z")
        with self.assertRaises(ReadinessError):
            transition(implementing, "INNER_AUDIT_COMPLETE", base_sha=BASE, head_sha=CANDIDATE, spark_findings=["F1"], spark_audit_count=1, dispositions={}, updated_at="9999-12-31T00:00:04Z")
        with self.assertRaisesRegex(ReadinessError, "explicit bounded Spark audit count"):
            transition(implementing, "INNER_AUDIT_COMPLETE", base_sha=BASE, head_sha=CANDIDATE, spark_findings=[], dispositions={}, updated_at="9999-12-31T00:00:04Z")
        audited = transition(implementing, "INNER_AUDIT_COMPLETE", base_sha=BASE, head_sha=CANDIDATE, spark_findings=["F1"], spark_audit_count=1, dispositions={"F1": "FIXED"}, evidence_ids=["E1"], updated_at="9999-12-31T00:00:04Z")
        self.assertEqual(audited["state"], "INNER_AUDIT_COMPLETE")
        with self.assertRaises(ReadinessError):
            transition(audited, "LOCAL_READY", base_sha=BASE, head_sha="fedcba9876543210fedcba9876543210fedcba98", evidence_ids=["E2"], updated_at="9999-12-31T00:00:05Z")

        zero_audit = transition(implementing, "INNER_AUDIT_COMPLETE", base_sha=BASE, head_sha=CANDIDATE, spark_findings=[], spark_audit_count=0, dispositions={}, updated_at="9999-12-31T00:00:04Z")
        self.assertEqual(zero_audit["spark_audit_count"], 0)

    def test_state_store_atomic_and_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = StateStore(tmp)
            state = initial_state("V16-PRODUCTIVITY", BASE, TREE)
            digest = store.save(state)
            self.assertEqual(len(digest), 64)
            self.assertEqual(store.load()["state"], "DRAFT")

    def test_policy_identity_is_frozen_and_cannot_be_spoofed(self):
        policies = {
            "low": {"review_risk": "low", "reasons": ["docs"], "classifier_identity": "classifier-v1"},
            "medium": {"review_risk": "medium", "reasons": ["bounded"], "classifier_identity": "classifier-v1"},
            "high": {"review_risk": "high", "high_risk_triggers": ["security"]},
        }
        for risk, policy in policies.items():
            state = initial_state("V16-PRODUCTIVITY", BASE, TREE, policy)
            self.assertEqual(state["review_risk"], risk)
            self.assertEqual(state["reviewer_route"], "high_risk" if risk == "high" else "general")
            self.assertEqual(state["review_policy_sha256"], canonical_sha256({k: state[k] for k in ("required_stages", "reviewer_model", "reasoning_effort", "review_risk", "reviewer_route", "classifier_identity", "high_risk_triggers")}))
            spoof = dict(state)
            spoof["reviewer_model"] = "gpt-5.6-terra"
            with self.assertRaises(ReadinessError):
                validate_state(spoof)
            spoof = dict(state)
            spoof["review_policy_sha256"] = "0" * 64
            with self.assertRaises(ReadinessError):
                validate_state(spoof)

    def test_fresh_readiness_uses_exact_frozen_stage_prefix(self):
        policies = (
            {"review_risk": "low", "reasons": ["docs"], "classifier_identity": "classifier-v1"},
            {"review_risk": "medium", "reasons": ["bounded"], "classifier_identity": "classifier-v1"},
            {"review_risk": "high", "high_risk_triggers": ["security"]},
        )
        for policy in policies:
            state = initial_state("V16-PRODUCTIVITY", BASE, TREE, policy)
            state.update({
                "state": "INNER_AUDIT_COMPLETE", "revision": 4, "updated_at": "9999-12-31T00:00:04Z",
                "head_sha": CANDIDATE, "tree_sha": TREE, "counterexample_ids": ["CE-1"],
                "red_counterexamples": ["CE-1"], "green_counterexamples": ["CE-1"],
                "author_closure_sha256": "a" * 64,
            })
            state = validate_state(state)
            receipts = {}
            evidence_ids, gate_ids = [], []
            for kind, prefix in (("evidence", "EVID"), ("gate", "GATE")):
                ids = evidence_ids if kind == "evidence" else gate_ids
                for stage in state["required_stages"]:
                    receipt_id = f"{prefix}-{stage}-{CANDIDATE}"
                    artifact = {"receipt_id": receipt_id, "kind": kind, "stage": stage, "head_sha": CANDIDATE, "tree_sha": TREE, "decision": "allow", "counts": {"total": 1, "ran": 1, "passed": 1, "failed": 0, "skipped": 0, "xfail": 0, "unknown": 0}, "artifact_sha256": ""}
                    artifact["artifact_sha256"] = canonical_sha256(artifact)
                    receipts[receipt_id] = artifact
                    ids.append(receipt_id)
            local_ids = [rid for rid in evidence_ids if receipts[rid]["stage"] == "targeted"]
            local = transition(state, "LOCAL_READY", base_sha=BASE, head_sha=CANDIDATE, tree_sha=TREE, evidence_ids=local_ids, receipt_artifacts=receipts, updated_at="9999-12-31T00:00:05Z")
            ready = transition(local, "FRESH_READY", base_sha=BASE, head_sha=CANDIDATE, tree_sha=TREE, evidence_ids=evidence_ids, gate_ids=gate_ids, receipt_artifacts=receipts, updated_at="9999-12-31T00:00:06Z")
            self.assertEqual({receipts[x]["stage"] for x in ready["evidence_ids"]}, set(state["required_stages"]))
            if state["required_stages"] == ["targeted"]:
                extra = dict(receipts)
                rid = f"EVID-full-{CANDIDATE}"
                extra[rid] = {"receipt_id": rid, "kind": "evidence", "stage": "full", "head_sha": CANDIDATE, "tree_sha": TREE, "decision": "allow", "counts": {"total": 1, "ran": 1, "passed": 1, "failed": 0, "skipped": 0, "xfail": 0, "unknown": 0}, "artifact_sha256": ""}
                extra[rid]["artifact_sha256"] = canonical_sha256(extra[rid])
                with self.assertRaises(ReadinessError):
                    transition(local, "FRESH_READY", base_sha=BASE, head_sha=CANDIDATE, tree_sha=TREE, evidence_ids=evidence_ids, gate_ids=gate_ids, receipt_artifacts=extra, updated_at="9999-12-31T00:00:06Z")

    def test_review_ready_requires_caller_bound_independent_lineage(self):
        state = initial_state("V16-PRODUCTIVITY", BASE, TREE)
        receipts = {}
        for kind, prefix in (("evidence", "EVID"), ("gate", "GATE")):
            for stage in ("targeted", "full", "fresh"):
                receipt_id = f"{prefix}-{stage}-{CANDIDATE}"
                artifact = {
                    "receipt_id": receipt_id, "kind": kind, "stage": stage,
                    "head_sha": CANDIDATE, "tree_sha": TREE,
                    "decision": "allow",
                    "counts": {"total": 1, "ran": 1, "passed": 1, "failed": 0, "skipped": 0, "xfail": 0, "unknown": 0},
                    "artifact_sha256": "",
                }
                artifact["artifact_sha256"] = canonical_sha256(artifact)
                receipts[receipt_id] = artifact
        state.update({
            "state": "FRESH_READY", "revision": 6,
            "updated_at": "9999-12-31T00:00:06Z",
            "head_sha": CANDIDATE, "tree_sha": TREE,
            "counterexample_ids": ["CE-1"],
            "red_counterexamples": ["CE-1"],
            "green_counterexamples": ["CE-1"],
            "spark_findings": [], "spark_audit_count": 0, "dispositions": {},
            "evidence_ids": [f"EVID-{stage}-{CANDIDATE}" for stage in ("targeted", "full", "fresh")],
            "gate_ids": [f"GATE-{stage}-{CANDIDATE}" for stage in ("targeted", "full", "fresh")],
            "receipt_artifacts": receipts,
            "author_closure_sha256": "a" * 64,
        })
        state = validate_state(state)
        lineage = {
            "dispatch_transcript_sha256": "d" * 64,
            "task_id": "/root/final-review",
            "parent_task_id": "/root",
            "sender": "/root/final-review",
        }
        packet = render_pr_trace(
            mission_id="V16-PRODUCTIVITY", base_sha=BASE, head_sha=CANDIDATE,
            tree_sha=TREE,
            checks=[{"id": "CHK-1", "status": "GREEN", "reused": False, "skipped": False, "cost": "tiny", "denominator": 1, "total": 1, "ran": 1, "passed": 1, "failed": 0, "unknown": 0, "xfail": 0}],
            findings=[], closures={}, reviewed_scope=["codex/v16"], unreviewed_scope=[],
            decision_basis={
                "acceptance_envelope_sha256": "1" * 64, "diff_sha256": "2" * 64,
                "reviewed_dependency_scope_sha256": "3" * 64, "evidence_bundle_sha256": "4" * 64,
                "evidence_denominator": 1, "review_risk": "high", "reviewer_route": "high_risk",
                "reviewer_model": "gpt-5.6-sol", "reasoning_effort": "xhigh",
                "required_stages": state["required_stages"], "classifier_identity": state["classifier_identity"], "high_risk_triggers": state["high_risk_triggers"], "review_policy_sha256": state["review_policy_sha256"],
                "reference_identity_sha256": "5" * 64, "operating_domain_sha256": "6" * 64, "acceptance_thresholds_sha256": "7" * 64, "invariants_sha256": "8" * 64, "non_goals_sha256": "9" * 64,
            },
        )["packet"]
        artifact = {
            "schema": "independent-review.v16", "reviewer_login": "Liang9921",
            "reviewer_model": "gpt-5.6-sol", "reasoning_effort": "xhigh",
            "reviewer_route": "high_risk", "review_risk": "high", "fork_turns": "none",
            "context_mode": "independent_clean_room", "report_only": True, "reviewer_is_writer": False,
            "base_sha": BASE, "head_sha": CANDIDATE, "tree_sha": TREE,
            "diff_sha256": "2" * 64, "coverage_status": "COMPLETE", "reviewed_scope": ["codex/v16"],
            "unreviewed_scope": [], "review_packet_sha256": canonical_sha256(packet),
            "acceptance_envelope_sha256": "1" * 64, "reviewed_dependency_scope_sha256": "3" * 64,
                "evidence_bundle_sha256": "4" * 64, "evidence_denominator": 1,
                "required_stages": ["targeted", "full", "fresh"], "classifier_identity": state["classifier_identity"], "high_risk_triggers": state["high_risk_triggers"], "review_policy_sha256": state["review_policy_sha256"],
                "reference_identity_sha256": "5" * 64, "operating_domain_sha256": "6" * 64, "acceptance_thresholds_sha256": "7" * 64, "invariants_sha256": "8" * 64, "non_goals_sha256": "9" * 64,
                "prior_review_artifact_sha256": None, "prior_head_sha": None, "delta_sha256": None,
                "reviewer_continuity_id": "reviewer-1", "run_id": "run-1", "escalation_trigger": None, "escalation_evidence_ref": "",
                "findings": [], "findings_sha256": canonical_sha256([]), "closures": {},
            "closures_sha256": canonical_sha256({}), "closure_matrix": {}, "closure_matrix_sha256": canonical_sha256({}), "known_limitations": [], "dispatch_lineage": lineage,
            "verdict": "APPROVE", "artifact_sha256": "",
        }
        artifact["artifact_sha256"] = canonical_sha256(artifact)
        with self.assertRaises(ReadinessError):
            transition(
                state, "REVIEW_READY", base_sha=BASE, head_sha=CANDIDATE,
                tree_sha=TREE, review_packet=packet, independent_artifact=artifact,
                independent_lineage={**lineage, "task_id": "/root/wrong"},
                independent_reviewed_scope=["codex/v16"],
                updated_at="9999-12-31T00:00:07Z",
            )
        ready = transition(
            state, "REVIEW_READY", base_sha=BASE, head_sha=CANDIDATE,
            tree_sha=TREE, review_packet=packet, independent_artifact=artifact,
            independent_lineage=lineage,
            independent_reviewed_scope=["codex/v16"],
            updated_at="9999-12-31T00:00:07Z",
        )
        self.assertTrue(ready["review_ready"])
        self.assertEqual(ready["approved_review_packet_sha256"], canonical_sha256(packet))
        self.assertEqual(ready["approved_review_artifact_sha256"], artifact["artifact_sha256"])


if __name__ == "__main__":
    unittest.main()

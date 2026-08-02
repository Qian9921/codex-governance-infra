import copy
import json
import pathlib
import unittest
from unittest.mock import patch

from codex.v16.compiler import compile_mission
from codex.v16.contracts import (
    ContractError,
    build_pre_execution_closure_authority,
    canonical_sha256,
)
from codex.v16.presubmit import _json_object_from_stdout, build_review_decision_basis
from codex.v16.trace import render_pr_trace


ROOT = pathlib.Path(__file__).parents[2]
MISSION = json.loads((ROOT / "codex/v16/fixtures/mission.valid.json").read_text(encoding="utf-8"))
HEAD = "a" * 40
TREE = "b" * 40
BASE = "c" * 40
SCOPE = ["codex/v16", "scripts", "tests", "manifest.json"]


def _compiled(mission=None):
    return {"plan": compile_mission(copy.deepcopy(MISSION if mission is None else mission))}


def _evidence(total=2, *, identity_mode="git-exact-object", snapshot_sha256="", clean=True):
    evidence = {
        "schema": "evidence-envelope.v16",
        "mission_id": "V16-PRODUCTIVITY",
        "head_sha": HEAD,
        "tree_sha": TREE,
        "identity_mode": identity_mode,
        "snapshot_sha256": snapshot_sha256,
        "clean": clean,
        "rows": [{
            "identity_mode": identity_mode,
            "snapshot_sha256": snapshot_sha256,
            "dirty": not clean,
            "counts": {"total": total, "ran": total, "passed": total, "failed": 0, "skipped": 0, "xfail": 0, "unknown": 0},
        }],
        "envelope_sha256": "",
    }
    evidence["envelope_sha256"] = canonical_sha256(evidence)
    return evidence


def _closure_receipt(compiled_plan_sha256):
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
        "compiled_plan_sha256": compiled_plan_sha256,
        "closure_plan_sha256": "b" * 64,
        "closure_plan_file_sha256": "c" * 64,
        "dispatch_transcript_file_sha256": "d" * 64,
        "normalized_source_artifacts": [
            {
                "audit_id": "SPARK-A",
                "artifact_path": "codex/v16/contracts/spark_result_A.v16.json",
                "artifact_sha256": "e" * 64,
            },
        ],
        "finding_count": 1,
        "bindings": [binding],
        "receipt_sha256": "",
    }
    receipt["receipt_sha256"] = canonical_sha256(receipt)
    return receipt


class ReviewDecisionBasisTests(unittest.TestCase):
    def test_json_object_uses_final_record_and_rejects_invalid_tail(self):
        setup = b'{"schema":"installer-result.v16","files":44}\n'
        checker = b'{"schema":"checker-result.v16","total":23}\n'
        self.assertEqual(
            _json_object_from_stdout(setup + checker)["schema"],
            "checker-result.v16",
        )
        with self.assertRaises((json.JSONDecodeError, ValueError)):
            _json_object_from_stdout(checker + b'{"status":"RED"\n')

    def _basis(self, *, compiled=None, evidence=None, base=BASE, head=HEAD, tree=TREE, scope=None, identity_mode="git-exact-object", snapshot_sha256="", prior_snapshot_sha256=None, prior_head_sha=None, delta_sha256=None, closure_authority=None):
        return build_review_decision_basis(
            ROOT,
            mission=MISSION,
            compiled=_compiled() if compiled is None else compiled,
            evidence=_evidence() if evidence is None else evidence,
            base_sha=base,
            head_sha=head,
            tree_sha=tree,
            reviewed_scope=SCOPE if scope is None else scope,
            identity_mode=identity_mode,
            snapshot_sha256=snapshot_sha256,
            prior_snapshot_sha256=prior_snapshot_sha256,
            prior_head_sha=prior_head_sha,
            delta_sha256=delta_sha256,
            closure_authority=closure_authority,
        )

    def test_policy_routes_are_resolved_without_writer_or_codegraph_fallback(self):
        expected = {
            "low": ("general", "gpt-5.6-terra", "high"),
            "medium": ("general", "gpt-5.6-terra", "high"),
            "high": ("high_risk", "gpt-5.6-sol", "xhigh"),
        }
        with patch("codex.v16.presubmit.git_identity", return_value=(HEAD, TREE, False)), patch(
            "codex.v16.presubmit._git_diff_bytes", return_value=b"exact patch"
        ):
            for risk, (route, model, effort) in expected.items():
                mission = copy.deepcopy(MISSION)
                if risk == "high":
                    mission["review_policy"] = {"review_risk": "high", "high_risk_triggers": ["security"]}
                    mission["reviewer_separation"]["independent_model"] = "gpt-5.6-sol"
                else:
                    mission["review_policy"] = {
                        "review_risk": risk,
                        "reasons": ["bounded review"],
                        "classifier_identity": "classifier-v1",
                        "required_stages": ["targeted", "full", "fresh"],
                    }
                    mission["reviewer_separation"]["independent_model"] = "gpt-5.6-terra"
                basis = self._basis(compiled=_compiled(mission))
                self.assertEqual((basis["reviewer_route"], basis["reviewer_model"], basis["reasoning_effort"]), (route, model, effort))
                self.assertEqual(basis["evidence_denominator"], 2)

    def test_provisional_and_final_renderers_can_reuse_one_basis(self):
        with patch("codex.v16.presubmit.git_identity", return_value=(HEAD, TREE, False)), patch(
            "codex.v16.presubmit._git_diff_bytes", return_value=b"exact patch"
        ):
            basis = self._basis()
        kwargs = {
            "mission_id": "V16-PRODUCTIVITY",
            "base_sha": BASE,
            "head_sha": HEAD,
            "tree_sha": TREE,
            "checks": [{"id": "CHK-1", "status": "GREEN", "reused": False, "skipped": False, "cost": "tiny", "denominator": 1, "total": 1, "ran": 1, "passed": 1, "failed": 0, "unknown": 0, "xfail": 0}],
            "findings": [],
            "closures": {},
            "reviewed_scope": SCOPE,
            "unreviewed_scope": [],
            "decision_basis": basis,
        }
        provisional = render_pr_trace(**kwargs)["packet"]
        final = render_pr_trace(**kwargs)["packet"]
        self.assertEqual(provisional["decision_basis"], final["decision_basis"])
        self.assertIn("reviewed_dependency_scope_sha256", provisional["decision_basis"])
        self.assertNotIn("closure_authority", provisional["decision_basis"])

    def test_preexecution_closure_authority_is_bound_into_decision_basis(self):
        compiled = _compiled()
        receipt = _closure_receipt(canonical_sha256(compiled["plan"]))
        authority = build_pre_execution_closure_authority(receipt)
        evidence = _evidence()
        evidence["closure_binding_receipt_sha256"] = receipt["receipt_sha256"]
        evidence["closure_plan_sha256"] = receipt["closure_plan_sha256"]
        evidence["envelope_sha256"] = ""
        evidence["envelope_sha256"] = canonical_sha256(evidence)
        with patch(
            "codex.v16.presubmit.git_identity",
            return_value=(HEAD, TREE, False),
        ), patch(
            "codex.v16.presubmit._git_diff_bytes",
            return_value=b"exact patch",
        ):
            basis = self._basis(
                compiled=compiled,
                evidence=evidence,
                closure_authority=authority,
            )
        self.assertEqual(basis["closure_authority"], authority)

        partial = copy.deepcopy(authority)
        del partial["bindings_sha256"]
        with patch(
            "codex.v16.presubmit.git_identity",
            return_value=(HEAD, TREE, False),
        ), patch(
            "codex.v16.presubmit._git_diff_bytes",
            return_value=b"exact patch",
        ), self.assertRaisesRegex(ContractError, "missing field"):
            self._basis(
                compiled=compiled,
                evidence=evidence,
                closure_authority=partial,
            )

        wrong_plan = copy.deepcopy(authority)
        wrong_plan["compiled_plan_sha256"] = "f" * 64
        wrong_plan["authority_sha256"] = ""
        wrong_plan["authority_sha256"] = canonical_sha256(wrong_plan)
        with patch(
            "codex.v16.presubmit.git_identity",
            return_value=(HEAD, TREE, False),
        ), patch(
            "codex.v16.presubmit._git_diff_bytes",
            return_value=b"exact patch",
        ), self.assertRaisesRegex(RuntimeError, "mission/plan drift"):
            self._basis(
                compiled=compiled,
                evidence=evidence,
                closure_authority=wrong_plan,
            )

    def test_malformed_policy_cannot_be_silently_rebound_to_a_route(self):
        compiled = _compiled()
        compiled["plan"]["review_policy"]["reviewer_model"] = "writer-model"
        with patch("codex.v16.presubmit.git_identity", return_value=(HEAD, TREE, False)), patch(
            "codex.v16.presubmit._git_diff_bytes", return_value=b"exact patch"
        ), self.assertRaisesRegex(RuntimeError, "risk/model/reasoning"):
            self._basis(compiled=compiled)

    def test_acceptance_scope_diff_and_snapshot_identity_drift_change_basis(self):
        with patch("codex.v16.presubmit.git_identity", return_value=(HEAD, TREE, False)), patch(
            "codex.v16.presubmit._git_diff_bytes", return_value=b"exact patch"
        ):
            original = self._basis()
            changed_acceptance = _compiled()
            changed_acceptance["plan"]["acceptance"][0]["why_red"] += " changed"
            acceptance_basis = self._basis(compiled=changed_acceptance)
            scope_basis = self._basis(scope=SCOPE + ["README.md"])
            base_basis = self._basis(base="e" * 40)
        self.assertNotEqual(original["acceptance_envelope_sha256"], acceptance_basis["acceptance_envelope_sha256"])
        self.assertNotEqual(original["reviewed_dependency_scope_sha256"], scope_basis["reviewed_dependency_scope_sha256"])
        self.assertNotEqual(original["diff_sha256"], base_basis["diff_sha256"])

    def test_stale_identity_and_unknown_denominator_are_rejected(self):
        with patch("codex.v16.presubmit.git_identity", return_value=("f" * 40, TREE, False)), patch(
            "codex.v16.presubmit._git_diff_bytes", return_value=b"exact patch"
        ):
            with self.assertRaisesRegex(RuntimeError, "identity drift"):
                self._basis()
        with patch("codex.v16.presubmit.git_identity", return_value=(HEAD, TREE, False)), patch(
            "codex.v16.presubmit._git_diff_bytes", return_value=b"exact patch"
        ):
            with self.assertRaisesRegex(RuntimeError, "denominator"):
                self._basis(evidence=_evidence(total=0))
            unknown = _evidence()
            unknown["rows"][0]["counts"]["passed"] = 1
            unknown["rows"][0]["counts"]["ran"] = 1
            unknown["rows"][0]["counts"]["unknown"] = 1
            with self.assertRaisesRegex(RuntimeError, "green and known"):
                self._basis(evidence=unknown)

    def test_non_git_basis_accepts_current_dirty_snapshot_and_rejects_stale_evidence(self):
        current_snapshot = "1" * 64
        stale_snapshot = "2" * 64
        current_evidence = _evidence(
            identity_mode="non-git-snapshot",
            snapshot_sha256=current_snapshot,
            clean=False,
        )
        with patch("codex.v16.presubmit.git_identity", return_value=(HEAD, TREE, True)), patch(
            "codex.v16.presubmit.content_snapshot", return_value=current_snapshot
        ), patch("codex.v16.presubmit._git_diff_bytes", return_value=b"exact patch"):
            basis = self._basis(
                evidence=current_evidence,
                identity_mode="non-git-snapshot",
                snapshot_sha256=current_snapshot,
            )
            self.assertEqual(basis["identity_mode"], "non-git-snapshot")
            self.assertEqual(basis["snapshot_sha256"], current_snapshot)

            stale_evidence = _evidence(
                identity_mode="non-git-snapshot",
                snapshot_sha256=stale_snapshot,
                clean=False,
            )
            with self.assertRaisesRegex(RuntimeError, "snapshot identity"):
                self._basis(
                    evidence=stale_evidence,
                    identity_mode="non-git-snapshot",
                    snapshot_sha256=current_snapshot,
                )

    def test_policy_stage_prefix_is_part_of_acceptance_and_decision_identity(self):
        mission_a = copy.deepcopy(MISSION)
        mission_b = copy.deepcopy(MISSION)
        compiled_a = _compiled(mission_a)
        compiled_b = copy.deepcopy(compiled_a)
        compiled_a["plan"]["review_policy"]["required_stages"] = ["targeted"]
        compiled_b["plan"]["review_policy"]["required_stages"] = ["targeted", "full", "fresh"]
        with patch("codex.v16.presubmit.git_identity", return_value=(HEAD, TREE, False)), patch(
            "codex.v16.presubmit._git_diff_bytes", return_value=b"exact patch"
        ):
            basis_a = build_review_decision_basis(ROOT, mission=mission_a, compiled=compiled_a, evidence=_evidence(), base_sha=BASE, head_sha=HEAD, tree_sha=TREE, reviewed_scope=SCOPE)
            basis_b = build_review_decision_basis(ROOT, mission=mission_b, compiled=compiled_b, evidence=_evidence(), base_sha=BASE, head_sha=HEAD, tree_sha=TREE, reviewed_scope=SCOPE)
        self.assertNotEqual(basis_a["review_policy_sha256"], basis_b["review_policy_sha256"])
        self.assertNotEqual(basis_a["acceptance_envelope_sha256"], basis_b["acceptance_envelope_sha256"])
        self.assertEqual(basis_a["required_stages"], ["targeted"])
        self.assertEqual(basis_b["required_stages"], ["targeted", "full", "fresh"])


if __name__ == "__main__":
    unittest.main()

import copy
import json
import pathlib
import unittest

from codex.v16.metrics import MetricsError, collect_metrics, dashboard, validate_metrics
from codex.v16.contracts import validate_mission


ROOT = pathlib.Path(__file__).parents[2]
FIXTURE = ROOT / "codex/v16/fixtures/mission.valid.json"
HEAD = "0123456789abcdef0123456789abcdef01234567"


def mission_for(risk):
    mission = json.loads(FIXTURE.read_text(encoding="utf-8"))
    mission["review_policy"] = (
        {"review_risk": risk, "reasons": ["bounded stage test"], "classifier_identity": "classifier-v1"}
        if risk in {"low", "medium"}
        else {"review_risk": "high", "high_risk_triggers": ["security"]}
    )
    mission["reviewer_separation"]["independent_model"] = "gpt-5.6-sol"
    return mission


def source_for(risk, stages):
    mission = mission_for(risk)
    rows = [
        {"stage": stage, "actual_head": HEAD, "correction_of": None, "writer_task_id": "writer-a"}
        for stage in stages
    ]
    evidence = {"rows": rows}
    review = {"verdict": "APPROVE", "round": 1, "findings": []}
    sparks = [{"elapsed_sec": 1.0} for _ in mission["spark_audits"]]
    runs = [{"elapsed_sec": 1.0}]
    return mission, evidence, review, sparks, runs


def collect(risk, stages):
    mission, evidence, review, sparks, runs = source_for(risk, stages)
    metrics = collect_metrics(
        mission=mission,
        evidence=evidence,
        review=review,
        spark_results=sparks,
        gate_runs=runs,
    )
    # collect_metrics hashes the normalized mission; preserve that exact source
    # identity for the independent recomputation assertion.
    source = {"mission": validate_mission(mission), "evidence": evidence, "review": review, "spark_results": sparks, "gate_runs": runs}
    return metrics, source


class MetricsPolicyStageTests(unittest.TestCase):
    def test_low_targeted_only_has_unavailable_full_and_fresh(self):
        metrics, source = collect("low", ["targeted"])
        self.assertIsNone(metrics["full_runs_per_head"])
        self.assertIsNone(metrics["fresh_runs_per_head"])
        self.assertEqual(validate_metrics(metrics, source_bundle=source), metrics)
        dashboard_view = dashboard(metrics)
        self.assertNotIn("first_pass_approval", dashboard_view["policy_targets"])
        self.assertNotIn("review_rounds", dashboard_view["policy_targets"])
        self.assertNotIn("new_blocker_admissions", dashboard_view["policy_targets"])
        self.assertNotIn("full_runs_per_head", dashboard_view["policy_targets"])
        self.assertNotIn("fresh_runs_per_head", dashboard_view["policy_targets"])

    def test_medium_requires_targeted_and_full_but_not_fresh(self):
        metrics, source = collect("medium", ["targeted", "full"])
        self.assertEqual(metrics["full_runs_per_head"], 1.0)
        self.assertIsNone(metrics["fresh_runs_per_head"])
        self.assertEqual(validate_metrics(metrics, source_bundle=source), metrics)

    def test_high_missing_fresh_is_rejected(self):
        mission, evidence, review, sparks, runs = source_for("high", ["targeted", "full"])
        with self.assertRaises(MetricsError):
            collect_metrics(
                mission=mission,
                evidence=evidence,
                review=review,
                spark_results=sparks,
                gate_runs=runs,
            )

    def test_source_bound_shape_rejects_bool_type_and_forged_unavailable_stage(self):
        metrics, source = collect("low", ["targeted"])
        for field, value in (("review_rounds", True), ("full_runs_per_head", 0), ("fresh_runs_per_head", "forged")):
            forged = copy.deepcopy(metrics)
            forged[field] = value
            with self.subTest(field=field, value=value), self.assertRaises(MetricsError):
                validate_metrics(forged, source_bundle=source)

    def test_required_stage_needs_current_head_denominator(self):
        mission, evidence, review, sparks, runs = source_for("medium", ["full"])
        with self.assertRaises(MetricsError):
            collect_metrics(
                mission=mission,
                evidence=evidence,
                review=review,
                spark_results=sparks,
                gate_runs=runs,
            )


if __name__ == "__main__":
    unittest.main()

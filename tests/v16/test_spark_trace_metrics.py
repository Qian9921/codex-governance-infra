import copy
import json
import pathlib
import unittest

from codex.v16.metrics import MetricsError, collect_metrics, dashboard
from codex.v16.spark import SparkAuditError, audit_requests, validate_bundle, validate_result
from codex.v16.trace import TraceError, render_pr_trace, validate_review_packet

ROOT = pathlib.Path(__file__).parents[2]
MISSION = json.loads((ROOT / "codex/v16/fixtures/mission.valid.json").read_text(encoding="utf-8"))
BASE = "e18439c8dfe01d901895efd09b8b73b6842327a9"
HEAD = "0123456789abcdef0123456789abcdef01234567"
TREE = "1de79a7c48e6c66f167be54ca9cf387310149f80"


def result_for(request, index):
    scope = request["scope"][0]
    finding = {"id": f"F-{index}", "severity": "P2", "label": "FOLLOW_UP", "scope": scope, "requirement": "bounded", "location": "fixture", "counterexample": "negative", "impact": "visible", "smallest_outcome": "reject", "acceptance_case": "assert error", "attribution": "ORIGINAL_SCOPE_MISSED"}
    return {"schema": "spark-audit-result.v16", "audit_id": request["audit_id"], "mission_id": request["mission_id"], "task_id": f"spark-task-{index}", "assigned_model": "gpt-5.3-codex-spark", "reasoning_effort": "high", "fork_turns": "none", "context_mode": "zero-context", "report_only": True, "scope": scope, "findings": [finding], "dispositions": {f"F-{index}": "FIXED"}, "started_at": "2026-07-31T00:00:00Z", "ended_at": "2026-07-31T00:00:01Z", "elapsed_sec": 1}


class SparkTraceMetricsTests(unittest.TestCase):
    def test_spark_exact_three_and_dispositions(self):
        requests = audit_requests(MISSION)
        results = [result_for(r, i) for i, r in enumerate(requests)]
        self.assertEqual(len(validate_bundle(requests, results)), 3)
        bad = copy.deepcopy(results); bad[0]["dispositions"] = {}
        with self.assertRaises(SparkAuditError):
            validate_bundle(requests, bad)
        duplicate = copy.deepcopy(results); duplicate[1]["audit_id"] = duplicate[0]["audit_id"]
        with self.assertRaises(SparkAuditError):
            validate_bundle(requests, duplicate)
        out_of_scope = copy.deepcopy(results); out_of_scope[0]["scope"] = "not-requested"
        with self.assertRaises(SparkAuditError):
            validate_bundle(requests, out_of_scope)
        too_many = copy.deepcopy(MISSION); too_many["spark_audits"].append(copy.deepcopy(too_many["spark_audits"][0])); too_many["spark_audits"][-1]["id"] = "SPARK-D"
        with self.assertRaises(SparkAuditError):
            audit_requests(too_many)

    def test_trace_identity_coverage_verdict(self):
        checks = [{"id": "CHK-1", "status": "GREEN", "reused": False, "skipped": False, "cost": "tiny", "denominator": 1, "total": 1, "passed": 1, "failed": 0}]
        finding = {"id": "F-1", "severity": "P2", "label": "FOLLOW_UP", "attribution": "ORIGINAL_SCOPE_MISSED", "location": "fixture", "counterexample": "negative", "disposition": "FIXED"}
        rendered = render_pr_trace(mission_id="V16-PRODUCTIVITY", base_sha=BASE, head_sha=HEAD, tree_sha=TREE, checks=checks, findings=[finding], closures={"F-1": "FIXED"}, reviewed_scope=["codex/v16"], unreviewed_scope=[])
        packet = validate_review_packet(rendered["packet"])
        self.assertEqual(packet["verdict"], "APPROVE")
        collision = copy.deepcopy(packet); collision["reviewer_login"] = "Qian9921"
        with self.assertRaises(TraceError):
            validate_review_packet(collision)
        incomplete = render_pr_trace(mission_id="V16-PRODUCTIVITY", base_sha=BASE, head_sha=HEAD, tree_sha=TREE, checks=checks, findings=[], closures={}, reviewed_scope=["codex/v16"], unreviewed_scope=["docs"])
        self.assertEqual(incomplete["packet"]["verdict"], "REQUEST_CHANGES")

    def test_metrics_derived_and_policy_dashboard(self):
        evidence = {"rows": [{"stage": "targeted", "actual_head": HEAD, "correction_of": None, "writer_task_id": "writer-a"}, {"stage": "full", "actual_head": HEAD, "correction_of": None, "writer_task_id": "writer-a"}, {"stage": "fresh", "actual_head": HEAD, "correction_of": "E-1", "writer_task_id": "writer-b"}]}
        review = {"verdict": "APPROVE", "round": 1, "findings": [{"severity": "P2", "label": "FOLLOW_UP", "attribution": "NEW_FALSIFIABLE_EVIDENCE"}]}
        sparks = [{"elapsed_sec": 1}, {"elapsed_sec": 2}, {"elapsed_sec": 3}]
        runs = [{"elapsed_sec": 1.5}]
        metrics = collect_metrics(mission=MISSION, evidence=evidence, review=review, spark_results=sparks, gate_runs=runs)
        self.assertTrue(metrics["first_pass_approval"])
        self.assertEqual(metrics["spark_audit_count"], 3)
        self.assertEqual(metrics["evidence_corrections"], 1)
        self.assertEqual(metrics["writer_handoffs"], 1)
        self.assertEqual(dashboard(metrics)["policy_targets"]["spark_audit_count"]["exact"], 3)
        with self.assertRaises(MetricsError):
            collect_metrics(mission=MISSION, evidence=evidence, review=review, spark_results=sparks[:2], gate_runs=runs)


if __name__ == "__main__":
    unittest.main()

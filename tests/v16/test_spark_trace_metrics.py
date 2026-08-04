import copy
import hashlib
import json
import pathlib
import subprocess
import tempfile
import unittest

from codex.v16.compiler import compile_mission
from codex.v16.metrics import MetricsError, collect_metrics, dashboard
from codex.v16.runner import GateRunError, validate_gate_result
from codex.v16.spark import (
    SparkAuditError,
    audit_requests,
    build_closure_binding_receipt,
    validate_bundle,
    validate_dispatch_transcript,
    validate_result,
)
from codex.v16.contracts import (
    build_pre_execution_closure_authority,
    canonical_sha256,
)
from codex.v16.trace import (
    TraceError,
    _counterexample_sha256,
    _evidence_result_sha256,
    identity_delta_sha256,
    ingest_independent_artifact,
    render_pr_trace,
    validate_review_packet,
)
from codex.v16.presubmit import source_identity_guard

ROOT = pathlib.Path(__file__).parents[2]
MISSION = json.loads((ROOT / "codex/v16/fixtures/mission.valid.json").read_text(encoding="utf-8"))
BASE = "e18439c8dfe01d901895efd09b8b73b6842327a9"
HEAD = "0123456789abcdef0123456789abcdef01234567"
TREE = "1de79a7c48e6c66f167be54ca9cf387310149f80"
LINEAGE = {
    "dispatch_transcript_sha256": "d" * 64,
    "task_id": "/root/final-review",
    "parent_task_id": "/root",
    "sender": "/root/final-review",
}


def result_for(request, index):
    scope = request["scope"][0]
    finding = {"id": f"F-{index}", "severity": "P2", "label": "FOLLOW_UP", "scope": scope, "requirement": "bounded", "location": "fixture", "counterexample": "negative", "impact": "visible", "smallest_outcome": "reject", "acceptance_case": "assert error", "attribution": "ORIGINAL_SCOPE_MISSED"}
    return {"schema": "spark-audit-result.v16", "audit_id": request["audit_id"], "mission_id": request["mission_id"], "task_id": f"spark-task-{index}", "assigned_model": "gpt-5.3-codex-spark", "reasoning_effort": "high", "fork_turns": "none", "context_mode": "zero-context", "report_only": True, "scope": scope, "findings": [finding], "dispositions": {f"F-{index}": "FIXED"}, "started_at": "2026-07-31T00:00:00Z", "ended_at": "2026-07-31T00:00:01Z", "elapsed_sec": 1}


def sha(index):
    return f"{index:064x}"


def decision_basis(index, risk="low", *, closure_receipt=None, closure_aware=True):
    route, model, effort = {
        "low": ("general", "gpt-5.6-sol", "high"),
        "medium": ("general", "gpt-5.6-sol", "high"),
        "high": ("high_risk", "gpt-5.6-sol", "xhigh"),
    }[risk]
    policy = {
        "required_stages": ["targeted"],
        "review_risk": risk,
        "reviewer_route": route,
        "reviewer_model": model,
        "reasoning_effort": effort,
        "classifier_identity": "classifier-v1",
        "high_risk_triggers": ["security"] if risk == "high" else [],
    }
    result = {
        "acceptance_envelope_sha256": sha(index * 10 + 1),
        "diff_sha256": sha(index * 10 + 2),
        "reviewed_dependency_scope_sha256": sha(3),
        "evidence_bundle_sha256": sha(index * 10 + 4),
        "evidence_denominator": index + 1,
        "review_risk": risk,
        "reviewer_route": route,
        "reviewer_model": model,
        "reasoning_effort": effort,
        "required_stages": policy["required_stages"],
        "classifier_identity": policy["classifier_identity"],
        "high_risk_triggers": policy["high_risk_triggers"],
        "review_policy_sha256": canonical_sha256(policy),
        "reference_identity_sha256": sha(index * 10 + 5),
        "operating_domain_sha256": sha(index * 10 + 6),
        "acceptance_thresholds_sha256": sha(index * 10 + 7),
        "invariants_sha256": sha(index * 10 + 8),
        "non_goals_sha256": sha(index * 10 + 9),
    }
    if closure_aware:
        receipt = closure_receipt or closure_binding_receipt()
        result["closure_authority"] = (
            build_pre_execution_closure_authority(receipt)
        )
    return result


def full_finding(fid, *, severity="P2", disposition="FOLLOW_UP", blocker_admission=None):
    is_p1 = severity == "P1"
    admission = ("NONE" if blocker_admission is None else blocker_admission) if is_p1 else "NONE"
    return {
        "id": fid,
        "severity": severity,
        "label": "BLOCKING" if is_p1 else "FOLLOW_UP",
        "attribution": "DELTA_INTRODUCED" if is_p1 else "ORIGINAL_SCOPE_MISSED",
        "location": "codex/v16/trace.py:1",
        "contract_clause": "REVIEW-1 finding contract",
        "counterexample": "deterministic counterexample",
        "impact": "approval could be unsound",
        "smallest_acceptable_outcome": "reject malformed artifact",
        "acceptance_check": "focused trace test",
        "blocker_admission": admission,
        "admission_evidence_ref": "CE-1" if is_p1 and admission != "NONE" else "",
        "disposition": disposition,
    }


_CLOSURE_CASES = (
    "P1-1", "P1-2", "P1-partial", "P1-shrink", "P1-drift", "P1-reopen",
)


def closure_binding_receipt(*, shared=False, closure_plan_sha256=None):
    bindings = []
    for finding_id in _CLOSURE_CASES:
        entrypoint_id = "EP-SHARED" if shared else f"EP-{finding_id}"
        binding = {
            "finding_id": finding_id,
            "counterexample_id": "CE-EVIDENCE",
            "executable_counterexample_id": "NF-001",
            "counterexample_sha256": _counterexample_sha256("deterministic counterexample"),
            "gate_id": "G-TARGETED",
            "stage": "targeted",
            "evidence_row_id": f"EVID-G-TARGETED-{entrypoint_id}",
            "entrypoint_id": entrypoint_id,
            "binding_sha256": "",
        }
        binding["binding_sha256"] = canonical_sha256(binding)
        bindings.append(binding)
    bindings.sort(key=lambda item: item["finding_id"])
    receipt = {
        "schema": "closure-binding-receipt.v16",
        "mission_id": "V16-PRODUCTIVITY",
        "compiled_plan_sha256": "a" * 64,
        "closure_plan_sha256": closure_plan_sha256 or ("b" * 64),
        "closure_plan_file_sha256": "c" * 64,
        "dispatch_transcript_file_sha256": "d" * 64,
        "normalized_source_artifacts": [
            {
                "audit_id": "SPARK-A",
                "artifact_path": "codex/v16/contracts/spark_result_A.v16.json",
                "artifact_sha256": "e" * 64,
            },
        ],
        "finding_count": len(bindings),
        "bindings": bindings,
        "receipt_sha256": "",
    }
    receipt["receipt_sha256"] = canonical_sha256(receipt)
    return receipt


def evidence_bundle(*, head, tree, identity_mode="git-exact-object", snapshot_sha256="", clean=None, receipt=None, closure_aware=True):
    if clean is None:
        clean = identity_mode == "git-exact-object"
    if receipt is None and closure_aware:
        receipt = closure_binding_receipt()
    rows = []
    grouped = {}
    if receipt is not None:
        for binding in receipt["bindings"]:
            grouped.setdefault(binding["evidence_row_id"], []).append(binding)
    else:
        for finding_id in _CLOSURE_CASES:
            grouped[f"CASE-{finding_id}"] = [{
                "finding_id": finding_id,
                "counterexample_sha256": _counterexample_sha256("deterministic counterexample"),
                "gate_id": "G-TARGETED",
                "stage": "targeted",
                "entrypoint_id": "EP-TARGETED",
                "binding_sha256": "",
            }]
    for index, (case_id, bindings) in enumerate(sorted(grouped.items())):
        binding = bindings[0]
        row = {
            "case_id": case_id,
            "gate_id": binding["gate_id"],
            "stage": binding["stage"],
            "entrypoint_id": binding["entrypoint_id"],
            "decision": "allow",
            "actual_head": head,
            "tree_sha": tree,
            "identity_mode": identity_mode,
            "snapshot_sha256": snapshot_sha256,
            "dirty": not clean,
            "counts": {"total": 1, "ran": 1, "passed": 1, "failed": 0, "skipped": 0, "xfail": 0, "unknown": 0},
            "log_sha256": f"{index + 1:064x}",
        }
        if len(bindings) == 1:
            row["finding_id"] = binding["finding_id"]
            row["counterexample_sha256"] = binding["counterexample_sha256"]
        if receipt is not None:
            row["closure_binding_receipt_sha256"] = receipt["receipt_sha256"]
            row["closure_binding_sha256s"] = sorted(
                item["binding_sha256"] for item in bindings
            )
        rows.append(row)
    bundle = {"schema": "evidence-envelope.v16", "mission_id": "V16-PRODUCTIVITY", "head_sha": head, "tree_sha": tree, "identity_mode": identity_mode, "snapshot_sha256": snapshot_sha256, "clean": clean, "rows": rows, "envelope_sha256": ""}
    if receipt is not None:
        bundle["closure_binding_receipt_sha256"] = receipt["receipt_sha256"]
        bundle["closure_plan_sha256"] = receipt["closure_plan_sha256"]
    bundle["envelope_sha256"] = canonical_sha256(bundle)
    return bundle


def trace_packet(*, head=HEAD, tree=TREE, basis=None, reviewed_scope=None, unreviewed_scope=None, bundle=None):
    if basis is None:
        basis = decision_basis(1)
    basis = copy.deepcopy(basis)
    identity_mode = basis.get("identity_mode", "git-exact-object")
    snapshot_sha256 = basis.get("snapshot_sha256", "")
    if bundle is None:
        bundle = evidence_bundle(
            head=head,
            tree=tree,
            identity_mode=identity_mode,
            snapshot_sha256=snapshot_sha256,
        )
    basis["evidence_bundle_sha256"] = bundle["envelope_sha256"]
    return render_pr_trace(
        mission_id="V16-PRODUCTIVITY",
        base_sha=BASE,
        head_sha=head,
        tree_sha=tree,
        checks=[{"id": "CHK-1", "status": "GREEN", "reused": False, "skipped": False, "cost": "tiny", "denominator": 1, "total": 1, "passed": 1, "failed": 0}],
        findings=[],
        closures={},
        reviewed_scope=["codex/v16"] if reviewed_scope is None else reviewed_scope,
        unreviewed_scope=[] if unreviewed_scope is None else unreviewed_scope,
        decision_basis=basis,
    )["packet"]


def seal_artifact(artifact):
    artifact["findings_sha256"] = canonical_sha256(artifact["findings"])
    artifact["closures_sha256"] = canonical_sha256(artifact["closures"])
    artifact["artifact_sha256"] = ""
    artifact["artifact_sha256"] = canonical_sha256(artifact)
    return artifact


def formal_artifact(
    packet,
    *,
    mode="independent_clean_room",
    verdict="APPROVE",
    findings=None,
    prior=None,
    continuity="reviewer-A",
    run_id="review-run-1",
    lineage=None,
    trigger=None,
    close_prior=False,
    closure_receipt=None,
):
    basis = packet["decision_basis"]
    if close_prior and prior is not None:
        reviewer_findings = copy.deepcopy(prior["findings"])
        for finding in reviewer_findings:
            finding["disposition"] = "FIXED"
    else:
        reviewer_findings = [full_finding("R-1")] if findings is None else copy.deepcopy(findings)
    dispatch = dict(LINEAGE if lineage is None else lineage)
    prior_findings = {} if prior is None else {
        finding["id"]: {
            "prior_disposition": finding["disposition"],
            "disposition": finding["disposition"],
            "evidence_ref": "prior-evidence",
            "counterexample_recheck": finding["counterexample"],
        }
        for finding in prior["findings"]
    }
    if close_prior and prior is not None:
        receipt = closure_receipt or closure_binding_receipt()
        bindings_by_finding = {
            binding["finding_id"]: binding
            for binding in receipt["bindings"]
        }
        for finding_id, entry in prior_findings.items():
            entry["disposition"] = "FIXED"
            target_head = packet["head_sha"]
            target_tree = packet["tree_sha"]
            bundle = evidence_bundle(
                head=target_head,
                tree=target_tree,
                identity_mode=packet.get("identity_mode", "git-exact-object"),
                snapshot_sha256=packet.get("snapshot_sha256", ""),
                receipt=receipt,
            )
            binding = bindings_by_finding[finding_id]
            row = next(
                row for row in bundle["rows"]
                if row["case_id"] == binding["evidence_row_id"]
            )
            entry["evidence_ref"] = {
                "case_id": row["case_id"],
                "evidence_row_sha256": canonical_sha256(row),
                "log_sha256": row["log_sha256"],
                "binding_sha256": binding["binding_sha256"],
            }
            prior_finding = prior["findings"][[f["id"] for f in prior["findings"]].index(finding_id)]
            entry["counterexample_recheck"] = {
                "case_id": row["case_id"],
                "evidence_row_sha256": canonical_sha256(row),
                "log_sha256": row["log_sha256"],
                "binding_sha256": binding["binding_sha256"],
                "counterexample_sha256": _counterexample_sha256(prior_finding["counterexample"]),
                "kind": "EXECUTABLE_RESULT",
                "result_sha256": _evidence_result_sha256(row),
            }
    artifact = {
        "schema": "independent-review.v16",
        "reviewer_login": "Liang9921",
        "reviewer_model": basis["reviewer_model"],
        "reasoning_effort": basis["reasoning_effort"],
        "reviewer_route": basis["reviewer_route"],
        "review_risk": basis["review_risk"],
        "fork_turns": "none",
        "context_mode": mode,
        "report_only": True,
        "reviewer_is_writer": False,
        "base_sha": packet["base_sha"],
        "head_sha": packet["head_sha"],
        "tree_sha": packet["tree_sha"],
        "diff_sha256": basis["diff_sha256"],
        "coverage_status": packet["coverage_status"],
        "reviewed_scope": list(packet["reviewed_scope"]),
        "unreviewed_scope": list(packet["unreviewed_scope"]),
        "review_packet_sha256": canonical_sha256(packet),
        "acceptance_envelope_sha256": basis["acceptance_envelope_sha256"],
        "reviewed_dependency_scope_sha256": basis["reviewed_dependency_scope_sha256"],
        "evidence_bundle_sha256": basis["evidence_bundle_sha256"],
        "evidence_denominator": basis["evidence_denominator"],
        "prior_review_artifact_sha256": None if prior is None else prior["artifact_sha256"],
        "prior_head_sha": None if prior is None else prior["head_sha"],
        "delta_sha256": None if prior is None else sha(90),
        "reviewer_continuity_id": continuity,
        "run_id": run_id,
        "escalation_trigger": trigger,
        "escalation_evidence_ref": "",
        "findings": reviewer_findings,
        "findings_sha256": "",
        "closures": {finding["id"]: finding["disposition"] for finding in reviewer_findings},
        "closures_sha256": "",
        "known_limitations": ["P2 follow-up remains visible"] if reviewer_findings else [],
        "dispatch_lineage": dispatch,
        "verdict": verdict,
        "artifact_sha256": "",
        "required_stages": list(basis["required_stages"]),
        "classifier_identity": basis["classifier_identity"],
        "high_risk_triggers": list(basis["high_risk_triggers"]),
        "review_policy_sha256": basis["review_policy_sha256"],
        "reference_identity_sha256": basis["reference_identity_sha256"],
        "operating_domain_sha256": basis["operating_domain_sha256"],
        "acceptance_thresholds_sha256": basis["acceptance_thresholds_sha256"],
        "invariants_sha256": basis["invariants_sha256"],
        "non_goals_sha256": basis["non_goals_sha256"],
        "closure_matrix": prior_findings,
        "closure_matrix_sha256": canonical_sha256(prior_findings),
    }
    if "identity_mode" in packet:
        artifact.update({
            "identity_mode": packet["identity_mode"],
            "snapshot_sha256": packet["snapshot_sha256"],
            "prior_snapshot_sha256": packet["prior_snapshot_sha256"],
        })
    return seal_artifact(artifact)


def ingest(packet, artifact, **kwargs):
    if artifact["context_mode"] != "independent_clean_room":
        kwargs.setdefault("expected_delta_sha256", artifact["delta_sha256"])
    if artifact["context_mode"] == "escalated_fresh":
        kwargs.setdefault("expected_escalation_evidence_ref", artifact["escalation_evidence_ref"])
    if "closure_binding_receipt" not in kwargs:
        kwargs["closure_binding_receipt"] = (
            closure_binding_receipt()
            if "closure_authority" in packet["decision_basis"]
            else None
        )
    receipt = kwargs.get("closure_binding_receipt")
    kwargs.setdefault(
        "evidence_bundle",
        evidence_bundle(
            head=packet["head_sha"],
            tree=packet["tree_sha"],
            identity_mode=packet.get("identity_mode", "git-exact-object"),
            snapshot_sha256=packet.get("snapshot_sha256", ""),
            receipt=receipt,
            closure_aware=receipt is not None,
        ),
    )
    return ingest_independent_artifact(
        packet,
        artifact,
        expected_dispatch_transcript_sha256=artifact["dispatch_lineage"]["dispatch_transcript_sha256"],
        expected_task_id=artifact["dispatch_lineage"]["task_id"],
        expected_parent_task_id=artifact["dispatch_lineage"]["parent_task_id"],
        expected_sender=artifact["dispatch_lineage"]["sender"],
        expected_review_risk=packet["decision_basis"]["review_risk"],
        expected_reviewer_route=packet["decision_basis"]["reviewer_route"],
        expected_reviewer_model=packet["decision_basis"]["reviewer_model"],
        expected_reasoning_effort=packet["decision_basis"]["reasoning_effort"],
        **kwargs,
    )


class SparkTraceMetricsTests(unittest.TestCase):
    def test_historical_transcript_does_not_recompile_with_candidate_compiler(self):
        transcript = json.loads(
            (
                ROOT / "codex/v16/contracts/v16_dispatch_transcript.json"
            ).read_text(encoding="utf-8")
        )
        current_plan_sha256 = canonical_sha256(compile_mission(MISSION))
        self.assertNotEqual(
            current_plan_sha256,
            transcript["compiled_plan_sha256"],
        )
        head = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT,
            text=True,
        ).strip()
        tree = subprocess.check_output(
            ["git", "rev-parse", "HEAD^{tree}"],
            cwd=ROOT,
            text=True,
        ).strip()
        checked = validate_dispatch_transcript(
            transcript,
            expected_head=head,
            expected_tree=tree,
            root=ROOT,
        )
        self.assertEqual(
            checked["compiled_plan_sha256"],
            transcript["compiled_plan_sha256"],
        )

    def test_preexecution_receipt_is_built_from_closure_plan_and_spark_sources(self):
        closure_plan_path = ROOT / "codex/v16/contracts/author_closure_plan.v16.json"
        closure_plan = json.loads(closure_plan_path.read_text(encoding="utf-8"))
        requests = audit_requests(MISSION)
        spark_results = []
        for index, request in enumerate(requests):
            result = result_for(request, index)
            template = result["findings"][0]
            plan_items = closure_plan["findings"][index::len(requests)]
            result["findings"] = [
                dict(template) | {
                    "id": item["finding_id"],
                    "counterexample": (
                        "frozen token counterexample transcript for "
                        f"{item['finding_id']}"
                    ),
                }
                for item in plan_items
            ]
            result["dispositions"] = {
                finding["id"]: "FOLLOW_UP" for finding in result["findings"]
            }
            result["artifact_sha256"] = f"{index + 1:064x}"
            spark_results.append(result)
        compiled = compile_mission(MISSION)
        source_paths = {
            request["audit_id"]: (
                f"codex/v16/contracts/spark_result_{index}.v16.json"
            )
            for index, request in enumerate(requests)
        }
        receipt = build_closure_binding_receipt(
            closure_plan,
            compiled_plan=compiled,
            spark_requests=requests,
            spark_results=spark_results,
            closure_plan_file_sha256=hashlib.sha256(
                closure_plan_path.read_bytes()
            ).hexdigest(),
            dispatch_transcript_file_sha256="d" * 64,
            normalized_source_artifact_paths=source_paths,
        )
        self.assertEqual(receipt["finding_count"], 18)
        self.assertEqual(
            receipt["compiled_plan_sha256"],
            canonical_sha256(compiled),
        )
        binding = next(
            item for item in receipt["bindings"]
            if item["finding_id"] == closure_plan["findings"][0]["finding_id"]
        )
        self.assertEqual(
            binding["counterexample_sha256"],
            _counterexample_sha256(
                "frozen token counterexample transcript for "
                f"{binding['finding_id']}"
            ),
        )
        missing = copy.deepcopy(spark_results)
        removed = missing[0]["findings"].pop()
        del missing[0]["dispositions"][removed["id"]]
        with self.assertRaisesRegex(SparkAuditError, "finding sets differ"):
            build_closure_binding_receipt(
                closure_plan,
                compiled_plan=compiled,
                spark_requests=requests,
                spark_results=missing,
                closure_plan_file_sha256=hashlib.sha256(
                    closure_plan_path.read_bytes()
                ).hexdigest(),
                dispatch_transcript_file_sha256="d" * 64,
                normalized_source_artifact_paths=source_paths,
            )

    def test_gate_result_strict_identity_time_counts_and_decision(self):
        head = "a" * 40; tree = "b" * 40
        with tempfile.TemporaryDirectory() as tmp:
            pathlib.Path(tmp, "stdout.log").write_bytes(b"c")
            pathlib.Path(tmp, "stderr.log").write_bytes(b"d")
            pathlib.Path(tmp, "stdout.log").chmod(0o644)
            pathlib.Path(tmp, "stderr.log").chmod(0o644)
            row = {
                "schema": "gate-row.v16", "gate_id": "G-TARGETED", "entrypoint_id": "EP", "stage": "targeted", "decision": "allow",
                "expected_head": head, "actual_head": head, "tree_sha": tree, "dirty": False, "snapshot_mode": "", "snapshot_sha256": "",
                "command": ["python3", "-c", "pass"], "cwd": ".", "runtime": "python-stdlib-offline", "config": "fixture",
                "started_at": "2026-07-31T00:00:00Z", "ended_at": "2026-07-31T00:00:01Z", "elapsed_sec": 1.0, "exit_status": 0,
                "counts": {"total": 1, "ran": 1, "passed": 1, "failed": 0, "skipped": 0, "xfail": 0, "unknown": 0},
                "log_paths": ["stdout.log", "stderr.log"], "log_shas": [hashlib.sha256(b"c").hexdigest(), hashlib.sha256(b"d").hexdigest()], "log_sizes": [1, 1], "log_modes": [0o644, 0o644],
                "parse_error": "", "privacy_error": "", "identity_error": "", "survivor": False, "timed_out": False, "term_sent": False, "kill_sent": False,
            }
            valid = {"schema": "gate-result.v16", "gate_id": "G-TARGETED", "stage": "targeted", "decision": "allow", "expected_head": head, "actual_head": head, "tree_sha": tree, "dirty": False, "snapshot_mode": "", "snapshot_sha256": "", "started_at": "2026-07-31T00:00:00Z", "ended_at": "2026-07-31T00:00:01Z", "elapsed_sec": 1.0, "rows": [row]}
            self.assertEqual(validate_gate_result(valid, expected_head=head, expected_tree=tree, artifact_root=pathlib.Path(tmp))["decision"], "allow")
            for mutation in (
                {"started_at": "2026-07-31T00:00:02Z"},
                {"elapsed_sec": 0.0},
                {"dirty": "false"},
                {"rows": [{**row, "counts": {**row["counts"], "ran": 0}}]},
                {"rows": [{**row, "decision": "deny"}]},
                {"actual_head": "e" * 40},
                {"tree_sha": "f" * 40},
            ):
                candidate = {**valid, **mutation}
                with self.assertRaises(GateRunError):
                    validate_gate_result(candidate, expected_head=head, expected_tree=tree, artifact_root=pathlib.Path(tmp))

    def test_spark_exact_three_and_dispositions(self):
        requests = audit_requests(MISSION)
        results = [result_for(r, i) for i, r in enumerate(requests)]
        self.assertEqual(len(validate_bundle(requests, results)), 3)
        two = copy.deepcopy(MISSION); two["spark_audits"] = two["spark_audits"][:2]
        two_requests = audit_requests(two)
        self.assertEqual(len(validate_bundle(two_requests, [result_for(r, i) for i, r in enumerate(two_requests)])), 2)
        none = copy.deepcopy(MISSION); none["spark_audits"] = []
        self.assertEqual(validate_bundle(audit_requests(none), []), [])
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
        # The author-side renderer can never synthesize the Independent gate;
        # a separately ingested Sol artifact is required before APPROVE.
        self.assertIsNone(packet["verdict"])
        collision = copy.deepcopy(packet); collision["reviewer_login"] = "Qian9921"
        with self.assertRaises(TraceError):
            validate_review_packet(collision)
        incomplete = render_pr_trace(mission_id="V16-PRODUCTIVITY", base_sha=BASE, head_sha=HEAD, tree_sha=TREE, checks=checks, findings=[], closures={}, reviewed_scope=["codex/v16"], unreviewed_scope=["docs"])
        self.assertIsNone(incomplete["packet"]["verdict"])

    def test_formal_initial_binds_packet_evidence_findings_and_policy(self):
        packet = trace_packet()
        artifact = formal_artifact(packet)
        approved = ingest(packet, artifact)
        self.assertEqual(approved["verdict"], "APPROVE")
        self.assertEqual(approved["findings"], artifact["findings"])
        self.assertEqual(approved["closures"], artifact["closures"])

        # A bare approval or an old compatibility receipt cannot enter v16.
        with self.assertRaisesRegex(TraceError, "formal bound"):
            ingest_independent_artifact(
                packet,
                {"schema": "independent-review.v16", "verdict": "APPROVE"},
                expected_dispatch_transcript_sha256=LINEAGE["dispatch_transcript_sha256"],
                expected_task_id=LINEAGE["task_id"],
                expected_parent_task_id=LINEAGE["parent_task_id"],
                expected_sender=LINEAGE["sender"],
            )

        wrong_packet = copy.deepcopy(artifact)
        wrong_packet["review_packet_sha256"] = sha(51)
        seal_artifact(wrong_packet)
        with self.assertRaisesRegex(TraceError, "decision basis"):
            ingest(packet, wrong_packet)

        wrong_evidence = copy.deepcopy(artifact)
        wrong_evidence["evidence_bundle_sha256"] = sha(52)
        seal_artifact(wrong_evidence)
        with self.assertRaisesRegex(TraceError, "decision basis"):
            ingest(packet, wrong_evidence)

        wrong_findings_hash = copy.deepcopy(artifact)
        wrong_findings_hash["findings_sha256"] = sha(53)
        wrong_findings_hash["artifact_sha256"] = ""
        wrong_findings_hash["artifact_sha256"] = canonical_sha256(wrong_findings_hash)
        with self.assertRaisesRegex(TraceError, "findings hash"):
            ingest(packet, wrong_findings_hash)

        author_contextual = copy.deepcopy(artifact)
        author_contextual["context_mode"] = "author_contextual"
        seal_artifact(author_contextual)
        with self.assertRaisesRegex(TraceError, "context mode"):
            ingest(packet, author_contextual)

        same_task_as_parent = copy.deepcopy(artifact)
        same_task_as_parent["dispatch_lineage"]["task_id"] = same_task_as_parent["dispatch_lineage"]["parent_task_id"]
        seal_artifact(same_task_as_parent)
        with self.assertRaisesRegex(TraceError, "task must differ"):
            ingest(packet, same_task_as_parent)

        malformed_policy = copy.deepcopy(packet["decision_basis"])
        malformed_policy["reviewer_model"] = "gpt-5.6-terra"
        with self.assertRaisesRegex(TraceError, "risk/route/model/effort"):
            trace_packet(basis=malformed_policy)

        # The policy is supplied by the caller and must agree with the packet.
        with self.assertRaisesRegex(TraceError, "caller policy"):
            ingest_independent_artifact(
                packet,
                artifact,
                expected_dispatch_transcript_sha256=LINEAGE["dispatch_transcript_sha256"],
                expected_task_id=LINEAGE["task_id"],
                expected_parent_task_id=LINEAGE["parent_task_id"],
                expected_sender=LINEAGE["sender"],
                expected_review_risk="low",
                expected_reviewer_route="general",
                expected_reviewer_model="gpt-5.6-sol",
                expected_reasoning_effort="xhigh",
            )

    def test_generic_nonclosure_formal_ingestion_remains_compatible(self):
        bundle = evidence_bundle(
            head=HEAD,
            tree=TREE,
            receipt=None,
            closure_aware=False,
        )
        packet = trace_packet(
            basis=decision_basis(1, closure_aware=False),
            bundle=bundle,
        )
        artifact = formal_artifact(packet, findings=[])
        approved = ingest(
            packet,
            artifact,
            closure_binding_receipt=None,
            evidence_bundle=bundle,
        )
        self.assertEqual(approved["verdict"], "APPROVE")
        self.assertNotIn("closure_authority", approved["decision_basis"])

    def test_initial_fixed_finding_requires_evidence_bound_continuation(self):
        bundle = evidence_bundle(
            head=HEAD,
            tree=TREE,
            receipt=None,
            closure_aware=False,
        )
        packet = trace_packet(
            basis=decision_basis(1, closure_aware=False),
            bundle=bundle,
        )
        artifact = formal_artifact(
            packet,
            verdict="REQUEST_CHANGES",
            findings=[full_finding("P1-1", severity="P1", disposition="OPEN")],
        )
        # Simulate a caller mutating and resealing an otherwise valid artifact;
        # ingestion must still reject it because an initial clean-room review
        # has no prior finding or executable closure evidence to bind FIXED.
        artifact["findings"][0]["disposition"] = "FIXED"
        artifact["closures"]["P1-1"] = "FIXED"
        artifact["verdict"] = "APPROVE"
        seal_artifact(artifact)
        with self.assertRaisesRegex(
            TraceError,
            "initial review cannot mark findings FIXED",
        ):
            ingest(
                packet,
                artifact,
                closure_binding_receipt=None,
                evidence_bundle=bundle,
            )

    def test_delta_continuation_requires_prior_lineage_new_run_and_frozen_basis(self):
        initial_packet = trace_packet()
        initial = formal_artifact(
            initial_packet,
            verdict="REQUEST_CHANGES",
            findings=[full_finding("P1-1", severity="P1", disposition="OPEN")],
            run_id="review-run-1",
        )
        next_head = "1" * 40
        next_tree = "2" * 40
        next_basis = copy.deepcopy(initial_packet["decision_basis"])
        next_basis["diff_sha256"] = sha(62)
        next_basis["evidence_bundle_sha256"] = sha(64)
        next_basis["evidence_denominator"] = 3
        next_packet = trace_packet(head=next_head, tree=next_tree, basis=next_basis)
        continuation = formal_artifact(
            next_packet,
            mode="delta_continuation",
            findings=[],
            prior=initial,
            run_id="review-run-2",
        )
        with self.assertRaisesRegex(TraceError, "prior active finding omitted"):
            ingest(next_packet, continuation, prior_artifact=initial)
        closure = formal_artifact(
            next_packet,
            mode="delta_continuation",
            prior=initial,
            run_id="review-run-closure",
            close_prior=True,
        )
        self.assertEqual(ingest(next_packet, closure, prior_artifact=initial)["verdict"], "APPROVE")
        self.assertEqual(ingest(initial_packet, initial)["verdict"], "REQUEST_CHANGES")

        wrong_delta = copy.deepcopy(continuation)
        with self.assertRaisesRegex(TraceError, "delta SHA mismatch"):
            ingest(next_packet, wrong_delta, prior_artifact=initial, expected_delta_sha256=sha(65))

        without_prior = copy.deepcopy(continuation)
        without_prior["prior_review_artifact_sha256"] = None
        without_prior["prior_head_sha"] = None
        without_prior["delta_sha256"] = None
        seal_artifact(without_prior)
        with self.assertRaisesRegex(TraceError, "requires prior"):
            ingest(next_packet, without_prior)

        same_run = copy.deepcopy(continuation)
        same_run["run_id"] = initial["run_id"]
        seal_artifact(same_run)
        with self.assertRaisesRegex(TraceError, "distinct run"):
            ingest(next_packet, same_run, prior_artifact=initial)

        different_task = copy.deepcopy(continuation)
        different_task["dispatch_lineage"]["task_id"] = "/root/different-reviewer-task"
        different_task["dispatch_lineage"]["sender"] = "/root/different-reviewer-task"
        seal_artifact(different_task)
        with self.assertRaisesRegex(TraceError, "reviewer task mismatch"):
            ingest(next_packet, different_task, prior_artifact=initial)

        inherited_context = copy.deepcopy(continuation)
        inherited_context["fork_turns"] = "all"
        seal_artifact(inherited_context)
        with self.assertRaisesRegex(TraceError, "fork_turns=none"):
            ingest(next_packet, inherited_context, prior_artifact=initial)

        inherited_p1 = formal_artifact(
            next_packet,
            mode="delta_continuation",
            verdict="REQUEST_CHANGES",
            findings=[full_finding("P1-1", severity="P1", disposition="OPEN")],
            prior=initial,
            run_id="review-run-inherited-p1",
        )
        self.assertEqual(ingest(next_packet, inherited_p1, prior_artifact=initial)["verdict"], "REQUEST_CHANGES")

        changed_basis = copy.deepcopy(next_basis)
        changed_basis["acceptance_envelope_sha256"] = sha(71)
        changed_packet = trace_packet(head=next_head, tree=next_tree, basis=changed_basis)
        changed_acceptance = formal_artifact(
            changed_packet,
            mode="delta_continuation",
            findings=[],
            prior=initial,
            run_id="review-run-3",
        )
        with self.assertRaisesRegex(TraceError, "frozen review basis"):
            ingest(changed_packet, changed_acceptance, prior_artifact=initial)

        unadmitted_p1 = formal_artifact(
            next_packet,
            mode="delta_continuation",
            verdict="REQUEST_CHANGES",
            findings=[
                full_finding("P1-1", severity="P1", disposition="OPEN"),
                full_finding("P1-2", severity="P1", disposition="OPEN"),
            ],
            prior=initial,
            run_id="review-run-p1",
        )
        with self.assertRaisesRegex(TraceError, "new active P1 requires blocker admission"):
            ingest(next_packet, unadmitted_p1, prior_artifact=initial)

    def test_fixed_closure_is_bound_to_current_case_row_log_and_result(self):
        initial_packet = trace_packet()
        initial = formal_artifact(
            initial_packet,
            verdict="REQUEST_CHANGES",
            findings=[full_finding("P1-1", severity="P1", disposition="OPEN")],
            run_id="closure-run-1",
        )
        next_basis = copy.deepcopy(initial_packet["decision_basis"])
        next_basis["diff_sha256"] = sha(302)
        next_packet = trace_packet(head="1" * 40, tree="2" * 40, basis=next_basis)
        closure = formal_artifact(
            next_packet,
            mode="delta_continuation",
            prior=initial,
            close_prior=True,
            run_id="closure-run-2",
        )
        self.assertEqual(ingest(next_packet, closure, prior_artifact=initial)["verdict"], "APPROVE")

        wrong_row = copy.deepcopy(closure)
        wrong_row["closure_matrix"]["P1-1"]["evidence_ref"]["evidence_row_sha256"] = "b" * 64
        wrong_row["closure_matrix_sha256"] = canonical_sha256(wrong_row["closure_matrix"])
        seal_artifact(wrong_row)
        with self.assertRaisesRegex(TraceError, "row/log identity"):
            ingest(next_packet, wrong_row, prior_artifact=initial)

        wrong_result = copy.deepcopy(closure)
        wrong_result["closure_matrix"]["P1-1"]["counterexample_recheck"]["result_sha256"] = "c" * 64
        wrong_result["closure_matrix_sha256"] = canonical_sha256(wrong_result["closure_matrix"])
        seal_artifact(wrong_result)
        with self.assertRaisesRegex(TraceError, "original executable result"):
            ingest(next_packet, wrong_result, prior_artifact=initial)

        swapped_case = copy.deepcopy(closure)
        current_bundle = evidence_bundle(head=next_packet["head_sha"], tree=next_packet["tree_sha"])
        other_row = next(row for row in current_bundle["rows"] if row["finding_id"] == "P1-2")
        other_binding = next(
            binding for binding in closure_binding_receipt()["bindings"]
            if binding["finding_id"] == "P1-2"
        )
        swapped_entry = swapped_case["closure_matrix"]["P1-1"]
        swapped_entry["evidence_ref"] = {
            "case_id": other_row["case_id"],
            "evidence_row_sha256": canonical_sha256(other_row),
            "log_sha256": other_row["log_sha256"],
            "binding_sha256": other_binding["binding_sha256"],
        }
        swapped_entry["counterexample_recheck"] = {
            "case_id": other_row["case_id"],
            "evidence_row_sha256": canonical_sha256(other_row),
            "log_sha256": other_row["log_sha256"],
            "binding_sha256": other_binding["binding_sha256"],
            "counterexample_sha256": _counterexample_sha256(initial["findings"][0]["counterexample"]),
            "kind": "EXECUTABLE_RESULT",
            "result_sha256": _evidence_result_sha256(other_row),
        }
        swapped_case["closure_matrix_sha256"] = canonical_sha256(swapped_case["closure_matrix"])
        seal_artifact(swapped_case)
        with self.assertRaisesRegex(TraceError, "frozen finding plan"):
            ingest(next_packet, swapped_case, prior_artifact=initial)

        stale_bundle = evidence_bundle(head=initial_packet["head_sha"], tree=initial_packet["tree_sha"])
        with self.assertRaisesRegex(TraceError, "evidence bundle hash mismatch"):
            ingest(next_packet, closure, prior_artifact=initial, evidence_bundle=stale_bundle)

    def test_fixed_closure_relabel_and_reseal_cannot_change_runner_binding(self):
        receipt = closure_binding_receipt()
        initial_packet = trace_packet()
        initial = formal_artifact(
            initial_packet,
            verdict="REQUEST_CHANGES",
            findings=[full_finding("P1-1", severity="P1", disposition="OPEN")],
            run_id="relabel-run-1",
        )
        malicious_bundle = evidence_bundle(
            head="1" * 40,
            tree="2" * 40,
            receipt=receipt,
        )
        row_b = next(
            row for row in malicious_bundle["rows"]
            if row["finding_id"] == "P1-2"
        )
        # Preserve B's actual gate/stage/entrypoint/log/head/tree and runner
        # binding set.  Only the caller-authored labels are rewritten to A.
        row_b["finding_id"] = "P1-1"
        row_b["counterexample_sha256"] = _counterexample_sha256(
            initial["findings"][0]["counterexample"]
        )
        malicious_bundle["envelope_sha256"] = ""
        malicious_bundle["envelope_sha256"] = canonical_sha256(malicious_bundle)
        next_basis = copy.deepcopy(initial_packet["decision_basis"])
        next_basis["diff_sha256"] = sha(402)
        malicious_packet = trace_packet(
            head="1" * 40,
            tree="2" * 40,
            basis=next_basis,
            bundle=malicious_bundle,
        )
        malicious = formal_artifact(
            malicious_packet,
            mode="delta_continuation",
            prior=initial,
            close_prior=True,
            run_id="relabel-run-2",
            closure_receipt=receipt,
        )
        binding_b = next(
            binding for binding in receipt["bindings"]
            if binding["finding_id"] == "P1-2"
        )
        entry = malicious["closure_matrix"]["P1-1"]
        entry["evidence_ref"] = {
            "case_id": row_b["case_id"],
            "evidence_row_sha256": canonical_sha256(row_b),
            "log_sha256": row_b["log_sha256"],
            "binding_sha256": binding_b["binding_sha256"],
        }
        entry["counterexample_recheck"] = {
            "case_id": row_b["case_id"],
            "evidence_row_sha256": canonical_sha256(row_b),
            "log_sha256": row_b["log_sha256"],
            "binding_sha256": binding_b["binding_sha256"],
            "counterexample_sha256": _counterexample_sha256(
                initial["findings"][0]["counterexample"]
            ),
            "kind": "EXECUTABLE_RESULT",
            "result_sha256": _evidence_result_sha256(row_b),
        }
        malicious["closure_matrix_sha256"] = canonical_sha256(
            malicious["closure_matrix"]
        )
        seal_artifact(malicious)
        with self.assertRaisesRegex(TraceError, "frozen finding plan"):
            ingest(
                malicious_packet,
                malicious,
                prior_artifact=initial,
                evidence_bundle=malicious_bundle,
                closure_binding_receipt=receipt,
                expected_closure_binding_receipt_sha256=receipt["receipt_sha256"],
                expected_closure_plan_sha256=receipt["closure_plan_sha256"],
            )

    def test_fixed_closure_receipt_and_free_hash_substitution_cannot_override_basis_authority(self):
        original_receipt = closure_binding_receipt()
        initial_packet = trace_packet(
            basis=decision_basis(1, closure_receipt=original_receipt),
        )
        initial = formal_artifact(
            initial_packet,
            verdict="REQUEST_CHANGES",
            findings=[full_finding("P1-1", severity="P1", disposition="OPEN")],
            run_id="authority-substitution-run-1",
        )

        substituted_receipt = copy.deepcopy(original_receipt)
        substituted_receipt["compiled_plan_sha256"] = "f" * 64
        substituted_receipt["closure_plan_sha256"] = "0" * 64
        substituted_receipt["normalized_source_artifacts"][0][
            "artifact_sha256"
        ] = "1" * 64
        substituted_receipt["receipt_sha256"] = ""
        substituted_receipt["receipt_sha256"] = canonical_sha256(
            substituted_receipt
        )
        substituted_bundle = evidence_bundle(
            head="1" * 40,
            tree="2" * 40,
            receipt=substituted_receipt,
        )

        # The exact pre-run authority stays frozen in the decision basis.  The
        # attacker reseals every mutable post-run layer and supplies matching
        # substituted compatibility hashes; none of those become the root.
        next_basis = copy.deepcopy(initial_packet["decision_basis"])
        next_basis["diff_sha256"] = sha(407)
        substituted_packet = trace_packet(
            head="1" * 40,
            tree="2" * 40,
            basis=next_basis,
            bundle=substituted_bundle,
        )
        substituted_artifact = formal_artifact(
            substituted_packet,
            mode="delta_continuation",
            prior=initial,
            close_prior=True,
            run_id="authority-substitution-run-2",
            closure_receipt=substituted_receipt,
        )
        with self.assertRaisesRegex(
            TraceError,
            "compatibility closure identity contradicts decision basis",
        ):
            ingest(
                substituted_packet,
                substituted_artifact,
                prior_artifact=initial,
                evidence_bundle=substituted_bundle,
                closure_binding_receipt=substituted_receipt,
                expected_closure_binding_receipt_sha256=(
                    substituted_receipt["receipt_sha256"]
                ),
                expected_closure_plan_sha256=(
                    substituted_receipt["closure_plan_sha256"]
                ),
            )

    def test_fixed_closure_authority_is_required_and_source_bound(self):
        receipt = closure_binding_receipt()
        initial_packet = trace_packet(
            basis=decision_basis(1, closure_receipt=receipt),
        )
        initial = formal_artifact(
            initial_packet,
            verdict="REQUEST_CHANGES",
            findings=[full_finding("P1-1", severity="P1", disposition="OPEN")],
            run_id="authority-source-run-1",
        )
        next_basis = copy.deepcopy(initial_packet["decision_basis"])
        next_basis["diff_sha256"] = sha(409)

        missing_basis = copy.deepcopy(next_basis)
        del missing_basis["closure_authority"]
        missing_packet = trace_packet(
            head="1" * 40,
            tree="2" * 40,
            basis=missing_basis,
        )
        missing_artifact = formal_artifact(
            missing_packet,
            mode="delta_continuation",
            prior=initial,
            close_prior=True,
            run_id="authority-source-run-missing",
            closure_receipt=receipt,
        )
        with self.assertRaisesRegex(
            TraceError,
            "FIXED closure requires decision-basis pre-execution closure authority",
        ):
            ingest(
                missing_packet,
                missing_artifact,
                prior_artifact=initial,
                closure_binding_receipt=None,
            )

        stale_source_basis = copy.deepcopy(next_basis)
        authority = stale_source_basis["closure_authority"]
        authority["normalized_source_artifacts"][0][
            "artifact_sha256"
        ] = "2" * 64
        authority["authority_sha256"] = ""
        authority["authority_sha256"] = canonical_sha256(authority)
        stale_source_packet = trace_packet(
            head="1" * 40,
            tree="2" * 40,
            basis=stale_source_basis,
        )
        stale_source_artifact = formal_artifact(
            stale_source_packet,
            mode="delta_continuation",
            prior=initial,
            close_prior=True,
            run_id="authority-source-run-stale",
            closure_receipt=receipt,
        )
        with self.assertRaisesRegex(
            TraceError,
            "closure receipt contradicts decision-basis authority",
        ):
            ingest(
                stale_source_packet,
                stale_source_artifact,
                prior_artifact=initial,
                closure_binding_receipt=receipt,
            )

    def test_fixed_closure_requires_exact_caller_receipt_and_plan_hashes(self):
        receipt = closure_binding_receipt()
        initial_packet = trace_packet()
        initial = formal_artifact(
            initial_packet,
            verdict="REQUEST_CHANGES",
            findings=[full_finding("P1-1", severity="P1", disposition="OPEN")],
            run_id="receipt-run-1",
        )
        next_basis = copy.deepcopy(initial_packet["decision_basis"])
        next_basis["diff_sha256"] = sha(412)
        next_packet = trace_packet(head="1" * 40, tree="2" * 40, basis=next_basis)
        closure = formal_artifact(
            next_packet,
            mode="delta_continuation",
            prior=initial,
            close_prior=True,
            run_id="receipt-run-2",
            closure_receipt=receipt,
        )
        with self.assertRaisesRegex(
            TraceError,
            "decision-basis pre-execution closure authority",
        ):
            ingest(
                next_packet,
                closure,
                prior_artifact=initial,
                closure_binding_receipt=None,
            )

        stale_receipt = copy.deepcopy(receipt)
        stale_receipt["compiled_plan_sha256"] = "f" * 64
        stale_receipt["receipt_sha256"] = ""
        stale_receipt["receipt_sha256"] = canonical_sha256(stale_receipt)
        with self.assertRaisesRegex(
            TraceError,
            "closure receipt contradicts decision-basis authority",
        ):
            ingest(
                next_packet,
                closure,
                prior_artifact=initial,
                closure_binding_receipt=stale_receipt,
                expected_closure_binding_receipt_sha256=receipt["receipt_sha256"],
                expected_closure_plan_sha256=receipt["closure_plan_sha256"],
            )

        wrong_plan = closure_binding_receipt(closure_plan_sha256="f" * 64)
        wrong_plan_bundle = evidence_bundle(
            head=next_packet["head_sha"],
            tree=next_packet["tree_sha"],
            receipt=wrong_plan,
        )
        with self.assertRaisesRegex(
            TraceError,
            "compatibility closure identity contradicts decision basis",
        ):
            ingest(
                next_packet,
                closure,
                prior_artifact=initial,
                evidence_bundle=wrong_plan_bundle,
                closure_binding_receipt=wrong_plan,
                expected_closure_binding_receipt_sha256=wrong_plan["receipt_sha256"],
                expected_closure_plan_sha256=receipt["closure_plan_sha256"],
            )

    def test_shared_evidence_row_requires_exact_sorted_binding_set(self):
        receipt = closure_binding_receipt(shared=True)
        initial_packet = trace_packet()
        initial = formal_artifact(
            initial_packet,
            verdict="REQUEST_CHANGES",
            findings=[
                full_finding("P1-1", severity="P1", disposition="OPEN"),
                full_finding("P1-2", severity="P1", disposition="OPEN"),
            ],
            run_id="shared-run-1",
        )
        bundle = evidence_bundle(
            head="1" * 40,
            tree="2" * 40,
            receipt=receipt,
        )
        next_basis = copy.deepcopy(initial_packet["decision_basis"])
        next_basis["diff_sha256"] = sha(422)
        next_basis["closure_authority"] = (
            build_pre_execution_closure_authority(receipt)
        )
        packet = trace_packet(
            head="1" * 40,
            tree="2" * 40,
            basis=next_basis,
            bundle=bundle,
        )
        closure = formal_artifact(
            packet,
            mode="delta_continuation",
            prior=initial,
            close_prior=True,
            run_id="shared-run-2",
            closure_receipt=receipt,
        )
        self.assertEqual(
            ingest(
                packet,
                closure,
                prior_artifact=initial,
                evidence_bundle=bundle,
                closure_binding_receipt=receipt,
            )["verdict"],
            "APPROVE",
        )

        missing_binding_bundle = copy.deepcopy(bundle)
        missing_binding_bundle["rows"][0]["closure_binding_sha256s"].pop()
        missing_binding_bundle["envelope_sha256"] = ""
        missing_binding_bundle["envelope_sha256"] = canonical_sha256(
            missing_binding_bundle
        )
        missing_packet = trace_packet(
            head="1" * 40,
            tree="2" * 40,
            basis=next_basis,
            bundle=missing_binding_bundle,
        )
        missing = formal_artifact(
            missing_packet,
            mode="delta_continuation",
            prior=initial,
            close_prior=True,
            run_id="shared-run-3",
            closure_receipt=receipt,
        )
        with self.assertRaisesRegex(TraceError, "closure binding receipt mismatch"):
            ingest(
                missing_packet,
                missing,
                prior_artifact=initial,
                evidence_bundle=missing_binding_bundle,
                closure_binding_receipt=receipt,
            )

    def test_non_git_formal_closure_rejects_stale_snapshot_and_bare_rows(self):
        snapshot_a = "a" * 64
        snapshot_b = "b" * 64
        initial_basis = decision_basis(1)
        initial_basis.update({
            "identity_mode": "non-git-snapshot",
            "snapshot_sha256": snapshot_a,
            "prior_snapshot_sha256": None,
        })
        initial_packet = trace_packet(basis=initial_basis)
        initial = formal_artifact(
            initial_packet,
            verdict="REQUEST_CHANGES",
            findings=[full_finding("P1-1", severity="P1", disposition="OPEN")],
            run_id="non-git-run-1",
        )
        delta_sha256 = identity_delta_sha256(
            identity_mode="non-git-snapshot",
            base_sha=initial_packet["base_sha"],
            prior_head_sha=initial_packet["head_sha"],
            head_sha=initial_packet["head_sha"],
            prior_snapshot_sha256=snapshot_a,
            snapshot_sha256=snapshot_b,
        )
        current_basis = copy.deepcopy(initial_packet["decision_basis"])
        current_basis.update({
            "snapshot_sha256": snapshot_b,
            "prior_snapshot_sha256": snapshot_a,
            "prior_head_sha": initial_packet["head_sha"],
            "delta_sha256": delta_sha256,
        })
        current_packet = trace_packet(basis=current_basis)
        current = formal_artifact(
            current_packet,
            mode="delta_continuation",
            prior=initial,
            close_prior=True,
            run_id="non-git-run-2",
        )
        current["delta_sha256"] = delta_sha256
        seal_artifact(current)
        self.assertEqual(ingest(current_packet, current, prior_artifact=initial)["verdict"], "APPROVE")

        current_bundle = evidence_bundle(
            head=current_packet["head_sha"],
            tree=current_packet["tree_sha"],
            identity_mode="non-git-snapshot",
            snapshot_sha256=snapshot_b,
        )
        with self.assertRaisesRegex(TraceError, "canonical receipt-bound evidence envelope"):
            ingest(current_packet, current, prior_artifact=initial, evidence_bundle=current_bundle["rows"])

        stale_bundle = evidence_bundle(
            head=current_packet["head_sha"],
            tree=current_packet["tree_sha"],
            identity_mode="non-git-snapshot",
            snapshot_sha256=snapshot_a,
        )
        stale_packet = trace_packet(basis=current_basis, bundle=stale_bundle)
        stale = formal_artifact(
            stale_packet,
            mode="delta_continuation",
            prior=initial,
            close_prior=True,
            run_id="non-git-run-stale",
        )
        stale["delta_sha256"] = delta_sha256
        seal_artifact(stale)
        with self.assertRaisesRegex(TraceError, "bundle snapshot mismatch"):
            ingest(stale_packet, stale, prior_artifact=initial, evidence_bundle=stale_bundle)

    def test_escalated_fresh_requires_trigger_and_distinct_reviewer_task(self):
        prior_packet = trace_packet()
        prior = formal_artifact(
            prior_packet,
            verdict="REQUEST_CHANGES",
            findings=[full_finding("P1-1", severity="P1", disposition="OPEN")],
            run_id="review-run-1",
        )
        high_basis = copy.deepcopy(prior_packet["decision_basis"])
        high_basis["diff_sha256"] = sha(82)
        high_basis["evidence_bundle_sha256"] = sha(84)
        high_basis["evidence_denominator"] = 5
        high_basis["review_risk"] = "high"
        high_basis["reviewer_route"] = "high_risk"
        high_basis["reviewer_model"] = "gpt-5.6-sol"
        high_basis["reasoning_effort"] = "xhigh"
        high_basis["high_risk_triggers"] = ["security"]
        high_policy = {
            "required_stages": high_basis["required_stages"],
            "review_risk": "high",
            "reviewer_route": "high_risk",
            "reviewer_model": "gpt-5.6-sol",
            "reasoning_effort": "xhigh",
            "classifier_identity": high_basis["classifier_identity"],
            "high_risk_triggers": ["security"],
        }
        high_basis["review_policy_sha256"] = canonical_sha256(high_policy)
        escalated_packet = trace_packet(head="3" * 40, tree="4" * 40, basis=high_basis)
        escalated_lineage = {
            **LINEAGE,
            "task_id": "/root/escalated-fresh-review",
            "sender": "/root/escalated-fresh-review",
        }
        escalated = formal_artifact(
            escalated_packet,
            mode="escalated_fresh",
            findings=[],
            prior=prior,
            continuity="reviewer-B",
            run_id="review-run-escalated",
            lineage=escalated_lineage,
            trigger="RISK_ESCALATION",
            close_prior=True,
        )
        with self.assertRaisesRegex(TraceError, "caller proof"):
            ingest(
                escalated_packet,
                escalated,
                prior_artifact=prior,
                expected_escalation_trigger="RISK_ESCALATION",
            )
        approved = ingest(
            escalated_packet,
            escalated,
            prior_artifact=prior,
            expected_escalation_trigger="RISK_ESCALATION",
            expected_distinct_reviewer_task_id=escalated_lineage["task_id"],
            expected_prior_reviewer_task_id=prior["dispatch_lineage"]["task_id"],
        )
        self.assertEqual(approved["verdict"], "APPROVE")

    def test_escalated_fresh_path_drift_requires_matching_trigger_and_new_identity(self):
        prior_packet = trace_packet()
        prior = formal_artifact(prior_packet, findings=[], run_id="path-prior")
        basis = copy.deepcopy(prior_packet["decision_basis"])
        basis.update({"review_risk": "high", "reviewer_route": "high_risk", "reviewer_model": "gpt-5.6-sol", "reasoning_effort": "xhigh"})
        basis["high_risk_triggers"] = ["security"]
        basis["review_policy_sha256"] = canonical_sha256({
            "required_stages": basis["required_stages"], "review_risk": "high", "reviewer_route": "high_risk",
            "reviewer_model": "gpt-5.6-sol", "reasoning_effort": "xhigh", "classifier_identity": basis["classifier_identity"],
            "high_risk_triggers": basis["high_risk_triggers"],
        })
        packet = trace_packet(head="d" * 40, tree="e" * 40, basis=basis, reviewed_scope=["codex/v16", "tests"])
        lineage = {**LINEAGE, "task_id": "/root/path-review", "sender": "/root/path-review"}
        artifact = formal_artifact(packet, mode="escalated_fresh", findings=[], prior=prior, continuity="path-reviewer", run_id="path-run", lineage=lineage, trigger="PATH_SET_SCOPE_DRIFT")
        approved = ingest(packet, artifact, prior_artifact=prior, expected_escalation_trigger="PATH_SET_SCOPE_DRIFT", expected_distinct_reviewer_task_id=lineage["task_id"], expected_prior_reviewer_task_id=LINEAGE["task_id"])
        self.assertEqual(approved["verdict"], "APPROVE")
        wrong = copy.deepcopy(artifact)
        wrong["escalation_trigger"] = "ACCEPTANCE_ENVELOPE_DRIFT"
        seal_artifact(wrong)
        with self.assertRaisesRegex(TraceError, "no matching old/new drift"):
            ingest(packet, wrong, prior_artifact=prior, expected_escalation_trigger="ACCEPTANCE_ENVELOPE_DRIFT", expected_distinct_reviewer_task_id=lineage["task_id"], expected_prior_reviewer_task_id=LINEAGE["task_id"])

    def test_non_derivable_escalation_triggers_require_caller_evidence(self):
        prior_packet = trace_packet()
        prior = formal_artifact(prior_packet, findings=[], run_id="non-derivable-prior")
        basis = copy.deepcopy(prior_packet["decision_basis"])
        basis.update({"diff_sha256": sha(970), "evidence_bundle_sha256": sha(971), "review_risk": "high", "reviewer_route": "high_risk", "reviewer_model": "gpt-5.6-sol", "reasoning_effort": "xhigh", "high_risk_triggers": ["security"]})
        basis["review_policy_sha256"] = canonical_sha256({"required_stages": basis["required_stages"], "review_risk": "high", "reviewer_route": "high_risk", "reviewer_model": "gpt-5.6-sol", "reasoning_effort": "xhigh", "classifier_identity": basis["classifier_identity"], "high_risk_triggers": ["security"]})
        packet = trace_packet(head="f" * 40, tree="0" * 40, basis=basis)
        lineage = {**LINEAGE, "task_id": "/root/non-derivable-review", "sender": "/root/non-derivable-review"}
        for trigger in ("REVIEWER_PARTICIPATED", "LINEAGE_LOSS"):
            artifact = formal_artifact(packet, mode="escalated_fresh", findings=[], prior=prior, continuity=f"continuity-{trigger}", run_id=f"run-{trigger}", lineage=lineage, trigger=trigger)
            artifact["escalation_evidence_ref"] = sha(980 if trigger == "REVIEWER_PARTICIPATED" else 981)
            seal_artifact(artifact)
            approved = ingest(packet, artifact, prior_artifact=prior, expected_escalation_trigger=trigger, expected_distinct_reviewer_task_id=lineage["task_id"], expected_prior_reviewer_task_id=LINEAGE["task_id"])
            self.assertEqual(approved["verdict"], "APPROVE")
            missing = copy.deepcopy(artifact)
            missing["escalation_evidence_ref"] = ""
            seal_artifact(missing)
            with self.assertRaisesRegex(TraceError, "SHA-256|escalation evidence"):
                ingest(packet, missing, prior_artifact=prior, expected_escalation_trigger=trigger, expected_distinct_reviewer_task_id=lineage["task_id"], expected_prior_reviewer_task_id=LINEAGE["task_id"])

        governance = formal_artifact(packet, mode="escalated_fresh", findings=[], prior=prior, continuity="governance-continuity", run_id="governance-run", lineage=lineage, trigger="REVIEW_HOOK_ROUTING_GOVERNANCE_CHANGE")
        governance["escalation_evidence_ref"] = governance["diff_sha256"]
        seal_artifact(governance)
        self.assertEqual(ingest(packet, governance, prior_artifact=prior, expected_escalation_trigger="REVIEW_HOOK_ROUTING_GOVERNANCE_CHANGE", expected_distinct_reviewer_task_id=lineage["task_id"], expected_prior_reviewer_task_id=LINEAGE["task_id"])["verdict"], "APPROVE")
        wrong_governance = copy.deepcopy(governance)
        wrong_governance["escalation_evidence_ref"] = sha(999)
        seal_artifact(wrong_governance)
        with self.assertRaisesRegex(TraceError, "no matching old/new drift"):
            ingest(packet, wrong_governance, prior_artifact=prior, expected_escalation_trigger="REVIEW_HOOK_ROUTING_GOVERNANCE_CHANGE", expected_distinct_reviewer_task_id=lineage["task_id"], expected_prior_reviewer_task_id=LINEAGE["task_id"])

    def test_continuation_coverage_is_monotonic_over_the_frozen_scope(self):
        partial_basis = decision_basis(20)
        partial_packet = trace_packet(
            basis=partial_basis,
            reviewed_scope=["codex/v16"],
            unreviewed_scope=["tests"],
        )
        partial = formal_artifact(
            partial_packet,
            verdict="REQUEST_CHANGES",
            findings=[full_finding("P1-partial", severity="P1", disposition="OPEN")],
            run_id="partial-run-1",
        )
        complete_basis = copy.deepcopy(partial_basis)
        complete_basis["diff_sha256"] = sha(202)
        complete_basis["evidence_bundle_sha256"] = sha(204)
        complete_basis["evidence_denominator"] = 21
        complete_packet = trace_packet(
            head="5" * 40,
            tree="6" * 40,
            basis=complete_basis,
            reviewed_scope=["codex/v16", "tests"],
            unreviewed_scope=[],
        )
        complete = formal_artifact(
            complete_packet,
            mode="delta_continuation",
            findings=[],
            prior=partial,
            run_id="partial-run-2",
        )
        with self.assertRaisesRegex(TraceError, "prior active finding omitted"):
            ingest(complete_packet, complete, prior_artifact=partial)
        complete = formal_artifact(
            complete_packet,
            mode="delta_continuation",
            prior=partial,
            run_id="partial-run-closure",
            close_prior=True,
        )
        self.assertEqual(ingest(complete_packet, complete, prior_artifact=partial)["verdict"], "APPROVE")

        shrink_packet = trace_packet(
            head="7" * 40,
            tree="8" * 40,
            basis={**complete_basis, "diff_sha256": sha(212)},
            reviewed_scope=["tests"],
            unreviewed_scope=["codex/v16"],
        )
        shrink = formal_artifact(
            shrink_packet,
            mode="delta_continuation",
            verdict="REQUEST_CHANGES",
            findings=[full_finding("P1-shrink", severity="P1", disposition="OPEN")],
            prior=partial,
            run_id="partial-run-3",
        )
        with self.assertRaisesRegex(TraceError, "reopened or shrank"):
            ingest(shrink_packet, shrink, prior_artifact=partial)

        drift_packet = trace_packet(
            head="9" * 40,
            tree="a" * 40,
            basis={**complete_basis, "diff_sha256": sha(222)},
            reviewed_scope=["codex/v16", "new-path"],
            unreviewed_scope=[],
        )
        drift = formal_artifact(
            drift_packet,
            mode="delta_continuation",
            verdict="REQUEST_CHANGES",
            findings=[full_finding("P1-drift", severity="P1", disposition="OPEN")],
            prior=partial,
            run_id="partial-run-4",
        )
        with self.assertRaisesRegex(TraceError, "frozen scope"):
            ingest(drift_packet, drift, prior_artifact=partial)

        reopen_packet = trace_packet(
            head="b" * 40,
            tree="c" * 40,
            basis={**complete_basis, "diff_sha256": sha(232)},
            reviewed_scope=["codex/v16"],
            unreviewed_scope=["tests"],
        )
        reopen = formal_artifact(
            reopen_packet,
            mode="delta_continuation",
            verdict="REQUEST_CHANGES",
            findings=[full_finding("P1-reopen", severity="P1", disposition="OPEN", blocker_admission="DELTA_INTRODUCED")],
            prior=complete,
            run_id="partial-run-5",
        )
        with self.assertRaisesRegex(TraceError, "reopened or shrank"):
            ingest(reopen_packet, reopen, prior_artifact=complete)

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
        self.assertEqual(dashboard(metrics)["policy_targets"]["spark_audit_count"]["max"], 3)
        with self.assertRaises(MetricsError):
            collect_metrics(mission=MISSION, evidence=evidence, review=review, spark_results=sparks[:2], gate_runs=runs)
        no_audits = copy.deepcopy(MISSION); no_audits["spark_audits"] = []
        no_audit_metrics = collect_metrics(mission=no_audits, evidence=evidence, review=review, spark_results=[], gate_runs=runs)
        self.assertEqual(no_audit_metrics["spark_audit_count"], 0)

    def test_source_identity_guard_rejects_mutation(self):
        head = "a" * 40; tree = "b" * 40
        self.assertTrue(source_identity_guard((head, tree, False), (head, tree, False)))
        self.assertFalse(source_identity_guard((head, tree, False), ("c" * 40, tree, False)))
        self.assertFalse(source_identity_guard((head, tree, False), (head, tree, True)))


if __name__ == "__main__":
    unittest.main()

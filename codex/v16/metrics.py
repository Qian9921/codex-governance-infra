"""Artifact-derived productivity metrics and policy dashboard.

Metrics are deliberately boring: every value is derived from a validated
source artifact, every denominator is explicit and non-zero, and the public
validator rejects extra or fabricated fields.
"""
from __future__ import annotations

import math
from typing import Any, Mapping, Sequence

from .contracts import ContractError, _id, canonical_sha256, validate_counterexample_linkage, validate_mission


class MetricsError(ContractError):
    pass


METRIC_NAMES = ("first_pass_approval", "pre_review_blocker_capture", "review_rounds", "full_runs_per_head", "fresh_runs_per_head", "evidence_corrections", "writer_handoffs", "spark_audit_count", "spark_audit_latency_sec", "gate_elapsed_sec", "new_blocker_admissions")
METRIC_FIELDS = {"schema", "mission_id", "source_hash", *METRIC_NAMES}


def _finite(value: Any, path: str, *, minimum: float = 0.0) -> float:
    if type(value) not in (int, float) or isinstance(value, bool) or not math.isfinite(float(value)) or float(value) < minimum:
        raise MetricsError("finite non-negative number required", path)
    return float(value)


def _nonneg_int(value: Any, path: str) -> int:
    if type(value) is not int or value < 0:
        raise MetricsError("non-negative integer required", path)
    return value


def _rows(evidence: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    if not isinstance(evidence, Mapping):
        raise MetricsError("evidence envelope required")
    rows = evidence.get("rows")
    if not isinstance(rows, list) or not rows:
        raise MetricsError("non-empty evidence rows required")
    if evidence.get("schema") not in (None, "evidence-envelope.v16"):
        raise MetricsError("evidence schema")
    envelope_head = evidence.get("head_sha")
    envelope_tree = evidence.get("tree_sha")
    if evidence.get("schema") == "evidence-envelope.v16":
        if evidence.get("clean") is not True or not isinstance(envelope_head, str) or len(envelope_head) != 40 or any(c not in "0123456789abcdef" for c in envelope_head.lower()) or not isinstance(envelope_tree, str) or len(envelope_tree) != 40 or any(c not in "0123456789abcdef" for c in envelope_tree.lower()):
            raise MetricsError("current clean evidence identity required")
    for index, row in enumerate(rows):
        if not isinstance(row, Mapping) or row.get("stage") not in {"targeted", "full", "fresh"} or not isinstance(row.get("actual_head"), str) or len(row["actual_head"]) != 40 or any(c not in "0123456789abcdef" for c in row["actual_head"].lower()):
            raise MetricsError("evidence row identity required", f"$.rows[{index}]")
        if evidence.get("schema") == "evidence-envelope.v16" and (row["actual_head"] != envelope_head or row.get("tree_sha") != envelope_tree):
            raise MetricsError("evidence row/envelope identity mismatch", f"$.rows[{index}]")
        if row.get("decision", "allow") not in {"allow", "reused"}:
            raise MetricsError("only current green evidence may feed metrics", f"$.rows[{index}]")
    return rows


def _validate_review(review: Mapping[str, Any]) -> None:
    if not isinstance(review, Mapping) or type(review.get("round")) is not int or review["round"] < 1:
        raise MetricsError("validated review packet and positive round required")
    if review.get("verdict") not in {None, "APPROVE", "REQUEST_CHANGES"}:
        raise MetricsError("review verdict")
    findings = review.get("findings")
    if not isinstance(findings, list):
        raise MetricsError("review findings required")


def collect_metrics(*, mission: Mapping[str, Any], evidence: Mapping[str, Any], review: Mapping[str, Any], spark_results: Sequence[Mapping[str, Any]], gate_runs: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Derive metrics solely from current, identity-bound artifacts."""
    try:
        checked_mission = validate_mission(mission)
        validate_counterexample_linkage(checked_mission)
    except Exception as exc:
        raise MetricsError("validated mission required") from exc
    rows = _rows(evidence)
    _validate_review(review)
    full_rows = [r for r in rows if r.get("stage") == "full"]
    fresh_rows = [r for r in rows if r.get("stage") == "fresh"]
    if not full_rows or not fresh_rows:
        raise MetricsError("full/fresh evidence denominators must be non-zero")
    full_heads = {r["actual_head"] for r in full_rows}; fresh_heads = {r["actual_head"] for r in fresh_rows}
    if not full_heads or not fresh_heads:
        raise MetricsError("stage head denominators must be non-zero")
    corrections = sum(1 for r in rows if r.get("correction_of"))
    writer_ids = {r.get("writer_task_id") for r in rows if r.get("writer_task_id")}
    if not gate_runs:
        raise MetricsError("non-empty gate run denominator required")
    gate_elapsed = 0.0
    for index, run in enumerate(gate_runs):
        if not isinstance(run, Mapping):
            raise MetricsError("gate run object required", f"$.gate_runs[{index}]")
        gate_elapsed += _finite(run.get("elapsed_sec"), f"$.gate_runs[{index}].elapsed_sec")
    if not spark_results or len(spark_results) != 3:
        raise MetricsError("Spark audit denominator must be exactly three")
    spark_latency = 0.0
    for index, result in enumerate(spark_results):
        if not isinstance(result, Mapping):
            raise MetricsError("Spark result object required", f"$.spark_results[{index}]")
        spark_latency += _finite(result.get("elapsed_sec"), f"$.spark_results[{index}].elapsed_sec")
    findings = review["findings"]
    blockers = [f for f in findings if isinstance(f, Mapping) and (f.get("severity") == "P1" or f.get("label") == "BLOCKING")]
    admissions = sum(1 for f in findings if isinstance(f, Mapping) and f.get("attribution") in {"DELTA_INTRODUCED", "ORIGINAL_SCOPE_MISSED", "NEW_FALSIFIABLE_EVIDENCE"})
    source = {"mission": checked_mission, "evidence": dict(evidence), "review": dict(review), "spark_results": [dict(r) for r in spark_results], "gate_runs": [dict(r) for r in gate_runs]}
    metrics: dict[str, Any] = {
        "schema": "metrics.v16", "mission_id": checked_mission["mission_id"], "source_hash": canonical_sha256(source),
        "first_pass_approval": review.get("verdict") == "APPROVE" and review["round"] == 1,
        "pre_review_blocker_capture": len(blockers), "review_rounds": review["round"],
        "full_runs_per_head": len(full_rows) / len(full_heads), "fresh_runs_per_head": len(fresh_rows) / len(fresh_heads),
        "evidence_corrections": corrections, "writer_handoffs": max(0, len(writer_ids) - 1),
        "spark_audit_count": len(spark_results), "spark_audit_latency_sec": round(spark_latency, 6),
        "gate_elapsed_sec": round(gate_elapsed, 6), "new_blocker_admissions": admissions,
    }
    return validate_metrics(metrics, source_bundle=source)


def validate_metrics_shape(metrics: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(metrics, Mapping) or set(metrics) != METRIC_FIELDS:
        raise MetricsError("exact metrics.v16 fields required")
    if metrics.get("schema") != "metrics.v16":
        raise MetricsError("metrics.v16 schema required")
    _id(metrics.get("mission_id"), "$.mission_id")
    source_hash = metrics.get("source_hash")
    if not isinstance(source_hash, str) or len(source_hash) != 64 or any(c not in "0123456789abcdef" for c in source_hash):
        raise MetricsError("source_hash must be SHA-256", "$.source_hash")
    if type(metrics.get("first_pass_approval")) is not bool:
        raise MetricsError("first_pass_approval boolean required")
    for name in ("pre_review_blocker_capture", "review_rounds", "evidence_corrections", "writer_handoffs", "spark_audit_count", "new_blocker_admissions"):
        _nonneg_int(metrics.get(name), f"$.{name}")
    if metrics["review_rounds"] < 1 or metrics["spark_audit_count"] != 3:
        raise MetricsError("review/Spark denominator contract")
    for name in ("full_runs_per_head", "fresh_runs_per_head", "spark_audit_latency_sec", "gate_elapsed_sec"):
        _finite(metrics.get(name), f"$.{name}")
    return dict(metrics)


def validate_metrics(metrics: Mapping[str, Any], *, source_bundle: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Acceptance validator: recompute metrics from the validated source bundle."""
    if source_bundle is None:
        raise MetricsError("source bundle required for metrics acceptance")
    checked = validate_metrics_shape(metrics)
    source = source_bundle
    if source is not None:
        if not isinstance(source, Mapping) or set(source) != {"mission", "evidence", "review", "spark_results", "gate_runs"}:
            raise MetricsError("validated metrics source bundle required", "$.source")
        try:
            checked_mission = validate_mission(source["mission"]); validate_counterexample_linkage(checked_mission)
            rows = _rows(source["evidence"]); _validate_review(source["review"])
        except Exception as exc:
            raise MetricsError("validated metrics source bundle required", "$.source") from exc
        full_rows = [r for r in rows if r.get("stage") == "full"]; fresh_rows = [r for r in rows if r.get("stage") == "fresh"]
        full_heads = {r["actual_head"] for r in full_rows}; fresh_heads = {r["actual_head"] for r in fresh_rows}
        if not full_rows or not fresh_rows or not full_heads or not fresh_heads:
            raise MetricsError("source metric denominators must be non-zero", "$.source.evidence")
        gate_runs = source["gate_runs"]; sparks = source["spark_results"]
        if not isinstance(gate_runs, Sequence) or not gate_runs or not isinstance(sparks, Sequence) or len(sparks) != 3:
            raise MetricsError("source gate/Spark denominators invalid", "$.source")
        blockers = [f for f in source["review"]["findings"] if isinstance(f, Mapping) and (f.get("severity") == "P1" or f.get("label") == "BLOCKING")]
        writer_ids = {r.get("writer_task_id") for r in rows if r.get("writer_task_id")}
        expected: dict[str, Any] = {
            "first_pass_approval": source["review"].get("verdict") == "APPROVE" and source["review"]["round"] == 1,
            "pre_review_blocker_capture": len(blockers), "review_rounds": source["review"]["round"],
            "full_runs_per_head": len(full_rows) / len(full_heads), "fresh_runs_per_head": len(fresh_rows) / len(fresh_heads),
            "evidence_corrections": sum(1 for r in rows if r.get("correction_of")), "writer_handoffs": max(0, len(writer_ids) - 1),
            "spark_audit_count": len(sparks), "spark_audit_latency_sec": round(sum(_finite(r.get("elapsed_sec"), "$.source.spark_results[].elapsed_sec") for r in sparks), 6),
            "gate_elapsed_sec": round(sum(_finite(r.get("elapsed_sec"), "$.source.gate_runs[].elapsed_sec") for r in gate_runs), 6),
            "new_blocker_admissions": sum(1 for f in source["review"]["findings"] if isinstance(f, Mapping) and f.get("attribution") in {"DELTA_INTRODUCED", "ORIGINAL_SCOPE_MISSED", "NEW_FALSIFIABLE_EVIDENCE"}),
        }
        if canonical_sha256(source) != checked["source_hash"] or any(checked[name] != value for name, value in expected.items()):
            raise MetricsError("metrics do not match validated source bundle")
    return checked


def dashboard(metrics: Mapping[str, Any], targets: Mapping[str, Any] | None = None, *, source_bundle: Mapping[str, Any] | None = None) -> dict[str, Any]:
    checked = validate_metrics_shape(metrics) if source_bundle is None else validate_metrics(metrics, source_bundle=source_bundle)
    policy = dict(targets or {"first_pass_approval": True, "pre_review_blocker_capture": {"min": 0}, "review_rounds": {"max": 2}, "full_runs_per_head": {"max": 1}, "fresh_runs_per_head": {"max": 1}, "evidence_corrections": {"max": 0}, "writer_handoffs": {"max": 0}, "spark_audit_count": {"exact": 3}, "spark_audit_latency_sec": {"max": 300}, "gate_elapsed_sec": {"max": 600}, "new_blocker_admissions": {"max": 0}})
    return {"schema": "metrics-dashboard.v16", "policy_targets": policy, "observed": {k: checked[k] for k in METRIC_NAMES}, "interpretation": "Targets are policy thresholds, not claimed results.", "source_hash": checked["source_hash"]}

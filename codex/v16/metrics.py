"""Artifact-derived productivity metrics and policy dashboard."""
from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping, Sequence

from .contracts import ContractError, canonical_json, canonical_sha256, _int, _str


class MetricsError(ContractError):
    pass

METRIC_NAMES = ("first_pass_approval", "pre_review_blocker_capture", "review_rounds", "full_runs_per_head", "fresh_runs_per_head", "evidence_corrections", "writer_handoffs", "spark_audit_count", "spark_audit_latency_sec", "gate_elapsed_sec", "new_blocker_admissions")


def _nonneg_int(value: Any, path: str) -> int:
    return _int(value, path, minimum=0)


def _rows(evidence: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    rows = evidence.get("rows") if isinstance(evidence, Mapping) else None
    if not isinstance(rows, list):
        raise MetricsError("evidence rows required")
    return rows


def collect_metrics(*, mission: Mapping[str, Any], evidence: Mapping[str, Any], review: Mapping[str, Any], spark_results: Sequence[Mapping[str, Any]], gate_runs: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Derive metrics solely from validated artifacts; never accept self-report values."""
    rows = _rows(evidence)
    if not isinstance(review, Mapping):
        raise MetricsError("review packet required")
    review_rounds = _nonneg_int(review.get("round"), "$.review.round")
    if review_rounds <= 0:
        raise MetricsError("review round denominator must be > 0")
    full_heads = {r.get("actual_head") for r in rows if r.get("stage") == "full"}
    fresh_heads = {r.get("actual_head") for r in rows if r.get("stage") == "fresh"}
    corrections = sum(1 for r in rows if r.get("correction_of"))
    elapsed = 0.0
    for run in gate_runs:
        if not isinstance(run, Mapping):
            raise MetricsError("gate run object required")
        value = run.get("elapsed_sec", 0)
        if type(value) not in (int, float) or value < 0:
            raise MetricsError("gate elapsed must be finite non-negative")
        elapsed += float(value)
    spark_count = len(spark_results)
    if spark_count != 3:
        raise MetricsError("Spark audit count denominator must be exactly three")
    spark_latency = 0.0
    for result in spark_results:
        value = result.get("elapsed_sec")
        if type(value) not in (int, float) or value < 0:
            raise MetricsError("Spark elapsed must be finite non-negative")
        spark_latency += float(value)
    findings = review.get("findings", [])
    if not isinstance(findings, list):
        raise MetricsError("review findings required")
    blockers = [f for f in findings if isinstance(f, Mapping) and (f.get("severity") == "P1" or f.get("label") == "BLOCKING")]
    admissions = sum(1 for f in findings if isinstance(f, Mapping) and f.get("attribution") in {"DELTA_INTRODUCED", "ORIGINAL_SCOPE_MISSED", "NEW_FALSIFIABLE_EVIDENCE"})
    metrics: dict[str, Any] = {
        "schema": "metrics.v16",
        "mission_id": mission.get("mission_id"),
        "source_hash": canonical_sha256({"mission": mission, "evidence": evidence, "review": review, "spark_results": list(spark_results), "gate_runs": list(gate_runs)}),
        "first_pass_approval": review.get("verdict") == "APPROVE" and review_rounds == 1,
        "pre_review_blocker_capture": len(blockers),
        "review_rounds": review_rounds,
        "full_runs_per_head": len([r for r in rows if r.get("stage") == "full"]) / max(1, len(full_heads)),
        "fresh_runs_per_head": len([r for r in rows if r.get("stage") == "fresh"]) / max(1, len(fresh_heads)),
        "evidence_corrections": corrections,
        "writer_handoffs": len({r.get("writer_task_id") for r in rows if r.get("writer_task_id")}) - 1 if any(r.get("writer_task_id") for r in rows) else 0,
        "spark_audit_count": spark_count,
        "spark_audit_latency_sec": round(spark_latency, 6),
        "gate_elapsed_sec": round(elapsed, 6),
        "new_blocker_admissions": admissions,
    }
    # Ensure all required metrics are present and no bool-as-int leakage.
    for name in METRIC_NAMES:
        if name not in metrics:
            raise MetricsError("missing metric: " + name)
    return metrics


def dashboard(metrics: Mapping[str, Any], targets: Mapping[str, Any] | None = None) -> dict[str, Any]:
    checked = dict(metrics)
    if checked.get("schema") != "metrics.v16":
        raise MetricsError("metrics schema")
    policy = dict(targets or {
        "first_pass_approval": True,
        "pre_review_blocker_capture": {"min": 0},
        "review_rounds": {"max": 2},
        "full_runs_per_head": {"max": 1},
        "fresh_runs_per_head": {"max": 1},
        "evidence_corrections": {"max": 0},
        "writer_handoffs": {"max": 0},
        "spark_audit_count": {"exact": 3},
        "spark_audit_latency_sec": {"max": 300},
        "gate_elapsed_sec": {"max": 600},
        "new_blocker_admissions": {"max": 0},
    })
    for key in METRIC_NAMES:
        if key not in checked:
            raise MetricsError("missing metric", f"$.{key}")
    return {"schema": "metrics-dashboard.v16", "policy_targets": policy, "observed": {k: checked[k] for k in METRIC_NAMES}, "interpretation": "Targets are policy thresholds, not claimed results.", "source_hash": checked.get("source_hash", "")}

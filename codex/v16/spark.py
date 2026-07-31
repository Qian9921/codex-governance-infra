"""Bounded Spark inner-loop audit packet/result protocol."""
from __future__ import annotations

import math
import re
from datetime import datetime
from typing import Any, Mapping, Sequence

from .contracts import ContractError, _id, _int, _sha, _str

MAX_SPARK_AUDITS = 3


class SparkAuditError(ContractError):
    pass


def _bool(value: Any, path: str) -> bool:
    if type(value) is not bool:
        raise SparkAuditError("boolean required", path)
    return value


def _timestamp(value: Any, path: str) -> str:
    value = _str(value, path, public=True)
    if not value.endswith("Z"):
        raise SparkAuditError("UTC timestamp required", path)
    return value


def audit_requests(mission: Mapping[str, Any]) -> list[dict[str, Any]]:
    audits = mission.get("spark_audits")
    if not isinstance(audits, list) or len(audits) != MAX_SPARK_AUDITS:
        raise SparkAuditError("exactly three Spark audits required")
    mission_id = _id(mission["mission_id"], "$.mission_id")
    requests: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, audit in enumerate(audits):
        aid = _id(audit["id"], f"$.spark_audits[{index}].id")
        if aid in seen:
            raise SparkAuditError("duplicate Spark audit ID", f"$.spark_audits[{index}].id")
        seen.add(aid)
        requests.append({
            "schema": "spark-audit-request.v16", "audit_id": aid, "mission_id": mission_id,
            "domain": _str(audit["domain"], f"$.spark_audits[{index}].domain", public=True),
            "scope": list(audit["scope"]), "max_findings": _int(audit["max_findings"], f"$.spark_audits[{index}].max_findings", minimum=1, maximum=16),
            "assigned_model": "gpt-5.3-codex-spark", "role": "inner-auditor", "permissions": ["read"],
            "fork_turns": "none", "context_mode": "zero-context", "report_only": True, "spawn_index": index + 1,
        })
    return requests


def validate_request(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise SparkAuditError("request object required")
    fields = {"schema", "audit_id", "mission_id", "domain", "scope", "max_findings", "assigned_model", "role", "permissions", "fork_turns", "context_mode", "report_only", "spawn_index"}
    if set(value) != fields:
        raise SparkAuditError("missing/additional request fields")
    if value["schema"] != "spark-audit-request.v16":
        raise SparkAuditError("schema")
    if value["assigned_model"] != "gpt-5.3-codex-spark":
        raise SparkAuditError("Spark model required")
    if value["role"] != "inner-auditor" or value["permissions"] != ["read"]:
        raise SparkAuditError("report-only read permission required")
    if value["fork_turns"] != "none" or value["context_mode"] != "zero-context" or value["report_only"] is not True:
        raise SparkAuditError("fresh report-only context required")
    _id(value["audit_id"], "$.audit_id"); _id(value["mission_id"], "$.mission_id"); _str(value["domain"], "$.domain", public=True)
    if not isinstance(value["scope"], list) or not value["scope"] or any(not isinstance(v, str) for v in value["scope"]):
        raise SparkAuditError("non-empty scope required", "$.scope")
    _int(value["max_findings"], "$.max_findings", minimum=1, maximum=16)
    _int(value["spawn_index"], "$.spawn_index", minimum=1, maximum=MAX_SPARK_AUDITS)
    return dict(value)


def _finding(value: Any, path: str, allowed_scope: Sequence[str]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise SparkAuditError("finding object required", path)
    fields = {"id", "severity", "label", "scope", "requirement", "location", "counterexample", "impact", "smallest_outcome", "acceptance_case", "attribution"}
    if set(value) != fields:
        raise SparkAuditError("missing/additional finding fields", path)
    fid = _id(value["id"], f"{path}.id")
    severity = _str(value["severity"], f"{path}.severity", public=True)
    if severity not in {"P1", "P2", "P3"}:
        raise SparkAuditError("severity", f"{path}.severity")
    label = _str(value["label"], f"{path}.label", public=True)
    if label not in {"BLOCKING", "NON_BLOCKING", "NIT", "QUESTION", "FOLLOW_UP", "CONTRACT_CHALLENGE"}:
        raise SparkAuditError("finding label", f"{path}.label")
    if severity == "P1" and label != "BLOCKING":
        raise SparkAuditError("P1 must be BLOCKING", f"{path}.label")
    if severity != "P1" and label == "BLOCKING":
        raise SparkAuditError("only P1 may be BLOCKING", f"{path}.label")
    scope = _str(value["scope"], f"{path}.scope", public=True)
    if scope not in allowed_scope:
        raise SparkAuditError("finding out of audit scope", f"{path}.scope")
    return {"id": fid, "severity": severity, "label": label, "scope": scope, "requirement": _str(value["requirement"], f"{path}.requirement", public=True), "location": _str(value["location"], f"{path}.location", public=True), "counterexample": _str(value["counterexample"], f"{path}.counterexample", public=True), "impact": _str(value["impact"], f"{path}.impact", public=True), "smallest_outcome": _str(value["smallest_outcome"], f"{path}.smallest_outcome", public=True), "acceptance_case": _str(value["acceptance_case"], f"{path}.acceptance_case", public=True), "attribution": _str(value["attribution"], f"{path}.attribution", public=True)}


def validate_result(value: Any, request: Mapping[str, Any]) -> dict[str, Any]:
    request = validate_request(request)
    if not isinstance(value, dict):
        raise SparkAuditError("result object required")
    fields = {"schema", "audit_id", "mission_id", "task_id", "assigned_model", "reasoning_effort", "fork_turns", "context_mode", "report_only", "scope", "findings", "dispositions", "started_at", "ended_at", "elapsed_sec"}
    if set(value) != fields:
        raise SparkAuditError("missing/additional result fields")
    if value["schema"] != "spark-audit-result.v16":
        raise SparkAuditError("schema")
    if value["audit_id"] != request["audit_id"] or value["mission_id"] != request["mission_id"]:
        raise SparkAuditError("request/result identity mismatch")
    if value["assigned_model"] != "gpt-5.3-codex-spark" or value["fork_turns"] != "none" or value["context_mode"] != "zero-context" or value["report_only"] is not True:
        raise SparkAuditError("Spark report-only identity required")
    _id(value["task_id"], "$.task_id")
    if value["reasoning_effort"] != "high":
        raise SparkAuditError("high reasoning effort required")
    scope = _str(value["scope"], "$.scope", public=True)
    if scope not in request["scope"]:
        raise SparkAuditError("result scope outside request")
    findings = value["findings"]
    if not isinstance(findings, list) or len(findings) > request["max_findings"]:
        raise SparkAuditError("finding count exceeds bounded request")
    checked = [_finding(item, f"$.findings[{i}]", request["scope"]) for i, item in enumerate(findings)]
    if len({f["id"] for f in checked}) != len(checked):
        raise SparkAuditError("duplicate findings")
    dispositions = value["dispositions"]
    if not isinstance(dispositions, dict) or set(dispositions) != {f["id"] for f in checked}:
        raise SparkAuditError("every finding must be dispositioned exactly once")
    if any(v not in {"FIXED", "DISAGREE", "FOLLOW_UP"} for v in dispositions.values()):
        raise SparkAuditError("invalid disposition")
    _timestamp(value["started_at"], "$.started_at"); _timestamp(value["ended_at"], "$.ended_at")
    elapsed = value["elapsed_sec"]
    if type(elapsed) not in (int, float) or isinstance(elapsed, bool) or not math.isfinite(float(elapsed)) or elapsed < 0:
        raise SparkAuditError("finite elapsed required", "$.elapsed_sec")
    result = dict(value); result["findings"] = checked; result["dispositions"] = dict(dispositions)
    return result


def validate_bundle(requests: Sequence[Mapping[str, Any]], results: Sequence[Mapping[str, Any]], *, budget: int = MAX_SPARK_AUDITS) -> list[dict[str, Any]]:
    if budget != MAX_SPARK_AUDITS or len(requests) != MAX_SPARK_AUDITS or len(results) != MAX_SPARK_AUDITS:
        raise SparkAuditError("Spark spawn budget is exactly three")
    checked_requests = [validate_request(r) for r in requests]
    if len({r["audit_id"] for r in checked_requests}) != len(checked_requests):
        raise SparkAuditError("duplicate Spark audit request")
    by_id = {r["audit_id"]: r for r in checked_requests}
    checked_results = []
    for result in results:
        aid = result.get("audit_id") if isinstance(result, dict) else None
        if aid not in by_id:
            raise SparkAuditError("missing/duplicate/out-of-scope Spark result")
        if any(r.get("audit_id") == aid for r in checked_results):
            raise SparkAuditError("duplicate Spark result")
        checked_results.append(validate_result(result, by_id[aid]))
    if {r["audit_id"] for r in checked_results} != set(by_id):
        raise SparkAuditError("missing Spark result")
    return checked_results

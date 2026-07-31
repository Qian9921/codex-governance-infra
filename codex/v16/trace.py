"""Deterministic, GitHub-free PR trace packet and sanitized body renderer."""
from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping, Sequence

from .contracts import ContractError, canonical_json, canonical_sha256, _id, _int, _sha, _str
from .evidence import EvidenceError, privacy_scan


class TraceError(ContractError):
    pass


def _bool(value: Any, path: str) -> bool:
    if type(value) is not bool:
        raise TraceError("boolean required", path)
    return value


def _check(value: Any, path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise TraceError("check object required", path)
    fields = {"id", "status", "reused", "skipped", "cost", "denominator", "total", "passed", "failed"}
    if set(value) != fields:
        raise TraceError("missing/additional check fields", path)
    status = _str(value["status"], f"{path}.status", public=True)
    if status not in {"GREEN", "RED", "SKIPPED", "REUSED"}:
        raise TraceError("check status", f"{path}.status")
    denominator = _int(value["denominator"], f"{path}.denominator", minimum=1)
    total = _int(value["total"], f"{path}.total", minimum=0); passed = _int(value["passed"], f"{path}.passed", minimum=0); failed = _int(value["failed"], f"{path}.failed", minimum=0)
    if total != passed + failed:
        raise TraceError("check arithmetic", path)
    return {"id": _id(value["id"], f"{path}.id"), "status": status, "reused": _bool(value["reused"], f"{path}.reused"), "skipped": _bool(value["skipped"], f"{path}.skipped"), "cost": _str(value["cost"], f"{path}.cost", public=True), "denominator": denominator, "total": total, "passed": passed, "failed": failed}


def _finding(value: Any, path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise TraceError("finding object required", path)
    fields = {"id", "severity", "label", "attribution", "location", "counterexample", "disposition"}
    if set(value) != fields:
        raise TraceError("missing/additional finding fields", path)
    fid = _id(value["id"], f"{path}.id")
    severity = _str(value["severity"], f"{path}.severity", public=True)
    if severity not in {"P1", "P2", "P3"}:
        raise TraceError("severity", f"{path}.severity")
    label = _str(value["label"], f"{path}.label", public=True)
    if severity == "P1" and label != "BLOCKING":
        raise TraceError("P1 must be BLOCKING", f"{path}.label")
    if severity != "P1" and label == "BLOCKING":
        raise TraceError("P2/P3 cannot be BLOCKING", f"{path}.label")
    disposition = _str(value["disposition"], f"{path}.disposition", public=True)
    if disposition not in {"FIXED", "DISAGREE", "FOLLOW_UP", "OPEN"}:
        raise TraceError("disposition", f"{path}.disposition")
    return {"id": fid, "severity": severity, "label": label, "attribution": _str(value["attribution"], f"{path}.attribution", public=True), "location": _str(value["location"], f"{path}.location", public=True), "counterexample": _str(value["counterexample"], f"{path}.counterexample", public=True), "disposition": disposition}


def validate_review_packet(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise TraceError("review packet object required")
    fields = {"schema", "mission_id", "author_login", "reviewer_login", "base_sha", "head_sha", "tree_sha", "lineage_mode", "coverage_status", "reviewed_scope", "unreviewed_scope", "checks", "findings", "closures", "verdict", "round", "body_sha256"}
    if set(value) != fields:
        raise TraceError("missing/additional review packet fields")
    if value["schema"] != "review-packet.v16":
        raise TraceError("schema")
    if value["author_login"] != "Qian9921" or value["reviewer_login"] != "Liang9921":
        raise TraceError("fixed author/reviewer identity required")
    if value["author_login"] == value["reviewer_login"]:
        raise TraceError("writer/reviewer identity collision")
    _id(value["mission_id"], "$.mission_id"); base = _sha(value["base_sha"], "$.base_sha"); head = _sha(value["head_sha"], "$.head_sha")
    tree = _str(value["tree_sha"], "$.tree_sha", max_len=64, public=True)
    if len(tree) != 40 or any(c not in "0123456789abcdef" for c in tree.lower()):
        raise TraceError("tree SHA", "$.tree_sha")
    lineage = _str(value["lineage_mode"], "$.lineage_mode", public=False)
    if lineage not in {"FULL_CONTROL_PLANE", "DISPATCH_TRANSCRIPT"}:
        raise TraceError("lineage mode", "$.lineage_mode")
    coverage = _str(value["coverage_status"], "$.coverage_status", public=True)
    if coverage not in {"PARTIAL", "COMPLETE"}:
        raise TraceError("coverage status", "$.coverage_status")
    reviewed = value["reviewed_scope"]; unreviewed = value["unreviewed_scope"]
    if not isinstance(reviewed, list) or not isinstance(unreviewed, list) or any(not isinstance(x, str) for x in reviewed + unreviewed):
        raise TraceError("scope arrays")
    checks = [_check(v, f"$.checks[{i}]") for i, v in enumerate(value["checks"])] if isinstance(value["checks"], list) else (_ for _ in ()).throw(TraceError("checks array", "$.checks"))
    if not checks:
        raise TraceError("at least one check with a known denominator is required", "$.checks")
    if len({c["id"] for c in checks}) != len(checks):
        raise TraceError("duplicate check IDs")
    findings = [_finding(v, f"$.findings[{i}]") for i, v in enumerate(value["findings"])] if isinstance(value["findings"], list) else (_ for _ in ()).throw(TraceError("findings array", "$.findings"))
    if len({f["id"] for f in findings}) != len(findings):
        raise TraceError("duplicate findings")
    closures = value["closures"]
    if not isinstance(closures, dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in closures.items()):
        raise TraceError("closures")
    verdict = value["verdict"]
    if verdict not in {"APPROVE", "REQUEST_CHANGES", None}:
        raise TraceError("verdict")
    if coverage == "COMPLETE" and unreviewed:
        raise TraceError("complete coverage cannot have unreviewed scope")
    if verdict == "APPROVE" and (coverage != "COMPLETE" or unreviewed or any(f["severity"] == "P1" or f["label"] == "BLOCKING" for f in findings)):
        raise TraceError("APPROVE requires complete scope/no blockers")
    round_no = _int(value["round"], "$.round", minimum=1)
    body_hash = _str(value["body_sha256"], "$.body_sha256", max_len=64, public=True)
    if len(body_hash) != 64 or any(c not in "0123456789abcdef" for c in body_hash):
        raise TraceError("body SHA-256", "$.body_sha256")
    return {"schema": "review-packet.v16", "mission_id": value["mission_id"], "author_login": "Qian9921", "reviewer_login": "Liang9921", "base_sha": base, "head_sha": head, "tree_sha": tree, "lineage_mode": lineage, "coverage_status": coverage, "reviewed_scope": list(reviewed), "unreviewed_scope": list(unreviewed), "checks": checks, "findings": findings, "closures": dict(closures), "verdict": verdict, "round": round_no, "body_sha256": body_hash}


def render_pr_trace(*, mission_id: str, base_sha: str, head_sha: str, tree_sha: str, checks: Sequence[Mapping[str, Any]], findings: Sequence[Mapping[str, Any]], closures: Mapping[str, str], reviewed_scope: Sequence[str], unreviewed_scope: Sequence[str], lineage_mode: str = "DISPATCH_TRANSCRIPT", round: int = 1) -> dict[str, Any]:
    blockers = any(f.get("severity") == "P1" or f.get("label") == "BLOCKING" for f in findings)
    verdict = "APPROVE" if checks and not blockers and not unreviewed_scope and all(c.get("status") in {"GREEN", "REUSED"} for c in checks) else "REQUEST_CHANGES"
    unsigned = {"schema": "review-packet.v16", "mission_id": mission_id, "author_login": "Qian9921", "reviewer_login": "Liang9921", "base_sha": base_sha, "head_sha": head_sha, "tree_sha": tree_sha, "lineage_mode": lineage_mode, "coverage_status": "COMPLETE" if not unreviewed_scope else "PARTIAL", "reviewed_scope": list(reviewed_scope), "unreviewed_scope": list(unreviewed_scope), "checks": [dict(c) for c in checks], "findings": [dict(f) for f in findings], "closures": dict(closures), "verdict": verdict, "round": round, "body_sha256": ""}
    public_lineage = "DISPATCH" if lineage_mode == "DISPATCH_TRANSCRIPT" else lineage_mode
    body = "\n".join([
        "### V16 productivity PR trace",
        f"- mission: `{mission_id}`",
        f"- author: `Qian9921`; independent reviewer: `Liang9921`",
        f"- base: `{base_sha}`; head: `{head_sha}`; tree: `{tree_sha}`",
        # The packet retains the exact lineage mode; the public body uses a
        # sanitized label so raw transcript material can never be copied out.
        f"- lineage: `{public_lineage}`; coverage: `{unsigned['coverage_status']}`; round: `{round}`",
        f"- checks: {len(checks)}; findings: {len(findings)}; verdict: `{verdict}`",
        "- denominators/costs and finding closures are machine-derived from the packet; this renderer makes no GitHub calls.",
    ]) + "\n"
    violations = privacy_scan(body)
    if violations:
        raise TraceError("privacy violation in public trace: " + ",".join(violations))
    unsigned["body_sha256"] = hashlib.sha256(body.encode("utf-8")).hexdigest()
    packet = validate_review_packet(unsigned)
    return {"packet": packet, "markdown": body, "packet_sha256": canonical_sha256(packet), "body_sha256": packet["body_sha256"]}

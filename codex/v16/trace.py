"""Deterministic, GitHub-free PR trace packet and sanitized body renderer."""
from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timezone
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
    fields = {"id", "status", "reused", "skipped", "cost", "denominator", "total", "ran", "passed", "failed", "unknown", "xfail"}
    if set(value) != fields:
        raise TraceError("missing/additional check fields", path)
    status = _str(value["status"], f"{path}.status", public=True)
    if status not in {"GREEN", "RED", "SKIPPED", "REUSED"}:
        raise TraceError("check status", f"{path}.status")
    denominator = _int(value["denominator"], f"{path}.denominator", minimum=1)
    total = _int(value["total"], f"{path}.total", minimum=1); ran = _int(value["ran"], f"{path}.ran", minimum=0); passed = _int(value["passed"], f"{path}.passed", minimum=0); failed = _int(value["failed"], f"{path}.failed", minimum=0); unknown = _int(value["unknown"], f"{path}.unknown", minimum=0); xfail = _int(value["xfail"], f"{path}.xfail", minimum=0)
    reused = _bool(value["reused"], f"{path}.reused"); skipped = _bool(value["skipped"], f"{path}.skipped")
    skipped_count = total if skipped else 0
    if total != passed + failed + skipped_count + unknown:
        raise TraceError("check arithmetic", path)
    if ran != passed + failed or denominator != total:
        raise TraceError("check denominator arithmetic", path)
    if skipped and (passed or failed or unknown or ran):
        raise TraceError("skipped check cannot report ran counts", path)
    if xfail > total:
        raise TraceError("xfail exceeds denominator", path)
    expected_status = "REUSED" if reused else ("SKIPPED" if skipped else ("GREEN" if failed == 0 and unknown == 0 and xfail == 0 and passed == total else "RED"))
    if status != expected_status:
        raise TraceError("check status contradicts counts", path)
    return {"id": _id(value["id"], f"{path}.id"), "status": status, "reused": reused, "skipped": skipped, "cost": _str(value["cost"], f"{path}.cost", public=True), "denominator": denominator, "total": total, "ran": ran, "passed": passed, "failed": failed, "unknown": unknown, "xfail": xfail}


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
    optional = {"independent_artifact_sha256", "expected_scope", "incident"}
    if set(value) - fields - optional or fields - set(value):
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
    if not reviewed:
        raise TraceError("reviewed scope cannot be empty")
    if "expected_scope" in value:
        expected_scope = value["expected_scope"]
        if not isinstance(expected_scope, list) or sorted(expected_scope) != sorted(reviewed + unreviewed):
            raise TraceError("reviewed scope must equal frozen scope")
    if "incident" in value:
        incident = value["incident"]
        if not isinstance(incident, dict) or set(incident) != {"commit_ids", "summary"} or not isinstance(incident["commit_ids"], list) or not incident["commit_ids"] or any(not isinstance(cid, str) or len(cid) != 40 or any(c not in "0123456789abcdef" for c in cid) for cid in incident["commit_ids"]):
            raise TraceError("strict incident record")
        _str(incident["summary"], "$.incident.summary", public=True)
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
    if set(closures) != {f["id"] for f in findings}:
        raise TraceError("every finding requires one closure", "$.closures")
    if any(closures[f["id"]] != f["disposition"] for f in findings):
        raise TraceError("finding closure/disposition mismatch", "$.closures")
    verdict = value["verdict"]
    if verdict not in {"APPROVE", "REQUEST_CHANGES", None}:
        raise TraceError("verdict")
    if coverage == "COMPLETE" and unreviewed:
        raise TraceError("complete coverage cannot have unreviewed scope")
    if verdict == "APPROVE" and (coverage != "COMPLETE" or unreviewed or any(f["severity"] == "P1" or f["label"] == "BLOCKING" for f in findings) or "independent_artifact_sha256" not in value):
        raise TraceError("APPROVE requires complete scope/no blockers")
    if "independent_artifact_sha256" in value:
        artifact_hash = _str(value["independent_artifact_sha256"], "$.independent_artifact_sha256", max_len=64, public=True)
        if len(artifact_hash) != 64 or any(c not in "0123456789abcdef" for c in artifact_hash):
            raise TraceError("Independent artifact SHA-256", "$.independent_artifact_sha256")
    round_no = _int(value["round"], "$.round", minimum=1)
    body_hash = _str(value["body_sha256"], "$.body_sha256", max_len=64, public=True)
    if len(body_hash) != 64 or any(c not in "0123456789abcdef" for c in body_hash):
        raise TraceError("body SHA-256", "$.body_sha256")
    result = {"schema": "review-packet.v16", "mission_id": value["mission_id"], "author_login": "Qian9921", "reviewer_login": "Liang9921", "base_sha": base, "head_sha": head, "tree_sha": tree, "lineage_mode": lineage, "coverage_status": coverage, "reviewed_scope": list(reviewed), "unreviewed_scope": list(unreviewed), "checks": checks, "findings": findings, "closures": dict(closures), "verdict": verdict, "round": round_no, "body_sha256": body_hash}
    if "independent_artifact_sha256" in value: result["independent_artifact_sha256"] = value["independent_artifact_sha256"]
    if "expected_scope" in value: result["expected_scope"] = list(value["expected_scope"])
    if "incident" in value: result["incident"] = {"commit_ids": list(value["incident"]["commit_ids"]), "summary": value["incident"]["summary"]}
    return result


def render_pr_trace(*, mission_id: str, base_sha: str, head_sha: str, tree_sha: str, checks: Sequence[Mapping[str, Any]], findings: Sequence[Mapping[str, Any]], closures: Mapping[str, str], reviewed_scope: Sequence[str], unreviewed_scope: Sequence[str], lineage_mode: str = "DISPATCH_TRANSCRIPT", round: int = 1, incident: Mapping[str, Any] | None = None) -> dict[str, Any]:
    verdict = None
    normalized_checks: list[dict[str, Any]] = []
    for check in checks:
        item = dict(check)
        # The renderer is a compatibility boundary for callers that provide a
        # compact check summary.  It expands only unambiguous zero-count fields;
        # validation below still enforces the complete packet schema.
        item.setdefault("ran", item.get("passed", 0) + item.get("failed", 0))
        item.setdefault("unknown", 0); item.setdefault("xfail", 0)
        item.setdefault("skipped", False); item.setdefault("reused", False)
        normalized_checks.append(item)
    normalized_findings = [dict(f) for f in findings]
    unsigned = {"schema": "review-packet.v16", "mission_id": mission_id, "author_login": "Qian9921", "reviewer_login": "Liang9921", "base_sha": base_sha, "head_sha": head_sha, "tree_sha": tree_sha, "lineage_mode": lineage_mode, "coverage_status": "COMPLETE" if not unreviewed_scope else "PARTIAL", "reviewed_scope": list(reviewed_scope), "unreviewed_scope": list(unreviewed_scope), "checks": normalized_checks, "findings": normalized_findings, "closures": dict(closures), "verdict": verdict, "round": round, "body_sha256": ""}
    if incident is not None:
        unsigned["incident"] = {"commit_ids": list(incident["commit_ids"]), "summary": incident["summary"]}
    public_lineage = "DISPATCH" if lineage_mode == "DISPATCH_TRANSCRIPT" else lineage_mode
    body = "\n".join([
        "### V16 productivity PR trace",
        f"- mission: `{mission_id}`",
        f"- author: `Qian9921`; independent reviewer: `Liang9921`",
        f"- base: `{base_sha}`; head: `{head_sha}`; tree: `{tree_sha}`",
        # The packet retains the exact lineage mode; the public body uses a
        # sanitized label so raw transcript material can never be copied out.
        f"- lineage: `{public_lineage}`; coverage: `{unsigned['coverage_status']}`; round: `{round}`",
        f"- checks: {len(normalized_checks)}; findings: {len(normalized_findings)}; verdict: `null` (author renderer)",
        "- denominators/costs and finding closures are machine-derived from the packet; this renderer makes no GitHub calls.",
    ]) + "\n"
    if incident is not None:
        body += f"- remediation incident recorded: `{len(incident['commit_ids'])}` transient local commit(s); source identity remained guarded.\n"
    violations = privacy_scan(body)
    if violations:
        raise TraceError("privacy violation in public trace: " + ",".join(violations))
    unsigned["body_sha256"] = hashlib.sha256(body.encode("utf-8")).hexdigest()
    packet = validate_review_packet(unsigned)
    return {"packet": packet, "markdown": body, "packet_sha256": canonical_sha256(packet), "body_sha256": packet["body_sha256"]}


def ingest_independent_artifact(packet: Mapping[str, Any], artifact: Mapping[str, Any]) -> dict[str, Any]:
    """Set APPROVE only from a separately verified Independent artifact."""
    candidate = dict(packet)
    if candidate.get("verdict") is not None:
        raise TraceError("author packet must have null verdict")
    if not isinstance(artifact, Mapping) or artifact.get("schema") != "independent-review.v16" or artifact.get("verdict") not in {"APPROVE", "REQUEST_CHANGES"}:
        raise TraceError("verified Independent artifact required")
    required = {"schema", "reviewer_login", "reviewer_model", "reasoning_effort", "fork_turns", "context_mode", "report_only", "reviewer_is_writer", "head_sha", "tree_sha", "coverage_status", "reviewed_scope", "unreviewed_scope", "verdict", "artifact_sha256"}
    if set(artifact) != required:
        raise TraceError("strict Independent artifact fields")
    if artifact.get("reviewer_login") != "Liang9921" or artifact.get("reviewer_model") != "gpt-5.6-sol" or artifact.get("reasoning_effort") != "xhigh" or artifact.get("fork_turns") != "none" or artifact.get("context_mode") != "zero-context" or artifact.get("report_only") is not True or artifact.get("reviewer_is_writer") is not False:
        raise TraceError("Independent reviewer lineage mismatch")
    if artifact.get("head_sha") != candidate.get("head_sha") or artifact.get("tree_sha") != candidate.get("tree_sha"):
        raise TraceError("Independent identity mismatch")
    if artifact.get("coverage_status") != "COMPLETE" or artifact.get("reviewed_scope") != candidate.get("reviewed_scope") or artifact.get("unreviewed_scope"):
        raise TraceError("Independent scope mismatch")
    digest = artifact.get("artifact_sha256")
    if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
        raise TraceError("Independent artifact hash required")
    unsigned_artifact = dict(artifact); unsigned_artifact["artifact_sha256"] = ""
    if hashlib.sha256(canonical_json(unsigned_artifact).encode("utf-8")).hexdigest() != digest:
        raise TraceError("Independent artifact hash mismatch")
    candidate["verdict"] = artifact["verdict"]; candidate["independent_artifact_sha256"] = digest
    return validate_review_packet(candidate)

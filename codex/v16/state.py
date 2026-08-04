"""Crash-safe V16 readiness state machine."""
from __future__ import annotations

import hashlib
import json
import re
import os
import pathlib
import tempfile
from datetime import datetime, timezone
from typing import Any, Mapping

from .contracts import ContractError, _id, _sha, canonical_json, canonical_sha256
from .review_policy import resolve_review_policy
from .trace import TraceError, identity_delta_sha256, ingest_independent_artifact

_IDENTITY_MODES = {"git-exact-object", "non-git-snapshot"}


def _validate_identity_fields(value: Mapping[str, Any], *, path: str = "$.identity") -> dict[str, Any]:
    mode = value.get("identity_mode", "git-exact-object")
    current = value.get("snapshot_sha256", "")
    previous = value.get("prior_snapshot_sha256")
    delta = value.get("delta_sha256")
    if mode not in _IDENTITY_MODES:
        raise ReadinessError("identity mode", f"{path}.identity_mode")
    if mode == "git-exact-object":
        if current != "" or previous is not None:
            raise ReadinessError("Git identity requires empty snapshots", path)
    else:
        if not isinstance(current, str) or not re.fullmatch(r"[0-9a-f]{64}", current):
            raise ReadinessError("non-Git current snapshot SHA-256 required", f"{path}.snapshot_sha256")
        if previous is not None and (not isinstance(previous, str) or not re.fullmatch(r"[0-9a-f]{64}", previous)):
            raise ReadinessError("non-Git prior snapshot SHA-256 required", f"{path}.prior_snapshot_sha256")
        if previous is not None and previous == current:
            raise ReadinessError("non-Git snapshots must differ", path)
        if previous is not None and delta is None:
            raise ReadinessError("non-Git continuation requires identity delta", path)
    if delta is not None and (not isinstance(delta, str) or not re.fullmatch(r"[0-9a-f]{64}", delta)):
        raise ReadinessError("identity delta SHA-256 required", f"{path}.delta_sha256")
    return {
        "identity_mode": mode,
        "snapshot_sha256": current,
        "prior_snapshot_sha256": previous,
        "delta_sha256": delta,
    }

STATES = ("DRAFT", "COUNTEREXAMPLES_FROZEN", "BASELINE_REPRODUCED", "IMPLEMENTING", "INNER_AUDIT_COMPLETE", "LOCAL_READY", "FRESH_READY", "REVIEW_READY")
_ALLOWED = {current: STATES[i + 1] if i + 1 < len(STATES) else None for i, current in enumerate(STATES)}


class ReadinessError(ContractError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _timestamp(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ReadinessError("UTC timestamp ending Z required", path)
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise ReadinessError("RFC3339 UTC timestamp required", path) from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise ReadinessError("UTC timestamp required", path)
    return value


def _fresh_timestamp(previous: str, current: str) -> None:
    if current < previous:
        raise ReadinessError("backdating forbidden", "$.updated_at")


def _resolved_review_identity(policy: Mapping[str, Any]) -> dict[str, Any]:
    """Return the immutable policy fields that readiness is allowed to trust."""
    risk = policy.get("review_risk")
    if risk not in {"low", "medium", "high"}:
        raise ReadinessError("resolved review policy risk required", "$.review_policy")
    stages = policy.get("required_stages")
    model = policy.get("reviewer_model")
    effort = policy.get("reasoning_effort")
    classifier = policy.get("classifier_identity", "")
    triggers = policy.get("high_risk_triggers", [])
    route = "high_risk" if risk == "high" else "general"
    expected_model, expected_effort = {
        "low": ("gpt-5.6-sol", "high"),
        "medium": ("gpt-5.6-sol", "high"),
        "high": ("gpt-5.6-sol", "xhigh"),
    }[risk]
    if stages not in (["targeted"], ["targeted", "full"], ["targeted", "full", "fresh"]):
        raise ReadinessError("required review stages must be an ordered prefix", "$.review_policy.required_stages")
    if model != expected_model or effort != expected_effort:
        raise ReadinessError("resolved review policy route/model mismatch", "$.review_policy")
    if not isinstance(classifier, str) or not isinstance(triggers, list) or any(not isinstance(item, str) for item in triggers):
        raise ReadinessError("resolved review policy classifier/triggers required", "$.review_policy")
    identity = {
        "required_stages": list(stages),
        "reviewer_model": model,
        "reasoning_effort": effort,
        "review_risk": risk,
        "reviewer_route": route,
        "classifier_identity": classifier,
        "high_risk_triggers": sorted(triggers),
    }
    identity["review_policy_sha256"] = canonical_sha256(identity)
    return identity


def initial_state(mission_id: str, base_sha: str, tree_sha: str = "", review_policy: Mapping[str, Any] | None = None) -> dict[str, Any]:
    # Accept either the raw mission/policy input or the compiler's already
    # resolved policy object.  In both cases only the strict identity fields
    # below are admitted into state; resolution metadata cannot spoof them.
    if isinstance(review_policy, Mapping) and ("resolution" in review_policy or "legacy_fallback" in review_policy):
        resolved = {key: review_policy.get(key) for key in ("required_stages", "reviewer_model", "reasoning_effort", "review_risk", "classifier_identity", "high_risk_triggers")}
    else:
        resolved = resolve_review_policy(review_policy)
    policy = _resolved_review_identity(resolved)
    return {
        "schema": "readiness-state.v16",
        "mission_id": _id(mission_id, "$.mission_id"),
        "state": "DRAFT",
        "revision": 0,
        "created_at": _now(),
        "updated_at": _now(),
        "base_sha": _sha(base_sha, "$.base_sha"),
        "head_sha": _sha(base_sha, "$.head_sha"),
        "tree_sha": tree_sha,
        "identity_mode": "git-exact-object",
        "snapshot_sha256": "",
        "prior_snapshot_sha256": None,
        "delta_sha256": None,
        "counterexample_ids": [],
        "red_counterexamples": [],
        "green_counterexamples": [],
        "spark_findings": [],
        "spark_audit_count": 0,
        "dispositions": {},
        "gate_ids": [],
        "evidence_ids": [],
        "receipt_artifacts": {},
        "author_closure_sha256": "",
        "review_ready": False,
        **policy,
        "approved_review_artifact_sha256": "",
        "approved_review_packet_sha256": "",
    }


def validate_state(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ReadinessError("object required")
    value = dict(value)
    identity_fields = {"identity_mode", "snapshot_sha256", "prior_snapshot_sha256", "delta_sha256"}
    present_identity = set(value) & identity_fields
    if not present_identity:
        # Legacy Git state receipts remain readable; the canonical output is
        # normalized to the strict default without allowing a partial union.
        value.update({"identity_mode": "git-exact-object", "snapshot_sha256": "", "prior_snapshot_sha256": None, "delta_sha256": None})
    elif present_identity != identity_fields:
        raise ReadinessError("complete state identity required", "$.identity")
    required = {"schema", "mission_id", "state", "revision", "created_at", "updated_at", "base_sha", "head_sha", "tree_sha", "identity_mode", "snapshot_sha256", "prior_snapshot_sha256", "delta_sha256", "counterexample_ids", "red_counterexamples", "green_counterexamples", "spark_findings", "spark_audit_count", "dispositions", "gate_ids", "evidence_ids", "receipt_artifacts", "author_closure_sha256", "review_ready", "required_stages", "reviewer_model", "reasoning_effort", "review_risk", "reviewer_route", "classifier_identity", "high_risk_triggers", "review_policy_sha256", "approved_review_artifact_sha256", "approved_review_packet_sha256"}
    extra = set(value) - required
    missing = required - set(value)
    if missing:
        raise ReadinessError("missing field(s): " + ",".join(sorted(missing)))
    if extra:
        raise ReadinessError("additionalProperties forbidden: " + ",".join(sorted(extra)))
    if value["schema"] != "readiness-state.v16":
        raise ReadinessError("schema")
    if value["state"] not in STATES:
        raise ReadinessError("unknown readiness state")
    if type(value["revision"]) is not int or value["revision"] < 0:
        raise ReadinessError("revision must be non-negative integer")
    _timestamp(value["created_at"], "$.created_at"); _timestamp(value["updated_at"], "$.updated_at")
    _fresh_timestamp(value["created_at"], value["updated_at"])
    _id(value["mission_id"], "$.mission_id"); _sha(value["base_sha"], "$.base_sha"); _sha(value["head_sha"], "$.head_sha")
    if value["tree_sha"] and (not isinstance(value["tree_sha"], str) or len(value["tree_sha"]) != 40 or any(c not in "0123456789abcdef" for c in value["tree_sha"].lower())):
        raise ReadinessError("tree_sha")
    _validate_identity_fields(value)
    for field in ("counterexample_ids", "red_counterexamples", "green_counterexamples", "spark_findings", "gate_ids", "evidence_ids"):
        if not isinstance(value[field], list) or any(not isinstance(x, str) for x in value[field]):
            raise ReadinessError(field)
        if len(set(value[field])) != len(value[field]):
            raise ReadinessError("duplicate IDs", f"$.{field}")
    if not isinstance(value["dispositions"], dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in value["dispositions"].items()):
        raise ReadinessError("dispositions")
    if type(value["spark_audit_count"]) is not int or not 0 <= value["spark_audit_count"] <= 3:
        raise ReadinessError("spark_audit_count")
    if type(value["review_ready"]) is not bool:
        raise ReadinessError("review_ready")
    identity = _resolved_review_identity({
        "required_stages": value["required_stages"],
        "reviewer_model": value["reviewer_model"],
        "reasoning_effort": value["reasoning_effort"],
        "review_risk": value["review_risk"],
        "classifier_identity": value["classifier_identity"],
        "high_risk_triggers": value["high_risk_triggers"],
    })
    if value["reviewer_route"] != identity["reviewer_route"] or value["review_policy_sha256"] != identity["review_policy_sha256"]:
        raise ReadinessError("review policy identity mismatch")
    if not isinstance(value["required_stages"], list) or value["required_stages"] != identity["required_stages"]:
        raise ReadinessError("required review stages mismatch")
    for field in ("reviewer_model", "reasoning_effort", "review_risk", "reviewer_route", "classifier_identity"):
        if not isinstance(value[field], str) or value[field] != identity[field]:
            raise ReadinessError("review policy identity mismatch", f"$.{field}")
    if not isinstance(value["high_risk_triggers"], list) or value["high_risk_triggers"] != identity["high_risk_triggers"]:
        raise ReadinessError("review policy identity mismatch", "$.high_risk_triggers")
    for field in ("approved_review_artifact_sha256", "approved_review_packet_sha256"):
        digest = value[field]
        if not isinstance(digest, str) or (digest and (len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest))):
            raise ReadinessError(field)
    if value["review_ready"] and (not value["approved_review_artifact_sha256"] or not value["approved_review_packet_sha256"]):
        raise ReadinessError("review-ready state requires approved review hashes")
    if not value["review_ready"] and (value["approved_review_artifact_sha256"] or value["approved_review_packet_sha256"]):
        raise ReadinessError("unapproved state cannot carry approved review hashes")
    closure_sha = value["author_closure_sha256"]
    if not isinstance(closure_sha, str) or (closure_sha and (len(closure_sha) != 64 or any(c not in "0123456789abcdef" for c in closure_sha))):
        raise ReadinessError("author_closure_sha256")
    artifacts = value["receipt_artifacts"]
    if not isinstance(artifacts, dict):
        raise ReadinessError("receipt_artifacts")
    for receipt_id, artifact in artifacts.items():
        if not isinstance(receipt_id, str) or not isinstance(artifact, dict):
            raise ReadinessError("receipt artifact object required", "$.receipt_artifacts")
        fields = {"receipt_id", "kind", "stage", "head_sha", "tree_sha", "decision", "counts", "artifact_sha256"}
        identity_fields = {"identity_mode", "snapshot_sha256"}
        if set(artifact) not in (fields, fields | identity_fields) or artifact["receipt_id"] != receipt_id:
            raise ReadinessError("strict receipt artifact fields", f"$.receipt_artifacts[{receipt_id}]")
        if artifact["kind"] not in {"evidence", "gate"} or artifact["stage"] not in {"targeted", "full", "fresh"} or artifact["decision"] not in {"allow", "reused"}:
            raise ReadinessError("receipt artifact kind/stage/decision", f"$.receipt_artifacts[{receipt_id}]")
        _sha(artifact["head_sha"], f"$.receipt_artifacts[{receipt_id}].head_sha")
        _sha(artifact["tree_sha"], f"$.receipt_artifacts[{receipt_id}].tree_sha")
        if set(artifact) == fields | identity_fields:
            if artifact["identity_mode"] not in _IDENTITY_MODES:
                raise ReadinessError("receipt identity mode", f"$.receipt_artifacts[{receipt_id}].identity_mode")
            snapshot = artifact["snapshot_sha256"]
            if artifact["identity_mode"] == "git-exact-object":
                if snapshot != "":
                    raise ReadinessError("Git receipt cannot carry content snapshot", f"$.receipt_artifacts[{receipt_id}].snapshot_sha256")
            elif not isinstance(snapshot, str) or not re.fullmatch(r"[0-9a-f]{64}", snapshot):
                raise ReadinessError("non-Git receipt snapshot SHA-256 required", f"$.receipt_artifacts[{receipt_id}].snapshot_sha256")
        digest = artifact["artifact_sha256"]
        if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise ReadinessError("receipt artifact SHA-256 required", f"$.receipt_artifacts[{receipt_id}].artifact_sha256")
        unsigned = dict(artifact); unsigned["artifact_sha256"] = ""
        if canonical_sha256(unsigned) != digest:
            raise ReadinessError("receipt artifact digest mismatch", f"$.receipt_artifacts[{receipt_id}].artifact_sha256")
        counts = artifact["counts"]
        if not isinstance(counts, dict) or set(counts) != {"total", "ran", "passed", "failed", "skipped", "xfail", "unknown"} or any(type(v) is not int or v < 0 for v in counts.values()):
            raise ReadinessError("receipt artifact counts", f"$.receipt_artifacts[{receipt_id}].counts")
        if counts["total"] <= 0 or counts["total"] != counts["passed"] + counts["failed"] + counts["skipped"] + counts["unknown"] or counts["ran"] != counts["passed"] + counts["failed"] or any(counts[k] for k in ("failed", "skipped", "xfail", "unknown")):
            raise ReadinessError("receipt artifact counts are not green", f"$.receipt_artifacts[{receipt_id}].counts")
    if value["state"] in {"LOCAL_READY", "FRESH_READY", "REVIEW_READY"}:
        if not value["author_closure_sha256"] or not value["red_counterexamples"] or not value["green_counterexamples"] or set(value["red_counterexamples"]) != set(value["green_counterexamples"]):
            raise ReadinessError("ready state requires closed counterexamples and author closure")
        required = set(value["required_stages"])
        _receipt_ids(value["evidence_ids"], "$.evidence_ids", artifacts=artifacts, head_sha=value["head_sha"], tree_sha=value["tree_sha"], kind="evidence", stages={"targeted"} if value["state"] == "LOCAL_READY" else required, identity_mode=value["identity_mode"], snapshot_sha256=value["snapshot_sha256"])
        if value["state"] in {"FRESH_READY", "REVIEW_READY"}:
            if any(a.get("stage") not in required for a in artifacts.values() if isinstance(a, Mapping)):
                raise ReadinessError("receipt stage exceeds frozen review policy")
            _receipt_ids(value["gate_ids"], "$.gate_ids", artifacts=artifacts, head_sha=value["head_sha"], tree_sha=value["tree_sha"], kind="gate", stages=required, identity_mode=value["identity_mode"], snapshot_sha256=value["snapshot_sha256"])
        if any(v == "FOLLOW_UP" for v in value["dispositions"].values()):
            raise ReadinessError("active Spark FOLLOW_UP prevents readiness")
    return dict(value)


def _require_exact_set(actual: list[str], expected: list[str], path: str) -> None:
    if set(actual) != set(expected):
        raise ReadinessError("exact counterexample identity required", path)


def _receipt_ids(values: list[str], path: str, *, artifacts: Mapping[str, Any], head_sha: str, tree_sha: str, kind: str, stages: set[str], identity_mode: str = "git-exact-object", snapshot_sha256: str = "") -> None:
    if not values:
        raise ReadinessError("non-empty receipt IDs required", path)
    if not tree_sha:
        raise ReadinessError("receipt validation requires candidate tree", path)
    seen_stages: set[str] = set()
    for value in values:
        if not isinstance(value, str):
            raise ReadinessError("validated receipt ID required", path)
        old_id = re.fullmatch(r"(?:EVID|GATE)-(?:targeted|full|fresh)-[0-9a-f]{40}", value)
        snapshot_id = re.fullmatch(r"(?:EVID|GATE)-(?:targeted|full|fresh)-[0-9a-f]{40}-[0-9a-f]{64}", value)
        if identity_mode == "non-git-snapshot":
            if snapshot_id is None or not value.endswith(snapshot_sha256):
                raise ReadinessError("non-Git receipt ID must bind snapshot", path)
        elif old_id is None and snapshot_id is None:
            raise ReadinessError("validated receipt ID required", path)
        artifact = artifacts.get(value)
        if not isinstance(artifact, Mapping) or artifact.get("kind") != kind or artifact.get("stage") not in stages or artifact.get("head_sha") != head_sha or artifact.get("tree_sha") != tree_sha or artifact.get("decision") not in {"allow", "reused"}:
            raise ReadinessError("receipt ID is not bound to a validated artifact", path)
        if identity_mode == "non-git-snapshot":
            if artifact.get("identity_mode") != identity_mode or artifact.get("snapshot_sha256") != snapshot_sha256:
                raise ReadinessError("receipt artifact snapshot mismatch", path)
        elif "identity_mode" in artifact and (artifact.get("identity_mode") != "git-exact-object" or artifact.get("snapshot_sha256") != ""):
            raise ReadinessError("receipt artifact Git identity mismatch", path)
        stage = artifact["stage"]
        if stage in seen_stages:
            raise ReadinessError("one receipt per required stage required", path)
        seen_stages.add(stage)
    if seen_stages != set(stages) or len(values) != len(stages):
        raise ReadinessError("receipt stages must equal frozen review policy", path)


def transition(previous: Mapping[str, Any], target: str, *, base_sha: str, head_sha: str, tree_sha: str = "", identity_mode: str | None = None, snapshot_sha256: str | None = None, prior_snapshot_sha256: str | None = None, delta_sha256: str | None = None, counterexample_ids: list[str] | None = None, red_counterexamples: list[str] | None = None, green_counterexamples: list[str] | None = None, spark_findings: list[str] | None = None, spark_audit_count: int | None = None, dispositions: Mapping[str, str] | None = None, gate_ids: list[str] | None = None, evidence_ids: list[str] | None = None, receipt_artifacts: Mapping[str, Mapping[str, Any]] | None = None, author_closure_sha256: str | None = None, review_ready: bool = False, review_packet: Mapping[str, Any] | None = None, independent_artifact: Mapping[str, Any] | None = None, independent_lineage: Mapping[str, str] | None = None, independent_reviewed_scope: list[str] | None = None, expected_delta_sha256: str | None = None, prior_artifact: Mapping[str, Any] | None = None, evidence_bundle: Mapping[str, Any] | list[Mapping[str, Any]] | None = None, closure_binding_receipt: Mapping[str, Any] | None = None, expected_escalation_trigger: str | None = None, expected_escalation_evidence_ref: str | None = None, expected_distinct_reviewer_task_id: str | None = None, expected_prior_reviewer_task_id: str | None = None, updated_at: str | None = None) -> dict[str, Any]:
    prev = validate_state(dict(previous))
    if target not in STATES or _ALLOWED[prev["state"]] != target:
        raise ReadinessError(f"illegal state jump {prev['state']} -> {target}", "$.state")
    if base_sha != prev["base_sha"]:
        raise ReadinessError("baseline head drift", "$.base_sha")
    if not isinstance(head_sha, str) or len(head_sha) != 40:
        raise ReadinessError("candidate exact head required", "$.head_sha")
    selected_mode = previous.get("identity_mode", "git-exact-object") if identity_mode is None else identity_mode
    selected_snapshot = previous.get("snapshot_sha256", "") if snapshot_sha256 is None else snapshot_sha256
    # Once a non-Git snapshot is active, an omitted prior snapshot means the
    # immediately preceding state's current snapshot.  Reusing the previous
    # state's *prior* value would let two same-head worktree states collide.
    if prior_snapshot_sha256 is None:
        if (
            selected_mode == "non-git-snapshot"
            and previous.get("identity_mode") == "non-git-snapshot"
            and previous.get("snapshot_sha256")
            and selected_snapshot != previous.get("snapshot_sha256")
        ):
            selected_prior_snapshot = previous["snapshot_sha256"]
        else:
            selected_prior_snapshot = previous.get("prior_snapshot_sha256")
    else:
        selected_prior_snapshot = prior_snapshot_sha256
    selected_delta = delta_sha256 if delta_sha256 is not None else previous.get("delta_sha256")
    identity_changed = (
        selected_mode == "git-exact-object"
        and head_sha != prev["head_sha"]
    ) or (
        selected_mode == "non-git-snapshot"
        and previous.get("identity_mode") == "non-git-snapshot"
        and selected_snapshot != previous.get("snapshot_sha256")
        and selected_prior_snapshot not in (None, "")
    )
    if identity_changed:
        computed_delta = identity_delta_sha256(
            identity_mode=selected_mode,
            base_sha=base_sha,
            prior_head_sha=prev["head_sha"],
            head_sha=head_sha,
            prior_snapshot_sha256=selected_prior_snapshot,
            snapshot_sha256=selected_snapshot,
        )
        if delta_sha256 is not None and delta_sha256 != computed_delta:
            raise ReadinessError("identity delta does not match prior/current identity", "$.delta_sha256")
        selected_delta = computed_delta
    next_identity = _validate_identity_fields(
        {
            "identity_mode": selected_mode,
            "snapshot_sha256": selected_snapshot,
            "prior_snapshot_sha256": selected_prior_snapshot,
            "delta_sha256": selected_delta,
        }
    )
    # A candidate head may be introduced exactly once when moving from the
    # reproduced baseline into implementation. Every later state transition is
    # bound to that same candidate; a mid-run head change invalidates evidence.
    if prev["state"] != "BASELINE_REPRODUCED" and head_sha != prev["head_sha"]:
        raise ReadinessError("candidate head drift", "$.head_sha")
    timestamp = updated_at or _now()
    _timestamp(timestamp, "$.updated_at"); _fresh_timestamp(prev["updated_at"], timestamp)
    result = dict(prev)
    result.update({"state": target, "revision": prev["revision"] + 1, "updated_at": timestamp, "head_sha": head_sha, "tree_sha": tree_sha or prev["tree_sha"], "review_ready": False, "approved_review_artifact_sha256": "", "approved_review_packet_sha256": "", **next_identity})
    if counterexample_ids is not None: result["counterexample_ids"] = list(counterexample_ids)
    if red_counterexamples is not None: result["red_counterexamples"] = list(red_counterexamples)
    if green_counterexamples is not None: result["green_counterexamples"] = list(green_counterexamples)
    if spark_findings is not None: result["spark_findings"] = list(spark_findings)
    if spark_audit_count is not None:
        if type(spark_audit_count) is not int or not 0 <= spark_audit_count <= 3:
            raise ReadinessError("spark_audit_count")
        result["spark_audit_count"] = spark_audit_count
    if dispositions is not None: result["dispositions"] = dict(dispositions)
    if gate_ids is not None: result["gate_ids"] = list(gate_ids)
    if evidence_ids is not None: result["evidence_ids"] = list(evidence_ids)
    if receipt_artifacts is not None:
        if not isinstance(receipt_artifacts, Mapping):
            raise ReadinessError("receipt_artifacts mapping required")
        result["receipt_artifacts"] = {str(k): dict(v) for k, v in receipt_artifacts.items()}
    if author_closure_sha256 is not None:
        if not isinstance(author_closure_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", author_closure_sha256):
            raise ReadinessError("author closure SHA-256 required")
        result["author_closure_sha256"] = author_closure_sha256
    if target == "BASELINE_REPRODUCED":
        ids = result["counterexample_ids"]
        if not ids or not result["red_counterexamples"]:
            raise ReadinessError("baseline must name RED counterexamples")
        _require_exact_set(result["red_counterexamples"], ids, "$.red_counterexamples")
        if head_sha != prev["base_sha"]:
            raise ReadinessError("baseline must use exact base head", "$.head_sha")
    if target == "INNER_AUDIT_COMPLETE":
        if spark_audit_count is None:
            raise ReadinessError("explicit bounded Spark audit count required")
        if result["spark_audit_count"] == 0 and (result["spark_findings"] or result["dispositions"]):
            raise ReadinessError("zero-audit decision cannot carry Spark findings")
        if set(result["dispositions"]) != set(result["spark_findings"]):
            raise ReadinessError("every Spark finding must be dispositioned")
        if any(v not in {"FIXED", "DISAGREE", "FOLLOW_UP"} for v in result["dispositions"].values()):
            raise ReadinessError("invalid Spark disposition")
    if target in {"LOCAL_READY", "FRESH_READY", "REVIEW_READY"}:
        if not result["author_closure_sha256"]:
            raise ReadinessError("validated author closure artifact required")
        required_stages = set(result["required_stages"])
        evidence_stages = {"targeted"} if target == "LOCAL_READY" else required_stages
        _receipt_ids(result["evidence_ids"], "$.evidence_ids", artifacts=result["receipt_artifacts"], head_sha=head_sha, tree_sha=result["tree_sha"], kind="evidence", stages=evidence_stages, identity_mode=result["identity_mode"], snapshot_sha256=result["snapshot_sha256"])
        if target in {"FRESH_READY", "REVIEW_READY"}:
            # A fresh route may not smuggle unbudgeted full/fresh artifacts into
            # a low/medium policy, even if those receipts are otherwise green.
            if any(a.get("stage") not in required_stages for a in result["receipt_artifacts"].values() if isinstance(a, Mapping)):
                raise ReadinessError("receipt stage exceeds frozen review policy")
        if not result["red_counterexamples"] or not result["green_counterexamples"] or set(result["red_counterexamples"]) != set(result["green_counterexamples"]):
            raise ReadinessError("baseline RED IDs must equal candidate GREEN IDs")
        if any(v == "FOLLOW_UP" for v in result["dispositions"].values()):
            raise ReadinessError("active Spark FOLLOW_UP prevents readiness")
    if target in {"FRESH_READY", "REVIEW_READY"}:
            _receipt_ids(result["gate_ids"], "$.gate_ids", artifacts=result["receipt_artifacts"], head_sha=head_sha, tree_sha=result["tree_sha"], kind="gate", stages=set(result["required_stages"]), identity_mode=result["identity_mode"], snapshot_sha256=result["snapshot_sha256"])
    if target == "REVIEW_READY":
        lineage_fields = {"dispatch_transcript_sha256", "task_id", "parent_task_id", "sender"}
        if review_packet is None or independent_artifact is None or not isinstance(independent_lineage, Mapping) or set(independent_lineage) != lineage_fields or not independent_reviewed_scope:
            raise ReadinessError("author review packet and formal Independent artifact required")
        if not isinstance(review_packet, Mapping) or list(review_packet.get("reviewed_scope", [])) != list(independent_reviewed_scope):
            raise ReadinessError("review scope is not caller-bound")
        packet_basis = review_packet.get("decision_basis") if isinstance(review_packet, Mapping) else None
        if not isinstance(packet_basis, Mapping) or packet_basis.get("review_policy_sha256") != result["review_policy_sha256"] or packet_basis.get("required_stages") != result["required_stages"] or packet_basis.get("classifier_identity") != result["classifier_identity"] or packet_basis.get("high_risk_triggers") != result["high_risk_triggers"]:
            raise ReadinessError("review packet policy identity mismatch")
        try:
            approved = ingest_independent_artifact(
                review_packet,
                independent_artifact,
                expected_dispatch_transcript_sha256=independent_lineage["dispatch_transcript_sha256"],
                expected_task_id=independent_lineage["task_id"],
                expected_parent_task_id=independent_lineage["parent_task_id"],
                expected_sender=independent_lineage["sender"],
                expected_reviewer_model=result["reviewer_model"],
                expected_reasoning_effort=result["reasoning_effort"],
                expected_review_risk=result["review_risk"],
                expected_reviewer_route=result["reviewer_route"],
                expected_required_stages=result["required_stages"],
                expected_classifier_identity=result["classifier_identity"],
                expected_high_risk_triggers=result["high_risk_triggers"],
                expected_review_policy_sha256=result["review_policy_sha256"],
                expected_reference_identity_sha256=packet_basis.get("reference_identity_sha256"),
                expected_operating_domain_sha256=packet_basis.get("operating_domain_sha256"),
                expected_acceptance_thresholds_sha256=packet_basis.get("acceptance_thresholds_sha256"),
                expected_invariants_sha256=packet_basis.get("invariants_sha256"),
                expected_non_goals_sha256=packet_basis.get("non_goals_sha256"),
                expected_identity_mode=result["identity_mode"],
                expected_snapshot_sha256=result["snapshot_sha256"],
                expected_prior_snapshot_sha256=result["prior_snapshot_sha256"],
                expected_delta_sha256=expected_delta_sha256,
                prior_artifact=prior_artifact,
                evidence_bundle=evidence_bundle,
                closure_binding_receipt=closure_binding_receipt,
                expected_escalation_trigger=expected_escalation_trigger,
                expected_escalation_evidence_ref=expected_escalation_evidence_ref,
                expected_distinct_reviewer_task_id=expected_distinct_reviewer_task_id,
                expected_prior_reviewer_task_id=expected_prior_reviewer_task_id,
            )
        except TraceError as exc:
            raise ReadinessError("Independent artifact/lineage mismatch") from exc
        if approved["verdict"] != "APPROVE":
            raise ReadinessError("Independent artifact did not approve")
        result["approved_review_artifact_sha256"] = approved["independent_artifact_sha256"]
        result["approved_review_packet_sha256"] = canonical_sha256(review_packet)
        result["review_ready"] = True
    if review_ready:
        raise ReadinessError("author cannot self-assert review_ready")
    return validate_state(result)


class StateStore:
    """Persist state beneath an explicit mission root without following symlinks."""
    def __init__(self, root: str | pathlib.Path):
        self.root = pathlib.Path(root)
        if self.root.exists() and self.root.is_symlink():
            raise ReadinessError("mission root symlink forbidden")
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "readiness-state.json"
        if self.path.exists() and self.path.is_symlink():
            raise ReadinessError("state symlink forbidden")

    def load(self) -> dict[str, Any]:
        if not self.path.exists():
            raise ReadinessError("state does not exist")
        return validate_state(json.loads(self.path.read_text(encoding="utf-8")))

    def save(self, state: Mapping[str, Any]) -> str:
        validated = validate_state(dict(state))
        payload = canonical_json(validated) + "\n"
        fd, tmp_name = tempfile.mkstemp(prefix=".readiness-", dir=str(self.root))
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(payload); handle.flush(); os.fsync(handle.fileno())
            os.replace(tmp_name, self.path)
        finally:
            if os.path.exists(tmp_name): os.unlink(tmp_name)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


validate_readiness_state = validate_state

"""Deterministic, GitHub-free PR trace packets and reviewer decision binding."""
from __future__ import annotations

import hashlib
import os
import re
from typing import Any, Mapping, Sequence

from .contracts import (
    ContractError,
    canonical_sha256,
    counterexample_sha256 as _frozen_counterexample_sha256,
    validate_closure_binding_receipt,
    validate_pre_execution_closure_authority,
    _id,
    _int,
    _sha,
    _str,
)
from .evidence import privacy_scan
from .review_policy import HIGH_RISK_TRIGGERS


class TraceError(ContractError):
    pass


PUBLIC_AUTHOR_ENV = "CODEX_GOV_AUTHOR_ACCOUNT"
PUBLIC_REVIEWER_ENV = "CODEX_GOV_REVIEWER_ACCOUNT"
DEFAULT_AUTHOR_ACCOUNT = "your-developer-account"
DEFAULT_REVIEWER_ACCOUNT = "your-reviewer-account"
_PUBLIC_ACCOUNT_RE = re.compile(r"[a-z0-9][a-z0-9._-]{1,63}\Z", re.IGNORECASE)


def public_account(kind: str) -> str:
    """Return a configured, portable public author/reviewer account label."""
    if kind not in {"author", "reviewer"}:
        raise TraceError("unknown public account kind")
    env_name, fallback = (
        (PUBLIC_AUTHOR_ENV, DEFAULT_AUTHOR_ACCOUNT)
        if kind == "author"
        else (PUBLIC_REVIEWER_ENV, DEFAULT_REVIEWER_ACCOUNT)
    )
    value = os.environ.get(env_name, fallback).strip() or fallback
    if not _PUBLIC_ACCOUNT_RE.fullmatch(value):
        raise TraceError(f"invalid {kind} account configuration")
    return value


_FORMAL_CONTEXT_MODES = frozenset(
    {"independent_clean_room", "delta_continuation", "escalated_fresh"}
)
# Formal identity has two deliberately separate, canonical wire modes.
# ``git-exact-object`` is also used by the Spark transcript contract.  Legacy
# packets that omit the union are read as this Git mode internally, but once a
# mode field appears no alternate spelling is accepted.
_IDENTITY_MODE_ALIASES = {
    "git-exact-object": "git-exact-object",
    "non-git-snapshot": "non-git-snapshot",
}
_RISK_LEVELS = frozenset({"low", "medium", "high"})
_REVIEWER_DEFAULTS = {
    "low": ("general", "gpt-5.6-sol", "high"),
    "medium": ("general", "gpt-5.6-sol", "high"),
    "high": ("high_risk", "gpt-5.6-sol", "xhigh"),
}
_FINDING_LABELS = frozenset(
    {"BLOCKING", "NON_BLOCKING", "NIT", "QUESTION", "FOLLOW_UP", "CONTRACT_CHALLENGE"}
)
_DISPOSITIONS = frozenset({"FIXED", "DISAGREE", "FOLLOW_UP", "OPEN"})
_BLOCKER_ADMISSIONS = frozenset(
    {"DELTA_INTRODUCED", "ORIGINAL_SCOPE_MISSED", "NEW_FALSIFIABLE_EVIDENCE"}
)
_ESCALATION_TRIGGERS = frozenset(
    {
        "ACCEPTANCE_ENVELOPE_DRIFT",
        "REFERENCE_DOMAIN_THRESHOLD_DRIFT",
        "PATH_SET_SCOPE_DRIFT",
        "RISK_ESCALATION",
        "MATERIAL_REWRITE",
        "REVIEWER_PARTICIPATED",
        "LINEAGE_LOSS",
        "ORIGINAL_SCOPE_MISSED_P1",
        "NEW_FALSIFIABLE_P1_EVIDENCE",
        "POST_REVIEW_INCIDENT",
        "TWO_ROUND_NON_CONVERGENCE",
        "CONTRACT_DISPUTE",
        "REVIEW_HOOK_ROUTING_GOVERNANCE_CHANGE",
        "PACKET_EVIDENCE_IDENTITY_INVALIDATED",
        "REVIEW_POLICY_DRIFT",
        "PRIOR_COVERAGE_INCOMPLETE",
    }
)
_LEGACY_ARTIFACT_FIELDS = frozenset(
    {
        "schema",
        "reviewer_login",
        "reviewer_model",
        "reasoning_effort",
        "fork_turns",
        "context_mode",
        "report_only",
        "reviewer_is_writer",
        "head_sha",
        "tree_sha",
        "coverage_status",
        "reviewed_scope",
        "unreviewed_scope",
        "verdict",
        "artifact_sha256",
        "dispatch_transcript_sha256",
        "task_id",
        "parent_task_id",
        "sender",
    }
)
_FULL_FINDING_FIELDS = frozenset(
    {
        "id",
        "severity",
        "label",
        "attribution",
        "location",
        "contract_clause",
        "counterexample",
        "impact",
        "smallest_acceptable_outcome",
        "acceptance_check",
        "blocker_admission",
        "admission_evidence_ref",
        "disposition",
    }
)
_LEGACY_FINDING_FIELDS = frozenset(
    {"id", "severity", "label", "attribution", "location", "counterexample", "disposition"}
)
_DECISION_BASIS_FIELDS = frozenset(
    {
        "acceptance_envelope_sha256",
        "diff_sha256",
        "reviewed_dependency_scope_sha256",
        "evidence_bundle_sha256",
        "evidence_denominator",
        "review_risk",
        "reviewer_route",
        "reviewer_model",
        "reasoning_effort",
    }
)
_DECISION_BASIS_LINEAGE_FIELDS = frozenset({"prior_head_sha", "delta_sha256"})
_DECISION_BASIS_CLOSURE_FIELDS = frozenset({"closure_authority"})
_IDENTITY_FIELDS = frozenset({"identity_mode", "snapshot_sha256", "prior_snapshot_sha256"})
_DECISION_BASIS_POLICY_FIELDS = frozenset(
    {
        "required_stages",
        "classifier_identity",
        "high_risk_triggers",
        "review_policy_sha256",
        "reference_identity_sha256",
        "operating_domain_sha256",
        "acceptance_thresholds_sha256",
        "invariants_sha256",
        "non_goals_sha256",
    }
)
_DISPATCH_LINEAGE_FIELDS = frozenset(
    {"dispatch_transcript_sha256", "task_id", "parent_task_id", "sender"}
)
_FORMAL_ARTIFACT_FIELDS = frozenset(
    {
        "schema",
        "reviewer_login",
        "reviewer_model",
        "reasoning_effort",
        "reviewer_route",
        "review_risk",
        "fork_turns",
        "context_mode",
        "report_only",
        "reviewer_is_writer",
        "base_sha",
        "head_sha",
        "tree_sha",
        "diff_sha256",
        "coverage_status",
        "reviewed_scope",
        "unreviewed_scope",
        "review_packet_sha256",
        "acceptance_envelope_sha256",
        "reviewed_dependency_scope_sha256",
        "evidence_bundle_sha256",
        "evidence_denominator",
        "prior_review_artifact_sha256",
        "prior_head_sha",
        "delta_sha256",
        "reviewer_continuity_id",
        "run_id",
        "escalation_trigger",
        "escalation_evidence_ref",
        "findings",
        "findings_sha256",
        "closures",
        "closures_sha256",
        "known_limitations",
        "dispatch_lineage",
        "verdict",
        "artifact_sha256",
        "required_stages",
        "classifier_identity",
        "high_risk_triggers",
        "review_policy_sha256",
        "reference_identity_sha256",
        "operating_domain_sha256",
        "acceptance_thresholds_sha256",
        "invariants_sha256",
        "non_goals_sha256",
        "closure_matrix",
        "closure_matrix_sha256",
        "identity_mode",
        "snapshot_sha256",
        "prior_snapshot_sha256",
    }
)
# The previous v16 formal shape already carried the policy and closure matrix;
# only the identity union is new.  This keeps old Git fixtures readable while
# callers that need a new approval still bind the canonical identity below.
_LEGACY_FORMAL_ARTIFACT_FIELDS = _FORMAL_ARTIFACT_FIELDS - _IDENTITY_FIELDS


def _identity_mode(value: Any, path: str) -> str:
    if not isinstance(value, str) or value not in _IDENTITY_MODE_ALIASES:
        raise TraceError("identity mode", path)
    return value


def _identity_kind(value: str) -> str:
    """Return the canonical identity family for a wire mode alias."""
    return _IDENTITY_MODE_ALIASES[value]


def _snapshot_sha(value: Any, path: str, *, allow_empty: bool) -> str:
    if allow_empty and value == "":
        return ""
    return _sha256(value, path)


def identity_delta_sha256(
    *,
    identity_mode: str,
    base_sha: str,
    prior_head_sha: str,
    head_sha: str,
    prior_snapshot_sha256: str | None,
    snapshot_sha256: str,
) -> str:
    """Compute the caller-bound old/new identity transition digest.

    The object is intentionally public so a writer/reviewer cannot invent a
    plausible-looking ``delta_sha256`` string.  Git mode still includes the
    old/new heads; non-Git mode additionally includes the two content
    snapshots and may keep the Git head unchanged.
    """
    mode = _IDENTITY_MODE_ALIASES.get(identity_mode)
    if mode is None:
        raise TraceError("identity mode")
    base = _sha(base_sha, "$.base_sha")
    previous = _sha(prior_head_sha, "$.prior_head_sha")
    current = _sha(head_sha, "$.head_sha")
    if mode == "git-exact-object":
        if prior_snapshot_sha256 not in (None, "") or snapshot_sha256 != "":
            raise TraceError("Git identity does not carry content snapshots")
        prior_snapshot: str | None = None
        current_snapshot = ""
    else:
        prior_snapshot = _sha256(prior_snapshot_sha256, "$.prior_snapshot_sha256")
        current_snapshot = _sha256(snapshot_sha256, "$.snapshot_sha256")
        if prior_snapshot == current_snapshot:
            raise TraceError("non-Git continuation requires a changed snapshot")
    return canonical_sha256(
        {
            "schema": "review-identity-delta.v16",
            "identity_mode": mode,
            "base_sha": base,
            "prior_head_sha": previous,
            "head_sha": current,
            "prior_snapshot_sha256": prior_snapshot,
            "snapshot_sha256": current_snapshot,
        }
    )


def _identity_fields(
    value: Mapping[str, Any],
    path: str,
    *,
    required: bool,
) -> dict[str, Any]:
    """Validate the formal identity union and return normalized fields.

    Legacy Git fixtures omit these fields.  They are read as the old
    git-exact-object shape (empty content snapshots), while any packet that
    carries one new identity field must carry the complete union.
    """
    fields = {"identity_mode", "snapshot_sha256", "prior_snapshot_sha256"}
    present = set(value) & fields
    if not present:
        if required:
            raise TraceError("formal identity required", path)
        return {
            "identity_mode": "git-exact-object",
            "snapshot_sha256": "",
            "prior_snapshot_sha256": None,
            "identity_strict": False,
        }
    if present != fields:
        raise TraceError("complete formal identity required", path)
    wire_mode = _identity_mode(value["identity_mode"], f"{path}.identity_mode")
    mode = _identity_kind(wire_mode)
    current = value["snapshot_sha256"]
    previous = value["prior_snapshot_sha256"]
    if mode == "git-exact-object":
        if current != "" or previous not in (None, ""):
            raise TraceError("Git identity requires empty snapshots", path)
        current = ""
        previous = None
    else:
        current = _snapshot_sha(current, f"{path}.snapshot_sha256", allow_empty=False)
        if previous is not None:
            previous = _snapshot_sha(previous, f"{path}.prior_snapshot_sha256", allow_empty=False)
    if previous is not None and current == previous:
        raise TraceError("continuation snapshots must differ", path)
    return {
        "identity_mode": wire_mode,
        "identity_kind": mode,
        "snapshot_sha256": current,
        "prior_snapshot_sha256": previous,
        "identity_strict": True,
    }


def _sha256(value: Any, path: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise TraceError("SHA-256 required", path)
    return value


def _text(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value or any(ord(char) < 0x20 for char in value):
        raise TraceError("non-empty text required", path)
    return value


def _scope(value: Any, path: str, *, nonempty: bool) -> list[str]:
    if not isinstance(value, list) or (nonempty and not value) or any(not isinstance(item, str) or not item for item in value):
        raise TraceError("scope array required", path)
    if len(set(value)) != len(value):
        raise TraceError("duplicate scope entries", path)
    return list(value)


def _policy_fields(value: Mapping[str, Any], path: str, *, required: bool = False) -> dict[str, Any]:
    """Validate the frozen review-policy identity carried by a packet.

    Older v16 fixtures predate this binding.  They remain readable when all
    policy fields are absent, but a packet that carries any one of them must
    carry and validate the complete identity.  Caller-bound ingestion passes
    the expected values, so a legacy packet cannot create a new approval.
    """
    present = set(value) & _DECISION_BASIS_POLICY_FIELDS
    if not present:
        if required:
            raise TraceError("review policy identity required", path)
        return {}
    if present != _DECISION_BASIS_POLICY_FIELDS:
        raise TraceError("complete review policy identity required", path)
    stages = _scope(value["required_stages"], f"{path}.required_stages", nonempty=True)
    if tuple(stages) not in (("targeted",), ("targeted", "full"), ("targeted", "full", "fresh")):
        raise TraceError("required_stages must be an ordered prefix", f"{path}.required_stages")
    classifier = value["classifier_identity"]
    if not isinstance(classifier, str) or any(ord(char) < 0x20 for char in classifier):
        raise TraceError("classifier identity", f"{path}.classifier_identity")
    triggers = value["high_risk_triggers"]
    if not isinstance(triggers, list) or any(not isinstance(item, str) or not item for item in triggers) or len(set(triggers)) != len(triggers):
        raise TraceError("high-risk trigger array", f"{path}.high_risk_triggers")
    if any(item not in HIGH_RISK_TRIGGERS for item in triggers):
        raise TraceError("unknown high-risk trigger", f"{path}.high_risk_triggers")
    for field in _DECISION_BASIS_POLICY_FIELDS - {"required_stages", "classifier_identity", "high_risk_triggers", "review_policy_sha256"}:
        _sha256(value[field], f"{path}.{field}")
    identity = {
        "required_stages": stages,
        "review_risk": value["review_risk"],
        "reviewer_route": value["reviewer_route"],
        "reviewer_model": value["reviewer_model"],
        "reasoning_effort": value["reasoning_effort"],
        "classifier_identity": classifier,
        "high_risk_triggers": list(triggers),
    }
    digest = _sha256(value["review_policy_sha256"], f"{path}.review_policy_sha256")
    if canonical_sha256(identity) != digest:
        raise TraceError("review policy hash mismatch", f"{path}.review_policy_sha256")
    return {
        "required_stages": stages,
        "classifier_identity": classifier,
        "high_risk_triggers": list(triggers),
        "review_policy_sha256": digest,
        "reference_identity_sha256": value["reference_identity_sha256"],
        "operating_domain_sha256": value["operating_domain_sha256"],
        "acceptance_thresholds_sha256": value["acceptance_thresholds_sha256"],
        "invariants_sha256": value["invariants_sha256"],
        "non_goals_sha256": value["non_goals_sha256"],
    }


def _fork_turns(value: Any, path: str, *, require_none: bool) -> str:
    if not isinstance(value, str):
        raise TraceError("fork_turns string required", path)
    if require_none:
        if value != "none":
            raise TraceError("fresh review requires fork_turns=none", path)
        return value
    if value == "none" or value == "all" or (value.isdecimal() and int(value) > 0):
        return value
    raise TraceError("invalid fork_turns", path)


def _review_decision(coverage: str, unreviewed: Sequence[str], findings: Sequence[Mapping[str, Any]], verdict: Any, path: str) -> None:
    if verdict not in {"APPROVE", "REQUEST_CHANGES", None}:
        raise TraceError("verdict", path)
    if coverage == "COMPLETE" and unreviewed:
        raise TraceError("complete coverage cannot have unreviewed scope", path)
    active = [finding for finding in findings if finding["disposition"] != "FIXED"]
    has_active_blocker = any(finding["severity"] == "P1" or finding["label"] == "BLOCKING" for finding in active)
    if verdict == "APPROVE" and (coverage != "COMPLETE" or unreviewed or has_active_blocker):
        raise TraceError("APPROVE requires COMPLETE coverage and no active P1/BLOCKING", path)
    if verdict == "REQUEST_CHANGES" and not has_active_blocker:
        raise TraceError("REQUEST_CHANGES requires an active P1/BLOCKING", path)


def _legacy_finding(value: Any, path: str) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != _LEGACY_FINDING_FIELDS:
        raise TraceError("legacy finding fields", path)
    severity = _text(value["severity"], f"{path}.severity")
    label = _text(value["label"], f"{path}.label")
    if severity not in {"P1", "P2", "P3"}:
        raise TraceError("severity", f"{path}.severity")
    if label not in _FINDING_LABELS:
        raise TraceError("label", f"{path}.label")
    if severity == "P1" and label != "BLOCKING":
        raise TraceError("P1 must be BLOCKING", f"{path}.label")
    if severity != "P1" and label == "BLOCKING":
        raise TraceError("P2/P3 cannot be BLOCKING", f"{path}.label")
    disposition = _text(value["disposition"], f"{path}.disposition")
    if disposition not in _DISPOSITIONS:
        raise TraceError("disposition", f"{path}.disposition")
    return {
        "id": _id(value["id"], f"{path}.id"),
        "severity": severity,
        "label": label,
        "attribution": _text(value["attribution"], f"{path}.attribution"),
        "location": _text(value["location"], f"{path}.location"),
        "counterexample": _text(value["counterexample"], f"{path}.counterexample"),
        "disposition": disposition,
    }


def _full_finding(value: Any, path: str) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != _FULL_FINDING_FIELDS:
        raise TraceError("full finding fields", path)
    severity = _text(value["severity"], f"{path}.severity")
    label = _text(value["label"], f"{path}.label")
    if severity not in {"P1", "P2", "P3"}:
        raise TraceError("severity", f"{path}.severity")
    if label not in _FINDING_LABELS:
        raise TraceError("label", f"{path}.label")
    if severity == "P1" and label != "BLOCKING":
        raise TraceError("P1 must be BLOCKING", f"{path}.label")
    if severity != "P1" and label == "BLOCKING":
        raise TraceError("P2/P3 cannot be BLOCKING", f"{path}.label")
    blocker_admission = value["blocker_admission"]
    admission_ref = value["admission_evidence_ref"]
    if severity == "P1":
        if blocker_admission == "NONE":
            if admission_ref != "":
                raise TraceError("unadmitted P1 cannot carry admission evidence", path)
        elif blocker_admission not in _BLOCKER_ADMISSIONS or not isinstance(admission_ref, str) or not admission_ref:
            raise TraceError("P1 admission requires recognized mode and evidence", path)
    elif blocker_admission != "NONE" or admission_ref != "":
        raise TraceError("only P1 may carry blocker admission", path)
    disposition = _text(value["disposition"], f"{path}.disposition")
    if disposition not in _DISPOSITIONS:
        raise TraceError("disposition", f"{path}.disposition")
    return {
        "id": _id(value["id"], f"{path}.id"),
        "severity": severity,
        "label": label,
        "attribution": _text(value["attribution"], f"{path}.attribution"),
        "location": _text(value["location"], f"{path}.location"),
        "contract_clause": _text(value["contract_clause"], f"{path}.contract_clause"),
        "counterexample": _text(value["counterexample"], f"{path}.counterexample"),
        "impact": _text(value["impact"], f"{path}.impact"),
        "smallest_acceptable_outcome": _text(value["smallest_acceptable_outcome"], f"{path}.smallest_acceptable_outcome"),
        "acceptance_check": _text(value["acceptance_check"], f"{path}.acceptance_check"),
        "blocker_admission": blocker_admission,
        "admission_evidence_ref": admission_ref,
        "disposition": disposition,
    }


def _findings(value: Any, path: str, *, full: bool) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise TraceError("findings array", path)
    validator = _full_finding if full else None
    result: list[dict[str, Any]] = []
    for index, item in enumerate(value):
        if validator is not None:
            result.append(validator(item, f"{path}[{index}]"))
        elif isinstance(item, Mapping) and set(item) == _FULL_FINDING_FIELDS:
            result.append(_full_finding(item, f"{path}[{index}]"))
        else:
            result.append(_legacy_finding(item, f"{path}[{index}]"))
    if len({finding["id"] for finding in result}) != len(result):
        raise TraceError("duplicate findings", path)
    return result


def _closures(value: Any, findings: Sequence[Mapping[str, Any]], path: str) -> dict[str, str]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) or not isinstance(item, str) for key, item in value.items()):
        raise TraceError("closures", path)
    expected = {finding["id"] for finding in findings}
    if set(value) != expected:
        raise TraceError("every finding requires one closure", path)
    result = dict(value)
    if any(result[finding["id"]] != finding["disposition"] for finding in findings):
        raise TraceError("finding closure/disposition mismatch", path)
    return result


_CLOSURE_ENTRY_FIELDS = frozenset({"prior_disposition", "disposition", "evidence_ref", "counterexample_recheck"})
_CLOSURE_EVIDENCE_FIELDS = frozenset(
    {"case_id", "evidence_row_sha256", "log_sha256", "binding_sha256"}
)
_CLOSURE_RECHECK_FIELDS = frozenset(
    {
        "case_id",
        "evidence_row_sha256",
        "log_sha256",
        "binding_sha256",
        "counterexample_sha256",
        "kind",
        "result_sha256",
    }
)
_RECHECK_KINDS = frozenset({"EXECUTABLE_RESULT"})


def _closure_matrix(value: Any, path: str) -> dict[str, dict[str, Any]]:
    if not isinstance(value, Mapping):
        raise TraceError("closure matrix object required", path)
    result: dict[str, dict[str, Any]] = {}
    for finding_id, entry in value.items():
        if not isinstance(finding_id, str) or not finding_id or not isinstance(entry, Mapping) or set(entry) != _CLOSURE_ENTRY_FIELDS:
            raise TraceError("strict closure matrix entry", path)
        prior = _text(entry["prior_disposition"], f"{path}.{finding_id}.prior_disposition")
        disposition = _text(entry["disposition"], f"{path}.{finding_id}.disposition")
        if prior not in _DISPOSITIONS or disposition not in _DISPOSITIONS:
            raise TraceError("closure matrix disposition", path)
        evidence_ref = entry["evidence_ref"]
        recheck = entry["counterexample_recheck"]
        if isinstance(evidence_ref, str):
            if any(ord(char) < 0x20 for char in evidence_ref):
                raise TraceError("closure evidence reference", path)
        elif not isinstance(evidence_ref, Mapping):
            raise TraceError("closure evidence reference", path)
        if isinstance(recheck, str):
            if any(ord(char) < 0x20 for char in recheck):
                raise TraceError("counterexample recheck", path)
        elif not isinstance(recheck, Mapping):
            raise TraceError("counterexample recheck", path)
        if disposition == "FIXED" and (not evidence_ref or not recheck):
            raise TraceError("closed finding requires evidence and counterexample recheck", path)
        result[finding_id] = {
            "prior_disposition": prior,
            "disposition": disposition,
            "evidence_ref": evidence_ref,
            "counterexample_recheck": recheck,
        }
    return result


def _evidence_result_sha256(row: Mapping[str, Any]) -> str:
    """Hash the executable outcome, excluding mutable presentation fields."""
    material = {
        "case_id": row.get("case_id"),
        "finding_id": row.get("finding_id"),
        "counterexample_sha256": row.get("counterexample_sha256"),
        "gate_id": row.get("gate_id"),
        "stage": row.get("stage"),
        "entrypoint_id": row.get("entrypoint_id"),
        "decision": row.get("decision"),
        "actual_head": row.get("actual_head"),
        "tree_sha": row.get("tree_sha"),
        "identity_mode": row.get("identity_mode"),
        "snapshot_sha256": row.get("snapshot_sha256"),
        "closure_binding_receipt_sha256": row.get("closure_binding_receipt_sha256"),
        "closure_binding_sha256s": row.get("closure_binding_sha256s"),
        "counts": row.get("counts"),
        "log_sha256": row.get("log_sha256"),
    }
    return canonical_sha256(material)


def _counterexample_sha256(value: str) -> str:
    return _frozen_counterexample_sha256(value)


def _evidence_rows(
    bundle: Mapping[str, Any] | Sequence[Mapping[str, Any]],
    *,
    expected_bundle_sha256: str,
    expected_head_sha: str | None = None,
    expected_tree_sha: str | None = None,
    expected_identity_mode: str = "git-exact-object",
    expected_snapshot_sha256: str = "",
    closure_binding_receipt: Mapping[str, Any] | None = None,
    closure_authority: Mapping[str, Any] | None = None,
    expected_closure_binding_receipt_sha256: str | None = None,
    expected_closure_plan_sha256: str | None = None,
) -> dict[str, dict[str, Any]]:
    """Resolve evidence against the pre-run authority in the decision basis."""
    _identity_mode(expected_identity_mode, "$.expected_identity_mode")
    if expected_identity_mode == "git-exact-object":
        if expected_snapshot_sha256 != "":
            raise TraceError("Git evidence requires an empty snapshot")
    else:
        _sha256(expected_snapshot_sha256, "$.expected_snapshot_sha256")
    checked_closure_receipt: dict[str, Any] | None = None
    checked_closure_authority: dict[str, Any] | None = None
    expected_bindings_by_case: dict[str, list[dict[str, Any]]] = {}
    if closure_authority is not None:
        try:
            checked_closure_authority = (
                validate_pre_execution_closure_authority(closure_authority)
            )
        except ContractError as exc:
            raise TraceError(
                "invalid decision-basis pre-execution closure authority"
            ) from exc
    compatibility_expectations = (
        (
            expected_closure_binding_receipt_sha256,
            "closure_binding_receipt_sha256",
        ),
        (expected_closure_plan_sha256, "closure_plan_sha256"),
    )
    for expected, field in compatibility_expectations:
        if expected is not None and (
            checked_closure_authority is None
            or expected != checked_closure_authority[field]
        ):
            raise TraceError(
                "compatibility closure identity contradicts decision basis",
                f"$.{field}",
            )
    if closure_binding_receipt is None:
        if checked_closure_authority is not None:
            raise TraceError(
                "decision-basis closure authority requires pre-execution receipt"
            )
    else:
        if checked_closure_authority is None:
            raise TraceError(
                "closure receipt requires decision-basis pre-execution authority"
            )
        try:
            checked_closure_authority = (
                validate_pre_execution_closure_authority(
                    checked_closure_authority,
                    closure_binding_receipt=closure_binding_receipt,
                )
            )
            checked_closure_receipt = validate_closure_binding_receipt(
                closure_binding_receipt,
                expected_receipt_sha256=checked_closure_authority[
                    "closure_binding_receipt_sha256"
                ],
                expected_compiled_plan_sha256=checked_closure_authority[
                    "compiled_plan_sha256"
                ],
                expected_closure_plan_sha256=checked_closure_authority[
                    "closure_plan_sha256"
                ],
                expected_closure_plan_file_sha256=checked_closure_authority[
                    "closure_plan_file_sha256"
                ],
                expected_dispatch_transcript_file_sha256=(
                    checked_closure_authority[
                        "dispatch_transcript_file_sha256"
                    ]
                ),
            )
        except ContractError as exc:
            raise TraceError(
                "closure receipt contradicts decision-basis authority"
            ) from exc
        for binding in checked_closure_receipt["bindings"]:
            expected_bindings_by_case.setdefault(
                binding["evidence_row_id"],
                [],
            ).append(binding)
        for bindings in expected_bindings_by_case.values():
            bindings.sort(key=lambda item: item["binding_sha256"])
    bundle_clean: bool | None = None
    if isinstance(bundle, Mapping):
        rows = bundle.get("rows")
        supplied_hash = bundle.get("envelope_sha256")
        if not isinstance(rows, list):
            raise TraceError("current evidence bundle rows required")
        if not isinstance(supplied_hash, str) or supplied_hash != expected_bundle_sha256:
            raise TraceError("current evidence bundle hash mismatch")
        if expected_head_sha is not None and bundle.get("head_sha") != expected_head_sha:
            raise TraceError("current evidence bundle head mismatch")
        if expected_tree_sha is not None and bundle.get("tree_sha") != expected_tree_sha:
            raise TraceError("current evidence bundle tree mismatch")
        if bundle.get("identity_mode") != expected_identity_mode:
            raise TraceError("current evidence bundle identity mode mismatch")
        if bundle.get("snapshot_sha256") != expected_snapshot_sha256:
            raise TraceError("current evidence bundle snapshot mismatch")
        bundle_clean = bundle.get("clean")
        if type(bundle_clean) is not bool:
            raise TraceError("current evidence bundle clean identity required")
        if expected_identity_mode == "git-exact-object" and bundle_clean is not True:
            raise TraceError("Git evidence bundle must be clean")
        envelope_closure_fields = {
            "closure_binding_receipt_sha256", "closure_plan_sha256",
        } & set(bundle)
        if checked_closure_receipt is None:
            if envelope_closure_fields:
                raise TraceError("closure envelope fields require caller-bound receipt")
        elif (
            envelope_closure_fields
            != {"closure_binding_receipt_sha256", "closure_plan_sha256"}
            or bundle.get("closure_binding_receipt_sha256")
            != checked_closure_receipt["receipt_sha256"]
            or bundle.get("closure_plan_sha256")
            != checked_closure_receipt["closure_plan_sha256"]
        ):
            raise TraceError("current evidence bundle closure receipt mismatch")
        unsigned = dict(bundle)
        unsigned["envelope_sha256"] = ""
        if canonical_sha256(unsigned) != supplied_hash:
            raise TraceError("current evidence bundle hash invalid")
    elif isinstance(bundle, Sequence) and not isinstance(bundle, (str, bytes)):
        if checked_closure_receipt is not None:
            raise TraceError("formal closure requires a canonical receipt-bound evidence envelope")
        if expected_identity_mode == "non-git-snapshot":
            raise TraceError("non-Git closure requires a canonical evidence envelope")
        rows = list(bundle)
        # A bare row sequence is accepted only when its canonical wrapper is
        # caller-bound to the packet evidence hash.
        if canonical_sha256({"rows": rows}) != expected_bundle_sha256:
            raise TraceError("current evidence bundle hash mismatch")
    else:
        raise TraceError("current evidence bundle required")
    if not rows:
        raise TraceError("current evidence bundle rows required")
    resolved: dict[str, dict[str, Any]] = {}
    for index, raw in enumerate(rows):
        if not isinstance(raw, Mapping):
            raise TraceError("evidence row object required", f"$.evidence_bundle.rows[{index}]")
        row = dict(raw)
        case_id = row.get("case_id")
        log_sha = row.get("log_sha256")
        if not isinstance(case_id, str) or not case_id or not isinstance(log_sha, str):
            raise TraceError("evidence row case/log identity required", f"$.evidence_bundle.rows[{index}]")
        _sha256(log_sha, f"$.evidence_bundle.rows[{index}].log_sha256")
        if expected_head_sha is not None and row.get("actual_head") != expected_head_sha:
            raise TraceError("evidence row head mismatch", f"$.evidence_bundle.rows[{index}].actual_head")
        if expected_tree_sha is not None and row.get("tree_sha") != expected_tree_sha:
            raise TraceError("evidence row tree mismatch", f"$.evidence_bundle.rows[{index}].tree_sha")
        if row.get("identity_mode") != expected_identity_mode:
            raise TraceError("evidence row identity mode mismatch", f"$.evidence_bundle.rows[{index}].identity_mode")
        if row.get("snapshot_sha256") != expected_snapshot_sha256:
            raise TraceError("evidence row snapshot mismatch", f"$.evidence_bundle.rows[{index}].snapshot_sha256")
        row_dirty = row.get("dirty")
        if type(row_dirty) is not bool:
            raise TraceError("evidence row dirty identity required", f"$.evidence_bundle.rows[{index}].dirty")
        if expected_identity_mode == "git-exact-object" and row_dirty:
            raise TraceError("Git evidence row must be clean", f"$.evidence_bundle.rows[{index}].dirty")
        if bundle_clean is not None and row_dirty is bundle_clean:
            raise TraceError("evidence row clean/dirty identity mismatch", f"$.evidence_bundle.rows[{index}].dirty")
        binding_fields = {"finding_id", "counterexample_sha256"} & set(row)
        if binding_fields and binding_fields != {"finding_id", "counterexample_sha256"}:
            raise TraceError("complete evidence finding binding required", f"$.evidence_bundle.rows[{index}]")
        if binding_fields:
            _id(row["finding_id"], f"$.evidence_bundle.rows[{index}].finding_id")
            _sha256(row["counterexample_sha256"], f"$.evidence_bundle.rows[{index}].counterexample_sha256")
        row_closure_fields = {
            "closure_binding_receipt_sha256", "closure_binding_sha256s",
        } & set(row)
        if checked_closure_receipt is None:
            if row_closure_fields:
                raise TraceError(
                    "evidence closure fields require caller-bound receipt",
                    f"$.evidence_bundle.rows[{index}]",
                )
        else:
            if row_closure_fields != {
                "closure_binding_receipt_sha256", "closure_binding_sha256s",
            }:
                raise TraceError(
                    "closure-aware evidence row requires exact receipt fields",
                    f"$.evidence_bundle.rows[{index}]",
                )
            expected_row_bindings = expected_bindings_by_case.get(case_id, [])
            expected_binding_digests = [
                binding["binding_sha256"]
                for binding in expected_row_bindings
                if (
                    binding["gate_id"] == row.get("gate_id")
                    and binding["stage"] == row.get("stage")
                    and binding["entrypoint_id"] == row.get("entrypoint_id")
                )
            ]
            if (
                row["closure_binding_receipt_sha256"]
                != checked_closure_receipt["receipt_sha256"]
                or row["closure_binding_sha256s"] != expected_binding_digests
            ):
                raise TraceError(
                    "evidence row closure binding receipt mismatch",
                    f"$.evidence_bundle.rows[{index}]",
                )
        if case_id in resolved:
            raise TraceError("duplicate evidence case", f"$.evidence_bundle.rows[{index}].case_id")
        counts = row.get("counts")
        if not isinstance(counts, Mapping):
            raise TraceError("evidence row executable counts required", f"$.evidence_bundle.rows[{index}].counts")
        result_sha = _evidence_result_sha256(row)
        if "result_sha256" in row and row["result_sha256"] != result_sha:
            raise TraceError("evidence executable result hash mismatch", f"$.evidence_bundle.rows[{index}].result_sha256")
        resolved[case_id] = {
            "row": row,
            "evidence_row_sha256": canonical_sha256(row),
            "log_sha256": log_sha,
            "result_sha256": result_sha,
        }
    if checked_closure_receipt is not None:
        observed_bindings = sorted(
            digest
            for resolved_row in resolved.values()
            for digest in resolved_row["row"]["closure_binding_sha256s"]
        )
        expected_bindings = sorted(
            binding["binding_sha256"]
            for binding in checked_closure_receipt["bindings"]
        )
        if observed_bindings != expected_bindings:
            raise TraceError("evidence closure binding denominator mismatch")
    return resolved


def _validate_fixed_closure(
    *,
    finding: Mapping[str, Any],
    entry: Mapping[str, Any],
    evidence_rows: Mapping[str, Mapping[str, Any]],
    closure_bindings: Mapping[str, Mapping[str, Any]],
    path: str,
) -> None:
    evidence = entry.get("evidence_ref")
    recheck = entry.get("counterexample_recheck")
    if not isinstance(evidence, Mapping) or set(evidence) != _CLOSURE_EVIDENCE_FIELDS:
        raise TraceError("FIXED closure requires structured evidence reference", path)
    if not isinstance(recheck, Mapping) or set(recheck) != _CLOSURE_RECHECK_FIELDS:
        raise TraceError("FIXED closure requires structured counterexample recheck", path)
    for field in ("case_id", "evidence_row_sha256", "log_sha256", "binding_sha256"):
        if not isinstance(evidence.get(field), str) or not evidence[field]:
            raise TraceError("FIXED closure evidence identity required", path)
    for field in ("case_id", "evidence_row_sha256", "log_sha256", "binding_sha256", "counterexample_sha256", "result_sha256"):
        if not isinstance(recheck.get(field), str) or not recheck[field]:
            raise TraceError("FIXED closure recheck identity required", path)
    if recheck.get("kind") not in _RECHECK_KINDS:
        raise TraceError("FIXED closure executable recheck kind required", path)
    _sha256(evidence["evidence_row_sha256"], f"{path}.evidence_ref.evidence_row_sha256")
    _sha256(evidence["log_sha256"], f"{path}.evidence_ref.log_sha256")
    _sha256(evidence["binding_sha256"], f"{path}.evidence_ref.binding_sha256")
    _sha256(recheck["evidence_row_sha256"], f"{path}.counterexample_recheck.evidence_row_sha256")
    _sha256(recheck["log_sha256"], f"{path}.counterexample_recheck.log_sha256")
    _sha256(recheck["binding_sha256"], f"{path}.counterexample_recheck.binding_sha256")
    _sha256(recheck["counterexample_sha256"], f"{path}.counterexample_recheck.counterexample_sha256")
    _sha256(recheck["result_sha256"], f"{path}.counterexample_recheck.result_sha256")
    case_id = evidence.get("case_id")
    if case_id not in evidence_rows:
        raise TraceError("closure evidence case is not in current bundle", path)
    row = evidence_rows[case_id]
    if (
        evidence.get("evidence_row_sha256") != row["evidence_row_sha256"]
        or evidence.get("log_sha256") != row["log_sha256"]
    ):
        raise TraceError("closure evidence row/log identity mismatch", path)
    raw_row = row["row"]
    original_counterexample_sha256 = _counterexample_sha256(finding["counterexample"])
    binding = closure_bindings.get(finding["id"])
    if binding is None:
        raise TraceError("finding absent from pre-execution closure binding receipt", path)
    if (
        binding.get("counterexample_sha256") != original_counterexample_sha256
        or binding.get("evidence_row_id") != case_id
        or binding.get("gate_id") != raw_row.get("gate_id")
        or binding.get("stage") != raw_row.get("stage")
        or binding.get("entrypoint_id") != raw_row.get("entrypoint_id")
        or raw_row.get("closure_binding_sha256s", []).count(
            binding.get("binding_sha256")
        ) != 1
        or evidence.get("binding_sha256") != binding.get("binding_sha256")
    ):
        raise TraceError("closure evidence case is not bound to the frozen finding plan", path)
    if (
        {"finding_id", "counterexample_sha256"} <= set(raw_row)
        and (
            raw_row["finding_id"] != binding["finding_id"]
            or raw_row["counterexample_sha256"] != binding["counterexample_sha256"]
        )
    ):
        raise TraceError("caller evidence annotation contradicts frozen finding plan", path)
    counts = raw_row.get("counts")
    count_fields = {"total", "ran", "passed", "failed", "skipped", "xfail", "unknown"}
    valid_counts = (
        isinstance(counts, Mapping)
        and set(counts) == count_fields
        and all(type(counts[key]) is int and counts[key] >= 0 for key in count_fields)
        and counts["total"] > 0
        and counts["total"] == counts["passed"] + counts["failed"] + counts["skipped"] + counts["unknown"]
        and counts["ran"] == counts["passed"] + counts["failed"]
        and counts["xfail"] <= counts["total"]
        and not any(counts[key] for key in ("failed", "skipped", "unknown", "xfail"))
    )
    if raw_row.get("decision") not in {"allow", "GREEN", "green"} or not valid_counts:
        raise TraceError("closure evidence executable result is not green", path)
    if (
        recheck.get("kind") not in _RECHECK_KINDS
        or recheck.get("case_id") != case_id
        or recheck.get("evidence_row_sha256") != row["evidence_row_sha256"]
        or recheck.get("log_sha256") != row["log_sha256"]
        or recheck.get("binding_sha256") != binding["binding_sha256"]
        or recheck.get("result_sha256") != row["result_sha256"]
        or recheck.get("counterexample_sha256") != original_counterexample_sha256
    ):
        raise TraceError("counterexample recheck is not bound to original executable result", path)


def _decision_basis(value: Any, path: str) -> dict[str, Any]:
    if (
        not isinstance(value, Mapping)
        or not _DECISION_BASIS_FIELDS.issubset(set(value))
        or set(value)
        - _DECISION_BASIS_FIELDS
        - _DECISION_BASIS_POLICY_FIELDS
        - _IDENTITY_FIELDS
        - _DECISION_BASIS_LINEAGE_FIELDS
        - _DECISION_BASIS_CLOSURE_FIELDS
    ):
        raise TraceError("strict decision basis required", path)
    risk = _text(value["review_risk"], f"{path}.review_risk")
    route = _text(value["reviewer_route"], f"{path}.reviewer_route")
    model = _text(value["reviewer_model"], f"{path}.reviewer_model")
    effort = _text(value["reasoning_effort"], f"{path}.reasoning_effort")
    if risk not in _RISK_LEVELS or (route, model, effort) != _REVIEWER_DEFAULTS[risk]:
        raise TraceError("risk/route/model/effort policy mismatch", path)
    result = {
        "acceptance_envelope_sha256": _sha256(value["acceptance_envelope_sha256"], f"{path}.acceptance_envelope_sha256"),
        "diff_sha256": _sha256(value["diff_sha256"], f"{path}.diff_sha256"),
        "reviewed_dependency_scope_sha256": _sha256(value["reviewed_dependency_scope_sha256"], f"{path}.reviewed_dependency_scope_sha256"),
        "evidence_bundle_sha256": _sha256(value["evidence_bundle_sha256"], f"{path}.evidence_bundle_sha256"),
        "evidence_denominator": _int(value["evidence_denominator"], f"{path}.evidence_denominator", minimum=1),
        "review_risk": risk,
        "reviewer_route": route,
        "reviewer_model": model,
        "reasoning_effort": effort,
    }
    result.update(_policy_fields(value, path, required=False))
    identity_present = bool(set(value) & _IDENTITY_FIELDS)
    if identity_present:
        identity = _identity_fields(value, path, required=True)
        result.update({field: identity[field] for field in _IDENTITY_FIELDS})
    for field in _DECISION_BASIS_LINEAGE_FIELDS:
        if field not in value:
            continue
        if field == "prior_head_sha":
            result[field] = _sha(value[field], f"{path}.{field}")
        else:
            result[field] = _sha256(value[field], f"{path}.{field}")
    if "closure_authority" in value:
        try:
            result["closure_authority"] = (
                validate_pre_execution_closure_authority(
                    value["closure_authority"],
                )
            )
        except ContractError as exc:
            raise TraceError(
                "invalid pre-execution closure authority",
                f"{path}.closure_authority",
            ) from exc
    return result


def _dispatch_lineage(
    value: Any,
    path: str,
    *,
    expected_dispatch_transcript_sha256: str | None = None,
    expected_task_id: str | None = None,
    expected_parent_task_id: str | None = None,
    expected_sender: str | None = None,
) -> dict[str, str]:
    if not isinstance(value, Mapping) or set(value) != _DISPATCH_LINEAGE_FIELDS:
        raise TraceError("strict dispatch lineage required", path)
    result = {
        "dispatch_transcript_sha256": _sha256(value["dispatch_transcript_sha256"], f"{path}.dispatch_transcript_sha256"),
        "task_id": _text(value["task_id"], f"{path}.task_id"),
        "parent_task_id": _text(value["parent_task_id"], f"{path}.parent_task_id"),
        "sender": _text(value["sender"], f"{path}.sender"),
    }
    if result["task_id"] == result["parent_task_id"]:
        raise TraceError("reviewer task must differ from parent", path)
    for field, expected in (
        ("dispatch_transcript_sha256", expected_dispatch_transcript_sha256),
        ("task_id", expected_task_id),
        ("parent_task_id", expected_parent_task_id),
        ("sender", expected_sender),
    ):
        if expected is not None and result[field] != expected:
            raise TraceError(f"Independent {field} mismatch", path)
    return result


def _validate_legacy_artifact(
    artifact: Mapping[str, Any],
    *,
    expected_head: str,
    expected_tree: str,
    expected_reviewed_scope: Sequence[str],
    expected_dispatch_transcript_sha256: str,
    expected_task_id: str,
    expected_parent_task_id: str,
    expected_sender: str,
) -> dict[str, Any]:
    if set(artifact) != _LEGACY_ARTIFACT_FIELDS:
        raise TraceError("strict legacy Independent artifact fields")
    if artifact["schema"] != "independent-review.v16" or artifact["verdict"] not in {"APPROVE", "REQUEST_CHANGES"}:
        raise TraceError("verified Independent artifact required")
    # This is compatibility only.  It intentionally admits no policy-selected
    # route and cannot be ingested into a new approval packet.
    if (
        artifact["reviewer_login"] != public_account("reviewer")
        or artifact["reviewer_model"] != "gpt-5.6-sol"
        or artifact["reasoning_effort"] != "xhigh"
        or artifact["fork_turns"] != "none"
        or artifact["context_mode"] != "zero-context"
        or artifact["report_only"] is not True
        or artifact["reviewer_is_writer"] is not False
    ):
        raise TraceError("legacy Independent reviewer lineage mismatch")
    if artifact["head_sha"] != expected_head or artifact["tree_sha"] != expected_tree:
        raise TraceError("legacy Independent identity mismatch")
    reviewed = _scope(artifact["reviewed_scope"], "$.reviewed_scope", nonempty=True)
    unreviewed = _scope(artifact["unreviewed_scope"], "$.unreviewed_scope", nonempty=False)
    if reviewed != list(expected_reviewed_scope) or artifact["coverage_status"] != "COMPLETE" or unreviewed:
        raise TraceError("legacy Independent scope mismatch")
    lineage = _dispatch_lineage(
        {
            "dispatch_transcript_sha256": artifact["dispatch_transcript_sha256"],
            "task_id": artifact["task_id"],
            "parent_task_id": artifact["parent_task_id"],
            "sender": artifact["sender"],
        },
        "$.dispatch_lineage",
        expected_dispatch_transcript_sha256=expected_dispatch_transcript_sha256,
        expected_task_id=expected_task_id,
        expected_parent_task_id=expected_parent_task_id,
        expected_sender=expected_sender,
    )
    del lineage
    digest = _sha256(artifact["artifact_sha256"], "$.artifact_sha256")
    unsigned = dict(artifact)
    unsigned["artifact_sha256"] = ""
    if canonical_sha256(unsigned) != digest:
        raise TraceError("legacy Independent artifact hash mismatch")
    return dict(artifact)


def _formal_artifact_shape(artifact: Mapping[str, Any]) -> dict[str, Any]:
    artifact_fields = set(artifact)
    legacy = artifact_fields == _LEGACY_FORMAL_ARTIFACT_FIELDS
    if not legacy and artifact_fields != _FORMAL_ARTIFACT_FIELDS:
        raise TraceError("strict formal Independent artifact fields")
    if artifact["schema"] != "independent-review.v16" or artifact["verdict"] not in {"APPROVE", "REQUEST_CHANGES"}:
        raise TraceError("verified Independent artifact required")
    if artifact["reviewer_login"] != public_account("reviewer") or artifact["report_only"] is not True or artifact["reviewer_is_writer"] is not False:
        raise TraceError("Independent reviewer separation mismatch")
    context_mode = artifact["context_mode"]
    if context_mode == "author_contextual" or context_mode not in _FORMAL_CONTEXT_MODES:
        raise TraceError("formal reviewer context mode required")
    _fork_turns(artifact["fork_turns"], "$.fork_turns", require_none=True)
    risk = _text(artifact["review_risk"], "$.review_risk")
    route = _text(artifact["reviewer_route"], "$.reviewer_route")
    model = _text(artifact["reviewer_model"], "$.reviewer_model")
    effort = _text(artifact["reasoning_effort"], "$.reasoning_effort")
    if risk not in _RISK_LEVELS or (route, model, effort) != _REVIEWER_DEFAULTS[risk]:
        raise TraceError("artifact risk/route/model/effort policy mismatch")
    coverage = _text(artifact["coverage_status"], "$.coverage_status")
    if coverage not in {"PARTIAL", "COMPLETE"}:
        raise TraceError("coverage status", "$.coverage_status")
    findings = _findings(artifact["findings"], "$.findings", full=True)
    closures = _closures(artifact["closures"], findings, "$.closures")
    if canonical_sha256(findings) != _sha256(artifact["findings_sha256"], "$.findings_sha256"):
        raise TraceError("reviewer findings hash mismatch")
    if canonical_sha256(closures) != _sha256(artifact["closures_sha256"], "$.closures_sha256"):
        raise TraceError("reviewer closures hash mismatch")
    unreviewed = _scope(artifact["unreviewed_scope"], "$.unreviewed_scope", nonempty=False)
    if (coverage == "COMPLETE") != (not unreviewed):
        raise TraceError("coverage status must match unreviewed scope", "$.coverage_status")
    if context_mode == "independent_clean_room" and any(
        finding["disposition"] == "FIXED" for finding in findings
    ):
        raise TraceError(
            "initial review cannot mark findings FIXED; use an evidence-bound continuation",
            "$.findings",
        )
    _review_decision(coverage, unreviewed, findings, artifact["verdict"], "$.verdict")
    known_limitations = artifact["known_limitations"]
    if not isinstance(known_limitations, list) or any(not isinstance(item, str) or not item for item in known_limitations):
        raise TraceError("known limitations array", "$.known_limitations")
    if len(set(known_limitations)) != len(known_limitations):
        raise TraceError("duplicate known limitations", "$.known_limitations")
    trigger = artifact["escalation_trigger"]
    if trigger is not None and trigger not in _ESCALATION_TRIGGERS:
        raise TraceError("invalid escalation trigger", "$.escalation_trigger")
    escalation_evidence_ref = artifact["escalation_evidence_ref"]
    if not isinstance(escalation_evidence_ref, str) or any(ord(char) < 0x20 for char in escalation_evidence_ref):
        raise TraceError("escalation evidence reference", "$.escalation_evidence_ref")
    if trigger in {"REVIEW_HOOK_ROUTING_GOVERNANCE_CHANGE", "REVIEWER_PARTICIPATED", "LINEAGE_LOSS"}:
        _sha256(escalation_evidence_ref, "$.escalation_evidence_ref")
    for field in ("prior_review_artifact_sha256", "prior_head_sha", "delta_sha256"):
        if artifact[field] is not None and not isinstance(artifact[field], str):
            raise TraceError("nullable lineage field", f"$.{field}")
    if artifact["prior_review_artifact_sha256"] is not None:
        _sha256(artifact["prior_review_artifact_sha256"], "$.prior_review_artifact_sha256")
    if artifact["prior_head_sha"] is not None:
        _sha(artifact["prior_head_sha"], "$.prior_head_sha")
    if artifact["delta_sha256"] is not None:
        _sha256(artifact["delta_sha256"], "$.delta_sha256")
    identity = _identity_fields(artifact, "$.identity", required=False)
    result = {
        "schema": artifact["schema"],
        "reviewer_login": artifact["reviewer_login"],
        "reviewer_model": model,
        "reasoning_effort": effort,
        "reviewer_route": route,
        "review_risk": risk,
        "fork_turns": artifact["fork_turns"],
        "context_mode": context_mode,
        "report_only": True,
        "reviewer_is_writer": False,
        "base_sha": _sha(artifact["base_sha"], "$.base_sha"),
        "head_sha": _sha(artifact["head_sha"], "$.head_sha"),
        "tree_sha": _sha(artifact["tree_sha"], "$.tree_sha"),
        "diff_sha256": _sha256(artifact["diff_sha256"], "$.diff_sha256"),
        "coverage_status": coverage,
        "reviewed_scope": _scope(artifact["reviewed_scope"], "$.reviewed_scope", nonempty=True),
        "unreviewed_scope": unreviewed,
        "review_packet_sha256": _sha256(artifact["review_packet_sha256"], "$.review_packet_sha256"),
        "acceptance_envelope_sha256": _sha256(artifact["acceptance_envelope_sha256"], "$.acceptance_envelope_sha256"),
        "reviewed_dependency_scope_sha256": _sha256(artifact["reviewed_dependency_scope_sha256"], "$.reviewed_dependency_scope_sha256"),
        "evidence_bundle_sha256": _sha256(artifact["evidence_bundle_sha256"], "$.evidence_bundle_sha256"),
        "evidence_denominator": _int(artifact["evidence_denominator"], "$.evidence_denominator", minimum=1),
        "prior_review_artifact_sha256": artifact["prior_review_artifact_sha256"],
        "prior_head_sha": artifact["prior_head_sha"],
        "delta_sha256": artifact["delta_sha256"],
        "reviewer_continuity_id": _text(artifact["reviewer_continuity_id"], "$.reviewer_continuity_id"),
        "run_id": _text(artifact["run_id"], "$.run_id"),
        "escalation_trigger": trigger,
        "escalation_evidence_ref": escalation_evidence_ref,
        "findings": findings,
        "findings_sha256": artifact["findings_sha256"],
        "closures": closures,
        "closures_sha256": artifact["closures_sha256"],
        "known_limitations": list(known_limitations),
        "dispatch_lineage": _dispatch_lineage(artifact["dispatch_lineage"], "$.dispatch_lineage"),
        "verdict": artifact["verdict"],
        "artifact_sha256": _sha256(artifact["artifact_sha256"], "$.artifact_sha256"),
        "identity_mode": identity["identity_mode"],
        "snapshot_sha256": identity["snapshot_sha256"],
        "prior_snapshot_sha256": identity["prior_snapshot_sha256"],
        "identity_kind": identity.get("identity_kind", "git-exact-object"),
        "identity_strict": identity["identity_strict"],
    }
    has_policy_fields = _DECISION_BASIS_POLICY_FIELDS.issubset(artifact_fields)
    has_closure_fields = {"closure_matrix", "closure_matrix_sha256"}.issubset(artifact_fields)
    if has_policy_fields and has_closure_fields:
        policy_fields = _policy_fields(artifact, "$.policy", required=True)
        closure_matrix = _closure_matrix(artifact["closure_matrix"], "$.closure_matrix")
        if canonical_sha256(closure_matrix) != _sha256(artifact["closure_matrix_sha256"], "$.closure_matrix_sha256"):
            raise TraceError("closure matrix hash mismatch")
        result.update(policy_fields)
        result["closure_matrix"] = closure_matrix
        result["closure_matrix_sha256"] = artifact["closure_matrix_sha256"]
    elif has_policy_fields or has_closure_fields:
        raise TraceError("formal policy/closure identity must be complete")
    if result["run_id"] == result["reviewer_continuity_id"]:
        raise TraceError("run receipt must be distinct from reviewer continuity ID")
    unsigned = dict(artifact)
    unsigned["artifact_sha256"] = ""
    if canonical_sha256(unsigned) != result["artifact_sha256"]:
        raise TraceError("Independent artifact hash mismatch")
    return result


def _continuation_basis(prior: Mapping[str, Any], current: Mapping[str, Any], *, escalated: bool) -> None:
    if current["base_sha"] != prior["base_sha"]:
        raise TraceError("continuation changed frozen review basis", "$.base_sha")
    if not escalated and current["acceptance_envelope_sha256"] != prior["acceptance_envelope_sha256"]:
        raise TraceError("continuation changed frozen review basis", "$.acceptance_envelope_sha256")
    if current["reviewed_dependency_scope_sha256"] != prior["reviewed_dependency_scope_sha256"] and not escalated:
        raise TraceError("continuation changed frozen review basis", "$.reviewed_dependency_scope_sha256")
    prior_scope = set(prior["reviewed_scope"]) | set(prior["unreviewed_scope"])
    current_scope = set(current["reviewed_scope"]) | set(current["unreviewed_scope"])
    if current_scope != prior_scope and not escalated:
        raise TraceError("continuation changed frozen scope")
    if not escalated and not set(prior["reviewed_scope"]).issubset(current["reviewed_scope"]):
        raise TraceError("continuation reopened or shrank reviewed scope")
    if not escalated and not set(current["unreviewed_scope"]).issubset(prior["unreviewed_scope"]):
        raise TraceError("continuation reopened unreviewed scope")
    if escalated:
        if (current["review_risk"], current["reviewer_route"], current["reviewer_model"], current["reasoning_effort"]) != ("high", "high_risk", "gpt-5.6-sol", "xhigh"):
            raise TraceError("escalated review requires high-risk route")
    elif (
        current["review_risk"],
        current["reviewer_route"],
        current["reviewer_model"],
        current["reasoning_effort"],
    ) != (
        prior["review_risk"],
        prior["reviewer_route"],
        prior["reviewer_model"],
        prior["reasoning_effort"],
    ):
        raise TraceError("continuation changed review route")


def _validate_identity_transition(
    prior: Mapping[str, Any],
    current: Mapping[str, Any],
    *,
    expected_identity_mode: str | None,
    expected_snapshot_sha256: str | None,
    expected_prior_snapshot_sha256: str | None,
    expected_delta_sha256: str | None,
) -> None:
    """Validate the strict Git/non-Git continuation identity union."""
    strict = bool(prior.get("identity_strict") or current.get("identity_strict"))
    if not strict:
        # Existing Git fixtures predate the identity union.  Keep their old
        # head transition rule, but never infer a Non-Git continuation from an
        # absent field.
        if current["head_sha"] == prior["head_sha"]:
            raise TraceError("continuation/escalation requires a new head")
        return
    if _identity_kind(current["identity_mode"]) != _identity_kind(prior["identity_mode"]):
        raise TraceError("continuation identity mode mismatch")
    if expected_identity_mode is not None:
        _identity_mode(expected_identity_mode, "$.expected_identity_mode")
        if _identity_kind(expected_identity_mode) != _identity_kind(current["identity_mode"]):
            raise TraceError("caller identity mode mismatch")
    if expected_snapshot_sha256 is not None and current["snapshot_sha256"] != expected_snapshot_sha256:
        raise TraceError("caller current snapshot mismatch")
    if expected_prior_snapshot_sha256 is not None and prior["snapshot_sha256"] != expected_prior_snapshot_sha256:
        raise TraceError("caller prior snapshot mismatch")
    mode = _identity_kind(current["identity_mode"])
    if mode == "git-exact-object":
        if current["head_sha"] == prior["head_sha"]:
            raise TraceError("continuation/escalation requires a new head")
        expected_delta = identity_delta_sha256(
            identity_mode="git-exact-object",
            base_sha=current["base_sha"],
            prior_head_sha=prior["head_sha"],
            head_sha=current["head_sha"],
            prior_snapshot_sha256=None,
            snapshot_sha256="",
        )
    else:
        if prior["snapshot_sha256"] in (None, "") or current["snapshot_sha256"] in (None, ""):
            raise TraceError("non-Git continuation requires prior/current snapshots")
        if prior["snapshot_sha256"] == current["snapshot_sha256"]:
            raise TraceError("non-Git continuation requires a changed snapshot")
        if current.get("prior_snapshot_sha256") != prior["snapshot_sha256"]:
            raise TraceError("non-Git continuation prior snapshot linkage mismatch")
        expected_delta = identity_delta_sha256(
            identity_mode="non-git-snapshot",
            base_sha=current["base_sha"],
            prior_head_sha=prior["head_sha"],
            head_sha=current["head_sha"],
            prior_snapshot_sha256=prior["snapshot_sha256"],
            snapshot_sha256=current["snapshot_sha256"],
        )
    if current.get("delta_sha256") != expected_delta:
        raise TraceError("identity delta does not match prior/current identity")
    if expected_delta_sha256 is None or expected_delta_sha256 != expected_delta:
        raise TraceError("caller identity delta mismatch")


def _validate_prior_closure(
    prior: Mapping[str, Any],
    current: Mapping[str, Any],
    *,
    evidence_bundle: Mapping[str, Any] | Sequence[Mapping[str, Any]] | None = None,
    closure_binding_receipt: Mapping[str, Any] | None = None,
    closure_authority: Mapping[str, Any] | None = None,
) -> None:
    """Require a monotonic, evidence-bound closure matrix for continuations."""
    prior_findings = {finding["id"]: finding for finding in prior["findings"]}
    current_findings = {finding["id"]: finding for finding in current["findings"]}
    matrix = current.get("closure_matrix", {})
    if current.get("closure_matrix_sha256") is None:
        # Legacy receipts cannot be used as a new continuation approval.
        if prior_findings:
            raise TraceError("continuation requires prior closure matrix")
        return
    if set(matrix) != set(prior_findings):
        raise TraceError("prior finding closure matrix is incomplete")
    requires_fixed_evidence = any(
        prior_finding["disposition"] != "FIXED"
        and current_findings.get(finding_id, {}).get("disposition") == "FIXED"
        for finding_id, prior_finding in prior_findings.items()
    )
    if requires_fixed_evidence and (
        closure_binding_receipt is None or closure_authority is None
    ):
        raise TraceError(
            "FIXED closure requires decision-basis pre-execution closure authority"
        )
    if (closure_binding_receipt is None) != (closure_authority is None):
        raise TraceError(
            "closure receipt and decision-basis authority must be supplied together"
        )
    closure_bindings: dict[str, Mapping[str, Any]] = {}
    if closure_binding_receipt is not None:
        try:
            checked_authority = validate_pre_execution_closure_authority(
                closure_authority,
                closure_binding_receipt=closure_binding_receipt,
            )
            checked_receipt = validate_closure_binding_receipt(
                closure_binding_receipt,
                expected_receipt_sha256=checked_authority[
                    "closure_binding_receipt_sha256"
                ],
                expected_compiled_plan_sha256=checked_authority[
                    "compiled_plan_sha256"
                ],
                expected_closure_plan_sha256=checked_authority[
                    "closure_plan_sha256"
                ],
                expected_closure_plan_file_sha256=checked_authority[
                    "closure_plan_file_sha256"
                ],
                expected_dispatch_transcript_file_sha256=checked_authority[
                    "dispatch_transcript_file_sha256"
                ],
            )
        except ContractError as exc:
            raise TraceError(
                "closure receipt contradicts decision-basis authority"
            ) from exc
        closure_bindings = {
            binding["finding_id"]: binding
            for binding in checked_receipt["bindings"]
        }
    evidence_rows: dict[str, Mapping[str, Any]] | None = None
    if evidence_bundle is not None:
        evidence_rows = _evidence_rows(
            evidence_bundle,
            expected_bundle_sha256=current["evidence_bundle_sha256"],
            expected_head_sha=current["head_sha"],
            expected_tree_sha=current["tree_sha"],
            expected_identity_mode=current["identity_mode"],
            expected_snapshot_sha256=current["snapshot_sha256"],
            closure_binding_receipt=closure_binding_receipt,
            closure_authority=closure_authority,
        )
    for finding_id, prior_finding in prior_findings.items():
        entry = matrix[finding_id]
        if entry["prior_disposition"] != prior_finding["disposition"]:
            raise TraceError("prior finding disposition mismatch", f"$.closure_matrix.{finding_id}")
        if prior_finding["disposition"] == "FIXED" and entry["disposition"] != "FIXED":
            raise TraceError("closed finding cannot be reopened in closure matrix", f"$.closure_matrix.{finding_id}")
        current_finding = current_findings.get(finding_id)
        if prior_finding["disposition"] != "FIXED":
            if current_finding is None:
                raise TraceError("prior active finding omitted", f"$.findings.{finding_id}")
            if current_finding["counterexample"] != prior_finding["counterexample"]:
                raise TraceError("original counterexample recheck mismatch", f"$.findings.{finding_id}")
            if entry["disposition"] != current_finding["disposition"]:
                raise TraceError("closure matrix/current disposition mismatch", f"$.closure_matrix.{finding_id}")
            if current_finding["disposition"] == "FIXED" and (not entry["evidence_ref"] or not entry["counterexample_recheck"]):
                raise TraceError("closed finding requires evidence and counterexample recheck", f"$.closure_matrix.{finding_id}")
            if current_finding["disposition"] == "FIXED":
                if evidence_rows is None:
                    raise TraceError("FIXED closure requires caller-bound evidence bundle", f"$.closure_matrix.{finding_id}")
                _validate_fixed_closure(
                    finding=prior_finding,
                    entry=entry,
                    evidence_rows=evidence_rows,
                    closure_bindings=closure_bindings,
                    path=f"$.closure_matrix.{finding_id}",
                )
        elif current_finding is not None:
            if current_finding["disposition"] != "FIXED":
                raise TraceError("closed finding cannot be reopened", f"$.findings.{finding_id}")
            if current_finding["counterexample"] != prior_finding["counterexample"]:
                raise TraceError("original counterexample recheck mismatch", f"$.findings.{finding_id}")
            if entry["disposition"] != current_finding["disposition"]:
                raise TraceError("closure matrix/current disposition mismatch", f"$.closure_matrix.{finding_id}")


def _escalation_drift(prior: Mapping[str, Any], current: Mapping[str, Any], trigger: str | None) -> None:
    """Match the requested trigger to a caller-bound old/new drift.

    A single transition can have multiple correlated drift identities; the
    requested trigger must match at least one of them.  Non-derivable events
    are admitted only when their strict evidence reference is validated below.
    """
    prior_scope = set(prior["reviewed_scope"]) | set(prior["unreviewed_scope"])
    current_scope = set(current["reviewed_scope"]) | set(current["unreviewed_scope"])
    drifts: set[str] = set()
    if current["acceptance_envelope_sha256"] != prior["acceptance_envelope_sha256"]:
        drifts.add("ACCEPTANCE_ENVELOPE_DRIFT")
    if current_scope != prior_scope:
        drifts.add("PATH_SET_SCOPE_DRIFT")
    if current.get("review_policy_sha256") != prior.get("review_policy_sha256") or current.get("review_risk") != prior.get("review_risk"):
        drifts.add("REVIEW_POLICY_DRIFT")
        if current.get("review_risk") != prior.get("review_risk"):
            drifts.add("RISK_ESCALATION")
    for field, token in (
        ("reference_identity_sha256", "REFERENCE_DOMAIN_THRESHOLD_DRIFT"),
        ("operating_domain_sha256", "REFERENCE_DOMAIN_THRESHOLD_DRIFT"),
        ("acceptance_thresholds_sha256", "REFERENCE_DOMAIN_THRESHOLD_DRIFT"),
        ("invariants_sha256", "ACCEPTANCE_ENVELOPE_DRIFT"),
        ("non_goals_sha256", "ACCEPTANCE_ENVELOPE_DRIFT"),
    ):
        if field in prior and field in current and current[field] != prior[field]:
            drifts.add(token)
    if prior["coverage_status"] == "PARTIAL" and current["coverage_status"] == "COMPLETE":
        drifts.add("PRIOR_COVERAGE_INCOMPLETE")
    prior_active_p1 = {f["id"] for f in prior["findings"] if f["severity"] == "P1" and f["disposition"] != "FIXED"}
    current_active_p1 = {f["id"] for f in current["findings"] if f["severity"] == "P1" and f["disposition"] != "FIXED"}
    if current_active_p1 - prior_active_p1:
        drifts.update({"ORIGINAL_SCOPE_MISSED_P1", "NEW_FALSIFIABLE_P1_EVIDENCE"})
    if current.get("known_limitations") != prior.get("known_limitations"):
        drifts.add("POST_REVIEW_INCIDENT")
    prior_active = {f["id"] for f in prior["findings"] if f["disposition"] != "FIXED"}
    current_active = {f["id"] for f in current["findings"] if f["disposition"] != "FIXED"}
    if prior_active & current_active:
        drifts.add("TWO_ROUND_NON_CONVERGENCE")
    if current["evidence_bundle_sha256"] != prior["evidence_bundle_sha256"]:
        drifts.add("PACKET_EVIDENCE_IDENTITY_INVALIDATED")
    if current.get("escalation_evidence_ref"):
        if current["escalation_evidence_ref"] == current["diff_sha256"] and current.get("review_policy_sha256") != prior.get("review_policy_sha256") and current["diff_sha256"] != prior["diff_sha256"]:
            drifts.add("REVIEW_HOOK_ROUTING_GOVERNANCE_CHANGE")
        if current["escalation_evidence_ref"] != prior.get("escalation_evidence_ref"):
            drifts.update({"REVIEWER_PARTICIPATED", "LINEAGE_LOSS"})
    if current["acceptance_envelope_sha256"] != prior["acceptance_envelope_sha256"] or current_scope != prior_scope:
        drifts.update({"MATERIAL_REWRITE", "CONTRACT_DISPUTE"})
    if trigger is None or trigger not in drifts:
        raise TraceError("escalation trigger has no matching old/new drift")


def _new_active_p1_admissions(prior: Mapping[str, Any], current: Mapping[str, Any]) -> None:
    """Require evidence only for new active P1s after a completed review."""
    if prior["coverage_status"] != "COMPLETE":
        return
    prior_active_p1_ids = {
        finding["id"]
        for finding in prior["findings"]
        if finding["severity"] == "P1" and finding["disposition"] != "FIXED"
    }
    for finding in current["findings"]:
        if finding["severity"] != "P1" or finding["disposition"] == "FIXED" or finding["id"] in prior_active_p1_ids:
            continue
        if finding["blocker_admission"] not in _BLOCKER_ADMISSIONS or not finding["admission_evidence_ref"]:
            raise TraceError("new active P1 requires blocker admission and evidence")


def validate_independent_artifact(
    artifact: Mapping[str, Any],
    *,
    expected_head: str,
    expected_tree: str,
    expected_reviewed_scope: Sequence[str],
    expected_dispatch_transcript_sha256: str,
    expected_task_id: str,
    expected_parent_task_id: str,
    expected_sender: str,
    expected_coverage_status: str | None = None,
    expected_unreviewed_scope: Sequence[str] | None = None,
    expected_base_sha: str | None = None,
    expected_review_packet_sha256: str | None = None,
    expected_acceptance_envelope_sha256: str | None = None,
    expected_diff_sha256: str | None = None,
    expected_reviewed_dependency_scope_sha256: str | None = None,
    expected_evidence_bundle_sha256: str | None = None,
    expected_evidence_denominator: int | None = None,
    expected_review_risk: str | None = None,
    expected_reviewer_route: str | None = None,
    expected_reviewer_model: str | None = None,
    expected_reasoning_effort: str | None = None,
    expected_required_stages: Sequence[str] | None = None,
    expected_classifier_identity: str | None = None,
    expected_high_risk_triggers: Sequence[str] | None = None,
    expected_review_policy_sha256: str | None = None,
    expected_reference_identity_sha256: str | None = None,
    expected_operating_domain_sha256: str | None = None,
    expected_acceptance_thresholds_sha256: str | None = None,
    expected_invariants_sha256: str | None = None,
    expected_non_goals_sha256: str | None = None,
    expected_identity_mode: str | None = None,
    expected_snapshot_sha256: str | None = None,
    expected_prior_snapshot_sha256: str | None = None,
    expected_delta_sha256: str | None = None,
    decision_basis: Mapping[str, Any] | None = None,
    prior_artifact: Mapping[str, Any] | None = None,
    evidence_bundle: Mapping[str, Any] | Sequence[Mapping[str, Any]] | None = None,
    closure_binding_receipt: Mapping[str, Any] | None = None,
    expected_closure_binding_receipt_sha256: str | None = None,
    expected_closure_plan_sha256: str | None = None,
    expected_escalation_trigger: str | None = None,
    expected_distinct_reviewer_task_id: str | None = None,
    expected_prior_reviewer_task_id: str | None = None,
    expected_escalation_evidence_ref: str | None = None,
) -> dict[str, Any]:
    """Validate a reviewer receipt against an exact packet and caller policy.

    Legacy Sol/zero-context receipts remain validator-compatible for existing
    v15 fixtures only.  They have no full decision basis and are rejected by
    :func:`ingest_independent_artifact`, so they cannot create a v16 approval.
    """
    if not isinstance(artifact, Mapping):
        raise TraceError("Independent artifact object required")
    if "review_packet_sha256" not in artifact:
        return _validate_legacy_artifact(
            artifact,
            expected_head=expected_head,
            expected_tree=expected_tree,
            expected_reviewed_scope=expected_reviewed_scope,
            expected_dispatch_transcript_sha256=expected_dispatch_transcript_sha256,
            expected_task_id=expected_task_id,
            expected_parent_task_id=expected_parent_task_id,
            expected_sender=expected_sender,
        )

    required_expected = {
        "expected_base_sha": expected_base_sha,
        "expected_review_packet_sha256": expected_review_packet_sha256,
        "expected_acceptance_envelope_sha256": expected_acceptance_envelope_sha256,
        "expected_diff_sha256": expected_diff_sha256,
        "expected_reviewed_dependency_scope_sha256": expected_reviewed_dependency_scope_sha256,
        "expected_evidence_bundle_sha256": expected_evidence_bundle_sha256,
        "expected_evidence_denominator": expected_evidence_denominator,
        "expected_coverage_status": expected_coverage_status,
        "expected_unreviewed_scope": expected_unreviewed_scope,
        "expected_review_risk": expected_review_risk,
        "expected_reviewer_route": expected_reviewer_route,
        "expected_reviewer_model": expected_reviewer_model,
        "expected_reasoning_effort": expected_reasoning_effort,
    }
    if any(value is None for value in required_expected.values()):
        raise TraceError("formal artifact requires complete caller-supplied decision policy")
    checked = _formal_artifact_shape(artifact)
    checked_decision_basis = (
        None
        if decision_basis is None
        else _decision_basis(decision_basis, "$.candidate_decision_basis")
    )
    closure_authority = (
        None
        if checked_decision_basis is None
        else checked_decision_basis.get("closure_authority")
    )
    for expected, field in (
        (
            expected_closure_binding_receipt_sha256,
            "closure_binding_receipt_sha256",
        ),
        (expected_closure_plan_sha256, "closure_plan_sha256"),
    ):
        if expected is not None and (
            closure_authority is None or expected != closure_authority[field]
        ):
            raise TraceError(
                "compatibility closure identity contradicts decision basis",
                f"$.{field}",
            )
    expected_policy = {
        "required_stages": expected_required_stages,
        "classifier_identity": expected_classifier_identity,
        "high_risk_triggers": expected_high_risk_triggers,
        "review_policy_sha256": expected_review_policy_sha256,
        "reference_identity_sha256": expected_reference_identity_sha256,
        "operating_domain_sha256": expected_operating_domain_sha256,
        "acceptance_thresholds_sha256": expected_acceptance_thresholds_sha256,
        "invariants_sha256": expected_invariants_sha256,
        "non_goals_sha256": expected_non_goals_sha256,
    }
    if any(item is not None for item in expected_policy.values()):
        if not checked.get("review_policy_sha256") or any(item is None for item in expected_policy.values()):
            raise TraceError("formal artifact policy identity required")
        for field, expected in expected_policy.items():
            if field in {"required_stages", "high_risk_triggers"}:
                if checked.get(field) != list(expected):
                    raise TraceError("formal artifact policy identity mismatch", f"$.{field}")
            elif checked.get(field) != expected:
                raise TraceError("formal artifact policy identity mismatch", f"$.{field}")
    if (
        checked["base_sha"] != expected_base_sha
        or checked["head_sha"] != expected_head
        or checked["tree_sha"] != expected_tree
        or checked["reviewed_scope"] != list(expected_reviewed_scope)
        or checked["coverage_status"] != expected_coverage_status
        or checked["unreviewed_scope"] != list(expected_unreviewed_scope)
        or checked["review_packet_sha256"] != expected_review_packet_sha256
        or checked["acceptance_envelope_sha256"] != expected_acceptance_envelope_sha256
        or checked["diff_sha256"] != expected_diff_sha256
        or checked["reviewed_dependency_scope_sha256"] != expected_reviewed_dependency_scope_sha256
        or checked["evidence_bundle_sha256"] != expected_evidence_bundle_sha256
        or checked["evidence_denominator"] != expected_evidence_denominator
        or checked["review_risk"] != expected_review_risk
        or checked["reviewer_route"] != expected_reviewer_route
        or checked["reviewer_model"] != expected_reviewer_model
        or checked["reasoning_effort"] != expected_reasoning_effort
    ):
        raise TraceError("formal artifact decision basis or policy mismatch")
    if expected_identity_mode is not None:
        _identity_mode(expected_identity_mode, "$.expected_identity_mode")
        if _identity_kind(expected_identity_mode) != _identity_kind(checked["identity_mode"]):
            raise TraceError("formal artifact identity mode mismatch")
    if expected_snapshot_sha256 is not None and checked["snapshot_sha256"] != expected_snapshot_sha256:
        raise TraceError("formal artifact current snapshot mismatch")
    if expected_prior_snapshot_sha256 is not None and checked["prior_snapshot_sha256"] != expected_prior_snapshot_sha256:
        raise TraceError("formal artifact prior snapshot mismatch")
    _dispatch_lineage(
        checked["dispatch_lineage"],
        "$.dispatch_lineage",
        expected_dispatch_transcript_sha256=expected_dispatch_transcript_sha256,
        expected_task_id=expected_task_id,
        expected_parent_task_id=expected_parent_task_id,
        expected_sender=expected_sender,
    )

    mode = checked["context_mode"]
    if mode == "independent_clean_room":
        if prior_artifact is not None or any(checked[field] is not None for field in ("prior_review_artifact_sha256", "prior_head_sha", "delta_sha256", "prior_snapshot_sha256")) or checked["escalation_trigger"] is not None:
            raise TraceError("initial review cannot carry continuation lineage")
    else:
        if prior_artifact is None:
            raise TraceError("continuation/escalation requires prior reviewer artifact")
        prior = _formal_artifact_shape(prior_artifact)
        if checked["prior_review_artifact_sha256"] != prior["artifact_sha256"] or checked["prior_head_sha"] != prior["head_sha"] or checked["delta_sha256"] is None:
            raise TraceError("prior review lineage mismatch")
        if expected_delta_sha256 is None or checked["delta_sha256"] != expected_delta_sha256:
            raise TraceError("continuation/escalation delta SHA mismatch")
        _validate_identity_transition(
            prior,
            checked,
            expected_identity_mode=expected_identity_mode,
            expected_snapshot_sha256=expected_snapshot_sha256,
            expected_prior_snapshot_sha256=expected_prior_snapshot_sha256,
            expected_delta_sha256=expected_delta_sha256,
        )
        _continuation_basis(prior, checked, escalated=mode == "escalated_fresh")
        _new_active_p1_admissions(prior, checked)
        if mode == "delta_continuation":
            if checked["reviewer_continuity_id"] != prior["reviewer_continuity_id"]:
                raise TraceError("continuation reviewer continuity mismatch")
            if checked["dispatch_lineage"]["task_id"] != prior["dispatch_lineage"]["task_id"]:
                raise TraceError("continuation reviewer task mismatch")
            if checked["run_id"] == prior["run_id"]:
                raise TraceError("continuation requires distinct run receipt")
            if checked["escalation_trigger"] is not None:
                raise TraceError("continuation cannot carry escalation trigger")
            _validate_prior_closure(
                prior,
                checked,
                evidence_bundle=evidence_bundle,
                closure_binding_receipt=closure_binding_receipt,
                closure_authority=closure_authority,
            )
        else:
            if checked["reviewer_continuity_id"] == prior["reviewer_continuity_id"]:
                raise TraceError("escalated fresh review requires a new reviewer continuity ID")
            if checked["escalation_trigger"] not in _ESCALATION_TRIGGERS or checked["escalation_trigger"] != expected_escalation_trigger:
                raise TraceError("escalated fresh review requires caller-approved trigger")
            if checked["escalation_trigger"] in {"REVIEW_HOOK_ROUTING_GOVERNANCE_CHANGE", "REVIEWER_PARTICIPATED", "LINEAGE_LOSS"}:
                if expected_escalation_evidence_ref is None or checked["escalation_evidence_ref"] != expected_escalation_evidence_ref:
                    raise TraceError("escalated fresh review requires caller-bound escalation evidence")
            if expected_distinct_reviewer_task_id is None or expected_prior_reviewer_task_id is None:
                raise TraceError("escalated fresh review requires caller proof of distinct reviewer task")
            task_id = checked["dispatch_lineage"]["task_id"]
            if task_id != expected_distinct_reviewer_task_id or task_id == expected_prior_reviewer_task_id or prior["dispatch_lineage"]["task_id"] != expected_prior_reviewer_task_id:
                raise TraceError("escalated reviewer task distinctness mismatch")
            _validate_prior_closure(
                prior,
                checked,
                evidence_bundle=evidence_bundle,
                closure_binding_receipt=closure_binding_receipt,
                closure_authority=closure_authority,
            )
            _escalation_drift(prior, checked, checked["escalation_trigger"])
    return checked


def _bool(value: Any, path: str) -> bool:
    if type(value) is not bool:
        raise TraceError("boolean required", path)
    return value


def _check(value: Any, path: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise TraceError("check object required", path)
    fields = {"id", "status", "reused", "skipped", "cost", "denominator", "total", "ran", "passed", "failed", "unknown", "xfail"}
    if set(value) != fields:
        raise TraceError("missing/additional check fields", path)
    status = _str(value["status"], f"{path}.status", public=True)
    if status not in {"GREEN", "RED", "SKIPPED", "REUSED"}:
        raise TraceError("check status", f"{path}.status")
    denominator = _int(value["denominator"], f"{path}.denominator", minimum=1)
    total = _int(value["total"], f"{path}.total", minimum=1)
    ran = _int(value["ran"], f"{path}.ran", minimum=0)
    passed = _int(value["passed"], f"{path}.passed", minimum=0)
    failed = _int(value["failed"], f"{path}.failed", minimum=0)
    unknown = _int(value["unknown"], f"{path}.unknown", minimum=0)
    xfail = _int(value["xfail"], f"{path}.xfail", minimum=0)
    reused = _bool(value["reused"], f"{path}.reused")
    skipped = _bool(value["skipped"], f"{path}.skipped")
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
    return {
        "id": _id(value["id"], f"{path}.id"),
        "status": status,
        "reused": reused,
        "skipped": skipped,
        "cost": _str(value["cost"], f"{path}.cost", public=True),
        "denominator": denominator,
        "total": total,
        "ran": ran,
        "passed": passed,
        "failed": failed,
        "unknown": unknown,
        "xfail": xfail,
    }


def validate_review_packet(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise TraceError("review packet object required")
    fields = {
        "schema",
        "mission_id",
        "author_login",
        "reviewer_login",
        "base_sha",
        "head_sha",
        "tree_sha",
        "lineage_mode",
        "coverage_status",
        "reviewed_scope",
        "unreviewed_scope",
        "checks",
        "findings",
        "closures",
        "verdict",
        "round",
        "body_sha256",
    }
    optional = {
        "independent_artifact_sha256",
        "expected_scope",
        "incident",
        "decision_basis",
        "identity_mode",
        "snapshot_sha256",
        "prior_snapshot_sha256",
        "prior_head_sha",
        "delta_sha256",
    }
    if set(value) - fields - optional or fields - set(value):
        raise TraceError("missing/additional review packet fields")
    if value["schema"] != "review-packet.v16":
        raise TraceError("schema")
    if value["author_login"] != public_account("author") or value["reviewer_login"] != public_account("reviewer") or value["author_login"] == value["reviewer_login"]:
        raise TraceError("fixed author/reviewer identity required")
    mission_id = _id(value["mission_id"], "$.mission_id")
    base = _sha(value["base_sha"], "$.base_sha")
    head = _sha(value["head_sha"], "$.head_sha")
    tree = _sha(value["tree_sha"], "$.tree_sha")
    lineage = _text(value["lineage_mode"], "$.lineage_mode")
    if lineage not in {"FULL_CONTROL_PLANE", "DISPATCH_TRANSCRIPT"}:
        raise TraceError("lineage mode", "$.lineage_mode")
    coverage = _text(value["coverage_status"], "$.coverage_status")
    if coverage not in {"PARTIAL", "COMPLETE"}:
        raise TraceError("coverage status", "$.coverage_status")
    reviewed = _scope(value["reviewed_scope"], "$.reviewed_scope", nonempty=True)
    unreviewed = _scope(value["unreviewed_scope"], "$.unreviewed_scope", nonempty=False)
    if (coverage == "COMPLETE") != (not unreviewed):
        raise TraceError("coverage status must match unreviewed scope", "$.coverage_status")
    if "expected_scope" in value and _scope(value["expected_scope"], "$.expected_scope", nonempty=True) != sorted(reviewed + unreviewed):
        raise TraceError("reviewed scope must equal frozen scope")
    if "incident" in value:
        incident = value["incident"]
        if not isinstance(incident, Mapping) or set(incident) != {"commit_ids", "summary"} or not isinstance(incident["commit_ids"], list) or not incident["commit_ids"]:
            raise TraceError("strict incident record")
        if any(not isinstance(commit, str) or len(commit) != 40 or any(char not in "0123456789abcdef" for char in commit) for commit in incident["commit_ids"]):
            raise TraceError("strict incident commit IDs")
        _text(incident["summary"], "$.incident.summary")
    checks = [_check(item, f"$.checks[{index}]") for index, item in enumerate(value["checks"])] if isinstance(value["checks"], list) else (_ for _ in ()).throw(TraceError("checks array", "$.checks"))
    if not checks or len({check["id"] for check in checks}) != len(checks):
        raise TraceError("checks require known unique denominators", "$.checks")
    findings = _findings(value["findings"], "$.findings", full=False)
    closures = _closures(value["closures"], findings, "$.closures")
    _review_decision(coverage, unreviewed, findings, value["verdict"], "$.verdict")
    if value["verdict"] == "APPROVE" and "independent_artifact_sha256" not in value:
        raise TraceError("APPROVE requires independent artifact")
    independent_artifact_sha256 = None
    if "independent_artifact_sha256" in value:
        independent_artifact_sha256 = _sha256(value["independent_artifact_sha256"], "$.independent_artifact_sha256")
    raw_basis = value.get("decision_basis")
    basis_identity_strict = isinstance(raw_basis, Mapping) and bool(set(raw_basis) & _IDENTITY_FIELDS)
    decision_basis = _decision_basis(raw_basis, "$.decision_basis") if "decision_basis" in value else None
    identity: dict[str, Any] | None = None
    if any(field in value for field in _IDENTITY_FIELDS):
        identity = _identity_fields(value, "$.identity", required=True)
        if decision_basis is not None and basis_identity_strict and any(
            identity[field] != decision_basis[field] for field in _IDENTITY_FIELDS
        ):
            raise TraceError("packet identity does not match decision basis")
    elif decision_basis is not None and basis_identity_strict:
        # A strict basis cannot be hidden behind an old packet shape.
        raise TraceError("strict decision basis requires packet identity")
    for field in ("prior_head_sha", "delta_sha256"):
        if field in value and value[field] is not None:
            if field == "prior_head_sha":
                _sha(value[field], f"$.{field}")
            else:
                _sha256(value[field], f"$.{field}")
        if decision_basis is not None and field in decision_basis and value.get(field) != decision_basis[field]:
            raise TraceError("packet lineage does not match decision basis", f"$.{field}")
    round_no = _int(value["round"], "$.round", minimum=1)
    body_hash = _sha256(value["body_sha256"], "$.body_sha256")
    result = {
        "schema": "review-packet.v16",
        "mission_id": mission_id,
        "author_login": public_account("author"),
        "reviewer_login": public_account("reviewer"),
        "base_sha": base,
        "head_sha": head,
        "tree_sha": tree,
        "lineage_mode": lineage,
        "coverage_status": coverage,
        "reviewed_scope": reviewed,
        "unreviewed_scope": unreviewed,
        "checks": checks,
        "findings": findings,
        "closures": closures,
        "verdict": value["verdict"],
        "round": round_no,
        "body_sha256": body_hash,
    }
    if independent_artifact_sha256 is not None:
        result["independent_artifact_sha256"] = independent_artifact_sha256
    if "expected_scope" in value:
        result["expected_scope"] = list(value["expected_scope"])
    if "incident" in value:
        result["incident"] = {"commit_ids": list(value["incident"]["commit_ids"]), "summary": value["incident"]["summary"]}
    if decision_basis is not None:
        result["decision_basis"] = decision_basis
    if identity is not None:
        result.update({field: identity[field] for field in _IDENTITY_FIELDS})
    for field in ("prior_head_sha", "delta_sha256"):
        if field in value:
            result[field] = value[field]
    return result


def render_pr_trace(
    *,
    mission_id: str,
    base_sha: str,
    head_sha: str,
    tree_sha: str,
    checks: Sequence[Mapping[str, Any]],
    findings: Sequence[Mapping[str, Any]],
    closures: Mapping[str, str],
    reviewed_scope: Sequence[str],
    unreviewed_scope: Sequence[str],
    lineage_mode: str = "DISPATCH_TRANSCRIPT",
    round: int = 1,
    incident: Mapping[str, Any] | None = None,
    decision_basis: Mapping[str, Any] | None = None,
    identity_mode: str | None = None,
    snapshot_sha256: str | None = None,
    prior_snapshot_sha256: str | None = None,
    prior_head_sha: str | None = None,
    delta_sha256: str | None = None,
) -> dict[str, Any]:
    """Render an author-side packet.  It always leaves ``verdict`` null."""
    normalized_checks: list[dict[str, Any]] = []
    for check in checks:
        item = dict(check)
        item.setdefault("ran", item.get("passed", 0) + item.get("failed", 0))
        item.setdefault("unknown", 0)
        item.setdefault("xfail", 0)
        item.setdefault("skipped", False)
        item.setdefault("reused", False)
        normalized_checks.append(item)
    unsigned: dict[str, Any] = {
        "schema": "review-packet.v16",
        "mission_id": mission_id,
        "author_login": public_account("author"),
        "reviewer_login": public_account("reviewer"),
        "base_sha": base_sha,
        "head_sha": head_sha,
        "tree_sha": tree_sha,
        "lineage_mode": lineage_mode,
        "coverage_status": "COMPLETE" if not unreviewed_scope else "PARTIAL",
        "reviewed_scope": list(reviewed_scope),
        "unreviewed_scope": list(unreviewed_scope),
        "checks": normalized_checks,
        "findings": [dict(finding) for finding in findings],
        "closures": dict(closures),
        "verdict": None,
        "round": round,
        "body_sha256": "",
    }
    if incident is not None:
        unsigned["incident"] = {"commit_ids": list(incident["commit_ids"]), "summary": incident["summary"]}
    if decision_basis is not None:
        unsigned["decision_basis"] = dict(decision_basis)
        if identity_mode is None and set(decision_basis) & _IDENTITY_FIELDS:
            identity_mode = decision_basis.get("identity_mode")
            snapshot_sha256 = decision_basis.get("snapshot_sha256")
            prior_snapshot_sha256 = decision_basis.get("prior_snapshot_sha256")
        if delta_sha256 is None and "delta_sha256" in decision_basis:
            delta_sha256 = decision_basis.get("delta_sha256")
        if prior_head_sha is None and "prior_head_sha" in decision_basis:
            prior_head_sha = decision_basis.get("prior_head_sha")
    if identity_mode is not None or snapshot_sha256 is not None or prior_snapshot_sha256 is not None:
        if identity_mode is None or snapshot_sha256 is None:
            raise TraceError("complete packet identity required")
        identity = _identity_fields(
            {
                "identity_mode": identity_mode,
                "snapshot_sha256": snapshot_sha256,
                "prior_snapshot_sha256": prior_snapshot_sha256,
            },
            "$.identity",
            required=True,
        )
        unsigned.update({field: identity[field] for field in _IDENTITY_FIELDS})
    if prior_head_sha is not None:
        unsigned["prior_head_sha"] = _sha(prior_head_sha, "$.prior_head_sha")
    if delta_sha256 is not None:
        unsigned["delta_sha256"] = _sha256(delta_sha256, "$.delta_sha256")
    public_lineage = "DISPATCH" if lineage_mode == "DISPATCH_TRANSCRIPT" else lineage_mode
    body = "\n".join(
        [
            "### V16 productivity PR trace",
            f"- mission: `{mission_id}`",
            f"- author: `{public_account('author')}`; independent reviewer: `{public_account('reviewer')}`",
            f"- base: `{base_sha}`; head: `{head_sha}`; tree: `{tree_sha}`",
            f"- lineage: `{public_lineage}`; coverage: `{unsigned['coverage_status']}`; round: `{round}`",
            f"- checks: {len(normalized_checks)}; findings: {len(findings)}; verdict: `null` (author renderer)",
            "- denominators/costs and finding closures are machine-derived from the packet; this renderer makes no GitHub calls.",
        ]
    ) + "\n"
    if incident is not None:
        body += f"- remediation incident recorded: `{len(incident['commit_ids'])}` transient local commit(s); source identity remained guarded.\n"
    violations = privacy_scan(body)
    if violations:
        raise TraceError("privacy violation in public trace: " + ",".join(violations))
    unsigned["body_sha256"] = hashlib.sha256(body.encode("utf-8")).hexdigest()
    packet = validate_review_packet(unsigned)
    return {
        "packet": packet,
        "markdown": body,
        "packet_sha256": canonical_sha256(packet),
        "body_sha256": packet["body_sha256"],
    }


def ingest_independent_artifact(
    packet: Mapping[str, Any],
    artifact: Mapping[str, Any],
    *,
    expected_dispatch_transcript_sha256: str,
    expected_task_id: str,
    expected_parent_task_id: str,
    expected_sender: str,
    expected_reviewer_model: str | None = None,
    expected_reasoning_effort: str | None = None,
    expected_review_risk: str | None = None,
    expected_reviewer_route: str | None = None,
    expected_required_stages: Sequence[str] | None = None,
    expected_classifier_identity: str | None = None,
    expected_high_risk_triggers: Sequence[str] | None = None,
    expected_review_policy_sha256: str | None = None,
    expected_reference_identity_sha256: str | None = None,
    expected_operating_domain_sha256: str | None = None,
    expected_acceptance_thresholds_sha256: str | None = None,
    expected_invariants_sha256: str | None = None,
    expected_non_goals_sha256: str | None = None,
    expected_identity_mode: str | None = None,
    expected_snapshot_sha256: str | None = None,
    expected_prior_snapshot_sha256: str | None = None,
    expected_delta_sha256: str | None = None,
    prior_artifact: Mapping[str, Any] | None = None,
    evidence_bundle: Mapping[str, Any] | Sequence[Mapping[str, Any]] | None = None,
    closure_binding_receipt: Mapping[str, Any] | None = None,
    expected_closure_binding_receipt_sha256: str | None = None,
    expected_closure_plan_sha256: str | None = None,
    expected_escalation_trigger: str | None = None,
    expected_distinct_reviewer_task_id: str | None = None,
    expected_prior_reviewer_task_id: str | None = None,
    expected_escalation_evidence_ref: str | None = None,
) -> dict[str, Any]:
    """Ingest only a fully bound formal reviewer decision.

    The caller packet contributes the immutable review basis.  Findings,
    closures, and verdict are copied exclusively from the hashed reviewer
    artifact after all packet, evidence, dispatch, policy, and lineage checks.
    """
    candidate = validate_review_packet(packet)
    if candidate["verdict"] is not None or "independent_artifact_sha256" in candidate:
        raise TraceError("author packet must have null verdict and no reviewer artifact")
    if not isinstance(artifact, Mapping) or "review_packet_sha256" not in artifact:
        raise TraceError("formal bound reviewer artifact required for ingestion")
    basis = candidate.get("decision_basis")
    if basis is None:
        raise TraceError("formal reviewer artifact requires packet decision basis")
    if "review_policy_sha256" not in basis and "review_policy_sha256" in artifact:
        raise TraceError("packet policy identity required for formal artifact")
    closure_authority = basis.get("closure_authority")
    if closure_authority is None:
        if (
            closure_binding_receipt is not None
            or expected_closure_binding_receipt_sha256 is not None
            or expected_closure_plan_sha256 is not None
        ):
            raise TraceError(
                "closure inputs require packet decision-basis authority"
            )
        derived_closure_receipt_sha256 = None
        derived_closure_plan_sha256 = None
    else:
        for supplied, field in (
            (
                expected_closure_binding_receipt_sha256,
                "closure_binding_receipt_sha256",
            ),
            (expected_closure_plan_sha256, "closure_plan_sha256"),
        ):
            if supplied is not None and supplied != closure_authority[field]:
                raise TraceError(
                    "compatibility closure identity contradicts decision basis",
                    f"$.decision_basis.closure_authority.{field}",
                )
        derived_closure_receipt_sha256 = closure_authority[
            "closure_binding_receipt_sha256"
        ]
        derived_closure_plan_sha256 = closure_authority[
            "closure_plan_sha256"
        ]
    if (
        expected_review_risk,
        expected_reviewer_route,
        expected_reviewer_model,
        expected_reasoning_effort,
    ) != (
        basis["review_risk"],
        basis["reviewer_route"],
        basis["reviewer_model"],
        basis["reasoning_effort"],
    ):
        raise TraceError("caller policy must match the packet risk route/model/effort")
    if "review_policy_sha256" in basis:
        expected_required_stages = basis["required_stages"] if expected_required_stages is None else expected_required_stages
        expected_classifier_identity = basis["classifier_identity"] if expected_classifier_identity is None else expected_classifier_identity
        expected_high_risk_triggers = basis["high_risk_triggers"] if expected_high_risk_triggers is None else expected_high_risk_triggers
        expected_review_policy_sha256 = basis["review_policy_sha256"] if expected_review_policy_sha256 is None else expected_review_policy_sha256
        expected_reference_identity_sha256 = basis["reference_identity_sha256"] if expected_reference_identity_sha256 is None else expected_reference_identity_sha256
        expected_operating_domain_sha256 = basis["operating_domain_sha256"] if expected_operating_domain_sha256 is None else expected_operating_domain_sha256
        expected_acceptance_thresholds_sha256 = basis["acceptance_thresholds_sha256"] if expected_acceptance_thresholds_sha256 is None else expected_acceptance_thresholds_sha256
        expected_invariants_sha256 = basis["invariants_sha256"] if expected_invariants_sha256 is None else expected_invariants_sha256
        expected_non_goals_sha256 = basis["non_goals_sha256"] if expected_non_goals_sha256 is None else expected_non_goals_sha256
    expected_identity_mode = basis.get("identity_mode") if expected_identity_mode is None else expected_identity_mode
    expected_snapshot_sha256 = basis.get("snapshot_sha256") if expected_snapshot_sha256 is None else expected_snapshot_sha256
    expected_prior_snapshot_sha256 = basis.get("prior_snapshot_sha256") if expected_prior_snapshot_sha256 is None else expected_prior_snapshot_sha256
    checked = validate_independent_artifact(
        artifact,
        expected_head=candidate["head_sha"],
        expected_tree=candidate["tree_sha"],
        expected_reviewed_scope=candidate["reviewed_scope"],
        expected_coverage_status=candidate["coverage_status"],
        expected_unreviewed_scope=candidate["unreviewed_scope"],
        expected_dispatch_transcript_sha256=expected_dispatch_transcript_sha256,
        expected_task_id=expected_task_id,
        expected_parent_task_id=expected_parent_task_id,
        expected_sender=expected_sender,
        expected_base_sha=candidate["base_sha"],
        expected_review_packet_sha256=canonical_sha256(candidate),
        expected_acceptance_envelope_sha256=basis["acceptance_envelope_sha256"],
        expected_diff_sha256=basis["diff_sha256"],
        expected_reviewed_dependency_scope_sha256=basis["reviewed_dependency_scope_sha256"],
        expected_evidence_bundle_sha256=basis["evidence_bundle_sha256"],
        expected_evidence_denominator=basis["evidence_denominator"],
        expected_review_risk=expected_review_risk,
        expected_reviewer_route=expected_reviewer_route,
        expected_reviewer_model=expected_reviewer_model,
        expected_reasoning_effort=expected_reasoning_effort,
        expected_required_stages=expected_required_stages,
        expected_classifier_identity=expected_classifier_identity,
        expected_high_risk_triggers=expected_high_risk_triggers,
        expected_review_policy_sha256=expected_review_policy_sha256,
        expected_reference_identity_sha256=expected_reference_identity_sha256,
        expected_operating_domain_sha256=expected_operating_domain_sha256,
        expected_acceptance_thresholds_sha256=expected_acceptance_thresholds_sha256,
        expected_invariants_sha256=expected_invariants_sha256,
        expected_non_goals_sha256=expected_non_goals_sha256,
        expected_identity_mode=expected_identity_mode,
        expected_snapshot_sha256=expected_snapshot_sha256,
        expected_prior_snapshot_sha256=expected_prior_snapshot_sha256,
        expected_delta_sha256=expected_delta_sha256,
        decision_basis=basis,
        prior_artifact=prior_artifact,
        evidence_bundle=evidence_bundle,
        closure_binding_receipt=closure_binding_receipt,
        expected_closure_binding_receipt_sha256=(
            derived_closure_receipt_sha256
        ),
        expected_closure_plan_sha256=derived_closure_plan_sha256,
        expected_escalation_trigger=expected_escalation_trigger,
        expected_distinct_reviewer_task_id=expected_distinct_reviewer_task_id,
        expected_prior_reviewer_task_id=expected_prior_reviewer_task_id,
        expected_escalation_evidence_ref=expected_escalation_evidence_ref,
    )
    candidate["findings"] = checked["findings"]
    candidate["closures"] = checked["closures"]
    candidate["verdict"] = checked["verdict"]
    candidate["independent_artifact_sha256"] = checked["artifact_sha256"]
    return validate_review_packet(candidate)

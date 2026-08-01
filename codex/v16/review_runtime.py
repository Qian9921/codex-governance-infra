"""Bounded runtime contract for formal review dispatch and convergence.

This module does not launch or interrupt a model.  It gives the control plane a
strict, source-derived contract for choosing the reviewer effort, bounding
scope/tool use, and deciding when to request a report or interrupt and replan.
Correctness gates remain owned by the independent review artifact.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any

from .review_policy import CONTEXT_MODES, resolve_review_policy


RUNTIME_SCHEMA = "review-runtime.v16"
PROGRESS_SCHEMA = "review-runtime-progress.v16"
RUNTIME_FIELDS = frozenset(
    {
        "schema",
        "review_policy_sha256",
        "review_identity_sha256",
        "prior_review_artifact_sha256",
        "reviewer_continuity_id",
        "review_risk",
        "context_mode",
        "reviewer_model",
        "reasoning_effort",
        "fresh_reviewer",
        "reuse_prior_reviewer",
        "delta_only",
        "prior_complete_required",
        "changed_files",
        "changed_lines",
        "max_files",
        "max_changed_lines",
        "max_context_chars",
        "max_tool_calls",
        "max_review_calls",
        "soft_deadline_sec",
        "hard_deadline_sec",
        "duplicate_full_scope_reviews",
        "scope_expansion_policy",
        "timeout_action",
        "escalation_triggers",
        "contract_sha256",
    }
)
EXPECTATION_FIELDS = frozenset(
    {
        "context_mode",
        "changed_files",
        "changed_lines",
        "review_identity_sha256",
        "prior_review_artifact_sha256",
        "reviewer_continuity_id",
    }
)
PROGRESS_FIELDS = frozenset(
    {
        "schema",
        "contract_sha256",
        "action",
        "reason_code",
        "approval_eligible",
        "elapsed_sec",
        "tool_calls",
        "files_read",
        "context_chars",
        "review_calls",
        "duplicate_full_scope_reviews",
        "verdict_present",
        "coverage_complete",
        "unreviewed_count",
        "scope_expansion_requested",
        "new_falsifiable_evidence",
        "budget_exceeded",
    }
)

# These are routing/SLO thresholds, never acceptance thresholds.  Exceeding a
# bound selects a stronger route or a replan; it cannot waive or manufacture a
# verdict.
_INITIAL_PROFILES = {
    "low": {
        "soft_deadline_sec": 180,
        "hard_deadline_sec": 480,
        "max_files": 20,
        "max_changed_lines": 3000,
        "max_context_chars": 20000,
        "max_tool_calls": 12,
    },
    "medium": {
        "soft_deadline_sec": 180,
        "hard_deadline_sec": 480,
        "max_files": 20,
        "max_changed_lines": 3000,
        "max_context_chars": 20000,
        "max_tool_calls": 12,
    },
    "high": {
        "soft_deadline_sec": 300,
        "hard_deadline_sec": 900,
        "max_files": 24,
        "max_changed_lines": 5000,
        "max_context_chars": 24000,
        "max_tool_calls": 16,
    },
}
_DELTA_PROFILE = {
    "soft_deadline_sec": 90,
    "hard_deadline_sec": 240,
    "max_files": 12,
    "max_changed_lines": 800,
    "max_context_chars": 12000,
    "max_tool_calls": 8,
}


class ReviewRuntimeError(ValueError):
    """Raised when a review runtime contract is malformed or unsafe."""


def _strict_int(value: Any, path: str, *, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise ReviewRuntimeError(f"{path}: integer >= {minimum} required")
    return value


def _strict_bool(value: Any, path: str) -> bool:
    if type(value) is not bool:
        raise ReviewRuntimeError(f"{path}: boolean required")
    return value


def _text(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value or any(ord(char) < 0x20 for char in value):
        raise ReviewRuntimeError(f"{path}: non-empty printable string required")
    return value


def _texts(value: Any, path: str) -> list[str]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ReviewRuntimeError(f"{path}: string array required")
    result = [_text(item, f"{path}[{index}]") for index, item in enumerate(value)]
    if len(result) != len(set(result)):
        raise ReviewRuntimeError(f"{path}: duplicate values forbidden")
    return result


def _canonical_sha256(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _sha256(value: Any, path: str, *, allow_empty: bool = False) -> str:
    if allow_empty and value == "":
        return ""
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(char not in "0123456789abcdef" for char in value)
    ):
        raise ReviewRuntimeError(f"{path}: lowercase SHA-256 required")
    return value


def _runtime_expectations(
    *,
    context_mode: str,
    changed_files: int,
    changed_lines: int,
    review_identity_sha256: str,
    prior_review_artifact_sha256: str,
    reviewer_continuity_id: str,
) -> dict[str, Any]:
    return {
        "context_mode": context_mode,
        "changed_files": changed_files,
        "changed_lines": changed_lines,
        "review_identity_sha256": review_identity_sha256,
        "prior_review_artifact_sha256": prior_review_artifact_sha256,
        "reviewer_continuity_id": reviewer_continuity_id,
    }


def compile_review_runtime(
    policy_or_mission: Mapping[str, Any],
    *,
    context_mode: str,
    changed_files: int,
    changed_lines: int,
    review_identity_sha256: str,
    prior_review_artifact_sha256: str = "",
    reviewer_continuity_id: str = "",
    prior_coverage_status: str = "",
    prior_unreviewed_count: int = 0,
    contract_drift: bool = False,
    same_reviewer_available: bool = False,
    escalation_triggers: Sequence[str] = (),
) -> dict[str, Any]:
    """Compile the non-weakening runtime route for one formal review run."""
    policy = resolve_review_policy(policy_or_mission)
    mode = _text(context_mode, "$.context_mode")
    if mode not in CONTEXT_MODES or mode == "author_contextual":
        raise ReviewRuntimeError("$.context_mode: formal gating mode required")
    files = _strict_int(changed_files, "$.changed_files", minimum=1)
    lines = _strict_int(changed_lines, "$.changed_lines", minimum=1)
    review_identity = _sha256(
        review_identity_sha256, "$.review_identity_sha256"
    )
    prior_artifact = _sha256(
        prior_review_artifact_sha256,
        "$.prior_review_artifact_sha256",
        allow_empty=True,
    )
    continuity = _sha256(
        reviewer_continuity_id,
        "$.reviewer_continuity_id",
        allow_empty=True,
    )
    unreviewed = _strict_int(prior_unreviewed_count, "$.prior_unreviewed_count")
    drift = _strict_bool(contract_drift, "$.contract_drift")
    same_reviewer = _strict_bool(same_reviewer_available, "$.same_reviewer_available")
    triggers = _texts(escalation_triggers, "$.escalation_triggers")

    if mode == "delta_continuation":
        if prior_coverage_status != "COMPLETE" or unreviewed != 0:
            raise ReviewRuntimeError(
                "$.prior_coverage_status: delta continuation requires COMPLETE prior coverage and empty unreviewed scope"
            )
        if drift:
            raise ReviewRuntimeError(
                "$.contract_drift: contract drift requires escalated_fresh"
            )
        if not same_reviewer:
            raise ReviewRuntimeError(
                "$.same_reviewer_available: delta continuation requires reviewer continuity"
            )
        if triggers:
            raise ReviewRuntimeError(
                "$.escalation_triggers: delta continuation cannot carry escalation triggers"
            )
        if not prior_artifact or not continuity:
            raise ReviewRuntimeError(
                "$: delta continuation requires prior review artifact and reviewer continuity identities"
            )
        profile = dict(_DELTA_PROFILE)
        if files > profile["max_files"] or lines > profile["max_changed_lines"]:
            raise ReviewRuntimeError(
                "$.delta: bounded continuation exceeded; escalated_fresh required"
            )
        fresh = False
        reuse = True
        delta_only = True
        prior_required = True
        # Deterministic evidence and prior COMPLETE coverage permit less
        # expensive reasoning without changing the reviewer model or truth bar.
        effort = "high"
        scope_policy = "escalate-on-new-falsifiable-evidence"
    elif mode == "escalated_fresh":
        if not triggers:
            raise ReviewRuntimeError(
                "$.escalation_triggers: escalated_fresh requires a named trigger"
            )
        if not prior_artifact or continuity:
            raise ReviewRuntimeError(
                "$: escalated_fresh requires prior artifact and forbids reused reviewer continuity"
            )
        profile = dict(_INITIAL_PROFILES[policy["review_risk"]])
        fresh = True
        reuse = False
        delta_only = False
        prior_required = True
        effort = policy["reasoning_effort"]
        scope_policy = "counterexample-only"
    else:
        if prior_coverage_status or unreviewed or same_reviewer or triggers:
            raise ReviewRuntimeError(
                "$: initial clean-room review cannot claim prior continuity or escalation"
            )
        if prior_artifact or continuity:
            raise ReviewRuntimeError(
                "$: initial clean-room review forbids prior artifact/reviewer continuity identities"
            )
        profile = dict(_INITIAL_PROFILES[policy["review_risk"]])
        fresh = True
        reuse = False
        delta_only = False
        prior_required = False
        effort = policy["reasoning_effort"]
        scope_policy = "counterexample-only"

    if profile["hard_deadline_sec"] <= profile["soft_deadline_sec"]:
        raise ReviewRuntimeError("$: hard deadline must exceed soft deadline")
    result: dict[str, Any] = {
        "schema": RUNTIME_SCHEMA,
        "review_policy_sha256": _canonical_sha256(policy),
        "review_identity_sha256": review_identity,
        "prior_review_artifact_sha256": prior_artifact,
        "reviewer_continuity_id": continuity,
        "review_risk": policy["review_risk"],
        "context_mode": mode,
        "reviewer_model": policy["reviewer_model"],
        "reasoning_effort": effort,
        "fresh_reviewer": fresh,
        "reuse_prior_reviewer": reuse,
        "delta_only": delta_only,
        "prior_complete_required": prior_required,
        "changed_files": files,
        "changed_lines": lines,
        **profile,
        "max_review_calls": 1,
        "duplicate_full_scope_reviews": 0,
        "scope_expansion_policy": scope_policy,
        "timeout_action": "return-partial-then-interrupt-replan",
        "escalation_triggers": triggers,
        "contract_sha256": "",
    }
    result["contract_sha256"] = _canonical_sha256(result)
    return validate_review_runtime(
        result,
        policy_or_mission=policy_or_mission,
        expectations=_runtime_expectations(
            context_mode=mode,
            changed_files=files,
            changed_lines=lines,
            review_identity_sha256=review_identity,
            prior_review_artifact_sha256=prior_artifact,
            reviewer_continuity_id=continuity,
        ),
    )


def validate_review_runtime(
    value: Mapping[str, Any],
    *,
    policy_or_mission: Mapping[str, Any],
    expectations: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate exact runtime fields and internal route invariants."""
    if not isinstance(value, Mapping) or set(value) != RUNTIME_FIELDS:
        raise ReviewRuntimeError("$: exact review-runtime fields required")
    result = dict(value)
    if result["schema"] != RUNTIME_SCHEMA:
        raise ReviewRuntimeError("$.schema: review-runtime.v16 required")
    if not isinstance(expectations, Mapping) or set(expectations) != EXPECTATION_FIELDS:
        raise ReviewRuntimeError(
            "$.expectations: exact caller-bound runtime expectations required"
        )
    expected_mode = _text(
        expectations["context_mode"], "$.expectations.context_mode"
    )
    expected_files = _strict_int(
        expectations["changed_files"],
        "$.expectations.changed_files",
        minimum=1,
    )
    expected_lines = _strict_int(
        expectations["changed_lines"],
        "$.expectations.changed_lines",
        minimum=1,
    )
    expected_review_identity = _sha256(
        expectations["review_identity_sha256"],
        "$.expectations.review_identity_sha256",
    )
    expected_prior_artifact = _sha256(
        expectations["prior_review_artifact_sha256"],
        "$.expectations.prior_review_artifact_sha256",
        allow_empty=True,
    )
    expected_continuity = _sha256(
        expectations["reviewer_continuity_id"],
        "$.expectations.reviewer_continuity_id",
        allow_empty=True,
    )
    policy_digest = result["review_policy_sha256"]
    if not isinstance(policy_digest, str) or len(policy_digest) != 64 or any(
        c not in "0123456789abcdef" for c in policy_digest
    ):
        raise ReviewRuntimeError("$.review_policy_sha256: lowercase SHA-256 required")
    if result["review_risk"] not in {"low", "medium", "high"}:
        raise ReviewRuntimeError("$.review_risk: known risk required")
    _sha256(result["review_identity_sha256"], "$.review_identity_sha256")
    _sha256(
        result["prior_review_artifact_sha256"],
        "$.prior_review_artifact_sha256",
        allow_empty=True,
    )
    _sha256(
        result["reviewer_continuity_id"],
        "$.reviewer_continuity_id",
        allow_empty=True,
    )
    if result["context_mode"] not in CONTEXT_MODES - {"author_contextual"}:
        raise ReviewRuntimeError("$.context_mode: formal gating mode required")
    _text(result["reviewer_model"], "$.reviewer_model")
    _text(result["reasoning_effort"], "$.reasoning_effort")
    for name in (
        "fresh_reviewer",
        "reuse_prior_reviewer",
        "delta_only",
        "prior_complete_required",
    ):
        _strict_bool(result[name], f"$.{name}")
    for name in (
        "changed_files",
        "changed_lines",
        "max_files",
        "max_changed_lines",
        "max_context_chars",
        "max_tool_calls",
        "max_review_calls",
        "soft_deadline_sec",
        "hard_deadline_sec",
    ):
        _strict_int(result[name], f"$.{name}", minimum=1)
    _strict_int(result["duplicate_full_scope_reviews"], "$.duplicate_full_scope_reviews")
    _text(result["scope_expansion_policy"], "$.scope_expansion_policy")
    _text(result["timeout_action"], "$.timeout_action")
    _texts(result["escalation_triggers"], "$.escalation_triggers")
    if result["hard_deadline_sec"] <= result["soft_deadline_sec"]:
        raise ReviewRuntimeError("$: hard deadline must exceed soft deadline")
    if result["max_review_calls"] != 1 or result["duplicate_full_scope_reviews"] != 0:
        raise ReviewRuntimeError("$: exactly one non-duplicated review call required")
    mode = result["context_mode"]
    if (
        mode != expected_mode
        or result["changed_files"] != expected_files
        or result["changed_lines"] != expected_lines
        or result["review_identity_sha256"] != expected_review_identity
        or result["prior_review_artifact_sha256"] != expected_prior_artifact
        or result["reviewer_continuity_id"] != expected_continuity
    ):
        raise ReviewRuntimeError(
            "$: runtime contract does not match caller-bound identity/delta expectations"
        )
    is_delta = mode == "delta_continuation"
    if is_delta != result["delta_only"] or is_delta != result["reuse_prior_reviewer"]:
        raise ReviewRuntimeError("$: delta continuity flags mismatch")
    if result["fresh_reviewer"] == result["reuse_prior_reviewer"]:
        raise ReviewRuntimeError("$: fresh/reused reviewer flags must be opposite")
    expected_profile = (
        _DELTA_PROFILE if is_delta else _INITIAL_PROFILES[result["review_risk"]]
    )
    for name, expected in expected_profile.items():
        if result[name] != expected:
            raise ReviewRuntimeError(f"$.{name}: compiled runtime profile mismatch")
    expected_prior = mode in {"delta_continuation", "escalated_fresh"}
    if result["prior_complete_required"] is not expected_prior:
        raise ReviewRuntimeError("$: prior-complete requirement does not match context mode")
    expected_scope_policy = (
        "escalate-on-new-falsifiable-evidence"
        if is_delta
        else "counterexample-only"
    )
    if result["scope_expansion_policy"] != expected_scope_policy:
        raise ReviewRuntimeError("$: scope expansion policy does not match context mode")
    if result["timeout_action"] != "return-partial-then-interrupt-replan":
        raise ReviewRuntimeError("$: timeout action cannot be weakened")
    if mode == "escalated_fresh" and not result["escalation_triggers"]:
        raise ReviewRuntimeError("$: escalated_fresh requires escalation triggers")
    if mode != "escalated_fresh" and result["escalation_triggers"]:
        raise ReviewRuntimeError("$: escalation triggers require escalated_fresh")
    if is_delta and (
        not result["prior_review_artifact_sha256"]
        or not result["reviewer_continuity_id"]
    ):
        raise ReviewRuntimeError("$: delta continuation identities are incomplete")
    if mode == "escalated_fresh" and (
        not result["prior_review_artifact_sha256"]
        or result["reviewer_continuity_id"]
    ):
        raise ReviewRuntimeError("$: escalated fresh identity mismatch")
    if mode == "independent_clean_room" and (
        result["prior_review_artifact_sha256"]
        or result["reviewer_continuity_id"]
    ):
        raise ReviewRuntimeError("$: initial clean-room identity mismatch")
    if is_delta and result["reasoning_effort"] != "high":
        raise ReviewRuntimeError("$: bounded delta continuation requires high effort")
    if not is_delta and result["review_risk"] == "high" and result["reasoning_effort"] != "xhigh":
        raise ReviewRuntimeError("$: fresh high-risk review requires xhigh effort")
    expected_model = (
        "gpt-5.6-sol" if result["review_risk"] == "high" else "gpt-5.6-terra"
    )
    if result["reviewer_model"] != expected_model:
        raise ReviewRuntimeError("$: reviewer model does not match risk route")
    if result["review_risk"] in {"low", "medium"} and result["reasoning_effort"] != "high":
        raise ReviewRuntimeError("$: low/medium review requires high effort")
    if is_delta and (
        result["changed_files"] > result["max_files"]
        or result["changed_lines"] > result["max_changed_lines"]
    ):
        raise ReviewRuntimeError("$: delta exceeds compiled runtime bounds")
    digest = result["contract_sha256"]
    if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
        raise ReviewRuntimeError("$.contract_sha256: lowercase SHA-256 required")
    unhashed = dict(result)
    unhashed["contract_sha256"] = ""
    if _canonical_sha256(unhashed) != digest:
        raise ReviewRuntimeError("$.contract_sha256: runtime contract hash mismatch")
    policy = resolve_review_policy(policy_or_mission)
    if (
        result["review_policy_sha256"] != _canonical_sha256(policy)
        or result["review_risk"] != policy["review_risk"]
        or result["reviewer_model"] != policy["reviewer_model"]
    ):
        raise ReviewRuntimeError("$: runtime contract does not match review policy")
    return result


def review_progress_decision(
    runtime: Mapping[str, Any],
    *,
    policy_or_mission: Mapping[str, Any],
    runtime_expectations: Mapping[str, Any],
    elapsed_sec: int,
    tool_calls: int,
    files_read: int,
    context_chars: int,
    review_calls: int,
    duplicate_full_scope_reviews: int,
    verdict_present: bool,
    coverage_complete: bool,
    unreviewed_count: int,
    scope_expansion_requested: bool = False,
    new_falsifiable_evidence: bool = False,
) -> dict[str, Any]:
    """Return the control-plane action for current bounded review progress."""
    contract = validate_review_runtime(
        runtime,
        policy_or_mission=policy_or_mission,
        expectations=runtime_expectations,
    )
    elapsed = _strict_int(elapsed_sec, "$.elapsed_sec")
    calls = _strict_int(tool_calls, "$.tool_calls")
    files = _strict_int(files_read, "$.files_read")
    context = _strict_int(context_chars, "$.context_chars")
    reviews = _strict_int(review_calls, "$.review_calls")
    duplicate_reviews = _strict_int(
        duplicate_full_scope_reviews, "$.duplicate_full_scope_reviews"
    )
    verdict = _strict_bool(verdict_present, "$.verdict_present")
    complete = _strict_bool(coverage_complete, "$.coverage_complete")
    unreviewed = _strict_int(unreviewed_count, "$.unreviewed_count")
    expansion = _strict_bool(scope_expansion_requested, "$.scope_expansion_requested")
    new_evidence = _strict_bool(new_falsifiable_evidence, "$.new_falsifiable_evidence")
    exceeded = (
        calls > contract["max_tool_calls"]
        or files > contract["max_files"]
        or context > contract["max_context_chars"]
        or reviews > contract["max_review_calls"]
        or duplicate_reviews > contract["duplicate_full_scope_reviews"]
        or elapsed >= contract["hard_deadline_sec"]
    )

    if exceeded:
        action, reason = "INTERRUPT_REPLAN", "HARD_RUNTIME_BUDGET_EXCEEDED"
    elif contract["context_mode"] == "delta_continuation" and new_evidence:
        action, reason = "ESCALATE_FRESH", "NEW_FALSIFIABLE_EVIDENCE"
    elif expansion and not new_evidence:
        action, reason = "STOP_SCOPE_EXPANSION", "SCOPE_EXPANSION_LACKS_COUNTEREXAMPLE"
    elif verdict:
        if complete and unreviewed == 0:
            action, reason = "ACCEPT_REPORT", "COMPLETE_REPORT_AVAILABLE"
        else:
            action, reason = "RETURN_PARTIAL", "VERDICT_WITH_INCOMPLETE_COVERAGE"
    elif elapsed >= contract["soft_deadline_sec"]:
        action, reason = "REQUEST_REPORT", "SOFT_DEADLINE_REACHED"
    else:
        action, reason = "CONTINUE", "WITHIN_RUNTIME_BUDGET"
    approval_eligible = (
        action == "ACCEPT_REPORT"
        and complete
        and unreviewed == 0
        and not exceeded
    )
    return {
        "schema": PROGRESS_SCHEMA,
        "contract_sha256": contract["contract_sha256"],
        "action": action,
        "reason_code": reason,
        "approval_eligible": approval_eligible,
        "elapsed_sec": elapsed,
        "tool_calls": calls,
        "files_read": files,
        "context_chars": context,
        "review_calls": reviews,
        "duplicate_full_scope_reviews": duplicate_reviews,
        "verdict_present": verdict,
        "coverage_complete": complete,
        "unreviewed_count": unreviewed,
        "scope_expansion_requested": expansion,
        "new_falsifiable_evidence": new_evidence,
        "budget_exceeded": exceeded,
    }


def validate_review_progress(
    value: Mapping[str, Any],
    *,
    runtime: Mapping[str, Any],
    policy_or_mission: Mapping[str, Any],
    runtime_expectations: Mapping[str, Any],
) -> dict[str, Any]:
    """Recompute a progress action from its runtime contract and observations."""
    if not isinstance(value, Mapping) or set(value) != PROGRESS_FIELDS:
        raise ReviewRuntimeError("$: exact review-runtime-progress fields required")
    if value.get("schema") != PROGRESS_SCHEMA:
        raise ReviewRuntimeError(
            "$.schema: review-runtime-progress.v16 required"
        )
    expected = review_progress_decision(
        runtime,
        policy_or_mission=policy_or_mission,
        runtime_expectations=runtime_expectations,
        elapsed_sec=value.get("elapsed_sec"),
        tool_calls=value.get("tool_calls"),
        files_read=value.get("files_read"),
        context_chars=value.get("context_chars"),
        review_calls=value.get("review_calls"),
        duplicate_full_scope_reviews=value.get(
            "duplicate_full_scope_reviews"
        ),
        verdict_present=value.get("verdict_present"),
        coverage_complete=value.get("coverage_complete"),
        unreviewed_count=value.get("unreviewed_count"),
        scope_expansion_requested=value.get("scope_expansion_requested"),
        new_falsifiable_evidence=value.get("new_falsifiable_evidence"),
    )
    if dict(value) != expected:
        raise ReviewRuntimeError(
            "$: progress decision does not match runtime contract"
        )
    return expected


__all__ = [
    "EXPECTATION_FIELDS",
    "PROGRESS_FIELDS",
    "PROGRESS_SCHEMA",
    "RUNTIME_FIELDS",
    "RUNTIME_SCHEMA",
    "ReviewRuntimeError",
    "compile_review_runtime",
    "review_progress_decision",
    "validate_review_progress",
    "validate_review_runtime",
]

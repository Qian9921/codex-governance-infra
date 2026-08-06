"""Small, machine-testable model-role policy for adaptive execution.

This module is deliberately a policy table plus validators.  It is not a
scheduler, a daemon, or a second workflow engine.  Callers use ``route_mission``
to decide who owns a mission and use the validation helpers at delegation and
review boundaries.  Hooks may record violations, but QUICK/STANDARD work is
not turned into a ceremony by this policy.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

try:  # Package import and direct hook execution are both supported.
    from .delegation_contract import model_family
except ImportError:  # pragma: no cover - exercised by direct execution.
    from delegation_contract import model_family


LUNA = "gpt-5.6-luna"
SOL = "gpt-5.6-sol"
TERRA = "gpt-5.6-terra"
SPARK = "gpt-5.3-codex-spark"

RISK_LEVELS = ("R0", "R1", "R2", "R3", "R4")
HIGH_RISK_LEVELS = frozenset(("R2", "R3", "R4"))
MAX_NESTED_DEPTH = 2
MAX_CROSS_MODEL_HOPS = 1


class ModelRoleError(ValueError):
    """Raised when a role route, delegation, or review packet is unsafe."""


def _family(value: Any) -> str:
    family = model_family(value)
    if family not in {"sol", "luna", "terra", "spark"}:
        raise ModelRoleError("known model family required")
    return family


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ModelRoleError(f"{field} is required")
    return value.strip()


def _bool(value: Any, field: str) -> bool:
    if type(value) is not bool:
        raise ModelRoleError(f"{field} must be boolean")
    return value


def _scope(value: Iterable[str], field: str) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)):
        raise ModelRoleError(f"{field} must be a path sequence")
    paths = tuple(_text(item, field) for item in value)
    if not paths:
        raise ModelRoleError(f"{field} must not be empty")
    if any(path.startswith("/") or ".." in path.split("/") for path in paths):
        raise ModelRoleError(f"{field} contains an unsafe path")
    return paths


def _path_within(child: str, parent: str) -> bool:
    parent = parent.rstrip("/")
    return child == parent or child.startswith(parent + "/")


def _role_tokens(role: str) -> set[str]:
    tokens = {token.lower() for token in re.findall(r"[a-z0-9]+", role)}
    # These aliases keep task names short while still exposing their role.
    aliases = {
        "independent_final_reviewer": {"reviewer", "review"},
        "contract_gate": {"gate", "contract"},
        "execution_lead": {"execution", "execute"},
        "continuity_fallback": {"fallback", "continuity"},
        "mechanical": {"mechanical", "execution"},
        "consultant": {"consultant", "consult"},
    }
    return tokens | aliases.get(role, set())


def _name_tokens(task_name: str) -> set[str]:
    return {token.lower() for token in re.findall(r"[a-z0-9]+", task_name)}


def validate_receipt_identity(receipt: Mapping[str, Any]) -> dict[str, str]:
    """Require transparent model/role identity at a new policy boundary.

    Existing v15 packets remain backward compatible and advisory.  New
    role-routed calls can opt into this stricter helper so a Terra fallback can
    never be recorded as an unnamed Luna task.
    """

    if not isinstance(receipt, Mapping):
        raise ModelRoleError("receipt must be an object")
    required = (
        "requested_model", "actual_model", "role", "fallback_reason", "task_name",
    )
    for field in required:
        _text(receipt.get(field), field)
    requested = _family(receipt["requested_model"])
    actual = _family(receipt["actual_model"])
    task_name = receipt["task_name"]
    tokens = _name_tokens(task_name)
    named_families = tokens & {"sol", "luna", "terra", "spark"}
    if len(named_families) > 1:
        raise ModelRoleError("task_name model family is ambiguous")
    if actual not in tokens:
        raise ModelRoleError("task_name must expose actual model family")
    if not (_role_tokens(receipt["role"]) & tokens):
        raise ModelRoleError("task_name must expose role")
    fallback = _text(receipt["fallback_reason"], "fallback_reason").strip()
    fallback_kind = fallback.lower()
    if requested != actual:
        if fallback_kind in {"none", "null", "n/a"}:
            raise ModelRoleError("fallback_reason required when model changes")
        if requested in tokens:
            raise ModelRoleError("fallback task_name retains requested model family")
    elif fallback_kind not in {"none", "null", "n/a"}:
        raise ModelRoleError("fallback_reason must be none when model is unchanged")
    return {
        "requested_model": receipt["requested_model"],
        "actual_model": receipt["actual_model"],
        "role": receipt["role"],
        "fallback_reason": fallback,
        "task_name": task_name,
    }


def validate_controller_request(
    requested_model: str,
    *,
    luna_available: bool = True,
    fallback_reason: str | None = None,
) -> str:
    """Validate the controller choice without silently changing models."""

    requested = _family(requested_model)
    if requested == "terra":
        if luna_available:
            raise ModelRoleError("Terra cannot be the universal controller")
        if not fallback_reason:
            raise ModelRoleError("Terra fallback requires an explicit reason")
        return "terra"
    if requested == "sol":
        raise ModelRoleError("Sol is a gate/reviewer, not the universal lifecycle controller")
    if requested == "spark":
        raise ModelRoleError("Spark is not enabled by the adaptive role policy")
    if requested == "luna" and not luna_available:
        if not fallback_reason:
            raise ModelRoleError("Luna unavailable requires an explicit fallback reason")
        return "terra"
    return requested


def route_mission(
    risk: str,
    *,
    profile: str = "STANDARD",
    luna_available: bool = True,
    review_required: bool | None = None,
) -> dict[str, Any]:
    """Return the compact lifecycle route for one mission.

    The result is declarative: a caller still owns spawning, leases, evidence,
    and review.  Luna is the normal controller/executor; Sol enters only for a
    short high-risk contract gate or independent review; Terra is explicit
    continuity fallback only.
    """

    risk = _text(risk, "risk").upper()
    if risk not in RISK_LEVELS:
        raise ModelRoleError("risk must be one of R0, R1, R2, R3, R4")
    profile = _text(profile, "profile").upper()
    if profile not in {"QUICK", "STANDARD", "STRICT"}:
        raise ModelRoleError("profile must be QUICK, STANDARD, or STRICT")
    fallback_reason = "none"
    actual_controller = LUNA
    controller_role = "controller"
    if not luna_available:
        fallback_reason = "luna_unavailable"
        actual_controller = TERRA
        controller_role = "continuity_fallback"
    if review_required is None:
        review_required = profile in {"STANDARD", "STRICT"}
    if type(review_required) is not bool:
        raise ModelRoleError("review_required must be boolean")
    high_risk = risk in HIGH_RISK_LEVELS
    if high_risk and not review_required:
        raise ModelRoleError("R2+ missions require an independent Sol final review")
    return {
        "risk": risk,
        "profile": profile,
        "requested_controller_model": LUNA,
        "controller_model": actual_controller,
        "execution_model": actual_controller,
        "controller_role": controller_role,
        "fallback_reason": fallback_reason,
        "universal_controller": actual_controller == LUNA,
        "sol_contract_gate": high_risk,
        "sol_contract_reasoning": "xhigh" if high_risk else None,
        "sol_research_interpretation": risk == "R4",
        "sol_inner_loop_required": False,
        "review": {
            "required": review_required,
            "model": SOL,
            "role": "independent_final_reviewer",
            "fresh": True,
            "read_only": True,
            "counterexample_required": high_risk,
            "max_rounds": 2,
            "delta_only_after_first": True,
        },
        "nested": {
            "allowed_child_models": [LUNA, SOL],
            "max_depth": MAX_NESTED_DEPTH,
            "max_cross_model_hops": MAX_CROSS_MODEL_HOPS,
            "cross_model_consultation_limit": 1,
            "scope_rule": "child scope must be a subset of parent scope",
            "allowed_edges": ["sol->luna:mechanical", "luna->sol:consultant"],
            "ping_pong": False,
        },
        "spark_policy": "legacy/explicit only; disabled by policy",
    }


def validate_nested_delegation(
    parent: Mapping[str, Any],
    child: Mapping[str, Any],
    *,
    depth: int,
    parent_scope: Iterable[str],
    child_scope: Iterable[str],
    lineage: Sequence[str] = (),
    uncertainty_id: str | None = None,
    seen_uncertainties: Iterable[str] = (),
) -> dict[str, Any]:
    """Validate one bounded nested edge and reject ping-pong delegation."""

    if type(depth) is not int or not 1 <= depth <= MAX_NESTED_DEPTH:
        raise ModelRoleError("nested delegation depth must be 1 or 2")
    parent_model = _family(parent.get("actual_model", parent.get("assigned_model")))
    child_model = _family(child.get("actual_model", child.get("assigned_model")))
    if child_model in {"terra", "spark"}:
        raise ModelRoleError("nested child must be Luna or Sol")
    parent_paths = _scope(parent_scope, "parent_scope")
    child_paths = _scope(child_scope, "child_scope")
    if not all(any(_path_within(path, allowed) for allowed in parent_paths) for path in child_paths):
        raise ModelRoleError("child scope must be contained by parent scope")
    if set(child_paths) == set(parent_paths):
        raise ModelRoleError("child scope must strictly narrow the parent scope")
    prior = tuple(_family(item) for item in lineage) if lineage else (parent_model,)
    if prior[-1] != parent_model:
        raise ModelRoleError("lineage does not end at parent model")
    cross_hops = sum(left != right for left, right in zip(prior, prior[1:]))
    if parent_model != child_model:
        if cross_hops >= MAX_CROSS_MODEL_HOPS:
            raise ModelRoleError("Luna/Sol ping-pong is not allowed")
        if (parent_model, child_model) == ("sol", "luna"):
            allowed_roles = {"mechanical", "execution", "test", "evidence", "build"}
            role = _text(child.get("role"), "child.role").lower()
            if role not in allowed_roles:
                raise ModelRoleError("Sol may delegate only bounded mechanical work to Luna")
        elif (parent_model, child_model) == ("luna", "sol"):
            role = _text(child.get("role"), "child.role").lower()
            if role not in {"consultant", "math_consultant", "numerical_consultant"}:
                raise ModelRoleError("Luna may delegate only a narrow Sol consultation")
            uncertainty_id = _text(uncertainty_id, "uncertainty_id")
            if uncertainty_id in set(seen_uncertainties):
                raise ModelRoleError("uncertainty consultation is already in flight")
        else:
            raise ModelRoleError("unsupported cross-model delegation edge")
    return {
        "parent_model": parent_model,
        "child_model": child_model,
        "depth": depth,
        "scope": list(child_paths),
        "cross_model_hops": cross_hops + (parent_model != child_model),
        "uncertainty_id": uncertainty_id,
    }


def validate_final_review(
    review: Mapping[str, Any],
    *,
    risk: str,
    author_lineage: Iterable[str] = (),
) -> dict[str, Any]:
    """Validate independent Sol review and its high-risk evidence contract."""

    risk = _text(risk, "risk").upper()
    if risk not in RISK_LEVELS:
        raise ModelRoleError("risk must be one of R0, R1, R2, R3, R4")
    if not isinstance(review, Mapping):
        raise ModelRoleError("review must be an object")
    if _family(review.get("reviewer_model")) != "sol":
        raise ModelRoleError("final reviewer must be Sol")
    parent_task_id = _text(review.get("parent_task_id"), "parent_task_id")
    controller_task_id = _text(review.get("controller_task_id"), "controller_task_id")
    reviewer_parent_task_id = _text(
        review.get("reviewer_parent_task_id"), "reviewer_parent_task_id"
    )
    author_lineage_id = _text(review.get("author_lineage_id"), "author_lineage_id")
    reviewer_lineage_id = _text(
        review.get("reviewer_lineage_id"), "reviewer_lineage_id"
    )
    if reviewer_parent_task_id != parent_task_id:
        raise ModelRoleError("reviewer parent identity does not match mission parent")
    if reviewer_lineage_id == author_lineage_id:
        raise ModelRoleError("reviewer and author lineage must be distinct")
    author_ids = set(author_lineage)
    if controller_task_id not in author_ids:
        raise ModelRoleError("controller identity must be bound to author lineage")
    if reviewer_lineage_id in author_ids:
        raise ModelRoleError("reviewer lineage overlaps author lineage")
    for field in ("fresh", "read_only", "reviewer_is_writer"):
        if field not in review:
            raise ModelRoleError(f"{field} is required")
    fresh = _bool(review["fresh"], "fresh")
    if not _bool(review["read_only"], "read_only"):
        raise ModelRoleError("final reviewer must be read-only")
    if _bool(review["reviewer_is_writer"], "reviewer_is_writer"):
        raise ModelRoleError("writer cannot be the independent final reviewer")
    reviewer_id = _text(review.get("reviewer_task_id"), "reviewer_task_id")
    if reviewer_id in set(author_lineage):
        raise ModelRoleError("author-lineage Sol consultant cannot review independently")
    rounds = review.get("round", 1)
    if type(rounds) is not int or rounds < 1 or rounds > 2:
        raise ModelRoleError("review rounds are limited to two")
    if rounds == 1 and not fresh:
        raise ModelRoleError("first final review must be fresh")
    if rounds > 1:
        if fresh:
            raise ModelRoleError("delta review must reuse the original reviewer")
        if not _bool(review.get("delta_only"), "delta_only"):
            raise ModelRoleError("follow-up review must be delta-only")
        if _text(review.get("reviewer_continuity_id"), "reviewer_continuity_id") != reviewer_id:
            raise ModelRoleError("delta review must reuse the same reviewer")
    if risk in HIGH_RISK_LEVELS:
        for field in ("source_ref", "contract_ref", "tests_ref"):
            _text(review.get(field), field)
        counterexample_ref = review.get("source_derived_counterexample_ref")
        if counterexample_ref is not None:
            _text(counterexample_ref, "source_derived_counterexample_ref")
        else:
            _text(review.get("boundary_analysis_ref"), "boundary_analysis_ref")
    return {
        "reviewer_model": review["reviewer_model"],
        "reviewer_task_id": reviewer_id,
        "parent_task_id": parent_task_id,
        "controller_task_id": controller_task_id,
        "reviewer_parent_task_id": reviewer_parent_task_id,
        "author_lineage_id": author_lineage_id,
        "reviewer_lineage_id": reviewer_lineage_id,
        "round": rounds,
        "delta_only": bool(review.get("delta_only", False)),
        "risk": risk,
    }


POLICY_SUMMARY = {
    "controller": LUNA,
    "execution": LUNA,
    "recovery": LUNA,
    "sol_contract_gate": "R2+",
    "sol_final_review": True,
    "terra": "continuity fallback only when Luna is unavailable",
    "spark": "legacy/explicit only; disabled by this policy",
    "nested_max_depth": MAX_NESTED_DEPTH,
    "nested_edges": ["sol->luna:mechanical", "luna->sol:consultant"],
    "no_ping_pong": True,
}

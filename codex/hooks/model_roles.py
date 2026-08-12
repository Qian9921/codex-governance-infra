"""Small, machine-testable model-role policy for adaptive execution.

This module is deliberately a policy table plus validators.  It is not a
scheduler, a daemon, or a second workflow engine.  Callers use ``route_mission``
to decide who owns a mission and use the validation helpers at delegation and
review boundaries.  Hooks may record violations, but QUICK/STANDARD work is
not turned into a ceremony by this policy.
"""

from __future__ import annotations

import math
import posixpath
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
SOL_CONTRACT_REASONING = "medium"
SOL_REVIEWER_REASONING = "high"
SOL_CONTRACT_MAX_OUTPUT_TOKENS = 2048
SOL_REVIEWER_MAX_OUTPUT_TOKENS = 4096
SOL_ALLOWED_REASONING = frozenset(("medium", "high"))

RISK_LEVELS = ("R0", "R1", "R2", "R3", "R4")
HIGH_RISK_LEVELS = frozenset(("R2", "R3", "R4"))
TERRA_BRIDGE_KINDS = ("TERRA_REPLAN", "TERRA_TRIAGE")
TERRA_BRIDGE_ALLOWED_RISKS = frozenset(("R0", "R1"))
TERRA_BRIDGE_MAX_DURATION_SEC = 900
TERRA_BRIDGE_MAX_TOOL_CALLS = 32
TERRA_BRIDGE_MAX_OUTPUT_TOKENS = 8192
TERRA_BRIDGE_REQUEST_FIELDS = frozenset(
    (
        "bridge_kind",
        "parent_task_id",
        "parent_model",
        "bridge_task_id",
        "requested_model",
        "actual_model",
        "fallback_reason",
        "role",
        "task_name",
        "risk",
        "parent_scope",
        "scope",
        "permissions",
        "max_duration_sec",
        "max_tool_calls",
        "max_output_tokens",
        "handoff_reason",
        "return_to_model",
        "return_to_task_id",
        "final_verdict",
        "can_write",
        "can_git",
        "can_review",
        "can_merge",
        "can_spawn",
        "long_listener",
        "continuation",
        "retry_allowed",
        "control_returned",
    )
)
TERRA_BRIDGE_RESULT_FIELDS = frozenset(
    (
        "bridge_task_id",
        "parent_task_id",
        "actual_model",
        "status",
        "return_to_model",
        "return_to_task_id",
        "control_returned",
        "final_verdict",
        "can_write",
        "can_git",
        "can_review",
        "can_merge",
        "can_spawn",
        "spawned_children",
        "long_listener",
        "retry_used",
        "elapsed_sec",
        "tool_calls",
        "output_tokens",
    )
)
CODE_MISSION_TOOL_INDEX_POLICY_SCHEMA = "code-mission-tool-index-policy.v1"
CODE_MISSION_TOOL_INDEX_POLICY_FIELDS = frozenset(
    (
        "schema",
        "mission_kind",
        "repository_work",
        "repo_root_sha256",
        "git_head_sha",
        "git_tree_sha",
        "worktree_sha256",
        "codegraph_index_sha256",
        "semble_index_sha256",
        "codegraph_health",
        "semble_health",
        "semble_semantic_discovery",
        "codegraph_structural_evidence",
        "semble_semantic_evidence_ref",
        "codegraph_structural_evidence_ref",
        "candidate_ready",
        "n_a_reason",
        "repair_owner",
        "repair_state",
        "dependent_claim_blocked",
        "quota_enforced",
    )
)
CODE_MISSION_EVIDENCE_SCHEMA = "code-mission-evidence.v1"
CODE_MISSION_EVIDENCE_FIELDS = frozenset(
    (
        "schema",
        "kind",
        "ref",
        "receipt_sha256",
        "query_sha256",
        "repo_root_sha256",
        "git_head_sha",
        "git_tree_sha",
        "worktree_sha256",
        "index_sha256",
    )
)
CODE_MISSION_TOOL_HEALTH_STATES = frozenset(
    (
        "HEALTHY",
        "RECOVERING",
        "DEGRADED",
        "EXTERNAL_WAIT",
        "USER_ACTION_REQUIRED",
        "UNRECOVERABLE",
        "N/A",
    )
)
MAX_NESTED_DEPTH = 2
MAX_CROSS_MODEL_HOPS = 1
NONE_LIKE_FALLBACKS = frozenset(("", "none", "null", "n/a", "na"))
EXECUTION_CONTEXT = {
    "mode": "bounded",
    "full_history": False,
    "max_history_messages": 0,
    "max_context_tokens": 1200,
}


class ModelRoleError(ValueError):
    """Raised when a role route, delegation, or review packet is unsafe."""


def validate_sol_reasoning_effort(role: str, effort: str) -> str:
    """Keep Sol's two bounded roles on the supported medium/high envelope."""

    role_text = _text(role, "role").lower().replace("-", "_")
    value = _text(effort, "reasoning_effort").lower()
    expected = {
        "sol_contract": SOL_CONTRACT_REASONING,
        "contract_gate": SOL_CONTRACT_REASONING,
        "sol_reviewer": SOL_REVIEWER_REASONING,
        "reviewer": SOL_REVIEWER_REASONING,
        "independent_final_reviewer": SOL_REVIEWER_REASONING,
    }.get(role_text)
    if expected is None:
        raise ModelRoleError("unknown Sol role")
    if value not in SOL_ALLOWED_REASONING or value != expected:
        raise ModelRoleError(f"{role_text} requires reasoning_effort={expected}")
    return value


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
    paths = []
    for item in value:
        path = _text(item, field)
        if path.startswith("/") or ".." in path.split("/"):
            raise ModelRoleError(f"{field} contains an unsafe path")
        paths.append(posixpath.normpath(path))
    paths = tuple(paths)
    if not paths:
        raise ModelRoleError(f"{field} must not be empty")
    return paths


def _normalized_fallback_reason(value: Any) -> str:
    """Normalize a fallback reason and reject placeholders on Terra routes."""

    reason = _text(value, "fallback_reason").strip().lower()
    if reason in NONE_LIKE_FALLBACKS:
        raise ModelRoleError("Terra fallback requires a non-empty reason")
    return reason


def _path_within(child: str, parent: str) -> bool:
    parent = parent.rstrip("/")
    return child == parent or child.startswith(parent + "/")


def _bounded_int(value: Any, field: str, maximum: int) -> int:
    if type(value) is not int or value < 1 or value > maximum:
        raise ModelRoleError(f"{field} must be an integer in the range 1..{maximum}")
    return value


def _nonnegative_int(value: Any, field: str) -> int:
    if type(value) is not int or value < 0:
        raise ModelRoleError(f"{field} must be a non-negative integer")
    return value


def _nonnegative_number(value: Any, field: str) -> int | float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ModelRoleError(f"{field} must be a non-negative finite number")
    if not math.isfinite(float(value)) or value < 0:
        raise ModelRoleError(f"{field} must be a non-negative finite number")
    return value


def _optional_hash(value: Any, field: str, length: int) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or re.fullmatch(rf"[0-9a-f]{{{length}}}", value) is None:
        raise ModelRoleError(f"{field} must be lowercase hexadecimal ({length} chars)")
    return value


def _required_hash(value: Any, field: str, length: int) -> str:
    normalized = _optional_hash(value, field, length)
    if normalized is None:
        raise ModelRoleError(f"{field} is required")
    return normalized


def _permissions(value: Any) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)):
        raise ModelRoleError("permissions must be a sequence")
    try:
        values = tuple(_text(item, "permissions").lower() for item in value)
    except TypeError as exc:
        raise ModelRoleError("permissions must be a sequence") from exc
    if not values or len(set(values)) != len(values):
        raise ModelRoleError("permissions must contain unique values")
    return values


def _strict_child_scope(
    parent: Iterable[str], child: Iterable[str]
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    parent_paths = _scope(parent, "parent_scope")
    child_paths = _scope(child, "scope")
    if set(parent_paths) == set(child_paths):
        raise ModelRoleError("bridge scope must strictly narrow the parent scope")
    for path in child_paths:
        if any(path == allowed for allowed in parent_paths):
            raise ModelRoleError("bridge scope must strictly narrow the parent scope")
        if not any(_path_within(path, allowed) for allowed in parent_paths):
            raise ModelRoleError("bridge scope must be contained by parent scope")
    return parent_paths, child_paths


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


def _normalized_task_name(actual: str, role: str, task_name: str) -> str:
    """Add missing machine identity markers without changing the source name."""

    role_slug = re.sub(r"[^a-z0-9]+", "-", role.lower()).strip("-") or "task"
    tokens = _name_tokens(task_name)
    prefix = []
    if actual not in tokens:
        prefix.append(actual)
    if not (_role_tokens(role) & tokens):
        prefix.append(role_slug)
    return "-".join((*prefix, task_name)) if prefix else task_name


def normalize_receipt_identity(
    receipt: Mapping[str, Any], *, strict: bool = False
) -> dict[str, Any]:
    """Normalize model/task identity for adaptive routing.

    Runtime ``actual_model`` is authoritative.  A task name that explicitly
    claims a different known family is deliberate identity misrepresentation
    and always blocks.  Missing markers, role drift, and a follow-up requested
    versus actual-family change are advisory in adaptive mode and receive a
    deterministic normalized name.  ``strict=True`` retains the fail-closed
    V16 behavior for explicit strict routes.
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
    role = receipt["role"]
    task_name = receipt["task_name"]
    tokens = _name_tokens(task_name)
    named_families = tokens & {"sol", "luna", "terra", "spark"}
    if len(named_families) > 1:
        raise ModelRoleError("task_name model family is ambiguous")
    if named_families and actual not in named_families:
        raise ModelRoleError("task_name misrepresents actual model family")

    fallback = _text(receipt["fallback_reason"], "fallback_reason").strip()
    fallback_kind = fallback.lower()
    advisory: list[str] = []
    if requested != actual:
        if fallback_kind in NONE_LIKE_FALLBACKS:
            if strict:
                raise ModelRoleError("fallback_reason required when model changes")
            advisory.append("requested_actual_family_drift")
        elif requested in tokens:
            # A fallback name retaining the requested family is never safe to
            # normalize because it would make telemetry lie about execution.
            raise ModelRoleError("fallback task_name retains requested model family")
    elif fallback_kind not in NONE_LIKE_FALLBACKS:
        if strict:
            raise ModelRoleError("fallback_reason must be none when model is unchanged")
        advisory.append("unexpected_fallback_reason")

    normalized_name = _normalized_task_name(actual, role, task_name)
    if actual not in tokens:
        advisory.append("task_name_missing_actual_family")
    if not (_role_tokens(role) & tokens):
        advisory.append("task_name_missing_role")
    if strict and advisory:
        raise ModelRoleError("strict identity requires task/model fields to agree")
    return {
        "requested_model": receipt["requested_model"],
        "actual_model": receipt["actual_model"],
        "role": role,
        "fallback_reason": fallback,
        "task_name": task_name,
        "normalized_task_name": normalized_name,
        "requested_model_family": requested,
        "actual_model_family": actual,
        "status": "advisory" if advisory else "ok",
        "advisory": tuple(advisory),
        "strict": strict,
    }


def validate_receipt_identity(receipt: Mapping[str, Any]) -> dict[str, str]:
    """Require transparent model/role identity at a new policy boundary.

    Existing v15 packets remain backward compatible and advisory.  New
    role-routed calls can opt into this stricter helper so a Terra fallback can
    never be recorded as an unnamed Luna task.
    """

    normalized = normalize_receipt_identity(receipt, strict=True)
    return {
        field: normalized[field]
        for field in ("requested_model", "actual_model", "role", "fallback_reason", "task_name")
    }


def validate_terra_bridge_request(bridge: Mapping[str, Any]) -> dict[str, Any]:
    """Validate one short-lived Terra synthesis/triage handoff.

    A bridge is a deliberately separate contract from ordinary nested
    delegation and Luna-unavailable continuity fallback.  Terra receives a
    narrow, bounded read/plan slice from Luna and must return control directly
    to that Luna parent; it is never a controller, reviewer, or Git operator.
    """

    if not isinstance(bridge, Mapping):
        raise ModelRoleError("Terra bridge must be an object")
    bridge_fields = set(bridge)
    if bridge_fields != TERRA_BRIDGE_REQUEST_FIELDS:
        missing = sorted(TERRA_BRIDGE_REQUEST_FIELDS - bridge_fields)
        unexpected = sorted(bridge_fields - TERRA_BRIDGE_REQUEST_FIELDS)
        details = []
        if missing:
            details.append("missing=" + ",".join(missing))
        if unexpected:
            details.append("unexpected=" + ",".join(unexpected))
        raise ModelRoleError("Terra bridge fields must match exactly (" + ";".join(details) + ")")

    kind = _text(bridge["bridge_kind"], "bridge_kind").upper()
    if kind not in TERRA_BRIDGE_KINDS:
        raise ModelRoleError("bridge_kind must be TERRA_REPLAN or TERRA_TRIAGE")
    parent_task_id = _text(bridge["parent_task_id"], "parent_task_id")
    bridge_task_id = _text(bridge["bridge_task_id"], "bridge_task_id")
    if parent_task_id == bridge_task_id:
        raise ModelRoleError("bridge_task_id must differ from parent_task_id")
    if bridge["parent_model"] != LUNA:
        raise ModelRoleError("Terra bridge parent must be Luna")
    if bridge["requested_model"] != TERRA:
        raise ModelRoleError("Terra bridge requested_model must equal gpt-5.6-terra")
    if bridge["actual_model"] != bridge["requested_model"]:
        raise ModelRoleError("Terra bridge actual_model must equal requested_model exactly")
    if bridge["fallback_reason"] != "none":
        raise ModelRoleError("explicit Terra bridge fallback_reason must be none")

    role = _text(bridge["role"], "role").lower()
    if role != kind.lower():
        raise ModelRoleError("Terra bridge role must match bridge_kind")
    task_tokens = _name_tokens(_text(bridge["task_name"], "task_name"))
    required_task_tokens = _name_tokens(kind.lower())
    if not required_task_tokens.issubset(task_tokens):
        raise ModelRoleError("bridge task_name must expose Terra and its bridge role")
    if task_tokens & {"fallback", "continuity"}:
        raise ModelRoleError("bridge task_name cannot be labeled as continuity fallback")
    validate_receipt_identity(
        {
            "requested_model": bridge["requested_model"],
            "actual_model": bridge["actual_model"],
            "role": bridge["role"],
            "fallback_reason": bridge["fallback_reason"],
            "task_name": bridge["task_name"],
        }
    )

    risk = _text(bridge["risk"], "risk").upper()
    if risk not in TERRA_BRIDGE_ALLOWED_RISKS:
        raise ModelRoleError("Terra bridges are limited to R0/R1 work")
    parent_scope, scope = _strict_child_scope(bridge["parent_scope"], bridge["scope"])
    permissions = _permissions(bridge["permissions"])
    allowed = {"read", "plan"}
    if not set(permissions).issubset(allowed) or "read" not in permissions:
        raise ModelRoleError("Terra bridge permissions are read/plan only")
    if kind == "TERRA_TRIAGE" and set(permissions) != {"read"}:
        raise ModelRoleError("TERRA_TRIAGE is read-only")

    duration = _bounded_int(
        bridge["max_duration_sec"], "max_duration_sec", TERRA_BRIDGE_MAX_DURATION_SEC
    )
    tool_calls = _bounded_int(
        bridge["max_tool_calls"], "max_tool_calls", TERRA_BRIDGE_MAX_TOOL_CALLS
    )
    output_tokens = _bounded_int(
        bridge["max_output_tokens"],
        "max_output_tokens",
        TERRA_BRIDGE_MAX_OUTPUT_TOKENS,
    )
    _text(bridge["handoff_reason"], "handoff_reason")
    if bridge["return_to_model"] != LUNA:
        raise ModelRoleError("Terra bridge must return to Luna")
    if _text(bridge["return_to_task_id"], "return_to_task_id") != parent_task_id:
        raise ModelRoleError("Terra bridge must return to its Luna parent")

    for field in (
        "final_verdict", "can_write", "can_git", "can_review", "can_merge",
        "can_spawn", "long_listener", "continuation", "retry_allowed",
        "control_returned",
    ):
        if _bool(bridge[field], field):
            raise ModelRoleError(f"Terra bridge {field} must be false")
    return {
        "bridge_kind": kind,
        "parent_task_id": parent_task_id,
        "parent_model": bridge["parent_model"],
        "bridge_task_id": bridge_task_id,
        "requested_model": bridge["requested_model"],
        "actual_model": bridge["actual_model"],
        "fallback_reason": "none",
        "role": role,
        "task_name": bridge["task_name"],
        "risk": risk,
        "parent_scope": list(parent_scope),
        "scope": list(scope),
        "permissions": list(permissions),
        "max_duration_sec": duration,
        "max_tool_calls": tool_calls,
        "max_output_tokens": output_tokens,
        "handoff_reason": bridge["handoff_reason"],
        "return_to_model": bridge["return_to_model"],
        "return_to_task_id": parent_task_id,
        "final_verdict": False,
        "can_write": False,
        "can_git": False,
        "can_review": False,
        "can_merge": False,
        "can_spawn": False,
        "long_listener": False,
        "continuation": False,
        "retry_allowed": False,
        "control_returned": False,
    }


def validate_terra_bridge_result(
    result: Mapping[str, Any], request: Mapping[str, Any]
) -> dict[str, Any]:
    """Require a completed bridge to return control directly to its Luna parent."""

    packet = validate_terra_bridge_request(request)
    if not isinstance(result, Mapping):
        raise ModelRoleError("Terra bridge result must be an object")
    result_fields = set(result)
    if result_fields != TERRA_BRIDGE_RESULT_FIELDS:
        missing = sorted(TERRA_BRIDGE_RESULT_FIELDS - result_fields)
        unexpected = sorted(result_fields - TERRA_BRIDGE_RESULT_FIELDS)
        details = []
        if missing:
            details.append("missing=" + ",".join(missing))
        if unexpected:
            details.append("unexpected=" + ",".join(unexpected))
        raise ModelRoleError(
            "Terra bridge result fields must match exactly (" + ";".join(details) + ")"
        )
    if _text(result["bridge_task_id"], "bridge_task_id") != packet["bridge_task_id"]:
        raise ModelRoleError("Terra bridge result task mismatch")
    if _text(result["parent_task_id"], "parent_task_id") != packet["parent_task_id"]:
        raise ModelRoleError("Terra bridge result parent mismatch")
    if result["actual_model"] != packet["actual_model"]:
        raise ModelRoleError("Terra bridge result actual_model must match the request exactly")
    if _text(result["status"], "status").lower() != "complete":
        raise ModelRoleError("Terra bridge result must be complete before handoff")
    if result["return_to_model"] != packet["return_to_model"]:
        raise ModelRoleError("Terra bridge result must return to Luna")
    if _text(result["return_to_task_id"], "return_to_task_id") != packet["parent_task_id"]:
        raise ModelRoleError("Terra bridge result return target mismatch")
    if not _bool(result["control_returned"], "control_returned"):
        raise ModelRoleError("Terra bridge must return control")
    for field in (
        "final_verdict", "can_write", "can_git", "can_review", "can_merge",
        "can_spawn", "spawned_children", "long_listener",
    ):
        if _bool(result[field], field):
            raise ModelRoleError(f"Terra bridge result {field} must be false")
    if type(result["retry_used"]) is not int or result["retry_used"] != 0:
        raise ModelRoleError("Terra bridge retry_used must be zero")
    elapsed_sec = _nonnegative_number(result["elapsed_sec"], "elapsed_sec")
    tool_calls = _nonnegative_int(result["tool_calls"], "tool_calls")
    output_tokens = _nonnegative_int(result["output_tokens"], "output_tokens")
    if elapsed_sec > packet["max_duration_sec"]:
        raise ModelRoleError("Terra bridge elapsed_sec exceeds max_duration_sec")
    if tool_calls > packet["max_tool_calls"]:
        raise ModelRoleError("Terra bridge tool_calls exceeds max_tool_calls")
    if output_tokens > packet["max_output_tokens"]:
        raise ModelRoleError("Terra bridge output_tokens exceeds max_output_tokens")
    return {
        "bridge_task_id": packet["bridge_task_id"],
        "parent_task_id": packet["parent_task_id"],
        "actual_model": result["actual_model"],
        "status": "complete",
        "return_to_model": result["return_to_model"],
        "return_to_task_id": packet["parent_task_id"],
        "control_returned": True,
        "final_verdict": False,
        "can_write": False,
        "can_git": False,
        "can_review": False,
        "can_merge": False,
        "can_spawn": False,
        "spawned_children": False,
        "retry_used": 0,
        "long_listener": False,
        "elapsed_sec": elapsed_sec,
        "tool_calls": tool_calls,
        "output_tokens": output_tokens,
    }


CODE_MISSION_TOOL_INDEX_POLICY = {
    "schema": CODE_MISSION_TOOL_INDEX_POLICY_SCHEMA,
    "evidence_schema": CODE_MISSION_EVIDENCE_SCHEMA,
    "evidence_binding": "repo_root/head/tree/worktree/index hashes",
    "large_code_requires": (
        "exact repo/worktree/revision identity plus healthy revision-matching "
        "Semble and CodeGraph"
    ),
    "semantic_before_development": "Semble semantic/similar discovery",
    "structural_before_candidate": "CodeGraph structural/blast evidence",
    "n_a_kinds": ["non_code", "exact_mechanical"],
    "n_a_requires_reason": True,
    "repair_owner": "Luna",
    "dependent_claim_only_blocked": True,
    "quota_enforced": False,
}


def _validate_code_mission_evidence(
    evidence: Any,
    *,
    field: str,
    kind: str,
    ref_prefix: str,
    policy: Mapping[str, Any],
    index_field: str,
) -> dict[str, Any]:
    if not isinstance(evidence, Mapping):
        raise ModelRoleError(f"{field} must be an evidence object")
    fields = set(evidence)
    if fields != CODE_MISSION_EVIDENCE_FIELDS:
        missing = sorted(CODE_MISSION_EVIDENCE_FIELDS - fields)
        unexpected = sorted(fields - CODE_MISSION_EVIDENCE_FIELDS)
        details = []
        if missing:
            details.append("missing=" + ",".join(missing))
        if unexpected:
            details.append("unexpected=" + ",".join(unexpected))
        raise ModelRoleError(
            f"{field} fields must match exactly (" + ";".join(details) + ")"
        )
    if evidence["schema"] != CODE_MISSION_EVIDENCE_SCHEMA:
        raise ModelRoleError(f"{field} schema is unsupported")
    if evidence["kind"] != kind:
        raise ModelRoleError(f"{field} kind must be {kind}")
    ref = evidence["ref"]
    if not isinstance(ref, str) or re.fullmatch(
        rf"{re.escape(ref_prefix)}/[0-9a-f]{{64}}", ref
    ) is None:
        raise ModelRoleError(f"{field} ref must use its canonical privacy-safe form")
    receipt_sha256 = _required_hash(evidence["receipt_sha256"], f"{field}.receipt_sha256", 64)
    query_sha256 = _required_hash(evidence["query_sha256"], f"{field}.query_sha256", 64)
    if ref.rsplit("/", 1)[-1] != receipt_sha256:
        raise ModelRoleError(f"{field} ref must bind receipt_sha256")
    identity_lengths = {
        "repo_root_sha256": 64,
        "git_head_sha": 40,
        "git_tree_sha": 40,
        "worktree_sha256": 64,
        "index_sha256": 64,
    }
    normalized = dict(evidence)
    for identity_field, length in identity_lengths.items():
        value = _required_hash(evidence[identity_field], f"{field}.{identity_field}", length)
        expected = policy[index_field] if identity_field == "index_sha256" else policy[identity_field]
        if value != expected:
            raise ModelRoleError(f"{field}.{identity_field} does not match frozen policy identity")
        normalized[identity_field] = value
    normalized["receipt_sha256"] = receipt_sha256
    normalized["query_sha256"] = query_sha256
    normalized["ref"] = ref
    return normalized


def validate_code_mission_tool_policy(
    policy: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate the bounded Semble/CodeGraph contract for code missions.

    Large-code packets carry opaque identity hashes rather than paths.  A
    pure non-code or exact-mechanical packet may explicitly mark the routes
    not applicable, but only with a reason.  This is evidence policy, not a
    per-turn or per-call quota.
    """

    if not isinstance(policy, Mapping):
        raise ModelRoleError("code mission tool policy must be an object")
    fields = set(policy)
    if fields != CODE_MISSION_TOOL_INDEX_POLICY_FIELDS:
        missing = sorted(CODE_MISSION_TOOL_INDEX_POLICY_FIELDS - fields)
        unexpected = sorted(fields - CODE_MISSION_TOOL_INDEX_POLICY_FIELDS)
        details = []
        if missing:
            details.append("missing=" + ",".join(missing))
        if unexpected:
            details.append("unexpected=" + ",".join(unexpected))
        raise ModelRoleError(
            "code mission tool policy fields must match exactly ("
            + ";".join(details)
            + ")"
        )

    if policy["schema"] != CODE_MISSION_TOOL_INDEX_POLICY_SCHEMA:
        raise ModelRoleError("code mission tool policy schema is unsupported")
    mission_kind = _text(policy["mission_kind"], "mission_kind").lower()
    if mission_kind not in {"large_code", "non_code", "exact_mechanical"}:
        raise ModelRoleError("mission_kind must be large_code, non_code, or exact_mechanical")
    repository_work = _bool(policy["repository_work"], "repository_work")
    for field, length in (
        ("repo_root_sha256", 64),
        ("worktree_sha256", 64),
        ("codegraph_index_sha256", 64),
        ("semble_index_sha256", 64),
        ("git_head_sha", 40),
        ("git_tree_sha", 40),
    ):
        _optional_hash(policy[field], field, length)
    codegraph_health = _text(policy["codegraph_health"], "codegraph_health").upper()
    semble_health = _text(policy["semble_health"], "semble_health").upper()
    if codegraph_health not in CODE_MISSION_TOOL_HEALTH_STATES:
        raise ModelRoleError("codegraph_health is not a recognized health state")
    if semble_health not in CODE_MISSION_TOOL_HEALTH_STATES:
        raise ModelRoleError("semble_health is not a recognized health state")
    repair_state = _text(policy["repair_state"], "repair_state").upper()
    if repair_state not in CODE_MISSION_TOOL_HEALTH_STATES:
        raise ModelRoleError("repair_state is not a recognized health state")
    semantic_discovery = _bool(
        policy["semble_semantic_discovery"], "semble_semantic_discovery"
    )
    structural_evidence = _bool(
        policy["codegraph_structural_evidence"], "codegraph_structural_evidence"
    )
    candidate_ready = _bool(policy["candidate_ready"], "candidate_ready")
    dependent_claim_blocked = _bool(
        policy["dependent_claim_blocked"], "dependent_claim_blocked"
    )
    semantic_evidence = policy["semble_semantic_evidence_ref"]
    structural_evidence_ref = policy["codegraph_structural_evidence_ref"]
    if semantic_discovery:
        semantic_evidence = _validate_code_mission_evidence(
            semantic_evidence,
            field="semble_semantic_evidence_ref",
            kind="semantic_discovery",
            ref_prefix="semble://evidence",
            policy=policy,
            index_field="semble_index_sha256",
        )
    elif semantic_evidence is not None:
        raise ModelRoleError(
            "Semble semantic discovery evidence cannot be supplied when discovery is false"
        )
    if structural_evidence:
        structural_evidence_ref = _validate_code_mission_evidence(
            structural_evidence_ref,
            field="codegraph_structural_evidence_ref",
            kind="structural_blast",
            ref_prefix="codegraph://evidence",
            policy=policy,
            index_field="codegraph_index_sha256",
        )
    elif structural_evidence_ref is not None:
        raise ModelRoleError(
            "CodeGraph structural/blast evidence cannot be supplied when evidence is false"
        )
    if _bool(policy["quota_enforced"], "quota_enforced"):
        raise ModelRoleError("code mission tool policy cannot impose a per-turn/count quota")

    if mission_kind == "large_code":
        if not repository_work:
            raise ModelRoleError("large_code missions require repository_work")
        for field in (
            "repo_root_sha256",
            "git_head_sha",
            "git_tree_sha",
            "worktree_sha256",
            "codegraph_index_sha256",
            "semble_index_sha256",
        ):
            if policy[field] is None:
                raise ModelRoleError(f"large_code requires exact {field}")
        if codegraph_health == "N/A" or semble_health == "N/A":
            raise ModelRoleError("large_code tool health cannot be N/A")
        if "DEGRADED" in {codegraph_health, semble_health}:
            raise ModelRoleError("required large_code tools cannot be marked DEGRADED")
        if not semantic_discovery:
            raise ModelRoleError("Semble semantic/similar discovery is required before development")
        if candidate_ready and (
            not structural_evidence
            or codegraph_health != "HEALTHY"
            or semble_health != "HEALTHY"
            or repair_state != "HEALTHY"
            or dependent_claim_blocked
        ):
            raise ModelRoleError(
                "candidate_ready requires healthy tools/repair, unblocked state, and CodeGraph evidence"
            )
        if _text(policy["repair_owner"], "repair_owner") != "Luna":
            raise ModelRoleError("Luna owns autonomous tool/index repair")
        if repair_state == "N/A":
            raise ModelRoleError("large_code repair_state cannot be N/A")
        if repair_state != "HEALTHY" or codegraph_health != "HEALTHY" or semble_health != "HEALTHY":
            if not dependent_claim_blocked:
                raise ModelRoleError(
                    "only the dependent claim may be blocked while required tool/index health is not healthy"
                )
        if candidate_ready and (semantic_evidence is None or structural_evidence_ref is None):
            raise ModelRoleError("candidate_ready requires bound Semble and CodeGraph evidence")
        n_a_reason = policy["n_a_reason"]
        if n_a_reason is not None:
            raise ModelRoleError("large_code cannot carry an N/A reason")
    else:
        if mission_kind == "non_code" and repository_work:
            raise ModelRoleError("non_code missions cannot claim repository_work")
        n_a_reason = _text(policy["n_a_reason"], "n_a_reason")
        if any(
            policy[field] is not None
            for field in (
                "repo_root_sha256",
                "git_head_sha",
                "git_tree_sha",
                "worktree_sha256",
                "codegraph_index_sha256",
                "semble_index_sha256",
            )
        ):
            raise ModelRoleError("N/A tool routes must not carry repository or index identity")
        if codegraph_health != "N/A" or semble_health != "N/A":
            raise ModelRoleError("N/A tool routes must use N/A health")
        if semantic_discovery or structural_evidence or candidate_ready:
            raise ModelRoleError("N/A tool routes cannot claim discovery or candidate evidence")
        if semantic_evidence is not None or structural_evidence_ref is not None:
            raise ModelRoleError("N/A tool routes cannot carry evidence refs")
        if _text(policy["repair_owner"], "repair_owner").lower() != "none":
            raise ModelRoleError("N/A tool routes have no repair owner")
        if repair_state != "N/A":
            raise ModelRoleError("N/A tool routes must use N/A repair_state")
        if dependent_claim_blocked:
            raise ModelRoleError("N/A tool routes do not block a dependent claim")

    return {
        "mission_kind": mission_kind,
        "repository_work": repository_work,
        "repo_root_sha256": policy["repo_root_sha256"],
        "git_head_sha": policy["git_head_sha"],
        "git_tree_sha": policy["git_tree_sha"],
        "worktree_sha256": policy["worktree_sha256"],
        "codegraph_index_sha256": policy["codegraph_index_sha256"],
        "semble_index_sha256": policy["semble_index_sha256"],
        "codegraph_health": codegraph_health,
        "semble_health": semble_health,
        "semble_semantic_discovery": semantic_discovery,
        "codegraph_structural_evidence": structural_evidence,
        "semble_semantic_evidence_ref": semantic_evidence,
        "codegraph_structural_evidence_ref": structural_evidence_ref,
        "candidate_ready": candidate_ready,
        "n_a_reason": n_a_reason,
        "repair_owner": policy["repair_owner"],
        "repair_state": repair_state,
        "dependent_claim_blocked": dependent_claim_blocked,
        "quota_enforced": False,
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
        _normalized_fallback_reason(fallback_reason)
        return "terra"
    if requested == "sol":
        raise ModelRoleError("Sol is a gate/reviewer, not the universal lifecycle controller")
    if requested == "spark":
        raise ModelRoleError("Spark is not enabled by the adaptive role policy")
    if requested == "luna" and not luna_available:
        _normalized_fallback_reason(fallback_reason)
        return "terra"
    return requested


def route_execution_task(
    *,
    requested_model: str = LUNA,
    actual_model: str | None = None,
    role: str = "execution",
    task_name: str = "execution-task",
    fallback_reason: str = "none",
    strict: bool = False,
) -> dict[str, Any]:
    """Route one execution/review task with bounded context.

    Luna is the execution owner.  A Sol task is a non-execution review or
    consultation path and is never silently promoted to lifecycle execution.
    Runtime identity drift is returned as advisory metadata in adaptive mode;
    deliberate task-name family claims and explicit strict drift still block.
    """

    actual = actual_model if actual_model is not None else requested_model
    _family(actual)
    role_text = _text(role, "role").lower()
    identity = normalize_receipt_identity(
        {
            "requested_model": requested_model,
            "actual_model": actual,
            "role": role_text,
            "fallback_reason": fallback_reason,
            "task_name": task_name,
        },
        strict=strict,
    )
    review_path = bool({"review", "reviewer", "consultant"} & _role_tokens(role_text))
    if review_path:
        selected_model = SOL
        selected_role = "independent_final_reviewer" if "review" in role_text else role_text
    else:
        selected_model = LUNA
        selected_role = "execution_lead"
    return {
        "model": selected_model,
        "role": selected_role,
        "execution": not review_path,
        "requested_model": requested_model,
        "actual_model": actual,
        "identity": identity,
        "context": dict(EXECUTION_CONTEXT),
    }


def route_mission(
    risk: str,
    *,
    profile: str = "STANDARD",
    luna_available: bool = True,
    review_required: bool | None = None,
    terra_bridge: str | None = None,
    requested_model: str = LUNA,
    actual_model: str | None = None,
    task_name: str | None = None,
    role: str = "execution",
    strict_opt_in: bool = False,
) -> dict[str, Any]:
    """Return the compact lifecycle route for one mission.

    The result is declarative: a caller still owns spawning, leases, evidence,
    and review.  Luna is the normal controller/executor; Sol enters only for a
    short high-risk contract gate or independent review; Terra is an explicit
    bounded R0/R1 bridge or a separate continuity fallback only.
    """

    risk = _text(risk, "risk").upper()
    if risk not in RISK_LEVELS:
        raise ModelRoleError("risk must be one of R0, R1, R2, R3, R4")
    profile = _text(profile, "profile").upper()
    if profile not in {"QUICK", "STANDARD", "STRICT"}:
        raise ModelRoleError("profile must be QUICK, STANDARD, or STRICT")
    if profile == "STRICT" and strict_opt_in is not True:
        raise ModelRoleError("STRICT requires an explicit user opt-in")
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
    bridge_kind: str | None = None
    if terra_bridge is not None:
        bridge_kind = _text(terra_bridge, "terra_bridge").upper()
        if bridge_kind not in TERRA_BRIDGE_KINDS:
            raise ModelRoleError("terra_bridge must be TERRA_REPLAN or TERRA_TRIAGE")
        if not luna_available:
            raise ModelRoleError(
                "Terra continuity fallback is required when Luna is unavailable"
            )
        if high_risk:
            raise ModelRoleError("R2+ missions require Sol authority, not a Terra bridge")
    default_task_name = task_name
    if default_task_name is None:
        actual_family = "luna" if actual_controller == LUNA else "terra"
        role_slug = re.sub(r"[^a-z0-9]+", "-", role.lower()).strip("-") or "execution"
        default_task_name = f"{actual_family}-{role_slug}-task"
    execution_identity = route_execution_task(
        requested_model=requested_model,
        actual_model=actual_model if actual_model is not None else actual_controller,
        role=role,
        task_name=default_task_name,
        fallback_reason=fallback_reason,
        strict=profile == "STRICT",
    )
    return {
        "risk": risk,
        "profile": profile,
        "requested_controller_model": LUNA,
        "controller_model": actual_controller,
        "execution_model": actual_controller,
        "execution_context": dict(EXECUTION_CONTEXT),
        "execution_identity": execution_identity["identity"],
        "controller_role": controller_role,
        "fallback_reason": fallback_reason,
        "universal_controller": actual_controller == LUNA,
        "sol_contract_gate": high_risk,
        "sol_contract_reasoning": SOL_CONTRACT_REASONING if high_risk else None,
        "sol_contract_max_output_tokens": SOL_CONTRACT_MAX_OUTPUT_TOKENS if high_risk else None,
        "sol_research_interpretation": risk == "R4",
        "sol_inner_loop_required": False,
        "review": {
            "required": review_required,
            "model": SOL,
            "reasoning_effort": SOL_REVIEWER_REASONING,
            "max_output_tokens": SOL_REVIEWER_MAX_OUTPUT_TOKENS,
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
        "terra_bridge": {
            "enabled": bridge_kind is not None,
            "selected": bridge_kind,
            "roles": list(TERRA_BRIDGE_KINDS),
            "requested_model": TERRA,
            "actual_model": TERRA,
            "parent_model": LUNA,
            "return_to_model": LUNA,
            "risk": "R0/R1 only",
            "max_duration_sec": TERRA_BRIDGE_MAX_DURATION_SEC,
            "max_tool_calls": TERRA_BRIDGE_MAX_TOOL_CALLS,
            "max_output_tokens": TERRA_BRIDGE_MAX_OUTPUT_TOKENS,
            "final_verdict": False,
            "can_review": False,
            "can_merge": False,
            "can_spawn": False,
            "long_listener": False,
            "continuation": False,
            "direct_return": True,
        },
        "tool_index_policy": dict(CODE_MISSION_TOOL_INDEX_POLICY),
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
    "terra": "bounded bridge or continuity fallback; never a universal controller",
    "terra_bridge_roles": list(TERRA_BRIDGE_KINDS),
    "terra_bridge_parent": LUNA,
    "terra_bridge_return": "direct Luna parent",
    "terra_bridge_risk": "R0/R1 only",
    "terra_bridge_max_duration_sec": TERRA_BRIDGE_MAX_DURATION_SEC,
    "terra_bridge_max_tool_calls": TERRA_BRIDGE_MAX_TOOL_CALLS,
    "terra_bridge_max_output_tokens": TERRA_BRIDGE_MAX_OUTPUT_TOKENS,
    "terra_bridge_authority": (
        "advisory synthesis/triage only; no final review, Git, merge, listener, "
        "or child delegation"
    ),
    "tool_index_policy": dict(CODE_MISSION_TOOL_INDEX_POLICY),
    "spark": "legacy/explicit only; disabled by this policy",
    "nested_max_depth": MAX_NESTED_DEPTH,
    "nested_edges": ["sol->luna:mechanical", "luna->sol:consultant"],
    "no_ping_pong": True,
}

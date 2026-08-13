#!/usr/bin/env python3
"""Emit the small, portable context contract consumed by hook runners.

Routing is deliberately declarative.  A hook must not inspect a prompt or a
shell command to guess intent: the operator chooses the route from this table,
while the pre-tool policy only gates explicit tool names.
"""

from __future__ import annotations

import json
import os
import pathlib
import sys

try:  # Support both direct hook execution and package-based test discovery.
    from . import hook_receipt
    from .governance_mode import current_mode
    from .model_roles import CODE_MISSION_TOOL_INDEX_POLICY, POLICY_SUMMARY
except ImportError:  # pragma: no cover - exercised by direct script invocation.
    import hook_receipt
    from governance_mode import current_mode
    from model_roles import CODE_MISSION_TOOL_INDEX_POLICY, POLICY_SUMMARY

PACKAGE_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))

from v16.tool_runtime import (  # noqa: E402
    ToolRuntimeError,
    begin_child_turn_state,
    begin_turn_state,
)


ROUTING_GUIDANCE = {
    "discovery": "Semble",
    "semantic_gateway": "compiler-derived clangd/Pyright gateway",
    "exact_evidence": "source/Git/compiler/build/test/benchmark",
}
TOOL_PREFLIGHT_GUIDANCE = {
    "required_before_repo_work": False,
    "required_before_relying_on_semantic_or_structural_tool": True,
    "semantic_capability_required_before_relying": True,
    "semantic_block_reason": "SEMANTIC_CAPABILITY_BLOCKED",
    "semantic_recovery_max": 2,
    "schema": "semantic-gateway.v1",
    "strict_ready_status": "READY",
    "mandatory_lanes": ["discovery", "semantic_gateway", "exact_evidence"],
    "usage_schema": "semantic-gateway.v1",
    "receipt_backed_usage_required": True,
    "task_contract_schema": "tool-task-contract.v16",
    "enforcement_schema": "tool-enforcement.v16",
    "adaptive_recovery_schema": "tool-recovery.v1",
    "adaptive_recovery_default": True,
    "strict_maintenance_schema": "tool-maintenance.v16",
    "strict_maintenance_flag": "--strict-maintenance",
    "automatic_repo_index_repair": False,
    "strict_repair_budget": 0,
    "strict_repair_owner": "explicit-semantic-tools-installer",
    "recovery_policy": (
        "do not repeat no-progress strategy; continue distinct evidence-producing "
        "recovery until the required capability is usable"
    ),
}


def _bounded_context(text: str, limit: int = 1500) -> str:
    """Keep the portable context bounded while retaining decisive tail facts."""

    if len(text) <= limit:
        return text
    tail = " ... See personal V21 policy. mode=" + current_mode() + "."
    return text[: max(0, limit - len(tail))] + tail
REVIEW_RUNTIME_GUIDANCE = {
    "planner": "Sol",
    "execution_lead": "Luna",
    "spark_owner": "legacy/explicit only; disabled by current role policy",
    "terra_fallback": "continuity only when Luna is unavailable",
    "terra_bridge": (
        "TERRA_REPLAN/TERRA_TRIAGE; bounded R0/R1 advisory slice; direct Luna return"
    ),
    "initial": "one independent Sol review when required by profile",
    "initial_high": "fresh Sol xhigh",
    "delta_continuation": (
        "same reviewer and model; Sol high; "
        "delta-only; 90s soft/240s hard"
    ),
    "escalated_high": "fresh Sol xhigh",
    "formal_review_calls": 1,
    "duplicate_full_scope_reviews": 0,
}
MODEL_ROLE_GUIDANCE = dict(POLICY_SUMMARY)
TOOL_INDEX_POLICY = dict(CODE_MISSION_TOOL_INDEX_POLICY)


def build_context(
    event: str | None = None,
    model: str | None = None,
    *,
    requested_model: str | None = None,
    actual_model: str | None = None,
    role: str | None = None,
    task_name: str | None = None,
    fallback_reason: str | None = None,
) -> dict[str, object]:
    """Build deterministic hook context without carrying user input.

    ``additionalContext`` remains bounded for compatibility with the v15 hook
    contract.  ``routing`` is a machine-readable copy for consumers that do
    not parse prose.
    """

    mode = current_mode()
    guidance = (
        f"V21 PERSONAL KERNEL. mode={mode}; QUICK/STANDARD/STRICT. "
        "AGENTS.md owns durable behavior; skills and agent TOMLs own conditional detail; "
        "hooks own mechanical integrity. Sol plans/contracts/reviews; Luna executes and "
        "recovers; Spark is legacy-disabled; Terra is bounded R0/R1 triage or recorded "
        "continuity; assigned models are unrestricted technically; identity "
        "misrepresentation is rejected. Lanes: unknown/similar=Semble, known="
        "compiler-derived semantic gateway, exact=source/Git/compiler/build/test/benchmark; "
        "verify provider and repository identity. "
        "Adaptive recovery never repeats a no-progress strategy. Explicit-user STRICT uses "
        "$v19-strict-proof and --strict-maintenance."
    )
    return {
        "event": event or "SessionStart",
        "policy": "v16",
        "governance_mode": mode,
        "model": model or os.environ.get("CODEX_MODEL", "unknown"),
        "spark_supported": True,
        "routing": dict(ROUTING_GUIDANCE),
        "tool_preflight": dict(TOOL_PREFLIGHT_GUIDANCE),
        "review_runtime": dict(REVIEW_RUNTIME_GUIDANCE),
        "model_roles": dict(MODEL_ROLE_GUIDANCE),
        "tool_index_policy": dict(TOOL_INDEX_POLICY),
        "agent_identity": {
            "task_name": task_name or "unknown_task_name",
            "requested_model": requested_model or model or "unknown_requested_model",
            "actual_model": actual_model or model or "unknown_actual_model",
            "role": role or "unknown_role",
            "fallback_reason": fallback_reason or "none",
            "naming_policy": "advisory-unless-identity-misrepresented",
        },
        "additionalContext": _bounded_context(guidance),
    }


if __name__ == "__main__":
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, OSError):
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    raw_event = payload.get("hook_event_name", payload.get("event"))
    event = raw_event if isinstance(raw_event, str) else "SessionStart"
    model = payload.get("model") if isinstance(payload.get("model"), str) else None
    runtime_model = model or os.environ.get("CODEX_MODEL", "unknown")
    identity = hook_receipt.identity_kwargs(payload, runtime_model=runtime_model)
    identity_error = hook_receipt.identity_validation_error(
        payload, runtime_model=runtime_model
    )
    context = build_context(event, model, **identity)
    intake_error: str | None = None
    intake: dict[str, str | None] | None = None
    if event == "UserPromptSubmit":
        try:
            intake = begin_turn_state(
                session_id=payload.get("session_id"),
                turn_id=payload.get("turn_id"),
                prompt=payload.get("prompt"),
            )
        except (OSError, ToolRuntimeError) as exc:
            intake_error = type(exc).__name__
    elif event == "SubagentStart":
        try:
            intake = begin_child_turn_state(
                session_id=payload.get("session_id"),
                turn_id=payload.get("turn_id"),
                agent_id=payload.get("agent_id"),
            )
        except (OSError, ToolRuntimeError) as exc:
            intake_error = type(exc).__name__
    if intake is not None:
        choices = " ".join("--" + name.replace("_", "-") for name in (
            "unknown_semantic_entrypoint", "similar_implementation",
            "known_symbol_or_call", "dependency_or_blast_radius",
            "exact_text_error_config_log", "shell_output_for_model",
            "machine_exact_only",
        ))
        lineage = (
            " This child intake inherits only the parent prompt-shape hash and "
            "opaque parent intake identity; no child prompt was invented."
            if event == "SubagentStart" else ""
        )
        mode = current_mode()
        intake_context = (
            "V21 INTAKE. Adaptive default; missing optional receipts stay advisory. "
            + lineage
            + " Explicit STRICT only: after the user requests it, use $v19-strict-proof and record once: rtk python3 "
            "\"${CODEX_HOME:-$HOME/.codex}/bin/toolchain-auto.py\" "
            "--record-task-contract [--repository-work|--non-repository-task] "
            f"--task-id-sha256 {intake['task_id_sha256']} "
            f"--task-shape-sha256 {intake['task_shape_sha256']} "
            f"--intake-id-sha256 {intake['intake_id_sha256']} "
            "with applicable flags: " + choices + ". Non-repository STRICT skips "
            f"repository preflight. mode={mode}."
        )
        # The intake contract is capped at 1500 bytes; this assembled form is
        # intentionally kept whole so its decisive tail (all route flags and
        # strict non-repository preflight rule) remains intact.
        context["additionalContext"] = _bounded_context(intake_context, 900)
    receipt_value = hook_receipt.receipt(
        event,
        runtime_model,
        decision="deny" if identity_error else "allow",
        reason_code=(
            "agent_identity_misrepresentation" if identity_error
            else "session_context_emitted"
        ),
        identifiers=payload,
        task_id_sha256=intake.get("task_id_sha256") if intake else None,
        intake_id_sha256=intake.get("intake_id_sha256") if intake else None,
        parent_intake_id_sha256=(
            intake.get("parent_intake_id_sha256") if intake else None
        ),
        agent_id_sha256=intake.get("agent_id_sha256") if intake else None,
        **identity,
    )
    written = hook_receipt.write_receipt(receipt_value)
    output = {
        "continue": not identity_error,
        "hookSpecificOutput": {
            "hookEventName": event,
            "additionalContext": context["additionalContext"],
        },
    }
    if identity_error:
        output["systemMessage"] = (
            "Agent model identity validation failed; this turn is blocked."
        )
    if not written:
        output["systemMessage"] = (
            "V16 hook receipt write failed; runtime-proof acceptance is unavailable."
        )
    elif intake_error:
        output["systemMessage"] = (
            "V16 turn intake state failed (" + intake_error
            + "); repository tools will fail closed until the next turn."
        )
    print(json.dumps(output, sort_keys=True))

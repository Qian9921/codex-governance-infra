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
except ImportError:  # pragma: no cover - exercised by direct script invocation.
    import hook_receipt
    from governance_mode import current_mode

PACKAGE_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))

from v16.tool_runtime import (  # noqa: E402
    ToolRuntimeError,
    begin_child_turn_state,
    begin_turn_state,
)


ROUTING_GUIDANCE = {
    "known_structure": "CodeGraph",
    "unknown_semantic_or_similar": "Semble",
    "shell_display": "rtk",
    "exact_text_log_config": "rg",
}
TOOL_PREFLIGHT_GUIDANCE = {
    "required_before_repo_work": False,
    "required_before_relying_on_semantic_or_structural_tool": True,
    "schema": "tool-preflight.v16",
    "strict_ready_status": "ready",
    "mandatory_tools": ["codegraph", "semble", "rtk"],
    "usage_schema": "tool-usage.v16",
    "receipt_backed_usage_required": True,
    "task_contract_schema": "tool-task-contract.v16",
    "enforcement_schema": "tool-enforcement.v16",
    "maintenance_schema": "tool-maintenance.v16",
    "automatic_repo_index_repair": True,
    "repair_budget": 1,
    "repair_owner": "assigned_execution_agent:tool_maintainer",
}
REVIEW_RUNTIME_GUIDANCE = {
    "planner": "Sol",
    "execution_lead": "Luna",
    "spark_owner": "Luna",
    "terra_fallback": "only when Luna is unavailable",
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


def build_context(event: str | None = None, model: str | None = None) -> dict[str, object]:
    """Build deterministic hook context without carrying user input.

    ``additionalContext`` remains bounded for compatibility with the v15 hook
    contract.  ``routing`` is a machine-readable copy for consumers that do
    not parse prose.
    """

    mode = current_mode()
    guidance = (
        "ADAPTIVE-GOVERNANCE: freeze one outcome and choose QUICK, STANDARD, or "
        "STRICT. Sol plans and independently reviews; Luna leads execution and "
        "may delegate bounded work to Spark; Terra is fallback only when Luna is "
        "unavailable; assigned models are unrestricted technically. Before new "
        "abstractions, choose REUSE, EXTEND, or NEW after "
        "checking existing ownership. ROUTING: unknown semantics/similar code -> "
        "Semble; known structure/calls/impact -> revision-matching CodeGraph; exact "
        "text/config/error -> rg; shell display -> rtk. Verify a semantic/structural "
        "tool before relying on it and repair the exact repo once; optional tool "
        "failure must not imprison unrelated work. Evidence is affected-first; one "
        "reviewer closes stable fixes delta-only. Default response: outcome, status, "
        "decisive evidence, risk, next action; at most five short points. Current "
        f"hook mode={mode}. STRICT alone requires the full V16 receipt gate."
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
        "additionalContext": guidance[:1500],
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
    context = build_context(event, model)
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
            "ADAPTIVE TOOL INTAKE." + lineage
            + " Use Semble for unknown semantics/similar code, CodeGraph for known "
            "structure/impact, rg for exact text, and rtk for shell display. Calls must "
            "answer a real task question. Verify CodeGraph/Semble before relying on them; "
            "the execution lead may repair the exact repo once. In adaptive mode missing "
            "contracts or optional receipts are advisory and must not stop unrelated work. "
            "For an explicitly STRICT mission, record once: rtk python3 "
            "\"${CODEX_HOME:-$HOME/.codex}/bin/toolchain-auto.py\" "
            "--record-task-contract [--repository-work|--non-repository-task] "
            f"--task-id-sha256 {intake['task_id_sha256']} "
            f"--task-shape-sha256 {intake['task_shape_sha256']} "
            f"--intake-id-sha256 {intake['intake_id_sha256']} "
            "and only applicable flags from: " + choices + ". For a strict "
            "non-repository task do not run repository preflight. "
            f"Current hook mode={mode}."
        )
        context["additionalContext"] = intake_context[:1900]
    receipt_value = hook_receipt.receipt(
        event,
        model or os.environ.get("CODEX_MODEL", "unknown"),
        decision="allow",
        reason_code="session_context_emitted",
        identifiers=payload,
        task_id_sha256=intake.get("task_id_sha256") if intake else None,
        intake_id_sha256=intake.get("intake_id_sha256") if intake else None,
        parent_intake_id_sha256=(
            intake.get("parent_intake_id_sha256") if intake else None
        ),
        agent_id_sha256=intake.get("agent_id_sha256") if intake else None,
    )
    written = hook_receipt.write_receipt(receipt_value)
    output = {
        "continue": True,
        "hookSpecificOutput": {
            "hookEventName": event,
            "additionalContext": context["additionalContext"],
        },
    }
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

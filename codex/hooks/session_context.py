#!/usr/bin/env python3
"""Emit the small, portable context contract consumed by hook runners.

Routing is deliberately declarative.  A hook must not inspect a prompt or a
shell command to guess intent: the operator chooses the route from this table,
while the pre-tool policy only gates explicit tool names.
"""

from __future__ import annotations

import json
import os
import sys

try:  # Support both direct hook execution and package-based test discovery.
    from . import hook_receipt
except ImportError:  # pragma: no cover - exercised by direct script invocation.
    import hook_receipt


ROUTING_GUIDANCE = {
    "known_structure": "CodeGraph",
    "unknown_semantic_or_similar": "Semble",
    "shell_display": "rtk",
    "exact_text_log_config": "rg",
}
TOOL_PREFLIGHT_GUIDANCE = {
    "required_before_repo_work": True,
    "schema": "tool-preflight.v16",
    "strict_ready_status": "ready",
    "mandatory_tools": ["codegraph", "semble", "rtk"],
    "usage_schema": "tool-usage.v16",
    "receipt_backed_usage_required": True,
}
REVIEW_RUNTIME_GUIDANCE = {
    "initial_high": "fresh Sol xhigh",
    "delta_continuation": (
        "same reviewer and model; high-risk Sol high, low/medium Terra high; "
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

    guidance = (
        "ACTIVE-MISSION-LOCK: parent brief controls role, scope, permissions "
        "and budget; assigned models are unrestricted technically; plugin "
        "inventory informational. ROUTING: known structure/symbol/call/impact "
        "-> CodeGraph; unknown semantic or similar implementation -> Semble; "
        "shell output/display -> rtk; exact text/log/config/error -> rg. "
        "REVIEW-RUNTIME: compile review-runtime.v16; initial/escalated high "
        "risk -> fresh Sol xhigh; contract-stable delta -> same reviewer/model "
        "(high-risk Sol high; low/medium Terra high), delta-only, 90s report/"
        "240s replan; one review call, zero "
        "duplicate full-scope reviews. TOOL-PREFLIGHT: before repository work "
        "require tool-preflight.v16 status=ready for CodeGraph/Semble/rtk, "
        "bound to current repo/head/worktree/config; then require receipt-backed "
        "tool-usage.v16. Choose by task shape; do not infer intent from raw "
        "command arguments."
    )
    return {
        "event": event or "SessionStart",
        "policy": "v16",
        "model": model or os.environ.get("CODEX_MODEL", "unknown"),
        "spark_supported": True,
        "routing": dict(ROUTING_GUIDANCE),
        "tool_preflight": dict(TOOL_PREFLIGHT_GUIDANCE),
        "review_runtime": dict(REVIEW_RUNTIME_GUIDANCE),
        "additionalContext": guidance[:1200],
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
    receipt_value = hook_receipt.receipt(
        event,
        model or os.environ.get("CODEX_MODEL", "unknown"),
        decision="allow",
        reason_code="session_context_emitted",
        identifiers=payload,
    )
    context["receipt_status"] = "success" if hook_receipt.write_receipt(receipt_value) else "write_failed"
    print(json.dumps(context, sort_keys=True))

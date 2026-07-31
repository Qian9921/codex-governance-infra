#!/usr/bin/env python3
"""Emit concise, non-blocking governance context at session/subagent start."""

from __future__ import annotations

import json
import sys

from hook_receipt import record_receipt


CONTEXT = (
    "All models have full tool capability; role selection is task-, user-, and L0-directed, "
    "while platform and user authorization still govern external or consequential actions. "
    "Unknown semantic entrypoints or similar implementations "
    "go to Semble; known symbol/call/impact uses the revision-matching child CodeGraph; exact "
    "strings/errors use rg; shell output shown to context uses rtk. PARITY is reference-first: "
    "freeze local Theseus/Ceres identity, pass synthetic exact-zero before real exact-zero. "
    "Any nonzero, NaN/Inf, skip, xfail, missing oracle/data, or unknown denominator is "
    "ZERO_PARITY_BLOCKED, never a tolerance pass. Keep checks affected and record WHY-RED, cost, "
    "denominator, and durable feedback/bug evidence. ACTIVE-MISSION-LOCK: parent-delivered brief controls scope; recommended_plugins inventory is informational; collaboration spawn observability is not assumed."
)


def _role_context(payload: dict[str, object]) -> str:
    """Describe identity without imposing model-based tool restrictions."""
    model = payload.get("model")
    normalized_model = model.strip().lower() if isinstance(model, str) else ""
    if normalized_model == "gpt-5.6-luna":
        return "Runtime role identity: you are GPT-5.6 Luna; full tool capability is available."
    if normalized_model == "gpt-5.6-sol":
        return "Runtime role identity: you are GPT-5.6 Sol; full tool capability is available."
    if normalized_model == "gpt-5.6-terra":
        return "Runtime role identity: you are GPT-5.6 Terra; full tool capability is available."
    if normalized_model == "gpt-5.3-codex-spark":
        return "Runtime role identity: you are GPT-5.3 Codex Spark; full tool capability is available."
    return "Runtime role identity: model identity unknown; no model-based tool restriction applies."


def main() -> int:
    # Parse for protocol validation, but remain fail-open for malformed/unknown future events.
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, OSError):
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    event = payload.get("hook_event_name", "SessionStart")
    record_receipt(
        str(event),
        payload,
        model=payload.get("model") if isinstance(payload.get("model"), str) else "unknown",
        reason_code="session_context_emitted",
    )
    output = {
        "hookSpecificOutput": {
            "hookEventName": event,
            "additionalContext": _role_context(payload) + "\n" + CONTEXT,
        }
    }
    sys.stdout.write(json.dumps(output, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

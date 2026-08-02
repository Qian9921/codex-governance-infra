#!/usr/bin/env python3
"""Record whether a routed local tool actually completed successfully.

Only normalized tool labels, route codes, hashed lifecycle identifiers and a
success/failure reason code are persisted.  Raw commands, arguments, outputs,
paths and prompts never leave the transient hook payload.
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Mapping, Sequence
from typing import Any

try:
    from . import hook_receipt, pre_tool_use_policy
except ImportError:  # pragma: no cover - direct hook execution.
    import hook_receipt
    import pre_tool_use_policy


def tool_succeeded(response: Any) -> bool:
    """Accept only explicit supported success shapes; unknown/scalar is failure."""

    if not isinstance(response, Mapping):
        return False
    for key in ("isError", "is_error"):
        if key in response and (type(response[key]) is not bool or response[key] is True):
            return False
    status_success = False
    if "status" in response:
        status = response["status"]
        if not isinstance(status, str):
            return False
        normalized = status.lower()
        if normalized in {"error", "failed", "failure", "blocked", "denied"}:
            return False
        if normalized not in {"ok", "success", "succeeded", "complete", "completed"}:
            return False
        status_success = True
    declared_success = False
    if "success" in response:
        if type(response["success"]) is not bool or response["success"] is False:
            return False
        declared_success = True
    exit_fields = [
        response[key]
        for key in ("exit_code", "exitCode", "returncode")
        if key in response
    ]
    if any(type(value) is not int or value != 0 for value in exit_fields):
        return False
    content = response.get("content")
    content_success = (
        isinstance(content, Sequence) and not isinstance(content, (str, bytes))
    )
    return bool(exit_fields or status_success or declared_success or content_success)


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, OSError):
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    tool_name = payload.get("tool_name", payload.get("tool", ""))
    tool_input = payload.get("tool_input", payload.get("args"))
    route = pre_tool_use_policy.route_for(tool_name, tool_input)
    succeeded = tool_succeeded(payload.get("tool_response"))
    value = hook_receipt.receipt(
        "PostToolUse",
        payload.get("model", os.environ.get("CODEX_MODEL", "unknown")),
        tool=tool_name,
        decision="allow" if succeeded else "deny",
        reason_code="tool_success" if succeeded else "tool_failure",
        route_code=route,
        identifiers=payload,
    )
    written = hook_receipt.write_receipt(value)
    output = {} if written else {
        "decision": "block",
        "reason": "V16 PostToolUse receipt persistence failed; runtime proof is unavailable.",
        "systemMessage": "V16 hook receipt write failed; current-turn evidence is incomplete.",
    }
    print(json.dumps(output, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

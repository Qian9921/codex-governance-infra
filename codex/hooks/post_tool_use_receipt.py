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
    """Conservatively classify stable error/status fields in a tool response."""

    if isinstance(response, Mapping):
        if response.get("isError") is True or response.get("is_error") is True:
            return False
        for key in ("exit_code", "exitCode", "returncode"):
            value = response.get(key)
            if type(value) is int and value != 0:
                return False
        status = response.get("status")
        if isinstance(status, str) and status.lower() in {
            "error", "failed", "failure", "blocked", "denied",
        }:
            return False
        return all(tool_succeeded(value) for value in response.values())
    if isinstance(response, Sequence) and not isinstance(response, (str, bytes)):
        return all(tool_succeeded(value) for value in response)
    return True


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
        "systemMessage": "V16 hook receipt write failed; runtime-proof acceptance is unavailable."
    }
    print(json.dumps(output, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

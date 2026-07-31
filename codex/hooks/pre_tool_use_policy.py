#!/usr/bin/env python3
"""Deterministic pre-tool decision and non-invasive routing signal.

Only the normalized tool name is inspected.  In particular, ``args`` is
intentionally ignored: raw machine commands may contain words such as
``git`` without turning an otherwise legitimate ``exec_command`` into a
blanket denial.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any

try:  # Support both direct hook execution and package-based test discovery.
    from . import hook_receipt
except ImportError:  # pragma: no cover - exercised by direct script invocation.
    import hook_receipt


FORBIDDEN_CHILD = frozenset({"git", "github", "merge", "review", "approve"})
ROUTE_BY_TOOL = {
    "codegraph": "CodeGraph",
    "codegraph_explore": "CodeGraph",
    "mcp__codegraph__codegraph_explore": "CodeGraph",
    "semble": "Semble",
    "semble_search": "Semble",
    "mcp__semble__search": "Semble",
    "mcp__semble__find_related": "Semble",
    "rtk": "rtk",
    "rg": "rg",
    "ripgrep": "rg",
    "grep": "rg",
}


def _tool_key(tool: Any) -> str:
    """Normalize only a tool label; never stringify or inspect arguments."""

    return tool.strip().lower() if isinstance(tool, str) else ""


def route_for(tool: Any) -> str:
    """Return an explicit route hint, or ``unspecified`` when not known."""

    return ROUTE_BY_TOOL.get(_tool_key(tool), "unspecified")


def decide(tool: Any, args: Any = None) -> dict[str, str]:
    """Return a stable allow/deny result.

    ``args`` is accepted for hook API compatibility but has no policy effect.
    Parent authorization remains required for the explicit child-action tools
    in :data:`FORBIDDEN_CHILD`.
    """

    del args
    key = _tool_key(tool)
    route = route_for(tool)
    if key in FORBIDDEN_CHILD:
        return {
            "decision": "deny",
            "reason": "child action requires parent authorization",
            "reason_code": "child_action_requires_parent_authorization",
            "route": route,
            "route_code": route.lower(),
        }
    return {
        "decision": "allow",
        "reason": "policy-pass",
        "reason_code": "policy_pass",
        "route": route,
        "route_code": route.lower(),
    }


if __name__ == "__main__":
    try:
        x = json.load(sys.stdin)
    except (json.JSONDecodeError, OSError):
        x = {}
    if not isinstance(x, dict):
        x = {}
    tool_name = x.get("tool_name", x.get("tool", ""))
    result = decide(tool_name, x.get("args"))
    receipt_value = hook_receipt.receipt(
        "PreToolUse",
        x.get("model", os.environ.get("CODEX_MODEL", "unknown")),
        tool=tool_name,
        decision=result["decision"],
        reason_code=result["reason_code"],
        route_code=result["route_code"],
        identifiers=x,
    )
    result["receipt_status"] = "success" if hook_receipt.write_receipt(receipt_value) else "write_failed"
    print(json.dumps(result, sort_keys=True))

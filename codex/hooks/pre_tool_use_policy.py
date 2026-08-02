#!/usr/bin/env python3
"""Deterministic pre-tool decision and non-invasive routing signal.

The normalized tool name owns policy.  For a generic execution tool, a direct
``rtk <evidence-tool>`` transport is classified as the substantive evidence
route.  Only executable labels are inspected transiently; raw arguments are
never persisted and never change allow/deny.
"""

from __future__ import annotations

import json
import os
import pathlib
import shlex
import sys
from collections.abc import Mapping
from typing import Any

try:  # Support both direct hook execution and package-based test discovery.
    from . import hook_receipt
except ImportError:  # pragma: no cover - exercised by direct script invocation.
    import hook_receipt


FORBIDDEN_CHILD = frozenset({"git", "github", "merge", "review", "approve"})
ROUTE_BY_TOOL = {
    "toolchain-doctor": "preflight",
    "toolchain_doctor": "preflight",
    "tool_preflight": "preflight",
    "toolchain-auto": "maintenance",
    "toolchain_auto": "maintenance",
    "tool_maintenance": "maintenance",
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


def _direct_shell_route(tool: Any, args: Any) -> str | None:
    if _tool_key(tool) not in {
        "exec_command", "functions.exec_command", "bash", "shell",
    } or not isinstance(args, Mapping):
        return None
    command = args.get("cmd", args.get("command"))
    if not isinstance(command, str) or not command.strip():
        return None
    try:
        tokens = shlex.split(command, posix=True)
    except ValueError:
        return None
    if not tokens:
        return None
    executable = pathlib.PurePosixPath(tokens[0]).name.lower()
    if executable == "rg":
        return "rg"
    if executable != "rtk":
        return None
    if len(tokens) < 2:
        return "rtk"
    nested = pathlib.PurePosixPath(tokens[1]).name.lower()
    if nested in {"python", "python3"} and len(tokens) >= 3:
        script = pathlib.PurePosixPath(tokens[2]).name.lower()
        if script in {"toolchain-auto.py", "toolchain_auto.py"}:
            return "maintenance"
        if script in {"toolchain-doctor.py", "tool_preflight.py"}:
            return "preflight"
    return {
        "codegraph": "CodeGraph",
        "semble": "Semble",
        "rg": "rg",
    }.get(nested, "rtk")


def route_for(tool: Any, args: Any = None) -> str:
    """Return an explicit route hint, or ``unspecified`` when not known."""

    key = _tool_key(tool)
    if key.startswith("mcp__codegraph__") or key.startswith("codegraph_"):
        return "CodeGraph"
    if key.startswith("mcp__semble__") or key.startswith("semble_"):
        return "Semble"
    return ROUTE_BY_TOOL.get(key, _direct_shell_route(tool, args) or "unspecified")


def decide(tool: Any, args: Any = None) -> dict[str, str]:
    """Return a stable allow/deny result.

    ``args`` has no policy effect. It may supply only the direct executable
    route hint described by :func:`_direct_shell_route`.
    Parent authorization remains required for the explicit child-action tools
    in :data:`FORBIDDEN_CHILD`.
    """

    key = _tool_key(tool)
    route = route_for(tool, args)
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
    tool_input = x.get("tool_input", x.get("args"))
    result = decide(tool_name, tool_input)
    receipt_value = hook_receipt.receipt(
        "PreToolUse",
        x.get("model", os.environ.get("CODEX_MODEL", "unknown")),
        tool=tool_name,
        decision=result["decision"],
        reason_code=result["reason_code"],
        route_code=result["route_code"],
        identifiers=x,
    )
    written = hook_receipt.write_receipt(receipt_value)
    specific = {
        "hookEventName": "PreToolUse",
        "permissionDecision": result["decision"],
        "permissionDecisionReason": result["reason"],
    }
    output = {"hookSpecificOutput": specific}
    if not written:
        output["systemMessage"] = (
            "V16 hook receipt write failed; runtime-proof acceptance is unavailable."
        )
    print(json.dumps(output, sort_keys=True))

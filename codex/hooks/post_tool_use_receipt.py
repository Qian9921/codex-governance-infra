#!/usr/bin/env python3
"""Record whether a routed local tool actually completed successfully.

Only normalized tool labels, route codes, hashed lifecycle identifiers and a
success/failure reason code are persisted.  Raw commands, arguments, outputs,
paths and prompts never leave the transient hook payload.
"""

from __future__ import annotations

import json
import math
import os
import pathlib
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

try:
    from . import hook_receipt, pre_tool_use_policy
except ImportError:  # pragma: no cover - direct hook execution.
    import hook_receipt
    import pre_tool_use_policy

PACKAGE_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))

from v16.tool_runtime import (  # noqa: E402
    ToolRuntimeError,
    load_tool_call_intake,
    load_tool_execution_status,
)


_KNOWN_KEYS = frozenset({
    "_meta", "chunk_id", "content", "exitCode", "exit_code", "isError",
    "is_error", "original_token_count", "output", "returncode", "session_id",
    "status", "stderr", "stdout", "structuredContent", "success",
    "wall_time_seconds",
})
_EXEC_KEYS = frozenset({
    "chunk_id", "wall_time_seconds", "exit_code", "session_id",
    "original_token_count", "output",
})
_SUCCESS_STATUSES = frozenset({"ok", "success", "succeeded", "complete", "completed"})
_FAILURE_STATUSES = frozenset({"error", "failed", "failure", "blocked", "denied"})


@dataclass(frozen=True)
class ToolResponseClassification:
    succeeded: bool
    reason_code: str
    diagnostics: Mapping[str, Any]


def _json_type(value: Any) -> str:
    if value is None:
        return "null"
    if type(value) is bool:
        return "boolean"
    if type(value) is int:
        return "integer"
    if type(value) is float:
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, Mapping):
        return "object"
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return "array"
    return "object"


def _diagnostics(
    response: Any, *, envelope_type: str, shape: str, status: str,
) -> dict[str, Any]:
    mapping = response if isinstance(response, Mapping) else {}
    known_keys = sorted(key for key in mapping if key in _KNOWN_KEYS)
    return {
        "schema": "tool-response-diagnostics.v16",
        "envelope_type": envelope_type,
        "normalized_shape": shape,
        "normalized_status": status,
        "known_keys": known_keys,
        "key_types": {key: _json_type(mapping[key]) for key in known_keys},
        "unknown_key_count": len(set(mapping) - _KNOWN_KEYS),
    }


def _classified(
    response: Any, *, envelope_type: str, shape: str, status: str,
) -> ToolResponseClassification:
    reason = {
        "success": "tool_success",
        "failure": "tool_failure_explicit",
        "contradictory": "tool_failure_contradictory",
        "incomplete": "tool_failure_incomplete",
        "unknown": "tool_failure_unknown",
    }[status]
    return ToolResponseClassification(
        status == "success",
        reason,
        _diagnostics(response, envelope_type=envelope_type, shape=shape, status=status),
    )


def classify_tool_response(response: Any) -> ToolResponseClassification:
    """Normalize supported App/MCP result shapes and reject every ambiguity."""

    envelope_type = "mapping"
    normalized = response
    if isinstance(response, str):
        envelope_type = "json_string"
        try:
            normalized = json.loads(response)
        except json.JSONDecodeError:
            return _classified(
                response, envelope_type="scalar_string", shape="unknown", status="unknown"
            )
        if not isinstance(normalized, Mapping):
            return _classified(
                normalized, envelope_type=envelope_type, shape="unknown", status="unknown"
            )
    elif response is None:
        return _classified(response, envelope_type="null", shape="unknown", status="unknown")
    elif isinstance(response, Sequence) and not isinstance(response, (str, bytes)):
        return _classified(response, envelope_type="sequence", shape="unknown", status="unknown")
    elif not isinstance(response, Mapping):
        return _classified(response, envelope_type="scalar", shape="unknown", status="unknown")

    keys = set(normalized)
    if not all(isinstance(key, str) for key in keys) or keys - _KNOWN_KEYS:
        return _classified(
            normalized, envelope_type=envelope_type, shape="unknown", status="unknown"
        )

    if {"wall_time_seconds", "exit_code", "output"}.issubset(keys):
        if keys - _EXEC_KEYS:
            return _classified(
                normalized, envelope_type=envelope_type,
                shape="codex_app_exec", status="contradictory",
            )
        valid = (
            type(normalized["wall_time_seconds"]) in {int, float}
            and normalized["wall_time_seconds"] >= 0
            and math.isfinite(normalized["wall_time_seconds"])
            and type(normalized["exit_code"]) is int
            and isinstance(normalized["output"], str)
            and ("chunk_id" not in normalized or isinstance(normalized["chunk_id"], str))
            and (
                "original_token_count" not in normalized
                or (
                    type(normalized["original_token_count"]) is int
                    and normalized["original_token_count"] >= 0
                )
            )
            and (
                "session_id" not in normalized
                or normalized["session_id"] is None
                or type(normalized["session_id"]) is int
            )
        )
        if not valid:
            return _classified(
                normalized, envelope_type=envelope_type,
                shape="codex_app_exec", status="unknown",
            )
        if normalized.get("session_id") is not None:
            return _classified(
                normalized, envelope_type=envelope_type,
                shape="codex_app_exec", status="incomplete",
            )
        return _classified(
            normalized, envelope_type=envelope_type, shape="codex_app_exec",
            status="success" if normalized["exit_code"] == 0 else "failure",
        )

    failure = False
    success = False
    invalid = False
    for key in ("isError", "is_error"):
        if key in normalized:
            if type(normalized[key]) is not bool:
                invalid = True
            elif normalized[key]:
                failure = True
    if "status" in normalized:
        status = normalized["status"]
        if not isinstance(status, str):
            invalid = True
        elif status.lower() in _FAILURE_STATUSES:
            failure = True
        elif status.lower() in _SUCCESS_STATUSES:
            success = True
        else:
            invalid = True
    if "success" in normalized:
        if type(normalized["success"]) is not bool:
            invalid = True
        elif normalized["success"]:
            success = True
        else:
            failure = True
    for key in ("exit_code", "exitCode", "returncode"):
        if key in normalized:
            if type(normalized[key]) is not int:
                invalid = True
            elif normalized[key] == 0:
                success = True
            else:
                failure = True
    if normalized.get("session_id") is not None:
        failure = True
    if "content" in normalized:
        if not isinstance(normalized["content"], Sequence) or isinstance(
            normalized["content"], (str, bytes)
        ):
            invalid = True
        elif not failure:
            success = True
    shape = "mcp_result" if "content" in normalized else "declared_result"
    if invalid:
        status = "unknown"
    elif success and failure:
        status = "contradictory"
    elif failure:
        status = "failure"
    elif success:
        status = "success"
    else:
        status = "unknown"
    return _classified(
        normalized, envelope_type=envelope_type, shape=shape, status=status,
    )


def tool_succeeded(response: Any) -> bool:
    """Accept only explicit supported success shapes; unknown/scalar is failure."""

    return classify_tool_response(response).succeeded


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
    intake: Mapping[str, Any] | None = None
    try:
        intake = load_tool_call_intake(
            session_id=payload.get("session_id"),
            turn_id=payload.get("turn_id"),
            agent_id=payload.get("agent_id"),
            tool_use_id=payload.get("tool_use_id", payload.get("tool_call_id")),
        )
    except (OSError, ToolRuntimeError):
        pass
    classification = classify_tool_response(payload.get("tool_response"))
    is_repo_bash = (
        pre_tool_use_policy._bash_command(tool_name, tool_input) is not None
        and pre_tool_use_policy._repo_activity(payload, tool_name, route)
    )
    if intake is not None and is_repo_bash:
        try:
            execution = load_tool_execution_status(
                session_id=payload.get("session_id"),
                turn_id=payload.get("turn_id"),
                agent_id=payload.get("agent_id"),
                tool_use_id=payload.get("tool_use_id", payload.get("tool_call_id")),
                intake_id_sha256=intake["intake_id_sha256"],
            )
        except (OSError, ToolRuntimeError):
            classification = ToolResponseClassification(
                False,
                "tool_execution_status_unavailable",
                {
                    "schema": "tool-response-diagnostics.v16",
                    "envelope_type": "private_execution_state",
                    "normalized_shape": "pretool_wrapped_bash",
                    "normalized_status": "unknown",
                    "known_keys": [],
                    "key_types": {},
                    "unknown_key_count": 0,
                },
            )
        else:
            classification = _classified(
                execution,
                envelope_type="private_execution_state",
                shape="pretool_wrapped_bash",
                status="success" if execution["exit_code"] == 0 else "failure",
            )
    succeeded = classification.succeeded and intake is not None
    reason_code = (
        classification.reason_code
        if intake is not None or not classification.succeeded
        else "tool_activity_state_unavailable"
    )
    value = hook_receipt.receipt(
        "PostToolUse",
        payload.get("model", os.environ.get("CODEX_MODEL", "unknown")),
        tool=tool_name,
        decision="allow" if succeeded else "deny",
        reason_code=reason_code,
        route_code=route,
        identifiers=payload,
        task_id_sha256=intake.get("task_id_sha256") if intake else None,
        intake_id_sha256=intake.get("intake_id_sha256") if intake else None,
        parent_intake_id_sha256=(
            intake.get("parent_intake_id_sha256") if intake else None
        ),
        agent_id_sha256=intake.get("agent_id_sha256") if intake else None,
        response_diagnostics=classification.diagnostics,
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

#!/usr/bin/env python3
"""One-shot Stop gate bound to current-turn contract and tool-call evidence.

Applicability comes only from the validated task contract persisted against the
UserPromptSubmit intake hash.  PreToolUse records the exact expected tool-call
ids; Stop requires a successful current-snapshot PostToolUse receipt for each
one.  The second Stop opens the circuit instead of creating a retry loop.
"""

from __future__ import annotations

import hashlib
import json
import os
import pathlib
import sys
from collections.abc import Mapping
from typing import Any

try:
    from . import hook_receipt
except ImportError:  # pragma: no cover - direct hook execution.
    import hook_receipt

PACKAGE_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))

from v16.tool_runtime import (  # noqa: E402
    ToolRuntimeError,
    load_current_intake,
    load_expected_tool_calls,
    load_turn_contract,
)


ROUTE_TO_RECEIPT = {
    "semantic_discovery": "semble",
    "structural_analysis": "codegraph",
    "exact_lookup": "rg",
    "shell_context": "rtk",
}


def _receipt_directory() -> pathlib.Path:
    override = os.environ.get("CODEX_HOOK_RECEIPT_DIR")
    return pathlib.Path(override) if override else hook_receipt.DEFAULT_RECEIPT_DIR


def _current_records(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    turn = payload.get("turn_id")
    if not isinstance(turn, (str, int)) or not str(turn):
        return []
    digest = hashlib.sha256(str(turn).encode("utf-8")).hexdigest()
    root = _receipt_directory()
    records: list[dict[str, Any]] = []
    try:
        files = sorted(root.glob("*.jsonl"), key=lambda path: path.name)[-2:]
    except OSError:
        return []
    for path in files:
        try:
            metadata = path.lstat()
            if path.is_symlink() or not path.is_file() or metadata.st_uid != os.geteuid():
                continue
            for line in path.read_text(encoding="utf-8").splitlines():
                value = json.loads(line)
                if isinstance(value, dict) and value.get("turn_id_sha256") == digest:
                    records.append(value)
        except (OSError, json.JSONDecodeError):
            continue
    return records


def evaluate(payload: Mapping[str, Any]) -> dict[str, Any]:
    try:
        intake = load_current_intake(
            session_id=payload.get("session_id"), turn_id=payload.get("turn_id"),
            agent_id=payload.get("agent_id"),
        )
        expected = load_expected_tool_calls(
            session_id=payload.get("session_id"), turn_id=payload.get("turn_id"),
            agent_id=payload.get("agent_id"),
        )
    except (OSError, ToolRuntimeError):
        return {"status": "blocked", "missing": ["current_turn_activity_state"]}
    if not expected:
        return {"status": "not_applicable", "missing": []}
    try:
        contract = load_turn_contract(
            session_id=payload.get("session_id"), turn_id=payload.get("turn_id"),
            agent_id=payload.get("agent_id"),
            intake_id_sha256=intake["intake_id_sha256"],
        )
    except (OSError, ToolRuntimeError):
        return {"status": "blocked", "missing": ["validated_bound_task_contract"]}

    expected_set = set(expected)
    post_records = [
        record for record in _current_records(payload)
        if record.get("event") == "PostToolUse"
        and record.get("intake_id_sha256") == intake["intake_id_sha256"]
        and record.get("tool_call_id_sha256") in expected_set
    ]
    post_ids = {record.get("tool_call_id_sha256") for record in post_records}
    missing: list[str] = []
    absent_count = len(expected_set - post_ids)
    if absent_count:
        missing.append(f"post_tool_receipt_count:{absent_count}")
    if any(
        record.get("decision") != "allow"
        or record.get("reason_code") != "tool_success"
        for record in post_records
    ):
        missing.append("successful_post_tool_receipts")
    current_snapshot = hook_receipt.receipt(
        "Stop", payload.get("model", "unknown"), identifiers=payload
    )["snapshot_sha256"]
    if any(record.get("snapshot_sha256") != current_snapshot for record in post_records):
        missing.append("current_hook_snapshot_receipts")

    successful_routes = {
        str(record.get("route_code"))
        for record in post_records
        if record.get("decision") == "allow"
        and record.get("reason_code") == "tool_success"
        and record.get("snapshot_sha256") == current_snapshot
    }
    if not ({"preflight", "maintenance"} & successful_routes):
        missing.append("strict_tool_preflight_or_maintenance")
    for row in contract["routes"]:
        if (
            row["applicability"] == "required"
            and ROUTE_TO_RECEIPT[row["route"]] not in successful_routes
        ):
            missing.append(row["route"])
    return {
        "status": "compliant" if not missing else "blocked",
        "missing": sorted(set(missing)),
    }


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, OSError):
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    result = evaluate(payload)
    blocked = result["status"] == "blocked"
    active = payload.get("stop_hook_active") is True
    try:
        intake = load_current_intake(
            session_id=payload.get("session_id"), turn_id=payload.get("turn_id"),
            agent_id=payload.get("agent_id"),
        )
    except (OSError, ToolRuntimeError):
        intake = None
    receipt_event = (
        "SubagentStop"
        if payload.get("hook_event_name") == "SubagentStop"
        else "Stop"
    )
    value = hook_receipt.receipt(
        receipt_event,
        payload.get("model", os.environ.get("CODEX_MODEL", "unknown")),
        decision="deny" if blocked else "allow",
        reason_code=(
            "tool_enforcement_circuit_open" if blocked and active
            else "tool_enforcement_blocked" if blocked
            else "tool_enforcement_pass"
        ),
        route_code="unspecified",
        identifiers=payload,
        intake_id_sha256=intake.get("intake_id_sha256") if intake else None,
        parent_intake_id_sha256=(
            intake.get("parent_intake_id_sha256") if intake else None
        ),
        agent_id_sha256=intake.get("agent_id_sha256") if intake else None,
    )
    written = hook_receipt.write_receipt(value)
    if not written:
        blocked = True
        result = {"status": "blocked", "missing": ["stop_hook_receipt"]}
    if not blocked:
        output: dict[str, Any] = {}
    elif active:
        output = {
            "systemMessage": (
                "TOOL_ENFORCEMENT_BLOCKED after one continuation; circuit is open. "
                "Do not claim completion. Missing: " + ",".join(result["missing"])
            )
        }
    else:
        output = {
            "decision": "block",
            "reason": (
                "Complete current-turn V16 tool enforcement before stopping. Missing: "
                + ",".join(result["missing"])
                + ". Record the bound task contract, run strict preflight/one-shot "
                "maintenance, and use every applicable preferred route successfully."
            ),
        }
    if not written:
        output["systemMessage"] = (
            "V16 Stop receipt write failed; runtime-proof acceptance is unavailable."
        )
    print(json.dumps(output, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

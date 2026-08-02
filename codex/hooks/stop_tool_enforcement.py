#!/usr/bin/env python3
"""One-shot Stop gate for task applicability and successful tool use.

The semantic applicability decision remains explicit and reviewable; this hook
does not guess intent from prompts.  It mechanically checks the complete four-
route declaration in a hidden final-message marker against privacy-safe,
current-turn PostToolUse receipts.  A failing first Stop continues once.  A
second Stop never continues again, which is the dead-loop circuit breaker.
"""

from __future__ import annotations

import hashlib
import json
import os
import pathlib
import re
import sys
from collections.abc import Mapping
from typing import Any

try:
    from . import hook_receipt
except ImportError:  # pragma: no cover - direct hook execution.
    import hook_receipt


ROUTE_TO_RECEIPT = {
    "semantic_discovery": "semble",
    "structural_analysis": "codegraph",
    "exact_lookup": "rg",
    "shell_context": "rtk",
}
MARKER = re.compile(
    r"<!--\s*tool-task-contract\.v16\s+"
    r"semantic_discovery=(required|not_applicable)\s+"
    r"structural_analysis=(required|not_applicable)\s+"
    r"exact_lookup=(required|not_applicable)\s+"
    r"shell_context=(required|not_applicable)\s+"
    r"machine_exact_only=(true|false)\s*-->",
    re.IGNORECASE,
)
REPO_ACTIVITY_TOOLS = frozenset({
    "bash", "exec_command", "functions.exec_command", "apply_patch", "edit", "write",
})


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


def _inside_repo(cwd: Any) -> bool:
    if not isinstance(cwd, str) or not cwd:
        return False
    try:
        current = pathlib.Path(cwd).resolve(strict=True)
    except (OSError, RuntimeError):
        return False
    return any((parent / ".git").exists() for parent in (current, *current.parents))


def evaluate(payload: Mapping[str, Any]) -> dict[str, Any]:
    records = _current_records(payload)
    repo_activity = _inside_repo(payload.get("cwd")) and any(
        record.get("event") == "PostToolUse"
        and (
            str(record.get("tool_name", "")).lower() in REPO_ACTIVITY_TOOLS
            or record.get("route_code") in {"preflight", "maintenance", "codegraph", "semble", "rtk", "rg"}
        )
        for record in records
    )
    if not repo_activity:
        return {"status": "not_applicable", "missing": []}
    message = payload.get("last_assistant_message")
    matches = list(MARKER.finditer(message)) if isinstance(message, str) else []
    if len(matches) != 1:
        return {"status": "blocked", "missing": ["complete_tool_task_contract_marker"]}
    choices = [item.lower() for item in matches[0].groups()]
    declarations = dict(zip(ROUTE_TO_RECEIPT, choices[:4]))
    machine_exact_only = choices[4] == "true"
    successful_routes = {
        str(record.get("route_code"))
        for record in records
        if record.get("event") == "PostToolUse"
        and record.get("decision") == "allow"
        and record.get("reason_code") == "tool_success"
    }
    missing: list[str] = []
    if not ({"preflight", "maintenance"} & successful_routes):
        missing.append("strict_tool_preflight_or_maintenance")
    required_count = sum(value == "required" for value in declarations.values())
    if required_count == 0 and not machine_exact_only:
        missing.append("route_or_machine_exact_only")
    if required_count and machine_exact_only:
        missing.append("machine_exact_only_conflict")
    for route, applicability in declarations.items():
        receipt_route = ROUTE_TO_RECEIPT[route]
        if applicability == "required" and receipt_route not in successful_routes:
            missing.append(route)
    return {"status": "compliant" if not missing else "blocked", "missing": missing}


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
    value = hook_receipt.receipt(
        "Stop",
        payload.get("model", os.environ.get("CODEX_MODEL", "unknown")),
        decision="deny" if blocked else "allow",
        reason_code=(
            "tool_enforcement_circuit_open" if blocked and active
            else "tool_enforcement_blocked" if blocked
            else "tool_enforcement_pass"
        ),
        route_code="unspecified",
        identifiers=payload,
    )
    written = hook_receipt.write_receipt(value)
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
                "Complete V16 tool enforcement before stopping. Missing: "
                + ",".join(result["missing"])
                + ". Add exactly one hidden marker: <!-- tool-task-contract.v16 "
                "semantic_discovery=required|not_applicable "
                "structural_analysis=required|not_applicable "
                "exact_lookup=required|not_applicable "
                "shell_context=required|not_applicable "
                "machine_exact_only=true|false -->. Replace each choice "
                "with one value and use every required preferred tool successfully."
            ),
        }
    if not written:
        output["systemMessage"] = (
            "V16 hook receipt write failed; runtime-proof acceptance is unavailable."
        )
    print(json.dumps(output, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

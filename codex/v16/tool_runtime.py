"""Fail-closed task applicability and tool-use enforcement for V16.

The routing primitives decide which tool owns one declared intent.  This
module closes the earlier omission loophole: every repository task must first
classify the complete, finite route surface.  Required routes must then be
present in an independently validated ``tool-usage.v16`` report; routes that
are not relevant remain explicit ``not_applicable`` rows instead of ceremonial
tool calls.

The classifier consumes only structured booleans and hashes.  It never stores
the user prompt, source text, absolute paths, or tool arguments.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from typing import Any


TASK_SCHEMA = "tool-task-contract.v16"
ENFORCEMENT_SCHEMA = "tool-enforcement.v16"
ROUTES = ("semantic_discovery", "structural_analysis", "exact_lookup", "shell_context")
ROUTE_TO_TOOL = {
    "semantic_discovery": "semble",
    "structural_analysis": "codegraph",
    "exact_lookup": "rg",
    "shell_context": "rtk",
}
SIGNALS = (
    "unknown_semantic_entrypoint",
    "similar_implementation",
    "known_symbol_or_call",
    "dependency_or_blast_radius",
    "exact_text_error_config_log",
    "shell_output_for_model",
    "machine_exact_only",
)
TASK_FIELDS = frozenset({
    "schema", "task_id_sha256", "classifier_identity", "task_shape_sha256",
    "repository_work", "classification_complete", "signals", "routes",
    "required_count", "not_applicable_count", "denominator",
    "denominator_known", "contract_sha256",
})
ROUTE_FIELDS = frozenset({"route", "tool", "applicability", "reason_code"})
ENFORCEMENT_FIELDS = frozenset({
    "schema", "status", "completion_eligible", "task_contract_sha256",
    "task_id_sha256", "usage_report_sha256", "required_routes",
    "satisfied_routes", "not_applicable_routes", "counts", "denominator",
    "denominator_known", "violations",
})
COUNT_FIELDS = frozenset(
    {"total", "ran", "passed", "failed", "skipped", "xfail", "unknown"}
)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class ToolRuntimeError(ValueError):
    """Raised when task applicability or enforcement evidence is invalid."""


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _sha256(value: str | bytes) -> str:
    if isinstance(value, str):
        value = value.encode("utf-8")
    return hashlib.sha256(value).hexdigest()


def _require_sha(value: Any, field: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ToolRuntimeError(f"{field} must be a SHA-256 digest")
    return value


def _require_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 160:
        raise ToolRuntimeError(f"{field} must be bounded non-empty text")
    return value.strip()


def _required_routes(signals: Mapping[str, bool]) -> set[str]:
    required: set[str] = set()
    if signals["unknown_semantic_entrypoint"] or signals["similar_implementation"]:
        required.add("semantic_discovery")
    if signals["known_symbol_or_call"] or signals["dependency_or_blast_radius"]:
        required.add("structural_analysis")
    if signals["exact_text_error_config_log"]:
        required.add("exact_lookup")
    if signals["shell_output_for_model"]:
        required.add("shell_context")
    return required


def _reason_code(route: str, required: bool) -> str:
    stem = {
        "semantic_discovery": "SEMANTIC_DISCOVERY",
        "structural_analysis": "STRUCTURAL_ANALYSIS",
        "exact_lookup": "EXACT_LOOKUP",
        "shell_context": "SHELL_CONTEXT",
    }[route]
    return f"TASK_REQUIRES_{stem}" if required else f"{stem}_NOT_APPLICABLE"


def compile_task_contract(
    *,
    task_id_sha256: str,
    classifier_identity: str,
    task_shape_sha256: str,
    repository_work: bool,
    signals: Mapping[str, bool],
) -> dict[str, Any]:
    """Compile the complete route matrix for one privacy-safe task shape.

    The caller classifies semantic facts once.  The compiler then deterministically
    derives every route, so a caller cannot omit Semble or CodeGraph by simply
    leaving the corresponding route out of a hand-written list.
    """

    task_id = _require_sha(task_id_sha256, "task_id_sha256")
    task_shape = _require_sha(task_shape_sha256, "task_shape_sha256")
    classifier = _require_text(classifier_identity, "classifier_identity")
    if type(repository_work) is not bool:
        raise ToolRuntimeError("repository_work must be boolean")
    if not isinstance(signals, Mapping) or set(signals) != set(SIGNALS):
        raise ToolRuntimeError("signals must cover the exact task-shape denominator")
    normalized: dict[str, bool] = {}
    for name in SIGNALS:
        value = signals[name]
        if type(value) is not bool:
            raise ToolRuntimeError(f"signal {name} must be boolean")
        normalized[name] = value
    if not repository_work and any(normalized.values()):
        raise ToolRuntimeError("non-repository task cannot declare repository tool signals")
    required = _required_routes(normalized) if repository_work else set()
    if repository_work and not required and not normalized["machine_exact_only"]:
        raise ToolRuntimeError(
            "repository work must require a tool route or explicitly be machine_exact_only"
        )
    if normalized["machine_exact_only"] and required:
        raise ToolRuntimeError("machine_exact_only cannot coexist with model-context routes")
    rows = [
        {
            "route": route,
            "tool": ROUTE_TO_TOOL[route],
            "applicability": "required" if route in required else "not_applicable",
            "reason_code": _reason_code(route, route in required),
        }
        for route in ROUTES
    ]
    value: dict[str, Any] = {
        "schema": TASK_SCHEMA,
        "task_id_sha256": task_id,
        "classifier_identity": classifier,
        "task_shape_sha256": task_shape,
        "repository_work": repository_work,
        "classification_complete": True,
        "signals": normalized,
        "routes": rows,
        "required_count": len(required),
        "not_applicable_count": len(ROUTES) - len(required),
        "denominator": len(ROUTES),
        "denominator_known": True,
        "contract_sha256": "",
    }
    value["contract_sha256"] = _sha256(_canonical_json(value))
    return validate_task_contract(value)


def validate_task_contract(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != TASK_FIELDS:
        raise ToolRuntimeError("task contract fields must match the exact v16 schema")
    result = dict(value)
    if result["schema"] != TASK_SCHEMA:
        raise ToolRuntimeError("task contract schema mismatch")
    task_id = _require_sha(result["task_id_sha256"], "task_id_sha256")
    task_shape = _require_sha(result["task_shape_sha256"], "task_shape_sha256")
    classifier = _require_text(result["classifier_identity"], "classifier_identity")
    signals = result["signals"]
    if not isinstance(signals, Mapping) or set(signals) != set(SIGNALS):
        raise ToolRuntimeError("signals must cover the exact task-shape denominator")
    normalized: dict[str, bool] = {}
    for name in SIGNALS:
        if type(signals[name]) is not bool:
            raise ToolRuntimeError(f"signal {name} must be boolean")
        normalized[name] = signals[name]
    repository_work = result["repository_work"]
    if type(repository_work) is not bool:
        raise ToolRuntimeError("repository_work must be boolean")
    if not repository_work and any(normalized.values()):
        raise ToolRuntimeError("non-repository task cannot declare repository tool signals")
    required = _required_routes(normalized) if repository_work else set()
    if repository_work and not required and not normalized["machine_exact_only"]:
        raise ToolRuntimeError(
            "repository work must require a tool route or explicitly be machine_exact_only"
        )
    if normalized["machine_exact_only"] and required:
        raise ToolRuntimeError("machine_exact_only cannot coexist with model-context routes")
    rows = [
        {
            "route": route,
            "tool": ROUTE_TO_TOOL[route],
            "applicability": "required" if route in required else "not_applicable",
            "reason_code": _reason_code(route, route in required),
        }
        for route in ROUTES
    ]
    if result["classification_complete"] is not True:
        raise ToolRuntimeError("task classification must be complete")
    if result["routes"] != rows:
        raise ToolRuntimeError("task route matrix does not match structured signals")
    if (
        result["required_count"] != len(required)
        or result["not_applicable_count"] != len(ROUTES) - len(required)
        or result["denominator"] != len(ROUTES)
        or result["denominator_known"] is not True
    ):
        raise ToolRuntimeError("task route denominator mismatch")
    unsigned = dict(result)
    digest = _require_sha(unsigned.pop("contract_sha256"), "contract_sha256")
    unsigned["contract_sha256"] = ""
    if digest != _sha256(_canonical_json(unsigned)):
        raise ToolRuntimeError("task contract digest mismatch")
    result["task_id_sha256"] = task_id
    result["task_shape_sha256"] = task_shape
    result["classifier_identity"] = classifier
    result["signals"] = normalized
    return result


def build_enforcement_report(
    task_contract: Mapping[str, Any],
    usage_report: Mapping[str, Any],
) -> dict[str, Any]:
    """Require actual preferred-tool use for every applicable route.

    ``usage_report`` must already have passed its caller-bound validator.  This
    function binds its task identity and successful calls to the complete task
    applicability denominator.  A verified fallback is visible as degraded,
    never as equivalent completion.
    """

    contract = validate_task_contract(task_contract)
    report = _derive_enforcement_report(contract, usage_report)
    return validate_enforcement_report(report, contract, usage_report)


def _derive_enforcement_report(
    contract: Mapping[str, Any],
    usage_report: Mapping[str, Any],
) -> dict[str, Any]:
    """Derive the only valid enforcement report for two validated inputs."""

    if not isinstance(usage_report, Mapping) or usage_report.get("schema") != "tool-usage.v16":
        raise ToolRuntimeError("enforcement requires a validated tool-usage.v16 report")
    if usage_report.get("task_id_sha256") != contract["task_id_sha256"]:
        raise ToolRuntimeError("task contract and usage report identity mismatch")
    usage_digest = _sha256(_canonical_json(usage_report))
    successful_tools = {
        call.get("tool")
        for call in usage_report.get("calls", [])
        if isinstance(call, Mapping) and call.get("status") == "success"
    }
    fallback_tools = {
        route.get("preferred_tool")
        for route in usage_report.get("routes", [])
        if isinstance(route, Mapping) and route.get("fallback") is True
    }
    required = [row["route"] for row in contract["routes"] if row["applicability"] == "required"]
    not_applicable = [row["route"] for row in contract["routes"] if row["applicability"] == "not_applicable"]
    satisfied: list[str] = []
    violations: list[str] = []
    degraded = False
    hard_violation = False
    for route in required:
        tool = ROUTE_TO_TOOL[route]
        if tool in successful_tools:
            satisfied.append(route)
        elif tool in fallback_tools:
            violations.append(f"PREFERRED_TOOL_NOT_SUCCESSFUL:{route}")
            degraded = True
        else:
            violations.append(f"REQUIRED_ROUTE_NOT_USED:{route}")
            hard_violation = True
    if usage_report.get("routing_compliant") is not True:
        violations.append("TOOL_USAGE_NOT_ROUTING_COMPLIANT")
        hard_violation = True
    if usage_report.get("coverage_equivalent") is not True:
        degraded = True
    passed = len(satisfied) + len(not_applicable)
    failed = len(ROUTES) - passed
    status = (
        "blocked" if hard_violation
        else ("degraded" if violations or degraded else "compliant")
    )
    completion_eligible = status == "compliant" and failed == 0
    return {
        "schema": ENFORCEMENT_SCHEMA,
        "status": status,
        "completion_eligible": completion_eligible,
        "task_contract_sha256": contract["contract_sha256"],
        "task_id_sha256": contract["task_id_sha256"],
        "usage_report_sha256": usage_digest,
        "required_routes": required,
        "satisfied_routes": satisfied,
        "not_applicable_routes": not_applicable,
        "counts": {
            "total": len(ROUTES),
            "ran": len(ROUTES),
            "passed": passed,
            "failed": failed,
            "skipped": 0,
            "xfail": 0,
            "unknown": 0,
        },
        "denominator": len(ROUTES),
        "denominator_known": True,
        "violations": sorted(set(violations)),
    }


def validate_enforcement_report(
    value: Mapping[str, Any],
    task_contract: Mapping[str, Any],
    usage_report: Mapping[str, Any],
) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != ENFORCEMENT_FIELDS:
        raise ToolRuntimeError("enforcement fields must match the exact v16 schema")
    result = dict(value)
    if result["schema"] != ENFORCEMENT_SCHEMA:
        raise ToolRuntimeError("enforcement schema mismatch")
    counts = result["counts"]
    if not isinstance(counts, Mapping) or set(counts) != COUNT_FIELDS:
        raise ToolRuntimeError("enforcement counts fields mismatch")
    if any(type(item) is not int or item < 0 for item in counts.values()):
        raise ToolRuntimeError("enforcement counts must be non-negative integers")
    if counts["total"] != counts["passed"] + counts["failed"]:
        raise ToolRuntimeError("enforcement count arithmetic mismatch")
    if counts["ran"] != counts["passed"] + counts["failed"]:
        raise ToolRuntimeError("enforcement ran arithmetic mismatch")
    if any(counts[name] for name in ("skipped", "xfail", "unknown")):
        raise ToolRuntimeError("enforcement cannot skip applicability rows")
    if result["denominator"] != len(ROUTES) or result["denominator_known"] is not True:
        raise ToolRuntimeError("enforcement denominator mismatch")
    contract = validate_task_contract(task_contract)
    if result["task_contract_sha256"] != contract["contract_sha256"]:
        raise ToolRuntimeError("enforcement task contract mismatch")
    if result["task_id_sha256"] != contract["task_id_sha256"]:
        raise ToolRuntimeError("enforcement task identity mismatch")
    _require_sha(result["usage_report_sha256"], "usage_report_sha256")
    if result["usage_report_sha256"] != _sha256(_canonical_json(usage_report)):
        raise ToolRuntimeError("enforcement usage report mismatch")
    for field in ("required_routes", "satisfied_routes", "not_applicable_routes", "violations"):
        if not isinstance(result[field], list) or any(not isinstance(item, str) for item in result[field]):
            raise ToolRuntimeError(f"enforcement {field} must be a string list")
    if result["status"] not in {"compliant", "degraded", "blocked"}:
        raise ToolRuntimeError("enforcement status invalid")
    if type(result["completion_eligible"]) is not bool:
        raise ToolRuntimeError("completion_eligible must be boolean")
    expected_eligible = (
        result["status"] == "compliant"
        and not result["violations"]
        and counts["failed"] == 0
    )
    if result["completion_eligible"] != expected_eligible:
        raise ToolRuntimeError("completion eligibility mismatch")
    expected = _derive_enforcement_report(contract, usage_report)
    if result != expected:
        raise ToolRuntimeError("enforcement report does not match deterministic derivation")
    return result


__all__ = [
    "ENFORCEMENT_SCHEMA", "ROUTES", "ROUTE_TO_TOOL", "SIGNALS", "TASK_SCHEMA",
    "ToolRuntimeError", "build_enforcement_report", "compile_task_contract",
    "validate_enforcement_report", "validate_task_contract",
]

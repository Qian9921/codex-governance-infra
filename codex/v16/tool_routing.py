"""Portable tool-routing contract for the v16 productivity line.

The router is deliberately a policy primitive, not a tool launcher.  It maps a
declared inspection intent to the tool that owns that kind of evidence and
returns a serialisable decision.  It never installs a tool, builds an index, or
executes a shell command.  A fallback is valid only when the preferred tool was
actually attempted (or its unavailability was observed), failed with a reason
code, and has an evidence reference.
"""

from __future__ import annotations

import enum
import re
import shutil
from dataclasses import dataclass
from typing import Any, Mapping, Sequence


ROUTE_SCHEMA = "tool-route-decision.v16"
HEALTH_SCHEMA = "tool-health.v16"
USAGE_SCHEMA = "tool-usage.v16"
# Compatibility name for older callers that imported SCHEMA.  New producers
# and validators use the distinct schema constants above.
SCHEMA = ROUTE_SCHEMA
TOOLS = ("codegraph", "semble", "rtk", "rg")
SOURCE_INJECTED = "injected observation"
SOURCE_WHICH = "shutil.which probe"
SOURCE_SEMBLE_UNVERIFIED = "shutil.which probe; Semble MCP capability unverified"
SOURCE_MISSING = "missing observation"
ROUTE_FIELDS = frozenset(
    {
        "schema", "intent", "declared", "preferred_tool", "selected_tool",
        "decision", "status", "fallback", "attempted_preferred", "reason_code",
        "evidence_ref",
    }
)
HEALTH_FIELDS = frozenset(
    {
        "schema", "status", "probe", "tools", "checks", "counts", "denominator",
        "denominator_known", "probe_sources", "mutations",
    }
)
CHECK_FIELDS = frozenset(
    {"tool", "status", "available", "healthy", "reason_code", "evidence_ref"}
)
COUNT_FIELDS = frozenset({"total", "ran", "passed", "failed", "skipped", "xfail", "unknown"})
USAGE_FIELDS = frozenset(
    {
        "schema", "status", "routing_compliant", "coverage_equivalent",
        "preflight_cache_key_sha256", "routes", "calls", "counts",
        "denominator", "denominator_known", "violations",
    }
)
CALL_FIELDS = frozenset(
    {"intent", "tool", "status", "evidence_ref", "receipt_sha256", "used_for"}
)


class RoutingError(ValueError):
    """Raised when a routing request or observation violates the contract."""


class Intent(str, enum.Enum):
    """Canonical inspection intents and their preferred evidence tools."""

    KNOWN_SYMBOL = "known_symbol"
    KNOWN_CALL = "known_call"
    BLAST_RADIUS = "blast_radius"
    SEMANTIC_ENTRY = "semantic_entry"
    SIMILAR_IMPLEMENTATION = "similar_implementation"
    SHELL_OUTPUT = "shell_output"
    EXACT_STRING = "exact_string"
    EXACT_ERROR = "exact_error"
    CONFIG = "config"
    LOG = "log"


INTENT_ALIASES = {
    # Structural questions are CodeGraph questions.
    "symbol": Intent.KNOWN_SYMBOL.value,
    "known-symbol": Intent.KNOWN_SYMBOL.value,
    "call": Intent.KNOWN_CALL.value,
    "known-call": Intent.KNOWN_CALL.value,
    "blast-radius": Intent.BLAST_RADIUS.value,
    "impact": Intent.BLAST_RADIUS.value,
    # Discovery/fuzzy questions are Semble questions.
    "semantic-entry": Intent.SEMANTIC_ENTRY.value,
    "unknown_entry": Intent.SEMANTIC_ENTRY.value,
    "unknown-entry": Intent.SEMANTIC_ENTRY.value,
    "similar": Intent.SIMILAR_IMPLEMENTATION.value,
    "similar-implementation": Intent.SIMILAR_IMPLEMENTATION.value,
    # Output and exact text have different ownership.
    "shell-output": Intent.SHELL_OUTPUT.value,
    "shell": Intent.SHELL_OUTPUT.value,
    "exact-string": Intent.EXACT_STRING.value,
    "string": Intent.EXACT_STRING.value,
    "error": Intent.EXACT_ERROR.value,
    "exact-error": Intent.EXACT_ERROR.value,
    "configuration": Intent.CONFIG.value,
    "logs": Intent.LOG.value,
}

PREFERRED_TOOL = {
    Intent.KNOWN_SYMBOL.value: "codegraph",
    Intent.KNOWN_CALL.value: "codegraph",
    Intent.BLAST_RADIUS.value: "codegraph",
    Intent.SEMANTIC_ENTRY.value: "semble",
    Intent.SIMILAR_IMPLEMENTATION.value: "semble",
    Intent.SHELL_OUTPUT.value: "rtk",
    Intent.EXACT_STRING.value: "rg",
    Intent.EXACT_ERROR.value: "rg",
    Intent.CONFIG.value: "rg",
    Intent.LOG.value: "rg",
}
USAGE_PURPOSE = {
    Intent.KNOWN_SYMBOL.value: "structure",
    Intent.KNOWN_CALL.value: "structure",
    Intent.BLAST_RADIUS.value: "structure",
    Intent.SEMANTIC_ENTRY.value: "discovery",
    Intent.SIMILAR_IMPLEMENTATION.value: "discovery",
    Intent.SHELL_OUTPUT.value: "context_display",
    Intent.EXACT_STRING.value: "literal",
    Intent.EXACT_ERROR.value: "literal",
    Intent.CONFIG.value: "literal",
    Intent.LOG.value: "literal",
}

# These are deliberately conservative.  rg can confirm text when either
# structural/semantic index is unavailable, while plain shell output is the
# last-resort representation if rtk is unavailable.  A fallback never claims
# equivalent structural or semantic coverage; the decision records that it is
# a fallback so the caller can downgrade the evidence.
FALLBACK_TOOL = {
    "codegraph": "rg",
    "semble": "rg",
    "rtk": "shell",
    "rg": "shell",
}


@dataclass(frozen=True)
class ToolObservation:
    """Read-only observation of one tool's availability/health."""

    tool: str
    available: bool
    healthy: bool = True
    evidence_ref: str | None = None
    reason_code: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.tool, str) or self.tool not in TOOLS:
            raise RoutingError(f"unknown tool: {self.tool!r}")
        if type(self.available) is not bool or type(self.healthy) is not bool:
            raise RoutingError("tool availability and health must be booleans")
        if self.evidence_ref is not None and (
            not isinstance(self.evidence_ref, str) or not self.evidence_ref.strip()
        ):
            raise RoutingError("evidence_ref must be a non-empty string when provided")
        if self.reason_code is not None and (
            not isinstance(self.reason_code, str) or not self.reason_code.strip()
        ):
            raise RoutingError("reason_code must be a non-empty string when provided")

    @property
    def usable(self) -> bool:
        return self.available and self.healthy


def _canonical_intent(intent: str | Intent) -> str:
    if isinstance(intent, Intent):
        return intent.value
    if not isinstance(intent, str) or not intent.strip():
        raise RoutingError("intent must be a non-empty string")
    value = intent.strip().lower().replace(" ", "_")
    value = INTENT_ALIASES.get(value, value)
    if value not in PREFERRED_TOOL:
        raise RoutingError(f"unknown declared intent: {intent!r}")
    return value


def _observations(value: Mapping[str, Any] | Sequence[ToolObservation] | None) -> dict[str, ToolObservation]:
    if value is None:
        return {}
    if not isinstance(value, Mapping) and (
        isinstance(value, (str, bytes)) or not isinstance(value, Sequence)
    ):
        raise RoutingError("observations must be a mapping or sequence")
    result: dict[str, ToolObservation] = {}
    if isinstance(value, Mapping):
        items = value.items()
    else:
        items = ((getattr(item, "tool", None), item) for item in value)
    for tool, raw in items:
        if tool not in TOOLS:
            raise RoutingError(f"unknown tool observation: {tool!r}")
        if isinstance(raw, ToolObservation):
            observation = raw
            if observation.tool != tool:
                raise RoutingError("observation key does not match observation.tool")
        elif isinstance(raw, bool):
            observation = ToolObservation(tool, raw)
        elif isinstance(raw, Mapping):
            allowed = {"tool", "available", "healthy", "evidence_ref", "reason_code"}
            extra = set(raw) - allowed
            if extra:
                raise RoutingError(f"unknown observation field(s): {','.join(sorted(extra))}")
            observation = ToolObservation(
                tool,
                raw.get("available"),
                raw.get("healthy", True),
                raw.get("evidence_ref"),
                raw.get("reason_code"),
            )
        else:
            raise RoutingError("observations must contain ToolObservation, bool, or mapping values")
        result[tool] = observation
    return result


def _decision(
    *,
    intent: str,
    declared: bool,
    preferred: str | None,
    selected: str | None,
    decision: str,
    reason_code: str,
    attempted_preferred: bool,
    fallback: bool = False,
    evidence_ref: str | None = None,
) -> dict[str, Any]:
    return {
        "schema": ROUTE_SCHEMA,
        "intent": intent,
        "declared": declared,
        "preferred_tool": preferred,
        "selected_tool": selected,
        "decision": decision,
        "status": decision,
        "fallback": fallback,
        "attempted_preferred": attempted_preferred,
        "reason_code": reason_code,
        "evidence_ref": evidence_ref,
    }


def route_tool(
    intent: str | Intent,
    *,
    declared: bool = True,
    observations: Mapping[str, Any] | Sequence[ToolObservation] | None = None,
    attempted_preferred: bool = False,
    preferred_failed: bool = False,
    failure_reason_code: str | None = None,
    evidence_ref: str | None = None,
) -> dict[str, Any]:
    """Return a deterministic route decision without invoking any tool.

    ``declared=False`` is an explicit no-op: an intent outside a mission's
    declared scope is not treated as a hard failure.  For a declared intent,
    an unavailable preferred tool may be replaced only after a preferred-tool
    attempt/failure and a non-empty reason code plus evidence reference.
    """

    if type(declared) is not bool:
        raise RoutingError("declared must be boolean")
    if not declared:
        label = intent.value if isinstance(intent, Intent) else str(intent)
        return _decision(
            intent=label,
            declared=False,
            preferred=None,
            selected=None,
            decision="not_declared",
            reason_code="INTENT_NOT_DECLARED",
            attempted_preferred=False,
        )

    canonical = _canonical_intent(intent)
    preferred = PREFERRED_TOOL[canonical]
    observed = _observations(observations)
    state = observed.get(preferred)
    usable = state.usable if state else False
    if usable:
        return _decision(
            intent=canonical,
            declared=True,
            preferred=preferred,
            selected=preferred,
            decision="route",
            reason_code="PREFERRED_AVAILABLE",
            attempted_preferred=False,
            evidence_ref=state.evidence_ref,
        )

    # A supplied observation is evidence of unavailability; callers may also
    # explicitly record a failed attempt.  Neither can be silently inferred.
    # A structured unavailable observation is itself an explicit attempted
    # preferred-tool probe when it carries both a reason and an evidence ref.
    # Bare ``available=False`` is intentionally insufficient: callers must not
    # manufacture a fallback from an unverified guess.
    reason = failure_reason_code or (state.reason_code if state else None)
    ref = evidence_ref or (state.evidence_ref if state else None)
    attempted = bool(
        attempted_preferred
        or preferred_failed
        or (state is not None and not usable and reason and ref)
    )
    if not attempted:
        return _decision(
            intent=canonical,
            declared=True,
            preferred=preferred,
            selected=None,
            decision="blocked",
            reason_code="PREFERRED_NOT_ATTEMPTED",
            attempted_preferred=False,
        )
    if not reason or not ref:
        return _decision(
            intent=canonical,
            declared=True,
            preferred=preferred,
            selected=None,
            decision="blocked",
            reason_code="FALLBACK_EVIDENCE_REQUIRED",
            attempted_preferred=True,
            evidence_ref=ref,
        )
    fallback = FALLBACK_TOOL[preferred]
    fallback_state = observed.get(fallback) if fallback in TOOLS else None
    if fallback in TOOLS and not (fallback_state and fallback_state.usable):
        return _decision(
            intent=canonical,
            declared=True,
            preferred=preferred,
            selected=None,
            decision="blocked",
            reason_code="FALLBACK_UNAVAILABLE",
            attempted_preferred=True,
            evidence_ref=ref,
        )
    return _decision(
        intent=canonical,
        declared=True,
        preferred=preferred,
        selected=fallback,
        decision="fallback",
        reason_code=reason,
        attempted_preferred=True,
        fallback=True,
        evidence_ref=ref,
    )


def route(*args: Any, **kwargs: Any) -> dict[str, Any]:
    """Short alias for :func:`route_tool`."""

    return route_tool(*args, **kwargs)


def health_report(
    *,
    observations: Mapping[str, Any] | Sequence[ToolObservation] | None = None,
    probe: bool = False,
) -> dict[str, Any]:
    """Return a deterministic four-tool health report.

    With ``probe=True`` only ``shutil.which`` is used.  This is a read-only
    presence check; the doctor never installs packages, indexes repositories,
    writes files, or starts a service.  Injected observations are merged over
    probes, making tests deterministic and making live results auditable.
    """

    if type(probe) is not bool:
        raise RoutingError("probe must be boolean")
    injected = _observations(observations)
    observed: dict[str, ToolObservation] = {}
    probe_sources: dict[str, str] = {}
    for tool in TOOLS:
        if tool in injected:
            observed[tool] = injected[tool]
            probe_sources[tool] = SOURCE_INJECTED
        elif probe:
            path = shutil.which(tool)
            # Semble is normally provided through MCP rather than a local
            # executable.  A missing binary therefore cannot prove that the
            # capability is unhealthy; leave it unknown until an injected MCP
            # observation/config snapshot supplies evidence.
            if tool == "semble" and path is None:
                probe_sources[tool] = SOURCE_SEMBLE_UNVERIFIED
                continue
            observed[tool] = ToolObservation(
                tool,
                path is not None,
                path is not None,
                evidence_ref=f"which:{tool}" if path else f"which:{tool}:missing",
                reason_code="PRESENT" if path else "NOT_FOUND",
            )
            probe_sources[tool] = SOURCE_WHICH
        else:
            probe_sources[tool] = SOURCE_MISSING

    checks: list[dict[str, Any]] = []
    passed = failed = unknown = 0
    for tool in TOOLS:
        item = observed.get(tool)
        if item is None:
            status = "unknown"
            reason = "MCP_CAPABILITY_UNVERIFIED" if tool == "semble" and probe else "OBSERVATION_MISSING"
            unknown += 1
        elif item.usable:
            status, reason = "pass", item.reason_code or "AVAILABLE"
            passed += 1
        else:
            status, reason = "fail", item.reason_code or "UNAVAILABLE"
            failed += 1
        checks.append(
            {
                "tool": tool,
                "status": status,
                "available": item.available if item else None,
                "healthy": item.healthy if item else None,
                "reason_code": reason,
                "evidence_ref": (
                    item.evidence_ref
                    if item
                    else ("probe:semble:mcp-unverified" if tool == "semble" and probe else None)
                ),
            }
        )
    total = len(TOOLS)
    counts = {
        "total": total,
        "ran": passed + failed,
        "passed": passed,
        "failed": failed,
        "skipped": 0,
        "xfail": 0,
        "unknown": unknown,
    }
    if failed:
        status = "degraded"
    elif unknown:
        status = "unknown"
    else:
        status = "healthy"
    return {
        "schema": HEALTH_SCHEMA,
        "status": status,
        "probe": probe,
        "tools": list(TOOLS),
        "checks": checks,
        "counts": counts,
        "denominator": total,
        "denominator_known": True,
        "probe_sources": probe_sources,
        "mutations": [],
    }


def _nonempty_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RoutingError(f"{field} must be a non-empty string")
    return value


def _nullable_text(value: Any, field: str) -> None:
    if value is not None:
        _nonempty_text(value, field)


def _strict_bool(value: Any, field: str) -> bool:
    if type(value) is not bool:
        raise RoutingError(f"{field} must be boolean")
    return value


def validate_route_decision(
    value: Mapping[str, Any],
    *,
    expected_intent: str | Intent | None = None,
    observations: Mapping[str, Any] | Sequence[ToolObservation] | None = None,
) -> dict[str, Any]:
    """Validate a route decision and optionally recompute it from observations."""

    if not isinstance(value, Mapping) or set(value) != ROUTE_FIELDS:
        raise RoutingError("route decision fields must match the exact v16 schema")
    result = dict(value)
    if result["schema"] != ROUTE_SCHEMA:
        raise RoutingError("route decision schema mismatch")
    intent = _nonempty_text(result["intent"], "intent")
    declared = _strict_bool(result["declared"], "declared")
    preferred = result["preferred_tool"]
    selected = result["selected_tool"]
    for field, item in (("preferred_tool", preferred), ("selected_tool", selected)):
        if item is not None and (not isinstance(item, str) or item not in set(TOOLS) | {"shell"}):
            raise RoutingError(f"{field} is not a supported tool")
    decision = _nonempty_text(result["decision"], "decision")
    status = _nonempty_text(result["status"], "status")
    if status != decision or decision not in {"route", "fallback", "blocked", "not_declared"}:
        raise RoutingError("decision/status mismatch")
    fallback = _strict_bool(result["fallback"], "fallback")
    attempted = _strict_bool(result["attempted_preferred"], "attempted_preferred")
    reason = _nonempty_text(result["reason_code"], "reason_code")
    _nullable_text(result["evidence_ref"], "evidence_ref")

    if not declared:
        if decision != "not_declared" or preferred is not None or selected is not None or fallback or attempted:
            raise RoutingError("not_declared route has incompatible fields")
        if reason != "INTENT_NOT_DECLARED" or result["evidence_ref"] is not None:
            raise RoutingError("not_declared route reason/evidence mismatch")
    else:
        canonical = _canonical_intent(intent)
        expected_preferred = PREFERRED_TOOL[canonical]
        if preferred != expected_preferred:
            raise RoutingError("preferred tool does not match intent mapping")
        if expected_intent is not None and canonical != _canonical_intent(expected_intent):
            raise RoutingError("route intent mismatch")
        if decision == "route":
            if selected != preferred or fallback or attempted or reason != "PREFERRED_AVAILABLE":
                raise RoutingError("preferred route invariant violated")
        elif decision == "fallback":
            if selected != FALLBACK_TOOL[preferred] or not fallback or not attempted or result["evidence_ref"] is None:
                raise RoutingError("fallback route invariant violated")
        elif decision == "blocked":
            if selected is not None or fallback:
                raise RoutingError("blocked route invariant violated")
            if not attempted and reason != "PREFERRED_NOT_ATTEMPTED":
                raise RoutingError("blocked route attempt invariant violated")
            if attempted and reason == "PREFERRED_NOT_ATTEMPTED":
                raise RoutingError("blocked route cannot claim not-attempted after an attempt")
            if not attempted and result["evidence_ref"] is not None:
                raise RoutingError("not-attempted blocked route cannot carry evidence")
            if reason not in {"PREFERRED_NOT_ATTEMPTED", "FALLBACK_EVIDENCE_REQUIRED", "FALLBACK_UNAVAILABLE"}:
                raise RoutingError("unknown blocked route reason")
        else:
            raise RoutingError("declared route cannot be not_declared")

        if observations is not None:
            recomputed = route_tool(
                canonical,
                declared=True,
                observations=observations,
                attempted_preferred=attempted,
                preferred_failed=decision in {"fallback", "blocked"} and attempted,
                failure_reason_code=reason if attempted else None,
                evidence_ref=result["evidence_ref"],
            )
            if recomputed != result:
                raise RoutingError("route decision does not match supplied observations")
    return result


def validate_health_report(
    value: Mapping[str, Any],
    *,
    observations: Mapping[str, Any] | Sequence[ToolObservation] | None = None,
) -> dict[str, Any]:
    """Validate a health report, arithmetic, ordering, and optional source bind."""

    if not isinstance(value, Mapping) or set(value) != HEALTH_FIELDS:
        raise RoutingError("health report fields must match the exact v16 schema")
    result = dict(value)
    if result["schema"] != HEALTH_SCHEMA:
        raise RoutingError("health report schema mismatch")
    status = _nonempty_text(result["status"], "status")
    if status not in {"healthy", "degraded", "unknown"}:
        raise RoutingError("invalid health status")
    probe = _strict_bool(result["probe"], "probe")
    if result["tools"] != list(TOOLS):
        raise RoutingError("health tools must use canonical order")
    if not isinstance(result["tools"], list):
        raise RoutingError("tools must be an array")
    if _strict_bool(result["denominator_known"], "denominator_known") is not True:
        raise RoutingError("health denominator must be known")
    if type(result["denominator"]) is not int or result["denominator"] != len(TOOLS):
        raise RoutingError("health denominator mismatch")
    if not isinstance(result["checks"], list) or len(result["checks"]) != len(TOOLS):
        raise RoutingError("health checks denominator mismatch")

    pass_count = fail_count = unknown_count = 0
    for expected_tool, check in zip(TOOLS, result["checks"]):
        if not isinstance(check, Mapping) or set(check) != CHECK_FIELDS:
            raise RoutingError("health check fields must be exact")
        if check["tool"] != expected_tool:
            raise RoutingError("health check order/tool mismatch")
        check_status = _nonempty_text(check["status"], "check.status")
        if check_status not in {"pass", "fail", "unknown"}:
            raise RoutingError("invalid health check status")
        available, healthy = check["available"], check["healthy"]
        if available is not None:
            _strict_bool(available, "check.available")
        if healthy is not None:
            _strict_bool(healthy, "check.healthy")
        if check_status == "unknown":
            if available is not None or healthy is not None:
                raise RoutingError("unknown check must not claim availability")
            unknown_count += 1
        elif check_status == "pass":
            if available is not True or healthy is not True:
                raise RoutingError("pass check must be available and healthy")
            pass_count += 1
        else:
            if available is None or healthy is None or (available and healthy):
                raise RoutingError("fail check must prove non-usability")
            fail_count += 1
        _nonempty_text(check["reason_code"], "check.reason_code")
        _nullable_text(check["evidence_ref"], "check.evidence_ref")

    counts = result["counts"]
    if not isinstance(counts, Mapping) or set(counts) != COUNT_FIELDS:
        raise RoutingError("health count fields must be exact")
    for field, item in counts.items():
        if type(item) is not int or item < 0:
            raise RoutingError(f"counts.{field} must be a non-negative integer")
    if counts["total"] != counts["passed"] + counts["failed"] + counts["skipped"] + counts["xfail"] + counts["unknown"]:
        raise RoutingError("health total arithmetic mismatch")
    if counts["ran"] != counts["passed"] + counts["failed"]:
        raise RoutingError("health ran arithmetic mismatch")
    if counts["total"] != len(TOOLS) or (counts["passed"], counts["failed"], counts["unknown"]) != (pass_count, fail_count, unknown_count):
        raise RoutingError("health count/check mismatch")
    if counts["skipped"] or counts["xfail"]:
        raise RoutingError("health report cannot contain skipped/xfail checks")
    expected_status = "degraded" if fail_count else ("unknown" if unknown_count else "healthy")
    if status != expected_status:
        raise RoutingError("health status/count mismatch")

    sources = result["probe_sources"]
    if not isinstance(sources, Mapping) or set(sources) != set(TOOLS):
        raise RoutingError("probe_sources must cover every tool")
    for tool in TOOLS:
        source = _nonempty_text(sources[tool], f"probe_sources.{tool}")
        if source not in {
            SOURCE_INJECTED,
            SOURCE_WHICH,
            SOURCE_SEMBLE_UNVERIFIED,
            SOURCE_MISSING,
        }:
            raise RoutingError("unknown probe source")
        if source == SOURCE_SEMBLE_UNVERIFIED and tool != "semble":
            raise RoutingError("Semble MCP source attached to another tool")
        if source == SOURCE_MISSING and probe:
            raise RoutingError("live probe cannot report a missing observation")
        if source == SOURCE_SEMBLE_UNVERIFIED and not probe:
            raise RoutingError("MCP-unverified source requires a live probe")
    if result["mutations"] != [] or not isinstance(result["mutations"], list):
        raise RoutingError("health doctor must report an empty mutation list")

    if observations is not None:
        recomputed = health_report(observations=observations, probe=probe)
        if recomputed != result:
            raise RoutingError("health report does not match supplied observations")
    return result


def tooling_doctor(
    *,
    observations: Mapping[str, Any] | Sequence[ToolObservation] | None = None,
    probe: bool = True,
) -> dict[str, Any]:
    """Read-only alias for :func:`health_report`."""

    return health_report(observations=observations, probe=probe)


doctor = tooling_doctor
route_intent = route_tool
build_health_report = health_report
run_tooling_doctor = tooling_doctor
validate_route = validate_route_decision
validate_health = validate_health_report


class ToolRouter:
    """Small state-free wrapper useful to callers that inject observations."""

    def __init__(self, observations: Mapping[str, Any] | Sequence[ToolObservation] | None = None) -> None:
        self.observations = observations

    def route(self, intent: str | Intent, **kwargs: Any) -> dict[str, Any]:
        kwargs.setdefault("observations", self.observations)
        return route_tool(intent, **kwargs)

    def health_report(self, *, probe: bool = False) -> dict[str, Any]:
        return health_report(observations=self.observations, probe=probe)


ToolRoutingContract = ToolRouter


def build_usage_report(
    *,
    preflight_cache_key_sha256: str,
    routes: Sequence[Mapping[str, Any]],
    calls: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Bind declared route decisions to actual, receipt-backed tool use.

    A successful decision is not usage evidence.  Every declared route must
    have one successful call to the selected tool, a hook receipt hash, an
    evidence reference, and a task-relevant purpose.  Fallbacks can be routing
    compliant, but they never claim equivalent semantic or structural coverage.
    """

    if not isinstance(preflight_cache_key_sha256, str) or not re.fullmatch(
        r"[0-9a-f]{64}", preflight_cache_key_sha256
    ):
        raise RoutingError("preflight_cache_key_sha256 must be a SHA-256 digest")
    if isinstance(routes, (str, bytes)) or not isinstance(routes, Sequence):
        raise RoutingError("routes must be a sequence")
    if isinstance(calls, (str, bytes)) or not isinstance(calls, Sequence):
        raise RoutingError("calls must be a sequence")

    normalized_routes: list[dict[str, Any]] = []
    route_by_intent: dict[str, dict[str, Any]] = {}
    for raw in routes:
        route = validate_route_decision(raw)
        if route["declared"] is not True:
            raise RoutingError("usage report accepts declared routes only")
        intent = _canonical_intent(route["intent"])
        if intent in route_by_intent:
            raise RoutingError("duplicate route intent")
        route_by_intent[intent] = route
        normalized_routes.append(route)

    normalized_calls: list[dict[str, Any]] = []
    call_by_intent: dict[str, dict[str, Any]] = {}
    for raw in calls:
        if not isinstance(raw, Mapping) or set(raw) != CALL_FIELDS:
            raise RoutingError("tool call fields must match the exact v16 schema")
        call = dict(raw)
        intent = _canonical_intent(call["intent"])
        if intent in call_by_intent:
            raise RoutingError("duplicate tool call intent")
        if not isinstance(call["tool"], str) or call["tool"] not in set(TOOLS) | {"shell"}:
            raise RoutingError("tool call uses an unsupported tool")
        if call["status"] not in {"success", "failure"}:
            raise RoutingError("tool call status invalid")
        for field in ("evidence_ref", "receipt_sha256"):
            if not isinstance(call[field], str) or not call[field]:
                raise RoutingError(f"tool call {field} required")
        if re.fullmatch(r"[0-9a-f]{64}", call["receipt_sha256"]) is None:
            raise RoutingError("tool call receipt_sha256 must be a SHA-256 digest")
        if call["used_for"] != USAGE_PURPOSE[intent]:
            raise RoutingError("tool call purpose does not match intent")
        call["intent"] = intent
        call_by_intent[intent] = call
        normalized_calls.append(call)

    violations: list[str] = []
    passed = failed = 0
    coverage_equivalent = True
    for intent, route in route_by_intent.items():
        call = call_by_intent.get(intent)
        if route["decision"] == "blocked":
            violations.append(f"ROUTE_BLOCKED:{intent}")
            failed += 1
            continue
        if call is None:
            violations.append(f"ROUTE_NOT_USED:{intent}")
            failed += 1
            continue
        if call["tool"] != route["selected_tool"]:
            violations.append(f"ROUTE_TOOL_MISMATCH:{intent}")
            failed += 1
            continue
        if call["status"] != "success":
            violations.append(f"ROUTE_CALL_FAILED:{intent}")
            failed += 1
            continue
        passed += 1
        if route["fallback"]:
            coverage_equivalent = False
    for intent in call_by_intent:
        if intent not in route_by_intent:
            violations.append(f"UNDECLARED_TOOL_CALL:{intent}")
            failed += 1

    total = len(route_by_intent)
    ran = passed + sum(
        1
        for intent in route_by_intent
        if intent in call_by_intent and call_by_intent[intent]["status"] == "failure"
    )
    routing_compliant = not violations
    status = (
        "blocked"
        if violations
        else ("compliant" if coverage_equivalent else "degraded")
    )
    report = {
        "schema": USAGE_SCHEMA,
        "status": status,
        "routing_compliant": routing_compliant,
        "coverage_equivalent": coverage_equivalent and routing_compliant,
        "preflight_cache_key_sha256": preflight_cache_key_sha256,
        "routes": normalized_routes,
        "calls": normalized_calls,
        "counts": {
            "total": total,
            "ran": ran,
            "passed": passed,
            "failed": total - passed,
            "skipped": 0,
            "xfail": 0,
            "unknown": 0,
        },
        "denominator": total,
        "denominator_known": True,
        "violations": violations,
    }
    return report


def validate_usage_report(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate a usage report by recomputing it from decisions and calls."""

    if not isinstance(value, Mapping) or set(value) != USAGE_FIELDS:
        raise RoutingError("usage report fields must match the exact v16 schema")
    result = dict(value)
    if result["schema"] != USAGE_SCHEMA:
        raise RoutingError("usage report schema mismatch")
    recomputed = build_usage_report(
        preflight_cache_key_sha256=result["preflight_cache_key_sha256"],
        routes=result["routes"],
        calls=result["calls"],
    )
    if recomputed != result:
        raise RoutingError("usage report does not match routes and calls")
    return result


__all__ = [
    "FALLBACK_TOOL",
    "INTENT_ALIASES",
    "Intent",
    "PREFERRED_TOOL",
    "RoutingError",
    "ROUTE_SCHEMA",
    "HEALTH_SCHEMA",
    "USAGE_SCHEMA",
    "SCHEMA",
    "TOOL_PREFERENCE",
    "TOOLS",
    "ToolObservation",
    "ToolRouter",
    "ToolRoutingContract",
    "doctor",
    "build_health_report",
    "build_usage_report",
    "health_report",
    "validate_health_report",
    "validate_health",
    "validate_route_decision",
    "validate_route",
    "validate_usage_report",
    "route",
    "route_intent",
    "route_tool",
    "run_tooling_doctor",
    "tooling_doctor",
]

# Backwards-compatible descriptive name for consumers that want a mapping.
TOOL_PREFERENCE = PREFERRED_TOOL

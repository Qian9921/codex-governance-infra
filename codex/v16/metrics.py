"""Artifact-derived productivity metrics and policy dashboard.

Metrics are deliberately boring: every value is derived from a validated
source artifact, every denominator is explicit and non-zero, and the public
validator rejects extra or fabricated fields.
"""
from __future__ import annotations

import math
import threading
from datetime import datetime, timezone
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Mapping, Sequence

from .contracts import ContractError, _id, canonical_sha256, validate_counterexample_linkage, validate_mission
from .review_policy import resolve_review_policy


class MetricsError(ContractError):
    pass


class RoutingError(ValueError):
    pass


_BUDGET_FIELDS = (
    "max_model_calls", "max_review_calls", "max_parallel_agents",
    "max_input_tokens", "max_output_tokens", "max_total_tokens",
)


def _nonnegative_budget_int(value: Any, field: str) -> int:
    if type(value) is not int or value < 0:
        raise RoutingError(f"{field} must be a non-negative integer")
    return value


@dataclass
class BudgetLedger:
    """Hard local call/token limits; this object never performs a model call."""

    limits: Mapping[str, int]
    model_calls: int = 0
    review_calls: int = 0
    parallel_agents: int = 0
    peak_parallel_agents: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    _reservations: dict[str, tuple[int, int, int]] = field(default_factory=dict, init=False, repr=False)
    _reservation_sequence: int = field(default=0, init=False, repr=False)
    _maximum_charged_calls: int = field(default=0, init=False, repr=False)
    _lock: threading.RLock = field(default_factory=threading.RLock, init=False, repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.limits, Mapping):
            raise RoutingError("routing limits must be a mapping")
        if set(self.limits) != set(_BUDGET_FIELDS):
            raise RoutingError("exact routing budget fields required")
        checked_limits: dict[str, int] = {}
        for name in _BUDGET_FIELDS:
            checked_limits[name] = _nonnegative_budget_int(self.limits[name], name)
        self.limits = MappingProxyType(checked_limits)
        for name in ("model_calls", "review_calls", "parallel_agents", "peak_parallel_agents", "input_tokens", "output_tokens"):
            _nonnegative_budget_int(getattr(self, name), name)
        if self.review_calls > self.model_calls or self.parallel_agents > self.peak_parallel_agents:
            raise RoutingError("inconsistent initial routing usage")
        if (
            self.model_calls > self.limits["max_model_calls"]
            or self.review_calls > self.limits["max_review_calls"]
            or self.peak_parallel_agents > self.limits["max_parallel_agents"]
            or self.input_tokens > self.limits["max_input_tokens"]
            or self.output_tokens > self.limits["max_output_tokens"]
            or self.total_tokens > self.limits["max_total_tokens"]
        ):
            raise RoutingError("initial routing usage exceeds budget")

    @property
    def total_tokens(self) -> int:
        with self._lock:
            return self.input_tokens + self.output_tokens

    @property
    def reserved_input_tokens(self) -> int:
        with self._lock:
            return sum(reservation[1] for reservation in self._reservations.values())

    @property
    def reserved_output_tokens(self) -> int:
        with self._lock:
            return sum(reservation[2] for reservation in self._reservations.values())

    def reserve(self, *, is_review: bool, agents: int = 0, input_tokens: int = 0, output_tokens: int = 0) -> str:
        """Fail closed before a proposed call could exceed any hard budget.

        Token values are conservative per-call maxima. Calls are cumulative;
        concurrency and token maxima remain outstanding until ``settle`` binds
        the actual provider-reported use.
        """
        with self._lock:
            return self._reserve_unlocked(
                is_review=is_review,
                agents=agents,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )

    def _reserve_unlocked(self, *, is_review: bool, agents: int, input_tokens: int, output_tokens: int) -> str:
        if type(is_review) is not bool:
            raise RoutingError("is_review must be boolean")
        agents = _nonnegative_budget_int(agents, "agents")
        input_tokens = _nonnegative_budget_int(input_tokens, "input_tokens")
        output_tokens = _nonnegative_budget_int(output_tokens, "output_tokens")
        proposed = {
            "max_model_calls": self.model_calls + 1,
            "max_review_calls": self.review_calls + int(is_review),
            "max_parallel_agents": self.parallel_agents + agents,
            "max_input_tokens": self.input_tokens + self.reserved_input_tokens + input_tokens,
            "max_output_tokens": self.output_tokens + self.reserved_output_tokens + output_tokens,
            "max_total_tokens": self.total_tokens + self.reserved_input_tokens + self.reserved_output_tokens + input_tokens + output_tokens,
        }
        if any(proposed[field] > self.limits[field] for field in _BUDGET_FIELDS):
            raise RoutingError("routing budget exhausted")
        self.model_calls = proposed["max_model_calls"]
        self.review_calls = proposed["max_review_calls"]
        self.parallel_agents = proposed["max_parallel_agents"]
        self.peak_parallel_agents = max(self.peak_parallel_agents, self.parallel_agents)
        self._reservation_sequence += 1
        reservation_id = f"call-{self._reservation_sequence}"
        self._reservations[reservation_id] = (agents, input_tokens, output_tokens)
        return reservation_id

    def settle(self, reservation_id: str, *, input_tokens: int, output_tokens: int, counts_available: bool = True) -> None:
        """Bind one call to reported use (or reserved maxima) and release it."""
        with self._lock:
            self._settle_unlocked(
                reservation_id,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                counts_available=counts_available,
            )

    def _settle_unlocked(self, reservation_id: str, *, input_tokens: int, output_tokens: int, counts_available: bool) -> None:
        if reservation_id not in self._reservations:
            raise RoutingError("unknown or already settled reservation")
        if type(counts_available) is not bool:
            raise RoutingError("counts_available must be boolean")
        actual_input = _nonnegative_budget_int(input_tokens, "input_tokens")
        actual_output = _nonnegative_budget_int(output_tokens, "output_tokens")
        agents, maximum_input, maximum_output = self._reservations[reservation_id]
        if actual_input > maximum_input or actual_output > maximum_output:
            raise RoutingError("actual token use exceeds the reserved maximum")
        if not counts_available and (actual_input != maximum_input or actual_output != maximum_output):
            raise RoutingError("unavailable counts must charge the reserved maxima")
        del self._reservations[reservation_id]
        self.parallel_agents -= agents
        self.input_tokens += actual_input
        self.output_tokens += actual_output
        self._maximum_charged_calls += int(not counts_available)

    def usage(self) -> dict[str, Any]:
        """Return a privacy-safe aggregate receipt; no prompts or raw auth data."""
        with self._lock:
            return self._usage_unlocked()

    def _usage_unlocked(self) -> dict[str, Any]:
        return {
            "model_calls": self.model_calls,
            "review_calls": self.review_calls,
            "active_parallel_agents": self.parallel_agents,
            "peak_parallel_agents": self.peak_parallel_agents,
            "charged_input_tokens": self.input_tokens,
            "charged_output_tokens": self.output_tokens,
            "charged_total_tokens": self.total_tokens,
            "token_count_basis": "upper-bound-mixed" if self._maximum_charged_calls else "provider-reported",
            "maximum_charged_calls": self._maximum_charged_calls,
            "outstanding_input_tokens": self.reserved_input_tokens,
            "outstanding_output_tokens": self.reserved_output_tokens,
            "outstanding_total_tokens": self.reserved_input_tokens + self.reserved_output_tokens,
            "outstanding_calls": len(self._reservations),
            "limits": dict(self.limits),
            "usd_cost": None,
            "usd_cost_reason": "unavailable-without-provider-plan-attribution",
        }


def choose_model(*, task_kind: str, risk: str, authorized_models: Sequence[str], live_models: Mapping[str, Mapping[str, Any]], preferences: Mapping[str, Mapping[str, int]] | None = None) -> str:
    """Select an authorized live model by task/risk preference, never slug rules.

    Preferences are keyed by model. A lower live ``token_cost_rank`` wins ties,
    then the brief's authorized order. Ranks are relative current metadata,
    never invented dollar prices.
    """
    if not isinstance(task_kind, str) or not task_kind.strip() or not isinstance(risk, str) or not risk.strip():
        raise RoutingError("task kind and risk required")
    if isinstance(authorized_models, (str, bytes)) or not isinstance(authorized_models, Sequence):
        raise RoutingError("authorized models must be an ordered sequence")
    authorized = list(authorized_models)
    if not authorized or any(not isinstance(model, str) or not model.strip() for model in authorized) or len(set(authorized)) != len(authorized):
        raise RoutingError("non-empty authorized model list required")
    if not isinstance(live_models, Mapping):
        raise RoutingError("live model catalog must be a mapping")
    if preferences is not None and not isinstance(preferences, Mapping):
        raise RoutingError("model routing preferences must be a mapping")
    policy = preferences or {}
    candidates: list[tuple[int, int, int, str]] = []
    for order, model in enumerate(authorized):
        live = live_models.get(model)
        if not isinstance(live, Mapping) or live.get("available") is not True:
            continue
        supported_risks = live.get("risks", ())
        if isinstance(supported_risks, (str, bytes)) or not isinstance(supported_risks, Sequence) or any(not isinstance(item, str) or not item for item in supported_risks):
            raise RoutingError("live model risks must be a string sequence")
        if supported_risks and risk not in supported_risks:
            continue
        scores = policy.get(model, {})
        if not isinstance(scores, Mapping):
            raise RoutingError("model routing preferences must be mappings")
        score = scores.get(f"{task_kind}:{risk}", scores.get(task_kind, scores.get("default", 0)))
        if type(score) is not int:
            raise RoutingError("routing preference score must be integer")
        cost_rank = live.get("token_cost_rank", 0)
        if type(cost_rank) is not int or cost_rank < 0:
            raise RoutingError("token_cost_rank must be a non-negative integer")
        candidates.append((-score, cost_rank, order, model))
    if not candidates:
        raise RoutingError("no authorized live model for task/risk")
    return min(candidates)[3]


METRIC_NAMES = ("first_pass_approval", "pre_review_blocker_capture", "review_rounds", "full_runs_per_head", "fresh_runs_per_head", "evidence_corrections", "writer_handoffs", "spark_audit_count", "spark_audit_latency_sec", "gate_elapsed_sec", "new_blocker_admissions")
METRIC_FIELDS = {"schema", "mission_id", "source_hash", *METRIC_NAMES}


RUNTIME_METRIC_FIELDS = ("ttft_sec", "decode_sec", "tool_sec", "quality")


def collect_runtime_metrics(observed: Mapping[str, Any]) -> dict[str, float | None]:
    """Keep unavailable timing/quality explicit instead of manufacturing zero.

    This is deliberately separate from the frozen metrics.v16 acceptance
    packet: runtime telemetry is optional operational evidence, not a claimed
    acceptance result.
    """
    if not isinstance(observed, Mapping) or set(observed) != set(RUNTIME_METRIC_FIELDS):
        raise MetricsError("exact runtime metric fields required")
    result: dict[str, float | None] = {}
    for name in RUNTIME_METRIC_FIELDS:
        value = observed[name]
        if value is None:
            result[name] = None
        else:
            result[name] = _finite(value, f"$.{name}")
    return result


def _finite(value: Any, path: str, *, minimum: float = 0.0) -> float:
    if type(value) not in (int, float) or isinstance(value, bool) or not math.isfinite(float(value)) or float(value) < minimum:
        raise MetricsError("finite non-negative number required", path)
    return float(value)


def _nonneg_int(value: Any, path: str) -> int:
    if type(value) is not int or value < 0:
        raise MetricsError("non-negative integer required", path)
    return value


def _rows(evidence: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    if not isinstance(evidence, Mapping):
        raise MetricsError("evidence envelope required")
    rows = evidence.get("rows")
    if not isinstance(rows, list) or not rows:
        raise MetricsError("non-empty evidence rows required")
    if evidence.get("schema") not in (None, "evidence-envelope.v16"):
        raise MetricsError("evidence schema")
    envelope_head = evidence.get("head_sha")
    envelope_tree = evidence.get("tree_sha")
    if evidence.get("schema") == "evidence-envelope.v16":
        if evidence.get("clean") is not True or not isinstance(envelope_head, str) or len(envelope_head) != 40 or any(c not in "0123456789abcdef" for c in envelope_head.lower()) or not isinstance(envelope_tree, str) or len(envelope_tree) != 40 or any(c not in "0123456789abcdef" for c in envelope_tree.lower()):
            raise MetricsError("current clean evidence identity required")
    for index, row in enumerate(rows):
        if not isinstance(row, Mapping) or row.get("stage") not in {"targeted", "full", "fresh"} or not isinstance(row.get("actual_head"), str) or len(row["actual_head"]) != 40 or any(c not in "0123456789abcdef" for c in row["actual_head"].lower()):
            raise MetricsError("evidence row identity required", f"$.rows[{index}]")
        if evidence.get("schema") == "evidence-envelope.v16" and (row["actual_head"] != envelope_head or row.get("tree_sha") != envelope_tree):
            raise MetricsError("evidence row/envelope identity mismatch", f"$.rows[{index}]")
        if row.get("decision", "allow") not in {"allow", "reused"}:
            raise MetricsError("only current green evidence may feed metrics", f"$.rows[{index}]")
    return rows


def _validate_review(review: Mapping[str, Any]) -> None:
    if not isinstance(review, Mapping) or type(review.get("round")) is not int or review["round"] < 1:
        raise MetricsError("validated review packet and positive round required")
    if review.get("verdict") not in {None, "APPROVE", "REQUEST_CHANGES"}:
        raise MetricsError("review verdict")
    findings = review.get("findings")
    if not isinstance(findings, list):
        raise MetricsError("review findings required")


def collect_metrics(*, mission: Mapping[str, Any], evidence: Mapping[str, Any], review: Mapping[str, Any], spark_results: Sequence[Mapping[str, Any]], gate_runs: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Derive metrics solely from current, identity-bound artifacts."""
    try:
        checked_mission = validate_mission(mission)
        validate_counterexample_linkage(checked_mission)
    except Exception as exc:
        raise MetricsError("validated mission required") from exc
    rows = _rows(evidence)
    _validate_review(review)
    review_policy = resolve_review_policy(checked_mission)
    required_stages = tuple(review_policy["required_stages"])
    stage_rows = {stage: [r for r in rows if r.get("stage") == stage] for stage in ("targeted", "full", "fresh")}
    stage_heads = {stage: {r["actual_head"] for r in stage_rows[stage]} for stage in stage_rows}
    for stage in required_stages:
        if not stage_rows[stage] or not stage_heads[stage]:
            raise MetricsError(f"required {stage} evidence denominator/head must be non-zero")
    full_rows, fresh_rows = stage_rows["full"], stage_rows["fresh"]
    corrections = sum(1 for r in rows if r.get("correction_of"))
    writer_ids = {r.get("writer_task_id") for r in rows if r.get("writer_task_id")}
    if not gate_runs:
        raise MetricsError("non-empty gate run denominator required")
    gate_elapsed = 0.0
    for index, run in enumerate(gate_runs):
        if not isinstance(run, Mapping):
            raise MetricsError("gate run object required", f"$.gate_runs[{index}]")
        gate_elapsed += _finite(run.get("elapsed_sec"), f"$.gate_runs[{index}].elapsed_sec")
    if len(spark_results) != len(checked_mission["spark_audits"]) or len(spark_results) > 3:
        raise MetricsError("Spark results must match the mission's bounded audit set")
    spark_latency = 0.0
    for index, result in enumerate(spark_results):
        if not isinstance(result, Mapping):
            raise MetricsError("Spark result object required", f"$.spark_results[{index}]")
        spark_latency += _finite(result.get("elapsed_sec"), f"$.spark_results[{index}].elapsed_sec")
    findings = review["findings"]
    blockers = [f for f in findings if isinstance(f, Mapping) and (f.get("severity") == "P1" or f.get("label") == "BLOCKING")]
    admissions = sum(1 for f in findings if isinstance(f, Mapping) and f.get("attribution") in {"DELTA_INTRODUCED", "ORIGINAL_SCOPE_MISSED", "NEW_FALSIFIABLE_EVIDENCE"})
    source = {"mission": checked_mission, "evidence": dict(evidence), "review": dict(review), "spark_results": [dict(r) for r in spark_results], "gate_runs": [dict(r) for r in gate_runs]}
    metrics: dict[str, Any] = {
        "schema": "metrics.v16", "mission_id": checked_mission["mission_id"], "source_hash": canonical_sha256(source),
        "first_pass_approval": review.get("verdict") == "APPROVE" and review["round"] == 1,
        "pre_review_blocker_capture": len(blockers), "review_rounds": review["round"],
        "full_runs_per_head": None if "full" not in required_stages else len(full_rows) / len(stage_heads["full"]),
        "fresh_runs_per_head": None if "fresh" not in required_stages else len(fresh_rows) / len(stage_heads["fresh"]),
        "evidence_corrections": corrections, "writer_handoffs": max(0, len(writer_ids) - 1),
        "spark_audit_count": len(spark_results), "spark_audit_latency_sec": round(spark_latency, 6),
        "gate_elapsed_sec": round(gate_elapsed, 6), "new_blocker_admissions": admissions,
    }
    return validate_metrics(metrics, source_bundle=source)


def validate_metrics_shape(metrics: Mapping[str, Any], *, required_stages: Sequence[str] | None = None) -> dict[str, Any]:
    if not isinstance(metrics, Mapping) or set(metrics) != METRIC_FIELDS:
        raise MetricsError("exact metrics.v16 fields required")
    if metrics.get("schema") != "metrics.v16":
        raise MetricsError("metrics.v16 schema required")
    _id(metrics.get("mission_id"), "$.mission_id")
    source_hash = metrics.get("source_hash")
    if not isinstance(source_hash, str) or len(source_hash) != 64 or any(c not in "0123456789abcdef" for c in source_hash):
        raise MetricsError("source_hash must be SHA-256", "$.source_hash")
    if type(metrics.get("first_pass_approval")) is not bool:
        raise MetricsError("first_pass_approval boolean required")
    for name in ("pre_review_blocker_capture", "review_rounds", "evidence_corrections", "writer_handoffs", "spark_audit_count", "new_blocker_admissions"):
        _nonneg_int(metrics.get(name), f"$.{name}")
    if metrics["review_rounds"] < 1 or metrics["spark_audit_count"] > 3:
        raise MetricsError("review/Spark denominator contract")
    if required_stages is not None:
        if isinstance(required_stages, (str, bytes)) or not isinstance(required_stages, Sequence) or any(stage not in {"targeted", "full", "fresh"} for stage in required_stages):
            raise MetricsError("valid required stage sequence required", "$.required_stages")
        required = set(required_stages)
    else:
        # Shape-only validation has no mission policy context.  It therefore
        # accepts either a finite ratio or explicit ``None``; source-bound
        # validation below enforces which stages are actually required.
        required = set()
    for stage, name in (("full", "full_runs_per_head"), ("fresh", "fresh_runs_per_head")):
        value = metrics.get(name)
        if value is None:
            if stage in required:
                raise MetricsError("required stage metric denominator must be available", f"$.{name}")
        else:
            _finite(value, f"$.{name}")
            if required_stages is not None and stage not in required:
                raise MetricsError("non-required stage metric must be unavailable", f"$.{name}")
    for name in ("spark_audit_latency_sec", "gate_elapsed_sec"):
        _finite(metrics.get(name), f"$.{name}")
    return dict(metrics)


def validate_metrics(metrics: Mapping[str, Any], *, source_bundle: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Acceptance validator: recompute metrics from the validated source bundle."""
    if source_bundle is None:
        raise MetricsError("source bundle required for metrics acceptance")
    checked: dict[str, Any]
    source = source_bundle
    if source is not None:
        if not isinstance(source, Mapping) or set(source) != {"mission", "evidence", "review", "spark_results", "gate_runs"}:
            raise MetricsError("validated metrics source bundle required", "$.source")
        try:
            checked_mission = validate_mission(source["mission"]); validate_counterexample_linkage(checked_mission)
            rows = _rows(source["evidence"]); _validate_review(source["review"])
        except Exception as exc:
            raise MetricsError("validated metrics source bundle required", "$.source") from exc
        review_policy = resolve_review_policy(checked_mission)
        required_stages = tuple(review_policy["required_stages"])
        stage_rows = {stage: [r for r in rows if r.get("stage") == stage] for stage in ("targeted", "full", "fresh")}
        stage_heads = {stage: {r["actual_head"] for r in stage_rows[stage]} for stage in stage_rows}
        for stage in required_stages:
            if not stage_rows[stage] or not stage_heads[stage]:
                raise MetricsError(f"source required {stage} denominator/head must be non-zero", "$.source.evidence")
        full_rows, fresh_rows = stage_rows["full"], stage_rows["fresh"]
        gate_runs = source["gate_runs"]; sparks = source["spark_results"]
        if not isinstance(gate_runs, Sequence) or not gate_runs or not isinstance(sparks, Sequence) or len(sparks) != len(checked_mission["spark_audits"]) or len(sparks) > 3:
            raise MetricsError("source gate/Spark denominators invalid", "$.source")
        blockers = [f for f in source["review"]["findings"] if isinstance(f, Mapping) and (f.get("severity") == "P1" or f.get("label") == "BLOCKING")]
        writer_ids = {r.get("writer_task_id") for r in rows if r.get("writer_task_id")}
        expected: dict[str, Any] = {
            "first_pass_approval": source["review"].get("verdict") == "APPROVE" and source["review"]["round"] == 1,
            "pre_review_blocker_capture": len(blockers), "review_rounds": source["review"]["round"],
            "full_runs_per_head": None if "full" not in required_stages else len(full_rows) / len(stage_heads["full"]),
            "fresh_runs_per_head": None if "fresh" not in required_stages else len(fresh_rows) / len(stage_heads["fresh"]),
            "evidence_corrections": sum(1 for r in rows if r.get("correction_of")), "writer_handoffs": max(0, len(writer_ids) - 1),
            "spark_audit_count": len(sparks), "spark_audit_latency_sec": round(sum(_finite(r.get("elapsed_sec"), "$.source.spark_results[].elapsed_sec") for r in sparks), 6),
            "gate_elapsed_sec": round(sum(_finite(r.get("elapsed_sec"), "$.source.gate_runs[].elapsed_sec") for r in gate_runs), 6),
            "new_blocker_admissions": sum(1 for f in source["review"]["findings"] if isinstance(f, Mapping) and f.get("attribution") in {"DELTA_INTRODUCED", "ORIGINAL_SCOPE_MISSED", "NEW_FALSIFIABLE_EVIDENCE"}),
        }
        checked = validate_metrics_shape(metrics, required_stages=required_stages)
        if canonical_sha256(source) != checked["source_hash"] or any(checked[name] != value for name, value in expected.items()):
            raise MetricsError("metrics do not match validated source bundle")
    return checked


# Review-efficiency metrics are intentionally a separate schema.  The legacy
# ``metrics.v16`` packet remains frozen for dashboard compatibility; these
# values optimize correctness/convergence only after evidence validity passes.
REVIEW_EFFICIENCY_SCHEMA = "review-efficiency.v16"
REVIEW_EFFICIENCY_FIELDS = {
    "schema", "source_hash", "time_to_first_actionable_finding_sec",
    "time_to_verdict_sec", "time_to_correct_verdict_sec",
    "time_to_correct_merge_sec", "review_round_count", "false_blocker_rate",
    "scope_reopened_count", "p1_miss_count", "original_scope_missed_p1_count",
    "evidence_reuse_rate", "model_calls", "input_tokens", "output_tokens",
    "total_tokens", "token_count_basis",
}
_REVIEW_SOURCE_FIELDS = {"schema", "envelope", "events", "adjudications", "usage"}
_REVIEW_EVENT_COMMON = {"event_id", "type", "timestamp", "round_id", "run_id", "head_sha", "envelope_sha256"}
_REVIEW_EVENT_FIELDS = {
    "review_started": _REVIEW_EVENT_COMMON,
    "start": _REVIEW_EVENT_COMMON,
    "actionable_finding": _REVIEW_EVENT_COMMON | {"finding"},
    "finding": _REVIEW_EVENT_COMMON | {"finding"},
    "verdict": _REVIEW_EVENT_COMMON | {"verdict"},
    "merge": _REVIEW_EVENT_COMMON | {"merge_status"},
    "evidence_check": _REVIEW_EVENT_COMMON | {"check_id", "reused"},
    "scope_reopened": _REVIEW_EVENT_COMMON | {"previous_envelope_sha256"},
}
_FINDING_FIELDS = {
    "id", "severity", "label", "attribution", "contract_clause", "location",
    "counterexample", "impact", "smallest_acceptable_outcome", "acceptance_check",
    "disposition",
}
_ADJ_FIELDS = {
    "adjudication_id", "type", "timestamp", "round_id", "run_id", "head_sha",
    "envelope_sha256", "finding_id", "correct", "oracle", "evidence_ref",
    "observation_id", "started_at", "ended_at", "closed", "is_false_blocker",
}


def _sha(value: Any, path: str, length: int) -> str:
    if not isinstance(value, str) or len(value) != length or any(c not in "0123456789abcdef" for c in value.lower()):
        raise MetricsError(f"hexadecimal digest of length {length} required", path)
    return value.lower()


def _utc(value: Any, path: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise MetricsError("ISO-8601 UTC timestamp ending in Z required", path)
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise MetricsError("valid ISO-8601 UTC timestamp required", path) from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise MetricsError("UTC timestamp required", path)
    return parsed


def _review_id(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 256:
        raise MetricsError("non-empty identifier required", path)
    return value


def _review_envelope(envelope: Any) -> dict[str, str]:
    if not isinstance(envelope, Mapping) or set(envelope) != {"head_sha", "tree_sha", "envelope_sha256"}:
        raise MetricsError("exact head/tree/envelope identity required", "$.envelope")
    return {
        "head_sha": _sha(envelope["head_sha"], "$.envelope.head_sha", 40),
        "tree_sha": _sha(envelope["tree_sha"], "$.envelope.tree_sha", 40),
        "envelope_sha256": _sha(envelope["envelope_sha256"], "$.envelope.envelope_sha256", 64),
    }


def _review_finding(value: Any, path: str) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != _FINDING_FIELDS:
        raise MetricsError("complete actionable finding contract required", path)
    for name in _FINDING_FIELDS - {"severity", "label", "attribution", "disposition"}:
        if not isinstance(value[name], str) or not value[name].strip():
            raise MetricsError("non-empty finding contract field required", f"{path}.{name}")
    for name in ("severity", "label", "attribution", "disposition"):
        if not isinstance(value[name], str) or not value[name].strip():
            raise MetricsError("finding contract string required", f"{path}.{name}")
    return dict(value)


def _validate_review_source(source_bundle: Mapping[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], dict[str, Any], dict[str, datetime]]:
    if not isinstance(source_bundle, Mapping) or set(source_bundle) != _REVIEW_SOURCE_FIELDS:
        raise MetricsError("exact review-efficiency source fields required", "$.source")
    if source_bundle.get("schema") != REVIEW_EFFICIENCY_SCHEMA:
        raise MetricsError("review-efficiency.v16 source schema required", "$.source.schema")
    envelope = _review_envelope(source_bundle["envelope"])
    events = source_bundle["events"]
    if not isinstance(events, list) or not events:
        raise MetricsError("non-empty source event list required", "$.source.events")
    checked_events: list[dict[str, Any]] = []
    event_ids: set[str] = set()
    run_rounds: dict[str, str] = {}
    event_times: dict[str, datetime] = {}
    for index, event in enumerate(events):
        path = f"$.source.events[{index}]"
        if not isinstance(event, Mapping):
            raise MetricsError("event object required", path)
        kind = event.get("type")
        allowed = _REVIEW_EVENT_FIELDS.get(kind)
        if allowed is None or set(event) != allowed:
            raise MetricsError("strict event fields required", path)
        event_id = _review_id(event.get("event_id"), f"{path}.event_id")
        if event_id in event_ids:
            raise MetricsError("event IDs must be distinct", path)
        event_ids.add(event_id)
        round_id = _review_id(event.get("round_id"), f"{path}.round_id")
        run_id = _review_id(event.get("run_id"), f"{path}.run_id")
        if run_id in run_rounds and run_rounds[run_id] != round_id:
            raise MetricsError("run ID maps to one round only", path)
        run_rounds[run_id] = round_id
        timestamp = _utc(event.get("timestamp"), f"{path}.timestamp")
        if _sha(event.get("head_sha"), f"{path}.head_sha", 40) != envelope["head_sha"] or _sha(event.get("envelope_sha256"), f"{path}.envelope_sha256", 64) != envelope["envelope_sha256"]:
            raise MetricsError("event/source identity mismatch", path)
        checked = dict(event)
        checked["head_sha"] = envelope["head_sha"]
        checked["envelope_sha256"] = envelope["envelope_sha256"]
        checked_events.append(checked)
        event_times[event_id] = timestamp
        if kind in {"actionable_finding", "finding"}:
            checked["finding"] = _review_finding(event["finding"], f"{path}.finding")
        elif kind == "verdict":
            if event.get("verdict") not in {"APPROVE", "REQUEST_CHANGES"}:
                raise MetricsError("review verdict required", f"{path}.verdict")
        elif kind == "merge" and event.get("merge_status") != "MERGED":
            raise MetricsError("merge event must be MERGED", f"{path}.merge_status")
        elif kind == "evidence_check":
            if not isinstance(event.get("check_id"), str) or not event["check_id"].strip() or type(event.get("reused")) is not bool:
                raise MetricsError("evidence check identity and boolean reuse required", path)
        elif kind == "scope_reopened":
            _sha(event.get("previous_envelope_sha256"), f"{path}.previous_envelope_sha256", 64)
    starts = [e for e in checked_events if e["type"] in {"review_started", "start"}]
    verdicts = [e for e in checked_events if e["type"] == "verdict"]
    if len(starts) != 1 or not verdicts:
        raise MetricsError("exactly one review start and at least one verdict required", "$.source.events")
    start_time = _utc(starts[0]["timestamp"], "$.source.events[start].timestamp")
    for event in checked_events:
        if _utc(event["timestamp"], "$.source.events.timestamp") < start_time:
            raise MetricsError("event timestamp precedes review start", "$.source.events")
    verdict_times = [_utc(event["timestamp"], "$.source.events[].timestamp") for event in verdicts]
    merge_times = [_utc(event["timestamp"], "$.source.events[].timestamp") for event in checked_events if event["type"] == "merge"]
    if merge_times and min(merge_times) < min(verdict_times):
        raise MetricsError("merge cannot precede verdict", "$.source.events")
    adjudications = source_bundle["adjudications"]
    if not isinstance(adjudications, list):
        raise MetricsError("adjudication list required", "$.source.adjudications")
    checked_adj: list[dict[str, Any]] = []
    adj_ids: set[str] = set()
    windows: dict[str, tuple[datetime, datetime, bool]] = {}
    for index, record in enumerate(adjudications):
        path = f"$.source.adjudications[{index}]"
        if not isinstance(record, Mapping) or set(record) - _ADJ_FIELDS:
            raise MetricsError("strict adjudication fields required", path)
        if not isinstance(record.get("type"), str) or record["type"] not in {"verdict_correctness", "blocker", "observation_window", "p1_miss"}:
            raise MetricsError("known adjudication type required", path)
        aid = _review_id(record.get("adjudication_id"), f"{path}.adjudication_id")
        if aid in adj_ids:
            raise MetricsError("adjudication IDs must be distinct", path)
        adj_ids.add(aid)
        if record["type"] != "observation_window":
            for key in ("timestamp", "round_id", "run_id", "head_sha", "envelope_sha256"):
                if key not in record:
                    raise MetricsError("adjudication identity required", path)
            _utc(record["timestamp"], f"{path}.timestamp")
            if _sha(record["head_sha"], f"{path}.head_sha", 40) != envelope["head_sha"] or _sha(record["envelope_sha256"], f"{path}.envelope_sha256", 64) != envelope["envelope_sha256"]:
                raise MetricsError("adjudication/source identity mismatch", path)
            _review_id(record["round_id"], f"{path}.round_id"); _review_id(record["run_id"], f"{path}.run_id")
        if record["type"] == "verdict_correctness":
            if type(record.get("correct")) is not bool or record.get("oracle") not in {"objective", "accepted_closure", "incident_observation"} or not isinstance(record.get("evidence_ref"), str) or not record["evidence_ref"].strip():
                raise MetricsError("objective verdict adjudication required", path)
        elif record["type"] == "blocker":
            if type(record.get("is_false_blocker")) is not bool or record.get("oracle") not in {"objective", "accepted_closure", "incident_observation"} or not isinstance(record.get("finding_id"), str) or not record["finding_id"].strip() or not isinstance(record.get("evidence_ref"), str) or not record["evidence_ref"].strip():
                raise MetricsError("objective blocker adjudication required", path)
        elif record["type"] == "observation_window":
            expected = {"adjudication_id", "type", "started_at", "ended_at", "closed", "head_sha", "envelope_sha256"}
            if set(record) != expected or type(record.get("closed")) is not bool:
                raise MetricsError("observation window fields required", path)
            if _sha(record["head_sha"], f"{path}.head_sha", 40) != envelope["head_sha"] or _sha(record["envelope_sha256"], f"{path}.envelope_sha256", 64) != envelope["envelope_sha256"]:
                raise MetricsError("observation/source identity mismatch", path)
            started = _utc(record["started_at"], f"{path}.started_at"); ended = _utc(record["ended_at"], f"{path}.ended_at")
            if ended < started:
                raise MetricsError("observation window ordering", path)
            windows[aid] = (started, ended, record["closed"])
        else:
            if not isinstance(record.get("observation_id"), str) or not isinstance(record.get("finding_id"), str) or not record["finding_id"].strip():
                raise MetricsError("P1 miss must reference observation window", path)
        checked_adj.append(dict(record))
    for index, record in enumerate(checked_adj):
        if record["type"] == "p1_miss" and record["observation_id"] not in windows:
            raise MetricsError("P1 miss must reference observation window", f"$.source.adjudications[{index}]")
        if record["type"] == "p1_miss":
            observed_at = _utc(record["timestamp"], f"$.source.adjudications[{index}].timestamp")
            window_start, window_end, _ = windows[record["observation_id"]]
            if not window_start <= observed_at <= window_end:
                raise MetricsError("P1 miss must fall within observation window", f"$.source.adjudications[{index}]")
    usage = source_bundle["usage"]
    if not isinstance(usage, Mapping) or set(usage) != {"model_calls", "input_tokens", "output_tokens", "total_tokens", "token_count_basis"}:
        raise MetricsError("exact secondary usage fields required", "$.source.usage")
    unavailable = all(value is None for value in (usage["model_calls"], usage["input_tokens"], usage["output_tokens"], usage["total_tokens"]))
    if unavailable:
        if usage["token_count_basis"] != "unavailable":
            raise MetricsError("unavailable usage requires unavailable basis", "$.source.usage")
    else:
        if any(type(usage[name]) is not int or usage[name] < 0 for name in ("model_calls", "input_tokens", "output_tokens", "total_tokens")) or usage["total_tokens"] != usage["input_tokens"] + usage["output_tokens"] or not isinstance(usage["token_count_basis"], str) or not usage["token_count_basis"].strip():
            raise MetricsError("usage arithmetic/basis invalid", "$.source.usage")
    return envelope, checked_events, checked_adj, dict(usage), {"start": start_time, **{f"event:{k}": v for k, v in event_times.items()}}


def _derive_review_efficiency_metrics(source_bundle: Mapping[str, Any]) -> dict[str, Any]:
    """Derive correctness/convergence metrics from strict, identity-bound artifacts."""
    envelope, events, adjudications, usage, times = _validate_review_source(source_bundle)
    start = times["start"]
    findings = [event["finding"] for event in events if event["type"] in {"actionable_finding", "finding"}]
    verdict_events = [event for event in events if event["type"] == "verdict"]
    verdict_time = min(_utc(event["timestamp"], "$.events[].timestamp") for event in verdict_events)
    finding_events = [event for event in events if event["type"] in {"actionable_finding", "finding"}]
    first_finding = min((_utc(event["timestamp"], "$.events[].timestamp") for event in finding_events), default=None)
    correct = [record for record in adjudications if record["type"] == "verdict_correctness" and record.get("correct") is True]
    correct_times = [_utc(record["timestamp"], "$.adjudications[].timestamp") for record in correct]
    merge_events = [event for event in events if event["type"] == "merge"]
    merge_time = min((_utc(event["timestamp"], "$.events[].timestamp") for event in merge_events), default=None)
    false_records = [record for record in adjudications if record["type"] == "blocker"]
    false_rate = None if not false_records else sum(1 for record in false_records if record["is_false_blocker"]) / len(false_records)
    reuse_events = [event for event in events if event["type"] == "evidence_check"]
    check_ids = [event["check_id"] for event in reuse_events]
    if len(set(check_ids)) != len(check_ids):
        raise MetricsError("evidence check IDs must be distinct", "$.source.events")
    reuse_rate = None if not reuse_events else sum(1 for event in reuse_events if event["reused"]) / len(reuse_events)
    scope_reopened = sum(1 for event in events if event["type"] == "scope_reopened" and event["previous_envelope_sha256"].lower() == envelope["envelope_sha256"])
    windows = {record["adjudication_id"]: record for record in adjudications if record["type"] == "observation_window"}
    closed_windows = [record for record in windows.values() if record["closed"]]
    p1_records = [record for record in adjudications if record["type"] == "p1_miss"]
    p1_miss = None if not closed_windows else sum(1 for record in p1_records if windows.get(record["observation_id"], {}).get("closed") is True)
    original_scope_missed = sum(1 for finding in findings if finding["severity"] == "P1" and finding["attribution"] == "ORIGINAL_SCOPE_MISSED")
    output: dict[str, Any] = {
        "schema": REVIEW_EFFICIENCY_SCHEMA,
        "source_hash": canonical_sha256(dict(source_bundle)),
        "time_to_first_actionable_finding_sec": None if first_finding is None else (first_finding - start).total_seconds(),
        "time_to_verdict_sec": (verdict_time - start).total_seconds(),
        "time_to_correct_verdict_sec": None if not correct_times else (min(correct_times) - start).total_seconds(),
        "time_to_correct_merge_sec": None if merge_time is None or not correct_times else (merge_time - start).total_seconds(),
        "review_round_count": len({event["round_id"] for event in events}),
        "false_blocker_rate": false_rate,
        "scope_reopened_count": scope_reopened,
        "p1_miss_count": p1_miss,
        "original_scope_missed_p1_count": original_scope_missed,
        "evidence_reuse_rate": reuse_rate,
        "model_calls": usage["model_calls"], "input_tokens": usage["input_tokens"],
        "output_tokens": usage["output_tokens"], "total_tokens": usage["total_tokens"],
        "token_count_basis": usage["token_count_basis"],
    }
    return output


def collect_review_efficiency_metrics(source_bundle: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and derive the versioned review-efficiency artifact packet."""
    output = _derive_review_efficiency_metrics(source_bundle)
    return validate_review_efficiency_metrics(output, source_bundle=source_bundle)


def validate_review_efficiency_metrics(metrics: Mapping[str, Any], *, source_bundle: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Recompute and compare the strict review-efficiency packet; no claims are trusted."""
    if source_bundle is None or not isinstance(metrics, Mapping) or set(metrics) != REVIEW_EFFICIENCY_FIELDS:
        raise MetricsError("exact review-efficiency fields and source bundle required")
    if metrics.get("schema") != REVIEW_EFFICIENCY_SCHEMA:
        raise MetricsError("review-efficiency.v16 schema required", "$.schema")
    source_hash = metrics.get("source_hash")
    if not isinstance(source_hash, str) or len(source_hash) != 64 or any(c not in "0123456789abcdef" for c in source_hash):
        raise MetricsError("source_hash must be lowercase SHA-256", "$.source_hash")
    for name in ("time_to_first_actionable_finding_sec", "time_to_verdict_sec", "time_to_correct_verdict_sec", "time_to_correct_merge_sec"):
        value = metrics.get(name)
        if value is not None:
            _finite(value, f"$.{name}")
    for name in ("false_blocker_rate", "evidence_reuse_rate"):
        value = metrics.get(name)
        if value is not None:
            _finite(value, f"$.{name}")
            if float(value) > 1.0:
                raise MetricsError("rate must be in [0,1]", f"$.{name}")
    if type(metrics.get("review_round_count")) is not int or metrics["review_round_count"] < 1:
        raise MetricsError("positive review round count required", "$.review_round_count")
    for name in ("scope_reopened_count", "original_scope_missed_p1_count"):
        _nonneg_int(metrics.get(name), f"$.{name}")
    p1_miss = metrics.get("p1_miss_count")
    if p1_miss is not None:
        _nonneg_int(p1_miss, "$.p1_miss_count")
    for name in ("model_calls", "input_tokens", "output_tokens", "total_tokens"):
        value = metrics.get(name)
        if value is not None and (type(value) is not int or value < 0):
            raise MetricsError("non-negative integer or unavailable value required", f"$.{name}")
    if not isinstance(metrics.get("token_count_basis"), str) or not metrics["token_count_basis"].strip():
        raise MetricsError("token count basis required", "$.token_count_basis")
    checked = _derive_review_efficiency_metrics(source_bundle) if metrics.get("source_hash") != "" else None
    if checked is None or any(metrics[name] != checked[name] for name in REVIEW_EFFICIENCY_FIELDS):
        raise MetricsError("review-efficiency metrics do not match source bundle")
    return dict(metrics)


def dashboard(metrics: Mapping[str, Any], targets: Mapping[str, Any] | None = None, *, source_bundle: Mapping[str, Any] | None = None) -> dict[str, Any]:
    checked = validate_metrics_shape(metrics) if source_bundle is None else validate_metrics(metrics, source_bundle=source_bundle)
    # Approval/round/admission outcomes are observed diagnostics, not default
    # optimization gates.  Keep only bounded safety/resource/evidence signals
    # here; callers may still provide an explicit target mapping when desired.
    policy = dict(targets or {"pre_review_blocker_capture": {"min": 0}, "full_runs_per_head": {"max": 1}, "fresh_runs_per_head": {"max": 1}, "evidence_corrections": {"max": 0}, "writer_handoffs": {"max": 0}, "spark_audit_count": {"max": 3}, "spark_audit_latency_sec": {"max": 300}, "gate_elapsed_sec": {"max": 600}})
    if targets is None:
        # A missing non-required stage is unavailable, not a zero-valued
        # resource result and therefore has no applicable default ceiling.
        for name in ("full_runs_per_head", "fresh_runs_per_head"):
            if checked[name] is None:
                policy.pop(name, None)
    return {"schema": "metrics-dashboard.v16", "policy_targets": policy, "observed": {k: checked[k] for k in METRIC_NAMES}, "interpretation": "Targets are policy thresholds, not claimed results.", "source_hash": checked["source_hash"]}

"""Risk-routed review policy for V16 missions.

The policy is deliberately small and stdlib-only.  ``validate_review_policy``
is the strict, author-facing contract; ``resolve_review_policy`` is the
runtime safety boundary and therefore falls back to the legacy high-risk route
when a policy is missing or cannot be interpreted safely.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any


class ReviewPolicyError(ValueError):
    """Raised when an explicit review policy is malformed or unsafe."""


HIGH_RISK_TRIGGERS = frozenset(
    {
        "math_numeric",
        "exact_parity",
        "security",
        "privacy",
        "public_contract",
        "schema_data_format",
        "irreversible_migration",
        "supply_chain_installer",
        "production_runtime",
        "formal_research_release",
        "hook_reviewer_model_routing",
    }
)
RISK_LEVELS = frozenset({"low", "medium", "high"})
CONTEXT_MODES = frozenset(
    {"author_contextual", "independent_clean_room", "delta_continuation", "escalated_fresh"}
)
STAGE_ORDER = ("targeted", "full", "fresh")
DEFAULT_STAGES = {"low": ("targeted",), "medium": ("targeted", "full"), "high": STAGE_ORDER}
DEFAULT_REVIEWER = {
    "low": ("gpt-5.6-terra", "high"),
    "medium": ("gpt-5.6-terra", "high"),
    "high": ("gpt-5.6-sol", "xhigh"),
}

# Keep this list explicit so a typo cannot silently become a policy knob.
POLICY_FIELDS = frozenset(
    {
        "review_risk",
        "reasons",
        "classifier",
        "classifier_identity",
        "high_risk_triggers",
        "required_stages",
        "reviewer_model",
        "reasoning_effort",
        "context_mode",
        "fork_turns",
        "report_only",
    }
)


def _error(message: str, path: str) -> ReviewPolicyError:
    return ReviewPolicyError(f"{path}: {message}")


def _text(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value:
        raise _error("non-empty string required", path)
    if any(ord(char) < 0x20 for char in value):
        raise _error("control characters forbidden", path)
    return value


def _text_list(value: Any, path: str, *, required: bool = False) -> list[str]:
    if not isinstance(value, list):
        raise _error("array of strings required", path)
    if required and not value:
        raise _error("non-empty array required", path)
    result = [_text(item, f"{path}[{index}]") for index, item in enumerate(value)]
    if len(set(result)) != len(result):
        raise _error("duplicate values forbidden", path)
    return result


def validate_review_policy(value: Any, path: str = "$.review_policy") -> dict[str, Any]:
    """Strictly validate and normalize an explicit policy without mutation."""
    if not isinstance(value, Mapping):
        raise _error("object required", path)
    extra = sorted(set(value) - POLICY_FIELDS)
    if extra:
        raise _error("additionalProperties forbidden: " + ",".join(extra), path)
    if "review_risk" not in value:
        raise _error("review_risk is required", path)
    risk = _text(value["review_risk"], f"{path}.review_risk")
    if risk not in RISK_LEVELS:
        raise _error("review_risk must be low/medium/high", f"{path}.review_risk")

    raw_triggers = value.get("high_risk_triggers", [])
    triggers = _text_list(raw_triggers, f"{path}.high_risk_triggers")
    unknown = sorted(set(triggers) - HIGH_RISK_TRIGGERS)
    if unknown:
        raise _error("unknown high-risk trigger(s): " + ",".join(unknown), f"{path}.high_risk_triggers")
    if risk in {"low", "medium"} and triggers:
        raise _error("low/medium policy cannot declare high-risk triggers", f"{path}.high_risk_triggers")
    if risk == "high" and not triggers:
        raise _error("high-risk policy requires at least one trigger", f"{path}.high_risk_triggers")

    reasons = _text_list(value.get("reasons", []), f"{path}.reasons", required=risk in {"low", "medium"})
    classifiers = [value[key] for key in ("classifier", "classifier_identity") if key in value]
    if len(classifiers) > 1 and classifiers[0] != classifiers[1]:
        raise _error("conflicting classifier identities", path)
    if risk in {"low", "medium"} and not classifiers:
        raise _error("explicit low/medium policy requires classifier identity", path)
    if classifiers and classifiers[0] == "" and risk == "high":
        # High-risk policies do not require a classifier identity; this empty
        # normalized value is accepted when a validated policy is resolved a
        # second time by the compiler.
        classifier = ""
    else:
        classifier = _text(classifiers[0], f"{path}.classifier_identity") if classifiers else ""

    stages = value.get("required_stages", list(DEFAULT_STAGES[risk]))
    stages = _text_list(stages, f"{path}.required_stages", required=True)
    valid_prefixes = {STAGE_ORDER[:index] for index in range(1, len(STAGE_ORDER) + 1)}
    if tuple(stages) not in valid_prefixes:
        raise _error(
            "required_stages must be an ordered non-empty prefix of targeted/full/fresh",
            f"{path}.required_stages",
        )

    context_mode = value.get("context_mode", "independent_clean_room")
    context_mode = _text(context_mode, f"{path}.context_mode")
    if context_mode not in CONTEXT_MODES:
        raise _error("unknown context mode", f"{path}.context_mode")
    if context_mode != "independent_clean_room":
        raise _error(
            "mission formal review route requires independent_clean_room",
            f"{path}.context_mode",
        )
    fork_turns = value.get("fork_turns", "none")
    if _text(fork_turns, f"{path}.fork_turns") != "none":
        raise _error("formal review route requires fork_turns=none", f"{path}.fork_turns")
    report_only = value.get("report_only", True)
    if type(report_only) is not bool or report_only is not True:
        raise _error("formal review route requires report_only=true", f"{path}.report_only")

    # Validate optional author suggestions for shape, but do not carry them
    # into the normalized route: risk owns the independent reviewer defaults.
    if "reviewer_model" in value:
        _text(value["reviewer_model"], f"{path}.reviewer_model")
    if "reasoning_effort" in value:
        _text(value["reasoning_effort"], f"{path}.reasoning_effort")
    reviewer_model, reasoning_effort = DEFAULT_REVIEWER[risk]
    return {
        "review_risk": risk,
        "reasons": reasons,
        "classifier_identity": classifier,
        "high_risk_triggers": sorted(triggers),
        "required_stages": list(stages),
        "reviewer_model": reviewer_model,
        "reasoning_effort": reasoning_effort,
        "context_mode": context_mode,
        "fork_turns": "none",
        "report_only": True,
    }


def _legacy_policy(*, resolution: str, errors: Sequence[str] = ()) -> dict[str, Any]:
    return {
        "review_risk": "high",
        "reasons": ["legacy or unresolvable policy; fail-closed compatibility route"],
        "classifier_identity": "legacy-fail-closed",
        "high_risk_triggers": ["hook_reviewer_model_routing"],
        "required_stages": list(STAGE_ORDER),
        "reviewer_model": "gpt-5.6-sol",
        "reasoning_effort": "xhigh",
        "context_mode": "independent_clean_room",
        "fork_turns": "none",
        "report_only": True,
        "legacy_fallback": True,
        "resolution": resolution,
        "resolution_errors": list(errors),
    }


def resolve_review_policy(value: Mapping[str, Any] | Any) -> dict[str, Any]:
    """Resolve a mission or policy, failing closed for absent/ambiguous input."""
    policy = value.get("review_policy") if isinstance(value, Mapping) and "review_policy" in value else value
    if policy is None:
        return _legacy_policy(resolution="missing")
    try:
        normalized = validate_review_policy(policy)
    except ReviewPolicyError as exc:
        return _legacy_policy(resolution="invalid", errors=(str(exc),))
    normalized["legacy_fallback"] = False
    normalized["resolution"] = "explicit"
    normalized["resolution_errors"] = []
    # The writer may describe a task-preferred model, but cannot weaken the
    # independent review route.  Reviewer model names are defaults selected by
    # risk, not capability bans on the writer's assigned model.
    normalized["reviewer_model"], normalized["reasoning_effort"] = DEFAULT_REVIEWER[normalized["review_risk"]]
    return normalized


def context_mode_is_gating(context_mode: str) -> bool:
    """Return whether a review result may participate in a readiness gate."""
    if context_mode not in CONTEXT_MODES:
        raise ReviewPolicyError(f"unknown context mode: {context_mode}")
    return context_mode != "author_contextual"


# Short aliases keep the public surface discoverable for callers that refer to
# the module as a policy resolver/validator rather than by the full contract
# function names.
resolve_policy = resolve_review_policy
validate_policy = validate_review_policy


__all__ = [
    "CONTEXT_MODES",
    "DEFAULT_STAGES",
    "DEFAULT_REVIEWER",
    "HIGH_RISK_TRIGGERS",
    "POLICY_FIELDS",
    "RISK_LEVELS",
    "ReviewPolicyError",
    "context_mode_is_gating",
    "resolve_review_policy",
    "resolve_policy",
    "validate_review_policy",
    "validate_policy",
]

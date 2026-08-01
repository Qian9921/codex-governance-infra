"""Strict, versioned V16 productivity contracts.

The validators intentionally avoid third-party JSON-schema dependencies so a
fresh clone on Python 3.9 can validate packets before any gate executes.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from typing import Any, Iterable, Mapping, Sequence

from .review_policy import DEFAULT_REVIEWER, ReviewPolicyError, validate_review_policy

SCHEMA_VERSION = "16"
ID_RE = re.compile(r"[A-Za-z][A-Za-z0-9_.:/-]{1,127}\Z")
SHA_RE = re.compile(r"[0-9a-f]{40}\Z")
HEX_RE = re.compile(r"[0-9a-fA-F]+\Z")
FORBIDDEN_TEXT_RE = re.compile(
    r"(?:gh[pso]_[A-Za-z0-9]{12,}|/" + r"home/|/Users/|prompt|token|credential|session[_-]?id|transcript)",
    re.I,
)
PRIVATE_FINDING_TEXT_RE = re.compile(
    r"(?:gh[pso]_[A-Za-z0-9]{12,}|/" + r"home/|/Users/)",
    re.I,
)

# A compact machine-readable inventory accompanies the executable validators.
# ``validation_mode`` is deliberately explicit: a source/caller-bound
# validator cannot be truthfully advertised as a standalone JSON validator.
# The JSON copy lives under ``contracts/schema_registry.v16.json`` and is
# checked for parity by the focused registry tests.  Validation itself remains
# in Python so fresh Python 3.9 clones need no JSON-schema package.
SCHEMA_REGISTRY = {
    "mission.v16": {
        "validator": "contracts.validate_mission",
        "validation_mode": "standalone",
        "required": ["schema", "mission_id", "milestone", "objective", "owner", "assigned_model", "role", "permissions", "scope", "reviewer_separation", "operating_domain", "invariants", "counterexamples", "entrypoints", "gates", "acceptance", "non_goals", "evidence_budget", "rollback", "stop_conditions", "spark_audits"],
        "optional": ["review_policy"],
    },
    "review-policy.v16": {
        "validator": "review_policy.validate_review_policy",
        "validation_mode": "standalone",
        "required": ["review_risk"],
        "optional": ["reasons", "classifier", "classifier_identity", "high_risk_triggers", "required_stages", "reviewer_model", "reasoning_effort", "context_mode", "fork_turns", "report_only"],
        "conditional_required": {"low_or_medium": ["reasons", "classifier_identity"], "high": ["high_risk_triggers"]},
    },
    "invariant.v16": {
        "validator": "contracts.validate_invariant",
        "validation_mode": "standalone",
        "required": ["id", "description", "blocking", "counterexample_ids"],
        "optional": [],
    },
    "counterexample.v16": {
        "validator": "contracts.validate_counterexample",
        "validation_mode": "standalone",
        "required": ["id", "semantics", "description", "entrypoint_id", "gate_id", "why_red", "cost", "denominator", "expected"],
        "optional": [],
    },
    "gate.v16": {
        "validator": "contracts.validate_gate",
        "validation_mode": "standalone",
        "required": ["id", "stage", "depends_on", "entrypoint_ids", "blocking", "reusable"],
        "optional": ["read_only"],
    },
    "acceptance.v16": {
        "validator": "contracts.validate_acceptance",
        "validation_mode": "standalone",
        "required": ["id", "invariant_id", "counterexample_id", "entrypoint_id", "gate_id", "blocking", "why_red", "cost", "denominator", "red_meaning", "green_meaning"],
        "optional": [],
    },
    "spark-audit-request.v16": {
        "validator": "spark.validate_request",
        "validation_mode": "standalone",
        "required": ["schema", "audit_id", "mission_id", "domain", "scope", "max_findings", "assigned_model", "role", "permissions", "fork_turns", "context_mode", "report_only", "spawn_index"],
        "optional": [],
    },
    "spark-audit-result.v16": {
        "validator": "spark.validate_result",
        "validation_mode": "caller-bound",
        "required": ["schema", "audit_id", "mission_id", "task_id", "assigned_model", "reasoning_effort", "fork_turns", "context_mode", "report_only", "scope", "findings", "dispositions", "started_at", "ended_at", "elapsed_sec"],
        "optional": ["artifact_sha256"],
        "external_inputs": ["request"],
    },
    "compiled-plan.v16": {
        "validator": "compiler.compile_mission",
        "validation_mode": "caller-bound",
        "required": ["schema", "mission_id", "mission_sha256", "base_sha", "tree_sha", "gate_order", "gates", "entrypoints", "acceptance", "counterexample_ids", "spark_audit_ids", "review_policy", "execution"],
        "optional": [],
        "external_inputs": ["mission.v16"],
    },
    "gate-result.v16": {
        "validator": "runner.validate_gate_result",
        "validation_mode": "caller-bound",
        "required": ["schema", "gate_id", "stage", "decision", "expected_head", "actual_head", "tree_sha", "dirty", "snapshot_mode", "snapshot_sha256", "started_at", "ended_at", "elapsed_sec", "rows"],
        "optional": ["reason", "closure_binding_receipt_sha256", "closure_binding_sha256s"],
        "external_inputs": ["expected_head", "expected_tree", "expected_snapshot", "artifact_root", "compiled-plan.v16", "closure-binding-receipt.v16"],
    },
    "closure-binding-receipt.v16": {
        "validator": "contracts.validate_closure_binding_receipt",
        "validation_mode": "caller-bound",
        "required": ["schema", "mission_id", "compiled_plan_sha256", "closure_plan_sha256", "closure_plan_file_sha256", "dispatch_transcript_file_sha256", "normalized_source_artifacts", "finding_count", "bindings", "receipt_sha256"],
        "optional": [],
        "external_inputs": ["expected_compiled_plan_sha256", "expected_closure_plan_sha256", "expected_closure_plan_file_sha256", "expected_dispatch_transcript_file_sha256", "expected_receipt_sha256"],
    },
    "pre-execution-closure-authority.v16": {
        "validator": "contracts.validate_pre_execution_closure_authority",
        "validation_mode": "caller-bound",
        "required": ["schema", "mission_id", "closure_binding_receipt_sha256", "compiled_plan_sha256", "closure_plan_sha256", "closure_plan_file_sha256", "dispatch_transcript_file_sha256", "normalized_source_artifacts", "finding_count", "bindings_sha256", "authority_sha256"],
        "optional": [],
        "external_inputs": ["closure-binding-receipt.v16", "expected_authority_sha256"],
    },
    "readiness-state.v16": {
        "validator": "state.validate_state",
        "validation_mode": "caller-bound",
        "required": ["schema", "mission_id", "state", "revision", "created_at", "updated_at", "base_sha", "head_sha", "tree_sha", "identity_mode", "snapshot_sha256", "prior_snapshot_sha256", "delta_sha256", "counterexample_ids", "red_counterexamples", "green_counterexamples", "spark_findings", "spark_audit_count", "dispositions", "gate_ids", "evidence_ids", "receipt_artifacts", "author_closure_sha256", "review_ready", "required_stages", "reviewer_model", "reasoning_effort", "review_risk", "reviewer_route", "classifier_identity", "high_risk_triggers", "review_policy_sha256", "approved_review_artifact_sha256", "approved_review_packet_sha256"],
        "optional": [],
        "approval_required": ["approved_review_artifact_sha256", "approved_review_packet_sha256"],
        "policy_binding": ["required_stages", "reviewer_model", "reasoning_effort", "review_risk", "reviewer_route", "classifier_identity", "high_risk_triggers", "review_policy_sha256", "identity_mode", "snapshot_sha256", "prior_snapshot_sha256", "delta_sha256"],
        "external_inputs": ["validated review packet", "validated independent-review.v16 artifact", "dispatch lineage"],
    },
    "evidence-envelope.v16": {
        "validator": "evidence.validate_envelope",
        "validation_mode": "caller-bound",
        "required": ["schema", "mission_id", "head_sha", "tree_sha", "identity_mode", "snapshot_sha256", "clean", "generated_at", "rows", "envelope_sha256"],
        "optional": ["dispatch_transcript_sha256", "closure_binding_receipt_sha256", "closure_plan_sha256"],
        "external_inputs": ["expected_head", "expected_tree", "expected_identity_mode", "expected_snapshot_sha256", "log_root", "transcript_path", "compiled-plan.v16", "closure-binding-receipt.v16", "expected_closure_binding_receipt_sha256", "expected_closure_plan_sha256"],
    },
    "review-packet.v16": {
        "validator": "trace.validate_review_packet",
        "validation_mode": "standalone",
        "required": ["schema", "mission_id", "author_login", "reviewer_login", "base_sha", "head_sha", "tree_sha", "lineage_mode", "coverage_status", "reviewed_scope", "unreviewed_scope", "checks", "findings", "closures", "verdict", "round", "body_sha256"],
        "optional": ["independent_artifact_sha256", "expected_scope", "incident", "decision_basis", "identity_mode", "snapshot_sha256", "prior_snapshot_sha256", "prior_head_sha", "delta_sha256"],
        "approval_required": ["decision_basis", "independent_artifact_sha256"],
        "decision_basis_required": ["acceptance_envelope_sha256", "diff_sha256", "reviewed_dependency_scope_sha256", "evidence_bundle_sha256", "evidence_denominator", "review_risk", "reviewer_route", "reviewer_model", "reasoning_effort", "required_stages", "classifier_identity", "high_risk_triggers", "review_policy_sha256", "reference_identity_sha256", "operating_domain_sha256", "acceptance_thresholds_sha256", "invariants_sha256", "non_goals_sha256", "identity_mode", "snapshot_sha256", "prior_snapshot_sha256"],
        "decision_basis_optional": ["prior_head_sha", "delta_sha256", "closure_authority"],
        "decision_basis_conditional_required": {"closure_authority": ["schema", "mission_id", "closure_binding_receipt_sha256", "compiled_plan_sha256", "closure_plan_sha256", "closure_plan_file_sha256", "dispatch_transcript_file_sha256", "normalized_source_artifacts", "finding_count", "bindings_sha256", "authority_sha256"]},
    },
    "independent-review.v16": {
        "validator": "trace.validate_independent_artifact",
        "validation_mode": "caller-bound",
        "required": ["schema", "reviewer_login", "reviewer_model", "reasoning_effort", "reviewer_route", "review_risk", "fork_turns", "context_mode", "report_only", "reviewer_is_writer", "base_sha", "head_sha", "tree_sha", "identity_mode", "snapshot_sha256", "prior_snapshot_sha256", "diff_sha256", "coverage_status", "reviewed_scope", "unreviewed_scope", "review_packet_sha256", "acceptance_envelope_sha256", "reviewed_dependency_scope_sha256", "evidence_bundle_sha256", "evidence_denominator", "required_stages", "classifier_identity", "high_risk_triggers", "review_policy_sha256", "reference_identity_sha256", "operating_domain_sha256", "acceptance_thresholds_sha256", "invariants_sha256", "non_goals_sha256", "prior_review_artifact_sha256", "prior_head_sha", "delta_sha256", "reviewer_continuity_id", "run_id", "escalation_trigger", "escalation_evidence_ref", "findings", "findings_sha256", "closures", "closures_sha256", "closure_matrix", "closure_matrix_sha256", "known_limitations", "dispatch_lineage", "verdict", "artifact_sha256"],
        "optional": [],
        "external_inputs": ["decision_basis", "expected_head", "expected_tree", "expected_reviewed_scope", "expected_dispatch_transcript_sha256", "expected_task_id", "expected_parent_task_id", "expected_sender", "expected_coverage_status", "expected_unreviewed_scope", "expected_base_sha", "expected_review_packet_sha256", "expected_acceptance_envelope_sha256", "expected_diff_sha256", "expected_reviewed_dependency_scope_sha256", "expected_evidence_bundle_sha256", "expected_evidence_denominator", "expected_review_risk", "expected_reviewer_route", "expected_reviewer_model", "expected_reasoning_effort", "expected_required_stages", "expected_classifier_identity", "expected_high_risk_triggers", "expected_review_policy_sha256", "expected_reference_identity_sha256", "expected_operating_domain_sha256", "expected_acceptance_thresholds_sha256", "expected_invariants_sha256", "expected_non_goals_sha256", "expected_identity_mode", "expected_snapshot_sha256", "expected_prior_snapshot_sha256", "expected_delta_sha256", "evidence_bundle", "closure_binding_receipt", "pre-execution-closure-authority.v16", "expected_closure_binding_receipt_sha256", "expected_closure_plan_sha256"],
        "compatibility_only_inputs": ["expected_closure_binding_receipt_sha256", "expected_closure_plan_sha256"],
    },
    "metrics.v16": {
        "validator": "metrics.validate_metrics",
        "validation_mode": "source-bound",
        "required": ["schema", "mission_id", "source_hash", "first_pass_approval", "pre_review_blocker_capture", "review_rounds", "full_runs_per_head", "fresh_runs_per_head", "evidence_corrections", "writer_handoffs", "spark_audit_count", "spark_audit_latency_sec", "gate_elapsed_sec", "new_blocker_admissions"],
        "optional": [],
        "external_inputs": ["source_bundle"],
    },
    "review-efficiency.v16": {
        "validator": "metrics.validate_review_efficiency_metrics",
        "validation_mode": "source-bound",
        "required": ["schema", "source_hash", "time_to_first_actionable_finding_sec", "time_to_verdict_sec", "time_to_correct_verdict_sec", "time_to_correct_merge_sec", "review_round_count", "false_blocker_rate", "scope_reopened_count", "p1_miss_count", "original_scope_missed_p1_count", "evidence_reuse_rate", "model_calls", "input_tokens", "output_tokens", "total_tokens", "token_count_basis"],
        "optional": [],
        "external_inputs": ["source_bundle"],
    },
    "review-runtime.v16": {
        "validator": "review_runtime.validate_review_runtime",
        "validation_mode": "caller-bound",
        "required": ["schema", "review_policy_sha256", "review_risk", "context_mode", "reviewer_model", "reasoning_effort", "fresh_reviewer", "reuse_prior_reviewer", "delta_only", "prior_complete_required", "changed_files", "changed_lines", "max_files", "max_changed_lines", "max_context_chars", "max_tool_calls", "max_review_calls", "soft_deadline_sec", "hard_deadline_sec", "duplicate_full_scope_reviews", "scope_expansion_policy", "timeout_action", "escalation_triggers", "contract_sha256"],
        "optional": [],
        "external_inputs": ["review-policy.v16"],
    },
    "review-runtime-progress.v16": {
        "validator": "review_runtime.validate_review_progress",
        "validation_mode": "caller-bound",
        "required": ["schema", "contract_sha256", "action", "reason_code", "approval_eligible", "elapsed_sec", "tool_calls", "files_read", "verdict_present", "coverage_complete", "unreviewed_count", "scope_expansion_requested", "new_falsifiable_evidence", "budget_exceeded"],
        "optional": [],
        "external_inputs": ["review-runtime.v16"],
    },
    "dispatch-transcript.v16": {
        "validator": "spark.validate_dispatch_transcript",
        "validation_mode": "caller-bound",
        "required": ["schema", "transcript_version", "lineage_mode", "mission_id", "base_sha", "base_tree", "mission_scope_sha256", "compiled_plan_sha256", "reviewed_head_sha", "reviewed_tree_sha", "snapshot", "audited_input_snapshot", "candidate_binding", "historical_original_audits", "accepted_current_audits", "finding_dispositions", "ordering", "historical_spawn_count", "accepted_current_spawn_count", "transcript_sha256"],
        "optional": [],
        "external_inputs": ["expected_head", "expected_tree", "root"],
    },
    "negative-matrix.v16": {
        "validator": "r1.run_negative_matrix",
        "validation_mode": "source-bound",
        "required": ["schema", "total", "ran", "passed", "failed", "skipped", "xfail", "unknown", "rows", "matrix_sha256"],
        "optional": [],
        "external_inputs": ["mission", "plan", "candidate root"],
    },
    "tool-route-decision.v16": {
        "validator": "tool_routing.validate_route_decision",
        "validation_mode": "standalone",
        "observation_mode": "injected-observation",
        "required": ["schema", "intent", "declared", "preferred_tool", "selected_tool", "decision", "status", "fallback", "attempted_preferred", "reason_code", "evidence_ref"],
        "optional": [],
        "injected_inputs": ["observations"],
    },
    "tool-health.v16": {
        "validator": "tool_routing.validate_health_report",
        "validation_mode": "standalone",
        "observation_mode": "injected-observation",
        "required": ["schema", "status", "probe", "tools", "checks", "counts", "denominator", "denominator_known", "probe_sources", "mutations"],
        "optional": [],
        "injected_inputs": ["observations"],
    },
}


class ContractError(ValueError):
    """Raised when a contract violates its strict schema or invariants."""

    def __init__(self, message: str, path: str = "$") -> None:
        self.path = path
        super().__init__(f"{path}: {message}")


def canonical_json(value: Any) -> str:
    """Return deterministic UTF-8 JSON without executable content."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def counterexample_sha256(value: Any, path: str = "$.counterexample") -> str:
    """Hash the exact frozen public counterexample text."""
    text = _str(value, path, public=False)
    if PRIVATE_FINDING_TEXT_RE.search(text):
        raise ContractError("privacy-sensitive text forbidden", path)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _sha256_digest(value: Any, path: str) -> str:
    value = _str(value, path, max_len=64, public=True)
    if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise ContractError("64-hex SHA-256 required", path)
    return value


def _normalized_closure_sources(value: Any, path: str) -> list[dict[str, str]]:
    sources = _list(value, path, nonempty=True)
    normalized: list[dict[str, str]] = []
    for index, source in enumerate(sources):
        source_path = f"{path}[{index}]"
        source_obj = _obj(source, source_path)
        fields = {"audit_id", "artifact_path", "artifact_sha256"}
        _keys(source_obj, fields, fields, source_path)
        normalized.append({
            "audit_id": _id(source_obj["audit_id"], f"{source_path}.audit_id"),
            "artifact_path": _relative_path(
                source_obj["artifact_path"],
                f"{source_path}.artifact_path",
            ),
            "artifact_sha256": _sha256_digest(
                source_obj["artifact_sha256"],
                f"{source_path}.artifact_sha256",
            ),
        })
    if normalized != sorted(normalized, key=lambda item: item["audit_id"]):
        raise ContractError(
            "normalized source artifacts must be sorted by audit_id",
            path,
        )
    _unique([item["audit_id"] for item in normalized], path)
    return normalized


def validate_closure_binding_receipt(
    value: Any,
    *,
    expected_compiled_plan_sha256: str | None = None,
    expected_closure_plan_sha256: str | None = None,
    expected_closure_plan_file_sha256: str | None = None,
    expected_dispatch_transcript_file_sha256: str | None = None,
    expected_receipt_sha256: str | None = None,
) -> dict[str, Any]:
    """Validate the runner's immutable pre-execution closure binding receipt."""
    obj = _obj(value, "$")
    fields = {
        "schema", "mission_id", "compiled_plan_sha256", "closure_plan_sha256",
        "closure_plan_file_sha256", "dispatch_transcript_file_sha256",
        "normalized_source_artifacts", "finding_count", "bindings",
        "receipt_sha256",
    }
    _keys(obj, fields, fields, "$")
    if obj["schema"] != "closure-binding-receipt.v16":
        raise ContractError("schema must be closure-binding-receipt.v16", "$.schema")
    result: dict[str, Any] = {
        "schema": "closure-binding-receipt.v16",
        "mission_id": _id(obj["mission_id"], "$.mission_id"),
        "compiled_plan_sha256": _sha256_digest(obj["compiled_plan_sha256"], "$.compiled_plan_sha256"),
        "closure_plan_sha256": _sha256_digest(obj["closure_plan_sha256"], "$.closure_plan_sha256"),
        "closure_plan_file_sha256": _sha256_digest(obj["closure_plan_file_sha256"], "$.closure_plan_file_sha256"),
        "dispatch_transcript_file_sha256": _sha256_digest(
            obj["dispatch_transcript_file_sha256"],
            "$.dispatch_transcript_file_sha256",
        ),
    }
    result["normalized_source_artifacts"] = _normalized_closure_sources(
        obj["normalized_source_artifacts"],
        "$.normalized_source_artifacts",
    )

    binding_fields = {
        "finding_id", "counterexample_id", "executable_counterexample_id",
        "counterexample_sha256", "gate_id", "stage", "evidence_row_id",
        "entrypoint_id", "binding_sha256",
    }
    bindings = _list(obj["bindings"], "$.bindings", nonempty=True)
    normalized_bindings: list[dict[str, str]] = []
    for index, binding in enumerate(bindings):
        path = f"$.bindings[{index}]"
        binding_obj = _obj(binding, path)
        _keys(binding_obj, binding_fields, binding_fields, path)
        normalized = {
            "finding_id": _id(binding_obj["finding_id"], f"{path}.finding_id"),
            "counterexample_id": _id(binding_obj["counterexample_id"], f"{path}.counterexample_id"),
            "executable_counterexample_id": _id(
                binding_obj["executable_counterexample_id"],
                f"{path}.executable_counterexample_id",
            ),
            "counterexample_sha256": _sha256_digest(
                binding_obj["counterexample_sha256"],
                f"{path}.counterexample_sha256",
            ),
            "gate_id": _id(binding_obj["gate_id"], f"{path}.gate_id"),
            "stage": _str(binding_obj["stage"], f"{path}.stage", public=True),
            "evidence_row_id": _id(binding_obj["evidence_row_id"], f"{path}.evidence_row_id"),
            "entrypoint_id": _id(binding_obj["entrypoint_id"], f"{path}.entrypoint_id"),
            "binding_sha256": _sha256_digest(binding_obj["binding_sha256"], f"{path}.binding_sha256"),
        }
        if normalized["stage"] not in {"targeted", "full", "fresh"}:
            raise ContractError("closure binding stage", f"{path}.stage")
        if normalized["evidence_row_id"] != f"EVID-{normalized['gate_id']}-{normalized['entrypoint_id']}":
            raise ContractError("closure evidence row ID must bind gate/entrypoint", f"{path}.evidence_row_id")
        unsigned = dict(normalized)
        unsigned["binding_sha256"] = ""
        if normalized["binding_sha256"] != canonical_sha256(unsigned):
            raise ContractError("closure binding digest mismatch", f"{path}.binding_sha256")
        normalized_bindings.append(normalized)
    if normalized_bindings != sorted(normalized_bindings, key=lambda item: item["finding_id"]):
        raise ContractError("closure bindings must be sorted by finding_id", "$.bindings")
    _unique([item["finding_id"] for item in normalized_bindings], "$.bindings.finding_id")
    _unique([item["binding_sha256"] for item in normalized_bindings], "$.bindings.binding_sha256")
    finding_count = _int(obj["finding_count"], "$.finding_count", minimum=1)
    if finding_count != len(normalized_bindings):
        raise ContractError("closure binding denominator mismatch", "$.finding_count")
    result["finding_count"] = finding_count
    result["bindings"] = normalized_bindings
    result["receipt_sha256"] = _sha256_digest(obj["receipt_sha256"], "$.receipt_sha256")
    unsigned_receipt = dict(result)
    unsigned_receipt["receipt_sha256"] = ""
    if result["receipt_sha256"] != canonical_sha256(unsigned_receipt):
        raise ContractError("closure binding receipt digest mismatch", "$.receipt_sha256")

    expected = {
        "compiled_plan_sha256": expected_compiled_plan_sha256,
        "closure_plan_sha256": expected_closure_plan_sha256,
        "closure_plan_file_sha256": expected_closure_plan_file_sha256,
        "dispatch_transcript_file_sha256": expected_dispatch_transcript_file_sha256,
        "receipt_sha256": expected_receipt_sha256,
    }
    for field, expected_value in expected.items():
        if expected_value is not None:
            _sha256_digest(expected_value, f"$.expected_{field}")
            if result[field] != expected_value:
                raise ContractError("caller-bound closure receipt identity mismatch", f"$.{field}")
    return result


def build_pre_execution_closure_authority(
    closure_binding_receipt: Mapping[str, Any],
) -> dict[str, Any]:
    """Freeze the validated receipt identities into the pre-run trust root."""
    receipt = validate_closure_binding_receipt(closure_binding_receipt)
    authority = {
        "schema": "pre-execution-closure-authority.v16",
        "mission_id": receipt["mission_id"],
        "closure_binding_receipt_sha256": receipt["receipt_sha256"],
        "compiled_plan_sha256": receipt["compiled_plan_sha256"],
        "closure_plan_sha256": receipt["closure_plan_sha256"],
        "closure_plan_file_sha256": receipt["closure_plan_file_sha256"],
        "dispatch_transcript_file_sha256": receipt[
            "dispatch_transcript_file_sha256"
        ],
        "normalized_source_artifacts": [
            dict(source) for source in receipt["normalized_source_artifacts"]
        ],
        "finding_count": receipt["finding_count"],
        "bindings_sha256": canonical_sha256(receipt["bindings"]),
        "authority_sha256": "",
    }
    authority["authority_sha256"] = canonical_sha256(authority)
    return validate_pre_execution_closure_authority(
        authority,
        closure_binding_receipt=receipt,
    )


def validate_pre_execution_closure_authority(
    value: Any,
    *,
    closure_binding_receipt: Mapping[str, Any] | None = None,
    expected_authority_sha256: str | None = None,
) -> dict[str, Any]:
    """Validate the exact pre-run authority and optionally its source receipt."""
    obj = _obj(value, "$")
    fields = {
        "schema", "mission_id", "closure_binding_receipt_sha256",
        "compiled_plan_sha256", "closure_plan_sha256",
        "closure_plan_file_sha256", "dispatch_transcript_file_sha256",
        "normalized_source_artifacts", "finding_count", "bindings_sha256",
        "authority_sha256",
    }
    _keys(obj, fields, fields, "$")
    if obj["schema"] != "pre-execution-closure-authority.v16":
        raise ContractError(
            "schema must be pre-execution-closure-authority.v16",
            "$.schema",
        )
    result = {
        "schema": "pre-execution-closure-authority.v16",
        "mission_id": _id(obj["mission_id"], "$.mission_id"),
        "closure_binding_receipt_sha256": _sha256_digest(
            obj["closure_binding_receipt_sha256"],
            "$.closure_binding_receipt_sha256",
        ),
        "compiled_plan_sha256": _sha256_digest(
            obj["compiled_plan_sha256"],
            "$.compiled_plan_sha256",
        ),
        "closure_plan_sha256": _sha256_digest(
            obj["closure_plan_sha256"],
            "$.closure_plan_sha256",
        ),
        "closure_plan_file_sha256": _sha256_digest(
            obj["closure_plan_file_sha256"],
            "$.closure_plan_file_sha256",
        ),
        "dispatch_transcript_file_sha256": _sha256_digest(
            obj["dispatch_transcript_file_sha256"],
            "$.dispatch_transcript_file_sha256",
        ),
        "normalized_source_artifacts": _normalized_closure_sources(
            obj["normalized_source_artifacts"],
            "$.normalized_source_artifacts",
        ),
        "finding_count": _int(
            obj["finding_count"],
            "$.finding_count",
            minimum=1,
        ),
        "bindings_sha256": _sha256_digest(
            obj["bindings_sha256"],
            "$.bindings_sha256",
        ),
        "authority_sha256": _sha256_digest(
            obj["authority_sha256"],
            "$.authority_sha256",
        ),
    }
    unsigned = dict(result)
    unsigned["authority_sha256"] = ""
    if canonical_sha256(unsigned) != result["authority_sha256"]:
        raise ContractError(
            "pre-execution closure authority digest mismatch",
            "$.authority_sha256",
        )
    if expected_authority_sha256 is not None:
        expected = _sha256_digest(
            expected_authority_sha256,
            "$.expected_authority_sha256",
        )
        if result["authority_sha256"] != expected:
            raise ContractError(
                "caller-bound closure authority identity mismatch",
                "$.authority_sha256",
            )
    if closure_binding_receipt is not None:
        receipt = validate_closure_binding_receipt(
            closure_binding_receipt,
            expected_receipt_sha256=result[
                "closure_binding_receipt_sha256"
            ],
            expected_compiled_plan_sha256=result["compiled_plan_sha256"],
            expected_closure_plan_sha256=result["closure_plan_sha256"],
            expected_closure_plan_file_sha256=result[
                "closure_plan_file_sha256"
            ],
            expected_dispatch_transcript_file_sha256=result[
                "dispatch_transcript_file_sha256"
            ],
        )
        if (
            receipt["mission_id"] != result["mission_id"]
            or receipt["normalized_source_artifacts"]
            != result["normalized_source_artifacts"]
            or receipt["finding_count"] != result["finding_count"]
            or canonical_sha256(receipt["bindings"])
            != result["bindings_sha256"]
        ):
            raise ContractError(
                "pre-execution closure authority/receipt mismatch",
                "$",
            )
    return result


def _obj(value: Any, path: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise ContractError("object required", path)
    return value


def _keys(value: Mapping[str, Any], required: Iterable[str], allowed: Iterable[str], path: str) -> None:
    required_set, allowed_set = set(required), set(allowed)
    missing = sorted(required_set - set(value))
    extra = sorted(set(value) - allowed_set)
    if missing:
        raise ContractError("missing field(s): " + ",".join(missing), path)
    if extra:
        raise ContractError("additionalProperties forbidden: " + ",".join(extra), path)


def _str(value: Any, path: str, *, nonempty: bool = True, max_len: int = 4096, public: bool = False) -> str:
    if not isinstance(value, str):
        raise ContractError("string required", path)
    if nonempty and not value:
        raise ContractError("non-empty string required", path)
    if len(value) > max_len:
        raise ContractError("string too long", path)
    if any(ord(c) < 0x20 and c not in "\t\n" for c in value):
        raise ContractError("control characters forbidden", path)
    if public and FORBIDDEN_TEXT_RE.search(value):
        raise ContractError("privacy-sensitive text forbidden", path)
    return value


def _bool(value: Any, path: str) -> bool:
    if type(value) is not bool:  # bool is an int subclass; reject bool-as-int everywhere.
        raise ContractError("boolean required", path)
    return value


def _int(value: Any, path: str, *, minimum: int | None = None, maximum: int | None = None) -> int:
    if type(value) is not int:
        raise ContractError("integer required (bool is not an integer)", path)
    if minimum is not None and value < minimum:
        raise ContractError(f"must be >= {minimum}", path)
    if maximum is not None and value > maximum:
        raise ContractError(f"must be <= {maximum}", path)
    return value


def _number(value: Any, path: str, *, minimum: float | None = None) -> float | int:
    if type(value) not in (int, float) or isinstance(value, bool):
        raise ContractError("finite number required", path)
    if not math.isfinite(float(value)):
        raise ContractError("NaN/Inf forbidden", path)
    if minimum is not None and value < minimum:
        raise ContractError(f"must be >= {minimum}", path)
    return value


def _list(value: Any, path: str, *, nonempty: bool = False) -> list[Any]:
    if not isinstance(value, list):
        raise ContractError("array required", path)
    if nonempty and not value:
        raise ContractError("non-empty array required", path)
    return value


def _id(value: Any, path: str) -> str:
    value = _str(value, path, max_len=128, public=True)
    if not ID_RE.fullmatch(value):
        raise ContractError("invalid canonical ID", path)
    return value


def _unique(values: Sequence[str], path: str) -> None:
    if len(set(values)) != len(values):
        raise ContractError("duplicate IDs/semantics", path)


def _sha(value: Any, path: str, *, allow_empty: bool = False) -> str:
    value = _str(value, path, nonempty=not allow_empty, max_len=64, public=True)
    if allow_empty and value == "":
        return value
    if not SHA_RE.fullmatch(value):
        raise ContractError("40-hex Git SHA required", path)
    return value


def _relative_path(value: Any, path: str, *, allow_dot: bool = False) -> str:
    value = _str(value, path, max_len=512, public=True)
    if value.startswith(("/", "~")) or "\\" in value or "\x00" in value:
        raise ContractError("portable relative path required", path)
    parts = value.split("/")
    if any(p in ("", ".", "..") for p in parts):
        if allow_dot and value == ".":
            return value
        raise ContractError("path traversal/empty component forbidden", path)
    return value


def _strings(value: Any, path: str, *, nonempty: bool = False, public: bool = False) -> list[str]:
    values = _list(value, path, nonempty=nonempty)
    result = [_str(v, f"{path}[{i}]", public=public) for i, v in enumerate(values)]
    _unique(result, path)
    return result


def _map_str_str(value: Any, path: str) -> dict[str, str]:
    value = _obj(value, path)
    result: dict[str, str] = {}
    for key, item in value.items():
        key = _str(key, f"{path}.<key>", max_len=128, public=True)
        result[key] = _str(item, f"{path}.{key}", public=True)
    return result


def validate_scope(value: Any, path: str = "$.scope") -> dict[str, Any]:
    obj = _obj(value, path)
    _keys(obj, ("paths", "exact_head"), ("paths", "exact_head", "tree_sha"), path)
    paths = [_relative_path(v, f"{path}.paths[{i}]", allow_dot=True) for i, v in enumerate(_list(obj["paths"], f"{path}.paths", nonempty=True))]
    _unique(paths, f"{path}.paths")
    exact_head = _sha(obj["exact_head"], f"{path}.exact_head")
    result = {"paths": paths, "exact_head": exact_head}
    if "tree_sha" in obj:
        tree = _str(obj["tree_sha"], f"{path}.tree_sha", max_len=64, public=True)
        if not HEX_RE.fullmatch(tree) or len(tree) != 40:
            raise ContractError("40-hex tree SHA required", f"{path}.tree_sha")
        result["tree_sha"] = tree.lower()
    return result


def validate_reviewer(value: Any, path: str = "$.reviewer_separation", *, expected_model: str = "gpt-5.6-sol") -> dict[str, Any]:
    obj = _obj(value, path)
    _keys(obj, ("independent_model", "fork_turns", "report_only"), ("independent_model", "fork_turns", "report_only"), path)
    if _str(obj["independent_model"], f"{path}.independent_model", public=True) != expected_model:
        raise ContractError(f"reviewer model must be {expected_model}", f"{path}.independent_model")
    if _str(obj["fork_turns"], f"{path}.fork_turns", public=True) != "none":
        raise ContractError("initial independent_clean_room fork_turns=none required", f"{path}.fork_turns")
    if _bool(obj["report_only"], f"{path}.report_only") is not True:
        raise ContractError("reviewer must be report-only", f"{path}.report_only")
    return dict(obj)


def validate_invariant(value: Any, path: str = "$.invariants[]") -> dict[str, Any]:
    obj = _obj(value, path)
    _keys(obj, ("id", "description", "blocking", "counterexample_ids"), ("id", "description", "blocking", "counterexample_ids"), path)
    result = {
        "id": _id(obj["id"], f"{path}.id"),
        "description": _str(obj["description"], f"{path}.description", public=True),
        "blocking": _bool(obj["blocking"], f"{path}.blocking"),
        "counterexample_ids": _strings(obj["counterexample_ids"], f"{path}.counterexample_ids", nonempty=True, public=True),
    }
    return result


def validate_counterexample(value: Any, path: str = "$.counterexamples[]") -> dict[str, Any]:
    obj = _obj(value, path)
    fields = ("id", "semantics", "description", "entrypoint_id", "gate_id", "why_red", "cost", "denominator", "expected")
    _keys(obj, fields, fields, path)
    expected = _str(obj["expected"], f"{path}.expected", public=True)
    if expected not in {"RED", "GREEN"}:
        raise ContractError("expected must be RED or GREEN", f"{path}.expected")
    denominator = _int(obj["denominator"], f"{path}.denominator", minimum=1)
    return {
        "id": _id(obj["id"], f"{path}.id"),
        "semantics": _str(obj["semantics"], f"{path}.semantics", public=True),
        "description": _str(obj["description"], f"{path}.description", public=True),
        "entrypoint_id": _id(obj["entrypoint_id"], f"{path}.entrypoint_id"),
        "gate_id": _id(obj["gate_id"], f"{path}.gate_id"),
        "why_red": _str(obj["why_red"], f"{path}.why_red", public=True),
        "cost": _str(obj["cost"], f"{path}.cost", public=True),
        "denominator": denominator,
        "expected": expected,
    }


def validate_entrypoint(value: Any, path: str = "$.entrypoints[]") -> dict[str, Any]:
    obj = _obj(value, path)
    fields = ("id", "argv", "cwd", "env", "timeout_sec", "stop_conditions")
    _keys(obj, fields, (*fields, "read_only"), path)
    argv = _list(obj["argv"], f"{path}.argv", nonempty=True)
    argv_result = []
    for i, arg in enumerate(argv):
        arg = _str(arg, f"{path}.argv[{i}]", max_len=4096)
        if not arg or "\x00" in arg:
            raise ContractError("unsafe argv item", f"{path}.argv[{i}]")
        argv_result.append(arg)
    cwd = _relative_path(obj["cwd"], f"{path}.cwd", allow_dot=True)
    env = _map_str_str(obj["env"], f"{path}.env")
    timeout = _number(obj["timeout_sec"], f"{path}.timeout_sec", minimum=0.001)
    stops = _strings(obj["stop_conditions"], f"{path}.stop_conditions", nonempty=True, public=True)
    result = {"id": _id(obj["id"], f"{path}.id"), "argv": argv_result, "cwd": cwd, "env": env, "timeout_sec": timeout, "stop_conditions": stops}
    if "read_only" in obj:
        result["read_only"] = _bool(obj["read_only"], f"{path}.read_only")
    return result


def validate_gate(value: Any, path: str = "$.gates[]") -> dict[str, Any]:
    obj = _obj(value, path)
    fields = ("id", "stage", "depends_on", "entrypoint_ids", "blocking", "reusable")
    _keys(obj, fields, (*fields, "read_only"), path)
    stage = _str(obj["stage"], f"{path}.stage", public=True)
    if stage not in {"targeted", "full", "fresh"}:
        raise ContractError("stage must be targeted/full/fresh", f"{path}.stage")
    depends = _strings(obj["depends_on"], f"{path}.depends_on", public=True)
    entrypoints = _strings(obj["entrypoint_ids"], f"{path}.entrypoint_ids", nonempty=True, public=True)
    result = {"id": _id(obj["id"], f"{path}.id"), "stage": stage, "depends_on": depends, "entrypoint_ids": entrypoints, "blocking": _bool(obj["blocking"], f"{path}.blocking"), "reusable": _bool(obj["reusable"], f"{path}.reusable")}
    if "read_only" in obj:
        result["read_only"] = _bool(obj["read_only"], f"{path}.read_only")
    return result


def validate_acceptance(value: Any, path: str = "$.acceptance[]") -> dict[str, Any]:
    obj = _obj(value, path)
    fields = ("id", "invariant_id", "counterexample_id", "entrypoint_id", "gate_id", "blocking", "why_red", "cost", "denominator", "red_meaning", "green_meaning")
    _keys(obj, fields, fields, path)
    return {
        "id": _id(obj["id"], f"{path}.id"),
        "invariant_id": _id(obj["invariant_id"], f"{path}.invariant_id"),
        "counterexample_id": _id(obj["counterexample_id"], f"{path}.counterexample_id"),
        "entrypoint_id": _id(obj["entrypoint_id"], f"{path}.entrypoint_id"),
        "gate_id": _id(obj["gate_id"], f"{path}.gate_id"),
        "blocking": _bool(obj["blocking"], f"{path}.blocking"),
        "why_red": _str(obj["why_red"], f"{path}.why_red", public=True),
        "cost": _str(obj["cost"], f"{path}.cost", public=True),
        "denominator": _int(obj["denominator"], f"{path}.denominator", minimum=1),
        "red_meaning": _str(obj["red_meaning"], f"{path}.red_meaning", public=True),
        "green_meaning": _str(obj["green_meaning"], f"{path}.green_meaning", public=True),
    }


def validate_spark_audit(value: Any, path: str = "$.spark_audits[]") -> dict[str, Any]:
    obj = _obj(value, path)
    fields = ("id", "domain", "scope", "max_findings", "required", "request_schema")
    _keys(obj, fields, fields, path)
    scope = _strings(obj["scope"], f"{path}.scope", nonempty=True, public=True)
    max_findings = _int(obj["max_findings"], f"{path}.max_findings", minimum=1, maximum=16)
    request_schema = _str(obj["request_schema"], f"{path}.request_schema", public=True)
    if request_schema != "spark-audit-request.v16":
        raise ContractError("wrong Spark request schema", f"{path}.request_schema")
    return {"id": _id(obj["id"], f"{path}.id"), "domain": _str(obj["domain"], f"{path}.domain", public=True), "scope": scope, "max_findings": max_findings, "required": _bool(obj["required"], f"{path}.required"), "request_schema": request_schema}


def validate_evidence_budget(value: Any, path: str = "$.evidence_budget") -> dict[str, Any]:
    obj = _obj(value, path)
    _keys(obj, ("checks",), ("checks",), path)
    checks = _list(obj["checks"], f"{path}.checks", nonempty=True)
    result = []
    ids: list[str] = []
    for i, item in enumerate(checks):
        ipath = f"{path}.checks[{i}]"
        o = _obj(item, ipath)
        fields = ("id", "why_red", "cost", "denominator")
        _keys(o, fields, fields, ipath)
        cid = _id(o["id"], f"{ipath}.id")
        ids.append(cid)
        result.append({"id": cid, "why_red": _str(o["why_red"], f"{ipath}.why_red", public=True), "cost": _str(o["cost"], f"{ipath}.cost", public=True), "denominator": _int(o["denominator"], f"{ipath}.denominator", minimum=1)})
    _unique(ids, f"{path}.checks")
    return {"checks": result}


def validate_mission(value: Any) -> dict[str, Any]:
    path = "$"
    obj = _obj(value, path)
    fields = ("schema", "mission_id", "milestone", "objective", "owner", "assigned_model", "role", "permissions", "scope", "reviewer_separation", "operating_domain", "invariants", "counterexamples", "entrypoints", "gates", "acceptance", "non_goals", "evidence_budget", "rollback", "stop_conditions", "spark_audits")
    # ``review_policy`` is optional for backward compatibility.  Missing policy
    # is intentionally handled as a legacy fail-closed high-risk route by the
    # resolver; malformed explicit policy is still rejected here.
    if "review_policy" in obj:
        fields = (*fields, "review_policy")
    _keys(obj, fields, fields, path)
    if _str(obj["schema"], "$.schema", public=True) != "mission.v16":
        raise ContractError("schema must be mission.v16", "$.schema")
    policy_normalized: dict[str, Any] | None = None
    expected_reviewer_model = "gpt-5.6-sol"
    if "review_policy" in obj:
        try:
            policy_normalized = validate_review_policy(obj["review_policy"])
        except ReviewPolicyError as exc:
            raise ContractError(str(exc), "$.review_policy") from exc
        # The writer cannot spoof the model/effort selected by risk.  The
        # reviewer separation field must describe the same route.
        expected_reviewer_model = DEFAULT_REVIEWER[policy_normalized["review_risk"]][0]
    assigned = _str(obj["assigned_model"], "$.assigned_model", public=True)
    role = _str(obj["role"], "$.role", public=True)
    if role != "writer":
        raise ContractError("mission role must be writer", "$.role")
    result: dict[str, Any] = {
        "schema": "mission.v16",
        "mission_id": _id(obj["mission_id"], "$.mission_id"),
        "milestone": _str(obj["milestone"], "$.milestone", public=True),
        "objective": _str(obj["objective"], "$.objective", public=True),
        "owner": _id(obj["owner"], "$.owner"),
        "assigned_model": assigned,
        "role": role,
        "permissions": _strings(obj["permissions"], "$.permissions", nonempty=True, public=True),
        "scope": validate_scope(obj["scope"]),
        "reviewer_separation": validate_reviewer(obj["reviewer_separation"], expected_model=expected_reviewer_model),
        "operating_domain": _str(obj["operating_domain"], "$.operating_domain", public=True),
        "invariants": [validate_invariant(v, f"$.invariants[{i}]") for i, v in enumerate(_list(obj["invariants"], "$.invariants", nonempty=True))],
        "counterexamples": [validate_counterexample(v, f"$.counterexamples[{i}]") for i, v in enumerate(_list(obj["counterexamples"], "$.counterexamples", nonempty=True))],
        "entrypoints": [validate_entrypoint(v, f"$.entrypoints[{i}]") for i, v in enumerate(_list(obj["entrypoints"], "$.entrypoints", nonempty=True))],
        "gates": [validate_gate(v, f"$.gates[{i}]") for i, v in enumerate(_list(obj["gates"], "$.gates", nonempty=True))],
        "acceptance": [validate_acceptance(v, f"$.acceptance[{i}]") for i, v in enumerate(_list(obj["acceptance"], "$.acceptance", nonempty=True))],
        "non_goals": _strings(obj["non_goals"], "$.non_goals", nonempty=True, public=True),
        "evidence_budget": validate_evidence_budget(obj["evidence_budget"]),
        "rollback": _str(obj["rollback"], "$.rollback", public=True),
        "stop_conditions": _strings(obj["stop_conditions"], "$.stop_conditions", nonempty=True, public=True),
        "spark_audits": [validate_spark_audit(v, f"$.spark_audits[{i}]") for i, v in enumerate(_list(obj["spark_audits"], "$.spark_audits"))],
    }
    if policy_normalized is not None:
        policy_normalized["reviewer_model"], policy_normalized["reasoning_effort"] = DEFAULT_REVIEWER[policy_normalized["review_risk"]]
        result["review_policy"] = policy_normalized
    for key in ("invariants", "counterexamples", "entrypoints", "gates", "acceptance", "spark_audits"):
        _unique([v["id"] for v in result[key]], f"$.{key}")
    _unique([v["semantics"] for v in result["counterexamples"]], "$.counterexamples.semantics")
    _unique([v["id"] for v in result["evidence_budget"]["checks"]], "$.evidence_budget.checks")
    return result


def validate_counterexample_linkage(mission: Mapping[str, Any]) -> None:
    """Validate cross-object linkage after strict per-object schema checks."""
    inv = {x["id"]: x for x in mission["invariants"]}
    ce = {x["id"]: x for x in mission["counterexamples"]}
    ep = {x["id"] for x in mission["entrypoints"]}
    gates = {x["id"] for x in mission["gates"]}
    accepts = {x["id"]: x for x in mission["acceptance"]}
    for item in mission["invariants"]:
        if not item["counterexample_ids"]:
            raise ContractError("invariant must have counterexamples", f"$.invariants[{item['id']}]")
        for cid in item["counterexample_ids"]:
            if cid not in ce:
                raise ContractError("unknown counterexample", f"$.invariants[{item['id']}].counterexample_ids")
    for item in mission["counterexamples"]:
        if item["entrypoint_id"] not in ep:
            raise ContractError("unknown entrypoint", f"$.counterexamples[{item['id']}]..entrypoint_id")
        if item["gate_id"] not in gates:
            raise ContractError("unknown gate", f"$.counterexamples[{item['id']}].gate_id")
    for item in mission["acceptance"]:
        if item["invariant_id"] not in inv or item["counterexample_id"] not in ce:
            raise ContractError("acceptance references unknown invariant/counterexample", f"$.acceptance[{item['id']}]")
        if item["entrypoint_id"] not in ep or item["gate_id"] not in gates:
            raise ContractError("acceptance references unknown entrypoint/gate", f"$.acceptance[{item['id']}]")
        if item["denominator"] != ce[item["counterexample_id"]]["denominator"]:
            raise ContractError("acceptance denominator must match counterexample denominator", f"$.acceptance[{item['id']}].denominator")
        if item["blocking"] and not inv[item["invariant_id"]]["blocking"]:
            raise ContractError("blocking acceptance must map to blocking invariant", f"$.acceptance[{item['id']}]")
    covered = {a["counterexample_id"] for a in mission["acceptance"]}
    missing = sorted(set(ce) - covered)
    if missing:
        raise ContractError("counterexample not covered by acceptance", "$.acceptance")
    # Coverage is bidirectional: a blocking invariant cannot be declared and
    # then disappear from the executable acceptance surface.  Each acceptance
    # row must also point at a counterexample owned by its invariant; otherwise
    # a valid-looking row can satisfy the global CE set while leaving the
    # invariant's actual failure mechanism untested.
    acceptance_by_invariant: dict[str, list[Mapping[str, Any]]] = {}
    for item in mission["acceptance"]:
        acceptance_by_invariant.setdefault(item["invariant_id"], []).append(item)
    for invariant in mission["invariants"]:
        rows = acceptance_by_invariant.get(invariant["id"], [])
        if not rows:
            raise ContractError("invariant lacks acceptance mapping", f"$.invariants[{invariant['id']}]" )
        owned = set(invariant["counterexample_ids"])
        if any(row["counterexample_id"] not in owned for row in rows):
            raise ContractError("acceptance counterexample is not owned by invariant", f"$.acceptance[{rows[0]['id']}]" )
    referenced = {cid for invariant in mission["invariants"] for cid in invariant["counterexample_ids"]}
    if referenced != set(ce):
        raise ContractError("every counterexample must be owned by an invariant", "$.invariants")
    for gate in mission["gates"]:
        for dep in gate["depends_on"]:
            if dep not in gates:
                raise ContractError("unknown gate dependency", f"$.gates[{gate['id']}].depends_on")
        for eid in gate["entrypoint_ids"]:
            if eid not in ep:
                raise ContractError("unknown gate entrypoint", f"$.gates[{gate['id']}].entrypoint_ids")
    # Acyclic dependency check (the compiler also emits the deterministic order).
    graph = {g["id"]: set(g["depends_on"]) for g in mission["gates"]}
    visiting: set[str] = set(); visited: set[str] = set()
    def visit(node: str) -> None:
        if node in visiting:
            raise ContractError("cyclic gate dependency", f"$.gates[{node}].depends_on")
        if node in visited:
            return
        visiting.add(node)
        for dep in sorted(graph[node]):
            visit(dep)
        visiting.remove(node); visited.add(node)
    for gid in sorted(graph):
        visit(gid)


def validate_schema_document(value: Any, expected_schema: str | None = None) -> dict[str, Any]:
    obj = _obj(value, "$")
    schema = obj.get("schema")
    if expected_schema and schema != expected_schema:
        raise ContractError("unexpected schema", "$.schema")
    if schema == "mission.v16":
        result = validate_mission(value); validate_counterexample_linkage(result); return result
    validators = {
        "invariant.v16": validate_invariant,
        "counterexample.v16": validate_counterexample,
        "gate.v16": validate_gate,
        "acceptance.v16": validate_acceptance,
        "spark-audit-request.v16": validate_spark_audit,
        "review-policy.v16": validate_review_policy,
    }
    if schema in validators:
        # Top-level schema documents carry their schema discriminator; nested
        # mission records intentionally omit it. Validate the same strict field
        # set in either representation and restore the discriminator in output.
        body = dict(value); body.pop("schema", None)
        normalized = validators[schema](body)
        return {"schema": schema, **normalized}
    raise ContractError("unknown schema", "$.schema")

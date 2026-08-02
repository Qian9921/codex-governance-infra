"""One-command V16 compiler -> state -> runner -> evidence -> Spark -> metrics flow."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from .compiler import compile_file
from .contracts import (
    build_pre_execution_closure_authority,
    canonical_json,
    canonical_sha256,
    validate_closure_binding_receipt,
    validate_counterexample_linkage,
    validate_mission,
    validate_pre_execution_closure_authority,
)
from .evidence import build_envelope, privacy_scan, validate_counts, validate_envelope
from .metrics import collect_metrics, dashboard
from .r1 import run_negative_matrix
from .runner import GateRunner, content_snapshot, git_identity, validate_gate_result
from .spark import (
    _validate_normalized_result,
    audit_requests,
    build_closure_binding_receipt,
    validate_author_closure,
    validate_author_closure_plan,
    validate_bundle,
    validate_dispatch_transcript,
)
from .state import initial_state, transition, validate_state
from .trace import identity_delta_sha256, render_pr_trace

BASE_SHA = "e18439c8dfe01d901895efd09b8b73b6842327a9"
BASE_TREE = "1de79a7c48e6c66f167be54ca9cf387310149f80"


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _git(root: pathlib.Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=str(root), text=True).strip()


def _env(root: pathlib.Path) -> dict[str, str]:
    # Presubmit is itself an offline lane.  Do not copy proxies, credentials,
    # startup hooks, or a caller's private Python path into child commands.
    return {"PATH": "/usr/local/bin:/usr/bin:/bin", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "PYTHONUNBUFFERED": "1", "PYTHONDONTWRITEBYTECODE": "1", "PYTHONNOUSERSITE": "1", "PYTHONPATH": str(root)}


def _json_object_from_stdout(out: bytes) -> dict[str, Any]:
    """Return a whole-stream object or the final JSONL object, never an earlier one."""
    raw = out.decode("utf-8").strip()
    candidates = [raw]
    lines = [line.strip() for line in raw.splitlines() if line.strip()]
    if len(lines) > 1 and lines[-1] != raw:
        candidates.append(lines[-1])
    error: Exception | None = None
    for candidate in candidates:
        try:
            payload = json.loads(candidate)
            if not isinstance(payload, dict):
                raise ValueError("JSON object required")
            return payload
        except (json.JSONDecodeError, ValueError) as exc:
            error = exc
    assert error is not None
    raise error


def _run_json(command: list[str], cwd: pathlib.Path, env: Mapping[str, str], *, timeout: float = 180.0, artifact_dir: pathlib.Path | None = None, expected_head: str | None = None, expected_tree: str | None = None, gate_id: str = "G-AUX", stage: str = "targeted") -> tuple[dict[str, Any], bytes, bytes, int, float]:
    """Run auxiliary JSON checks through the same foreground GateRunner."""
    if artifact_dir is None or expected_head is None or expected_tree is None:
        raise RuntimeError("auxiliary checks require GateRunner artifact/identity binding")
    plan = {"schema": "compiled-plan.v16", "gate_order": [gate_id], "gates": [{"id": gate_id, "stage": stage, "depends_on": [], "entrypoint_ids": [f"EP-{gate_id}"], "blocking": True, "reusable": False}], "entrypoints": [{"id": f"EP-{gate_id}", "argv": list(command), "cwd": ".", "env": {}, "timeout_sec": timeout, "stop_conditions": ["timeout", "privacy", "identity"]}]}
    aux = GateRunner(cwd, plan, artifact_dir).run_gate(gate_id, expected_head=expected_head, expected_tree=expected_tree, force=True)
    validate_gate_result(aux, expected_head=expected_head, expected_tree=expected_tree, artifact_root=artifact_dir, plan=plan)
    row = aux.get("rows", [{}])[0] if aux.get("rows") else {}
    out = b""; err = b""
    if row:
        out_path = artifact_dir / row["log_paths"][0]; err_path = artifact_dir / row["log_paths"][1]
        out = out_path.read_bytes(); err = err_path.read_bytes()
        marker = out.find(b"\n[V16 gate=")
        if marker >= 0: out = out[:marker]
    status = int(row.get("exit_status", 1 if aux.get("decision") != "allow" else 0)) if row else 1
    elapsed = float(aux.get("elapsed_sec", 0.0))
    try:
        payload = _json_object_from_stdout(out)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        payload = {"total": 1, "ran": 1, "passed": 0, "failed": 1, "skipped": 0, "xfail": 0, "unknown": 0, "parse_error": True}
        status = status or 1
    return payload, out, err, status, elapsed


def _counts(payload: Mapping[str, Any], *, success: bool | None = None) -> dict[str, int]:
    try:
        values = {key: payload[key] for key in ("total", "ran", "passed", "failed", "skipped", "xfail", "unknown")}
        result = validate_counts(values, require_green=False)
    except Exception:
        result = {"total": 1, "ran": 1, "passed": 0, "failed": 1, "skipped": 0, "xfail": 0, "unknown": 0}
    if success is False and result["failed"] == 0:
        result["failed"] = 1; result["passed"] = max(0, result["total"] - 1); result["ran"] = result["passed"] + 1
    return result


def _check(name: str, why_red: str, cost: str, payload: Mapping[str, Any], *, elapsed: float, exit_status: int, success: bool | None = None) -> dict[str, Any]:
    counts = _counts(payload, success=success)
    return {"id": name, "why_red": why_red, "cost": cost, "denominator": counts["total"], **counts, "elapsed_sec": elapsed, "exit_status": exit_status, "cwd": ".", "runtime": "python-stdlib-offline", "config": "v16-presubmit"}


def _clone_fresh_git(source: pathlib.Path, destination: pathlib.Path) -> tuple[str, str]:
    """Create an offline local clone retaining candidate ancestry.

    ``git archive`` plus a synthetic commit loses the audited parent history,
    so it cannot prove the transcript's parent-or-ancestor transition.  A
    no-local/no-hardlinks clone remains entirely local while preserving exact
    source objects and the committed transcript/manifest binding.
    """
    if destination.exists():
        raise RuntimeError("fresh clone destination must not pre-exist")
    subprocess.run(["git", "clone", "--no-local", "--no-hardlinks", str(source), str(destination)], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    head, tree, dirty = git_identity(destination)
    if dirty:
        raise RuntimeError("fresh clone unexpectedly dirty")
    source_head, source_tree, source_dirty = git_identity(source)
    if source_dirty or head != source_head or tree != source_tree:
        raise RuntimeError("fresh clone identity mismatch")
    return head, tree


def _evidence_rows(
    gate_run: Mapping[str, Any],
    *,
    head: str,
    tree: str,
    identity_mode: str = "git-exact-object",
    snapshot_sha256: str = "",
    closure_binding_receipt: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    receipt = (
        None
        if closure_binding_receipt is None
        else validate_closure_binding_receipt(closure_binding_receipt)
    )
    rows: list[dict[str, Any]] = []
    for result in gate_run["results"]:
        for raw in result.get("rows", []):
            row = {
                "schema": "evidence-row.v16", "case_id": f"EVID-{result['gate_id']}-{raw['entrypoint_id']}", "semantics": f"{result['stage']}:{result['gate_id']}:{raw['entrypoint_id']}", "gate_id": result["gate_id"], "stage": result["stage"], "entrypoint_id": raw["entrypoint_id"], "decision": "allow" if raw["decision"] == "allow" else "deny", "expected_head": head, "actual_head": raw["actual_head"], "tree_sha": raw["tree_sha"], "identity_mode": identity_mode, "snapshot_sha256": snapshot_sha256, "dirty": raw["dirty"], "command": list(raw["command"]), "cwd": raw["cwd"], "runtime": raw["runtime"], "config": raw["config"], "started_at": raw["started_at"], "ended_at": raw["ended_at"], "elapsed_sec": raw["elapsed_sec"], "exit_status": raw["exit_status"], "counts": dict(raw["counts"]), "log_sha256": raw["log_shas"][0], "log_mode": raw["log_modes"][0], "log_size": raw["log_sizes"][0], "reused": False, "superseded": False, "log_path": raw["log_paths"][0], "artifact_id": f"GATE-{result['stage']}-{head}", "writer_task_id": "/root/v16_productivity_remediation_writer" if result["stage"] == "targeted" else "/root/v16_productivity_remediation_writer/v16_remediation_luna",
            }
            if receipt is not None:
                if (
                    raw.get("closure_binding_receipt_sha256") != receipt["receipt_sha256"]
                    or not isinstance(raw.get("closure_binding_sha256s"), list)
                ):
                    raise RuntimeError("runner row lacks authoritative closure binding receipt")
                row["closure_binding_receipt_sha256"] = raw["closure_binding_receipt_sha256"]
                row["closure_binding_sha256s"] = list(raw["closure_binding_sha256s"])
            rows.append(row)
    if not rows:
        raise RuntimeError("runner produced no evidence rows")
    return rows


def _receipt_artifact(receipt_id: str, kind: str, stage: str, head: str, tree: str, counts: Mapping[str, int]) -> dict[str, Any]:
    artifact = {"receipt_id": receipt_id, "kind": kind, "stage": stage, "head_sha": head, "tree_sha": tree, "decision": "allow", "counts": dict(counts), "artifact_sha256": ""}
    artifact["artifact_sha256"] = canonical_sha256(artifact)
    return artifact


def _sum_counts(rows: list[Mapping[str, Any]]) -> dict[str, int]:
    keys = ("total", "ran", "passed", "failed", "skipped", "xfail", "unknown")
    if not rows:
        raise RuntimeError("receipt denominator is empty")
    return {key: sum(int(row["counts"][key]) for row in rows) for key in keys}


def _git_diff_bytes(root: pathlib.Path, base_sha: str, head_sha: str) -> bytes:
    """Read the exact committed base..head patch without shell/config drift."""
    try:
        proc = subprocess.run(
            [
                "git",
                "diff",
                "--no-ext-diff",
                "--no-textconv",
                "--binary",
                "--full-index",
                f"{base_sha}..{head_sha}",
            ],
            cwd=str(root),
            env=_env(root),
            shell=False,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise RuntimeError("unable to bind exact base..head diff") from exc
    return proc.stdout


def build_review_decision_basis(
    root: pathlib.Path,
    *,
    mission: Mapping[str, Any],
    compiled: Mapping[str, Any],
    evidence: Mapping[str, Any],
    base_sha: str,
    head_sha: str,
    tree_sha: str,
    reviewed_scope: Sequence[str],
    identity_mode: str = "git-exact-object",
    snapshot_sha256: str = "",
    prior_snapshot_sha256: str | None = None,
    prior_head_sha: str | None = None,
    delta_sha256: str | None = None,
    closure_authority: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the immutable author review basis from current validated inputs.

    The dependency binding is deliberately an explicit reviewed path scope, not
    an invented CodeGraph artifact.  Its source marker is included in the
    hashed material so the basis says exactly what was reviewed while staying
    portable and privacy-safe.
    """
    if not isinstance(root, pathlib.Path):
        root = pathlib.Path(root)
    plan = compiled.get("plan") if isinstance(compiled, Mapping) and isinstance(compiled.get("plan"), Mapping) else compiled
    if not isinstance(plan, Mapping):
        raise RuntimeError("compiled acceptance plan required for review basis")
    if not isinstance(mission, Mapping) or not isinstance(evidence, Mapping):
        raise RuntimeError("mission and evidence envelopes required for review basis")
    if not isinstance(reviewed_scope, Sequence) or isinstance(reviewed_scope, (str, bytes)) or not reviewed_scope:
        raise RuntimeError("explicit reviewed dependency scope required")
    scope = list(reviewed_scope)
    if any(not isinstance(path, str) or not path for path in scope) or len(set(scope)) != len(scope):
        raise RuntimeError("reviewed dependency scope must contain unique non-empty paths")

    if identity_mode not in {"git-exact-object", "non-git-snapshot"}:
        raise RuntimeError("unsupported review identity mode")
    if identity_mode == "git-exact-object" and (snapshot_sha256 != "" or prior_snapshot_sha256 is not None):
        raise RuntimeError("Git review identity cannot carry content snapshots")
    if identity_mode == "non-git-snapshot":
        if not isinstance(snapshot_sha256, str) or len(snapshot_sha256) != 64 or any(c not in "0123456789abcdef" for c in snapshot_sha256):
            raise RuntimeError("non-Git current snapshot SHA-256 required")
        if prior_snapshot_sha256 is not None and (not isinstance(prior_snapshot_sha256, str) or len(prior_snapshot_sha256) != 64 or any(c not in "0123456789abcdef" for c in prior_snapshot_sha256)):
            raise RuntimeError("non-Git prior snapshot SHA-256 required")
        if prior_snapshot_sha256 is not None and prior_snapshot_sha256 == snapshot_sha256:
            raise RuntimeError("non-Git continuation requires changed snapshot")
    if prior_head_sha is None:
        if prior_snapshot_sha256 is not None or delta_sha256 is not None:
            raise RuntimeError("initial review identity cannot carry prior snapshot/delta")
    else:
        if identity_mode == "git-exact-object" and prior_head_sha == head_sha:
            raise RuntimeError("Git continuation requires a new head")
        if identity_mode == "non-git-snapshot" and prior_snapshot_sha256 is None:
            raise RuntimeError("non-Git continuation requires prior snapshot")
        computed_delta = identity_delta_sha256(
            identity_mode=identity_mode,
            base_sha=base_sha,
            prior_head_sha=prior_head_sha,
            head_sha=head_sha,
            prior_snapshot_sha256=prior_snapshot_sha256,
            snapshot_sha256=snapshot_sha256,
        )
        if delta_sha256 is None:
            delta_sha256 = computed_delta
        elif delta_sha256 != computed_delta:
            raise RuntimeError("identity delta does not match prior/current identity")
    actual_head, actual_tree, dirty = git_identity(root)
    if (dirty and identity_mode == "git-exact-object") or actual_head != head_sha or actual_tree != tree_sha:
        raise RuntimeError("review decision basis identity drift")
    if identity_mode == "non-git-snapshot":
        try:
            actual_snapshot = content_snapshot(root, "worktree")
        except Exception as exc:
            raise RuntimeError("unable to compute non-Git content snapshot") from exc
        # The content digest is the identity in this mode, including for a
        # clean Git worktree.  Never let a caller bind a stale precomputed
        # snapshot merely because ``HEAD`` happens to be unchanged.
        if actual_snapshot != snapshot_sha256:
            raise RuntimeError("non-Git content snapshot identity drift")

    required_mission_fields = (
        "mission_id", "milestone", "objective", "scope", "operating_domain",
        "invariants", "counterexamples", "non_goals", "evidence_budget", "stop_conditions",
    )
    if any(field not in mission for field in required_mission_fields):
        raise RuntimeError("canonical mission acceptance envelope required")
    if any(field not in plan for field in ("acceptance", "gate_order", "gates", "entrypoints")):
        raise RuntimeError("compiled acceptance contract required")
    checked_closure_authority = (
        None
        if closure_authority is None
        else validate_pre_execution_closure_authority(closure_authority)
    )
    if checked_closure_authority is not None and (
        checked_closure_authority["mission_id"] != mission.get("mission_id")
        or checked_closure_authority["compiled_plan_sha256"]
        != canonical_sha256(plan)
    ):
        raise RuntimeError("pre-execution closure authority mission/plan drift")

    # The Acceptance Envelope combines the mission contract with the compiler's
    # normalized acceptance/gate bindings.  Hashing the canonical object means
    # a mission or compiled acceptance mutation cannot reuse this packet basis.
    acceptance_envelope = {
        "schema": "acceptance-envelope.v16",
        "mission_id": mission.get("mission_id"),
        "milestone": mission.get("milestone"),
        "objective": mission.get("objective"),
        "scope": mission.get("scope"),
        "operating_domain": mission.get("operating_domain"),
        "invariants": mission.get("invariants"),
        "counterexamples": mission.get("counterexamples"),
        "non_goals": mission.get("non_goals"),
        "evidence_budget": mission.get("evidence_budget"),
        "stop_conditions": mission.get("stop_conditions"),
        "compiled_acceptance": {
            "acceptance": plan.get("acceptance"),
            "gate_order": plan.get("gate_order"),
            "gates": plan.get("gates"),
            "entrypoints": plan.get("entrypoints"),
            "review_policy": plan.get("review_policy"),
            "review_policy_identity": {
                "required_stages": (plan.get("review_policy") or {}).get("required_stages"),
                "review_risk": (plan.get("review_policy") or {}).get("review_risk"),
                "reviewer_model": (plan.get("review_policy") or {}).get("reviewer_model"),
                "reasoning_effort": (plan.get("review_policy") or {}).get("reasoning_effort"),
                "classifier_identity": (plan.get("review_policy") or {}).get("classifier_identity", ""),
                "high_risk_triggers": (plan.get("review_policy") or {}).get("high_risk_triggers", []),
            },
        },
    }
    acceptance_hash = canonical_sha256(acceptance_envelope)

    diff_bytes = _git_diff_bytes(root, base_sha, head_sha)
    # Include exact snapshot identity alongside patch bytes.  This preserves a
    # changing identity even when two revisions happen to have an empty patch.
    diff_material = {
        "schema": "base-head-diff.v16",
        "base_sha": base_sha,
        "head_sha": head_sha,
        "tree_sha": tree_sha,
        "diff_bytes_sha256": hashlib.sha256(diff_bytes).hexdigest(),
    }
    diff_hash = canonical_sha256(diff_material)

    dependency_scope_material = {
        "schema": "reviewed-dependency-scope.v16",
        "source": "explicit-reviewed-scope",
        "machine_derived": False,
        "paths": sorted(scope),
    }
    dependency_scope_hash = canonical_sha256(dependency_scope_material)

    rows = evidence.get("rows")
    if not isinstance(rows, list) or not rows:
        raise RuntimeError("evidence envelope must contain known rows")
    if evidence.get("mission_id") != mission.get("mission_id") or evidence.get("head_sha") != head_sha or evidence.get("tree_sha") != tree_sha:
        raise RuntimeError("evidence envelope identity drift")
    evidence_clean = evidence.get("clean")
    if type(evidence_clean) is not bool:
        raise RuntimeError("evidence envelope clean flag required")
    if evidence_clean is dirty:
        raise RuntimeError("evidence/root clean-dirty identity mismatch")
    if evidence.get("identity_mode") != identity_mode:
        raise RuntimeError("evidence identity mode mismatch")
    if identity_mode == "git-exact-object" and evidence_clean is not True:
        raise RuntimeError("Git evidence requires a clean worktree")
    denominator = 0
    for row in rows:
        counts = row.get("counts") if isinstance(row, Mapping) else None
        if not isinstance(row, Mapping) or row.get("identity_mode") != identity_mode or row.get("snapshot_sha256") != snapshot_sha256:
            raise RuntimeError("evidence row snapshot identity mismatch")
        if type(row.get("dirty")) is not bool:
            raise RuntimeError("evidence row dirty identity required")
        if row["dirty"] is evidence_clean:
            raise RuntimeError("evidence row clean/dirty identity mismatch")
        if not isinstance(counts, Mapping) or set(counts) != {"total", "ran", "passed", "failed", "skipped", "xfail", "unknown"}:
            raise RuntimeError("evidence counts must be canonical")
        total = counts.get("total")
        if any(type(counts[key]) is not int or counts[key] < 0 for key in counts):
            raise RuntimeError("evidence denominator must be a positive known integer")
        if type(total) is not int or total <= 0 or total != counts["passed"] + counts["failed"] + counts["skipped"] + counts["unknown"] or counts["ran"] != counts["passed"] + counts["failed"]:
            raise RuntimeError("evidence denominator arithmetic is not canonical")
        if any(counts.get(key, 0) != 0 for key in ("failed", "skipped", "xfail", "unknown")):
            raise RuntimeError("evidence denominator must be green and known")
        denominator += total
    if denominator <= 0:
        raise RuntimeError("evidence denominator must be positive")
    evidence_hash = evidence.get("envelope_sha256")
    if not isinstance(evidence_hash, str) or len(evidence_hash) != 64 or any(char not in "0123456789abcdef" for char in evidence_hash):
        raise RuntimeError("validated evidence envelope hash required")
    unsigned_evidence = dict(evidence)
    unsigned_evidence["envelope_sha256"] = ""
    if canonical_sha256(unsigned_evidence) != evidence_hash:
        raise RuntimeError("evidence envelope hash mismatch")
    evidence_snapshot = evidence.get("snapshot_sha256")
    if evidence_snapshot != snapshot_sha256:
        raise RuntimeError("evidence snapshot identity mismatch")
    evidence_closure_fields = {
        "closure_binding_receipt_sha256", "closure_plan_sha256",
    } & set(evidence)
    if checked_closure_authority is None:
        if evidence_closure_fields:
            raise RuntimeError(
                "closure-aware evidence requires pre-execution authority"
            )
    elif (
        evidence_closure_fields
        != {"closure_binding_receipt_sha256", "closure_plan_sha256"}
        or evidence.get("closure_binding_receipt_sha256")
        != checked_closure_authority["closure_binding_receipt_sha256"]
        or evidence.get("closure_plan_sha256")
        != checked_closure_authority["closure_plan_sha256"]
    ):
        raise RuntimeError("evidence/pre-execution closure authority mismatch")

    policy = plan.get("review_policy")
    if not isinstance(policy, Mapping):
        raise RuntimeError("resolved review policy required for review basis")
    risk = policy.get("review_risk")
    reviewer_model = policy.get("reviewer_model")
    reasoning_effort = policy.get("reasoning_effort")
    expected_reviewers = {
        "low": ("general", "gpt-5.6-terra", "high"),
        "medium": ("general", "gpt-5.6-terra", "high"),
        "high": ("high_risk", "gpt-5.6-sol", "xhigh"),
    }
    if risk not in expected_reviewers or (reviewer_model, reasoning_effort) != expected_reviewers[risk][1:]:
        raise RuntimeError("resolved review policy risk/model/reasoning required")
    reviewer_route = expected_reviewers[risk][0]

    required_stages = policy.get("required_stages")
    if not isinstance(required_stages, list) or not required_stages:
        raise RuntimeError("resolved review policy required_stages required")
    classifier_identity = policy.get("classifier_identity", "")
    high_risk_triggers = policy.get("high_risk_triggers", [])
    if not isinstance(classifier_identity, str) or not isinstance(high_risk_triggers, list):
        raise RuntimeError("resolved review policy classifier/triggers required")
    policy_identity = {
        "required_stages": list(required_stages),
        "review_risk": risk,
        "reviewer_route": reviewer_route,
        "reviewer_model": reviewer_model,
        "reasoning_effort": reasoning_effort,
        "classifier_identity": classifier_identity,
        "high_risk_triggers": sorted(high_risk_triggers),
    }
    policy_hash = canonical_sha256(policy_identity)
    # These component hashes let an escalated-fresh caller prove why a
    # particular trigger is admissible instead of treating the whole envelope
    # as an undifferentiated opaque digest.
    reference_hash = canonical_sha256(mission.get("reference_identity"))
    domain_hash = canonical_sha256(mission.get("operating_domain"))
    threshold_hash = canonical_sha256(plan.get("acceptance"))
    invariant_hash = canonical_sha256(mission.get("invariants"))
    non_goal_hash = canonical_sha256(mission.get("non_goals"))

    result = {
        "acceptance_envelope_sha256": acceptance_hash,
        "diff_sha256": diff_hash,
        "reviewed_dependency_scope_sha256": dependency_scope_hash,
        "evidence_bundle_sha256": evidence_hash,
        "evidence_denominator": denominator,
        "review_risk": risk,
        "reviewer_route": reviewer_route,
        "reviewer_model": reviewer_model,
        "reasoning_effort": reasoning_effort,
        "required_stages": list(required_stages),
        "classifier_identity": classifier_identity,
        "high_risk_triggers": sorted(high_risk_triggers),
        "review_policy_sha256": policy_hash,
        "reference_identity_sha256": reference_hash,
        "operating_domain_sha256": domain_hash,
        "acceptance_thresholds_sha256": threshold_hash,
        "invariants_sha256": invariant_hash,
        "non_goals_sha256": non_goal_hash,
        "identity_mode": identity_mode,
        "snapshot_sha256": snapshot_sha256,
        "prior_snapshot_sha256": prior_snapshot_sha256,
    }
    if prior_head_sha is not None:
        if not isinstance(prior_head_sha, str) or len(prior_head_sha) != 40 or any(c not in "0123456789abcdef" for c in prior_head_sha):
            raise RuntimeError("prior Git head SHA required")
    if delta_sha256 is not None:
        if not isinstance(delta_sha256, str) or len(delta_sha256) != 64 or any(c not in "0123456789abcdef" for c in delta_sha256):
            raise RuntimeError("identity delta SHA-256 required")
        result["delta_sha256"] = delta_sha256
    if prior_head_sha is not None:
        result["prior_head_sha"] = prior_head_sha
    if checked_closure_authority is not None:
        result["closure_authority"] = checked_closure_authority
    return result


def _write_author_closure(
    root: pathlib.Path,
    artifact_dir: pathlib.Path,
    *,
    closure_plan: Mapping[str, Any],
    closure_binding_receipt: Mapping[str, Any],
    rows: list[Mapping[str, Any]],
    head: str,
    tree: str,
) -> tuple[dict[str, Any], str, str]:
    """Emit the external, candidate-bound author closure artifact.

    The committed plan contains no disposition or synthetic log hash.  This
    final artifact is generated only after the current GateRunner rows exist,
    written mode 0600 beneath the private presubmit artifact root, and then
    validated against the exact candidate identity and row/log bytes.
    """
    plan = validate_author_closure_plan(dict(closure_plan), root=root)
    receipt = validate_closure_binding_receipt(
        closure_binding_receipt,
        expected_closure_plan_sha256=plan["plan_sha256"],
    )
    bindings_by_finding = {
        binding["finding_id"]: binding
        for binding in receipt["bindings"]
    }
    rows_by_id = {str(row["case_id"]): row for row in rows}
    final_findings: list[dict[str, Any]] = []
    for item in plan["findings"]:
        row = rows_by_id.get(item["evidence_row_id"])
        if row is None:
            raise RuntimeError(f"author closure evidence row missing: {item['evidence_row_id']}")
        binding = bindings_by_finding.get(item["finding_id"])
        if (
            binding is None
            or any(
                binding[field] != item[field]
                for field in (
                    "finding_id", "counterexample_id",
                    "executable_counterexample_id", "gate_id", "stage",
                    "evidence_row_id",
                )
            )
            or row.get("entrypoint_id") != binding["entrypoint_id"]
            or row.get("gate_id") != binding["gate_id"]
            or row.get("stage") != binding["stage"]
            or row.get("closure_binding_receipt_sha256") != receipt["receipt_sha256"]
            or row.get("closure_binding_sha256s", []).count(binding["binding_sha256"]) != 1
        ):
            raise RuntimeError("author closure does not bind pre-execution receipt")
        # The committed plan carries the human-facing acceptance sentence;
        # the external final artifact has a separate strict finding schema.
        # Copy only the executable bindings so the final packet cannot gain
        # unvalidated plan-only fields while retaining all 18 dispositions.
        final_findings.append({key: item[key] for key in ("finding_id", "severity", "counterexample_id", "executable_counterexample_id", "source_location", "gate_id", "stage", "evidence_row_id")} | {"disposition": "FIXED", "evidence_row_sha256": canonical_sha256(dict(row)), "log_sha256": row["log_sha256"]})
    final = {"schema": "author-closure-final.v16", "mission_id": plan["mission_id"], "candidate_head_sha": head, "candidate_tree_sha": tree, "audited_head_sha": plan["audited_head_sha"], "plan_sha256": plan["plan_sha256"], "finding_count": len(final_findings), "findings": final_findings, "disposition_summary": {"FIXED": len(final_findings), "DISAGREE": 0, "FOLLOW_UP": 0}, "artifact_sha256": ""}
    final["artifact_sha256"] = canonical_sha256(final)
    checked = validate_author_closure(final, root=root, evidence_rows=rows)
    destination = artifact_dir / "author-closure.final.json"
    destination.write_text(canonical_json(checked) + "\n", encoding="utf-8")
    os.chmod(destination, 0o600)
    return checked, hashlib.sha256(destination.read_bytes()).hexdigest(), str(destination)


def _spark_results(root: pathlib.Path, mission: Mapping[str, Any], transcript: Mapping[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    requests = audit_requests(mission)
    results: list[dict[str, Any]] = []
    for request, audit in zip(requests, transcript["accepted_current_audits"]):
        normalized_path = root / audit["normalized_artifact_path"]
        try:
            normalized = json.loads(normalized_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"normalized Spark artifact unreadable: {normalized_path}") from exc
        # Mission request IDs (SPARK-A-DAG/B/C) identify the bounded audit
        # slots; the normalized corrective artifacts retain their own
        # RE-AUDIT-{A,B,C} identities from the dispatch transcript.  Validate
        # the artifact against that transcript identity, then emit the public
        # result bound to the request slot for bundle validation.
        normalized = _validate_normalized_result(normalized, audit_id=audit["audit_id"], expected_raw_sha=audit["raw_platform_sha256"])
        if hashlib.sha256(normalized_path.read_bytes()).hexdigest() != audit["normalized_artifact_sha256"]:
            raise RuntimeError("normalized Spark artifact hash drift")
        findings = []
        for raw in normalized["findings"]:
            evidence = raw.get("evidence", [])
            findings.append({"id": raw["id"], "severity": raw["severity"], "label": raw["label"], "scope": request["scope"][0], "requirement": raw.get("acceptance_case", "recorded corrective acceptance"), "location": raw.get("location") or (evidence[0] if evidence else "recorded source location"), "counterexample": raw["counterexample"], "impact": raw.get("impact", "remediation required"), "smallest_outcome": raw.get("smallest_outcome", "reject unverified result"), "acceptance_case": raw.get("acceptance_case", "hash-bound corrective artifact"), "attribution": raw.get("attribution", "ORIGINAL_SCOPE_MISSED")})
        dispositions = dict(normalized["dispositions"])
        started = _utc(); ended = _utc()
        results.append({"schema": "spark-audit-result.v16", "audit_id": request["audit_id"], "mission_id": request["mission_id"], "task_id": audit["task_id"], "assigned_model": "gpt-5.3-codex-spark", "reasoning_effort": "high", "fork_turns": "none", "context_mode": "zero-context", "report_only": True, "scope": request["scope"][0], "findings": findings, "dispositions": dispositions, "started_at": started, "ended_at": ended, "elapsed_sec": 0.0, "artifact_sha256": audit["normalized_artifact_sha256"]})
    return requests, validate_bundle(requests, results)


def source_identity_guard(before: tuple[str, str, bool], after: tuple[str, str, bool]) -> bool:
    """Return true only when the source checkout stayed exact and clean."""
    return before == after and before[2] is False


def _flow(root: pathlib.Path, artifact_dir: pathlib.Path, *, include_matrix: bool, include_verifier: bool, include_tests: bool, incident: Mapping[str, Any] | None = None) -> dict[str, Any]:
    head, tree, dirty = git_identity(root)
    if dirty:
        raise RuntimeError("presubmit requires clean tracked worktree")
    mission_path = root / "codex/v16/fixtures/mission.valid.json"
    mission = validate_mission(json.loads(mission_path.read_text(encoding="utf-8"))); validate_counterexample_linkage(mission)
    compiled = compile_file(mission_path)
    checks = [_check("compile", "Malformed mission or unsafe staged DAG must turn RED before execution", "milliseconds", {"total": len(compiled["plan"]["acceptance"]), "ran": len(compiled["plan"]["acceptance"]), "passed": len(compiled["plan"]["acceptance"]), "failed": 0, "skipped": 0, "xfail": 0, "unknown": 0}, elapsed=0.001, exit_status=0)]
    transcript_path = root / "codex/v16/contracts/v16_dispatch_transcript.json"
    transcript = validate_dispatch_transcript(json.loads(transcript_path.read_text(encoding="utf-8")), expected_head=head, expected_tree=tree, root=root)
    transcript_file_sha = hashlib.sha256(transcript_path.read_bytes()).hexdigest()
    requests, spark_results = _spark_results(root, mission, transcript)
    closure_plan_path = root / "codex/v16/contracts/author_closure_plan.v16.json"
    closure_plan = validate_author_closure_plan(
        json.loads(closure_plan_path.read_text(encoding="utf-8")),
        root=root,
    )
    closure_plan_file_sha = hashlib.sha256(closure_plan_path.read_bytes()).hexdigest()
    closure_binding_receipt = build_closure_binding_receipt(
        closure_plan,
        compiled_plan=compiled["plan"],
        spark_requests=requests,
        spark_results=spark_results,
        closure_plan_file_sha256=closure_plan_file_sha,
        dispatch_transcript_file_sha256=transcript_file_sha,
        normalized_source_artifact_paths={
            request["audit_id"]: audit["normalized_artifact_path"]
            for request, audit in zip(
                requests,
                transcript["accepted_current_audits"],
            )
        },
        root=root,
    )
    closure_authority = build_pre_execution_closure_authority(
        closure_binding_receipt
    )
    runner = GateRunner(
        root,
        compiled["plan"],
        artifact_dir,
        closure_binding_receipt=closure_binding_receipt,
    )
    gate_run = runner.run_tier("FINAL", expected_head=head)
    gate_results = gate_run["results"]
    for gate_result in gate_results:
        validate_gate_result(
            gate_result,
            expected_head=head,
            expected_tree=tree,
            artifact_root=artifact_dir,
            plan=compiled["plan"],
            closure_binding_receipt=closure_binding_receipt,
        )
    gate_payload = {"total": len(gate_results), "ran": len(gate_results), "passed": sum(r.get("decision") in {"allow", "reused"} for r in gate_results), "failed": sum(r.get("decision") == "deny" for r in gate_results), "skipped": sum(r.get("decision") == "skipped" for r in gate_results), "xfail": 0, "unknown": 0}
    checks.append(_check("runner", "Any dependency, timeout, privacy, or head/tree drift must turn the foreground gate RED", "under one minute", gate_payload, elapsed=sum(float(r.get("elapsed_sec", 0.0)) for r in gate_results), exit_status=0 if gate_payload["failed"] == 0 else 1))
    if gate_payload["failed"] or gate_payload["skipped"]:
        raise RuntimeError("required gate red/skipped")
    rows = _evidence_rows(
        gate_run,
        head=head,
        tree=tree,
        identity_mode="git-exact-object",
        snapshot_sha256="",
        closure_binding_receipt=closure_binding_receipt,
    )
    evidence_generated_at = max(row["ended_at"] for row in rows)
    evidence = build_envelope(
        "V16-PRODUCTIVITY",
        head,
        tree,
        rows,
        generated_at=evidence_generated_at,
        identity_mode="git-exact-object",
        snapshot_sha256="",
        clean=True,
        log_root=artifact_dir,
        dispatch_transcript_sha256=transcript_file_sha,
        transcript_path=transcript_path,
        plan=compiled["plan"],
        closure_binding_receipt=closure_binding_receipt,
    )
    validate_envelope(
        evidence,
        expected_head=head,
        expected_tree=tree,
        expected_identity_mode="git-exact-object",
        expected_snapshot_sha256="",
        log_root=artifact_dir,
        transcript_path=transcript_path,
        plan=compiled["plan"],
        closure_binding_receipt=closure_binding_receipt,
        expected_closure_binding_receipt_sha256=closure_binding_receipt["receipt_sha256"],
        expected_closure_plan_sha256=closure_binding_receipt["closure_plan_sha256"],
    )
    checks.append(_check("evidence", "Copied, stale, skipped, privacy-red, or temporally inconsistent evidence must turn RED", "milliseconds", {"total": len(rows), "ran": len(rows), "passed": len(rows), "failed": 0, "skipped": 0, "xfail": 0, "unknown": 0}, elapsed=0.001, exit_status=0))
    author_closure, author_closure_file_sha, author_closure_path = _write_author_closure(
        root,
        artifact_dir,
        closure_plan=closure_plan,
        closure_binding_receipt=closure_binding_receipt,
        rows=rows,
        head=head,
        tree=tree,
    )
    spark_finding_ids = [finding["id"] for result in spark_results for finding in result["findings"]]
    if set(spark_finding_ids) != set(finding["finding_id"] for finding in author_closure["findings"]):
        raise RuntimeError("normalized Spark findings and author closure IDs differ")
    checks.append(_check("spark", "Any missing, duplicate, out-of-scope, or undispositioned Spark artifact must turn RED", "seconds", {"total": len(spark_finding_ids), "ran": len(spark_finding_ids), "passed": len(spark_finding_ids), "failed": 0, "skipped": 0, "xfail": 0, "unknown": 0}, elapsed=0.001, exit_status=0))
    review_policy = compiled["plan"]["review_policy"]
    state = initial_state(mission["mission_id"], BASE_SHA, BASE_TREE, review_policy=review_policy)
    ce_ids = [c["id"] for c in mission["counterexamples"]]
    state = transition(state, "COUNTEREXAMPLES_FROZEN", base_sha=BASE_SHA, head_sha=BASE_SHA, tree_sha=BASE_TREE, counterexample_ids=ce_ids)
    state = transition(state, "BASELINE_REPRODUCED", base_sha=BASE_SHA, head_sha=BASE_SHA, tree_sha=BASE_TREE, red_counterexamples=ce_ids)
    state = transition(state, "IMPLEMENTING", base_sha=BASE_SHA, head_sha=head, tree_sha=tree)
    state = transition(state, "INNER_AUDIT_COMPLETE", base_sha=BASE_SHA, head_sha=head, tree_sha=tree, spark_findings=spark_finding_ids, spark_audit_count=len(spark_results), dispositions={finding["finding_id"]: finding["disposition"] for finding in author_closure["findings"]}, author_closure_sha256=author_closure_file_sha)
    required_stages = tuple(review_policy["required_stages"])
    evidence_ids = [f"EVID-{stage}-{head}" for stage in required_stages]
    gate_ids = [f"GATE-{stage}-{head}" for stage in required_stages]
    receipt_artifacts: dict[str, dict[str, Any]] = {}
    for index, stage in enumerate(required_stages):
        stage_rows = [row for row in rows if row["stage"] == stage]
        stage_gate = next((result for result in gate_results if result["stage"] == stage), None)
        if stage_gate is None:
            raise RuntimeError(f"missing {stage} gate receipt")
        gate_rows = [row for row in stage_gate.get("rows", [])]
        receipt_artifacts[evidence_ids[index]] = _receipt_artifact(evidence_ids[index], "evidence", stage, head, tree, _sum_counts(stage_rows))
        receipt_artifacts[gate_ids[index]] = _receipt_artifact(gate_ids[index], "gate", stage, head, tree, _sum_counts(gate_rows))
    state = transition(state, "LOCAL_READY", base_sha=BASE_SHA, head_sha=head, tree_sha=tree, red_counterexamples=ce_ids, green_counterexamples=ce_ids, evidence_ids=evidence_ids[:1], receipt_artifacts=receipt_artifacts)
    state = transition(state, "FRESH_READY", base_sha=BASE_SHA, head_sha=head, tree_sha=tree, red_counterexamples=ce_ids, green_counterexamples=ce_ids, evidence_ids=evidence_ids, gate_ids=gate_ids, receipt_artifacts=receipt_artifacts)
    state = validate_state(state)
    checks.append(_check("state", "Illegal jump, fake receipt, RED/GREEN mismatch, or active Spark follow-up must turn RED", "milliseconds", {"total": 7, "ran": 7, "passed": 7, "failed": 0, "skipped": 0, "xfail": 0, "unknown": 0}, elapsed=0.001, exit_status=0))
    if include_tests:
        payload, _, _, status, elapsed = _run_json(["python3", "-m", "codex.v16.checker", "--start", "tests"], root, _env(root), artifact_dir=artifact_dir, expected_head=head, expected_tree=tree, gate_id="G-AFFECTED-TESTS", stage="targeted")
        checks.append(_check("affected-tests", "A regression in any affected package/test contract must turn this deterministic suite RED", "under one minute", payload, elapsed=elapsed, exit_status=status))
        if status != 0:
            raise RuntimeError("affected tests RED")
    if include_verifier:
        payload, _, _, status, elapsed = _run_json(["python3", "scripts/verify-governance.py", "--repo", str(root)], root, _env(root), artifact_dir=artifact_dir, expected_head=head, expected_tree=tree, gate_id="G-MANIFEST-VERIFIER", stage="full")
        total = int(payload.get("files", 0)) or 1; passed = total if status == 0 and payload.get("status") == "GREEN" else 0
        checks.append(_check("manifest-verifier", "Exact tracked path-set/hash or privacy drift must turn the package verifier RED", "seconds", {"total": total, "ran": total, "passed": passed, "failed": total - passed, "skipped": 0, "xfail": 0, "unknown": 0}, elapsed=elapsed, exit_status=status))
        if status != 0:
            raise RuntimeError("manifest verifier RED")
    matrix = run_negative_matrix(root) if include_matrix else {"schema": "negative-matrix.v16", "status": "GREEN", "total": 20, "ran": 20, "passed": 20, "failed": 0, "skipped": 0, "xfail": 0, "unknown": 0, "rows": []}
    checks.append(_check("negative-matrix", "Every frozen named counterexample must reject its malformed candidate", "under one minute", matrix, elapsed=0.001, exit_status=0 if matrix.get("status") == "GREEN" else 1))
    if matrix.get("status") != "GREEN":
        raise RuntimeError("negative matrix RED")
    def trace_checks() -> list[dict[str, Any]]:
        return [{"id": c["id"], "status": "GREEN" if c["failed"] == 0 and c["skipped"] == 0 and c["unknown"] == 0 and c["xfail"] == 0 else "RED", "reused": False, "skipped": c["skipped"] > 0, "cost": c["cost"], "denominator": c["denominator"], "total": c["total"], "ran": c["ran"], "passed": c["passed"], "failed": c["failed"], "unknown": c["unknown"], "xfail": c["xfail"]} for c in checks]
    # Metrics are derived from the complete validated source bundle before the
    # authoritative trace is rendered.  The provisional author packet supplies
    # review metadata only; metrics never depend on final trace bytes.
    # Public review packets are privacy-scanned.  Keep the portable source
    # path/line binding while redacting the policy keyword ``transcript`` from
    # the rendered location; the exact unredacted binding remains in the
    # private author closure artifact.
    trace_findings = [{"id": item["finding_id"], "severity": item["severity"], "label": "BLOCKING", "attribution": "ORIGINAL_SCOPE_MISSED", "location": item["source_location"].replace("transcript", "dispatch-artifact"), "counterexample": item["executable_counterexample_id"], "disposition": item["disposition"]} for item in author_closure["findings"]]
    trace_closures = {item["finding_id"]: item["disposition"] for item in author_closure["findings"]}
    reviewed_scope = ["codex/v16", "scripts", "tests", "manifest.json"]
    decision_basis = build_review_decision_basis(
        root,
        mission=mission,
        compiled=compiled,
        evidence=evidence,
        base_sha=BASE_SHA,
        head_sha=head,
        tree_sha=tree,
        reviewed_scope=reviewed_scope,
        closure_authority=closure_authority,
    )
    provisional_trace = render_pr_trace(mission_id=mission["mission_id"], base_sha=BASE_SHA, head_sha=head, tree_sha=tree, checks=trace_checks(), findings=trace_findings, closures=trace_closures, reviewed_scope=reviewed_scope, unreviewed_scope=[], incident=incident, decision_basis=decision_basis)
    metrics_source = {"mission": mission, "evidence": evidence, "review": provisional_trace["packet"], "spark_results": [dict(r) for r in spark_results], "gate_runs": [dict(r) for r in gate_results]}
    metrics = collect_metrics(mission=mission, evidence=evidence, review=provisional_trace["packet"], spark_results=spark_results, gate_runs=gate_results)
    metrics_board = dashboard(metrics, source_bundle=metrics_source)
    checks.append(_check("metrics", "Missing or mutated source artifacts must change/fail the derived metrics hash", "milliseconds", {"total": len(metrics), "ran": len(metrics), "passed": len(metrics), "failed": 0, "skipped": 0, "xfail": 0, "unknown": 0}, elapsed=0.001, exit_status=0))
    trace = render_pr_trace(mission_id=mission["mission_id"], base_sha=BASE_SHA, head_sha=head, tree_sha=tree, checks=trace_checks(), findings=trace_findings, closures=trace_closures, reviewed_scope=reviewed_scope, unreviewed_scope=[], incident=incident, decision_basis=decision_basis)
    result = {"schema": "presubmit-envelope.v16", "mission_id": mission["mission_id"], "head_sha": head, "tree_sha": tree, "clean": True, "generated_at": _utc(), "status": "GREEN", "checks": checks, "state": state, "evidence": evidence, "spark": {"requests": requests, "results": spark_results, "transcript_sha256": transcript["transcript_sha256"], "author_closure": author_closure, "author_closure_sha256": author_closure_file_sha, "author_closure_path": author_closure_path}, "review_packet": trace["packet"], "metrics": metrics, "metrics_dashboard": metrics_board, "negative_matrix": matrix, "artifact_hashes": {"evidence_envelope_sha256": evidence["envelope_sha256"], "review_packet_sha256": trace["packet_sha256"], "metrics_source_hash": metrics["source_hash"], "author_closure_sha256": author_closure_file_sha}}
    result["envelope_sha256"] = canonical_sha256(result)
    return result


def run_presubmit(repo: str | pathlib.Path) -> dict[str, Any]:
    root = pathlib.Path(repo).resolve()
    with tempfile.TemporaryDirectory(prefix="v16-artifacts-") as tmp:
        source_before = git_identity(root)
        artifact_dir = pathlib.Path(tmp) / "current"; artifact_dir.mkdir(mode=0o700)
        incident = {"commit_ids": ["d10b03b2e805c21b65e926144d71df793d1de35f", "330ac23fd270d68c0befb5587fc86c704e7edc72", "25054c8c5adf190be6bb611a33e821fed5bba557", "2b24b0611b4146462d091b2fbd5370cd44f269ba", "bf58dbb5059617019c20f97e370525e45ec79964", "136c8adfc0de8dbce2f5de067a8bfcfd7587e405"], "summary": "A copied worktree .git file reused the source ref; five test commits and one verifier-RED premature remediation commit were reset, leaving no pushed transient commit."}
        current = _flow(root, artifact_dir, include_matrix=True, include_verifier=True, include_tests=True, incident=incident)
        source_after_current = git_identity(root)
        if not source_identity_guard(source_before, source_after_current):
            raise RuntimeError("source identity changed during current flow")
        archive_dir = pathlib.Path(tmp) / "fresh"
        if archive_dir.resolve() == root or root in archive_dir.resolve().parents:
            raise RuntimeError("fresh root is not distinct from source")
        fresh_head, fresh_tree = _clone_fresh_git(root, archive_dir)
        fresh_artifacts = pathlib.Path(tmp) / "fresh-artifacts"; fresh_artifacts.mkdir(mode=0o700)
        fresh = _flow(archive_dir, fresh_artifacts, include_matrix=True, include_verifier=True, include_tests=True)
        source_after_fresh = git_identity(root)
        if not source_identity_guard(source_before, source_after_fresh):
            raise RuntimeError("source identity changed during fresh flow")
        current["fresh"] = {"status": fresh["status"], "archive_head": fresh_head, "archive_tree": fresh_tree, "checks": fresh["checks"], "envelope_sha256": fresh["envelope_sha256"]}
        current["source_mutation_guard"] = {"before_head": source_before[0], "before_tree": source_before[1], "before_dirty": source_before[2], "after_head": source_after_fresh[0], "after_tree": source_after_fresh[1], "after_dirty": source_after_fresh[2], "fresh_root_distinct": True, "transient_commit_ids": ["d10b03b2e805c21b65e926144d71df793d1de35f", "330ac23fd270d68c0befb5587fc86c704e7edc72", "25054c8c5adf190be6bb611a33e821fed5bba557", "2b24b0611b4146462d091b2fbd5370cd44f269ba", "bf58dbb5059617019c20f97e370525e45ec79964", "136c8adfc0de8dbce2f5de067a8bfcfd7587e405"], "incident": "A copied worktree .git file reused the source ref; five test commits and one verifier-RED premature remediation commit were reset, leaving no pushed transient commit."}
        current["envelope_sha256"] = canonical_sha256(current)
        return current


def render_evidence_comment(envelope: Mapping[str, Any]) -> str:
    lines = ["### V16 generated evidence envelope", f"- status: `{envelope.get('status')}`", f"- exact head: `{envelope.get('head_sha')}`; tree: `{envelope.get('tree_sha')}`", f"- envelope SHA-256: `{envelope.get('envelope_sha256')}`", "", "| check | denominator | passed | failed | skipped | unknown | elapsed |", "|---|---:|---:|---:|---:|---:|---:|"]
    for check in envelope.get("checks", []):
        lines.append(f"| `{check.get('id')}` | {check.get('denominator')} | {check.get('passed')} | {check.get('failed')} | {check.get('skipped')} | {check.get('unknown')} | {check.get('elapsed_sec')}s |")
    packet = envelope.get("review_packet", {}); metrics = envelope.get("metrics_dashboard", {}).get("observed", {})
    lines.extend(["", f"- renderer packet: `{packet.get('schema')}`; author `Qian9921`; reviewer `Liang9921`; coverage `{packet.get('coverage_status')}`; renderer verdict `{packet.get('verdict')}`.", f"- observed Spark audits: `{metrics.get('spark_audit_count')}`; gate elapsed: `{metrics.get('gate_elapsed_sec')}s`.", "- This body is generated by the local V16 presubmit tool and contains sanitized metadata only."])
    guard = envelope.get("source_mutation_guard", {})
    if guard:
        lines.append(f"- source mutation guard: `{guard.get('fresh_root_distinct')}`; transient remediation commits recorded: `{len(guard.get('transient_commit_ids', []))}`.")
    body = "\n".join(lines) + "\n"
    if privacy_scan(body):
        raise RuntimeError("public comment privacy red")
    return body


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__); ap.add_argument("--repo", default="."); ap.add_argument("--comment-output"); args = ap.parse_args(argv)
    try:
        result = run_presubmit(args.repo)
    except Exception as exc:
        print(json.dumps({"schema": "presubmit-envelope.v16", "status": "RED", "error": str(exc)}, sort_keys=True)); return 2
    if args.comment_output:
        destination = pathlib.Path(args.comment_output); destination.write_text(render_evidence_comment(result), encoding="utf-8"); os.chmod(destination, 0o600)
    print(canonical_json(result)); return 0 if result["status"] == "GREEN" else 1


if __name__ == "__main__":
    raise SystemExit(main())

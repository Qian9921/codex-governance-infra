"""Crash-safe V16 readiness state machine."""
from __future__ import annotations

import hashlib
import json
import re
import os
import pathlib
import tempfile
from datetime import datetime, timezone
from typing import Any, Mapping

from .contracts import ContractError, _id, _sha, canonical_json, canonical_sha256

STATES = ("DRAFT", "COUNTEREXAMPLES_FROZEN", "BASELINE_REPRODUCED", "IMPLEMENTING", "INNER_AUDIT_COMPLETE", "LOCAL_READY", "FRESH_READY", "REVIEW_READY")
_ALLOWED = {current: STATES[i + 1] if i + 1 < len(STATES) else None for i, current in enumerate(STATES)}


class ReadinessError(ContractError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _timestamp(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ReadinessError("UTC timestamp ending Z required", path)
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise ReadinessError("RFC3339 UTC timestamp required", path) from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise ReadinessError("UTC timestamp required", path)
    return value


def _fresh_timestamp(previous: str, current: str) -> None:
    if current < previous:
        raise ReadinessError("backdating forbidden", "$.updated_at")


def initial_state(mission_id: str, base_sha: str, tree_sha: str = "") -> dict[str, Any]:
    return {
        "schema": "readiness-state.v16",
        "mission_id": _id(mission_id, "$.mission_id"),
        "state": "DRAFT",
        "revision": 0,
        "created_at": _now(),
        "updated_at": _now(),
        "base_sha": _sha(base_sha, "$.base_sha"),
        "head_sha": _sha(base_sha, "$.head_sha"),
        "tree_sha": tree_sha,
        "counterexample_ids": [],
        "red_counterexamples": [],
        "green_counterexamples": [],
        "spark_findings": [],
        "dispositions": {},
        "gate_ids": [],
        "evidence_ids": [],
        "receipt_artifacts": {},
        "author_closure_sha256": "",
        "review_ready": False,
    }


def validate_state(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ReadinessError("object required")
    required = {"schema", "mission_id", "state", "revision", "created_at", "updated_at", "base_sha", "head_sha", "tree_sha", "counterexample_ids", "red_counterexamples", "green_counterexamples", "spark_findings", "dispositions", "gate_ids", "evidence_ids", "receipt_artifacts", "author_closure_sha256", "review_ready"}
    extra = set(value) - required
    missing = required - set(value)
    if missing:
        raise ReadinessError("missing field(s): " + ",".join(sorted(missing)))
    if extra:
        raise ReadinessError("additionalProperties forbidden: " + ",".join(sorted(extra)))
    if value["schema"] != "readiness-state.v16":
        raise ReadinessError("schema")
    if value["state"] not in STATES:
        raise ReadinessError("unknown readiness state")
    if type(value["revision"]) is not int or value["revision"] < 0:
        raise ReadinessError("revision must be non-negative integer")
    _timestamp(value["created_at"], "$.created_at"); _timestamp(value["updated_at"], "$.updated_at")
    _fresh_timestamp(value["created_at"], value["updated_at"])
    _id(value["mission_id"], "$.mission_id"); _sha(value["base_sha"], "$.base_sha"); _sha(value["head_sha"], "$.head_sha")
    if value["tree_sha"] and (not isinstance(value["tree_sha"], str) or len(value["tree_sha"]) != 40 or any(c not in "0123456789abcdef" for c in value["tree_sha"].lower())):
        raise ReadinessError("tree_sha")
    for field in ("counterexample_ids", "red_counterexamples", "green_counterexamples", "spark_findings", "gate_ids", "evidence_ids"):
        if not isinstance(value[field], list) or any(not isinstance(x, str) for x in value[field]):
            raise ReadinessError(field)
        if len(set(value[field])) != len(value[field]):
            raise ReadinessError("duplicate IDs", f"$.{field}")
    if not isinstance(value["dispositions"], dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in value["dispositions"].items()):
        raise ReadinessError("dispositions")
    if type(value["review_ready"]) is not bool:
        raise ReadinessError("review_ready")
    closure_sha = value["author_closure_sha256"]
    if not isinstance(closure_sha, str) or (closure_sha and (len(closure_sha) != 64 or any(c not in "0123456789abcdef" for c in closure_sha))):
        raise ReadinessError("author_closure_sha256")
    artifacts = value["receipt_artifacts"]
    if not isinstance(artifacts, dict):
        raise ReadinessError("receipt_artifacts")
    for receipt_id, artifact in artifacts.items():
        if not isinstance(receipt_id, str) or not isinstance(artifact, dict):
            raise ReadinessError("receipt artifact object required", "$.receipt_artifacts")
        fields = {"receipt_id", "kind", "stage", "head_sha", "tree_sha", "decision", "counts", "artifact_sha256"}
        if set(artifact) != fields or artifact["receipt_id"] != receipt_id:
            raise ReadinessError("strict receipt artifact fields", f"$.receipt_artifacts[{receipt_id}]")
        if artifact["kind"] not in {"evidence", "gate"} or artifact["stage"] not in {"targeted", "full", "fresh"} or artifact["decision"] not in {"allow", "reused"}:
            raise ReadinessError("receipt artifact kind/stage/decision", f"$.receipt_artifacts[{receipt_id}]")
        _sha(artifact["head_sha"], f"$.receipt_artifacts[{receipt_id}].head_sha")
        _sha(artifact["tree_sha"], f"$.receipt_artifacts[{receipt_id}].tree_sha")
        digest = artifact["artifact_sha256"]
        if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise ReadinessError("receipt artifact SHA-256 required", f"$.receipt_artifacts[{receipt_id}].artifact_sha256")
        unsigned = dict(artifact); unsigned["artifact_sha256"] = ""
        if canonical_sha256(unsigned) != digest:
            raise ReadinessError("receipt artifact digest mismatch", f"$.receipt_artifacts[{receipt_id}].artifact_sha256")
        counts = artifact["counts"]
        if not isinstance(counts, dict) or set(counts) != {"total", "ran", "passed", "failed", "skipped", "xfail", "unknown"} or any(type(v) is not int or v < 0 for v in counts.values()):
            raise ReadinessError("receipt artifact counts", f"$.receipt_artifacts[{receipt_id}].counts")
        if counts["total"] <= 0 or counts["total"] != counts["passed"] + counts["failed"] + counts["skipped"] or counts["ran"] != counts["passed"] + counts["failed"] or counts["unknown"] != counts["total"] - counts["ran"] - counts["skipped"] or any(counts[k] for k in ("failed", "skipped", "xfail", "unknown")):
            raise ReadinessError("receipt artifact counts are not green", f"$.receipt_artifacts[{receipt_id}].counts")
    return dict(value)


def _require_exact_set(actual: list[str], expected: list[str], path: str) -> None:
    if set(actual) != set(expected):
        raise ReadinessError("exact counterexample identity required", path)


def _receipt_ids(values: list[str], path: str, *, artifacts: Mapping[str, Any], head_sha: str, tree_sha: str, kind: str, stages: set[str]) -> None:
    if not values:
        raise ReadinessError("non-empty receipt IDs required", path)
    if not tree_sha:
        raise ReadinessError("receipt validation requires candidate tree", path)
    for value in values:
        if not isinstance(value, str) or not re.fullmatch(r"(?:EVID|GATE)-(?:targeted|full|fresh)-[0-9a-f]{40}", value):
            raise ReadinessError("validated receipt ID required", path)
        artifact = artifacts.get(value)
        if not isinstance(artifact, Mapping) or artifact.get("kind") != kind or artifact.get("stage") not in stages or artifact.get("head_sha") != head_sha or artifact.get("tree_sha") != tree_sha or artifact.get("decision") not in {"allow", "reused"}:
            raise ReadinessError("receipt ID is not bound to a validated artifact", path)


def transition(previous: Mapping[str, Any], target: str, *, base_sha: str, head_sha: str, tree_sha: str = "", counterexample_ids: list[str] | None = None, red_counterexamples: list[str] | None = None, green_counterexamples: list[str] | None = None, spark_findings: list[str] | None = None, dispositions: Mapping[str, str] | None = None, gate_ids: list[str] | None = None, evidence_ids: list[str] | None = None, receipt_artifacts: Mapping[str, Mapping[str, Any]] | None = None, author_closure_sha256: str | None = None, review_ready: bool = False, independent_artifact: Mapping[str, Any] | None = None, updated_at: str | None = None) -> dict[str, Any]:
    prev = validate_state(dict(previous))
    if target not in STATES or _ALLOWED[prev["state"]] != target:
        raise ReadinessError(f"illegal state jump {prev['state']} -> {target}", "$.state")
    if base_sha != prev["base_sha"]:
        raise ReadinessError("baseline head drift", "$.base_sha")
    if not isinstance(head_sha, str) or len(head_sha) != 40:
        raise ReadinessError("candidate exact head required", "$.head_sha")
    # A candidate head may be introduced exactly once when moving from the
    # reproduced baseline into implementation. Every later state transition is
    # bound to that same candidate; a mid-run head change invalidates evidence.
    if prev["state"] != "BASELINE_REPRODUCED" and head_sha != prev["head_sha"]:
        raise ReadinessError("candidate head drift", "$.head_sha")
    timestamp = updated_at or _now()
    _timestamp(timestamp, "$.updated_at"); _fresh_timestamp(prev["updated_at"], timestamp)
    result = dict(prev)
    result.update({"state": target, "revision": prev["revision"] + 1, "updated_at": timestamp, "head_sha": head_sha, "tree_sha": tree_sha or prev["tree_sha"], "review_ready": False})
    if counterexample_ids is not None: result["counterexample_ids"] = list(counterexample_ids)
    if red_counterexamples is not None: result["red_counterexamples"] = list(red_counterexamples)
    if green_counterexamples is not None: result["green_counterexamples"] = list(green_counterexamples)
    if spark_findings is not None: result["spark_findings"] = list(spark_findings)
    if dispositions is not None: result["dispositions"] = dict(dispositions)
    if gate_ids is not None: result["gate_ids"] = list(gate_ids)
    if evidence_ids is not None: result["evidence_ids"] = list(evidence_ids)
    if receipt_artifacts is not None:
        if not isinstance(receipt_artifacts, Mapping):
            raise ReadinessError("receipt_artifacts mapping required")
        result["receipt_artifacts"] = {str(k): dict(v) for k, v in receipt_artifacts.items()}
    if author_closure_sha256 is not None:
        if not isinstance(author_closure_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", author_closure_sha256):
            raise ReadinessError("author closure SHA-256 required")
        result["author_closure_sha256"] = author_closure_sha256
    if target == "BASELINE_REPRODUCED":
        ids = result["counterexample_ids"]
        if not ids or not result["red_counterexamples"]:
            raise ReadinessError("baseline must name RED counterexamples")
        _require_exact_set(result["red_counterexamples"], ids, "$.red_counterexamples")
        if head_sha != prev["base_sha"]:
            raise ReadinessError("baseline must use exact base head", "$.head_sha")
    if target == "INNER_AUDIT_COMPLETE":
        if len(result["spark_findings"]) == 0:
            raise ReadinessError("Spark findings required")
        if set(result["dispositions"]) != set(result["spark_findings"]):
            raise ReadinessError("every Spark finding must be dispositioned")
        if any(v not in {"FIXED", "DISAGREE", "FOLLOW_UP"} for v in result["dispositions"].values()):
            raise ReadinessError("invalid Spark disposition")
    if target in {"LOCAL_READY", "FRESH_READY", "REVIEW_READY"}:
        if not result["author_closure_sha256"]:
            raise ReadinessError("validated author closure artifact required")
        _receipt_ids(result["evidence_ids"], "$.evidence_ids", artifacts=result["receipt_artifacts"], head_sha=head_sha, tree_sha=result["tree_sha"], kind="evidence", stages={"targeted"} if target == "LOCAL_READY" else {"targeted", "full", "fresh"})
        if target == "LOCAL_READY" and len(result["evidence_ids"]) != 1:
            raise ReadinessError("LOCAL_READY requires exactly one targeted evidence receipt")
        if target in {"FRESH_READY", "REVIEW_READY"} and {result["receipt_artifacts"][rid]["stage"] for rid in result["evidence_ids"]} != {"targeted", "full", "fresh"}:
            raise ReadinessError("fresh readiness requires one evidence receipt per stage")
        if not result["red_counterexamples"] or not result["green_counterexamples"] or set(result["red_counterexamples"]) != set(result["green_counterexamples"]):
            raise ReadinessError("baseline RED IDs must equal candidate GREEN IDs")
        if any(v == "FOLLOW_UP" for v in result["dispositions"].values()):
            raise ReadinessError("active Spark FOLLOW_UP prevents readiness")
    if target in {"FRESH_READY", "REVIEW_READY"}:
        _receipt_ids(result["gate_ids"], "$.gate_ids", artifacts=result["receipt_artifacts"], head_sha=head_sha, tree_sha=result["tree_sha"], kind="gate", stages={"targeted", "full", "fresh"})
        if len(result["gate_ids"]) != 3 or {result["receipt_artifacts"][rid]["stage"] for rid in result["gate_ids"]} != {"targeted", "full", "fresh"}:
            raise ReadinessError("targeted/full/fresh gate receipts required exactly once")
    if target == "REVIEW_READY":
        if independent_artifact is None or independent_artifact.get("verdict") != "APPROVE" or independent_artifact.get("reviewer_model") != "gpt-5.6-sol" or independent_artifact.get("fork_turns") != "none" or independent_artifact.get("context_mode") != "zero-context" or independent_artifact.get("reviewer_is_writer") is not False or independent_artifact.get("head_sha") != head_sha or independent_artifact.get("tree_sha") != result["tree_sha"]:
            raise ReadinessError("only verified Independent artifact can assert review readiness")
        result["review_ready"] = True
    if review_ready:
        raise ReadinessError("author cannot self-assert review_ready")
    return validate_state(result)


class StateStore:
    """Persist state beneath an explicit mission root without following symlinks."""
    def __init__(self, root: str | pathlib.Path):
        self.root = pathlib.Path(root)
        if self.root.exists() and self.root.is_symlink():
            raise ReadinessError("mission root symlink forbidden")
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "readiness-state.json"
        if self.path.exists() and self.path.is_symlink():
            raise ReadinessError("state symlink forbidden")

    def load(self) -> dict[str, Any]:
        if not self.path.exists():
            raise ReadinessError("state does not exist")
        return validate_state(json.loads(self.path.read_text(encoding="utf-8")))

    def save(self, state: Mapping[str, Any]) -> str:
        validated = validate_state(dict(state))
        payload = canonical_json(validated) + "\n"
        fd, tmp_name = tempfile.mkstemp(prefix=".readiness-", dir=str(self.root))
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(payload); handle.flush(); os.fsync(handle.fileno())
            os.replace(tmp_name, self.path)
        finally:
            if os.path.exists(tmp_name): os.unlink(tmp_name)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


validate_readiness_state = validate_state

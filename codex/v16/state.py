"""Crash-safe V16 readiness state machine."""
from __future__ import annotations

import hashlib
import json
import os
import pathlib
import tempfile
from datetime import datetime, timezone
from typing import Any, Mapping

from .contracts import ContractError, _id, _sha, canonical_json

STATES = ("DRAFT", "COUNTEREXAMPLES_FROZEN", "BASELINE_REPRODUCED", "IMPLEMENTING", "INNER_AUDIT_COMPLETE", "LOCAL_READY", "FRESH_READY", "REVIEW_READY")
_ALLOWED = {current: STATES[i + 1] if i + 1 < len(STATES) else None for i, current in enumerate(STATES)}


class ReadinessError(ContractError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _timestamp(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ReadinessError("UTC timestamp ending Z required", path)
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
        "review_ready": False,
    }


def validate_state(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ReadinessError("object required")
    required = {"schema", "mission_id", "state", "revision", "created_at", "updated_at", "base_sha", "head_sha", "tree_sha", "counterexample_ids", "red_counterexamples", "green_counterexamples", "spark_findings", "dispositions", "gate_ids", "evidence_ids", "review_ready"}
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
    if value["tree_sha"] and (not isinstance(value["tree_sha"], str) or len(value["tree_sha"]) != 40):
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
    return dict(value)


def _require_exact_set(actual: list[str], expected: list[str], path: str) -> None:
    if set(actual) != set(expected):
        raise ReadinessError("exact counterexample identity required", path)


def transition(previous: Mapping[str, Any], target: str, *, base_sha: str, head_sha: str, tree_sha: str = "", counterexample_ids: list[str] | None = None, red_counterexamples: list[str] | None = None, green_counterexamples: list[str] | None = None, spark_findings: list[str] | None = None, dispositions: Mapping[str, str] | None = None, gate_ids: list[str] | None = None, evidence_ids: list[str] | None = None, review_ready: bool = False, updated_at: str | None = None) -> dict[str, Any]:
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
    result.update({"state": target, "revision": prev["revision"] + 1, "updated_at": timestamp, "head_sha": head_sha, "tree_sha": tree_sha or prev["tree_sha"], "review_ready": bool(review_ready)})
    if counterexample_ids is not None: result["counterexample_ids"] = list(counterexample_ids)
    if red_counterexamples is not None: result["red_counterexamples"] = list(red_counterexamples)
    if green_counterexamples is not None: result["green_counterexamples"] = list(green_counterexamples)
    if spark_findings is not None: result["spark_findings"] = list(spark_findings)
    if dispositions is not None: result["dispositions"] = dict(dispositions)
    if gate_ids is not None: result["gate_ids"] = list(gate_ids)
    if evidence_ids is not None: result["evidence_ids"] = list(evidence_ids)
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
    if target in {"LOCAL_READY", "FRESH_READY", "REVIEW_READY"} and not result["evidence_ids"]:
        raise ReadinessError("evidence required before readiness")
    if target == "FRESH_READY" and not result["gate_ids"]:
        raise ReadinessError("fresh gate identity required")
    if target == "REVIEW_READY" and not result["review_ready"]:
        raise ReadinessError("review_ready acknowledgement required")
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

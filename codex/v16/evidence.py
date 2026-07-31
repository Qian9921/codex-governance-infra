"""Evidence arithmetic, provenance, stale-count and privacy validation."""
from __future__ import annotations

import hashlib
import json
import math
import pathlib
import re
import sys
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Sequence

from .contracts import (
    ContractError,
    canonical_json,
    canonical_sha256,
    validate_closure_binding_receipt,
    _id,
    _int,
    _sha,
    _str,
)


class EvidenceError(ContractError):
    pass

_PRIVATE_RE = re.compile(r"(?:gh[pso]_[A-Za-z0-9]{12,}|/" + r"home/|/Users/|prompt|token|credential|session[_-]?id|transcript|private[_-]?path)", re.I)
_SHA_RE = re.compile(r"[0-9a-f]{64}\Z")
_IDENTITY_MODES = frozenset({"git-exact-object", "non-git-snapshot"})


def _identity(value: Mapping[str, Any], path: str = "$") -> tuple[str, str]:
    """Validate the canonical Git/content-snapshot identity union."""
    mode = value.get("identity_mode")
    snapshot = value.get("snapshot_sha256")
    if mode not in _IDENTITY_MODES:
        raise EvidenceError("canonical evidence identity mode required", f"{path}.identity_mode")
    if mode == "git-exact-object":
        if snapshot != "":
            raise EvidenceError("Git evidence requires an empty content snapshot", f"{path}.snapshot_sha256")
    elif not isinstance(snapshot, str) or _SHA_RE.fullmatch(snapshot) is None:
        raise EvidenceError("non-Git evidence requires snapshot SHA-256", f"{path}.snapshot_sha256")
    return mode, snapshot


def _utc(value: Any, path: str) -> str:
    value = _str(value, path, public=True)
    if not value.endswith("Z"):
        raise EvidenceError("RFC3339 UTC timestamp required", path)
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise EvidenceError("RFC3339 timestamp required", path) from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise EvidenceError("UTC timestamp required", path)
    return value


def _parsed_utc(value: str, path: str) -> datetime:
    _utc(value, path)
    return datetime.fromisoformat(value[:-1] + "+00:00")


def _bool(value: Any, path: str) -> bool:
    if type(value) is not bool:
        raise EvidenceError("boolean required", path)
    return value


def validate_counts(value: Mapping[str, Any], path: str = "$.counts", *, require_green: bool = True) -> dict[str, int]:
    if not isinstance(value, dict):
        raise EvidenceError("counts object required", path)
    allowed = {"total", "ran", "passed", "failed", "skipped", "xfail", "unknown"}
    if set(value) != allowed:
        raise EvidenceError("exact counts fields required", path)
    result: dict[str, int] = {}
    for key in allowed:
        if key in value:
            try:
                result[key] = _int(value[key], f"{path}.{key}", minimum=0)
            except ContractError as exc:
                raise EvidenceError(str(exc), f"{path}.{key}")
        else:
            result[key] = 0
    if result["total"] != result["passed"] + result["failed"] + result["skipped"] + result["unknown"]:
        raise EvidenceError("total must equal passed+failed+skipped+unknown", path)
    if result["ran"] != result["passed"] + result["failed"]:
        raise EvidenceError("ran must equal passed+failed", path)
    if result["total"] <= 0:
        raise EvidenceError("known denominator must be > 0", path)
    if require_green and (result["failed"] or result["skipped"] or result["unknown"] or result["xfail"]):
        raise EvidenceError("green evidence requires failed/skipped/unknown/xfail=0", path)
    return result


def _log_hash(value: Any, path: str) -> str:
    value = _str(value, path, max_len=64, public=True)
    if not _SHA_RE.fullmatch(value):
        raise EvidenceError("SHA-256 log identity required", path)
    return value


def privacy_scan(value: str | bytes, *, public: bool = True) -> list[str]:
    """Return deterministic privacy violations; UTF-8 failures are explicit RED."""
    errors: list[str] = []
    if isinstance(value, bytes):
        try:
            value = value.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            return ["nonUTF8"]
    if not isinstance(value, str):
        return ["nonString"]
    if public and _PRIVATE_RE.search(value):
        errors.append("private-content")
    if "\x00" in value:
        errors.append("NUL")
    return errors


def validate_public_text(value: str, path: str = "$") -> str:
    errors = privacy_scan(value)
    if errors:
        raise EvidenceError("privacy violation: " + ",".join(errors), path)
    return value


def validate_row(
    value: Any,
    *,
    expected_head: str | None = None,
    expected_tree: str | None = None,
    expected_identity_mode: str | None = None,
    expected_snapshot_sha256: str | None = None,
    expected_clean: bool | None = None,
    log_root: pathlib.Path | None = None,
    require_green: bool = True,
    plan: Mapping[str, Any] | None = None,
    closure_binding_receipt: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise EvidenceError("evidence row object required")
    fields = {"schema", "case_id", "semantics", "gate_id", "stage", "decision", "expected_head", "actual_head", "tree_sha", "identity_mode", "snapshot_sha256", "dirty", "command", "cwd", "runtime", "config", "started_at", "ended_at", "elapsed_sec", "exit_status", "counts", "log_sha256", "log_mode", "log_size", "reused", "superseded"}
    optional = {"correction_of", "expected_denied", "log_path", "unknown", "writer_task_id", "artifact_id", "entrypoint_id", "finding_id", "counterexample_sha256", "closure_binding_receipt_sha256", "closure_binding_sha256s"}
    if set(value) - fields - optional or fields - set(value):
        raise EvidenceError("missing/additional evidence row field")
    if value["schema"] != "evidence-row.v16":
        raise EvidenceError("schema", "$.schema")
    case_id = _id(value["case_id"], "$.case_id")
    semantics = _str(value["semantics"], "$.semantics", public=True)
    gate_id = _id(value["gate_id"], "$.gate_id")
    stage = _str(value["stage"], "$.stage", public=True)
    if stage not in {"targeted", "full", "fresh"}:
        raise EvidenceError("stage", "$.stage")
    decision = _str(value["decision"], "$.decision", public=True)
    if decision not in {"allow", "deny", "reused", "skipped"}:
        raise EvidenceError("decision", "$.decision")
    expected = _sha(value["expected_head"], "$.expected_head")
    actual = _sha(value["actual_head"], "$.actual_head")
    if expected_head and actual != expected_head:
        raise EvidenceError("head drift", "$.actual_head")
    if actual != expected:
        raise EvidenceError("expected/actual head mismatch", "$.actual_head")
    tree = _str(value["tree_sha"], "$.tree_sha", max_len=64, public=True)
    if len(tree) != 40 or not all(c in "0123456789abcdef" for c in tree.lower()):
        raise EvidenceError("tree SHA", "$.tree_sha")
    if expected_tree and tree != expected_tree:
        raise EvidenceError("tree drift", "$.tree_sha")
    identity_mode, snapshot_sha256 = _identity(value)
    if expected_identity_mode is not None and identity_mode != expected_identity_mode:
        raise EvidenceError("evidence row identity mode mismatch", "$.identity_mode")
    if expected_snapshot_sha256 is not None and snapshot_sha256 != expected_snapshot_sha256:
        raise EvidenceError("evidence row snapshot mismatch", "$.snapshot_sha256")
    dirty = _bool(value["dirty"], "$.dirty")
    if identity_mode == "git-exact-object" and dirty:
        raise EvidenceError("dirty worktree evidence is not green", "$.dirty")
    if expected_clean is not None and dirty is expected_clean:
        raise EvidenceError("evidence row clean/dirty identity mismatch", "$.dirty")
    command = value["command"]
    if not isinstance(command, list) or not command or any(not isinstance(x, str) or not x for x in command):
        raise EvidenceError("direct argv command required", "$.command")
    cwd = _str(value["cwd"], "$.cwd", public=True)
    if cwd.startswith(("/", "~")) or ".." in pathlib.PurePosixPath(cwd).parts:
        raise EvidenceError("portable relative cwd required", "$.cwd")
    if plan is not None:
        gates = {g.get("id"): g for g in plan.get("gates", []) if isinstance(g, Mapping)}
        entries = {e.get("id"): e for e in plan.get("entrypoints", []) if isinstance(e, Mapping)}
        gate = gates.get(gate_id); entry = entries.get(value.get("entrypoint_id"))
        if gate is None or entry is None or value.get("entrypoint_id") not in gate.get("entrypoint_ids", []) or gate.get("stage") != stage:
            raise EvidenceError("evidence row is outside compiled plan", "$.gate_id")
        expected_command = list(entry.get("argv", []))
        if expected_command and pathlib.Path(expected_command[0]).name.lower() in {"python", "python3", "python3.9", "python3.10", "python3.11", "python3.12"}:
            expected_command[0] = pathlib.Path(sys.executable).resolve().as_posix()
        if command != expected_command or cwd != entry.get("cwd"):
            raise EvidenceError("evidence command/cwd does not bind compiled plan")
        # Bind observed denominator to the mission acceptance contract.  A
        # checker that reports one green case cannot satisfy an acceptance row
        # requiring ten cases.
        acceptances = [a for a in plan.get("acceptance", []) if isinstance(a, Mapping)
                       and a.get("gate_id") == gate_id and a.get("entrypoint_id") == value.get("entrypoint_id")]
        if acceptances:
            expected_denominators = {a.get("denominator") for a in acceptances}
            if len(expected_denominators) != 1:
                raise EvidenceError("ambiguous acceptance denominator in compiled plan")
            expected_denominator = next(iter(expected_denominators))
            if not isinstance(expected_denominator, int) or expected_denominator < 1:
                raise EvidenceError("invalid acceptance denominator in compiled plan")
            # counts is validated below; retain the comparison after parsing.
    runtime = _str(value["runtime"], "$.runtime", public=True)
    config = _str(value["config"], "$.config", public=True)
    started = _utc(value["started_at"], "$.started_at"); ended = _utc(value["ended_at"], "$.ended_at")
    start_dt = _parsed_utc(started, "$.started_at"); end_dt = _parsed_utc(ended, "$.ended_at")
    if end_dt < start_dt:
        raise EvidenceError("ended_at precedes started_at", "$.ended_at")
    elapsed = value["elapsed_sec"]
    if type(elapsed) not in (int, float) or isinstance(elapsed, bool) or not math.isfinite(float(elapsed)) or elapsed < 0:
        raise EvidenceError("finite elapsed seconds required", "$.elapsed_sec")
    observed_elapsed = (end_dt - start_dt).total_seconds()
    if abs(float(elapsed) - observed_elapsed) > max(0.05, observed_elapsed * 0.05):
        raise EvidenceError("elapsed_sec/timestamp mismatch", "$.elapsed_sec")
    exit_status = _int(value["exit_status"], "$.exit_status", minimum=-255)
    counts = validate_counts(value["counts"], "$.counts", require_green=require_green)
    if plan is not None and acceptances:
        if counts["total"] != expected_denominator:
            raise EvidenceError("evidence denominator does not satisfy acceptance contract", "$.counts.total")
    if "unknown" in value:
        try:
            supplied_unknown = _int(value["unknown"], "$.unknown", minimum=0)
        except ContractError as exc:
            raise EvidenceError(str(exc), "$.unknown")
        if supplied_unknown != counts["unknown"]:
            raise EvidenceError("unknown arithmetic mismatch", "$.unknown")
    log_sha = _log_hash(value["log_sha256"], "$.log_sha256")
    log_mode = _int(value["log_mode"], "$.log_mode", minimum=0, maximum=0o777)
    log_size = _int(value["log_size"], "$.log_size", minimum=1)
    if _bool(value["superseded"], "$.superseded"):
        raise EvidenceError("stale/superseded evidence cannot be accepted", "$.superseded")
    reused = _bool(value["reused"], "$.reused")
    if "expected_denied" in value and type(value["expected_denied"]) is not bool:
        raise EvidenceError("expected_denied must be boolean", "$.expected_denied")
    if "correction_of" in value and (not isinstance(value["correction_of"], str) or not value["correction_of"]):
        raise EvidenceError("correction_of must be a non-empty ID", "$.correction_of")
    if "writer_task_id" in value and (not isinstance(value["writer_task_id"], str) or not value["writer_task_id"]):
        raise EvidenceError("writer_task_id must be a non-empty ID", "$.writer_task_id")
    if "entrypoint_id" in value and (not isinstance(value["entrypoint_id"], str) or not value["entrypoint_id"]):
        raise EvidenceError("entrypoint_id must be a non-empty ID", "$.entrypoint_id")
    finding_fields = {"finding_id", "counterexample_sha256"} & set(value)
    if finding_fields and finding_fields != {"finding_id", "counterexample_sha256"}:
        raise EvidenceError("complete finding/counterexample binding required")
    if finding_fields:
        _id(value["finding_id"], "$.finding_id")
        if not isinstance(value["counterexample_sha256"], str) or _SHA_RE.fullmatch(value["counterexample_sha256"]) is None:
            raise EvidenceError("counterexample SHA-256 required", "$.counterexample_sha256")
    closure_fields = {"closure_binding_receipt_sha256", "closure_binding_sha256s"} & set(value)
    if closure_binding_receipt is None:
        if closure_fields:
            raise EvidenceError("closure binding fields require authoritative receipt")
    else:
        if closure_fields != {"closure_binding_receipt_sha256", "closure_binding_sha256s"}:
            raise EvidenceError("closure-aware evidence row requires exact receipt fields")
        try:
            receipt = validate_closure_binding_receipt(closure_binding_receipt)
        except ContractError as exc:
            raise EvidenceError("invalid closure binding receipt") from exc
        expected_bindings = sorted(
            binding["binding_sha256"]
            for binding in receipt["bindings"]
            if (
                binding["evidence_row_id"] == case_id
                and binding["gate_id"] == gate_id
                and binding["stage"] == stage
                and binding["entrypoint_id"] == value.get("entrypoint_id")
            )
        )
        if (
            value["closure_binding_receipt_sha256"] != receipt["receipt_sha256"]
            or value["closure_binding_sha256s"] != expected_bindings
        ):
            raise EvidenceError("evidence row closure binding receipt mismatch")
    if decision == "skipped":
        raise EvidenceError("skipped evidence cannot be green", "$.decision")
    if decision in {"allow", "reused"} and (exit_status != 0 or counts["failed"] or counts["skipped"] or counts["unknown"] or counts["xfail"]):
        raise EvidenceError("allow/reused evidence must be fully green", "$.decision")
    if require_green and decision not in {"allow", "reused"}:
        raise EvidenceError("green evidence requires allow/reused decision", "$.decision")
    if require_green and (counts["failed"] or counts["skipped"] or counts["unknown"] or counts["xfail"]):
        raise EvidenceError("green evidence requires no failed/skipped/unknown/xfail", "$.counts")
    if decision == "deny" and exit_status == 0 and not value.get("expected_denied", False):
        raise EvidenceError("deny with exit 0 is ambiguous", "$.decision")
    if log_root is not None:
        if log_root.exists() and log_root.is_symlink():
            raise EvidenceError("symlink artifact root", "$.log_path")
        rel = value.get("log_path", "")
        if not isinstance(rel, str) or not rel or rel.startswith(("/", "~")) or ".." in pathlib.PurePosixPath(rel).parts:
            raise EvidenceError("missing/unsafe log path", "$.log_path")
        path = (log_root / rel).resolve()
        try:
            path.relative_to(log_root.resolve())
        except ValueError:
            raise EvidenceError("log outside artifact root", "$.log_path")
        if path.is_symlink() or not path.is_file():
            raise EvidenceError("missing/symlink log", "$.log_path")
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != log_sha:
            raise EvidenceError("mismatched log SHA", "$.log_sha256")
        if len(data) != log_size:
            raise EvidenceError("mismatched log size", "$.log_size")
        if privacy_scan(data):
            raise EvidenceError("private/nonUTF8 log", "$.log_path")
    result = dict(value)
    result["counts"] = counts
    return result


def validate_envelope(
    value: Any,
    *,
    expected_head: str | None = None,
    expected_tree: str | None = None,
    expected_identity_mode: str | None = None,
    expected_snapshot_sha256: str | None = None,
    log_root: pathlib.Path | None = None,
    transcript_path: pathlib.Path | None = None,
    plan: Mapping[str, Any] | None = None,
    closure_binding_receipt: Mapping[str, Any] | None = None,
    expected_closure_binding_receipt_sha256: str | None = None,
    expected_closure_plan_sha256: str | None = None,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise EvidenceError("evidence envelope object required")
    fields = {"schema", "mission_id", "head_sha", "tree_sha", "identity_mode", "snapshot_sha256", "clean", "generated_at", "rows", "envelope_sha256"}
    optional = {"dispatch_transcript_sha256", "closure_binding_receipt_sha256", "closure_plan_sha256"}
    if set(value) - fields - optional or fields - set(value):
        raise EvidenceError("missing/additional envelope field")
    if value["schema"] != "evidence-envelope.v16":
        raise EvidenceError("schema", "$.schema")
    closure_fields = {"closure_binding_receipt_sha256", "closure_plan_sha256"} & set(value)
    checked_closure_receipt: dict[str, Any] | None = None
    if closure_binding_receipt is None:
        if closure_fields:
            raise EvidenceError("closure envelope fields require authoritative receipt")
        if expected_closure_binding_receipt_sha256 is not None or expected_closure_plan_sha256 is not None:
            raise EvidenceError("expected closure identity requires authoritative receipt")
    else:
        if closure_fields != {"closure_binding_receipt_sha256", "closure_plan_sha256"}:
            raise EvidenceError("closure-aware envelope requires exact receipt fields")
        try:
            checked_closure_receipt = validate_closure_binding_receipt(
                closure_binding_receipt,
                expected_receipt_sha256=expected_closure_binding_receipt_sha256,
                expected_closure_plan_sha256=expected_closure_plan_sha256,
            )
        except ContractError as exc:
            raise EvidenceError("invalid closure binding receipt") from exc
        if (
            value["closure_binding_receipt_sha256"] != checked_closure_receipt["receipt_sha256"]
            or value["closure_plan_sha256"] != checked_closure_receipt["closure_plan_sha256"]
        ):
            raise EvidenceError("evidence envelope closure receipt mismatch")
    mission_id = _id(value["mission_id"], "$.mission_id")
    head = _sha(value["head_sha"], "$.head_sha")
    if expected_head and head != expected_head:
        raise EvidenceError("envelope head drift", "$.head_sha")
    tree = _str(value["tree_sha"], "$.tree_sha", max_len=64, public=True)
    if len(tree) != 40 or not all(c in "0123456789abcdef" for c in tree.lower()):
        raise EvidenceError("tree SHA", "$.tree_sha")
    if expected_tree and tree != expected_tree:
        raise EvidenceError("envelope tree drift", "$.tree_sha")
    identity_mode, snapshot_sha256 = _identity(value)
    if expected_identity_mode is not None and identity_mode != expected_identity_mode:
        raise EvidenceError("evidence envelope identity mode mismatch", "$.identity_mode")
    if expected_snapshot_sha256 is not None and snapshot_sha256 != expected_snapshot_sha256:
        raise EvidenceError("evidence envelope snapshot mismatch", "$.snapshot_sha256")
    clean = _bool(value["clean"], "$.clean")
    if identity_mode == "git-exact-object" and clean is not True:
        raise EvidenceError("clean worktree required", "$.clean")
    generated = _utc(value["generated_at"], "$.generated_at")
    rows = value["rows"]
    if not isinstance(rows, list) or not rows:
        raise EvidenceError("non-empty evidence rows required", "$.rows")
    checked = [
        validate_row(
            row,
            expected_head=head,
            expected_tree=tree,
            expected_identity_mode=identity_mode,
            expected_snapshot_sha256=snapshot_sha256,
            expected_clean=clean,
            log_root=log_root,
            plan=plan,
            closure_binding_receipt=checked_closure_receipt,
        )
        for row in rows
    ]
    generated_dt = _parsed_utc(generated, "$.generated_at")
    row_starts = [_parsed_utc(row["started_at"], "$.rows[].started_at") for row in checked]
    row_ends = [_parsed_utc(row["ended_at"], "$.rows[].ended_at") for row in checked]
    if (log_root is not None or "dispatch_transcript_sha256" in value) and (generated_dt < min(row_starts) or generated_dt > max(row_ends)):
        raise EvidenceError("generated_at must fall within evidence run window", "$.generated_at")
    case_ids = [r["case_id"] for r in checked]; semantics = [r["semantics"] for r in checked]
    if len(set(case_ids)) != len(case_ids) or len(set(semantics)) != len(semantics):
        raise EvidenceError("duplicate case semantics", "$.rows")
    by_counts: dict[tuple[int, ...], list[dict[str, Any]]] = {}
    for row in checked:
        key = tuple(row["counts"][x] for x in ("total", "ran", "passed", "failed", "skipped", "xfail", "unknown"))
        by_counts.setdefault(key, []).append(row)
    for same in by_counts.values():
        if len(same) > 1 and len({r["log_sha256"] for r in same}) == 1:
            raise EvidenceError("copied/constant counts with identical log", "$.rows")
    unsigned = dict(value); unsigned["envelope_sha256"] = ""
    expected_hash = canonical_sha256(unsigned)
    if value["envelope_sha256"] != expected_hash:
        raise EvidenceError("envelope SHA mismatch", "$.envelope_sha256")
    if "dispatch_transcript_sha256" in value:
        transcript_hash = value["dispatch_transcript_sha256"]
        if not isinstance(transcript_hash, str) or _SHA_RE.fullmatch(transcript_hash) is None:
            raise EvidenceError("dispatch transcript SHA-256 required", "$.dispatch_transcript_sha256")
        if transcript_path is not None:
            path = pathlib.Path(transcript_path)
            if path.is_symlink() or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != transcript_hash:
                raise EvidenceError("dispatch transcript hash/path mismatch", "$.dispatch_transcript_sha256")
    result = {"schema": "evidence-envelope.v16", "mission_id": mission_id, "head_sha": head, "tree_sha": tree, "identity_mode": identity_mode, "snapshot_sha256": snapshot_sha256, "clean": clean, "generated_at": generated, "rows": checked, "envelope_sha256": expected_hash}
    if "dispatch_transcript_sha256" in value:
        result["dispatch_transcript_sha256"] = value["dispatch_transcript_sha256"]
    if checked_closure_receipt is not None:
        result["closure_binding_receipt_sha256"] = checked_closure_receipt["receipt_sha256"]
        result["closure_plan_sha256"] = checked_closure_receipt["closure_plan_sha256"]
    return result


def build_envelope(
    mission_id: str,
    head_sha: str,
    tree_sha: str,
    rows: Sequence[Mapping[str, Any]],
    *,
    generated_at: str,
    identity_mode: str = "git-exact-object",
    snapshot_sha256: str = "",
    clean: bool = True,
    log_root: pathlib.Path | None = None,
    dispatch_transcript_sha256: str | None = None,
    transcript_path: pathlib.Path | None = None,
    plan: Mapping[str, Any] | None = None,
    closure_binding_receipt: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    mode, snapshot = _identity({"identity_mode": identity_mode, "snapshot_sha256": snapshot_sha256})
    bound_rows: list[dict[str, Any]] = []
    for index, raw in enumerate(rows):
        if not isinstance(raw, Mapping):
            raise EvidenceError("evidence row object required", f"$.rows[{index}]")
        row = dict(raw)
        for field, expected in (("identity_mode", mode), ("snapshot_sha256", snapshot)):
            if field in row and row[field] != expected:
                raise EvidenceError("row/envelope identity mismatch", f"$.rows[{index}].{field}")
            row[field] = expected
        bound_rows.append(row)
    unsigned = {"schema": "evidence-envelope.v16", "mission_id": mission_id, "head_sha": head_sha, "tree_sha": tree_sha, "identity_mode": mode, "snapshot_sha256": snapshot, "clean": clean, "generated_at": generated_at, "rows": bound_rows, "envelope_sha256": ""}
    if dispatch_transcript_sha256 is not None:
        unsigned["dispatch_transcript_sha256"] = dispatch_transcript_sha256
    if closure_binding_receipt is not None:
        try:
            receipt = validate_closure_binding_receipt(closure_binding_receipt)
        except ContractError as exc:
            raise EvidenceError("invalid closure binding receipt") from exc
        unsigned["closure_binding_receipt_sha256"] = receipt["receipt_sha256"]
        unsigned["closure_plan_sha256"] = receipt["closure_plan_sha256"]
    checked = validate_envelope(
        unsigned | {"envelope_sha256": canonical_sha256(unsigned)},
        expected_head=head_sha,
        expected_tree=tree_sha,
        expected_identity_mode=mode,
        expected_snapshot_sha256=snapshot,
        log_root=log_root,
        transcript_path=transcript_path,
        plan=plan,
        closure_binding_receipt=closure_binding_receipt,
    )
    return checked


def write_envelope(envelope: Mapping[str, Any], path: str | pathlib.Path, *, transcript_path: pathlib.Path | None = None) -> tuple[str, str]:
    if envelope.get("dispatch_transcript_sha256") and transcript_path is None:
        raise EvidenceError("transcript_path is required when binding a dispatch transcript")
    checked = validate_envelope(dict(envelope), expected_head=envelope["head_sha"], expected_tree=envelope["tree_sha"], transcript_path=transcript_path)
    payload = canonical_json(checked) + "\n"
    destination = pathlib.Path(path)
    destination.write_text(payload, encoding="utf-8")
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    sidecar = destination.with_suffix(destination.suffix + ".sha256")
    sidecar.write_text(digest + "\n", encoding="ascii")
    return digest, str(sidecar)


# Stable public names used by contract dispatchers and downstream checkers.
validate_evidence_envelope = validate_envelope

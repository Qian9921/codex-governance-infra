"""Evidence arithmetic, provenance, stale-count and privacy validation."""
from __future__ import annotations

import hashlib
import json
import math
import pathlib
import re
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Sequence

from .contracts import ContractError, canonical_json, canonical_sha256, _id, _int, _sha, _str


class EvidenceError(ContractError):
    pass

_PRIVATE_RE = re.compile(r"(?:gh[pso]_[A-Za-z0-9]{12,}|/home/|/Users/|prompt|token|credential|session[_-]?id|transcript|private[_-]?path)", re.I)
_SHA_RE = re.compile(r"[0-9a-f]{64}\Z")


def _utc(value: Any, path: str) -> str:
    value = _str(value, path, public=True)
    if not value.endswith("Z"):
        raise EvidenceError("UTC timestamp required", path)
    return value


def _bool(value: Any, path: str) -> bool:
    if type(value) is not bool:
        raise EvidenceError("boolean required", path)
    return value


def validate_counts(value: Mapping[str, Any], path: str = "$.counts", *, require_green: bool = True) -> dict[str, int]:
    if not isinstance(value, dict):
        raise EvidenceError("counts object required", path)
    allowed = {"total", "ran", "passed", "failed", "skipped", "xfail", "unknown"}
    if set(value) - allowed or not {"total", "passed", "failed", "skipped"} <= set(value):
        raise EvidenceError("counts fields", path)
    result: dict[str, int] = {}
    for key in allowed:
        if key in value:
            try:
                result[key] = _int(value[key], f"{path}.{key}", minimum=0)
            except ContractError as exc:
                raise EvidenceError(str(exc), f"{path}.{key}")
        else:
            result[key] = 0
    if result["total"] != result["passed"] + result["failed"] + result["skipped"]:
        raise EvidenceError("total must equal passed+failed+skipped", path)
    if result["ran"] != result["passed"] + result["failed"]:
        raise EvidenceError("ran must equal passed+failed", path)
    computed_unknown = result["total"] - result["ran"] - result["skipped"]
    if result["unknown"] != computed_unknown:
        raise EvidenceError("unknown arithmetic mismatch", path)
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


def validate_row(value: Any, *, expected_head: str | None = None, log_root: pathlib.Path | None = None, require_green: bool = True) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise EvidenceError("evidence row object required")
    fields = {"schema", "case_id", "semantics", "gate_id", "stage", "decision", "expected_head", "actual_head", "tree_sha", "dirty", "command", "cwd", "runtime", "config", "started_at", "ended_at", "elapsed_sec", "exit_status", "counts", "log_sha256", "log_mode", "log_size", "reused", "superseded"}
    optional = {"correction_of", "expected_denied", "log_path", "unknown"}
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
    if _bool(value["dirty"], "$.dirty"):
        raise EvidenceError("dirty worktree evidence is not green", "$.dirty")
    command = value["command"]
    if not isinstance(command, list) or not command or any(not isinstance(x, str) or not x for x in command):
        raise EvidenceError("direct argv command required", "$.command")
    cwd = _str(value["cwd"], "$.cwd", public=True)
    if cwd.startswith(("/", "~")) or ".." in pathlib.PurePosixPath(cwd).parts:
        raise EvidenceError("portable relative cwd required", "$.cwd")
    runtime = _str(value["runtime"], "$.runtime", public=True)
    config = _str(value["config"], "$.config", public=True)
    started = _utc(value["started_at"], "$.started_at"); ended = _utc(value["ended_at"], "$.ended_at")
    elapsed = value["elapsed_sec"]
    if type(elapsed) not in (int, float) or isinstance(elapsed, bool) or not math.isfinite(float(elapsed)) or elapsed < 0:
        raise EvidenceError("finite elapsed seconds required", "$.elapsed_sec")
    exit_status = _int(value["exit_status"], "$.exit_status", minimum=-255)
    counts = validate_counts(value["counts"], "$.counts", require_green=require_green)
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


def validate_envelope(value: Any, *, expected_head: str | None = None, log_root: pathlib.Path | None = None) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise EvidenceError("evidence envelope object required")
    fields = {"schema", "mission_id", "head_sha", "tree_sha", "clean", "generated_at", "rows", "envelope_sha256"}
    if set(value) != fields:
        raise EvidenceError("missing/additional envelope field")
    if value["schema"] != "evidence-envelope.v16":
        raise EvidenceError("schema", "$.schema")
    mission_id = _id(value["mission_id"], "$.mission_id")
    head = _sha(value["head_sha"], "$.head_sha")
    if expected_head and head != expected_head:
        raise EvidenceError("envelope head drift", "$.head_sha")
    tree = _str(value["tree_sha"], "$.tree_sha", max_len=64, public=True)
    if len(tree) != 40 or not all(c in "0123456789abcdef" for c in tree.lower()):
        raise EvidenceError("tree SHA", "$.tree_sha")
    if _bool(value["clean"], "$.clean") is not True:
        raise EvidenceError("clean worktree required", "$.clean")
    generated = _utc(value["generated_at"], "$.generated_at")
    rows = value["rows"]
    if not isinstance(rows, list) or not rows:
        raise EvidenceError("non-empty evidence rows required", "$.rows")
    checked = [validate_row(row, expected_head=head, log_root=log_root) for row in rows]
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
    return {"schema": "evidence-envelope.v16", "mission_id": mission_id, "head_sha": head, "tree_sha": tree, "clean": True, "generated_at": generated, "rows": checked, "envelope_sha256": expected_hash}


def build_envelope(mission_id: str, head_sha: str, tree_sha: str, rows: Sequence[Mapping[str, Any]], *, generated_at: str, log_root: pathlib.Path | None = None) -> dict[str, Any]:
    unsigned = {"schema": "evidence-envelope.v16", "mission_id": mission_id, "head_sha": head_sha, "tree_sha": tree_sha, "clean": True, "generated_at": generated_at, "rows": [dict(r) for r in rows], "envelope_sha256": ""}
    checked = validate_envelope(unsigned | {"envelope_sha256": canonical_sha256(unsigned)}, expected_head=head_sha, log_root=log_root)
    return checked


def write_envelope(envelope: Mapping[str, Any], path: str | pathlib.Path) -> tuple[str, str]:
    checked = validate_envelope(dict(envelope), expected_head=envelope["head_sha"])
    payload = canonical_json(checked) + "\n"
    destination = pathlib.Path(path)
    destination.write_text(payload, encoding="utf-8")
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    sidecar = destination.with_suffix(destination.suffix + ".sha256")
    sidecar.write_text(digest + "\n", encoding="ascii")
    return digest, str(sidecar)


# Stable public names used by contract dispatchers and downstream checkers.
validate_evidence_envelope = validate_envelope

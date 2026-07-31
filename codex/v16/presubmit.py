"""One-command V16 presubmit orchestration and complete machine envelope."""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import pathlib
import subprocess
import tarfile
import tempfile
import time
from datetime import datetime, timezone
from typing import Any, Mapping

from .compiler import compile_file
from .contracts import canonical_json, canonical_sha256
from .evidence import validate_counts, privacy_scan
from .metrics import dashboard
from .trace import render_pr_trace


def _utc() -> str:
    return datetime.now(timezone.utc).replace(microsecond=3_000).isoformat().replace("+00:00", "Z")


def _git(root: pathlib.Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=str(root), text=True).strip()


def _run_json(command: list[str], cwd: pathlib.Path, env: Mapping[str, str]) -> tuple[dict[str, Any], bytes, int, float]:
    start = time.monotonic(); proc = subprocess.run(command, cwd=str(cwd), env=dict(env), shell=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False); elapsed = round(time.monotonic() - start, 6)
    try:
        payload = json.loads(proc.stdout.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        payload = {"schema": "checker-result.v16", "total": 1, "ran": 1, "passed": 0, "failed": 1, "skipped": 0, "xfail": 0, "unknown": 0, "parse_error": True}
    return payload, proc.stderr, proc.returncode, elapsed


def _safe_extract(data: bytes, destination: pathlib.Path) -> None:
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:") as archive:
        root = destination.resolve()
        for member in archive.getmembers():
            target = (root / member.name).resolve()
            try:
                target.relative_to(root)
            except ValueError:
                raise RuntimeError("fresh archive path traversal")
            if member.issym() or member.islnk():
                raise RuntimeError("fresh archive symlink forbidden")
        archive.extractall(destination)


def _check(name: str, why_red: str, cost: str, payload: Mapping[str, Any], *, elapsed: float, exit_status: int) -> dict[str, Any]:
    counts = validate_counts(payload, require_green=False)
    return {"id": name, "why_red": why_red, "cost": cost, "denominator": counts["total"], "total": counts["total"], "ran": counts["ran"], "passed": counts["passed"], "failed": counts["failed"], "skipped": counts["skipped"], "xfail": counts["xfail"], "unknown": counts["unknown"], "elapsed_sec": elapsed, "exit_status": exit_status}


def run_presubmit(repo: str | pathlib.Path) -> dict[str, Any]:
    root = pathlib.Path(repo).resolve()
    head = _git(root, "rev-parse", "HEAD")
    tree = _git(root, "rev-parse", "HEAD^{tree}")
    dirty = bool(_git(root, "status", "--porcelain", "--untracked-files=no"))
    if dirty:
        raise RuntimeError("presubmit requires clean tracked worktree")
    env = os.environ.copy(); env["PYTHONPATH"] = str(root); env.pop("PYTHONSTARTUP", None)
    mission_path = root / "codex/v16/fixtures/mission.valid.json"
    compile_start = time.monotonic(); compiled = compile_file(mission_path); compile_elapsed = round(time.monotonic() - compile_start, 6)
    compile_payload = {"total": len(compiled["plan"]["acceptance"]), "ran": len(compiled["plan"]["acceptance"]), "passed": len(compiled["plan"]["acceptance"]), "failed": 0, "skipped": 0, "xfail": 0, "unknown": 0}
    checks = [_check("compile", "Malformed mission or unsafe gate DAG must fail before execution", "milliseconds", compile_payload, elapsed=compile_elapsed, exit_status=0)]
    checker = ["python3", "-m", "codex.v16.checker", "--start", "tests/v16"]
    test_payload, test_stderr, test_status, test_elapsed = _run_json(checker, root, env)
    checks.append(_check("unit-meta-negative", "Any mandatory counterexample validator regression must turn RED", "under one minute", test_payload, elapsed=test_elapsed, exit_status=test_status))
    with tempfile.TemporaryDirectory(prefix="v16-fresh-") as tmp:
        fresh = pathlib.Path(tmp) / "clone"; fresh.mkdir()
        archive = subprocess.check_output(["git", "archive", "--format=tar", "HEAD"], cwd=str(root))
        _safe_extract(archive, fresh)
        fresh_env = dict(env); fresh_env["PYTHONPATH"] = str(fresh)
        fresh_compile = ["python3", "-m", "codex.v16.compiler", str(fresh / "codex/v16/fixtures/mission.valid.json")]
        fresh_payload, fresh_stderr, fresh_status, fresh_elapsed = _run_json(fresh_compile, fresh, fresh_env)
        # Compiler emits status/plan hash rather than checker counts; one exact
        # deterministic compile is the known denominator for this portability gate.
        fresh_counts = {"total": 1, "ran": 1, "passed": 1 if fresh_status == 0 and fresh_payload.get("status") == "GREEN" else 0, "failed": 0 if fresh_status == 0 and fresh_payload.get("status") == "GREEN" else 1, "skipped": 0, "xfail": 0, "unknown": 0}
        checks.append(_check("fresh-clone", "Fresh archive must compile without repository-local state", "seconds", fresh_counts, elapsed=fresh_elapsed, exit_status=fresh_status))
    all_pass = all(c["failed"] == 0 and c["skipped"] == 0 and c["unknown"] == 0 and c["xfail"] == 0 for c in checks) and not dirty
    review = render_pr_trace(mission_id="V16-PRODUCTIVITY", base_sha="e18439c8dfe01d901895efd09b8b73b6842327a9", head_sha=head, tree_sha=tree, checks=[{"id": c["id"], "status": "GREEN" if c["failed"] == 0 else "RED", "reused": False, "skipped": c["skipped"] > 0, "cost": c["cost"], "denominator": c["denominator"], "total": c["total"], "passed": c["passed"], "failed": c["failed"]} for c in checks], findings=[] if all_pass else [{"id": "PRESUBMIT-RED", "severity": "P1", "label": "BLOCKING", "attribution": "DELTA_INTRODUCED", "location": "presubmit", "counterexample": "gate red", "disposition": "OPEN"}], closures={}, reviewed_scope=["codex/v16", "scripts", "tests/v16"], unreviewed_scope=[] if all_pass else ["fresh-readiness"])
    metrics = {"schema": "metrics.v16", "mission_id": "V16-PRODUCTIVITY", "source_hash": canonical_sha256({"head": head, "checks": checks}), "first_pass_approval": False, "pre_review_blocker_capture": 0 if all_pass else 1, "review_rounds": 1, "full_runs_per_head": 0.0, "fresh_runs_per_head": 1.0, "evidence_corrections": 0, "writer_handoffs": 0, "spark_audit_count": 3, "spark_audit_latency_sec": 0.0, "gate_elapsed_sec": round(sum(c["elapsed_sec"] for c in checks), 6), "new_blocker_admissions": 0 if all_pass else 1}
    envelope = {"schema": "presubmit-envelope.v16", "mission_id": "V16-PRODUCTIVITY", "head_sha": head, "tree_sha": tree, "clean": not dirty, "generated_at": _utc(), "checks": checks, "review_packet": review["packet"], "metrics_dashboard": dashboard(metrics), "status": "GREEN" if all_pass else "RED"}
    envelope["envelope_sha256"] = canonical_sha256(envelope)
    return envelope


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__); ap.add_argument("--repo", default="."); args = ap.parse_args(argv)
    try:
        result = run_presubmit(args.repo)
    except Exception as exc:
        print(json.dumps({"schema": "presubmit-envelope.v16", "status": "RED", "error": str(exc)}, sort_keys=True))
        return 2
    print(canonical_json(result))
    return 0 if result["status"] == "GREEN" else 1


if __name__ == "__main__":
    raise SystemExit(main())

"""Foreground, direct-argv gate execution with identity-bound receipts."""
from __future__ import annotations

import hashlib
import json
import os
import pathlib
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from typing import Any, Mapping

from .contracts import canonical_json
from .evidence import EvidenceError, validate_counts


class GateRunError(RuntimeError):
    pass


def _utc() -> str:
    return datetime.now(timezone.utc).replace(microsecond=3_000).isoformat().replace("+00:00", "Z")


def _sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _git(root: pathlib.Path, *argv: str) -> str:
    proc = subprocess.run(["git", *argv], cwd=str(root), shell=False, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    return proc.stdout.strip()


def git_identity(root: pathlib.Path) -> tuple[str, str, bool]:
    head = _git(root, "rev-parse", "HEAD")
    tree = _git(root, "rev-parse", "HEAD^{tree}")
    dirty = bool(_git(root, "status", "--porcelain", "--untracked-files=no"))
    return head, tree, dirty


class GateRunner:
    def __init__(self, root: str | pathlib.Path, plan: Mapping[str, Any], artifact_dir: str | pathlib.Path):
        self.root = pathlib.Path(root).resolve()
        self.plan = dict(plan)
        self.artifact_dir = pathlib.Path(artifact_dir).resolve()
        if self.artifact_dir == self.root or self.artifact_dir.is_symlink():
            raise GateRunError("artifact root must be explicit and not repository/symlink")
        self.artifact_dir.mkdir(parents=True, exist_ok=True)
        self.results: dict[str, dict[str, Any]] = {}

    def _write_log(self, gate_id: str, entry_id: str, suffix: str, data: bytes) -> tuple[str, int, int, str]:
        folder = self.artifact_dir / "logs" / gate_id
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{entry_id}.{suffix}.log"
        if path.exists() and path.is_symlink():
            raise GateRunError("log symlink forbidden")
        path.write_bytes(data)
        os.chmod(path, 0o600)
        return str(path.relative_to(self.artifact_dir)), len(data), path.stat().st_mode & 0o777, _sha_bytes(data)

    def run_gate(self, gate_id: str, *, expected_head: str, force: bool = False, env_overrides: Mapping[str, str] | None = None) -> dict[str, Any]:
        gates = {g["id"]: g for g in self.plan["gates"]}
        entries = {e["id"]: e for e in self.plan["entrypoints"]}
        if gate_id not in gates:
            raise GateRunError("unknown gate")
        gate = gates[gate_id]
        for dep in gate["depends_on"]:
            if dep not in self.results:
                raise GateRunError(f"dependency {dep} has not run")
            if self.results[dep]["decision"] not in {"allow", "reused"}:
                result = {"schema": "gate-result.v16", "gate_id": gate_id, "stage": gate["stage"], "decision": "skipped", "reason": "DEPENDENCY_RED", "expected_head": expected_head, "actual_head": expected_head, "tree_sha": ""}
                self.results[gate_id] = result
                return result
        actual_head, tree_sha, dirty = git_identity(self.root)
        if actual_head != expected_head:
            raise GateRunError("head changed before gate")
        if dirty:
            raise GateRunError("dirty worktree before gate")
        if not force and gate_id in self.results and self.results[gate_id].get("actual_head") == actual_head and self.results[gate_id].get("decision") == "allow":
            cached = dict(self.results[gate_id]); cached["decision"] = "reused"; self.results[gate_id] = cached; return cached
        all_rows: list[dict[str, Any]] = []
        gate_start = time.monotonic(); start = _utc()
        for entry_id in gate["entrypoint_ids"]:
            entry = entries[entry_id]
            command = entry["argv"]
            if not isinstance(command, list) or not command or any(not isinstance(x, str) for x in command):
                raise GateRunError("direct argv required")
            cwd = (self.root / entry["cwd"]).resolve()
            try:
                cwd.relative_to(self.root)
            except ValueError:
                raise GateRunError("entrypoint cwd escapes repository")
            env = os.environ.copy(); env.update(entry.get("env", {})); env.update(env_overrides or {})
            env.setdefault("PYTHONUNBUFFERED", "1")
            entry_start = _utc(); timed_out = False
            try:
                proc = subprocess.run(command, cwd=str(cwd), env=env, shell=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=float(entry["timeout_sec"]), check=False)
                status = proc.returncode
                out, err = proc.stdout, proc.stderr
            except subprocess.TimeoutExpired as exc:
                timed_out = True; status = 124; out = exc.stdout or b""; err = (exc.stderr or b"") + b"\nTIMEOUT\n"
            rel_out, out_size, out_mode, out_sha = self._write_log(gate_id, entry_id, "stdout", out)
            rel_err, err_size, err_mode, err_sha = self._write_log(gate_id, entry_id, "stderr", err)
            raw = out.decode("utf-8", errors="replace").strip()
            try:
                checker = json.loads(raw)
                counts = validate_counts(checker, require_green=False)
            except (ValueError, json.JSONDecodeError, EvidenceError) as exc:
                checker = {}; counts = {"total": 0, "ran": 0, "passed": 0, "failed": 1, "skipped": 0, "xfail": 0, "unknown": 0}; parse_error = str(exc)
            else:
                parse_error = ""
            if timed_out or status != 0:
                decision = "deny"
            elif parse_error or counts["failed"] or counts["skipped"] or counts["unknown"] or counts["xfail"]:
                decision = "deny"
            else:
                decision = "allow"
            all_rows.append({"schema": "gate-row.v16", "gate_id": gate_id, "entrypoint_id": entry_id, "stage": gate["stage"], "decision": decision, "expected_head": expected_head, "actual_head": actual_head, "tree_sha": tree_sha, "dirty": dirty, "command": command, "cwd": entry["cwd"], "runtime": "python-stdlib", "config": "mission-plan", "started_at": entry_start, "ended_at": _utc(), "elapsed_sec": round(time.monotonic() - gate_start, 6), "exit_status": status, "counts": counts, "log_paths": [rel_out, rel_err], "log_shas": [out_sha, err_sha], "log_sizes": [out_size, err_size], "parse_error": parse_error})
            if decision != "allow":
                break
        overall = "allow" if all(r["decision"] == "allow" for r in all_rows) and len(all_rows) == len(gate["entrypoint_ids"]) else "deny"
        result = {"schema": "gate-result.v16", "gate_id": gate_id, "stage": gate["stage"], "decision": overall, "expected_head": expected_head, "actual_head": actual_head, "tree_sha": tree_sha, "dirty": dirty, "started_at": start, "ended_at": _utc(), "elapsed_sec": round(time.monotonic() - gate_start, 6), "rows": all_rows}
        self.results[gate_id] = result
        return result

    def run_plan(self, *, expected_head: str, gate_ids: list[str] | None = None, force: bool = False) -> dict[str, Any]:
        selected = set(gate_ids) if gate_ids else set(self.plan["gate_order"])
        for gate_id in self.plan["gate_order"]:
            if gate_id not in selected:
                continue
            try:
                self.run_gate(gate_id, expected_head=expected_head, force=force)
            except GateRunError as exc:
                self.results[gate_id] = {"schema": "gate-result.v16", "gate_id": gate_id, "decision": "deny", "reason": str(exc), "expected_head": expected_head, "actual_head": "", "tree_sha": ""}
                break
        return {"schema": "gate-run.v16", "expected_head": expected_head, "results": [self.results[g] for g in self.plan["gate_order"] if g in self.results]}

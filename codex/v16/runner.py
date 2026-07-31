"""Foreground, direct-argv gate execution with identity-bound receipts."""
from __future__ import annotations

import hashlib
import json
import os
import pathlib
import signal
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from typing import Any, Mapping
import math
import re

from .contracts import canonical_json
from .evidence import EvidenceError, privacy_scan, validate_counts


class GateRunError(RuntimeError):
    pass


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _git(root: pathlib.Path, *argv: str) -> str:
    proc = subprocess.run(["git", *argv], cwd=str(root), shell=False, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    return proc.stdout.strip()


def git_identity(root: pathlib.Path) -> tuple[str, str, bool]:
    head = _git(root, "rev-parse", "HEAD")
    tree = _git(root, "rev-parse", "HEAD^{tree}")
    dirty = bool(_git(root, "status", "--porcelain"))
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
        self._interpreter = str(pathlib.Path(sys.executable).resolve())

    def _write_log(self, gate_id: str, entry_id: str, suffix: str, data: bytes) -> tuple[str, int, int, str]:
        folder = self.artifact_dir / "logs" / gate_id
        if folder.exists() and folder.is_symlink():
            raise GateRunError("log directory symlink forbidden")
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{entry_id}.{suffix}.log"
        if path.exists() and path.is_symlink():
            raise GateRunError("log symlink forbidden")
        path.write_bytes(data)
        os.chmod(path, 0o600)
        return str(path.relative_to(self.artifact_dir)), len(data), path.stat().st_mode & 0o777, _sha_bytes(data)

    def _environment(self, entry: Mapping[str, Any], env_overrides: Mapping[str, str] | None) -> dict[str, str]:
        """Build a small private environment; never inherit secrets/proxies."""
        # Never inherit PATH; argv is normalized to the exact interpreter below.
        allowed_host = {"LANG", "LC_ALL", "TZ"}
        env: dict[str, str] = {key: os.environ[key] for key in allowed_host if key in os.environ}
        home = self.artifact_dir / "private-home"; codex_home = self.artifact_dir / "private-codex"
        home.mkdir(mode=0o700, parents=True, exist_ok=True); codex_home.mkdir(mode=0o700, parents=True, exist_ok=True)
        site_dir = self.artifact_dir / "offline-site"; site_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        site = site_dir / "sitecustomize.py"
        if not site.exists():
            site.write_text("import os, socket, subprocess\ndef _blocked(*a,**k): raise RuntimeError('offline socket denied')\nsocket.socket=_blocked\nsocket.create_connection=lambda *a,**k: (_ for _ in ()).throw(RuntimeError('offline network denied'))\ndef _escape(*a,**k): raise RuntimeError('process-group escape denied')\nos.setsid=_escape\nos.setpgid=_escape\n_orig_popen=subprocess.Popen\nclass _GuardedPopen(_orig_popen):\n    def __init__(self,*a,**k):\n        if k.get('start_new_session') or k.get('preexec_fn') is not None: raise RuntimeError('process-group escape denied')\n        super().__init__(*a,**k)\nsubprocess.Popen=_GuardedPopen\n", encoding="utf-8")
            os.chmod(site, 0o600)
        env.update({"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": str(home), "CODEX_HOME": str(codex_home), "PYTHONUNBUFFERED": "1", "PYTHONDONTWRITEBYTECODE": "1", "PYTHONNOUSERSITE": "1", "PYTHONPATH": os.pathsep.join((str(site_dir), str(self.root)))})
        declared = entry.get("env", {})
        if not isinstance(declared, Mapping):
            raise GateRunError("entrypoint environment must be a mapping")
        for key, value in declared.items():
            if key in {"HOME", "CODEX_HOME", "PYTHONPATH", "ALL_PROXY", "HTTP_PROXY", "HTTPS_PROXY", "all_proxy", "http_proxy", "https_proxy"}:
                raise GateRunError("forbidden environment override")
            if not isinstance(key, str) or not isinstance(value, str) or key not in allowed_host | {"PYTHONUNBUFFERED", "PYTHONDONTWRITEBYTECODE", "PYTHONNOUSERSITE"}:
                raise GateRunError("environment key outside explicit allowlist")
            env[key] = value
        if env_overrides:
            for key, value in env_overrides.items():
                if key not in env or key not in allowed_host or not isinstance(value, str):
                    raise GateRunError("environment override outside explicit allowlist")
                env[key] = value
        return env

    @staticmethod
    def _terminate_group_receipt(proc: subprocess.Popen[bytes], *, timeout: float = 1.0) -> tuple[bool, bool, bool]:
        """TERM then KILL an owned process group and prove no survivor.

        A parent can exit promptly after TERM while an orphaned child ignores
        it.  We therefore inspect the process group independently of the
        leader's ``poll()`` state and issue KILL whenever the group remains.
        The returned tuple is ``(no_survivor, term_sent, kill_sent)`` for the
        structured gate receipt.
        """
        term_sent = False
        kill_sent = False

        def group_exists() -> bool:
            try:
                os.killpg(proc.pid, 0)
            except ProcessLookupError:
                return False
            except PermissionError:
                # Ownership cannot be proven; fail closed as a survivor.
                return True
            return True

        if group_exists():
            try:
                os.killpg(proc.pid, signal.SIGTERM)
                term_sent = True
            except ProcessLookupError:
                pass
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and proc.poll() is None:
            time.sleep(0.01)
        if proc.poll() is None:
            try:
                proc.wait(timeout=max(0.01, timeout))
            except subprocess.TimeoutExpired:
                pass
        if group_exists():
            try:
                os.killpg(proc.pid, signal.SIGKILL)
                kill_sent = True
            except ProcessLookupError:
                pass
            try:
                proc.wait(timeout=max(0.01, timeout))
            except subprocess.TimeoutExpired:
                pass
            # Give the kernel a bounded opportunity to reap the group before
            # the final identity check; never wait indefinitely on an unknown
            # process.
            reap_deadline = time.monotonic() + min(timeout, 0.25)
            while time.monotonic() < reap_deadline and group_exists():
                time.sleep(0.01)
        return (not group_exists(), term_sent, kill_sent)

    @staticmethod
    def _terminate_group(proc: subprocess.Popen[bytes], *, timeout: float = 1.0) -> bool:
        """Compatibility wrapper returning only the no-survivor result."""
        return GateRunner._terminate_group_receipt(proc, timeout=timeout)[0]

    def run_gate(self, gate_id: str, *, expected_head: str, expected_tree: str | None = None, force: bool = False, env_overrides: Mapping[str, str] | None = None) -> dict[str, Any]:
        gates = {g["id"]: g for g in self.plan["gates"]}
        entries = {e["id"]: e for e in self.plan["entrypoints"]}
        if gate_id not in gates:
            raise GateRunError("unknown gate")
        gate = gates[gate_id]
        for dep in gate["depends_on"]:
            if dep not in self.results:
                raise GateRunError(f"dependency {dep} has not run")
            if self.results[dep]["decision"] not in {"allow", "reused"}:
                try:
                    skipped_head, skipped_tree, skipped_dirty = git_identity(self.root)
                except Exception:
                    skipped_head, skipped_tree, skipped_dirty = expected_head, "0" * 40, True
                result = {"schema": "gate-result.v16", "gate_id": gate_id, "stage": gate["stage"], "decision": "skipped", "reason": "DEPENDENCY_RED", "expected_head": expected_head, "actual_head": skipped_head, "tree_sha": skipped_tree, "dirty": skipped_dirty, "started_at": _utc(), "ended_at": _utc(), "elapsed_sec": 0.0, "rows": []}
                self.results[gate_id] = result
                return result
        actual_head, tree_sha, dirty = git_identity(self.root)
        if actual_head != expected_head:
            raise GateRunError("head changed before gate")
        if expected_tree and tree_sha != expected_tree:
            raise GateRunError("tree changed before gate")
        if dirty and expected_tree is not None:
            raise GateRunError("dirty worktree before gate")
        if not force and gate_id in self.results and self.results[gate_id].get("actual_head") == actual_head and self.results[gate_id].get("decision") == "allow":
            cached = dict(self.results[gate_id]); cached["decision"] = "reused"; self.results[gate_id] = cached; return cached
        all_rows: list[dict[str, Any]] = []
        gate_start = time.monotonic(); start = _utc()
        for entry_id in gate["entrypoint_ids"]:
            entry = entries[entry_id]
            command = list(entry["argv"])
            if not isinstance(command, list) or not command or any(not isinstance(x, str) for x in command):
                raise GateRunError("direct argv required")
            executable = pathlib.Path(command[0]).name.lower()
            if executable in {"python", "python3", "python3.9", "python3.10", "python3.11", "python3.12"}:
                command[0] = self._interpreter
            elif command[0] != self._interpreter and not executable.startswith("codex-"):
                raise GateRunError("entrypoint executable is not the exact allowed interpreter")
            cwd = (self.root / entry["cwd"]).resolve()
            try:
                cwd.relative_to(self.root)
            except ValueError:
                raise GateRunError("entrypoint cwd escapes repository")
            env = self._environment(entry, env_overrides)
            entry_start = _utc(); entry_clock = time.monotonic(); timed_out = False; survivor = False; term_sent = False; kill_sent = False
            proc: subprocess.Popen[bytes] | None = None
            try:
                proc = subprocess.Popen(command, cwd=str(cwd), env=env, shell=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
                try:
                    out, err = proc.communicate(timeout=float(entry["timeout_sec"]))
                    status = proc.returncode
                except subprocess.TimeoutExpired as exc:
                    timed_out = True; clean, term_sent, kill_sent = self._terminate_group_receipt(proc); survivor = not clean; status = 124
                    try:
                        drained_out, drained_err = proc.communicate(timeout=1.0)
                    except subprocess.TimeoutExpired:
                        drained_out, drained_err = b"", b""
                    out = (exc.stdout or b"") + (drained_out or b"")
                    err = (exc.stderr or b"") + (drained_err or b"") + b"\nTIMEOUT\n"
            except OSError as exc:
                status = 127; out = b""; err = str(exc).encode("utf-8", "replace")
            # Bind each log to its gate/entrypoint without changing the
            # structured checker bytes parsed above.  This prevents identical
            # counts from being accepted merely because a copied log hash is
            # reused across staged gates.
            log_out = out + f"\n[V16 gate={gate_id} entry={entry_id}]\n".encode("utf-8")
            rel_out, out_size, out_mode, out_sha = self._write_log(gate_id, entry_id, "stdout", log_out)
            rel_err, err_size, err_mode, err_sha = self._write_log(gate_id, entry_id, "stderr", err)
            if privacy_scan(out) or privacy_scan(err):
                privacy_error = "privacy-red"
            else:
                privacy_error = ""
            raw = out.decode("utf-8", errors="replace").strip()
            try:
                checker = json.loads(raw)
                # The standalone checker emits a typed envelope while the
                # gate receipt stores only its exact arithmetic fields.  Strip
                # that one documented schema tag before the strict count
                # validator; every other extra key remains a hard RED.
                if isinstance(checker, dict) and checker.get("schema") == "checker-result.v16":
                    checker = {key: value for key, value in checker.items() if key != "schema"}
                elif isinstance(checker, dict) and isinstance(checker.get("files"), int) and checker.get("status") in {"GREEN", "RED"} and isinstance(checker.get("errors"), list):
                    # ``verify-governance.py`` reports an exact file
                    # denominator rather than checker count keys.  Preserve
                    # its source payload for _run_json while binding the gate
                    # row to the verifier's known file count and status.
                    total = checker["files"]
                    passed = total if checker["status"] == "GREEN" and not checker["errors"] else 0
                    checker = {"total": total, "ran": total, "passed": passed, "failed": total - passed, "skipped": 0, "xfail": 0, "unknown": 0}
                counts = validate_counts(checker, require_green=False)
            except (ValueError, json.JSONDecodeError, EvidenceError) as exc:
                checker = {}; counts = {"total": 0, "ran": 0, "passed": 0, "failed": 1, "skipped": 0, "xfail": 0, "unknown": 0}; parse_error = str(exc)
            else:
                parse_error = ""
            try:
                after_head, after_tree, after_dirty = git_identity(self.root)
            except Exception as exc:
                after_head, after_tree, after_dirty = "", "", True
                identity_error = str(exc)
            else:
                identity_error = "" if (after_head == expected_head and after_tree == tree_sha and (expected_tree is None or not after_dirty)) else "post-run identity drift"
            if timed_out or survivor or status != 0:
                decision = "deny"
            elif privacy_error or identity_error or parse_error or counts["failed"] or counts["skipped"] or counts["unknown"] or counts["xfail"]:
                decision = "deny"
            else:
                decision = "allow"
            all_rows.append({"schema": "gate-row.v16", "gate_id": gate_id, "entrypoint_id": entry_id, "stage": gate["stage"], "decision": decision, "expected_head": expected_head, "actual_head": after_head or actual_head, "tree_sha": after_tree or tree_sha, "dirty": after_dirty, "command": command, "cwd": entry["cwd"], "runtime": "python-stdlib-offline", "config": "mission-plan", "started_at": entry_start, "ended_at": _utc(), "elapsed_sec": round(time.monotonic() - entry_clock, 6), "exit_status": status, "counts": counts, "log_paths": [rel_out, rel_err], "log_shas": [out_sha, err_sha], "log_sizes": [out_size, err_size], "log_modes": [out_mode, err_mode], "parse_error": parse_error, "privacy_error": privacy_error, "identity_error": identity_error, "survivor": survivor, "timed_out": timed_out, "term_sent": term_sent, "kill_sent": kill_sent})
            if decision != "allow":
                break
        overall = "allow" if all(r["decision"] == "allow" for r in all_rows) and len(all_rows) == len(gate["entrypoint_ids"]) else "deny"
        final_head, final_tree, final_dirty = git_identity(self.root)
        if overall == "allow" and (final_head != expected_head or (expected_tree and final_tree != expected_tree) or final_tree != tree_sha or (expected_tree is not None and final_dirty)):
            overall = "deny"
        result = {"schema": "gate-result.v16", "gate_id": gate_id, "stage": gate["stage"], "decision": overall, "expected_head": expected_head, "actual_head": final_head, "tree_sha": final_tree, "dirty": final_dirty, "started_at": start, "ended_at": _utc(), "elapsed_sec": round(time.monotonic() - gate_start, 6), "rows": all_rows}
        self.results[gate_id] = result
        return result

    def run_plan(self, *, expected_head: str, expected_tree: str | None = None, gate_ids: list[str] | None = None, force: bool = False) -> dict[str, Any]:
        selected = set(gate_ids) if gate_ids else set(self.plan["gate_order"])
        for gate_id in self.plan["gate_order"]:
            if gate_id not in selected:
                continue
            try:
                self.run_gate(gate_id, expected_head=expected_head, expected_tree=expected_tree, force=force)
            except GateRunError as exc:
                try:
                    error_head, error_tree, error_dirty = git_identity(self.root)
                except Exception:
                    error_head, error_tree, error_dirty = expected_head, expected_tree or ("0" * 40), True
                self.results[gate_id] = {"schema": "gate-result.v16", "gate_id": gate_id, "stage": next((g["stage"] for g in self.plan["gates"] if g["id"] == gate_id), "targeted"), "decision": "deny", "reason": str(exc), "expected_head": expected_head, "actual_head": error_head, "tree_sha": error_tree, "dirty": error_dirty, "started_at": _utc(), "ended_at": _utc(), "elapsed_sec": 0.0, "rows": []}
                # Preserve an explicit stop receipt for every dependent rather
                # than silently omitting it from the machine envelope.
                order = self.plan["gate_order"]
                start = order.index(gate_id)
                for dependent in order[start + 1:]:
                    if dependent in selected and dependent not in self.results:
                        try:
                            skip_head, skip_tree, skip_dirty = git_identity(self.root)
                        except Exception:
                            skip_head, skip_tree, skip_dirty = expected_head, expected_tree or ("0" * 40), True
                        self.results[dependent] = {"schema": "gate-result.v16", "gate_id": dependent, "stage": next((g["stage"] for g in self.plan["gates"] if g["id"] == dependent), "fresh"), "decision": "skipped", "reason": "DEPENDENCY_RED", "expected_head": expected_head, "actual_head": skip_head, "tree_sha": skip_tree, "dirty": skip_dirty, "started_at": _utc(), "ended_at": _utc(), "elapsed_sec": 0.0, "rows": []}
                break
        return {"schema": "gate-run.v16", "expected_head": expected_head, "results": [self.results[g] for g in self.plan["gate_order"] if g in self.results]}


def validate_gate_result(value: Mapping[str, Any], *, expected_head: str | None = None, expected_tree: str | None = None, artifact_root: pathlib.Path | None = None, plan: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Strict receipt validator for a single gate result."""
    if not isinstance(value, Mapping):
        raise GateRunError("gate result object required")
    required = {"schema", "gate_id", "stage", "decision", "expected_head", "actual_head", "tree_sha", "dirty", "started_at", "ended_at", "elapsed_sec", "rows"}
    allowed = required | {"reason"}
    if set(value) != required and not (set(value) == allowed and isinstance(value.get("reason"), str)):
        raise GateRunError("missing/additional gate result fields")
    if value["schema"] != "gate-result.v16" or value["decision"] not in {"allow", "deny", "reused", "skipped"}:
        raise GateRunError("gate result schema/decision")
    if not isinstance(value["gate_id"], str) or not value["gate_id"] or not isinstance(value["stage"], str) or value["stage"] not in {"targeted", "full", "fresh"}:
        raise GateRunError("gate ID")
    plan_gate = None
    plan_entries: dict[str, Mapping[str, Any]] = {}
    if plan is not None:
        if not isinstance(plan, Mapping) or plan.get("schema") != "compiled-plan.v16":
            raise GateRunError("compiled plan binding required")
        plan_gate = next((gate for gate in plan.get("gates", []) if isinstance(gate, Mapping) and gate.get("id") == value["gate_id"]), None)
        if plan_gate is None or plan_gate.get("stage") != value["stage"]:
            raise GateRunError("gate not present in compiled plan")
        plan_entries = {str(entry.get("id")): entry for entry in plan.get("entrypoints", []) if isinstance(entry, Mapping) and isinstance(entry.get("id"), str)}
        expected_entry_ids = list(plan_gate.get("entrypoint_ids", []))
    else:
        expected_entry_ids = []

    def exact_sha(value_: Any, label: str) -> str:
        if not isinstance(value_, str) or re.fullmatch(r"[0-9a-f]{40}", value_) is None:
            raise GateRunError(f"exact {label}")
        return value_

    expected = exact_sha(value["expected_head"], "expected head")
    actual = exact_sha(value["actual_head"], "actual head")
    tree = exact_sha(value["tree_sha"], "tree SHA")
    if expected_head and value["expected_head"] != expected_head:
        raise GateRunError("expected head mismatch")
    if expected_head and actual != expected_head and value["decision"] in {"allow", "reused"}:
        raise GateRunError("actual head mismatch")
    if expected_tree and value["tree_sha"] != expected_tree:
        raise GateRunError("tree mismatch")
    if type(value["dirty"]) is not bool:
        raise GateRunError("dirty must be boolean")
    if type(value["elapsed_sec"]) not in (int, float) or isinstance(value["elapsed_sec"], bool) or not math.isfinite(float(value["elapsed_sec"])) or value["elapsed_sec"] < 0:
        raise GateRunError("finite elapsed seconds required")

    def timestamp(value_: Any, label: str) -> datetime:
        if not isinstance(value_, str) or re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z", value_) is None:
            raise GateRunError(f"RFC3339 UTC timestamp required: {label}")
        try:
            parsed = datetime.fromisoformat(value_[:-1] + "+00:00")
        except ValueError as exc:
            raise GateRunError(f"RFC3339 UTC timestamp required: {label}") from exc
        if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
            raise GateRunError(f"UTC timestamp required: {label}")
        return parsed

    started = timestamp(value["started_at"], "started_at")
    ended = timestamp(value["ended_at"], "ended_at")
    if ended < started:
        raise GateRunError("ended_at precedes started_at")
    observed = (ended - started).total_seconds()
    if abs(float(value["elapsed_sec"]) - observed) > max(0.05, observed * 0.05):
        raise GateRunError("elapsed/timestamp mismatch")
    rows = value["rows"]
    if not isinstance(rows, list):
        raise GateRunError("rows array required")
    if rows and artifact_root is None:
        raise GateRunError("artifact_root required for gate receipt acceptance")

    row_allowed = {"schema", "gate_id", "entrypoint_id", "stage", "decision", "expected_head", "actual_head", "tree_sha", "dirty", "command", "cwd", "runtime", "config", "started_at", "ended_at", "elapsed_sec", "exit_status", "counts", "log_paths", "log_shas", "log_sizes", "log_modes", "parse_error", "privacy_error", "identity_error", "survivor", "timed_out", "term_sent", "kill_sent"}
    for index, row in enumerate(rows):
        if not isinstance(row, Mapping) or set(row) != row_allowed:
            raise GateRunError(f"strict gate row fields [{index}]")
        if row["schema"] != "gate-row.v16" or row["gate_id"] != value["gate_id"] or row["stage"] != value["stage"] or row["decision"] not in {"allow", "deny"}:
            raise GateRunError(f"gate row schema/decision [{index}]")
        if row["expected_head"] != expected:
            raise GateRunError(f"gate row expected head mismatch [{index}]")
        row_actual = exact_sha(row["actual_head"], f"gate row actual head [{index}]")
        row_tree = exact_sha(row["tree_sha"], f"gate row tree [{index}]")
        if row_actual != actual or row_tree != tree:
            raise GateRunError(f"after-run identity mismatch [{index}]")
        if type(row["dirty"]) is not bool:
            raise GateRunError(f"gate row dirty must be boolean [{index}]")
        if not isinstance(row["entrypoint_id"], str) or not row["entrypoint_id"]:
            raise GateRunError(f"gate row entrypoint [{index}]")
        command = row["command"]
        if not isinstance(command, list) or not command or any(not isinstance(item, str) or not item for item in command):
            raise GateRunError(f"gate row direct argv [{index}]")
        if plan is not None:
            if row["entrypoint_id"] not in expected_entry_ids or row["entrypoint_id"] not in plan_entries:
                raise GateRunError(f"gate row entrypoint is not in compiled gate [{index}]")
            entry = plan_entries[row["entrypoint_id"]]
            expected_command = list(entry.get("argv", []))
            if expected_command and pathlib.Path(expected_command[0]).name.lower() in {"python", "python3", "python3.9", "python3.10", "python3.11", "python3.12"}:
                expected_command[0] = pathlib.Path(sys.executable).resolve().as_posix()
            if command != expected_command or row["cwd"] != entry.get("cwd"):
                raise GateRunError(f"gate row command/cwd does not bind compiled plan [{index}]")
        if not isinstance(row["cwd"], str) or row["cwd"].startswith(("/", "~")) or ".." in pathlib.PurePosixPath(row["cwd"]).parts:
            raise GateRunError(f"gate row cwd [{index}]")
        for label in ("runtime", "config", "parse_error", "privacy_error", "identity_error"):
            if not isinstance(row[label], str):
                raise GateRunError(f"gate row {label} [{index}]")
        row_started = timestamp(row["started_at"], f"rows[{index}].started_at")
        row_ended = timestamp(row["ended_at"], f"rows[{index}].ended_at")
        if row_ended < row_started:
            raise GateRunError(f"gate row ended_at precedes started_at [{index}]")
        row_elapsed = row["elapsed_sec"]
        if type(row_elapsed) not in (int, float) or isinstance(row_elapsed, bool) or not math.isfinite(float(row_elapsed)) or row_elapsed < 0:
            raise GateRunError(f"gate row elapsed [{index}]")
        row_observed = (row_ended - row_started).total_seconds()
        if abs(float(row_elapsed) - row_observed) > max(0.05, row_observed * 0.05):
            raise GateRunError(f"gate row elapsed/timestamp mismatch [{index}]")
        if type(row["exit_status"]) is not int or isinstance(row["exit_status"], bool) or row["exit_status"] < -255 or row["exit_status"] > 255:
            raise GateRunError(f"gate row exit status [{index}]")
        try:
            counts = validate_counts(row["counts"], require_green=False)
        except EvidenceError as exc:
            raise GateRunError(f"gate row counts [{index}]") from exc
        if counts["xfail"] > counts["total"]:
            raise GateRunError(f"gate row xfail arithmetic [{index}]")
        for label, width in (("log_paths", None), ("log_shas", 64), ("log_sizes", None), ("log_modes", None)):
            if not isinstance(row[label], list) or len(row[label]) != 2:
                raise GateRunError(f"gate row {label} [{index}]")
            if label == "log_paths" and any(not isinstance(item, str) or not item or item.startswith(("/", "~")) or ".." in pathlib.PurePosixPath(item).parts for item in row[label]):
                raise GateRunError(f"gate row log paths [{index}]")
            if label == "log_shas" and any(not isinstance(item, str) or re.fullmatch(r"[0-9a-f]{64}", item) is None for item in row[label]):
                raise GateRunError(f"gate row log hashes [{index}]")
            if label == "log_sizes" and any(type(item) is not int or item < 0 for item in row[label]):
                raise GateRunError(f"gate row log sizes [{index}]")
            if label == "log_modes" and any(type(item) is not int or item < 0 or item > 0o777 for item in row[label]):
                raise GateRunError(f"gate row log modes [{index}]")
        if artifact_root is not None:
            root_path = pathlib.Path(artifact_root).resolve()
            if root_path.is_symlink() or not root_path.is_dir():
                raise GateRunError("artifact root must be a regular directory")
            for rel, expected_sha, expected_size, expected_mode in zip(row["log_paths"], row["log_shas"], row["log_sizes"], row["log_modes"]):
                path = (root_path / rel).resolve()
                try:
                    path.relative_to(root_path)
                except ValueError:
                    raise GateRunError(f"gate log escapes artifact root [{index}]")
                if path.is_symlink() or not path.is_file():
                    raise GateRunError(f"gate log missing/nonregular [{index}]")
                stat = path.stat()
                if stat.st_size != expected_size or stat.st_mode & 0o777 != expected_mode or _sha_bytes(path.read_bytes()) != expected_sha:
                    raise GateRunError(f"gate log identity mismatch [{index}]")
        for label in ("survivor", "timed_out", "term_sent", "kill_sent"):
            if type(row[label]) is not bool:
                raise GateRunError(f"gate row {label} must be boolean [{index}]")
        if row["kill_sent"] and not row["term_sent"]:
            raise GateRunError(f"gate row KILL without TERM [{index}]")
        green_row = row["exit_status"] == 0 and not any(counts[k] for k in ("failed", "skipped", "unknown", "xfail")) and not row["dirty"] and not row["survivor"] and not row["timed_out"] and not row["parse_error"] and not row["privacy_error"] and not row["identity_error"]
        if row["decision"] == "allow" and not green_row:
            raise GateRunError(f"allow row contradicts status/counts [{index}]")
        if row["decision"] == "deny" and green_row:
            raise GateRunError(f"deny row is fully green [{index}]")

    if value["decision"] in {"allow", "reused"}:
        if not rows or any(row["decision"] != "allow" for row in rows) or value["dirty"] or (expected_head and actual != expected_head):
            raise GateRunError("allow/reused decision contradicts rows/identity")
    elif value["decision"] == "skipped":
        if rows or value.get("reason") != "DEPENDENCY_RED":
            raise GateRunError("skipped decision requires dependency-red empty receipt")
    elif rows and not any(row["decision"] == "deny" for row in rows):
        raise GateRunError("deny decision requires a red row or explicit reason")
    return dict(value)

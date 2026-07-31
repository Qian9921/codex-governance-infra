"""Foreground, direct-argv gate execution with identity-bound receipts."""
from __future__ import annotations

import hashlib
import json
import os
import pathlib
import shutil
import signal
import stat
import subprocess
import sys
import time
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from types import MappingProxyType
from typing import Any, Mapping
import math
import re

from .contracts import (
    ContractError,
    canonical_json,
    canonical_sha256,
    validate_closure_binding_receipt,
)
from .compiler import gate_ids_for_tier
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


def content_snapshot(root: pathlib.Path, mode: str = "worktree") -> str:
    """Return an exact content-address for a staged or worktree snapshot.

    This is intentionally distinct from ``HEAD^{tree}``: FAST may validate a
    dirty implementation before it is committed.  Git's index supplies staged
    bytes; worktree mode reads the checked-out bytes for tracked/untracked
    non-ignored files.  Neither mode hashes ``.git`` or runner artifacts.
    """
    if mode not in {"staged", "worktree"}:
        raise GateRunError("snapshot mode must be staged/worktree")
    digest = hashlib.sha256()
    digest.update(f"v16-{mode}\0".encode("ascii"))
    try:
        if mode == "staged":
            # Index mode, blob IDs and raw paths completely represent the
            # staged snapshot and intentionally exclude unstaged/untracked
            # worktree bytes.
            payload = subprocess.run(
                ["git", "ls-files", "--stage", "-z"], cwd=str(root),
                check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            ).stdout
            digest.update(payload)
            return digest.hexdigest()
        payload = subprocess.run(
            ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
            cwd=str(root), check=True, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        ).stdout
    except (OSError, subprocess.CalledProcessError) as exc:
        raise GateRunError("unable to enumerate content snapshot") from exc
    for relative_bytes in sorted(path for path in payload.split(b"\0") if path):
        path = root / os.fsdecode(relative_bytes)
        try:
            metadata = path.lstat()
        except FileNotFoundError:
            kind = b"missing"
            mode_bits = 0
            source = b""
        else:
            mode_bits = stat.S_IMODE(metadata.st_mode)
            if stat.S_ISREG(metadata.st_mode):
                kind = b"file"
                source = path.read_bytes()
            elif stat.S_ISLNK(metadata.st_mode):
                kind = b"symlink"
                source = os.fsencode(os.readlink(path))
            else:
                raise GateRunError("snapshot path must be a file, symlink, or deletion")
        digest.update(len(relative_bytes).to_bytes(8, "big")); digest.update(relative_bytes)
        digest.update(len(kind).to_bytes(8, "big")); digest.update(kind)
        digest.update(mode_bits.to_bytes(4, "big"))
        digest.update(len(source).to_bytes(8, "big")); digest.update(source)
    return digest.hexdigest()


def _materialized_execution_snapshot(root: pathlib.Path) -> str:
    """Bind staged execution to its isolated HEAD, index, and worktree bytes."""
    try:
        head = _git(root, "rev-parse", "HEAD")
    except GateRunError as exc:
        raise GateRunError("materialized snapshot lacks isolated Git identity") from exc
    digest = hashlib.sha256()
    digest.update(b"v16-materialized-all-files\0")
    for directory, names, files in os.walk(root, followlinks=False):
        directory_path = pathlib.Path(directory)
        if directory_path == root and ".git" in names:
            names.remove(".git")
        names.sort()
        candidates = []
        for name in sorted([*names, *files]):
            path = directory_path / name
            if path.is_symlink() or path.is_file():
                candidates.append(path)
        for path in candidates:
            relative = os.fsencode(path.relative_to(root))
            metadata = path.lstat()
            if stat.S_ISLNK(metadata.st_mode):
                kind = b"symlink"
                payload = os.fsencode(os.readlink(path))
            elif stat.S_ISREG(metadata.st_mode):
                kind = b"file"
                payload = path.read_bytes()
            else:
                raise GateRunError("materialized snapshot path type")
            digest.update(len(relative).to_bytes(8, "big")); digest.update(relative)
            digest.update(len(kind).to_bytes(8, "big")); digest.update(kind)
            digest.update(stat.S_IMODE(metadata.st_mode).to_bytes(4, "big"))
            digest.update(len(payload).to_bytes(8, "big")); digest.update(payload)
    return canonical_sha256({
        "head": head,
        "index": content_snapshot(root, "staged"),
        "all_files": digest.hexdigest(),
    })


class GateRunner:
    def __init__(
        self,
        root: str | pathlib.Path,
        plan: Mapping[str, Any],
        artifact_dir: str | pathlib.Path,
        *,
        max_workers: int = 1,
        closure_binding_receipt: Mapping[str, Any] | None = None,
        _closure_binding_plan: Mapping[str, Any] | None = None,
    ):
        self.root = pathlib.Path(root).resolve()
        self.plan = dict(plan)
        requested_artifact_dir = pathlib.Path(artifact_dir)
        if requested_artifact_dir.is_symlink():
            raise GateRunError("artifact root symlink forbidden")
        self.artifact_dir = requested_artifact_dir.resolve()
        try:
            self.artifact_dir.relative_to(self.root)
        except ValueError:
            pass
        else:
            raise GateRunError("artifact root must be outside the repository")
        self.artifact_dir.mkdir(parents=True, exist_ok=True)
        self.results: dict[str, dict[str, Any]] = {}
        self._interpreter = str(pathlib.Path(sys.executable).resolve())
        if type(max_workers) is not int or not 1 <= max_workers <= 16:
            raise GateRunError("max_workers must be an integer in [1,16]")
        self.max_workers = max_workers
        self._results_lock = threading.RLock()
        self.closure_binding_receipt: dict[str, Any] | None = None
        self._closure_bindings: dict[tuple[str, str], tuple[str, ...]] = {}
        if closure_binding_receipt is not None:
            authority_plan = (
                self.plan
                if _closure_binding_plan is None
                else dict(_closure_binding_plan)
            )
            try:
                receipt = validate_closure_binding_receipt(
                    closure_binding_receipt,
                    expected_compiled_plan_sha256=canonical_sha256(authority_plan),
                )
            except ContractError as exc:
                raise GateRunError("invalid closure binding receipt") from exc
            gates = {
                gate.get("id"): gate
                for gate in authority_plan.get("gates", [])
                if isinstance(gate, Mapping)
            }
            entries = {
                entry.get("id")
                for entry in authority_plan.get("entrypoints", [])
                if isinstance(entry, Mapping)
            }
            grouped: dict[tuple[str, str], list[str]] = {}
            for binding in receipt["bindings"]:
                gate = gates.get(binding["gate_id"])
                if (
                    gate is None
                    or gate.get("stage") != binding["stage"]
                    or binding["entrypoint_id"] not in entries
                    or binding["entrypoint_id"] not in gate.get("entrypoint_ids", [])
                ):
                    raise GateRunError("closure binding is outside compiled plan")
                grouped.setdefault(
                    (binding["gate_id"], binding["entrypoint_id"]),
                    [],
                ).append(binding["binding_sha256"])
            self.closure_binding_receipt = receipt
            self._closure_bindings = {
                key: tuple(sorted(digests))
                for key, digests in grouped.items()
            }

    def _closure_fields(self, gate_id: str, entrypoint_id: str | None = None) -> dict[str, Any]:
        if self.closure_binding_receipt is None:
            return {}
        if entrypoint_id is None:
            digests = sorted(
                digest
                for (bound_gate_id, _), values in self._closure_bindings.items()
                if bound_gate_id == gate_id
                for digest in values
            )
        else:
            digests = list(self._closure_bindings.get((gate_id, entrypoint_id), ()))
        return {
            "closure_binding_receipt_sha256": self.closure_binding_receipt["receipt_sha256"],
            "closure_binding_sha256s": digests,
        }

    def _staged_execution_root(self, snapshot: str) -> pathlib.Path:
        identity = canonical_sha256({
            "source_root_sha256": _sha_bytes(os.fsencode(self.root)),
            "plan_sha256": canonical_sha256(self.plan),
            "snapshot": snapshot,
        })
        return self.artifact_dir / "staged-snapshots" / identity

    def _internal_environment(self, execution_root: pathlib.Path) -> dict[str, str]:
        return {
            "HOME": str(self.artifact_dir / "private-home"),
            "CODEX_HOME": str(self.artifact_dir / "private-codex"),
            "PYTHONPATH": os.pathsep.join((str(self.artifact_dir / "offline-site"), str(execution_root))),
        }

    def _cache_key(self, gate: Mapping[str, Any], entries: Mapping[str, Mapping[str, Any]], snapshot: str, effective_environments: Mapping[str, Mapping[str, str]]) -> str:
        """Bind reusable receipt reuse to every execution-relevant byte."""
        selected = []
        for entry_id in gate["entrypoint_ids"]:
            entry = entries[entry_id]
            selected.append({
                "id": entry_id,
                "argv": list(entry["argv"]),
                "effective_env": dict(effective_environments[entry_id]),
                "cwd": entry["cwd"],
                "timeout_sec": entry["timeout_sec"],
            })
        return canonical_sha256({
            "snapshot": snapshot,
            "plan": canonical_sha256(self.plan),
            "closure_binding_receipt_sha256": (
                ""
                if self.closure_binding_receipt is None
                else self.closure_binding_receipt["receipt_sha256"]
            ),
            "gate": dict(gate),
            "entrypoints": selected,
            "environment_policy": "isolated-v16.1",
            "runtime": {"executable": self._interpreter, "version": tuple(sys.version_info[:3])},
        })

    def _cached_result(self, key: str, *, expected_head: str, expected_tree: str | None, expected_snapshot: str) -> dict[str, Any] | None:
        path = self.artifact_dir / "receipt-cache" / f"{key}.json"
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(payload, dict) or set(payload) != {"schema", "key", "result"} or payload["schema"] != "receipt-cache.v16" or payload["key"] != key or not isinstance(payload["result"], dict):
            return None
        result = dict(payload["result"])
        if result.get("expected_head") != expected_head or (expected_tree is not None and result.get("tree_sha") != expected_tree) or result.get("decision") != "allow":
            return None
        try:
            validate_gate_result(
                result, expected_head=expected_head, expected_tree=expected_tree,
                expected_snapshot=expected_snapshot, artifact_root=self.artifact_dir,
                plan=self.plan if self.plan.get("schema") == "compiled-plan.v16" else None,
                closure_binding_receipt=self.closure_binding_receipt,
            )
        except GateRunError:
            return None
        result["decision"] = "reused"
        return result

    def _store_cached_result(self, key: str, result: Mapping[str, Any]) -> None:
        folder = self.artifact_dir / "receipt-cache"; folder.mkdir(mode=0o700, parents=True, exist_ok=True)
        path = folder / f"{key}.json"
        temp = folder / f".{key}.{os.getpid()}.tmp"
        temp.write_text(canonical_json({"schema": "receipt-cache.v16", "key": key, "result": dict(result)}) + "\n", encoding="utf-8")
        os.chmod(temp, 0o600); os.replace(temp, path)

    def _write_log(self, gate_id: str, entry_id: str, suffix: str, data: bytes, *, cache_key: str = "") -> tuple[str, int, int, str]:
        folder = self.artifact_dir / "logs" / gate_id / (cache_key or "current")
        if folder.exists() and folder.is_symlink():
            raise GateRunError("log directory symlink forbidden")
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{entry_id}.{suffix}.log"
        if path.exists() and path.is_symlink():
            raise GateRunError("log symlink forbidden")
        path.write_bytes(data)
        os.chmod(path, 0o600)
        return str(path.relative_to(self.artifact_dir)), len(data), path.stat().st_mode & 0o777, _sha_bytes(data)

    def _materialize_staged_snapshot(self, snapshot: str) -> pathlib.Path:
        """Create an isolated local clone whose files/index equal the source index."""
        parent = self.artifact_dir / "staged-snapshots"
        parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        destination = self._staged_execution_root(snapshot)
        marker = parent / f"{destination.name}.owner.json"
        expected_owner = {
            "schema": "staged-materialization-owner.v16",
            "source_root_sha256": _sha_bytes(os.fsencode(self.root)),
            "plan_sha256": canonical_sha256(self.plan),
            "snapshot": snapshot,
        }
        if destination.exists():
            try:
                owner = json.loads(marker.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise GateRunError("existing staged materialization lacks ownership marker") from exc
            if owner != expected_owner or destination.is_symlink():
                raise GateRunError("staged materialization ownership mismatch")
            shutil.rmtree(destination)
            marker.unlink()
        try:
            subprocess.run(
                ["git", "clone", "--quiet", "--no-hardlinks", "--no-checkout", str(self.root), str(destination)],
                check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
            subprocess.run(
                ["git", "checkout-index", "--all", "--force", f"--prefix={destination}{os.sep}"],
                cwd=str(self.root), check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
            subprocess.run(
                ["git", "add", "-A"], cwd=str(destination),
                check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
            source_index = subprocess.run(
                ["git", "ls-files", "--stage", "-z"], cwd=str(self.root),
                check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            ).stdout
            materialized_index = subprocess.run(
                ["git", "ls-files", "--stage", "-z"], cwd=str(destination),
                check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            ).stdout
        except (OSError, subprocess.CalledProcessError) as exc:
            raise GateRunError("unable to materialize staged snapshot") from exc
        if source_index != materialized_index or content_snapshot(self.root, "staged") != snapshot:
            raise GateRunError("staged snapshot changed during materialization")
        marker.write_text(canonical_json(expected_owner) + "\n", encoding="utf-8")
        os.chmod(marker, 0o600)
        return destination

    def _capture_environment(self, entry: Mapping[str, Any], env_overrides: Mapping[str, str] | None, *, execution_root: pathlib.Path) -> Mapping[str, str]:
        """Freeze the exact environment shared by cache identity and Popen."""
        # Never inherit PATH; argv is normalized to the exact interpreter below.
        allowed_host = {"LANG", "LC_ALL", "TZ"}
        env: dict[str, str] = {key: os.environ[key] for key in allowed_host if key in os.environ}
        env.update({"PATH": "/usr/local/bin:/usr/bin:/bin", "PYTHONUNBUFFERED": "1", "PYTHONDONTWRITEBYTECODE": "1", "PYTHONNOUSERSITE": "1"})
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
        env.update(self._internal_environment(execution_root))
        return MappingProxyType(env)

    def _prepare_environment(self, captured: Mapping[str, str], *, execution_root: pathlib.Path) -> Mapping[str, str]:
        """Create private support paths without re-reading any environment."""
        if not isinstance(captured, Mapping) or any(not isinstance(key, str) or not isinstance(value, str) for key, value in captured.items()):
            raise GateRunError("captured environment must be a string mapping")
        expected_internal = self._internal_environment(execution_root)
        if any(captured.get(key) != value for key, value in expected_internal.items()):
            raise GateRunError("captured internal environment mismatch")
        home = self.artifact_dir / "private-home"; codex_home = self.artifact_dir / "private-codex"
        home.mkdir(mode=0o700, parents=True, exist_ok=True); codex_home.mkdir(mode=0o700, parents=True, exist_ok=True)
        site_dir = self.artifact_dir / "offline-site"; site_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        site = site_dir / "sitecustomize.py"
        if not site.exists():
            site.write_text("import os, socket, subprocess\ndef _blocked(*a,**k): raise RuntimeError('offline socket denied')\nsocket.socket=_blocked\nsocket.create_connection=lambda *a,**k: (_ for _ in ()).throw(RuntimeError('offline network denied'))\ndef _escape(*a,**k): raise RuntimeError('process-group escape denied')\nos.setsid=_escape\nos.setpgid=_escape\n_orig_popen=subprocess.Popen\nclass _GuardedPopen(_orig_popen):\n    def __init__(self,*a,**k):\n        if k.get('start_new_session') or k.get('preexec_fn') is not None: raise RuntimeError('process-group escape denied')\n        super().__init__(*a,**k)\nsubprocess.Popen=_GuardedPopen\n", encoding="utf-8")
            os.chmod(site, 0o600)
        return captured

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

    def run_gate(
        self,
        gate_id: str,
        *,
        expected_head: str,
        expected_tree: str | None = None,
        force: bool = False,
        env_overrides: Mapping[str, str] | None = None,
        snapshot_mode: str | None = None,
        expected_snapshot: str | None = None,
        _captured_environments: Mapping[str, Mapping[str, str]] | None = None,
    ) -> dict[str, Any]:
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
                result = {"schema": "gate-result.v16", "gate_id": gate_id, "stage": gate["stage"], "decision": "skipped", "reason": "DEPENDENCY_RED", "expected_head": expected_head, "actual_head": skipped_head, "tree_sha": skipped_tree, "dirty": skipped_dirty, "snapshot_mode": snapshot_mode or "", "snapshot_sha256": expected_snapshot or "", "started_at": _utc(), "ended_at": _utc(), "elapsed_sec": 0.0, "rows": []} | self._closure_fields(gate_id)
                self.results[gate_id] = result
                return result
        actual_head, tree_sha, dirty = git_identity(self.root)
        if actual_head != expected_head:
            raise GateRunError("head changed before gate")
        if expected_tree and tree_sha != expected_tree:
            raise GateRunError("tree changed before gate")
        if dirty and expected_tree is not None and snapshot_mode is None:
            raise GateRunError("dirty worktree before gate")
        snapshot = ""
        # Reusable receipts are always content-addressed, including clean
        # CANDIDATE/FINAL gates.  FAST merely makes the snapshot identity an
        # explicit caller-facing precondition.
        snapshot_kind = snapshot_mode or ("worktree" if gate.get("reusable") else None)
        if snapshot_kind is not None:
            snapshot = content_snapshot(self.root, snapshot_kind)
            if expected_snapshot is not None and snapshot != expected_snapshot:
                raise GateRunError("content snapshot changed before gate")
        cache_execution_root = self._staged_execution_root(snapshot) if snapshot_kind == "staged" else self.root
        if _captured_environments is not None and env_overrides is not None:
            raise GateRunError("captured environments and overrides are mutually exclusive")
        if _captured_environments is None:
            effective_environments = MappingProxyType({
                entry_id: self._capture_environment(
                    entries[entry_id], env_overrides,
                    execution_root=cache_execution_root,
                )
                for entry_id in gate["entrypoint_ids"]
            })
        else:
            if set(_captured_environments) != set(gate["entrypoint_ids"]):
                raise GateRunError("captured environment entrypoint mismatch")
            frozen_environments: dict[str, Mapping[str, str]] = {}
            for entry_id in gate["entrypoint_ids"]:
                captured = _captured_environments[entry_id]
                if not isinstance(captured, Mapping) or any(
                    not isinstance(key, str) or not isinstance(value, str)
                    for key, value in captured.items()
                ):
                    raise GateRunError("captured environment must be a string mapping")
                frozen_environments[entry_id] = MappingProxyType(dict(captured))
            effective_environments = MappingProxyType(frozen_environments)
        cache_key = self._cache_key(
            gate, entries, snapshot, effective_environments,
        ) if gate.get("reusable") and snapshot else ""
        with self._results_lock:
            if not force and cache_key:
                cached = self._cached_result(cache_key, expected_head=expected_head, expected_tree=expected_tree, expected_snapshot=snapshot)
                if cached is not None:
                    self.results[gate_id] = cached
                    return cached
        # Entry points are normally serialized because the frozen mission
        # schema does not claim they are read-only.  An embedding may opt in
        # explicitly; run each as a one-entry gate so the existing timeout,
        # environment, receipt, and identity machinery remains identical.
        if snapshot_kind != "staged" and self._parallel_safe(gate) and len(gate["entrypoint_ids"]) > 1 and self.max_workers > 1:
            started = _utc(); clock = time.monotonic()

            def run_entry(entry_id: str) -> dict[str, Any]:
                # The parent already proved the gate dependencies.  The child
                # is an execution shard of this gate, not an independent DAG.
                child_gate = dict(gate) | {"entrypoint_ids": [entry_id], "depends_on": []}
                child_plan = dict(self.plan) | {"gates": [child_gate], "gate_order": [gate_id]}
                child = GateRunner(
                    self.root,
                    child_plan,
                    self.artifact_dir,
                    max_workers=1,
                    closure_binding_receipt=self.closure_binding_receipt,
                    _closure_binding_plan=self.plan,
                )
                result = child.run_gate(
                    gate_id,
                    expected_head=expected_head,
                    expected_tree=expected_tree,
                    force=force,
                    snapshot_mode=snapshot_mode,
                    expected_snapshot=expected_snapshot,
                    _captured_environments={
                        entry_id: effective_environments[entry_id],
                    },
                )
                return result

            with ThreadPoolExecutor(max_workers=min(self.max_workers, len(gate["entrypoint_ids"]))) as pool:
                child_results = list(pool.map(run_entry, gate["entrypoint_ids"]))
            rows = [row for child in child_results for row in child["rows"]]
            final_head, final_tree, final_dirty = git_identity(self.root)
            final_snapshot = content_snapshot(self.root, snapshot_kind) if snapshot_kind else ""
            identity_errors = []
            if final_head != expected_head:
                identity_errors.append("HEAD_DRIFT")
            if final_tree != tree_sha or (expected_tree and final_tree != expected_tree):
                identity_errors.append("TREE_DRIFT")
            if expected_tree is not None and final_dirty:
                identity_errors.append("DIRTY_WORKTREE")
            if snapshot and final_snapshot != snapshot:
                identity_errors.append("CONTENT_SNAPSHOT_DRIFT")
            if any("materialized content snapshot drift" in row.get("identity_error", "") for row in rows):
                identity_errors.append("MATERIALIZED_CONTENT_SNAPSHOT_DRIFT")
            overall = "allow" if len(rows) == len(gate["entrypoint_ids"]) and all(row["decision"] == "allow" for row in rows) and not identity_errors else "deny"
            result = {"schema": "gate-result.v16", "gate_id": gate_id, "stage": gate["stage"], "decision": overall, "expected_head": expected_head, "actual_head": final_head, "tree_sha": final_tree, "dirty": final_dirty, "snapshot_mode": snapshot_kind or "", "snapshot_sha256": snapshot, "started_at": started, "ended_at": _utc(), "elapsed_sec": round(time.monotonic() - clock, 6), "rows": rows} | self._closure_fields(gate_id)
            if identity_errors:
                result["reason"] = ",".join(identity_errors)
            with self._results_lock:
                self.results[gate_id] = result
                if cache_key and result["decision"] == "allow":
                    self._store_cached_result(cache_key, result)
            return result
        execution_root = self._materialize_staged_snapshot(snapshot) if snapshot_kind == "staged" else self.root
        materialized_snapshot = _materialized_execution_snapshot(execution_root) if snapshot_kind == "staged" else ""
        all_rows: list[dict[str, Any]] = []
        gate_start = time.monotonic(); start = _utc()
        for entry_id in gate["entrypoint_ids"]:
            if materialized_snapshot and _materialized_execution_snapshot(execution_root) != materialized_snapshot:
                raise GateRunError("materialized content changed before entrypoint")
            entry = entries[entry_id]
            command = list(entry["argv"])
            if not isinstance(command, list) or not command or any(not isinstance(x, str) for x in command):
                raise GateRunError("direct argv required")
            executable = pathlib.Path(command[0]).name.lower()
            if executable in {"python", "python3", "python3.9", "python3.10", "python3.11", "python3.12"}:
                command[0] = self._interpreter
            elif command[0] != self._interpreter and not executable.startswith("codex-"):
                raise GateRunError("entrypoint executable is not the exact allowed interpreter")
            cwd = (execution_root / entry["cwd"]).resolve()
            try:
                cwd.relative_to(execution_root)
            except ValueError:
                raise GateRunError("entrypoint cwd escapes repository")
            env = self._prepare_environment(
                effective_environments[entry_id],
                execution_root=execution_root,
            )
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
            rel_out, out_size, out_mode, out_sha = self._write_log(gate_id, entry_id, "stdout", log_out, cache_key=cache_key)
            rel_err, err_size, err_mode, err_sha = self._write_log(gate_id, entry_id, "stderr", err, cache_key=cache_key)
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
                snapshot_after = content_snapshot(self.root, snapshot_kind) if snapshot_kind is not None else snapshot
                identity_errors = []
                if not (after_head == expected_head and after_tree == tree_sha and (expected_tree is None or snapshot_mode is not None or not after_dirty) and snapshot_after == snapshot):
                    identity_errors.append("post-run source identity drift")
                if materialized_snapshot and _materialized_execution_snapshot(execution_root) != materialized_snapshot:
                    identity_errors.append("materialized content snapshot drift")
                identity_error = ",".join(identity_errors)
            if timed_out or survivor or status != 0:
                decision = "deny"
            elif privacy_error or identity_error or parse_error or counts["failed"] or counts["skipped"] or counts["unknown"] or counts["xfail"]:
                decision = "deny"
            else:
                decision = "allow"
            all_rows.append({"schema": "gate-row.v16", "gate_id": gate_id, "entrypoint_id": entry_id, "stage": gate["stage"], "decision": decision, "expected_head": expected_head, "actual_head": after_head or actual_head, "tree_sha": after_tree or tree_sha, "dirty": after_dirty, "snapshot_mode": snapshot_kind or "", "snapshot_sha256": snapshot, "command": command, "cwd": entry["cwd"], "runtime": "python-stdlib-offline", "config": "mission-plan", "started_at": entry_start, "ended_at": _utc(), "elapsed_sec": round(time.monotonic() - entry_clock, 6), "exit_status": status, "counts": counts, "log_paths": [rel_out, rel_err], "log_shas": [out_sha, err_sha], "log_sizes": [out_size, err_size], "log_modes": [out_mode, err_mode], "parse_error": parse_error, "privacy_error": privacy_error, "identity_error": identity_error, "survivor": survivor, "timed_out": timed_out, "term_sent": term_sent, "kill_sent": kill_sent} | self._closure_fields(gate_id, entry_id))
            # A non-skipped gate receipt must account for every frozen
            # entrypoint, including RED rows.  Dependency-red branches are
            # represented separately as skipped gates with no rows above.
        overall = "allow" if all(r["decision"] == "allow" for r in all_rows) and len(all_rows) == len(gate["entrypoint_ids"]) else "deny"
        final_head, final_tree, final_dirty = git_identity(self.root)
        final_snapshot = content_snapshot(self.root, snapshot_kind) if snapshot_kind else ""
        identity_errors = []
        if final_head != expected_head:
            identity_errors.append("HEAD_DRIFT")
        if final_tree != tree_sha or (expected_tree and final_tree != expected_tree):
            identity_errors.append("TREE_DRIFT")
        if expected_tree is not None and final_dirty:
            identity_errors.append("DIRTY_WORKTREE")
        if snapshot and final_snapshot != snapshot:
            identity_errors.append("CONTENT_SNAPSHOT_DRIFT")
        if materialized_snapshot and _materialized_execution_snapshot(execution_root) != materialized_snapshot:
            identity_errors.append("MATERIALIZED_CONTENT_SNAPSHOT_DRIFT")
        if overall == "allow" and identity_errors:
            overall = "deny"
        result = {"schema": "gate-result.v16", "gate_id": gate_id, "stage": gate["stage"], "decision": overall, "expected_head": expected_head, "actual_head": final_head, "tree_sha": final_tree, "dirty": final_dirty, "snapshot_mode": snapshot_kind or "", "snapshot_sha256": snapshot, "started_at": start, "ended_at": _utc(), "elapsed_sec": round(time.monotonic() - gate_start, 6), "rows": all_rows} | self._closure_fields(gate_id)
        if identity_errors:
            result["reason"] = ",".join(identity_errors)
        with self._results_lock:
            self.results[gate_id] = result
            if cache_key and result["decision"] == "allow":
                self._store_cached_result(cache_key, result)
        return result

    def _parallel_safe(self, gate: Mapping[str, Any]) -> bool:
        """Only explicit read-only declarations may share a foreground layer."""
        entries = {entry["id"]: entry for entry in self.plan["entrypoints"]}
        return gate.get("read_only") is True and all(entries[entry_id].get("read_only") is True for entry_id in gate["entrypoint_ids"])

    def run_tier(self, tier: str, *, expected_head: str, snapshot_mode: str | None = None, force: bool = False) -> dict[str, Any]:
        """Run FAST/CANDIDATE/FINAL with FAST content-addressed, not commit-bound."""
        selected = gate_ids_for_tier(self.plan, tier)
        if tier == "FAST":
            if snapshot_mode not in {"staged", "worktree"}:
                raise GateRunError("FAST requires staged or worktree snapshot mode")
            snapshot = content_snapshot(self.root, snapshot_mode)
            return self.run_plan(expected_head=expected_head, gate_ids=selected, force=force, snapshot_mode=snapshot_mode, expected_snapshot=snapshot)
        if snapshot_mode is not None:
            raise GateRunError("only FAST accepts a mutable snapshot")
        actual_head, tree, dirty = git_identity(self.root)
        if actual_head != expected_head or dirty:
            raise GateRunError("CANDIDATE/FINAL require the exact clean candidate")
        return self.run_plan(expected_head=expected_head, expected_tree=tree, gate_ids=selected, force=force)

    def run_plan(self, *, expected_head: str, expected_tree: str | None = None, gate_ids: list[str] | None = None, force: bool = False, snapshot_mode: str | None = None, expected_snapshot: str | None = None) -> dict[str, Any]:
        selected = set(gate_ids) if gate_ids else set(self.plan["gate_order"])
        gates = {gate["id"]: gate for gate in self.plan["gates"]}
        remaining = [gate_id for gate_id in self.plan["gate_order"] if gate_id in selected]
        while remaining:
            ready = [gate_id for gate_id in remaining if all(dependency in self.results for dependency in gates[gate_id]["depends_on"])]
            if not ready:
                raise GateRunError("selected gates lack completed dependencies")
            serial = [
                gate_id for gate_id in ready
                if snapshot_mode == "staged" or not self._parallel_safe(gates[gate_id])
            ]
            parallel = [gate_id for gate_id in ready if gate_id not in serial]
            # A non-read-only gate is always isolated.  Explicit independent
            # read-only gates can use the bounded worker pool; dependencies are
            # checked before the batch and no write-capable gate enters it.
            batches = [[gate_id] for gate_id in serial]
            if parallel:
                batches.append(parallel)
            for batch in batches:
                def invoke(gate_id: str) -> None:
                    try:
                        self.run_gate(gate_id, expected_head=expected_head, expected_tree=expected_tree, force=force, snapshot_mode=snapshot_mode, expected_snapshot=expected_snapshot)
                    except GateRunError as exc:
                        try:
                            error_head, error_tree, error_dirty = git_identity(self.root)
                        except Exception:
                            error_head, error_tree, error_dirty = expected_head, expected_tree or ("0" * 40), True
                        with self._results_lock:
                            self.results[gate_id] = {"schema": "gate-result.v16", "gate_id": gate_id, "stage": gates[gate_id]["stage"], "decision": "deny", "reason": str(exc), "expected_head": expected_head, "actual_head": error_head, "tree_sha": error_tree, "dirty": error_dirty, "snapshot_mode": snapshot_mode or "", "snapshot_sha256": expected_snapshot or "", "started_at": _utc(), "ended_at": _utc(), "elapsed_sec": 0.0, "rows": []} | self._closure_fields(gate_id)
                if len(batch) == 1:
                    invoke(batch[0])
                else:
                    with ThreadPoolExecutor(max_workers=min(self.max_workers, len(batch))) as pool:
                        list(pool.map(invoke, batch))
            remaining = [gate_id for gate_id in remaining if gate_id not in self.results]
            if any(self.results[gate_id]["decision"] not in {"allow", "reused"} for gate_id in ready):
                # The dependency-red receipts still need be explicit, but gates
                # unrelated to the RED branch may finish in their own layer.
                for gate_id in list(remaining):
                    if any(dep in self.results and self.results[dep]["decision"] not in {"allow", "reused"} for dep in gates[gate_id]["depends_on"]):
                        head, tree, dirty = git_identity(self.root)
                        self.results[gate_id] = {"schema": "gate-result.v16", "gate_id": gate_id, "stage": gates[gate_id]["stage"], "decision": "skipped", "reason": "DEPENDENCY_RED", "expected_head": expected_head, "actual_head": head, "tree_sha": tree, "dirty": dirty, "snapshot_mode": snapshot_mode or "", "snapshot_sha256": expected_snapshot or "", "started_at": _utc(), "ended_at": _utc(), "elapsed_sec": 0.0, "rows": []} | self._closure_fields(gate_id)
        return {"schema": "gate-run.v16", "expected_head": expected_head, "results": [self.results[g] for g in self.plan["gate_order"] if g in self.results]}


def validate_gate_result(
    value: Mapping[str, Any],
    *,
    expected_head: str | None = None,
    expected_tree: str | None = None,
    expected_snapshot: str | None = None,
    artifact_root: pathlib.Path | None = None,
    plan: Mapping[str, Any] | None = None,
    closure_binding_receipt: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Strict receipt validator for a single gate result."""
    if not isinstance(value, Mapping):
        raise GateRunError("gate result object required")
    closure_fields = {"closure_binding_receipt_sha256", "closure_binding_sha256s"}
    checked_closure_receipt: dict[str, Any] | None = None
    expected_binding_sets: dict[tuple[str, str], list[str]] = {}
    if closure_binding_receipt is not None:
        if not isinstance(plan, Mapping) or plan.get("schema") != "compiled-plan.v16":
            raise GateRunError("closure-aware gate receipt requires compiled plan")
        try:
            checked_closure_receipt = validate_closure_binding_receipt(
                closure_binding_receipt,
                expected_compiled_plan_sha256=canonical_sha256(plan),
            )
        except ContractError as exc:
            raise GateRunError("invalid closure binding receipt") from exc
        for binding in checked_closure_receipt["bindings"]:
            expected_binding_sets.setdefault(
                (binding["gate_id"], binding["entrypoint_id"]),
                [],
            ).append(binding["binding_sha256"])
        expected_binding_sets = {
            key: sorted(digests)
            for key, digests in expected_binding_sets.items()
        }
    required = {"schema", "gate_id", "stage", "decision", "expected_head", "actual_head", "tree_sha", "dirty", "snapshot_mode", "snapshot_sha256", "started_at", "ended_at", "elapsed_sec", "rows"}
    if checked_closure_receipt is not None:
        required |= closure_fields
    allowed = required | {"reason"}
    if set(value) != required and not (set(value) == allowed and isinstance(value.get("reason"), str)):
        raise GateRunError("missing/additional gate result fields")
    if value["schema"] != "gate-result.v16" or value["decision"] not in {"allow", "deny", "reused", "skipped"}:
        raise GateRunError("gate result schema/decision")
    snapshot_mode = value["snapshot_mode"]
    snapshot_sha = value["snapshot_sha256"]
    if snapshot_mode not in {"", "staged", "worktree"} or not isinstance(snapshot_sha, str):
        raise GateRunError("gate snapshot identity")
    if (snapshot_mode == "") != (snapshot_sha == "") or (snapshot_sha and re.fullmatch(r"[0-9a-f]{64}", snapshot_sha) is None):
        raise GateRunError("gate snapshot identity")
    if expected_snapshot is not None and (re.fullmatch(r"[0-9a-f]{64}", expected_snapshot) is None or snapshot_sha != expected_snapshot):
        raise GateRunError("gate snapshot mismatch")
    if value["dirty"] and (expected_snapshot is None or snapshot_mode == ""):
        raise GateRunError("dirty gate receipt requires caller-bound snapshot")
    if not isinstance(value["gate_id"], str) or not value["gate_id"] or not isinstance(value["stage"], str) or value["stage"] not in {"targeted", "full", "fresh"}:
        raise GateRunError("gate ID")
    if checked_closure_receipt is not None:
        expected_gate_bindings = sorted(
            digest
            for (gate_id, _), digests in expected_binding_sets.items()
            if gate_id == value["gate_id"]
            for digest in digests
        )
        if (
            value["closure_binding_receipt_sha256"] != checked_closure_receipt["receipt_sha256"]
            or value["closure_binding_sha256s"] != expected_gate_bindings
        ):
            raise GateRunError("gate closure binding receipt mismatch")
    plan_gate = None
    plan_entries: dict[str, Mapping[str, Any]] = {}
    if plan is not None:
        if not isinstance(plan, Mapping) or plan.get("schema") != "compiled-plan.v16":
            raise GateRunError("compiled plan binding required")
        plan_gate = next((gate for gate in plan.get("gates", []) if isinstance(gate, Mapping) and gate.get("id") == value["gate_id"]), None)
        if plan_gate is None or plan_gate.get("stage") != value["stage"]:
            raise GateRunError("gate not present in compiled plan")
        plan_entries = {str(entry.get("id")): entry for entry in plan.get("entrypoints", []) if isinstance(entry, Mapping) and isinstance(entry.get("id"), str)}
        frozen_entry_ids = plan_gate.get("entrypoint_ids")
        if (
            not isinstance(frozen_entry_ids, list)
            or any(not isinstance(entry_id, str) or not entry_id for entry_id in frozen_entry_ids)
            or len(set(frozen_entry_ids)) != len(frozen_entry_ids)
        ):
            raise GateRunError("compiled gate entrypoint denominator")
        expected_entry_ids = list(frozen_entry_ids)
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
    if plan is not None and value["decision"] != "skipped":
        # The compiled gate's ordered entrypoint list is the receipt's
        # denominator.  Membership-only checks would accept missing rows,
        # duplicates, or reordered artifacts while still reporting GREEN.
        observed_entry_ids = [
            row.get("entrypoint_id") if isinstance(row, Mapping) else None
            for row in rows
        ]
        if observed_entry_ids != expected_entry_ids:
            raise GateRunError("gate entrypoint denominator/order mismatch")

    row_allowed = {"schema", "gate_id", "entrypoint_id", "stage", "decision", "expected_head", "actual_head", "tree_sha", "dirty", "snapshot_mode", "snapshot_sha256", "command", "cwd", "runtime", "config", "started_at", "ended_at", "elapsed_sec", "exit_status", "counts", "log_paths", "log_shas", "log_sizes", "log_modes", "parse_error", "privacy_error", "identity_error", "survivor", "timed_out", "term_sent", "kill_sent"}
    if checked_closure_receipt is not None:
        row_allowed |= closure_fields
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
        if row["snapshot_mode"] != snapshot_mode or row["snapshot_sha256"] != snapshot_sha:
            raise GateRunError(f"gate row snapshot mismatch [{index}]")
        if not isinstance(row["entrypoint_id"], str) or not row["entrypoint_id"]:
            raise GateRunError(f"gate row entrypoint [{index}]")
        if checked_closure_receipt is not None:
            expected_row_bindings = expected_binding_sets.get(
                (value["gate_id"], row["entrypoint_id"]),
                [],
            )
            if (
                row["closure_binding_receipt_sha256"] != checked_closure_receipt["receipt_sha256"]
                or row["closure_binding_sha256s"] != expected_row_bindings
            ):
                raise GateRunError(f"gate row closure binding receipt mismatch [{index}]")
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
        snapshot_bound = not row["dirty"] or (expected_snapshot is not None and snapshot_sha == expected_snapshot)
        green_row = row["exit_status"] == 0 and not any(counts[k] for k in ("failed", "skipped", "unknown", "xfail")) and snapshot_bound and not row["survivor"] and not row["timed_out"] and not row["parse_error"] and not row["privacy_error"] and not row["identity_error"]
        if row["decision"] == "allow" and not green_row:
            raise GateRunError(f"allow row contradicts status/counts [{index}]")
        if row["decision"] == "deny" and green_row:
            raise GateRunError(f"deny row is fully green [{index}]")

    if value["decision"] in {"allow", "reused"}:
        snapshot_bound = not value["dirty"] or (expected_snapshot is not None and snapshot_sha == expected_snapshot)
        if not rows or any(row["decision"] != "allow" for row in rows) or not snapshot_bound or (expected_head and actual != expected_head):
            raise GateRunError("allow/reused decision contradicts rows/identity")
    elif value["decision"] == "skipped":
        if rows or value.get("reason") != "DEPENDENCY_RED":
            raise GateRunError("skipped decision requires dependency-red empty receipt")
    elif value["decision"] == "deny" and not any(row["decision"] == "deny" for row in rows) and not value.get("reason"):
        raise GateRunError("deny decision requires a red row or explicit reason")
    return dict(value)

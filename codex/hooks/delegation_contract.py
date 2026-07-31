"""Typed delegation packet/result contract and process-safe parent state bridge."""
from __future__ import annotations
import argparse, contextlib, hashlib, json, os, pathlib, re, subprocess, stat, tempfile
try:
    import fcntl
except ImportError:  # pragma: no cover - Linux contract rejects this path
    fcntl = None

class ContractError(ValueError): pass

REQUIRED_PACKET = {"schema","repo_root","repo_snapshot","parent_task_id","child_task_id","assigned_model","role","max_depth","depth","permissions","forbidden_permissions","lease","retry_budget","active_mission_lock","plugin_inventory","result_schema"}
REQUIRED_RESULT = {"schema","parent_task_id","child_task_id","assigned_model","task_id","depth","attempt_id","changed_paths","counts","retry_used","retry_transcript","contamination","status","artifact_sha256","evidence_id"}
SAFE_PERMISSIONS = {"read","write_paths","test","inspect","evidence"}
FORBIDDEN_CANONICAL = {"git","github","review","approve","merge","shell","bash","git_push","github_api","reviewer","approver","merger"}
STATUSES = {"complete","blocked","failed","rejected"}
STATE_SCHEMA = "delegation-state.v2"


def _id(v): return isinstance(v, str) and bool(re.fullmatch(r"[A-Za-z0-9_.:/-]+", v))
def _positive_id(v): return _id(v) and not v.startswith("-")
def _not_bool_int(v): return isinstance(v, int) and not isinstance(v, bool)
def _sha256(v): return isinstance(v, str) and bool(re.fullmatch(r"[0-9a-f]{64}", v))


def _lstat(path: pathlib.Path):
    try: return os.lstat(path)
    except FileNotFoundError: return None


def _chain_no_symlink(path: pathlib.Path, *, allow_missing_leaf=True) -> pathlib.Path:
    path = pathlib.Path(os.path.abspath(os.fspath(path))); cur = pathlib.Path(path.anchor); missing = []
    for part in path.parts[1:]:
        cur /= part; info = _lstat(cur)
        if info is None:
            missing.append(cur); continue
        if stat.S_ISLNK(info.st_mode): raise ContractError("symlink repository/state component")
        if not stat.S_ISDIR(info.st_mode) and cur != path: raise ContractError("non-directory repository ancestor")
    if missing and not allow_missing_leaf: raise ContractError("missing repository path")
    return path


def _git_head(root: pathlib.Path) -> str | None:
    try:
        p = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True, text=True, check=False)
        return p.stdout.strip() if p.returncode == 0 and re.fullmatch(r"[0-9a-f]{40}", p.stdout.strip()) else None
    except OSError: return None


def normalize_path(v):
    if not isinstance(v, str) or not v or "\\" in v or v.startswith("/") or re.match(r"^[A-Za-z]:", v): raise ContractError("non-relative path")
    parts = v.split("/")
    if any(x in ("", ".", "..") for x in parts): raise ContractError("noncanonical path")
    return "/".join(parts)


def _paths(paths):
    if not isinstance(paths, list) or not paths: raise ContractError("lease paths")
    out = [normalize_path(x) for x in paths]
    if len(set(out)) != len(out): raise ContractError("duplicate lease")
    for i, a in enumerate(out):
        for b in out[i+1:]:
            if a == b or a.startswith(b + "/") or b.startswith(a + "/"): raise ContractError("overlapping lease")
    return out


def _validate_repo_and_lease(packet, verify_snapshot=False):
    root_raw = packet.get("repo_root")
    if not isinstance(root_raw, str) or not os.path.isabs(root_raw): raise ContractError("repo root")
    root = pathlib.Path(root_raw)
    _chain_no_symlink(root, allow_missing_leaf=False)
    if not root.is_dir() or pathlib.Path(os.path.realpath(root)) != root: raise ContractError("repo root canonical")
    snap = packet.get("repo_snapshot")
    if not (isinstance(snap, str) and re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", snap)): raise ContractError("repo snapshot")
    head = _git_head(root)
    if verify_snapshot and head and snap != "0" * 40 and snap != head: raise ContractError("repo snapshot mismatch")
    lease = packet.get("lease")
    paths = _paths(lease.get("paths") if isinstance(lease, dict) else None)
    for rel in paths:
        cur = root
        components = rel.split("/")
        for i, part in enumerate(components):
            cur /= part; info = _lstat(cur)
            if info is None:
                if i != len(components) - 1: raise ContractError("missing lease ancestor")
                break  # missing leaf is allowed
            if stat.S_ISLNK(info.st_mode): raise ContractError("symlink lease path")
            if i != len(components) - 1 and not stat.S_ISDIR(info.st_mode): raise ContractError("lease ancestor not directory")
    return paths


def validate_packet(packet, parent_task_id=None, active_leases=None, *, verify_snapshot=False):
    if not isinstance(packet, dict) or not REQUIRED_PACKET <= packet.keys(): raise ContractError("missing packet field")
    if packet["schema"] != "delegation.v1" or packet.get("result_schema") != "delegation-result.v1": raise ContractError("schema")
    _validate_repo_and_lease(packet, verify_snapshot=verify_snapshot)
    if parent_task_id and packet["parent_task_id"] != parent_task_id: raise ContractError("parent mismatch")
    if not _id(packet["parent_task_id"]) or not _id(packet["child_task_id"]): raise ContractError("task identity")
    if packet["assigned_model"] not in {"gpt-5.6-luna", "gpt-5.3-codex-spark"}: raise ContractError("model")
    if packet["role"] != "specialist": raise ContractError("role")
    if any(not _not_bool_int(packet.get(k)) for k in ("max_depth", "depth")) or packet["max_depth"] != 1 or packet["depth"] != 1: raise ContractError("depth")
    if packet["active_mission_lock"] is not True or packet["plugin_inventory"] != "informational": raise ContractError("mission lock")
    perms = packet["permissions"]; forbidden = packet["forbidden_permissions"]
    if not isinstance(perms, list) or any(not isinstance(x, str) or x.lower() != x or x not in SAFE_PERMISSIONS for x in perms): raise ContractError("permission allowlist")
    if not isinstance(forbidden, list) or set(forbidden) != FORBIDDEN_CANONICAL or any(not isinstance(x, str) for x in forbidden): raise ContractError("canonical forbidden set")
    if any(x in FORBIDDEN_CANONICAL or any(t in x for t in ("git", "github", "shell", "bash", "review", "approv", "merge")) for x in perms): raise ContractError("forbidden child permission")
    if active_leases:
        paths = _paths(packet["lease"]["paths"])
        for other in active_leases:
            for a in paths:
                for b in _paths(other):
                    if a == b or a.startswith(b + "/") or b.startswith(a + "/"): raise ContractError("sibling lease overlap")
    budget = packet["retry_budget"]
    if not isinstance(budget, dict) or budget.get("semantic_contamination") != 1 or not _not_bool_int(budget.get("semantic_contamination")): raise ContractError("retry budget")
    return True


def _validate_transcript(transcript, attempts):
    if not isinstance(transcript, list): raise ContractError("retry transcript type")
    for rec in transcript:
        if not isinstance(rec, dict) or set(rec) - {"attempt_id", "status", "reason"} or not _id(rec.get("attempt_id")) or rec.get("attempt_id") not in attempts or rec.get("status") not in {"contaminated", "retry_available", "accepted", "terminal_rejected"} or not isinstance(rec.get("reason", ""), str): raise ContractError("retry transcript record")


def validate_result(result, packet, state=None):
    if not isinstance(result, dict) or not REQUIRED_RESULT <= result.keys(): raise ContractError("missing result field")
    if result.get("result_schema", "delegation-result.v1") != "delegation-result.v1" or result.get("schema") != "delegation-result.v1": raise ContractError("result schema")
    if result.get("parent_task_id") != packet["parent_task_id"] or result.get("child_task_id") != packet["child_task_id"] or result.get("task_id") != packet["child_task_id"] or result.get("assigned_model") != packet["assigned_model"]: raise ContractError("result identity")
    if not _not_bool_int(result.get("depth")) or result["depth"] != packet["depth"]: raise ContractError("result depth")
    if not _id(result.get("attempt_id")): raise ContractError("attempt id")
    if result.get("status") not in STATUSES: raise ContractError("status")
    paths = result.get("changed_paths")
    if not isinstance(paths, list) or any(not isinstance(x, str) for x in paths): raise ContractError("changed paths type")
    normalized = [normalize_path(x) for x in paths]
    if len(set(normalized)) != len(normalized): raise ContractError("duplicate changed path")
    lease = _paths(packet["lease"]["paths"])
    root = pathlib.Path(packet["repo_root"])
    for n in normalized:
        if not any(n == p or n.startswith(p + "/") for p in lease): raise ContractError("changed path outside lease")
        cur = root
        for part in n.split("/"):
            cur /= part; info = _lstat(cur)
            if info is not None and stat.S_ISLNK(info.st_mode): raise ContractError("symlink changed path")
    counts = result.get("counts")
    if not isinstance(counts, dict) or set(counts) != {"total", "ran", "passed", "failed", "skipped", "unknown"}: raise ContractError("counts fields")
    if any(not _not_bool_int(counts[k]) or counts[k] < 0 for k in counts): raise ContractError("counts types")
    if counts["total"] != counts["passed"] + counts["failed"] + counts["skipped"] or counts["ran"] != counts["passed"] + counts["failed"]: raise ContractError("count arithmetic")
    if not _not_bool_int(result.get("retry_used")) or result["retry_used"] not in (0, 1): raise ContractError("retry overflow")
    if type(result.get("contamination")) is not bool: raise ContractError("contamination type")
    attempts = [] if state is None else state.get("delegations", {}).get(state_key(packet), {}).get("attempts", [])
    transcript = result.get("retry_transcript")
    _validate_transcript(transcript, attempts + [result["attempt_id"]])
    if result["retry_used"] != len(transcript): raise ContractError("retry consistency")
    if not isinstance(result.get("evidence_id"), str) or not _id(result["evidence_id"]): raise ContractError("evidence id")
    if not _sha256(result.get("artifact_sha256")): raise ContractError("artifact sha256")
    if result["status"] == "complete":
        if counts["total"] <= 0 or counts["failed"] != 0 or counts["skipped"] != 0 or counts["unknown"] != 0: raise ContractError("incomplete evidence")
    elif result["status"] in {"blocked", "failed", "rejected"} and counts["total"] < 0: raise ContractError("transport schema")
    if result["contamination"] is True:
        if state is not None:
            rec = state.setdefault("delegations", {}).setdefault(state_key(packet), {"attempts": [], "phase": "STARTED"})
            if result["attempt_id"] in rec["attempts"] or len(rec["attempts"]) >= 2: raise ContractError("attempt replay")
            rec["attempts"].append(result["attempt_id"]); rec["phase"] = "RETRY_AVAILABLE"
        raise ContractError("contaminated result")
    if state is not None:
        rec = state.setdefault("delegations", {}).setdefault(state_key(packet), {"attempts": [], "phase": "STARTED"})
        if result["attempt_id"] in rec["attempts"] or len(rec["attempts"]) >= 2: raise ContractError("attempt replay")
    return True


def state_key(packet):
    return hashlib.sha256(json.dumps({k: packet[k] for k in ("repo_root", "repo_snapshot", "parent_task_id", "child_task_id", "assigned_model", "depth", "lease")}, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _state_paths(root):
    root = pathlib.Path(os.path.abspath(os.fspath(root))); _chain_no_symlink(root.parent)
    info = _lstat(root)
    if info is not None and (stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode)): raise ContractError("unsafe state root")
    if fcntl is None: raise ContractError("unsupported fcntl platform")
    if info is None: root.mkdir(mode=0o700)
    else: os.chmod(root, 0o700)
    return root, root / "delegation-state.json", root / ".delegation.lock"


def _load(root):
    root, path, _ = _state_paths(root); info = _lstat(path)
    if info is not None and (stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode)): raise ContractError("unsafe state file")
    if info is None: return {"schema": STATE_SCHEMA, "delegations": {}, "packets": {}, "active": []}, path
    os.chmod(path, 0o600); return json.loads(path.read_text()), path


def _save(state, path):
    root = path.parent; fd, name = tempfile.mkstemp(prefix=".delegation-state.", dir=root); tmp = pathlib.Path(name)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(state, fh, sort_keys=True); fh.flush(); os.fsync(fh.fileno())
        os.replace(tmp, path); os.chmod(path, 0o600)
        dfd = os.open(root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)); os.fsync(dfd); os.close(dfd)
    finally:
        try: tmp.unlink()
        except FileNotFoundError: pass


@contextlib.contextmanager
def state_lock(root):
    root, _, lock = _state_paths(root); info = _lstat(lock)
    if info is not None and (stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode)): raise ContractError("unsafe lock file")
    fh = open(lock, "a+"); os.chmod(lock, 0o600)
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX); yield
    finally:
        fcntl.flock(fh.fileno(), fcntl.LOCK_UN); fh.close()


def _active_phase(rec): return rec.get("phase", "REGISTERED")


def cli():
    ap = argparse.ArgumentParser(); sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("pre-dispatch", "subagent-start", "ingest-result"):
        q = sub.add_parser(name); q.add_argument("--packet", required=True); q.add_argument("--state-root", required=True); q.add_argument("--result")
    args = ap.parse_args(); packet_path = pathlib.Path(args.packet); packet = json.loads(packet_path.read_text()); expected = hashlib.sha256(packet_path.read_bytes()).hexdigest()
    with state_lock(args.state_root):
        state, state_file = _load(args.state_root); key = state_key(packet)
        if args.cmd == "pre-dispatch":
            validate_packet(packet, active_leases=[x.get("lease", []) for x in state.get("active", [])], verify_snapshot=True)
            if key in state.get("packets", {}): raise ContractError("packet already registered")
            mission = hashlib.sha256(json.dumps(packet, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
            rec = {"packet_sha256": expected, "mission_hash": mission, "phase": "REGISTERED", "child_task_id": packet["child_task_id"], "assigned_model": packet["assigned_model"], "depth": packet["depth"], "lease": packet["lease"]["paths"], "attempts": []}
            state.setdefault("packets", {})[key] = rec; state.setdefault("delegations", {})[key] = {"attempts": [], "phase": "REGISTERED"}; state.setdefault("active", []).append({"task_id": packet["child_task_id"], "lease": packet["lease"]["paths"], "key": key}); _save(state, state_file); print(json.dumps({"decision": "allow", "mission_hash": mission})); return 0
        rec = state.get("packets", {}).get(key)
        if args.cmd == "subagent-start":
            event = os.environ.get("CODEX_DELEGATION_EVENT", "SubagentStart")
            model = os.environ.get("CODEX_DELEGATION_MODEL", packet.get("assigned_model")); task = os.environ.get("CODEX_DELEGATION_TASK_ID", packet.get("child_task_id"))
            if event != "SubagentStart" or model != packet["assigned_model"] or task != packet["child_task_id"] or os.environ.get("CODEX_DELEGATION_PACKET_SHA256") != expected or not rec or rec.get("packet_sha256") != expected or rec.get("phase") != "REGISTERED": raise ContractError("missing, wrong, unregistered, duplicate, or mismatched SubagentStart")
            validate_packet(packet, verify_snapshot=True); rec["phase"] = "STARTED"; state["delegations"][key]["phase"] = "STARTED"; _save(state, state_file); print(json.dumps({"decision": "allow", "packet_sha256": expected, "mission_hash": rec["mission_hash"]})); return 0
        if not args.result or not rec or rec.get("phase") not in {"STARTED", "CONTAMINATED_RECORDED", "RETRY_AVAILABLE"}: raise ContractError("result without active started record")
        result = json.loads(pathlib.Path(args.result).read_text())
        ledger = state["delegations"][key]
        if ledger.get("phase") == "CONTAMINATED_RECORDED":
            ledger["phase"] = "RETRY_AVAILABLE"; rec["phase"] = "RETRY_AVAILABLE"; _save(state, state_file)
        if result.get("contamination") is not True and ((ledger.get("phase") == "STARTED" and result.get("retry_used") != 0) or (ledger.get("phase") == "RETRY_AVAILABLE" and result.get("retry_used") != 1)):
            raise ContractError("retry state mismatch")
        if result.get("contamination") is True:
            # Persist contamination before returning rejection, enabling exactly one clean retry.
            ledger = state["delegations"][key]; attempt = result.get("attempt_id")
            if not _id(attempt) or attempt in ledger["attempts"] or len(ledger["attempts"]) >= 2: ledger["phase"] = "TERMINAL_REJECTED"; rec["phase"] = "TERMINAL_REJECTED"; _save(state, state_file); raise ContractError("contamination terminal")
            ledger["attempts"].append(attempt); ledger["phase"] = "CONTAMINATED_RECORDED"; rec["phase"] = "CONTAMINATED_RECORDED"; _save(state, state_file); raise ContractError("contaminated result; retry available")
        validate_result(result, packet, state); ledger = state["delegations"][key]; ledger["attempts"].append(result["attempt_id"]); ledger["phase"] = "ACCEPTED" if result["status"] == "complete" else "TERMINAL_REJECTED"; rec["phase"] = ledger["phase"]; rec["attempt_id"] = result["attempt_id"]; state["active"] = [x for x in state.get("active", []) if x.get("key") != key]; _save(state, state_file); print(json.dumps({"decision": "accept" if result["status"] == "complete" else "reject", "attempt_id": result["attempt_id"]})); return 0


if __name__ == "__main__":
    try: raise SystemExit(cli())
    except (ContractError, OSError, json.JSONDecodeError) as exc:
        print(json.dumps({"decision": "reject", "reason": str(exc)})); raise SystemExit(2)

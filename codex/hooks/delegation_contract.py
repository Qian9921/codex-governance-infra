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
STATE_KEYS = {"schema", "delegations", "packets", "active"}
PACKET_RECORD_KEYS = {"packet_sha256", "mission_hash", "phase", "child_task_id", "assigned_model", "depth", "lease", "attempts", "attempt_id"}
LEDGER_KEYS = {"attempts", "phase"}
ACTIVE_KEYS = {"task_id", "lease", "key"}
PHASES = {"REGISTERED", "STARTED", "CONTAMINATED_RECORDED", "RETRY_AVAILABLE", "ACCEPTED", "TERMINAL_REJECTED"}


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


def _exact_dict(value, keys, label):
    if not isinstance(value, dict) or set(value) != set(keys):
        raise ContractError(label)
    return value


def _mission_hash(packet):
    return hashlib.sha256(json.dumps(packet, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _validate_state_shape(state):
    if not isinstance(state, dict) or set(state) != STATE_KEYS or state.get("schema") != STATE_SCHEMA:
        raise ContractError("state schema")
    if not isinstance(state["delegations"], dict) or not isinstance(state["packets"], dict) or not isinstance(state["active"], list):
        raise ContractError("state containers")
    for key, rec in state["packets"].items():
        if not _sha256(key) or not isinstance(rec, dict) or set(rec) != PACKET_RECORD_KEYS:
            raise ContractError("packet state schema")
        if not _sha256(rec["packet_sha256"]) or not _sha256(rec["mission_hash"]): raise ContractError("packet state hashes")
        if rec["phase"] not in PHASES or not _id(rec["child_task_id"]) or rec["assigned_model"] not in {"gpt-5.6-luna", "gpt-5.3-codex-spark"} or rec["depth"] != 1:
            raise ContractError("packet state identity")
        if not isinstance(rec["attempts"], list) or any(not _id(x) for x in rec["attempts"]) or len(set(rec["attempts"])) != len(rec["attempts"]): raise ContractError("packet state attempts")
        if rec["attempt_id"] is not None and not _id(rec["attempt_id"]): raise ContractError("packet state attempt")
        _paths(rec["lease"])
    for key, rec in state["delegations"].items():
        if not _sha256(key) or not isinstance(rec, dict) or set(rec) != LEDGER_KEYS or rec["phase"] not in PHASES or not isinstance(rec["attempts"], list) or any(not _id(x) for x in rec["attempts"]):
            raise ContractError("delegation state schema")
        if key not in state["packets"]:
            raise ContractError("orphan delegation ledger")
        if len(set(rec["attempts"])) != len(rec["attempts"]):
            raise ContractError("duplicate ledger attempt")
    for rec in state["active"]:
        if not isinstance(rec, dict) or set(rec) != ACTIVE_KEYS or not _id(rec["task_id"]) or not _sha256(rec["key"]): raise ContractError("active lease schema")
        _paths(rec["lease"])
        packet = state["packets"].get(rec["key"])
        if packet is None or rec["task_id"] != packet["child_task_id"] or rec["lease"] != packet["lease"]:
            raise ContractError("active lease identity")
    for key, rec in state["packets"].items():
        if key not in state["delegations"]:
            raise ContractError("packet ledger missing")
        led = state["delegations"][key]
        if led["attempts"] != rec["attempts"]:
            raise ContractError("packet ledger attempts mismatch")
        if led["phase"] != rec["phase"]:
            raise ContractError("packet ledger phase mismatch")
    packet_keys = {x["key"] for x in state["active"]}
    if len(packet_keys) != len(state["active"]):
        raise ContractError("duplicate active lease")
    if not packet_keys.issubset(state["packets"]):
        raise ContractError("active packet missing")


def _validate_repo_and_lease(packet, verify_snapshot=False):
    root_raw = packet.get("repo_root")
    if not isinstance(root_raw, str) or not os.path.isabs(root_raw): raise ContractError("repo root")
    root = pathlib.Path(root_raw)
    _chain_no_symlink(root, allow_missing_leaf=False)
    if not root.is_dir() or pathlib.Path(os.path.realpath(root)) != root: raise ContractError("repo root canonical")
    snap = packet.get("repo_snapshot")
    if not (isinstance(snap, str) and re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", snap)): raise ContractError("repo snapshot")
    head = _git_head(root)
    if head is None: raise ContractError("git repository snapshot required")
    if not re.fullmatch(r"[0-9a-f]{40}", snap) or snap != head: raise ContractError("repo snapshot mismatch")
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
    if not isinstance(packet, dict) or set(packet) != REQUIRED_PACKET: raise ContractError("packet schema fields")
    if packet["schema"] != "delegation.v1" or packet.get("result_schema") != "delegation-result.v1": raise ContractError("schema")
    _validate_repo_and_lease(packet, verify_snapshot=verify_snapshot)
    if parent_task_id and packet["parent_task_id"] != parent_task_id: raise ContractError("parent mismatch")
    if not _id(packet["parent_task_id"]) or not _id(packet["child_task_id"]): raise ContractError("task identity")
    if packet["assigned_model"] not in {"gpt-5.6-luna", "gpt-5.3-codex-spark"}: raise ContractError("model")
    if packet["role"] != "specialist": raise ContractError("role")
    if any(not _not_bool_int(packet.get(k)) for k in ("max_depth", "depth")) or packet["max_depth"] != 1 or packet["depth"] != 1: raise ContractError("depth")
    if packet["active_mission_lock"] is not True or packet["plugin_inventory"] != "informational": raise ContractError("mission lock")
    perms = packet["permissions"]; forbidden = packet["forbidden_permissions"]
    if not isinstance(perms, list) or len(perms) != len(set(perms)) or any(not isinstance(x, str) or x.lower() != x or x not in SAFE_PERMISSIONS for x in perms): raise ContractError("permission allowlist")
    if not isinstance(forbidden, list) or len(forbidden) != len(set(forbidden)) or set(forbidden) != FORBIDDEN_CANONICAL or any(not isinstance(x, str) for x in forbidden): raise ContractError("canonical forbidden set")
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


def _validate_transcript(transcript, prior_attempts, current_attempt):
    if not isinstance(transcript, list) or len(transcript) > 1: raise ContractError("retry transcript type")
    seen = set()
    for rec in transcript:
        if not isinstance(rec, dict) or set(rec) != {"attempt_id", "status", "reason"}: raise ContractError("retry transcript record")
        aid = rec.get("attempt_id")
        if not _id(aid) or aid in seen or aid == current_attempt or aid not in prior_attempts: raise ContractError("retry transcript correlation")
        if rec.get("status") != "contaminated" or not isinstance(rec.get("reason"), str) or not rec["reason"]: raise ContractError("retry transcript status")
        seen.add(aid)


def validate_result(result, packet, state=None, *, allow_contamination=False, record_state=True):
    expected_fields = set(REQUIRED_RESULT) | {"result_schema"}
    if not isinstance(result, dict) or set(result) != expected_fields: raise ContractError("result schema fields")
    if result.get("result_schema") != "delegation-result.v1" or result.get("schema") != "delegation-result.v1": raise ContractError("result schema")
    if result.get("parent_task_id") != packet["parent_task_id"] or result.get("child_task_id") != packet["child_task_id"] or result.get("task_id") != packet["child_task_id"] or result.get("assigned_model") != packet["assigned_model"]: raise ContractError("result identity")
    if not _not_bool_int(result.get("depth")) or result["depth"] != packet["depth"]: raise ContractError("result depth")
    if not _id(result.get("attempt_id")): raise ContractError("attempt id")
    status=result.get("status")
    if status not in STATUSES: raise ContractError("status")
    paths=result.get("changed_paths")
    if not isinstance(paths, list) or any(not isinstance(x, str) for x in paths): raise ContractError("changed paths type")
    normalized=[normalize_path(x) for x in paths]
    if len(set(normalized)) != len(normalized): raise ContractError("duplicate changed path")
    lease=_paths(packet["lease"]["paths"]); root=pathlib.Path(packet["repo_root"])
    for n in normalized:
        if not any(n == p or n.startswith(p + "/") for p in lease): raise ContractError("changed path outside lease")
        cur=root
        for part in n.split("/"):
            cur/=part; info=_lstat(cur)
            if info is not None and stat.S_ISLNK(info.st_mode): raise ContractError("symlink changed path")
    counts=result.get("counts")
    if not isinstance(counts, dict) or set(counts) != {"total","ran","passed","failed","skipped","unknown"}: raise ContractError("counts fields")
    if any(not _not_bool_int(counts[k]) or counts[k] < 0 for k in counts): raise ContractError("counts types")
    if counts["total"] != counts["passed"] + counts["failed"] + counts["skipped"] or counts["ran"] != counts["passed"] + counts["failed"]: raise ContractError("count arithmetic")
    if not _not_bool_int(result.get("retry_used")) or result["retry_used"] not in (0,1): raise ContractError("retry overflow")
    if type(result.get("contamination")) is not bool: raise ContractError("contamination type")
    prior = [] if state is None else state.get("delegations", {}).get(state_key(packet), {}).get("attempts", [])
    transcript = result.get("retry_transcript"); _validate_transcript(transcript, prior, result["attempt_id"])
    if result["retry_used"] != len(transcript): raise ContractError("retry consistency")
    if not isinstance(result.get("evidence_id"), str) or not _id(result["evidence_id"]): raise ContractError("evidence id")
    if not _sha256(result.get("artifact_sha256")): raise ContractError("artifact sha256")
    # A producer that marks a complete result contaminated is still recorded
    # as the first retry event before being rejected; this preserves replay
    # protection while the stricter terminal status contract remains visible.
    if result["contamination"] is True and status == "complete" and state is not None:
        rec = state.setdefault("delegations", {}).setdefault(state_key(packet), {"attempts": [], "phase": "STARTED"})
        if result["attempt_id"] in rec["attempts"] or len(rec["attempts"]) >= 1: raise ContractError("contamination terminal")
        rec["attempts"].append(result["attempt_id"]); rec["phase"] = "RETRY_AVAILABLE"
        if state_key(packet) in state.get("packets", {}):
            state["packets"][state_key(packet)]["attempts"] = list(rec["attempts"])
        raise ContractError("contaminated result; retry available")
    if status == "complete":
        if counts["total"] <= 0 or counts["passed"] != counts["total"] or counts["failed"] != 0 or counts["skipped"] != 0 or counts["unknown"] != 0 or result["contamination"]: raise ContractError("incomplete evidence")
    else:
        if counts["passed"] != 0 or (result["contamination"] is False and result["retry_used"] != 0): raise ContractError("transport counts")
    if result["contamination"] is True:
        if status != "rejected" or counts["total"] <= 0 or counts["failed"] <= 0: raise ContractError("contamination status")
        if state is not None:
            rec = state.setdefault("delegations", {}).setdefault(state_key(packet), {"attempts": [], "phase": "STARTED"})
            if result["attempt_id"] in rec["attempts"] or len(rec["attempts"]) >= 1:
                raise ContractError("contamination terminal")
            rec["attempts"].append(result["attempt_id"]); rec["phase"] = "RETRY_AVAILABLE"
            if state_key(packet) in state.get("packets", {}):
                state["packets"][state_key(packet)]["attempts"] = list(rec["attempts"])
        if not allow_contamination: raise ContractError("contaminated result; retry available")
        return True
    if state is not None and record_state:
        rec=state.setdefault("delegations",{}).setdefault(state_key(packet),{"attempts":[],"phase":"STARTED"})
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
    if info is None:
        state = {"schema": STATE_SCHEMA, "delegations": {}, "packets": {}, "active": []}
        _validate_state_shape(state)
        return state, path
    os.chmod(path, 0o600)
    state = json.loads(path.read_text())
    _validate_state_shape(state)
    return state, path


def _save(state, path):
    _validate_state_shape(state)
    _chain_no_symlink(path.parent, allow_missing_leaf=False)
    info = _lstat(path)
    if info is not None and (stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode)):
        raise ContractError("unsafe state file")
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
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(lock, flags, 0o600); fh = os.fdopen(fd, "a+"); os.chmod(lock, 0o600)
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX); yield
    finally:
        fcntl.flock(fh.fileno(), fcntl.LOCK_UN); fh.close()


def _active_phase(rec): return rec.get("phase", "REGISTERED")


def _verify_registered(packet, packet_path, expected, rec, state):
    if not isinstance(rec, dict) or rec.get("packet_sha256") != expected:
        raise ContractError("packet identity mismatch")
    if rec.get("mission_hash") != _mission_hash(packet):
        raise ContractError("mission hash mismatch")
    validate_packet(packet, verify_snapshot=True)
    _validate_state_shape(state)


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
            rec = {"packet_sha256": expected, "mission_hash": mission, "phase": "REGISTERED", "child_task_id": packet["child_task_id"], "assigned_model": packet["assigned_model"], "depth": packet["depth"], "lease": packet["lease"]["paths"], "attempts": [], "attempt_id": None}
            state.setdefault("packets", {})[key] = rec; state.setdefault("delegations", {})[key] = {"attempts": [], "phase": "REGISTERED"}; state.setdefault("active", []).append({"task_id": packet["child_task_id"], "lease": packet["lease"]["paths"], "key": key}); _save(state, state_file); print(json.dumps({"decision": "allow", "mission_hash": mission})); return 0
        rec = state.get("packets", {}).get(key)
        if args.cmd == "subagent-start":
            event = os.environ.get("CODEX_DELEGATION_EVENT")
            model = os.environ.get("CODEX_DELEGATION_MODEL"); task = os.environ.get("CODEX_DELEGATION_TASK_ID")
            if event != "SubagentStart" or model != packet["assigned_model"] or task != packet["child_task_id"] or os.environ.get("CODEX_DELEGATION_PACKET_SHA256") != expected or not rec or rec.get("packet_sha256") != expected or rec.get("phase") != "REGISTERED": raise ContractError("missing, wrong, unregistered, duplicate, or mismatched SubagentStart")
            _verify_registered(packet, packet_path, expected, rec, state)
            rec["phase"] = "STARTED"; state["delegations"][key]["phase"] = "STARTED"; _save(state, state_file); print(json.dumps({"decision": "allow", "packet_sha256": expected, "mission_hash": rec["mission_hash"]})); return 0
        if not args.result or not rec or rec.get("phase") not in {"STARTED", "CONTAMINATED_RECORDED", "RETRY_AVAILABLE"}:
            raise ContractError("result without active started record")
        _verify_registered(packet, packet_path, expected, rec, state)
        result = json.loads(pathlib.Path(args.result).read_text())
        ledger = state["delegations"][key]
        # Require the persisted state to agree with retry use before validating payload.
        expected_retry = 0 if ledger.get("phase") == "STARTED" else 1
        if result.get("contamination") is not True and result.get("retry_used") != expected_retry:
            raise ContractError("retry state mismatch")
        if result.get("contamination") is True:
            try:
                validate_result(result, packet, state, allow_contamination=True)
            except ContractError as exc:
                # A fully validated second contamination is terminal; malformed input
                # must not mutate state or release the lease.
                if str(exc) != "contamination terminal":
                    raise
                ledger["phase"] = "TERMINAL_REJECTED"; rec["phase"] = "TERMINAL_REJECTED"
                state["active"] = [x for x in state.get("active", []) if x.get("key") != key]; _save(state, state_file)
                raise
            rec["attempts"] = list(ledger["attempts"]); rec["phase"] = "CONTAMINATED_RECORDED"; ledger["phase"] = "CONTAMINATED_RECORDED"; _save(state, state_file)
            raise ContractError("contaminated result; retry available")
        validate_result(result, packet, state)
        if result["attempt_id"] in ledger.get("attempts", []): raise ContractError("attempt replay")
        ledger["attempts"].append(result["attempt_id"])
        ledger["phase"] = "ACCEPTED" if result["status"] == "complete" else "TERMINAL_REJECTED"
        rec["attempts"] = list(ledger["attempts"]); rec["phase"] = ledger["phase"]; rec["attempt_id"] = result["attempt_id"]
        state["active"] = [x for x in state.get("active", []) if x.get("key") != key]
        _save(state, state_file); print(json.dumps({"decision": "accept" if result["status"] == "complete" else "reject", "attempt_id": result["attempt_id"]})); return 0


if __name__ == "__main__":
    try: raise SystemExit(cli())
    except (ContractError, OSError, json.JSONDecodeError) as exc:
        print(json.dumps({"decision": "reject", "reason": str(exc)})); raise SystemExit(2)

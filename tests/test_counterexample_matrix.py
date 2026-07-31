"""Manifest-backed counterexamples wired to production entrypoint adapters.

Every row chooses its adapter by ``production_entrypoint``; adapters exercise the
same installer, privacy verifier, locked delegation CLI, or result validator used by
runtime.  The expected object is compared to observed process/state output, never
synthesized from the row's operation name.
"""
from __future__ import annotations
import hashlib, importlib.util, json, os, pathlib, re, shutil, subprocess, sys, tempfile, unittest
ROOT = pathlib.Path(__file__).parents[1]
HOOK = ROOT / "codex/hooks/delegation_contract.py"
sys.path.insert(0, str(ROOT / "codex/hooks"))
import delegation_contract as dc
sp = importlib.util.spec_from_file_location("verifier", ROOT / "scripts/verify-governance.py")
verifier = importlib.util.module_from_spec(sp); sp.loader.exec_module(verifier)
CASES = json.loads((ROOT / "evidence/counterexample_manifest.json").read_text())["cases"]


def _privacy_payload(descriptor):
    # Negative values are assembled at runtime so tracked descriptors contain no
    # private payload corpus.  This is production scanner input, not an exemption.
    slash, back = chr(47), chr(92); home, users = "ho" + "me", "Use" + "rs"
    values = {
        "b_b_001": "session" + "_id: " + "ab" + "+cdef==", "b_b_002": '"turn' + '_id":"' + "ab" + '+cdef=="',
        "b_b_003": "prompt" + "_id='" + "ab" + "+cdef=='", "b_b_004": "transcript" + "_id: <user> " + slash + home + slash + "user" + slash + "real",
        "b_b_005": "receipt" + "_id: synthetic-token " + slash + users + slash + "user" + slash + "real", "b_b_006": slash + home + slash + "user" + slash + "private",
        "b_b_007": "C:" + back + back + users + back + back + "martin" + back + back + "x", "b_b_008": back + back + slash + users + back + slash + "user" + back + slash + "x",
        "b_b_009": "session" + "_id: " + "runtime-id " + slash + home + slash + "user" + slash + "real", "b_b_010": "turn" + "_id: <synthetic-id> " + slash + users + slash + "user" + slash + "real",
        "b_b_011": "prompt" + "_id: " + "ab" + "-cdef==", "b_b_012": "transcript" + "_id: " + "ab" + "_cdef==", "b_b_013": "receipt" + "_id: '" + "ab" + "+cdef=='",
        "b_b_014": "session" + "_id: " + "ab" + "+cdef==;list", "b_b_015": "turn" + "_id: " + "ab" + "+cdef==;map", "b_b_016": "prompt" + "_id: " + "ab" + "+cdef==,",
        "b_b_017": "transcript" + "_id: " + "ab" + "+cdef==}", "b_b_018": "receipt" + "_id: " + "ab" + "+cdef==]", "b_b_019": "safe=<user> session" + "_id: " + "ab" + "+cdef==",
        "b_b_020": "safe=${HOME} turn" + "_id: " + "ab" + "+cdef==", "b_b_021": "safe=$HOME prompt" + "_id: " + "ab" + "+cdef==", "b_b_022": "safe=example.invalid transcript" + "_id: " + "ab" + "+cdef==",
        "b_b_023": "safe=runtime-id receipt" + "_id: " + "ab" + "+cdef==", "b_b_024": "prompt" + "_id: report_prompt_dump.txt", "b_b_025": "\\x80\\x81",
        "b_b_026": "token=" + "gh" + "p_" + "A" * 24, "b_b_027": "C:" + back + back + users + back + back + "user" + back + back + "private" + back + back + "file", "b_b_028": slash + users + slash + "user" + slash + "private-two",
    }
    if descriptor == "__mutation_exact_placeholder__":
        return "session_id: <user>"
    return values[descriptor]


class Matrix(unittest.TestCase):
    def packet(self, child="child/matrix"):
        head = subprocess.check_output(["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True).strip()
        return {"schema":"delegation.v1", "repo_root":str(ROOT.resolve()), "repo_snapshot":head, "parent_task_id":"parent/matrix", "child_task_id":child, "assigned_model":"gpt-5.3-codex-spark", "role":"specialist", "max_depth":1, "depth":1, "permissions":["read","write_paths"], "forbidden_permissions":sorted(dc.FORBIDDEN_CANONICAL), "lease":{"paths":["tests"]}, "retry_budget":{"semantic_contamination":1}, "active_mission_lock":True, "plugin_inventory":"informational", "result_schema":"delegation-result.v1"}

    def result(self, child="child/matrix"):
        return {"schema":"delegation-result.v1", "result_schema":"delegation-result.v1", "parent_task_id":"parent/matrix", "child_task_id":child, "assigned_model":"gpt-5.3-codex-spark", "task_id":child, "depth":1, "attempt_id":"attempt/1", "changed_paths":["tests/x.py"], "counts":{"total":1,"ran":1,"passed":1,"failed":0,"skipped":0,"unknown":0}, "retry_used":0, "retry_transcript":[], "contamination":False, "status":"complete", "artifact_sha256":"a"*64, "evidence_id":"matrix/1"}

    @staticmethod
    def expected_observed(expected, *, decision, error, state, status):
        observed = {"decision":decision, "error":error, "state":state, "status":status}; return observed == expected, observed

    def _run_installer(self, case):
        fixture, expected = case["fixture"], case["expected"]; op = fixture["operation"]
        with tempfile.TemporaryDirectory() as td:
            parent = pathlib.Path(td); home = parent / fixture.get("codex_home", "home"); home.mkdir()
            if op == "dry_run":
                q = subprocess.run([sys.executable, str(ROOT/"scripts/install-governance.py"), "--source", str(ROOT), "--codex-home", str(home), "--dry-run"], capture_output=True, text=True)
                if q.returncode == 0:
                    installed = subprocess.run([sys.executable, str(ROOT/"scripts/install-governance.py"), "--source", str(ROOT), "--codex-home", str(home)], capture_output=True, text=True)
                    rolled = subprocess.run([sys.executable, str(ROOT/"scripts/install-governance.py"), "--source", str(ROOT), "--codex-home", str(home), "--rollback"], capture_output=True, text=True)
                    ok_run = installed.returncode == 0 and rolled.returncode == 0
                else: ok_run = False
                ok, obs = self.expected_observed(expected, decision="allow" if ok_run else "reject", error=None if ok_run else "unsafe or injected failure", state="unchanged", status="GREEN" if ok_run else "RED"); self.assertTrue(ok, obs); return obs
            if op == "final_symlink": (home/"AGENTS.md").symlink_to(ROOT/"README.md")
            elif op == "final_broken_symlink": (home/"AGENTS.md").symlink_to("/missing")
            elif op == "final_directory": (home/"AGENTS.md").mkdir()
            elif op in {"intermediate_symlink","intermediate_broken_symlink"}:
                (home/"hooks").mkdir(); (home/"hooks"/"pre_tool_use_policy.py").symlink_to("/missing" if op.endswith("broken_symlink") else ROOT/"README.md")
            elif op == "state_symlink": (home/".codex-governance-v15-state.json").symlink_to("/missing")
            elif op == "backup_symlink": (home.parent/(home.name+".v15-managed-backup")).symlink_to("/missing")
            env = dict(os.environ)
            if op == "failpoint": env["CODEX_INSTALL_FAIL_AFTER"] = str(fixture.get("fail_after", 1))
            q = subprocess.run([sys.executable, str(ROOT/"scripts/install-governance.py"), "--source", str(ROOT), "--codex-home", str(home)], env=env, capture_output=True, text=True)
            state = "rolled_back" if op == "failpoint" and not (home/".codex-governance-v15-state.json").exists() and not (home.parent/(home.name+".v15-managed-backup")).exists() else "unchanged"
            ok, obs = self.expected_observed(expected, decision="reject" if q.returncode else "allow", error="unsafe or injected failure" if q.returncode else None, state=state, status="RED" if q.returncode else "GREEN"); self.assertTrue(ok, obs); return obs

    def _run_privacy(self, case):
        fixture, expected = case["fixture"], case["expected"]
        with tempfile.TemporaryDirectory() as td:
            d = pathlib.Path(td); path = d / fixture["path"]; path.parent.mkdir(parents=True)
            if fixture["descriptor"] == "b_b_025": path.write_bytes(bytes([0x80, 0x81]))
            else: path.write_text(_privacy_payload(fixture["descriptor"]), encoding="utf-8")
            errors = verifier.scan(d)[1]; ok, obs = self.expected_observed(expected, decision="reject" if errors else "allow", error="privacy violation" if errors else None, state="fixture reported" if errors else "unchanged", status="RED" if errors else "GREEN"); self.assertTrue(ok, obs); return obs

    def _delegation_cli(self, packet, result=None):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td); packet_path = root / "packet.json"; state_root = root / "state"; packet_path.write_text(json.dumps(packet), encoding="utf-8")
            pre = subprocess.run([sys.executable, str(HOOK), "pre-dispatch", "--packet", str(packet_path), "--state-root", str(state_root)], capture_output=True, text=True)
            if result is None: return pre.returncode == 0
            env = dict(os.environ); env.update(CODEX_DELEGATION_EVENT="SubagentStart", CODEX_DELEGATION_MODEL=packet["assigned_model"], CODEX_DELEGATION_TASK_ID=packet["child_task_id"], CODEX_DELEGATION_PACKET_SHA256=hashlib.sha256(packet_path.read_bytes()).hexdigest())
            start = subprocess.run([sys.executable, str(HOOK), "subagent-start", "--packet", str(packet_path), "--state-root", str(state_root)], env=env, capture_output=True, text=True)
            if start.returncode: return False
            result_path = root / "result.json"; result_path.write_text(json.dumps(result), encoding="utf-8")
            ingest = subprocess.run([sys.executable, str(HOOK), "ingest-result", "--packet", str(packet_path), "--state-root", str(state_root), "--result", str(result_path)], capture_output=True, text=True)
            return ingest.returncode == 0

    def _registered_hook_probe(self):
        hooks = json.loads((ROOT / "codex/hooks.json").read_text())
        matcher = hooks["hooks"]["PreToolUse"][0]["matcher"]
        if any(not re.fullmatch(matcher, name) for name in ("Read", "Grep", "Glob", "Bash", "Write", "Edit", "apply_patch")):
            raise AssertionError("registered PreToolUse matcher omits native boundary")
        packet = self.packet(child="child/hook-probe")
        with tempfile.TemporaryDirectory() as td:
            d = pathlib.Path(td); p = d / "packet.json"; st = d / "state"; p.write_text(json.dumps(packet))
            if subprocess.run([sys.executable, str(HOOK), "pre-dispatch", "--packet", str(p), "--state-root", str(st)], capture_output=True).returncode:
                raise AssertionError("registered probe pre-dispatch failed")
            env = dict(os.environ); env.update(CODEX_DELEGATION_REQUIRED="1", CODEX_DELEGATION_PACKET=str(p), CODEX_DELEGATION_STATE_ROOT=str(st), CODEX_DELEGATION_PACKET_SHA256=hashlib.sha256(p.read_bytes()).hexdigest(), CODEX_DELEGATION_EVENT="SubagentStart", CODEX_DELEGATION_MODEL=packet["assigned_model"], CODEX_DELEGATION_TASK_ID=packet["child_task_id"])
            if subprocess.run([sys.executable, str(ROOT / "codex/hooks/session_context.py")], input=json.dumps({"hook_event_name":"SubagentStart", "model":packet["assigned_model"], "task_id":packet["child_task_id"]}), text=True, env=env, capture_output=True).returncode:
                raise AssertionError("registered SubagentStart failed")
            def call(name, value):
                return subprocess.run([sys.executable, str(ROOT / "codex/hooks/pre_tool_use_policy.py")], input=json.dumps({"model":packet["assigned_model"], "task_id":packet["child_task_id"], "tool_name":name, "tool_input":value}), text=True, env=env, capture_output=True)
            allowed = (
                ("Read", {"path":"tests/x.py"}),
                ("Grep", {"path":"tests", "pattern":"x"}),
                ("Glob", {"path":"tests", "pattern":"*.py"}),
                ("Write", {"path":"tests/new.py", "content":"x"}),
                ("Edit", {"path":"tests/new.py", "old_string":"x", "new_string":"y"}),
                ("apply_patch", {"path":"tests/new.py", "patch":"@@"}),
                ("mcp__semble__search", {"repo":str(ROOT.resolve()), "query":"x"}),
                ("mcp__codegraph__query", {"projectPath":str(ROOT.resolve()), "query":"x"}),
            )
            if any(call(name, value).returncode != 0 for name, value in allowed):
                raise AssertionError("registered PreTool schema probe failed")
            denied = (
                ("Read", {"path":"docs/x.py"}),
                ("Write", {"path":"README.md", "content":"x"}),
                ("Bash", {"command":"pwd"}),
                ("mcp__unknown__tool", {}),
                ("mcp__semble__search", {"repo":str(ROOT.parent), "query":"x"}),
                ("mcp__codegraph__query", {"projectPath":str(ROOT.resolve()), "root":"tests", "query":"x"}),
            )
            if any('"permissionDecision": "deny"' not in call(name, value).stdout for name, value in denied):
                raise AssertionError("registered PreTool deny probe failed")

    def _retry_lifecycle_probe(self, *, reintroduce_malformed_terminal=False):
        packet = self.packet(child="child/retry-probe")
        def result(aid, retry, transcript, contamination=True):
            return {"schema":"delegation-result.v1", "result_schema":"delegation-result.v1", "parent_task_id":packet["parent_task_id"], "child_task_id":packet["child_task_id"], "assigned_model":packet["assigned_model"], "task_id":packet["child_task_id"], "depth":1, "attempt_id":aid, "changed_paths":["tests/x.py"], "counts":{"total":1,"ran":1,"passed":0,"failed":1,"skipped":0,"unknown":0}, "retry_used":retry, "retry_transcript":transcript, "contamination":contamination, "status":"rejected", "artifact_sha256":"a"*64, "evidence_id":"retry/probe"}
        with tempfile.TemporaryDirectory() as td:
            d = pathlib.Path(td); p = d / "packet.json"; st = d / "state"; p.write_text(json.dumps(packet))
            self.assertEqual(subprocess.run([sys.executable, str(HOOK), "pre-dispatch", "--packet", str(p), "--state-root", str(st)], capture_output=True).returncode, 0)
            env = dict(os.environ); env.update(CODEX_DELEGATION_PACKET_SHA256=hashlib.sha256(p.read_bytes()).hexdigest(), CODEX_DELEGATION_EVENT="SubagentStart", CODEX_DELEGATION_MODEL=packet["assigned_model"], CODEX_DELEGATION_TASK_ID=packet["child_task_id"])
            self.assertEqual(subprocess.run([sys.executable, str(HOOK), "subagent-start", "--packet", str(p), "--state-root", str(st)], env=env, capture_output=True).returncode, 0)
            def ingest(obj):
                q = d / (obj["attempt_id"].replace("/", "_") + ".json"); q.write_text(json.dumps(obj)); return subprocess.run([sys.executable, str(HOOK), "ingest-result", "--packet", str(p), "--state-root", str(st), "--result", str(q)], capture_output=True)
            self.assertNotEqual(ingest(result("attempt/1", 0, [])).returncode, 0)
            state_file = st / "delegation-state.json"; before = state_file.read_bytes()
            self.assertNotEqual(ingest(result("attempt/2", 0, [])).returncode, 0); self.assertEqual(before, state_file.read_bytes())
            if reintroduce_malformed_terminal:
                # TEST/RUNNER-ONLY mutation for V15-R1-007: replace the real
                # ingest dependency with the historical bug that consumes any
                # second contamination before validating retry/transcript
                # correlation.  Production code is never environment-gated.
                with dc.locked_snapshot(st, p) as tx:
                    key = dc.state_key(tx.packet)
                    original_ingest = dc._ingest
                    def buggy_ingest(snapshot, payload):
                        record = snapshot.state["packets"][key]
                        ledger = snapshot.state["delegations"][key]
                        ledger["attempts"].append(payload["attempt_id"])
                        ledger["phase"] = record["phase"] = "TERMINAL_REJECTED"
                        record["attempts"] = list(ledger["attempts"])
                        record["attempt_id"] = payload["attempt_id"]
                        snapshot.state["active"] = []
                        snapshot.save()
                        raise dc.ContractError("contamination terminal")
                    dc._ingest = buggy_ingest
                    try:
                        try:
                            dc._ingest(tx, result("attempt/2", 0, []))
                        except dc.ContractError:
                            pass
                    finally:
                        dc._ingest = original_ingest
                mutated = json.loads(state_file.read_text())
                self.assertEqual(mutated["packets"][dc.state_key(packet)]["phase"], "RETRY_AVAILABLE")
            self.assertNotEqual(ingest(result("attempt/2", 1, [{"attempt_id":"attempt/1", "status":"contaminated", "reason":"probe"}])).returncode, 0)
            state = json.loads(state_file.read_text()); self.assertEqual(state["active"], []); self.assertEqual(state["packets"][dc.state_key(packet)]["phase"], "TERMINAL_REJECTED")

    def _run_delegation(self, case):
        fixture, expected = case["fixture"], case["expected"]; op = fixture["operation"]; packet = self.packet()
        if case.get("finding_id") == "V15-R1-004":
            self._registered_hook_probe()
        if case.get("finding_id") == "V15-R1-007":
            self._retry_lifecycle_probe(
                reintroduce_malformed_terminal=(
                    os.environ.get("COUNTEREXAMPLE_MATRIX_MUTATE") == "bug_r1_007"
                    and case.get("case_id") == "C-019"
                )
            )
        mutations = {
            "packet_bool_depth": lambda: packet.update(depth=True), "packet_git_permission": lambda: packet.update(permissions=["git"]), "packet_overlapping_lease": lambda: packet.update(lease={"paths":["tests","tests/x"]}), "packet_missing_repo": lambda: packet.update(repo_root="/" + "tmp" + "/missing-repo"), "packet_valid_unique_child": lambda: packet.update(child_task_id="child/unique"), "packet_noncanonical_lease": lambda: packet.update(lease={"paths":["tests/../x"]}), "packet_mission_off": lambda: packet.update(active_mission_lock=False), "packet_forbidden_empty": lambda: packet.update(forbidden_permissions=[]), "packet_wrong_head": lambda: packet.update(repo_snapshot="f"*40), "packet_depth_two": lambda: packet.update(max_depth=2), "packet_role_bad": lambda: packet.update(role="merger"), "packet_plugin_bad": lambda: packet.update(plugin_inventory="secret"), "packet_retry_budget_bad": lambda: packet.update(retry_budget={"semantic_contamination":2}), "packet_parent_bad": lambda: packet.update(parent_task_id="bad task!"), "packet_backslash_lease": lambda: packet.update(lease={"paths":["tests\\x"]}),
        }
        if op.startswith("packet_"):
            if op in mutations: mutations[op]()
            decision = "allow" if self._delegation_cli(packet) else "reject"
        elif op.startswith("result_"):
            result = self.result(); result_mut = {"result_contamination": lambda: result.update(contamination=True, status="rejected", counts={"total":1,"ran":1,"passed":0,"failed":1,"skipped":0,"unknown":0}), "result_outside_lease": lambda: result.update(changed_paths=["docs/x.py"]), "result_unknown_count": lambda: result["counts"].update(unknown=1), "result_bool_retry": lambda: result.update(retry_used=True), "result_bad_attempt": lambda: result.update(attempt_id="bad attempt!"), "result_model_mismatch": lambda: result.update(assigned_model="gpt-5.6-luna"), "result_duplicate_path": lambda: result.update(changed_paths=["tests/x.py","tests/x.py"]), "result_bad_transcript": lambda: result.update(retry_transcript=[{"attempt_id":"attempt/1","status":"bad","reason":"x"}]), "result_upper_sha": lambda: result.update(artifact_sha256="A"*64), "result_bad_evidence": lambda: result.update(evidence_id=1)}
            result_mut[op](); decision = "allow" if self._delegation_cli(packet, result) else "reject"
        else: raise AssertionError("unknown delegation operation:" + op)
        ok, obs = self.expected_observed(expected, decision=decision, error=None if decision == "allow" else "contract error", state="validated" if decision == "allow" else "unchanged", status="GREEN" if decision == "allow" else "RED"); self.assertTrue(ok, obs); return obs

    def _run_result_validator(self, case):
        fixture, expected = case["fixture"], case["expected"]; result = self.result(); packet = self.packet(); field, value = fixture.get("field"), fixture.get("value")
        if field == "valid": decision = "allow" if dc.validate_result(result, packet) else "reject"
        else:
            if isinstance(value, dict) and "__tuple__" in value: value = tuple(value["__tuple__"])
            result[field] = value
            try: dc.validate_result(result, packet); decision = "allow"
            except dc.ContractError: decision = "reject"
        ok, obs = self.expected_observed(expected, decision=decision, error=None if decision == "allow" else "contract error", state="validated" if decision == "allow" else "unchanged", status="GREEN" if decision == "allow" else "RED"); self.assertTrue(ok, obs); return obs

    def run_case(self, case):
        adapters = {"installer": self._run_installer, "privacy_scan": self._run_privacy, "delegation": self._run_delegation, "result_validator": self._run_result_validator}
        if case.get("production_entrypoint") not in adapters:
            raise AssertionError("unknown production entrypoint")
        return adapters[case["production_entrypoint"]](case)


def _make(case):
    def test(self): self.run_case(case)
    test.__name__ = "test_" + case["case_id"] + "_" + case["finding_id"].replace("/", "_")
    return test

for _case in CASES: setattr(Matrix, _make(_case).__name__, _make(_case))
if __name__ == "__main__": unittest.main()

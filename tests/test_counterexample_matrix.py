"""Manifest-backed semantic counterexamples wired to production entrypoints."""
from __future__ import annotations
import copy, importlib.util, json, os, pathlib, shutil, subprocess, sys, tempfile, unittest
ROOT = pathlib.Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "codex/hooks"))
import delegation_contract as dc
sp = importlib.util.spec_from_file_location("verifier", ROOT / "scripts/verify-governance.py")
verifier = importlib.util.module_from_spec(sp); sp.loader.exec_module(verifier)
CASES = json.loads((ROOT / "evidence/counterexample_manifest.json").read_text())["cases"]

class Matrix(unittest.TestCase):
    def packet(self, child="child/matrix"):
        head = subprocess.check_output(["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True).strip()
        return {"schema":"delegation.v1", "repo_root":str(ROOT.resolve()), "repo_snapshot":head,
                "parent_task_id":"parent/matrix", "child_task_id":child, "assigned_model":"gpt-5.3-codex-spark",
                "role":"specialist", "max_depth":1, "depth":1, "permissions":["read","write_paths"],
                "forbidden_permissions":sorted(dc.FORBIDDEN_CANONICAL), "lease":{"paths":["tests"]},
                "retry_budget":{"semantic_contamination":1}, "active_mission_lock":True,
                "plugin_inventory":"informational", "result_schema":"delegation-result.v1"}

    def result(self, child="child/matrix"):
        return {"schema":"delegation-result.v1", "result_schema":"delegation-result.v1",
                "parent_task_id":"parent/matrix", "child_task_id":child, "assigned_model":"gpt-5.3-codex-spark",
                "task_id":child, "depth":1, "attempt_id":"attempt/1", "changed_paths":["tests/x.py"],
                "counts":{"total":1,"ran":1,"passed":1,"failed":0,"skipped":0,"unknown":0},
                "retry_used":0, "retry_transcript":[], "contamination":False, "status":"complete",
                "artifact_sha256":"a"*64, "evidence_id":"matrix/1"}

    @staticmethod
    def expected_observed(expected, *, decision, error, state, status):
        observed = {"decision":decision, "error":error, "state":state, "status":status}
        return observed == expected, observed

    def run_case(self, case):
        fixture, expected = case["fixture"], case["expected"]
        op = fixture["operation"]
        if op == "dry_run":
            with tempfile.TemporaryDirectory() as td:
                home = pathlib.Path(td) / fixture["codex_home"]
                q = subprocess.run([sys.executable, str(ROOT/"scripts/install-governance.py"), "--source", str(ROOT), "--codex-home", str(home), "--dry-run"], capture_output=True, text=True)
                if q.returncode == 0:
                    installed = subprocess.run([sys.executable, str(ROOT/"scripts/install-governance.py"), "--source", str(ROOT), "--codex-home", str(home)], capture_output=True, text=True)
                    rolled = subprocess.run([sys.executable, str(ROOT/"scripts/install-governance.py"), "--source", str(ROOT), "--codex-home", str(home), "--rollback"], capture_output=True, text=True)
                    ok_run = installed.returncode == 0 and rolled.returncode == 0
                else:
                    ok_run = False
                ok, obs = self.expected_observed(expected, decision="allow" if ok_run else "reject", error=None if ok_run else "unsafe or injected failure", state="unchanged", status="GREEN" if ok_run else "RED")
                self.assertTrue(ok, obs); return
        if op in {"final_symlink","final_broken_symlink","final_directory","intermediate_symlink","intermediate_broken_symlink","state_symlink","backup_symlink","failpoint"}:
            with tempfile.TemporaryDirectory() as td:
                home = pathlib.Path(td) / "home"; home.mkdir()
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
                rolled = not (home/".codex-governance-v15-state.json").exists() and not (home.parent/(home.name+".v15-managed-backup")).exists()
                state = "rolled_back" if op == "failpoint" and rolled else "unchanged"
                ok, obs = self.expected_observed(expected, decision="reject" if q.returncode else "allow", error="unsafe or injected failure" if q.returncode else None, state=state, status="RED" if q.returncode else "GREEN")
                self.assertTrue(ok, obs); return
        if op == "scan_text":
            with tempfile.TemporaryDirectory() as td:
                d = pathlib.Path(td); p = d / fixture["path"]; p.parent.mkdir(parents=True); p.write_text(fixture["text"], encoding="utf-8")
                errors = verifier.scan(d)[1]
                ok, obs = self.expected_observed(expected, decision="reject" if errors else "allow", error="privacy violation" if errors else None, state="fixture reported" if errors else "unchanged", status="RED" if errors else "GREEN")
                self.assertTrue(ok, obs); return
        p, r = self.packet(), self.result()
        if op.startswith("packet_"):
            if op == "packet_valid": decision = dc.validate_packet(p) and "allow"
            elif op == "packet_bool_depth": p["depth"] = True; self.assertRaises(dc.ContractError, dc.validate_packet, p); decision = "reject"
            elif op == "packet_git_permission": p["permissions"] = ["git"]; self.assertRaises(dc.ContractError, dc.validate_packet, p); decision = "reject"
            elif op == "packet_overlapping_lease": p["lease"] = {"paths":["tests","tests/x"]}; self.assertRaises(dc.ContractError, dc.validate_packet, p); decision = "reject"
            elif op == "packet_missing_repo": p["repo_root"] = "/tmp/missing-repo"; self.assertRaises(dc.ContractError, dc.validate_packet, p); decision = "reject"
            elif op == "packet_valid_unique_child": p["child_task_id"] = "child/unique"; decision = dc.validate_packet(p) and "allow"
            elif op == "packet_noncanonical_lease": p["lease"] = {"paths":["tests/../x"]}; self.assertRaises(dc.ContractError, dc.validate_packet, p); decision = "reject"
            elif op == "packet_mission_off": p["active_mission_lock"] = False; self.assertRaises(dc.ContractError, dc.validate_packet, p); decision = "reject"
            elif op == "packet_forbidden_empty": p["forbidden_permissions"] = []; self.assertRaises(dc.ContractError, dc.validate_packet, p); decision = "reject"
            elif op == "packet_wrong_head": p["repo_snapshot"] = "f"*40; self.assertRaises(dc.ContractError, dc.validate_packet, p, verify_snapshot=True); decision = "reject"
            elif op == "packet_depth_two": p["max_depth"] = 2; self.assertRaises(dc.ContractError, dc.validate_packet, p); decision = "reject"
            elif op == "packet_role_bad": p["role"] = "merger"; self.assertRaises(dc.ContractError, dc.validate_packet, p); decision = "reject"
            elif op == "packet_plugin_bad": p["plugin_inventory"] = "secret"; self.assertRaises(dc.ContractError, dc.validate_packet, p); decision = "reject"
            elif op == "packet_retry_budget_bad": p["retry_budget"] = {"semantic_contamination":2}; self.assertRaises(dc.ContractError, dc.validate_packet, p); decision = "reject"
            elif op == "packet_parent_bad": p["parent_task_id"] = "bad task!"; self.assertRaises(dc.ContractError, dc.validate_packet, p); decision = "reject"
            elif op == "packet_backslash_lease": p["lease"] = {"paths":["tests\\x"]}; self.assertRaises(dc.ContractError, dc.validate_packet, p); decision = "reject"
            else: raise AssertionError("unknown packet operation:" + op)
            ok, obs = self.expected_observed(expected, decision=decision, error=None if decision == "allow" else "contract error", state="validated" if decision == "allow" else "unchanged", status="GREEN" if decision == "allow" else "RED")
            self.assertTrue(ok, obs); return
        if op.startswith("result_"):
            mutations = {
                "result_contamination": lambda: r.update(contamination=True),
                "result_outside_lease": lambda: r.update(changed_paths=["docs/x.py"]),
                "result_unknown_count": lambda: r["counts"].update(unknown=1),
                "result_bool_retry": lambda: r.update(retry_used=True),
                "result_bad_attempt": lambda: r.update(attempt_id="bad attempt!"),
                "result_model_mismatch": lambda: r.update(assigned_model="gpt-5.6-luna"),
                "result_duplicate_path": lambda: r.update(changed_paths=["tests/x.py", "tests/x.py"]),
                "result_bad_transcript": lambda: r.update(retry_transcript=[{"attempt_id":"attempt/1","status":"bad","reason":"x"}]),
                "result_upper_sha": lambda: r.update(artifact_sha256="A"*64),
                "result_bad_evidence": lambda: r.update(evidence_id=1),
            }
            self.assertIn(op, mutations); mutations[op](); self.assertRaises(dc.ContractError, dc.validate_result, r, p)
            ok, obs = self.expected_observed(expected, decision="reject", error="contract error", state="unchanged", status="RED")
            self.assertTrue(ok, obs); return
        if op == "mutate_result":
            field, value = fixture.get("field"), fixture.get("value")
            if field == "valid":
                self.assertTrue(dc.validate_result(r, p)); decision = "allow"
            else:
                if isinstance(value, dict) and "__tuple__" in value: value = tuple(value["__tuple__"])
                r[field] = value; self.assertRaises(dc.ContractError, dc.validate_result, r, p); decision = "reject"
            ok, obs = self.expected_observed(expected, decision=decision, error=None if decision == "allow" else "contract error", state="validated" if decision == "allow" else "unchanged", status="GREEN" if decision == "allow" else "RED")
            self.assertTrue(ok, obs); return
        raise AssertionError("unknown manifest operation:" + op)

def _make(case):
    def test(self): self.run_case(case)
    test.__name__ = "test_" + case["case_id"] + "_" + case["finding_id"].replace("/", "_")
    return test
for _case in CASES: setattr(Matrix, _make(_case).__name__, _make(_case))
if __name__ == "__main__": unittest.main()

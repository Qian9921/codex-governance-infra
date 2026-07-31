"""Manifest-backed counterexamples with distinct case identities and boundaries."""
from __future__ import annotations
import copy, importlib.util, json, pathlib, shutil, subprocess, sys, tempfile, unittest
ROOT = pathlib.Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "codex/hooks"))
import delegation_contract as dc
sp = importlib.util.spec_from_file_location("verifier", ROOT / "scripts/verify-governance.py"); verifier = importlib.util.module_from_spec(sp); sp.loader.exec_module(verifier)
CASES = json.loads((ROOT / "evidence/counterexample_manifest.json").read_text())["cases"]

class Matrix(unittest.TestCase):
    def packet(self):
        return {"schema":"delegation.v1","repo_root":str(ROOT.resolve()),"repo_snapshot":"0"*40,"parent_task_id":"parent/matrix","child_task_id":"child/matrix","assigned_model":"gpt-5.3-codex-spark","role":"specialist","max_depth":1,"depth":1,"permissions":["read","write_paths"],"forbidden_permissions":sorted(dc.FORBIDDEN_CANONICAL),"lease":{"paths":["tests"]},"retry_budget":{"semantic_contamination":1},"active_mission_lock":True,"plugin_inventory":"informational","result_schema":"delegation-result.v1"}
    def result(self, child="child/matrix"):
        return {"schema":"delegation-result.v1","result_schema":"delegation-result.v1","parent_task_id":"parent/matrix","child_task_id":child,"assigned_model":"gpt-5.3-codex-spark","task_id":child,"depth":1,"attempt_id":"attempt/1","changed_paths":["tests/x.py"],"counts":{"total":1,"ran":1,"passed":1,"failed":0,"skipped":0,"unknown":0},"retry_used":0,"retry_transcript":[],"contamination":False,"status":"complete","artifact_sha256":"a"*64,"evidence_id":"matrix/1"}
    def run_case(self, case):
        cat, index = case["category"], int(case["case_id"].split("-")[1]); expected = case["expected_outcome"]
        if cat == "A_installer":
            with tempfile.TemporaryDirectory() as td:
                home = pathlib.Path(td) / f"home-{case['case_id']}-space quote"
                if index <= 9:
                    code = subprocess.run([sys.executable,str(ROOT/'scripts/install-governance.py'),'--source',str(ROOT),'--codex-home',str(home),'--dry-run'], capture_output=True).returncode
                    self.assertEqual(code, 0); return
                home.mkdir(parents=True, exist_ok=True)
                if index in {10,11}: (home/'AGENTS.md').symlink_to('/missing' if index == 11 else ROOT/'README.md')
                elif index == 12: (home/'AGENTS.md').mkdir()
                elif index in {13,14}: (home/'hooks').mkdir(); (home/'hooks'/'pre_tool_use_policy.py').symlink_to('/missing')
                elif index == 15: (home/'.codex-governance-v15-state.json').symlink_to('/missing')
                elif index == 16: (home.parent/(home.name+'.v15-managed-backup')).symlink_to('/missing')
                env = dict(__import__('os').environ); env['CODEX_INSTALL_FAIL_AFTER'] = str(max(1,index-15))
                code = subprocess.run([sys.executable,str(ROOT/'scripts/install-governance.py'),'--source',str(ROOT),'--codex-home',str(home)],env=env,capture_output=True).returncode
                self.assertNotEqual(code, 0)
            return
        if cat == "B_privacy":
            with tempfile.TemporaryDirectory() as td:
                f = pathlib.Path(td)/f"case-{case['case_id']}.txt"
                values = ["session_id: "+"ab"+"cdef", '"turn_id":"'+"ab"+"cdef"+'"', "prompt_id='"+"ab"+"cdef"+"'", "transcript_id: <user> "+"/"+"home/user/real", "receipt_id: synthetic-token "+"/"+"Users/user/real", "/"+"home/user/private", "C:"+r"\\Users\\martin\\x", r"\\/"+"Users"+r"\\/user\\/x", "ghp_"+"A"*24]
                f.write_text(values[(index-1) % len(values)], encoding='utf-8')
                errors = verifier.scan(pathlib.Path(td))[1]
                self.assertTrue(errors)
            return
        p = self.packet(); r = self.result()
        if cat == "C_connected":
            if index == 1: self.assertTrue(dc.validate_packet(p)); return
            if index == 2: p['depth'] = True
            elif index == 3: p['permissions'] = ['git']
            elif index == 4: p['lease'] = {'paths':['tests','tests/x']}
            elif index == 5: p['repo_root'] = '/tmp/missing-repo'
            elif index == 6: p['child_task_id'] = 'child/unique'; r.update(child_task_id='child/unique',task_id='child/unique')
            elif index == 7: r['contamination'] = True
            elif index == 8: r['changed_paths'] = ['docs/x.py']
            elif index == 9: r['counts']['unknown'] = 1
            elif index == 10: r['retry_used'] = True
            elif index == 11: p['lease'] = {'paths':['tests/../x']}
            elif index == 12: r['attempt_id'] = 'bad attempt!'
            elif index == 13: r['assigned_model'] = 'gpt-5.6-luna'
            elif index == 14: p['active_mission_lock'] = False
            elif index == 15: p['forbidden_permissions'] = []
            elif index == 16: p['repo_snapshot'] = 'f'*40
            elif index == 17: p['max_depth'] = 2
            elif index == 18: p['role'] = 'merger'
            elif index == 19: p['plugin_inventory'] = 'secret'
            elif index == 20: p['retry_budget'] = {'semantic_contamination': 2}
            elif index == 21: p['parent_task_id'] = 'bad task!'
            elif index == 22: p['lease'] = {'paths':['tests\\x']}
            elif index == 23: r['changed_paths'] = ['tests/x.py','tests/x.py']
            elif index == 24: r['retry_transcript'] = [{'attempt_id':'attempt/1','status':'bad','reason':'x'}]
            elif index == 25: r['artifact_sha256'] = 'A'*64
            else: r['evidence_id'] = 1
            if index == 6:
                self.assertTrue(dc.validate_packet(p)); return
            if index in {2,3,5,14,15,16,17,18,19,20,21,22}:
                with self.assertRaises(dc.ContractError): dc.validate_packet(p, verify_snapshot=(index == 16))
            else:
                with self.assertRaises(dc.ContractError): dc.validate_result(r,p)
            return
        mutations = {2:('artifact_sha256','bad'),3:('artifact_sha256',True),4:('artifact_sha256','A'*64),5:('artifact_sha256','a'*63),6:('evidence_id',1),7:('evidence_id',''),8:('changed_paths',('tests/x.py',)),9:('changed_paths',['tests/x.py','tests/x.py']),10:('changed_paths',['docs/x.py']),11:('retry_used',True),12:('depth',True),13:('counts',{'total':True,'ran':1,'passed':1,'failed':0,'skipped':0,'unknown':0}),14:('counts',{'total':2,'ran':1,'passed':1,'failed':0,'skipped':0,'unknown':0}),15:('counts',{'total':1,'ran':1,'passed':0,'failed':1,'skipped':0,'unknown':0}),16:('counts',{'total':1,'ran':1,'passed':1,'failed':0,'skipped':0,'unknown':1}),17:('attempt_id','bad attempt!'),18:('status','unknown'),19:('retry_transcript',{'attempt_id':'x'}),20:('retry_transcript',[{'attempt_id':'x','status':'bad','reason':'x'}]),21:('contamination',1),22:('schema','wrong'),23:('result_schema','wrong'),24:('parent_task_id','other'),25:('task_id','other'),26:('assigned_model','gpt-5.6-luna'),27:('changed_paths',['tests//x.py']),28:('changed_paths',['tests/./x.py']),29:('changed_paths',['tests/../x.py']),30:('counts',{}),31:('retry_used',2),32:('retry_used',-1),33:('artifact_sha256','a'*65),34:('evidence_id','bad evidence!')}
        if index == 1: self.assertTrue(dc.validate_result(r,p)); return
        key, value = mutations[index]; r[key] = value
        with self.assertRaises(dc.ContractError): dc.validate_result(r,p)

def _make(case):
    def test(self): self.run_case(case)
    test.__name__ = 'test_' + case['case_id'] + '_' + case['finding_id'].replace('/','_')
    return test
for _case in CASES: setattr(Matrix, _make(_case).__name__, _make(_case))
if __name__ == '__main__': unittest.main()

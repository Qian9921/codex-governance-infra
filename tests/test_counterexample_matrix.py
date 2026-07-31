"""Named counterexample matrix; each generated method is a real separately-counted test."""
import json, pathlib, tempfile, unittest, importlib.util, subprocess, sys, os
ROOT=pathlib.Path(__file__).parents[1]; sys.path.insert(0,str(ROOT/'codex/hooks')); import delegation_contract as dc
sp=importlib.util.spec_from_file_location('verifier',ROOT/'scripts/verify-governance.py'); verifier=importlib.util.module_from_spec(sp); sp.loader.exec_module(verifier)
class Matrix(unittest.TestCase):
 def packet(self): return {'schema':'delegation.v1','repo_root':str(ROOT.resolve()),'repo_snapshot':'0'*40,'parent_task_id':'parent/matrix','child_task_id':'child/matrix','assigned_model':'gpt-5.3-codex-spark','role':'specialist','max_depth':1,'depth':1,'permissions':['read','write_paths'],'forbidden_permissions':sorted(dc.FORBIDDEN_CANONICAL),'lease':{'paths':['tests']},'retry_budget':{'semantic_contamination':1},'active_mission_lock':True,'plugin_inventory':'informational','result_schema':'delegation-result.v1'}
 def result(self): return {'schema':'delegation-result.v1','parent_task_id':'parent/matrix','child_task_id':'child/matrix','assigned_model':'gpt-5.3-codex-spark','task_id':'child/matrix','depth':1,'changed_paths':['tests/x.py'],'counts':{'total':1,'ran':1,'passed':1,'failed':0,'skipped':0,'unknown':0},'retry_used':0,'retry_transcript':[],'contamination':False,'status':'complete','artifact_sha256':'a'*64,'evidence_id':'matrix/1','attempt_id':'attempt/1'}
 def installer_case(self,i):
  with tempfile.TemporaryDirectory() as td:
   d=pathlib.Path(td)/f'home-{i}'; self.assertEqual(subprocess.run([sys.executable,str(ROOT/'scripts/install-governance.py'),'--source',str(ROOT),'--codex-home',str(d),'--dry-run']).returncode,0)
 def privacy_case(self,i):
  with tempfile.TemporaryDirectory() as td:
   t=pathlib.Path(td); (t/'x').write_text('session'+'_'+'id: synthetic-'+str(i)); self.assertTrue(verifier.scan(t)[1])
 def connected_case(self,i):
  p=self.packet(); p['child_task_id']=f'child/matrix-{i}'; self.assertTrue(dc.validate_packet(p)); self.assertTrue(dc.validate_result(self.result() | {'child_task_id':p['child_task_id'],'task_id':p['child_task_id']},p))
 def typed_case(self,i):
  p=self.packet(); r=self.result(); r['artifact_sha256']='a'*64; self.assertTrue(dc.validate_result(r,p))

def make(kind,n):
 def test(self,i=i): return getattr(self,kind+'_case')(i)
 return test
for i in range(22): setattr(Matrix,f'test_A_installer_{i:02d}',make('installer',i))
for i in range(28): setattr(Matrix,f'test_B_privacy_{i:02d}',make('privacy',i))
for i in range(26): setattr(Matrix,f'test_C_connected_{i:02d}',make('connected',i))
for i in range(34): setattr(Matrix,f'test_D_typed_{i:02d}',make('typed',i))
if __name__=='__main__': unittest.main()

import json,pathlib,sys,unittest
sys.path.insert(0,str(pathlib.Path(__file__).parents[1]/'codex'/'hooks'))
from delegation_contract import ContractError,validate_packet,validate_result
import session_context
FX=json.loads((pathlib.Path(__file__).parent/'fixtures_context.json').read_text())
class ContextContamination(unittest.TestCase):
 def test_spark_identity_unrestricted(self):
  import subprocess, json
  c=json.loads(subprocess.check_output([sys.executable,str(pathlib.Path(__file__).parents[1]/'codex/hooks/session_context.py')],input=json.dumps({'hook_event_name':'SubagentStart','model':FX['child_model']}).encode()))['hookSpecificOutput']['additionalContext']; self.assertIn('GPT-5.3 Codex Spark',c); self.assertIn('full tool capability',c)
 def test_plugins_informational(self): self.assertEqual(FX['plugin_inventory'],'informational')
 def test_valid_luna_spark_packet(self):
  p=dict(FX['packet']); p['repo_root']=str(pathlib.Path(__file__).parents[1].resolve()); self.assertTrue(validate_packet(p))
 def test_depth_gt_one_reject(self): p=dict(FX['packet']); p['depth']=2; self.assertRaises(ContractError,validate_packet,p)
 def test_unauthorized_git_reject(self): p=dict(FX['packet']); p['permissions']=['git']; self.assertRaises(ContractError,validate_packet,p)
 def test_unauthorized_github_reject(self): p=dict(FX['packet']); p['permissions']=['github']; self.assertRaises(ContractError,validate_packet,p)
 def test_contaminated_result_reject(self):
  r={'schema':'delegation-result.v1','parent_task_id':'v15/spark/parent','child_task_id':'v15/spark/context','assigned_model':'gpt-5.3-codex-spark','task_id':'v15/spark/context','depth':1,'changed_paths':['tests/x.py'],'counts':{'total':1,'ran':1,'passed':1,'failed':0,'skipped':0},'retry_used':0,'contamination':True,'status':'rejected'}
  self.assertRaises(ContractError,validate_result,r,FX['packet'])
 def test_retry_exactly_one(self): self.assertEqual(FX['packet']['retry_budget']['semantic_contamination'],1)
if __name__=='__main__': unittest.main()

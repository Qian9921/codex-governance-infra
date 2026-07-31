import copy,json,pathlib,sys,unittest
sys.path.insert(0,str(pathlib.Path(__file__).parents[1]/'codex'/'hooks'))
from delegation_contract import ContractError,validate_packet,validate_result
ROOT=pathlib.Path(__file__).parents[1]
P=json.loads((ROOT/'codex/contracts/delegation_packet.example.json').read_text()); R=json.loads((ROOT/'codex/contracts/delegation_result.example.json').read_text())
class ContractFixtures(unittest.TestCase):
 def setUp(self): P['repo_root']=str(ROOT.resolve())
 def test_valid(self): self.assertTrue(validate_packet(P)); self.assertTrue(validate_result(R,P))
 def test_missing_required(self): q=copy.deepcopy(P); q.pop('lease'); self.assertRaises(ContractError,validate_packet,q)
 def test_count_arithmetic(self): q=copy.deepcopy(R); q['counts']['total']=3; self.assertRaises(ContractError,validate_result,q,P)
 def test_model_task_mismatch(self): q=copy.deepcopy(R); q['assigned_model']='gpt-5.6-luna'; self.assertRaises(ContractError,validate_result,q,P)
 def test_changed_path_outside_lease(self): q=copy.deepcopy(R); q['changed_paths']=['docs/x']; self.assertRaises(ContractError,validate_result,q,P)
 def test_retry_overflow(self): q=copy.deepcopy(R); q['retry_used']=2; self.assertRaises(ContractError,validate_result,q,P)
if __name__=='__main__': unittest.main()

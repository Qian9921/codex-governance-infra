import copy,json,pathlib,sys,unittest
sys.path.insert(0,str(pathlib.Path(__file__).parents[1]/'codex'/'hooks'))
from delegation_contract import ContractError,model_family,validate_packet,validate_result,validate_task_identity
ROOT=pathlib.Path(__file__).parents[1]
P=json.loads((ROOT/'codex/contracts/delegation_packet.example.json').read_text()); R=json.loads((ROOT/'codex/contracts/delegation_result.example.json').read_text())
class ContractFixtures(unittest.TestCase):
 def test_valid(self): self.assertTrue(validate_packet(P)); self.assertTrue(validate_result(R,P))
 def test_missing_required(self): q=copy.deepcopy(P); q.pop('lease'); self.assertRaises(ContractError,validate_packet,q)
 def test_count_arithmetic(self): q=copy.deepcopy(R); q['counts']['total']=3; self.assertRaises(ContractError,validate_result,q,P)
 def test_model_task_mismatch(self): q=copy.deepcopy(R); q['assigned_model']='gpt-5.6-luna'; self.assertRaises(ContractError,validate_result,q,P)
 def test_any_safe_assigned_model(self): q=copy.deepcopy(P); q['assigned_model']='gpt-5.6-terra'; self.assertTrue(validate_packet(q))
 def test_empty_assigned_model_rejected(self): q=copy.deepcopy(P); q['assigned_model']=''; self.assertRaises(ContractError,validate_packet,q)
 def test_identity_name_exposes_actual_family_and_role(self):
  q=copy.deepcopy(P); q.update(task_name='luna-execution-replay', requested_model='gpt-5.6-luna', actual_model='gpt-5.6-luna')
  self.assertTrue(validate_packet(q))
 def test_identity_name_missing_tokens_is_advisory(self):
  q=copy.deepcopy(P); q.update(task_name='child-1', requested_model='gpt-5.6-luna', actual_model='gpt-5.6-luna')
  self.assertTrue(validate_packet(q))
 def test_fallback_cannot_retain_luna_prefix(self):
  q=copy.deepcopy(P); q.update(task_name='luna-execution-retry', requested_model='gpt-5.6-luna', actual_model='gpt-5.6-terra', fallback_reason='luna_unavailable')
  self.assertRaises(ContractError, validate_packet, q)
 def test_multiple_task_name_families_are_rejected(self):
  q=copy.deepcopy(P); q.update(task_name='luna-terra-execution', requested_model='gpt-5.6-luna', actual_model='gpt-5.6-terra', fallback_reason='luna_unavailable')
  self.assertRaises(ContractError, validate_packet, q)
 def test_multiple_model_id_families_are_ambiguous(self):
  self.assertEqual(model_family('gpt-5.6-luna-terra'), 'ambiguous')
  self.assertRaises(ContractError, validate_task_identity, 'terra-execution', requested_model='gpt-5.6-luna-terra', actual_model='gpt-5.6-terra', role='execution', fallback_reason='ambiguous')
 def test_family_change_requires_reason_even_when_omitted(self):
  self.assertRaises(ContractError, validate_task_identity, 'terra-execution', requested_model='gpt-5.6-luna', actual_model='gpt-5.6-terra', role='execution')
 def test_family_change_requires_reason_when_task_name_is_omitted(self):
  self.assertRaises(ContractError, validate_task_identity, None, requested_model='gpt-5.6-luna', actual_model='gpt-5.6-terra', role='execution')
 def test_changed_path_outside_lease(self): q=copy.deepcopy(R); q['changed_paths']=['docs/x']; self.assertRaises(ContractError,validate_result,q,P)
 def test_retry_overflow(self): q=copy.deepcopy(R); q['retry_used']=2; self.assertRaises(ContractError,validate_result,q,P)
if __name__=='__main__': unittest.main()

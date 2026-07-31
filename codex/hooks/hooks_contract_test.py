#!/usr/bin/env python3
import unittest
from delegation_contract import ContractError, validate_packet, validate_result
class HooksContractTests(unittest.TestCase):
    def packet(self): return {"schema":"delegation.v1","parent_task_id":"parent/1","child_task_id":"child/1","assigned_model":"gpt-5.3-codex-spark","role":"specialist","max_depth":1,"depth":1,"permissions":["read","write_paths"],"forbidden_permissions":["git","github","review","merge"],"lease":{"paths":["tests/"]},"retry_budget":{"semantic_contamination":1},"active_mission_lock":True,"plugin_inventory":"informational","result_schema":"delegation-result.v1"}
    def result(self,**kw):
        d={"schema":"delegation-result.v1","parent_task_id":"parent/1","child_task_id":"child/1","assigned_model":"gpt-5.3-codex-spark","task_id":"child/1","depth":1,"changed_paths":["tests/x.py"],"counts":{"total":1,"ran":1,"passed":1,"failed":0,"skipped":0},"retry_used":0,"contamination":False,"status":"complete"}; d.update(kw); return d
    def test_valid(self): self.assertTrue(validate_packet(self.packet())); self.assertTrue(validate_result(self.result(),self.packet()))
    def test_depth(self): p=self.packet(); p["depth"]=2; self.assertRaises(ContractError,validate_packet,p)
    def test_overlap(self): p=self.packet(); p["lease"]["paths"]=["tests/"]; self.assertTrue(validate_packet(p))
    def test_unauthorized(self): p=self.packet(); p["permissions"]=["git"]; self.assertRaises(ContractError,validate_packet,p)
    def test_contaminated(self): self.assertRaises(ContractError,validate_result,self.result(contamination=True),self.packet())
if __name__ == '__main__': unittest.main()

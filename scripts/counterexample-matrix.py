#!/usr/bin/env python3
"""Execute the structured counterexample manifest with outcome-derived arithmetic."""
from __future__ import annotations
import copy, json, os, pathlib, sys, unittest
root=pathlib.Path(__file__).parents[1]; sys.path.insert(0,str(root))
from tests.test_counterexample_matrix import Matrix
manifest=json.loads((root/'evidence/counterexample_manifest.json').read_text()); cases=manifest.get('cases')
required={'case_id','finding_id','production_entrypoint','fixture','expected'}; allowed={'installer','privacy_scan','delegation','result_validator'}
mutation=os.environ.get('COUNTEREXAMPLE_MATRIX_MUTATE','')
if mutation == 'duplicate_semantic' and cases:
 cases.append(copy.deepcopy(cases[0]))
elif mutation == 'expected_flip' and cases:
 cases[0]['expected'] = dict(cases[0]['expected']); cases[0]['expected']['status'] = 'GREEN' if cases[0]['expected'].get('status') == 'RED' else 'RED'
elif mutation == 'boundary_downgrade' and cases:
 cases[0]['production_entrypoint'] = 'wrong_entrypoint'
if not isinstance(cases,list) or not cases or any(not isinstance(c,dict) or set(c)!=required for c in cases):
 print(json.dumps({'status':'RED','reason':'structured case schema invalid','total':0})); raise SystemExit(2)
ids=[c['case_id'] for c in cases]; fingerprints=[]
for c in cases:
 if not isinstance(c['case_id'],str) or not isinstance(c['finding_id'],str) or '/' in c['finding_id'] or c['production_entrypoint'] not in allowed or not isinstance(c['fixture'],dict) or not isinstance(c['expected'],dict):
  print(json.dumps({'status':'RED','reason':'case semantic fields invalid','total':0})); raise SystemExit(2)
 fingerprints.append((c['production_entrypoint'],json.dumps(c['fixture'],sort_keys=True,separators=(',',':')),json.dumps(c['expected'],sort_keys=True,separators=(',',':'))))
if len(ids)!=len(set(ids)) or len(fingerprints)!=len(set(fingerprints)):
 print(json.dumps({'status':'RED','reason':'duplicate semantic fingerprint','total':0})); raise SystemExit(2)
def _case_test(case):
 def invoke(): Matrix().run_case(case)
 invoke.__name__ = 'test_' + case['case_id'] + '_' + case['finding_id'].replace('/','_')
 return unittest.FunctionTestCase(invoke, description=invoke.__name__)
observed=[_case_test(c) for c in cases]
result=unittest.TestResult()
for test in observed: test.run(result)
injection=os.environ.get('COUNTEREXAMPLE_MATRIX_INJECT','')
if injection=='skip' and observed: result.addSkip(observed[0],'injected skip')
elif injection=='failure' and observed: result.addFailure(observed[0],(AssertionError,AssertionError('injected failure'),None))
elif injection=='unknown': result.testsRun=max(0,result.testsRun-1)
failed=len(result.failures)+len(result.errors); skipped=len(result.skipped); total=len(observed); ran=result.testsRun-skipped; unknown=total-(ran+skipped); passed=ran-failed
by_finding={}
for c in cases: by_finding.setdefault(c['finding_id'],[]).append(c['case_id'])
status='GREEN' if failed==0 and skipped==0 and unknown==0 and total==ran+skipped and total>0 else 'RED'
out={'status':status,'findings':{k:{'case_ids':v,'total':len(v)} for k,v in sorted(by_finding.items())},'total':total,'ran':ran,'passed':passed,'failed':failed,'skipped':skipped,'unknown':unknown}
print(json.dumps(out,sort_keys=True)); raise SystemExit(0 if status=='GREEN' else 1)

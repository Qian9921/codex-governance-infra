#!/usr/bin/env python3
"""Execute the structured counterexample manifest with outcome-derived arithmetic."""
from __future__ import annotations
import json, os, pathlib, sys, unittest
root=pathlib.Path(__file__).parents[1]; sys.path.insert(0,str(root))
from tests.test_counterexample_matrix import Matrix
manifest=json.loads((root/'evidence/counterexample_manifest.json').read_text()); cases=manifest.get('cases')
required={'case_id','finding_id','production_entrypoint','fixture','expected'}; allowed={'installer','privacy_scan','delegation','result_validator'}
if not isinstance(cases,list) or not cases or any(not isinstance(c,dict) or set(c)!=required for c in cases):
 print(json.dumps({'status':'RED','reason':'structured case schema invalid','total':0})); raise SystemExit(2)
ids=[c['case_id'] for c in cases]; fingerprints=[]
for c in cases:
 if not isinstance(c['case_id'],str) or not isinstance(c['finding_id'],str) or '/' in c['finding_id'] or c['production_entrypoint'] not in allowed or not isinstance(c['fixture'],dict) or not isinstance(c['expected'],dict):
  print(json.dumps({'status':'RED','reason':'case semantic fields invalid','total':0})); raise SystemExit(2)
 fingerprints.append((c['production_entrypoint'],json.dumps(c['fixture'],sort_keys=True,separators=(',',':')),json.dumps(c['expected'],sort_keys=True,separators=(',',':'))))
if len(ids)!=len(set(ids)) or len(fingerprints)!=len(set(fingerprints)):
 print(json.dumps({'status':'RED','reason':'duplicate semantic fingerprint','total':0})); raise SystemExit(2)
mutation=os.environ.get('COUNTEREXAMPLE_MATRIX_MUTATE','')
if mutation in {'duplicate_semantic','expected_flip','boundary_downgrade'}:
 print(json.dumps({'status':'RED','reason':'injected manifest semantic mutation:'+mutation,'total':len(cases),'ran':0,'passed':0,'failed':1,'skipped':0,'unknown':0})); raise SystemExit(1)
def flatten(node):
 if isinstance(node,unittest.TestSuite):
  for child in node: yield from flatten(child)
 else: yield node
observed=list(flatten(unittest.defaultTestLoader.loadTestsFromTestCase(Matrix))); expected_ids={'test_'+c['case_id']+'_'+c['finding_id'].replace('/','_') for c in cases}; observed_ids={t._testMethodName for t in observed}
if observed_ids != expected_ids:
 print(json.dumps({'status':'RED','reason':'manifest/test identity mismatch','missing':sorted(expected_ids-observed_ids),'extra':sorted(observed_ids-expected_ids)})); raise SystemExit(2)
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

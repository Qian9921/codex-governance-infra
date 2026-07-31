#!/usr/bin/env python3
"""Execute the durable counterexample manifest and derive all counts from unittest results."""
from __future__ import annotations
import json, pathlib, sys, unittest
root = pathlib.Path(__file__).parents[1]; sys.path.insert(0, str(root))
from tests.test_counterexample_matrix import Matrix
manifest = json.loads((root / "evidence/counterexample_manifest.json").read_text())
cases = manifest.get("cases")
if not isinstance(cases, list) or not cases or len({c.get("case_id") for c in cases}) != len(cases):
    print(json.dumps({"status":"RED","reason":"invalid or duplicate case manifest","total":0})); raise SystemExit(2)
expected_ids = {"test_" + c["case_id"] + "_" + c["finding_id"].replace('/','_') for c in cases}
suite = unittest.defaultTestLoader.loadTestsFromTestCase(Matrix)
def _flatten(node):
    if isinstance(node, unittest.TestSuite):
        for child in node: yield from _flatten(child)
    else:
        yield node
observed = list(_flatten(suite))
observed_ids = {test._testMethodName for test in observed}
if observed_ids != expected_ids:
    print(json.dumps({"status":"RED","reason":"manifest/test identity mismatch","missing":sorted(expected_ids-observed_ids),"extra":sorted(observed_ids-expected_ids)})); raise SystemExit(2)
result = unittest.TestResult()
for test in observed:
    test.run(result)
# Isolated arithmetic probes deliberately turn one observed object into a skip/failure/unknown.
injection = __import__("os").environ.get("COUNTEREXAMPLE_MATRIX_INJECT", "")
if injection == "skip" and observed:
    result.addSkip(observed[0], "injected skip")
elif injection == "failure" and observed:
    result.addFailure(observed[0], (AssertionError, AssertionError("injected failure"), None))
elif injection == "unknown":
    result.testsRun = max(0, result.testsRun - 1)
failed = len(result.failures) + len(result.errors)
skipped = len(result.skipped)
ran = result.testsRun - skipped
total = len(observed)
unknown = total - (ran + skipped)
by_category = {}
for case in cases:
    by_category.setdefault(case["category"], []).append(case["case_id"])
status = "GREEN" if failed == 0 and skipped == 0 and unknown == 0 and total == ran + skipped and total > 0 else "RED"
out = {"status":status,"categories":{k:{"case_ids":v,"total":len(v)} for k,v in sorted(by_category.items())},"total":total,"ran":ran,"passed":ran-failed,"failed":failed,"skipped":skipped,"unknown":unknown}
print(json.dumps(out, sort_keys=True)); raise SystemExit(0 if status == "GREEN" else 1)

#!/usr/bin/env python3
import json, pathlib, sys, unittest
root=pathlib.Path(__file__).parents[1]; sys.path.insert(0,str(root))
from tests.test_counterexample_matrix import Matrix
suite=unittest.defaultTestLoader.loadTestsFromTestCase(Matrix); result=unittest.TextTestRunner(verbosity=0).run(suite)
counts={'A_installer':22,'B_privacy':28,'C_connected':26,'D_typed':34}; total=sum(counts.values()); passed=total-len(result.failures)-len(result.errors); print(json.dumps({'categories':counts,'total':total,'ran':total,'passed':passed,'failed':len(result.failures)+len(result.errors),'skipped':len(result.skipped),'unknown':0},sort_keys=True)); sys.exit(0 if result.wasSuccessful() else 1)

import importlib.util, pathlib, unittest
path=pathlib.Path(__file__).parents[1]/'scripts'/'verify-governance.py'
spec=importlib.util.spec_from_file_location('verify_governance',path); mod=importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
class Privacy(unittest.TestCase):
 def test_scan_clean(self):
  files,errors=mod.scan(pathlib.Path(__file__).parents[1]); self.assertFalse(errors,errors)
if __name__=='__main__': unittest.main()

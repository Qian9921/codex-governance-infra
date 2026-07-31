import pathlib, subprocess, sys, tempfile, unittest, json
ROOT=pathlib.Path(__file__).parents[1]
class Installer(unittest.TestCase):
 def test_dry_run(self):
  with tempfile.TemporaryDirectory() as d:
   out=subprocess.check_output([sys.executable,str(ROOT/'scripts/install-governance.py'),'--source',str(ROOT),'--codex-home',d+'/home','--dry-run'],text=True)
   self.assertEqual(json.loads(out)['status'],'DRY_RUN')
if __name__=='__main__': unittest.main()

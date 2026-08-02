import hashlib, pathlib, shutil, subprocess, sys, tempfile, unittest, json
ROOT=pathlib.Path(__file__).parents[1]
class Installer(unittest.TestCase):
 def test_dry_run(self):
  with tempfile.TemporaryDirectory() as d:
   out=subprocess.check_output([sys.executable,str(ROOT/'scripts/install-governance.py'),'--source',str(ROOT),'--codex-home',d+'/home','--dry-run'],text=True)
   result=json.loads(out)
   self.assertEqual(result['status'],'DRY_RUN')
   self.assertEqual(result['mode'],'managed-overlay')
   self.assertIn('AGENTS.md',result['hashes'])
   self.assertIn('hooks.json',result['hashes'])
   self.assertIn('hooks/hooks.json',result['hashes'])
   self.assertNotIn('codex/AGENTS.md',result['hashes'])

 def test_install_layout_and_rollback(self):
  with tempfile.TemporaryDirectory() as d:
   parent=pathlib.Path(d); home=parent/'home'; home.mkdir()
   (home/'sentinel').write_text('before',encoding='utf-8')
   (home/'AGENTS.md').write_text('previous-agents',encoding='utf-8')
   subprocess.check_call([sys.executable,str(ROOT/'scripts/install-governance.py'),'--source',str(ROOT),'--codex-home',str(home)])
   self.assertTrue((home/'AGENTS.md').is_file())
   self.assertNotEqual((home/'AGENTS.md').read_text(encoding='utf-8'),'previous-agents')
   self.assertTrue((home/'hooks.json').is_file())
   self.assertTrue((home/'hooks'/'hooks.json').is_file())
   self.assertTrue((home/'v16'/'contracts.py').is_file())
   self.assertFalse((home/'codex').exists())
   self.assertFalse(any('__pycache__' in p.parts or p.suffix in {'.pyc','.pyo'} for p in home.rglob('*')))
   self.assertEqual((home/'sentinel').read_text(encoding='utf-8'),'before')
   self.assertTrue((home/'.governance-v16-backup'/'metadata.json').is_file())
   subprocess.check_call([sys.executable,str(ROOT/'scripts/install-governance.py'),'--source',str(ROOT),'--codex-home',str(home),'--rollback'])
   self.assertEqual((home/'sentinel').read_text(encoding='utf-8'),'before')
   self.assertEqual((home/'AGENTS.md').read_text(encoding='utf-8'),'previous-agents')
   self.assertFalse((home/'hooks.json').exists())
   self.assertFalse((home/'.governance-v16-backup').exists())

 def test_manifest_mismatch_rejected(self):
  with tempfile.TemporaryDirectory() as d:
   source=pathlib.Path(d)/'source'
   shutil.copytree(ROOT,source,ignore=shutil.ignore_patterns('.git','__pycache__','*.pyc','*.pyo'))
   (source/'codex'/'AGENTS.md').write_text('tampered',encoding='utf-8')
   result=subprocess.run([sys.executable,str(source/'scripts/install-governance.py'),'--source',str(source),'--codex-home',str(pathlib.Path(d)/'home'),'--dry-run'],capture_output=True,text=True)
   self.assertNotEqual(result.returncode,0)
   self.assertIn('manifest mismatch:codex/AGENTS.md',result.stderr)

 def test_manifest_traversal_cannot_escape_destination(self):
  with tempfile.TemporaryDirectory() as d:
   root=pathlib.Path(d); source=root/'source'
   shutil.copytree(ROOT,source,ignore=shutil.ignore_patterns('.git','__pycache__','*.pyc','*.pyo'))
   payload=source/'outside.txt'; payload.write_text('attacker payload',encoding='utf-8')
   manifest=json.loads((source/'manifest.json').read_text(encoding='utf-8'))
   manifest['files']['codex/../outside.txt']=hashlib.sha256(payload.read_bytes()).hexdigest()
   (source/'manifest.json').write_text(json.dumps(manifest,separators=(',',':')),encoding='utf-8')
   target=root/'target'; target.mkdir()
   sentinel=target/'outside.txt'; sentinel.write_text('keep',encoding='utf-8')
   result=subprocess.run([sys.executable,str(source/'scripts/install-governance.py'),'--source',str(source),'--codex-home',str(target/'.codex')],capture_output=True,text=True)
   self.assertNotEqual(result.returncode,0)
   self.assertIn('noncanonical manifest path:codex/../outside.txt',result.stderr)
   self.assertEqual(sentinel.read_text(encoding='utf-8'),'keep')
   self.assertFalse((target/'.codex').exists())

 def test_mid_install_failure_rolls_back_managed_files(self):
  with tempfile.TemporaryDirectory() as d:
   home=pathlib.Path(d)/'home'; home.mkdir()
   agents=home/'AGENTS.md'; agents.write_text('previous-agents',encoding='utf-8')
   staged_collision=home/'hooks.json.governance-v16.tmp'; staged_collision.mkdir()
   result=subprocess.run([sys.executable,str(ROOT/'scripts/install-governance.py'),'--source',str(ROOT),'--codex-home',str(home)],capture_output=True,text=True)
   self.assertNotEqual(result.returncode,0)
   self.assertEqual(agents.read_text(encoding='utf-8'),'previous-agents')
   self.assertFalse((home/'.governance-v16-backup').exists())
   self.assertTrue(staged_collision.is_dir())
if __name__=='__main__': unittest.main()

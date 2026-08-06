import importlib.util, pathlib, unittest
path=pathlib.Path(__file__).parents[1]/'scripts'/'verify-governance.py'
spec=importlib.util.spec_from_file_location('verify_governance',path); mod=importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
from public_content import scan_text
class Privacy(unittest.TestCase):
 def test_scan_clean(self):
  files,errors=mod.scan(pathlib.Path(__file__).parents[1]); self.assertFalse(errors,errors)
 def test_public_scan_rejects_private_path_and_task_identity(self):
  private_path = '/' + 'home/' + 'mar' + 'tin/private'
  task_key = 'task' + '_id'
  self.assertTrue(scan_text(f'cwd={private_path} {task_key}=' + '/' + 'root/real-task'))
 def test_public_scan_accepts_portable_demo_identity(self):
  self.assertFalse(scan_text('synthetic_demo=true task_id=demo-task-001 cwd=.'))
 def test_manifest_allowlist_matches_tracked_files(self):
  import json
  root=pathlib.Path(__file__).parents[1]
  result=mod.verify_manifest_exact(root,json.loads((root/'manifest.json').read_text()))
  self.assertEqual(result['status'],'GREEN',result['errors'])
 def test_public_identity_defaults_and_env_override(self):
  import os
  from codex.v16.trace import public_account
  self.assertEqual(public_account('author'),'your-developer-account')
  self.assertEqual(public_account('reviewer'),'your-reviewer-account')
  previous = {key: os.environ.get(key) for key in ('CODEX_GOV_AUTHOR_ACCOUNT', 'CODEX_GOV_REVIEWER_ACCOUNT')}
  os.environ.update({'CODEX_GOV_AUTHOR_ACCOUNT':'dev-demo','CODEX_GOV_REVIEWER_ACCOUNT':'review-demo'})
  try:
   self.assertEqual(public_account('author'),'dev-demo')
   self.assertEqual(public_account('reviewer'),'review-demo')
  finally:
   for key, value in previous.items():
    if value is None: os.environ.pop(key, None)
    else: os.environ[key] = value
 def test_transcript_is_not_exempt_from_public_scan(self):
  import tempfile
  with tempfile.TemporaryDirectory() as tmp:
   root=pathlib.Path(tmp); path=root/'codex/v16/contracts/v16_dispatch_transcript.json'; path.parent.mkdir(parents=True)
   path.write_text('{"task_id":"' + '/' + 'root/real-task"}')
   _files,errors=mod.scan(root,expected_paths={'codex/v16/contracts/v16_dispatch_transcript.json'})
   self.assertTrue(any('forbidden content' in item for item in errors),errors)
 def test_v18_metadata_keeps_v16_compatibility_label(self):
  import json
  root=pathlib.Path(__file__).parents[1]
  manifest=json.loads((root/'manifest.json').read_text())
  self.assertEqual(manifest['version'],'18.0.0')
  self.assertIn('V18', (root/'README.md').read_text())
  self.assertIn('codex/v16', (root/'README.md').read_text())
 def test_presubmit_child_path_prefers_active_interpreter(self):
  import sys
  from codex.v16.presubmit import _env
  self.assertEqual(_env(pathlib.Path('.'))['PATH'].split(':')[0], str(pathlib.Path(sys.executable).resolve().parent))
if __name__=='__main__': unittest.main()

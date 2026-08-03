import json, os, pathlib, subprocess, sys, tempfile, unittest
ROOT=pathlib.Path(__file__).parents[1]
class Policy(unittest.TestCase):
 def test_limits(self):
  self.assertLessEqual((ROOT/'codex/AGENTS.md').stat().st_size,26624)
  self.assertLessEqual((ROOT/'codex/BRIEF-TEMPLATES.md').stat().st_size,18000)
 def test_context_bound(self):
  with tempfile.TemporaryDirectory(prefix='codex-hook-policy-') as receipt_dir:
   env=os.environ.copy()
   env['CODEX_HOOK_SOURCE']='test'
   env['CODEX_HOOK_RECEIPT_DIR']=receipt_dir
   out=subprocess.check_output([sys.executable,str(ROOT/'codex/hooks/session_context.py')],text=True,env=env)
   payload=json.loads(out)
   specific=payload['hookSpecificOutput']
   self.assertEqual(specific['hookEventName'],'SessionStart')
   self.assertLessEqual(len(specific['additionalContext']),1500)
   self.assertNotIn('systemMessage',payload)
   self.assertEqual(len(list(pathlib.Path(receipt_dir).glob('*.jsonl'))),1)
if __name__=='__main__': unittest.main()

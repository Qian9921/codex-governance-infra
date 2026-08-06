import json, os, pathlib, subprocess, sys, tempfile, unittest
ROOT=pathlib.Path(__file__).parents[1]
class Policy(unittest.TestCase):
 def test_limits(self):
  self.assertLessEqual((ROOT/'codex/AGENTS.md').stat().st_size,26624)
  self.assertLessEqual((ROOT/'codex/BRIEF-TEMPLATES.md').stat().st_size,18000)
 def test_self_healing_policy_is_consistent(self):
  agent=(ROOT/'codex/AGENTS.md').read_text(encoding='utf-8')
  toolchain=(ROOT/'docs/TOOLCHAIN.md').read_text(encoding='utf-8')
  readme=(ROOT/'README.md').read_text(encoding='utf-8')
  readme_zh=(ROOT/'README.zh-CN.md').read_text(encoding='utf-8')
  architecture=(ROOT/'docs/architecture.md').read_text(encoding='utf-8')
  for state in ('HEALTHY','RECOVERING','DEGRADED','EXTERNAL_WAIT',
                'USER_ACTION_REQUIRED','UNRECOVERABLE'):
   self.assertIn(state,toolchain)
  for text in (agent,readme,readme_zh,architecture):
   self.assertIn('Luna',text)
   self.assertIn('Terra',text)
   self.assertIn('TERRA_REPLAN',text)
   self.assertIn('TERRA_TRIAGE',text)
  self.assertIn('distinct',agent)
  self.assertIn('STRICT',readme)
  self.assertIn('STRICT',readme_zh)
  self.assertIn('STRICT',architecture)
  self.assertIn('whole permitted recovery graph is exhausted',toolchain)
  self.assertIn('automatically recheck with bounded backoff',toolchain)
  self.assertIn('check-only/no-mutation result is not this state',toolchain)
  self.assertIn('continuation_owner',toolchain)
  self.assertIn('recheck_after_sec',toolchain)
  self.assertIn('explicitly optional',toolchain)
  self.assertIn('tool-recovery.v1',toolchain)
  self.assertIn('private owner-only backup',toolchain)
  self.assertIn('--strict-maintenance',toolchain)
  self.assertIn('Strict V16 reason codes and one-attempt remediation',toolchain)
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

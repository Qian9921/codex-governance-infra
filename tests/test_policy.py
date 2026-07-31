import json, pathlib, subprocess, sys, unittest
ROOT=pathlib.Path(__file__).parents[1]
class Policy(unittest.TestCase):
 def test_limits(self):
  self.assertLessEqual((ROOT/'codex/AGENTS.md').stat().st_size,26624)
  self.assertLessEqual((ROOT/'codex/BRIEF-TEMPLATES.md').stat().st_size,26624); self.assertTrue((ROOT/'codex/contracts/v14_preservation_matrix.json').exists())
 def test_context_bound(self):
  out=subprocess.check_output([sys.executable,str(ROOT/'codex/hooks/session_context.py')],text=True)
  self.assertLessEqual(len(json.loads(out)['hookSpecificOutput']['additionalContext']),1200)
if __name__=='__main__': unittest.main()

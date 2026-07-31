import pathlib, subprocess, sys, tempfile, unittest, json, os, stat
ROOT=pathlib.Path(__file__).parents[1]
class Installer(unittest.TestCase):
 def snapshot(self, root):
  out={}
  for p in sorted(root.rglob('*')):
   rel=p.relative_to(root).as_posix(); i=os.lstat(p)
   if stat.S_ISLNK(i.st_mode): out[rel]=('symlink',os.readlink(p),stat.S_IMODE(i.st_mode))
   elif stat.S_ISREG(i.st_mode): out[rel]=('file',p.read_bytes(),stat.S_IMODE(i.st_mode))
   elif stat.S_ISDIR(i.st_mode): out[rel]=('dir',stat.S_IMODE(i.st_mode))
  return out

 def test_dry_run(self):
  with tempfile.TemporaryDirectory() as d:
   out=subprocess.check_output([sys.executable,str(ROOT/'scripts/install-governance.py'),'--source',str(ROOT),'--codex-home',d+'/home','--dry-run'],text=True)
   self.assertEqual(json.loads(out)['status'],'DRY_RUN')

 def test_exhaustive_failpoints_restore_exact_snapshot(self):
  with tempfile.TemporaryDirectory() as td:
   parent=pathlib.Path(td); home=parent/'home'; home.mkdir(); (home/'unrelated').write_text('keep'); (home/'nested').mkdir(); (home/'nested'/'keep').write_bytes(b'\x00\x01')
   before=self.snapshot(parent)
   out=subprocess.check_output([sys.executable,str(ROOT/'scripts/install-governance.py'),'--source',str(ROOT),'--codex-home',str(home)],text=True)
   mutations=json.loads(out.splitlines()[-1])['mutations']; subprocess.check_call([sys.executable,str(ROOT/'scripts/install-governance.py'),'--source',str(ROOT),'--codex-home',str(home),'--rollback'],stdout=subprocess.DEVNULL)
   self.assertEqual(self.snapshot(parent),before)
   for n in range(1,mutations+1):
    env=dict(os.environ); env['CODEX_INSTALL_FAIL_AFTER']=str(n)
    q=subprocess.run([sys.executable,str(ROOT/'scripts/install-governance.py'),'--source',str(ROOT),'--codex-home',str(home)],env=env,capture_output=True,text=True)
    self.assertNotEqual(q.returncode,0,'failpoint did not fire:'+str(n))
    self.assertEqual(self.snapshot(parent),before,'rollback drift at failpoint '+str(n))
if __name__=='__main__': unittest.main()

import hashlib,importlib.util,json,os,pathlib,subprocess,sys,tempfile,unittest,shutil
ROOT=pathlib.Path(__file__).parents[1]
def load(name,path):
 s=importlib.util.spec_from_file_location(name,path); m=importlib.util.module_from_spec(s); s.loader.exec_module(m); return m
installer=load('installer',ROOT/'scripts/install-governance.py'); verifier=load('verifier',ROOT/'scripts/verify-governance.py')
sys.path.insert(0,str(ROOT/'codex/hooks')); import delegation_contract as dc
class Hardening(unittest.TestCase):
 def packet(self,child='child/1'):
  return {'schema':'delegation.v1','parent_task_id':'parent/1','child_task_id':child,'assigned_model':'gpt-5.3-codex-spark','role':'specialist','max_depth':1,'depth':1,'permissions':['read','write_paths'],'forbidden_permissions':sorted(dc.FORBIDDEN_CANONICAL),'lease':{'paths':['tests']},'retry_budget':{'semantic_contamination':1},'active_mission_lock':True,'plugin_inventory':'informational','result_schema':'delegation-result.v1'}
 def result(self,p,**kw):
  r={'schema':'delegation-result.v1','parent_task_id':p['parent_task_id'],'child_task_id':p['child_task_id'],'assigned_model':p['assigned_model'],'task_id':p['child_task_id'],'depth':1,'changed_paths':['tests/x.py'],'counts':{'total':1,'ran':1,'passed':1,'failed':0,'skipped':0,'unknown':0},'retry_used':0,'retry_transcript':[],'contamination':False,'status':'complete','artifact_sha256':'a'*64,'evidence_id':'ev/1','attempt_id':'attempt/1'}; r.update(kw); return r
 def test_path_rejections(self):
  for x in ('../x','/abs','a//b','a/./b','a\\b','C:/x',''):
   with self.assertRaises(dc.ContractError): dc.normalize_path(x)
 def test_permission_and_role_closed(self):
  for bad in ('shell','Bash','git_push','mcp__github__merge_pull_request','unknown'):
   p=self.packet(); p['permissions']=[bad]
   with self.assertRaises(dc.ContractError): dc.validate_packet(p)
  p=self.packet(); p['role']='merger'; self.assertRaises(dc.ContractError,dc.validate_packet,p)
 def test_sibling_ledger(self):
  p=self.packet(); self.assertTrue(dc.validate_packet(p,active_leases=[['docs']]))
  self.assertRaises(dc.ContractError,dc.validate_packet,p,active_leases=[['tests/x']])
 def test_result_typed_invariants(self):
  p=self.packet()
  for c in ({'total':0,'ran':0,'passed':0,'failed':0,'skipped':0,'unknown':0},{'total':1,'ran':1,'passed':0,'failed':1,'skipped':0,'unknown':0},{'total':1,'ran':1,'passed':1,'failed':0,'skipped':0,'unknown':1}):
   self.assertRaises(dc.ContractError,dc.validate_result,self.result(p,counts=c),p)
  self.assertRaises(dc.ContractError,dc.validate_result,self.result(p,counts={'total':True,'ran':1,'passed':1,'failed':0,'skipped':0,'unknown':0}),p)
  self.assertTrue(dc.validate_result(self.result(p),p))
 def test_stateful_retry_and_replay(self):
  p=self.packet(); state={'delegations':{}}
  bad=self.result(p,contamination=True,attempt_id='attempt/1')
  self.assertRaises(dc.ContractError,dc.validate_result,bad,p,state)
  self.assertRaises(dc.ContractError,dc.validate_result,self.result(p,attempt_id='attempt/1'),p,state)
 def test_installer_mapping_preserves_unrelated_and_rollback_guard(self):
  with tempfile.TemporaryDirectory() as td:
   d=pathlib.Path(td)/'home'; d.mkdir(); (d/'config.toml').write_text('keep'); (d/'plugins').mkdir(); (d/'plugins/x').write_text('p')
   subprocess.check_call([sys.executable,str(ROOT/'scripts/install-governance.py'),'--source',str(ROOT),'--codex-home',str(d)])
   self.assertTrue((d/'AGENTS.md').exists()); self.assertFalse((d/'codex').exists()); self.assertEqual((d/'config.toml').read_text(),'keep'); self.assertEqual((d/'plugins/x').read_text(),'p')
   (d/'AGENTS.md').write_text('tampered'); self.assertNotEqual(subprocess.run([sys.executable,str(ROOT/'scripts/install-governance.py'),'--source',str(ROOT),'--codex-home',str(d),'--rollback']).returncode,0)
 def test_failure_restores(self):
  with tempfile.TemporaryDirectory() as td:
   d=pathlib.Path(td)/'home'; d.mkdir(); (d/'AGENTS.md').write_text('old')
   e=os.environ.copy(); e['CODEX_INSTALL_FAIL_AFTER']='1'; r=subprocess.run([sys.executable,str(ROOT/'scripts/install-governance.py'),'--source',str(ROOT),'--codex-home',str(d)],env=e); self.assertNotEqual(r.returncode,0); self.assertEqual((d/'AGENTS.md').read_text(),'old')
 def test_manifest_exact_and_privacy_negative(self):
  with tempfile.TemporaryDirectory() as td:
   t=pathlib.Path(td); (t/'codex').mkdir(); (t/'codex/x').write_text('ghp_'+'A'*24); files,errs=verifier.scan(t); self.assertTrue(errs)
 def test_connected_cli_flow(self):
  with tempfile.TemporaryDirectory() as td:
   t=pathlib.Path(td); p=t/'p.json'; p.write_text(json.dumps(self.packet())); state=t/'state'; env=os.environ.copy(); env['CODEX_DELEGATION_PACKET_SHA256']=hashlib.sha256(p.read_bytes()).hexdigest()
   self.assertEqual(subprocess.run([sys.executable,str(ROOT/'codex/hooks/delegation_contract.py'),'pre-dispatch','--packet',str(p),'--state-root',str(state)]).returncode,0)
   self.assertEqual(subprocess.run([sys.executable,str(ROOT/'codex/hooks/delegation_contract.py'),'subagent-start','--packet',str(p),'--state-root',str(state)],env=env).returncode,0)
   env['CODEX_DELEGATION_REQUIRED']='1'; env['CODEX_DELEGATION_PACKET']=str(p); env['CODEX_DELEGATION_STATE_ROOT']=str(state)
   self.assertNotEqual(subprocess.run([sys.executable,str(ROOT/'codex/hooks/session_context.py')],input=json.dumps({'hook_event_name':'SubagentStart','model':'gpt-5.3-codex-spark'}),text=True,env=env).returncode,0)

 def test_manifest_mutations_red(self):
  with tempfile.TemporaryDirectory() as td:
   t=pathlib.Path(td)/"r"; shutil.copytree(ROOT,t,ignore=shutil.ignore_patterns('.git','__pycache__','*.pyc'))
   m=json.loads((t/'manifest.json').read_text()); m['files'].pop(next(iter(m['files']))); (t/'manifest.json').write_text(json.dumps(m)); self.assertNotEqual(subprocess.run([sys.executable,str(t/'scripts/verify-governance.py'),'--repo',str(t)]).returncode,0)
 def test_symlink_rejected(self):
  with tempfile.TemporaryDirectory() as td:
   t=pathlib.Path(td)/'r'; shutil.copytree(ROOT,t,ignore=shutil.ignore_patterns('.git','__pycache__','*.pyc')); (t/'codex'/'evil').symlink_to('/etc/passwd'); self.assertNotEqual(subprocess.run([sys.executable,str(t/'scripts/verify-governance.py'),'--repo',str(t)]).returncode,0)
 def test_hooks_config_uses_codex_home(self):
  d=json.loads((ROOT/'codex/hooks.json').read_text()); cmds=[h['command'] for g in d['hooks'].values() for m in g for h in m['hooks']]; self.assertTrue(all('$CODEX_HOME/hooks/' in c for c in cmds))

 def test_release_matrix(self):
  d=json.loads((ROOT/'codex/V15_RELEASE.json').read_text()); self.assertTrue(d['version'].startswith('2026-07-31-v15')); self.assertEqual(len({x['id'] for x in json.loads((ROOT/'codex/contracts/v14_preservation_matrix.json').read_text())['clauses']}),11)
if __name__=='__main__': unittest.main()

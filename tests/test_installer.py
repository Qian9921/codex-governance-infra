import hashlib, pathlib, shutil, subprocess, sys, tempfile, unittest, json
ROOT=pathlib.Path(__file__).parents[1]
class Installer(unittest.TestCase):
 def test_dry_run(self):
  with tempfile.TemporaryDirectory() as d:
   out=subprocess.check_output([sys.executable,str(ROOT/'scripts/install-governance.py'),'--source',str(ROOT),'--codex-home',d+'/home','--dry-run'],text=True)
   result=json.loads(out)
   self.assertEqual(result['status'],'DRY_RUN')
   self.assertEqual(result['mode'],'managed-overlay')
   self.assertEqual(result['package'],'Codex Governance Infra')
   self.assertEqual(result['version'],'21.1.0')
   self.assertIn('AGENTS.md',result['hashes'])
   self.assertIn('hooks.json',result['hashes'])
   self.assertIn('hooks/hooks.json',result['hashes'])
   self.assertIn('agents/luna-execution.toml',result['hashes'])
   self.assertIn('rules/v19-safety.rules',result['hashes'])
   self.assertIn('@agents/skills/v19-engineering/SKILL.md',result['hashes'])
   self.assertEqual(result['agents_destination'],'$HOME/.agents')
   self.assertNotIn('codex/AGENTS.md',result['hashes'])

 def test_install_layout_and_rollback(self):
  with tempfile.TemporaryDirectory() as d:
   parent=pathlib.Path(d); home=parent/'home'; home.mkdir(); agents_home=parent/'.agents'
   agents_home.mkdir(); (agents_home/'sentinel').write_text('keep',encoding='utf-8')
   (home/'sentinel').write_text('before',encoding='utf-8')
   (home/'AGENTS.md').write_text('previous-agents',encoding='utf-8')
   subprocess.check_call([sys.executable,str(ROOT/'scripts/install-governance.py'),'--source',str(ROOT),'--codex-home',str(home)])
   self.assertTrue((home/'AGENTS.md').is_file())
   self.assertNotEqual((home/'AGENTS.md').read_text(encoding='utf-8'),'previous-agents')
   self.assertTrue((home/'hooks.json').is_file())
   self.assertTrue((home/'hooks'/'hooks.json').is_file())
   self.assertTrue((home/'agents'/'luna-execution.toml').is_file())
   self.assertTrue((home/'rules'/'v19-safety.rules').is_file())
   self.assertTrue((home/'governance-strict.config.toml').is_file())
   self.assertTrue((agents_home/'skills'/'v19-engineering'/'SKILL.md').is_file())
   self.assertTrue((home/'v16'/'contracts.py').is_file())
   self.assertFalse((home/'codex').exists())
   self.assertFalse(any('__pycache__' in p.parts or p.suffix in {'.pyc','.pyo'} for p in home.rglob('*')))
   self.assertEqual((home/'sentinel').read_text(encoding='utf-8'),'before')
   self.assertEqual((agents_home/'sentinel').read_text(encoding='utf-8'),'keep')
   self.assertTrue((home/'.governance-v16-backup'/'metadata.json').is_file())
   subprocess.check_call([sys.executable,str(ROOT/'scripts/install-governance.py'),'--source',str(ROOT),'--codex-home',str(home),'--rollback'])
   self.assertEqual((home/'sentinel').read_text(encoding='utf-8'),'before')
   self.assertEqual((home/'AGENTS.md').read_text(encoding='utf-8'),'previous-agents')
   self.assertFalse((home/'hooks.json').exists())
   self.assertFalse((home/'agents'/'luna-execution.toml').exists())
   self.assertFalse((home/'rules'/'v19-safety.rules').exists())
   self.assertFalse((agents_home/'skills'/'v19-engineering'/'SKILL.md').exists())
   self.assertEqual((agents_home/'sentinel').read_text(encoding='utf-8'),'keep')
   self.assertFalse((home/'.governance-v16-backup').exists())

 def test_manifest_mismatch_rejected(self):
  with tempfile.TemporaryDirectory() as d:
   source=pathlib.Path(d)/'source'
   shutil.copytree(ROOT,source,ignore=shutil.ignore_patterns('.git','__pycache__','*.pyc','*.pyo'))
   (source/'codex'/'AGENTS.md').write_text('tampered',encoding='utf-8')
   result=subprocess.run([sys.executable,str(source/'scripts/install-governance.py'),'--source',str(source),'--codex-home',str(pathlib.Path(d)/'home'),'--dry-run'],capture_output=True,text=True)
   self.assertNotEqual(result.returncode,0)
   self.assertIn('manifest mismatch:codex/AGENTS.md',result.stderr)

 def test_personal_skill_upgrade_and_rollback_restore_previous_file(self):
  with tempfile.TemporaryDirectory() as d:
   parent=pathlib.Path(d); home=parent/'home'; home.mkdir(); agents_home=parent/'.agents'
   skill=agents_home/'skills'/'v19-engineering'/'SKILL.md'; skill.parent.mkdir(parents=True)
   skill.write_text('previous-personal-skill',encoding='utf-8')
   command=[sys.executable,str(ROOT/'scripts/install-governance.py'),'--source',str(ROOT),'--codex-home',str(home)]
   subprocess.check_call(command)
   self.assertNotEqual(skill.read_text(encoding='utf-8'),'previous-personal-skill')
   subprocess.check_call(command+['--rollback'])
   self.assertEqual(skill.read_text(encoding='utf-8'),'previous-personal-skill')

 def test_custom_agents_home_root_drift_preserves_both_roots_and_backup(self):
  with tempfile.TemporaryDirectory() as d:
   parent=pathlib.Path(d); home=parent/'home'; home.mkdir(); custom=parent/'custom-agents'; default=parent/'.agents'
   custom_skill=custom/'skills'/'v19-engineering'/'SKILL.md'; custom_skill.parent.mkdir(parents=True); custom_skill.write_text('custom-before',encoding='utf-8')
   default_skill=default/'skills'/'v19-engineering'/'SKILL.md'; default_skill.parent.mkdir(parents=True); default_skill.write_text('default-unrelated',encoding='utf-8')
   command=[sys.executable,str(ROOT/'scripts/install-governance.py'),'--source',str(ROOT),'--codex-home',str(home),'--agents-home',str(custom)]
   subprocess.check_call(command)
   installed=custom_skill.read_text(encoding='utf-8')
   self.assertNotEqual(installed,'custom-before')
   result=subprocess.run(command[:-2]+['--rollback'],capture_output=True,text=True)
   self.assertNotEqual(result.returncode,0)
   self.assertIn('backup root mismatch',result.stderr)
   self.assertEqual(custom_skill.read_text(encoding='utf-8'),installed)
   self.assertEqual(default_skill.read_text(encoding='utf-8'),'default-unrelated')
   self.assertTrue((home/'.governance-v16-backup').is_dir())
   subprocess.check_call(command+['--rollback'])
   self.assertEqual(custom_skill.read_text(encoding='utf-8'),'custom-before')
   self.assertEqual(default_skill.read_text(encoding='utf-8'),'default-unrelated')

 def test_personal_skill_parent_symlink_escape_is_rejected(self):
  with tempfile.TemporaryDirectory() as d:
   parent=pathlib.Path(d); home=parent/'home'; home.mkdir(); agents_home=parent/'.agents'
   unrelated=agents_home/'unrelated-personal-state'; unrelated.mkdir(parents=True)
   sentinel=unrelated/'SKILL.md'; sentinel.write_text('keep',encoding='utf-8')
   skills=agents_home/'skills'; skills.mkdir(); (skills/'v19-engineering').symlink_to(unrelated,target_is_directory=True)
   result=subprocess.run([sys.executable,str(ROOT/'scripts/install-governance.py'),'--source',str(ROOT),'--codex-home',str(home)],capture_output=True,text=True)
   self.assertNotEqual(result.returncode,0)
   self.assertIn('destination escape:@agents/skills/v19-engineering/SKILL.md',result.stderr)
   self.assertEqual(sentinel.read_text(encoding='utf-8'),'keep')
   self.assertFalse((home/'.governance-v16-backup').exists())

 def test_personal_skills_root_symlink_escape_is_rejected(self):
  with tempfile.TemporaryDirectory() as d:
   parent=pathlib.Path(d); home=parent/'home'; home.mkdir(); agents_home=parent/'.agents'; agents_home.mkdir()
   unrelated=parent/'unrelated-outside-agents'; unrelated.mkdir()
   sentinel=unrelated/'sentinel'; sentinel.write_text('keep',encoding='utf-8')
   (agents_home/'skills').symlink_to(unrelated,target_is_directory=True)
   result=subprocess.run([sys.executable,str(ROOT/'scripts/install-governance.py'),'--source',str(ROOT),'--codex-home',str(home)],capture_output=True,text=True)
   self.assertNotEqual(result.returncode,0)
   self.assertIn('unsafe agents skills root',result.stderr)
   self.assertEqual(sentinel.read_text(encoding='utf-8'),'keep')
   self.assertFalse((unrelated/'v19-engineering').exists())
   self.assertFalse((home/'.governance-v16-backup').exists())

 def test_interrupted_recovery_rejects_agents_root_drift(self):
  with tempfile.TemporaryDirectory() as d:
   parent=pathlib.Path(d); home=parent/'home'; home.mkdir(); custom=parent/'custom-agents'; default=parent/'.agents'
   custom.mkdir(); default.mkdir()
   backup=home/'.governance-v16-backup'; backup.mkdir()
   key='@agents/skills/v19-engineering/SKILL.md'
   (backup/'metadata.json').write_text(json.dumps({'schema':'governance-overlay-backup.v19','roots':{'codex_home':str(home.resolve()),'agents_home':str(custom.resolve())},'managed':[key],'previous':[],'installed':{key:'0'*64},'committed':False}),encoding='utf-8')
   result=subprocess.run([sys.executable,str(ROOT/'scripts/install-governance.py'),'--source',str(ROOT),'--codex-home',str(home)],capture_output=True,text=True)
   self.assertNotEqual(result.returncode,0)
   self.assertIn('backup root mismatch',result.stderr)
   self.assertTrue(backup.is_dir())

 def test_agents_home_symlink_into_codex_home_is_rejected(self):
  with tempfile.TemporaryDirectory() as d:
   parent=pathlib.Path(d); home=parent/'home'; home.mkdir()
   (parent/'.agents').symlink_to(home,target_is_directory=True)
   result=subprocess.run([sys.executable,str(ROOT/'scripts/install-governance.py'),'--source',str(ROOT),'--codex-home',str(home),'--dry-run'],capture_output=True,text=True)
   self.assertNotEqual(result.returncode,0)
   self.assertIn('must be disjoint',result.stderr)

 def test_rollback_rejects_agents_scope_outside_skills(self):
  with tempfile.TemporaryDirectory() as d:
   parent=pathlib.Path(d); home=parent/'home'; home.mkdir(); agents_home=parent/'.agents'; agents_home.mkdir()
   backup=home/'.governance-v16-backup'; backup.mkdir()
   (backup/'metadata.json').write_text(json.dumps({'schema':'governance-overlay-backup.v19','roots':{'codex_home':str(home.resolve()),'agents_home':str(agents_home.resolve())},'managed':['@agents/sentinel'],'previous':[],'installed':{'@agents/sentinel':'0'*64},'committed':True}),encoding='utf-8')
   sentinel=agents_home/'sentinel'; sentinel.write_text('keep',encoding='utf-8')
   result=subprocess.run([sys.executable,str(ROOT/'scripts/install-governance.py'),'--source',str(ROOT),'--codex-home',str(home),'--rollback'],capture_output=True,text=True)
   self.assertNotEqual(result.returncode,0)
   self.assertIn('invalid agents-home target',result.stderr)
   self.assertEqual(sentinel.read_text(encoding='utf-8'),'keep')

 def test_rollback_rejects_backup_source_symlink_escape(self):
  with tempfile.TemporaryDirectory() as d:
   parent=pathlib.Path(d); home=parent/'home'; home.mkdir(); agents_home=parent/'.agents'; agents_home.mkdir()
   outside=parent/'outside'; outside.mkdir(); (outside/'SKILL.md').write_text('outside',encoding='utf-8')
   backup=home/'.governance-v16-backup'; files=backup/'files'/'@agents'; files.mkdir(parents=True)
   (files/'skills').symlink_to(outside,target_is_directory=True)
   key='@agents/skills/SKILL.md'
   (backup/'metadata.json').write_text(json.dumps({'schema':'governance-overlay-backup.v19','roots':{'codex_home':str(home.resolve()),'agents_home':str(agents_home.resolve())},'managed':[key],'previous':[key],'installed':{key:'0'*64},'committed':True}),encoding='utf-8')
   result=subprocess.run([sys.executable,str(ROOT/'scripts/install-governance.py'),'--source',str(ROOT),'--codex-home',str(home),'--rollback'],capture_output=True,text=True)
   self.assertNotEqual(result.returncode,0)
   self.assertIn('backup source escape',result.stderr)
   self.assertEqual((outside/'SKILL.md').read_text(encoding='utf-8'),'outside')

 def test_rollback_rejects_personal_skill_parent_symlink_escape(self):
  with tempfile.TemporaryDirectory() as d:
   parent=pathlib.Path(d); home=parent/'home'; home.mkdir(); agents_home=parent/'.agents'
   unrelated=agents_home/'unrelated-personal-state'; unrelated.mkdir(parents=True)
   sentinel=unrelated/'SKILL.md'; sentinel.write_text('keep',encoding='utf-8')
   skills=agents_home/'skills'; skills.mkdir(); (skills/'demo').symlink_to(unrelated,target_is_directory=True)
   backup=home/'.governance-v16-backup'; backup.mkdir()
   key='@agents/skills/demo/SKILL.md'
   (backup/'metadata.json').write_text(json.dumps({'schema':'governance-overlay-backup.v19','roots':{'codex_home':str(home.resolve()),'agents_home':str(agents_home.resolve())},'managed':[key],'previous':[],'installed':{key:'0'*64},'committed':True}),encoding='utf-8')
   result=subprocess.run([sys.executable,str(ROOT/'scripts/install-governance.py'),'--source',str(ROOT),'--codex-home',str(home),'--rollback'],capture_output=True,text=True)
   self.assertNotEqual(result.returncode,0)
   self.assertIn('destination escape:'+key,result.stderr)
   self.assertEqual(sentinel.read_text(encoding='utf-8'),'keep')
   self.assertTrue(backup.is_dir())

 def test_interrupted_recovery_rejects_personal_skill_parent_symlink_escape(self):
  with tempfile.TemporaryDirectory() as d:
   parent=pathlib.Path(d); home=parent/'home'; home.mkdir(); agents_home=parent/'.agents'
   unrelated=agents_home/'unrelated-personal-state'; unrelated.mkdir(parents=True)
   sentinel=unrelated/'SKILL.md'; sentinel.write_text('keep',encoding='utf-8')
   skills=agents_home/'skills'; skills.mkdir(); (skills/'demo').symlink_to(unrelated,target_is_directory=True)
   backup=home/'.governance-v16-backup'; backup.mkdir()
   key='@agents/skills/demo/SKILL.md'
   (backup/'metadata.json').write_text(json.dumps({'schema':'governance-overlay-backup.v19','roots':{'codex_home':str(home.resolve()),'agents_home':str(agents_home.resolve())},'managed':[key],'previous':[],'installed':{key:'0'*64},'committed':False}),encoding='utf-8')
   result=subprocess.run([sys.executable,str(ROOT/'scripts/install-governance.py'),'--source',str(ROOT),'--codex-home',str(home)],capture_output=True,text=True)
   self.assertNotEqual(result.returncode,0)
   self.assertIn('destination escape:'+key,result.stderr)
   self.assertEqual(sentinel.read_text(encoding='utf-8'),'keep')
   self.assertTrue(backup.is_dir())

 def test_rollback_rejects_personal_skills_root_symlink_escape(self):
  with tempfile.TemporaryDirectory() as d:
   parent=pathlib.Path(d); home=parent/'home'; home.mkdir(); agents_home=parent/'.agents'; agents_home.mkdir()
   unrelated=parent/'unrelated-outside-agents'; unrelated.mkdir()
   sentinel=unrelated/'SKILL.md'; sentinel.write_text('keep',encoding='utf-8')
   (agents_home/'skills').symlink_to(unrelated,target_is_directory=True)
   backup=home/'.governance-v16-backup'; backup.mkdir()
   key='@agents/skills/SKILL.md'
   (backup/'metadata.json').write_text(json.dumps({'schema':'governance-overlay-backup.v19','roots':{'codex_home':str(home.resolve()),'agents_home':str(agents_home.resolve())},'managed':[key],'previous':[],'installed':{key:'0'*64},'committed':True}),encoding='utf-8')
   result=subprocess.run([sys.executable,str(ROOT/'scripts/install-governance.py'),'--source',str(ROOT),'--codex-home',str(home),'--rollback'],capture_output=True,text=True)
   self.assertNotEqual(result.returncode,0)
   self.assertIn('unsafe agents skills root',result.stderr)
   self.assertEqual(sentinel.read_text(encoding='utf-8'),'keep')
   self.assertTrue(backup.is_dir())

 def test_interrupted_recovery_rejects_personal_skills_root_symlink_escape(self):
  with tempfile.TemporaryDirectory() as d:
   parent=pathlib.Path(d); home=parent/'home'; home.mkdir(); agents_home=parent/'.agents'; agents_home.mkdir()
   unrelated=parent/'unrelated-outside-agents'; unrelated.mkdir()
   sentinel=unrelated/'SKILL.md'; sentinel.write_text('keep',encoding='utf-8')
   (agents_home/'skills').symlink_to(unrelated,target_is_directory=True)
   backup=home/'.governance-v16-backup'; backup.mkdir()
   key='@agents/skills/SKILL.md'
   (backup/'metadata.json').write_text(json.dumps({'schema':'governance-overlay-backup.v19','roots':{'codex_home':str(home.resolve()),'agents_home':str(agents_home.resolve())},'managed':[key],'previous':[],'installed':{key:'0'*64},'committed':False}),encoding='utf-8')
   result=subprocess.run([sys.executable,str(ROOT/'scripts/install-governance.py'),'--source',str(ROOT),'--codex-home',str(home)],capture_output=True,text=True)
   self.assertNotEqual(result.returncode,0)
   self.assertIn('unsafe agents skills root',result.stderr)
   self.assertEqual(sentinel.read_text(encoding='utf-8'),'keep')
   self.assertTrue(backup.is_dir())

 def test_manifest_hash_matching_forbidden_content_is_rejected(self):
  with tempfile.TemporaryDirectory() as d:
   source=pathlib.Path(d)/'source'
   shutil.copytree(ROOT,source,ignore=shutil.ignore_patterns('.git','__pycache__','*.pyc','*.pyo'))
   payload='synthetic_demo=true task_id=' + '/' + 'root/real-task\n'
   target=source/'codex'/'AGENTS.md'; target.write_text(payload,encoding='utf-8')
   manifest=json.loads((source/'manifest.json').read_text(encoding='utf-8'))
   manifest['files']['codex/AGENTS.md']=hashlib.sha256(payload.encode()).hexdigest()
   (source/'manifest.json').write_text(json.dumps(manifest,separators=(',',':')),encoding='utf-8')
   result=subprocess.run([sys.executable,str(source/'scripts/install-governance.py'),'--source',str(source),'--codex-home',str(pathlib.Path(d)/'home'),'--dry-run'],capture_output=True,text=True)
   self.assertNotEqual(result.returncode,0)
   self.assertIn('forbidden content:',result.stderr)
 def test_manifest_unknown_metadata_is_rejected(self):
  with tempfile.TemporaryDirectory() as d:
   source=pathlib.Path(d)/'source'
   shutil.copytree(ROOT,source,ignore=shutil.ignore_patterns('.git','__pycache__','*.pyc','*.pyo'))
   manifest=json.loads((source/'manifest.json').read_text(encoding='utf-8'))
   manifest['synthetic_forbidden'] = '/' + 'home/' + 'not-public'
   (source/'manifest.json').write_text(json.dumps(manifest,separators=(',',':')),encoding='utf-8')
   result=subprocess.run([sys.executable,str(source/'scripts/install-governance.py'),'--source',str(source),'--codex-home',str(pathlib.Path(d)/'home'),'--dry-run'],capture_output=True,text=True)
   self.assertNotEqual(result.returncode,0)
   self.assertIn('invalid manifest metadata:',result.stderr)

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

 def test_failed_upgrade_preserves_prior_rollback_generation(self):
  with tempfile.TemporaryDirectory() as d:
   home=pathlib.Path(d)/'home'; home.mkdir()
   agents=home/'AGENTS.md'; agents.write_text('original-agents',encoding='utf-8')
   command=[sys.executable,str(ROOT/'scripts/install-governance.py'),'--source',str(ROOT),'--codex-home',str(home)]
   subprocess.check_call(command)
   self.assertTrue((home/'.governance-v16-backup'/'metadata.json').is_file())
   agents.write_text('active-before-failed-upgrade',encoding='utf-8')
   staged_collision=home/'hooks.json.governance-v16.tmp'; staged_collision.mkdir()
   result=subprocess.run(command,capture_output=True,text=True)
   self.assertNotEqual(result.returncode,0)
   self.assertEqual(agents.read_text(encoding='utf-8'),'active-before-failed-upgrade')
   self.assertTrue((home/'.governance-v16-backup'/'metadata.json').is_file())
   self.assertFalse((home/'.governance-v16-backup.previous').exists())
   staged_collision.rmdir()
   subprocess.check_call(command+['--rollback'])
   self.assertEqual(agents.read_text(encoding='utf-8'),'original-agents')
if __name__=='__main__': unittest.main()

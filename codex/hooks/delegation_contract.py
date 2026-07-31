"""Typed v15 delegation contract and parent-owned state bridge (stdlib only)."""
from __future__ import annotations
import argparse, hashlib, json, os, pathlib, re, sys, tempfile, contextlib
try:
 import fcntl
except ImportError:
 fcntl=None
class ContractError(ValueError): pass
REQUIRED_PACKET={"schema","parent_task_id","child_task_id","assigned_model","role","max_depth","depth","permissions","forbidden_permissions","lease","retry_budget","active_mission_lock","plugin_inventory","result_schema"}
REQUIRED_RESULT={"schema","parent_task_id","child_task_id","assigned_model","task_id","depth","changed_paths","counts","retry_used","contamination","status"}
SAFE_PERMISSIONS={"read","write_paths","test","inspect","evidence"}
FORBIDDEN_CANONICAL={"git","github","review","approve","merge","shell","bash","git_push","github_api","reviewer","approver","merger"}
STATUSES={"complete","blocked","failed","rejected"}
def _id(v): return isinstance(v,str) and bool(re.fullmatch(r"[A-Za-z0-9_.:/-]+",v))
def normalize_path(v):
 if not isinstance(v,str) or not v or '\\' in v or v.startswith('/') or re.match(r'^[A-Za-z]:',v): raise ContractError('non-relative path')
 parts=v.split('/')
 if any(x in ('','.','..') for x in parts): raise ContractError('noncanonical path')
 return '/'.join(parts)
def _paths(paths):
 if not isinstance(paths,list) or not paths: raise ContractError('lease')
 out=[normalize_path(x) for x in paths]
 if len(set(out))!=len(out): raise ContractError('duplicate lease')
 for i,a in enumerate(out):
  for b in out[i+1:]:
   if a==b or a.startswith(b+'/') or b.startswith(a+'/'): raise ContractError('overlapping lease')
 return out
def validate_packet(packet,parent_task_id=None,active_leases=None):
 if not isinstance(packet,dict) or not REQUIRED_PACKET<=packet.keys(): raise ContractError('missing packet field')
 if packet['schema']!='delegation.v1': raise ContractError('schema')
 if parent_task_id and packet['parent_task_id']!=parent_task_id: raise ContractError('parent mismatch')
 if not _id(packet['parent_task_id']) or not _id(packet['child_task_id']): raise ContractError('task identity')
 if packet['assigned_model'] not in {'gpt-5.6-luna','gpt-5.3-codex-spark'}: raise ContractError('model')
 if packet['role']!='specialist': raise ContractError('role')
 if packet['max_depth']!=1 or packet['depth']!=1: raise ContractError('depth')
 if not packet['active_mission_lock'] or packet['plugin_inventory']!='informational': raise ContractError('mission lock')
 perms=packet['permissions']; forbidden=packet['forbidden_permissions']
 if not isinstance(perms,list) or any(not isinstance(x,str) or x.lower()!=x or x not in SAFE_PERMISSIONS for x in perms): raise ContractError('permission allowlist')
 if set(forbidden)!=FORBIDDEN_CANONICAL: raise ContractError('canonical forbidden set')
 if any(x.lower() in FORBIDDEN_CANONICAL or any(t in x.lower() for t in ('git','github','shell','bash','review','approv','merge')) for x in perms): raise ContractError('forbidden child permission')
 paths=_paths(packet['lease'].get('paths') if isinstance(packet.get('lease'),dict) else None)
 if active_leases:
  for other in active_leases:
   for a in paths:
    for b in other:
     if a==b or a.startswith(b+'/') or b.startswith(a+'/'): raise ContractError('sibling lease overlap')
 if not isinstance(packet['retry_budget'],dict) or packet['retry_budget'].get('semantic_contamination')!=1: raise ContractError('retry budget')
 return True
def validate_result(result,packet,state=None):
 if not isinstance(result,dict) or not REQUIRED_RESULT<=result.keys(): raise ContractError('missing result field')
 if result['schema']!='delegation-result.v1': raise ContractError('result schema')
 if result['parent_task_id']!=packet['parent_task_id'] or result['child_task_id']!=packet['child_task_id']: raise ContractError('result identity')
 if result['assigned_model']!=packet['assigned_model'] or result['task_id']!=packet['child_task_id']: raise ContractError('child model/task mismatch')
 if result['depth']!=packet['depth']: raise ContractError('result depth')
 status=result['status'];
 if status not in STATUSES: raise ContractError('status')
 if not isinstance(result['retry_used'],int) or isinstance(result['retry_used'],bool) or result['retry_used'] not in (0,1): raise ContractError('retry overflow')
 transcript=result.get('retry_transcript',[])
 if not isinstance(transcript,list) or len(transcript)>1 or result['retry_used']!=len(transcript): raise ContractError('retry consistency')
 c=result['counts']
 if not isinstance(c,dict): raise ContractError('counts')
 keys=('total','ran','passed','failed','skipped','unknown')
 if any(k not in c for k in keys): raise ContractError('counts fields')
 if any(not isinstance(c[k],int) or isinstance(c[k],bool) or c[k]<0 for k in keys): raise ContractError('counts types')
 if c['total']!=c['passed']+c['failed']+c['skipped'] or c['ran']!=c['passed']+c['failed']: raise ContractError('count arithmetic')
 if result['contamination'] is not False:
  if state is not None:
   attempt=result.get('attempt_id'); rec=state.setdefault('delegations',{}).setdefault(state_key(packet),{'attempts':[],'accepted':False})
   if not _id(attempt) or attempt in rec['attempts'] or len(rec['attempts'])>=2: raise ContractError('attempt replay')
   rec['attempts'].append(attempt)
  raise ContractError('contaminated result')
 if status=='complete' and (c['total']<=0 or c['failed']!=0 or c['skipped']!=0 or c['unknown']!=0 or not result.get('artifact_sha256') or not result.get('evidence_id')): raise ContractError('incomplete evidence')
 if result['contamination'] is not False:
  if state is not None:
   attempt=result.get('attempt_id'); rec=state.setdefault('delegations',{}).setdefault(state_key(packet),{'attempts':[],'accepted':False})
   if not _id(attempt) or attempt in rec['attempts'] or len(rec['attempts'])>=2: raise ContractError('attempt replay')
   rec['attempts'].append(attempt)
  raise ContractError('contaminated result')
 lease=_paths(packet['lease']['paths'])
 for path in result['changed_paths']:
  n=normalize_path(path)
  if not any(n==p or n.startswith(p+'/') for p in lease): raise ContractError('changed path outside lease')
 if state:
  key=state_key(packet); rec=state.setdefault('delegations',{}).setdefault(key,{'attempts':[],'accepted':False})
  attempt=result.get('attempt_id')
  if not _id(attempt) or attempt in rec['attempts']: raise ContractError('attempt replay')
  if len(rec['attempts'])>=2: raise ContractError('retry ledger exhausted')
 return True
def state_key(packet): return hashlib.sha256(json.dumps({k:packet[k] for k in ('parent_task_id','child_task_id','assigned_model','depth','lease')},sort_keys=True).encode()).hexdigest()
def _load(root):
 p=pathlib.Path(root); p.mkdir(mode=0o700,parents=True,exist_ok=True); f=p/'delegation-state.json'
 if f.exists(): return json.loads(f.read_text()),f
 return {'schema':'delegation-state.v1','delegations':{}},f
def _save(state,f):
 tmp=f.with_suffix('.tmp'); tmp.write_text(json.dumps(state,sort_keys=True)); os.chmod(tmp,0o600); os.replace(tmp,f)
@contextlib.contextmanager
def state_lock(root):
 p=pathlib.Path(root); p.mkdir(mode=0o700,parents=True,exist_ok=True); lf=p/'.delegation.lock'; fh=open(lf,'a+')
 try:
  if fcntl: fcntl.flock(fh.fileno(),fcntl.LOCK_EX)
  yield
 finally:
  if fcntl: fcntl.flock(fh.fileno(),fcntl.LOCK_UN)
  fh.close()

def cli():
 ap=argparse.ArgumentParser(); sub=ap.add_subparsers(dest='cmd',required=True)
 for name in ('pre-dispatch','subagent-start','ingest-result'):
  q=sub.add_parser(name); q.add_argument('--packet',required=True); q.add_argument('--state-root',required=True); q.add_argument('--result')
 a=ap.parse_args(); packet=json.loads(pathlib.Path(a.packet).read_text()); expected=hashlib.sha256(pathlib.Path(a.packet).read_bytes()).hexdigest()
 with state_lock(a.state_root):
  state,f=_load(a.state_root)
  key=state_key(packet)
  if a.cmd=='pre-dispatch':
   validate_packet(packet,active_leases=[x.get('lease',[]) for x in state.get('active',[])])
   if key in state.get('packets',{}): raise ContractError('packet already registered')
   mission=hashlib.sha256(json.dumps(packet,sort_keys=True).encode()).hexdigest(); state.setdefault('packets',{})[key]={'packet_sha256':expected,'mission_hash':mission,'started':False,'terminal':False,'child_task_id':packet['child_task_id'],'assigned_model':packet['assigned_model'],'depth':packet['depth'],'lease':packet['lease']['paths']}; state.setdefault('active',[]).append({'task_id':packet['child_task_id'],'lease':packet['lease']['paths'],'key':key}); _save(state,f); print(json.dumps({'decision':'allow','mission_hash':mission})); return 0
  rec=state.get('packets',{}).get(key)
  if a.cmd=='subagent-start':
   if os.environ.get('CODEX_DELEGATION_PACKET_SHA256')!=expected or not rec or rec.get('packet_sha256')!=expected or rec.get('started') or rec.get('terminal'): raise ContractError('missing, wrong, unregistered, or duplicate packet')
   validate_packet(packet); rec['started']=True; _save(state,f); print(json.dumps({'decision':'allow','packet_sha256':expected})); return 0
  if not a.result: raise ContractError('result required')
  if not rec or not rec.get('started') or rec.get('terminal'): raise ContractError('result without active started record')
  result=json.loads(pathlib.Path(a.result).read_text()); validate_result(result,packet,state); rec['terminal']=True; rec['decision']='accept' if result['status']=='complete' else 'reject'; rec['attempt_id']=result['attempt_id']; state['active']=[x for x in state.get('active',[]) if x.get('key')!=key]; _save(state,f); print(json.dumps({'decision':rec['decision'],'attempt_id':result['attempt_id']})); return 0
if __name__=='__main__':
 try: raise SystemExit(cli())
 except ContractError as e: print(json.dumps({'decision':'reject','reason':str(e)})); raise SystemExit(2)

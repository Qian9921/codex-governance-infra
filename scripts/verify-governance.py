#!/usr/bin/env python3
from __future__ import annotations
import argparse, hashlib, json, pathlib, re, stat, sys
FORBIDDEN_PARTS=("sessions","hook-receipts","plugins","connections","models_cache.json",".env")
TOKEN_RE=re.compile(r"gh[pso]_[A-Za-z0-9]{20,}")
PRIVATE_PATHS=(re.compile(r"/"+"home/[A-Za-z0-9._-]+/"),re.compile(r"/"+"Users/[A-Za-z0-9._-]+/"),re.compile(r"[A-Za-z]:\\\\Users\\\\[A-Za-z0-9._-]+\\\\"))
SAFE_DOC_TERMS=("credential patterns","tokens","sessions","private paths","do not include")
ROOT_ALLOW={"README.md","SECURITY.md","PRIVACY.md","LICENSE","AGENTS.md","manifest.json"}
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def tracked(root):
 out=[]; errs=[]
 for p in sorted(root.rglob('*')):
  if '.git' in p.parts or '__pycache__' in p.parts or p.suffix=='.pyc': continue
  rel=p.relative_to(root).as_posix()
  if p.is_symlink(): errs.append('symlink:'+rel); continue
  if not p.is_file(): continue
  out.append(rel)
 return out,errs
def allowed(rel,data):
 allow=data.get('allowlist',[]) if isinstance(data,dict) else []
 return rel in ROOT_ALLOW or any(rel.startswith(x) for x in allow if isinstance(x,str))
def scan(root):
 files,errors=tracked(root)
 for rel in files:
  p=root/rel
  if any(x in rel.lower() for x in FORBIDDEN_PARTS): errors.append('forbidden path:'+rel)
  try: raw=p.read_bytes(); text=raw.decode('utf-8')
  except UnicodeDecodeError: errors.append('non-utf8:'+rel); continue
  if TOKEN_RE.search(text): errors.append('credential token:'+rel)
  for pat in PRIVATE_PATHS:
   if pat.search(text) and not any(term in text.lower() for term in SAFE_DOC_TERMS): errors.append('private path:'+rel)
 return files,errors
def main():
 ap=argparse.ArgumentParser(); ap.add_argument('--repo',default='.'); a=ap.parse_args(); root=pathlib.Path(a.repo).resolve(); files,errors=scan(root)
 for req in ('codex/AGENTS.md','codex/BRIEF-TEMPLATES.md','codex/hooks.json','scripts/install-governance.py','manifest.json'):
  if req not in files: errors.append('missing:'+req)
 man=root/'manifest.json'
 try: data=json.loads(man.read_text());
 except Exception as e: data={}; errors.append('manifest parse:'+str(e))
 if not isinstance(data,dict) or data.get('schema_version') not in ('1','1.0'): errors.append('manifest schema')
 declared=data.get('files') if isinstance(data,dict) else None
 if not isinstance(declared,dict) or any(not isinstance(k,str) or not isinstance(v,str) or not re.fullmatch(r'[0-9a-f]{64}',v) for k,v in declared.items()): errors.append('manifest files schema')
 expected={f for f in files if f!='manifest.json'}
 if isinstance(declared,dict):
  if set(declared)!=expected: errors.append('manifest path-set mismatch')
  for f in expected:
   if not allowed(f,data): errors.append('outside allowlist:'+f)
   elif declared.get(f)!=sha(root/f): errors.append('hash mismatch:'+f)
 policy=(root/'codex/AGENTS.md').stat().st_size if (root/'codex/AGENTS.md').exists() else 0; brief=(root/'codex/BRIEF-TEMPLATES.md').stat().st_size if (root/'codex/BRIEF-TEMPLATES.md').exists() else 0
 if policy>26624 or brief>26624: errors.append('hard size limit')
 rel=root/'codex/V15_RELEASE.json'; mat=root/'codex/contracts/v14_preservation_matrix.json'
 if not rel.exists(): errors.append('missing:codex/V15_RELEASE.json')
 else:
  try:
   rd=json.loads(rel.read_text());
   if not isinstance(rd.get('version'),str) or not rd['version'].startswith('2026-07-31-v15'): errors.append('release identity')
   for x in rd.get('v14_sources',{}).values():
    if not all(k in x for k in ('sha256','bytes','lines')): errors.append('release baseline fields')
  except Exception: errors.append('release parse')
 if mat.exists():
  try:
   rows=json.loads(mat.read_text()).get('clauses',[]); ids=[x.get('id') for x in rows];
   if len(ids)!=len(set(ids)) or any(not i for i in ids): errors.append('matrix ids')
   if len(rows)<11: errors.append('matrix incomplete')
  except Exception: errors.append('matrix parse')
 else: errors.append('missing:matrix')
 out={'repo':str(root),'files':len(files),'errors':errors,'status':'GREEN' if not errors else 'RED'}; print(json.dumps(out,sort_keys=True)); return 0 if not errors else 1
if __name__=='__main__': raise SystemExit(main())

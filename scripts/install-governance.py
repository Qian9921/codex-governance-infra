#!/usr/bin/env python3
from __future__ import annotations
import argparse, hashlib, json, os, pathlib, shutil, tempfile, stat
from typing import List, Dict
FORBIDDEN=("sessions","hook-receipts","plugins","connections","models_cache.json",".env")

def digest(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def safe_entries(src: pathlib.Path):
    out=[]
    for p in sorted((src/'codex').rglob('*')):
        if '__pycache__' in p.parts or p.suffix=='.pyc': continue
        if p.is_symlink(): raise RuntimeError(f'non-regular artifact:{p}')
        if p.is_dir(): continue
        if not p.is_file(): raise RuntimeError(f'non-regular artifact:{p}')
        rel=p.relative_to(src/'codex').as_posix()
        target=rel
        if rel.startswith('hooks/') or rel.startswith('contracts/'):
            target=rel
        elif rel in ('AGENTS.md','BRIEF-TEMPLATES.md','hooks.json','V15_RELEASE.json'):
            target=rel
        else: raise RuntimeError(f'outside install allowlist:{rel}')
        if any(x in target.lower() for x in FORBIDDEN): raise RuntimeError(f'forbidden:{target}')
        out.append((target,p))
    return out

def state_path(dest): return dest/'.codex-governance-v15-state.json'
def transformed_source(source, target, dest):
    if target != 'hooks.json': return source
    data=json.loads(source.read_text())
    for groups in data.get('hooks',{}).values():
        for matcher in groups:
            for hook in matcher.get('hooks',[]):
                cmd=hook.get('command','').replace('$CODEX_HOME',str(dest))
                hook['command']=cmd
    tmp=pathlib.Path(tempfile.mktemp(prefix='codex-v15-hooks-')); tmp.write_text(json.dumps(data,sort_keys=True)); return tmp
def backup_path(dest): return dest.parent/(dest.name+'.v15-managed-backup')
def failpoint(n):
    raw=os.environ.get('CODEX_INSTALL_FAIL_AFTER','')
    return raw and int(raw)==n

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--source',default='.'); ap.add_argument('--codex-home',required=True); ap.add_argument('--dry-run',action='store_true'); ap.add_argument('--rollback',action='store_true'); a=ap.parse_args()
    src=pathlib.Path(a.source).resolve(); dest=pathlib.Path(a.codex_home).resolve(); state=state_path(dest); backup=backup_path(dest)
    if a.rollback:
        if not state.exists(): raise SystemExit('no managed transaction state')
        rec=json.loads(state.read_text());
        if rec.get('schema')!='install-transaction.v1': raise SystemExit('invalid transaction state')
        for item in rec['managed']:
            target=dest/item['path']; expected=item.get('installed_sha256')
            if target.exists() and digest(target)!=expected: raise SystemExit('rollback refused: managed target changed:'+item['path'])
        for item in rec['managed']:
            target=dest/item['path']
            if item['previous_exists']:
                srcb=backup/item['path']; target.parent.mkdir(parents=True,exist_ok=True); shutil.copy2(srcb,target); os.chmod(target,item.get('previous_mode') or 0o644)
            elif target.exists(): target.unlink()
        if backup.exists(): shutil.rmtree(backup)
        state.unlink(); print(json.dumps({'status':'ROLLED_BACK','files':len(rec['managed'])},sort_keys=True)); return 0
    entries=safe_entries(src)
    if backup.exists() or state.exists(): raise SystemExit('unowned backup/state collision')
    managed=[]
    for target,p in entries:
        q=dest/target
        if q.lexists() if hasattr(q,'lexists') else os.path.lexists(q):
            info=os.lstat(q)
            if stat.S_ISLNK(info.st_mode) or stat.S_ISDIR(info.st_mode): raise SystemExit('unsafe pre-existing managed object:'+target)
        managed.append({'path':target,'source_sha256':digest(p),'previous_exists':q.exists(),'previous_sha256':digest(q) if q.exists() else None,'previous_mode':stat.S_IMODE(os.lstat(q).st_mode) if q.exists() else None})
    plan={'schema':'install-transaction.v1','managed':managed}
    print(json.dumps({'status':'DRY_RUN' if a.dry_run else 'READY','files':len(entries),'destination':'$CODEX_HOME' if a.dry_run else str(dest),'hashes':{x[0]:digest(x[1]) for x in entries}},sort_keys=True))
    if a.dry_run: return 0
    dest.mkdir(parents=True,exist_ok=True); backup.mkdir(mode=0o700)
    try:
        for i,item in enumerate(managed,1):
            target=dest/item['path']; target.parent.mkdir(parents=True,exist_ok=True)
            if item['previous_exists']:
                b=backup/item['path']; b.parent.mkdir(parents=True,exist_ok=True); shutil.copy2(target,b)
            if failpoint(i): raise RuntimeError('injected failure after backup/mutation')
            source=dict(entries)[item['path']]; generated=transformed_source(source,item['path'],dest); tmp=target.with_name(target.name+'.v15tmp'); shutil.copy2(generated,tmp); os.chmod(tmp,0o600 if str(target).endswith('.json') else 0o644); os.replace(tmp,target); item['installed_sha256']=digest(target); item['installed_mode']=stat.S_IMODE(os.lstat(target).st_mode);
            if generated != source: generated.unlink(missing_ok=True)
        state.write_text(json.dumps(plan,sort_keys=True)); os.chmod(state,0o600)
    except Exception:
        for item in reversed(managed):
            target=dest/item['path']
            if item['previous_exists'] and (backup/item['path']).exists(): shutil.copy2(backup/item['path'],target); os.chmod(target,item.get('previous_mode') or 0o644)
            elif not item['previous_exists'] and target.exists(): target.unlink()
        if backup.exists(): shutil.rmtree(backup)
        raise
    print(json.dumps({'status':'INSTALLED','files':len(managed)},sort_keys=True)); return 0
if __name__=='__main__': raise SystemExit(main())

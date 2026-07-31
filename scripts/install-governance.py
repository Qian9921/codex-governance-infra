#!/usr/bin/env python3
from __future__ import annotations
import argparse,hashlib,json,os,pathlib,shutil,tempfile
ALLOW=("codex/",)
FORBIDDEN=("sessions","hook-receipts","plugins","connections","models_cache.json",".env")
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def collect(src):
    out=[]
    for p in sorted(src.rglob('*')):
        if p.is_file() and '.git' not in p.parts and '__pycache__' not in p.parts and p.suffix != '.pyc':
            rel=p.relative_to(src).as_posix()
            if rel.startswith(ALLOW): out.append((rel,p))
            elif rel not in {'README.md','SECURITY.md','PRIVACY.md','LICENSE','AGENTS.md','manifest.json'}: continue
    return out
def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--source',default='.'); ap.add_argument('--codex-home',required=True); ap.add_argument('--dry-run',action='store_true'); ap.add_argument('--rollback',action='store_true'); a=ap.parse_args(); src=pathlib.Path(a.source).resolve(); dest=pathlib.Path(a.codex_home).resolve(); backup=dest.with_name(dest.name+'.v15-backup')
    if a.rollback:
        if not backup.exists(): raise SystemExit('no backup')
        if dest.exists(): shutil.rmtree(dest)
        backup.rename(dest); print(json.dumps({'status':'ROLLED_BACK','destination':str(dest)})); return 0
    entries=collect(src); bad=[r for r,_ in entries if any(x in r for x in FORBIDDEN)]
    if bad: raise SystemExit('forbidden:'+','.join(bad))
    print(json.dumps({'status':'DRY_RUN' if a.dry_run else 'READY','files':len(entries),'destination':'$CODEX_HOME' if a.dry_run else str(dest),'hashes':{r:sha(p) for r,p in entries}},sort_keys=True))
    if a.dry_run: return 0
    tmp=pathlib.Path(tempfile.mkdtemp(prefix='codex-v15-',dir=dest.parent));
    try:
        for rel,p in entries:
            q=tmp/rel; q.parent.mkdir(parents=True,exist_ok=True); shutil.copy2(p,q); os.chmod(q,0o600 if q.suffix=='.json' else 0o644)
        if backup.exists(): shutil.rmtree(backup)
        if dest.exists(): dest.rename(backup)
        tmp.rename(dest)
    except Exception:
        shutil.rmtree(tmp,ignore_errors=True); raise
    return 0
if __name__=='__main__': main()

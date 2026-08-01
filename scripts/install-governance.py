#!/usr/bin/env python3
from __future__ import annotations
import argparse,hashlib,json,os,pathlib,shutil,tempfile
FORBIDDEN=("sessions","hook-receipts","plugins","connections","models_cache.json",".env")
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def collect(src):
    package=src/"codex"
    if not package.is_dir(): raise SystemExit("missing codex package")
    package_root=package.resolve(strict=True)
    try:
        declared=json.loads((src/"manifest.json").read_text(encoding="utf-8"))["files"]
    except (OSError,KeyError,TypeError,json.JSONDecodeError):
        raise SystemExit("invalid manifest")
    if not isinstance(declared,dict): raise SystemExit("invalid manifest files")
    out=[]
    for source_rel,expected_hash in sorted(declared.items()):
        if not isinstance(source_rel,str) or not source_rel.startswith("codex/"): continue
        parts=source_rel.split("/")
        if "\\" in source_rel or len(parts)<2 or parts[0]!="codex" or any(part in {"",".",".."} for part in parts):
            raise SystemExit("noncanonical manifest path:"+source_rel)
        rel="/".join(parts[1:])
        p=package.joinpath(*parts[1:])
        try:
            resolved=p.resolve(strict=True)
            resolved.relative_to(package_root)
        except (FileNotFoundError,RuntimeError,ValueError):
            raise SystemExit("source escape:"+source_rel)
        if not p.is_file() or p.is_symlink() or sha(p)!=expected_hash:
            raise SystemExit("manifest mismatch:"+source_rel)
        out.append((rel,p))
    if not out: raise SystemExit("empty codex package")
    return out
def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--source',default='.'); ap.add_argument('--codex-home',required=True); ap.add_argument('--dry-run',action='store_true'); ap.add_argument('--rollback',action='store_true'); a=ap.parse_args(); src=pathlib.Path(a.source).resolve(); dest=pathlib.Path(a.codex_home).resolve(); backup=dest.with_name(dest.name+'.v16-backup')
    if a.rollback:
        if not backup.exists(): raise SystemExit('no backup')
        if dest.exists(): shutil.rmtree(dest)
        backup.rename(dest); print(json.dumps({'status':'ROLLED_BACK','destination':str(dest)})); return 0
    entries=collect(src); bad=[r for r,_ in entries if any(x in r for x in FORBIDDEN)]
    if bad: raise SystemExit('forbidden:'+','.join(bad))
    print(json.dumps({'status':'DRY_RUN' if a.dry_run else 'READY','files':len(entries),'destination':'$CODEX_HOME' if a.dry_run else str(dest),'hashes':{r:sha(p) for r,p in entries}},sort_keys=True))
    if a.dry_run: return 0
    tmp=pathlib.Path(tempfile.mkdtemp(prefix='codex-v16-',dir=dest.parent));
    try:
        for rel,p in entries:
            q=tmp.joinpath(*rel.split("/"))
            try: q.resolve(strict=False).relative_to(tmp.resolve(strict=True))
            except (RuntimeError,ValueError): raise SystemExit("destination escape:"+rel)
            q.parent.mkdir(parents=True,exist_ok=True); shutil.copy2(p,q); os.chmod(q,0o600 if q.suffix=='.json' else 0o644)
        if backup.exists(): shutil.rmtree(backup)
        if dest.exists(): dest.rename(backup)
        tmp.rename(dest)
    except Exception:
        shutil.rmtree(tmp,ignore_errors=True); raise
    return 0
if __name__=='__main__': main()

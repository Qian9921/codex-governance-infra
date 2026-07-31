#!/usr/bin/env python3
from __future__ import annotations
import argparse, hashlib, json, pathlib, re, sys
FORBIDDEN_PARTS=("sessions","hook-receipts","plugins","connections","models_cache.json",".env")
FORBIDDEN_RE=(re.compile(r"gh[pso]_[A-Za-z0-9]{20,}"),re.compile(r"(?:session|turn|prompt|transcript)[_-]?id\s*[:=]\s*[A-Za-z0-9-]{12,}",re.I),re.compile(r"/" + "home/" + "martin/"))
ALLOW_PATHS={"README.md","SECURITY.md","PRIVACY.md","LICENSE","AGENTS.md","manifest.json"}
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def scan(root):
    files=[]; errors=[]
    for p in sorted(root.rglob("*")):
        if not p.is_file() or ".git" in p.parts: continue
        rel=p.relative_to(root).as_posix(); files.append(rel)
        if any(x in rel.lower() for x in FORBIDDEN_PARTS): errors.append(f"forbidden path:{rel}")
        try: text=p.read_text(errors="strict")
        except UnicodeDecodeError: continue
        for pat in FORBIDDEN_RE:
            if pat.search(text) and rel not in {"PRIVACY.md","SECURITY.md"}: errors.append(f"forbidden content:{rel}:{pat.pattern}")
    return files,errors
def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--repo",default="."); a=ap.parse_args(); root=pathlib.Path(a.repo).resolve(); files,errors=scan(root)
    for req in ("codex/AGENTS.md","codex/BRIEF-TEMPLATES.md","codex/hooks/hooks.json","scripts/install-governance.py","manifest.json"): 
        if req not in files: errors.append("missing:"+req)
    policy=(root/"codex/AGENTS.md").stat().st_size; brief=(root/"codex/BRIEF-TEMPLATES.md").stat().st_size
    if policy>26624 or brief>18000: errors.append("size limit")
    manifest=root/"manifest.json"
    if manifest.exists():
        data=json.loads(manifest.read_text()); tracked={f:sha(root/f) for f in files if f != "manifest.json"}; data["files"]={k:v for k,v in data.get("files",{}).items() if k in tracked}
        # manifest is updated by --write-manifest; verifier validates listed hashes and allowlist.
        for f,h in data.get("files",{}).items():
            if f not in tracked or tracked[f]!=h: errors.append("hash mismatch:"+f)
    out={"repo":str(root),"files":len(files),"errors":errors,"status":"GREEN" if not errors else "RED"}
    print(json.dumps(out,sort_keys=True)); return 0 if not errors else 1
if __name__=='__main__': sys.exit(main())

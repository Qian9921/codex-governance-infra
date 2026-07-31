#!/usr/bin/env python3
"""No-follow package/privacy verifier; manifest is the policy source of truth."""
from __future__ import annotations
import argparse, hashlib, json, pathlib, re, stat, subprocess, sys

ID_NAMES = ("session_id", "turn_id", "prompt_id", "transcript_id", "receipt_id")
ID_RE = re.compile(r"(?<![A-Za-z0-9_])[\"\']?(?P<key>session_id|turn_id|prompt_id|transcript_id|receipt_id)[\"\']?\s*(?:=|:)\s*(?P<quote>[\"\']?)(?P<value>[^\"\'\s,}\]]+)(?P=quote)", re.I)
TOKEN_RE = re.compile(r"\bgh[pso]_[A-Za-z0-9]{20,}\b")
LINUX_PATH_RE = re.compile(r"(?<![A-Za-z0-9])/(?:home|Users)/[A-Za-z0-9._-]+(?:/[^\s'\"<>`]*)?")
WINDOWS_PATH_RE = re.compile(r"(?<![A-Za-z0-9])[A-Za-z]:\\+Users\\+[A-Za-z0-9._-]+(?:\\+[^\s'\"<>`]*)?")
ESCAPED_PATH_RE = re.compile(r"(?:\\{1,4}/|/|\\+/)(?:home|Users)(?:\\{1,4}/|/|\\+/)[A-Za-z0-9._-]+")
SAFE_PLACEHOLDERS = {"<user>", "${HOME}", "$HOME", "${CODEX_HOME}", "$CODEX_HOME", "example.invalid", "synthetic-token", "<synthetic-id>", "session-secret-123", "turn-secret-456", "runtime-id"}
ROOT_ALLOW = {"README.md", "SECURITY.md", "PRIVACY.md", "LICENSE", "AGENTS.md", "manifest.json"}


def sha(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _lstat(path: pathlib.Path):
    try: return path.lstat()
    except FileNotFoundError: return None


def _manifest(root: pathlib.Path) -> tuple[dict, list[str]]:
    errors: list[str] = []
    try:
        data = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    except Exception as exc:
        return {}, ["manifest parse:" + str(exc)]
    if not isinstance(data, dict): return {}, ["manifest schema"]
    if data.get("schema_version") not in ("1", "1.0"): errors.append("manifest schema")
    forbidden = data.get("forbidden")
    if not isinstance(forbidden, list) or any(not isinstance(x, str) or not x for x in forbidden): errors.append("manifest forbidden schema")
    return data, errors


def tracked(root: pathlib.Path) -> tuple[list[str], list[str]]:
    out: list[str] = []; errors: list[str] = []
    for p in sorted(root.rglob("*")):
        if ".git" in p.parts or "__pycache__" in p.parts or p.suffix == ".pyc": continue
        rel = p.relative_to(root).as_posix(); info = _lstat(p)
        if info is None: continue
        if stat.S_ISLNK(info.st_mode): errors.append("symlink:" + rel); continue
        if stat.S_ISREG(info.st_mode): out.append(rel)
    return out, errors


def git_tracked(root: pathlib.Path):
    try:
        p = subprocess.run(["git", "-C", str(root), "ls-files", "-z"], capture_output=True, check=False)
        if p.returncode != 0: return None
        return {x for x in p.stdout.decode().split("\0") if x}
    except (OSError, UnicodeDecodeError): return None


def allowed(rel: str, data: dict) -> bool:
    allow = data.get("allowlist", []) if isinstance(data, dict) else []
    return rel in ROOT_ALLOW or any(isinstance(x, str) and (rel == x.rstrip("/") or rel.startswith(x)) for x in allow)


def _is_safe_value(value: str) -> bool:
    return value in SAFE_PLACEHOLDERS


def _content_errors(text: str, rel: str) -> list[str]:
    errors: list[str] = []
    for match in ID_RE.finditer(text):
        value = match.group("value")
        # Source-level mapping/tuple declarations are not runtime payloads.
        if not match.group("quote") and value[:1] in "([{":
            continue
        if not _is_safe_value(value): errors.append(f"raw runtime identifier:{rel}:{match.group('key')}")
    if TOKEN_RE.search(text): errors.append("credential token:" + rel)
    # Check raw and JSON/YAML-escaped path encodings. A placeholder only exempts its exact match.
    for pattern in (LINUX_PATH_RE, WINDOWS_PATH_RE, ESCAPED_PATH_RE):
        for match in pattern.finditer(text):
            value = match.group(0)
            if not _is_safe_value(value): errors.append("private path:" + rel)
    try:
        decoded = json.loads(text)
        if isinstance(decoded, (dict, list)):
            encoded = json.dumps(decoded, ensure_ascii=False)
            if encoded != text:
                errors.extend(_content_errors(encoded, rel + ":json"))
    except Exception:
        pass
    return errors


def scan(root: pathlib.Path, manifest: dict | None = None) -> tuple[list[str], list[str]]:
    root = pathlib.Path(root); data = manifest if manifest is not None else _manifest(root)[0]
    files, errors = tracked(root)
    forbidden = [x.lower() for x in data.get("forbidden", []) if isinstance(x, str)]
    for rel in files:
        normalized_rel = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", rel).lower()
        path_tokens = set(re.findall(r"[a-z0-9]+", normalized_rel))
        # Normative forbidden path semantics: decorated names such as prompt_dump and my-transcript match.
        for item in forbidden:
            if item.lower() in path_tokens:
                errors.append("forbidden path:" + rel); break
        try:
            raw = (root / rel).read_bytes(); text = raw.decode("utf-8")
        except UnicodeDecodeError: errors.append("non-utf8:" + rel); continue
        errors.extend(_content_errors(text, rel))
    return files, errors


def main() -> int:
    ap = argparse.ArgumentParser(); ap.add_argument("--repo", default="."); args = ap.parse_args()
    root = pathlib.Path(args.repo).resolve(); data, errors = _manifest(root)
    files, scan_errors = scan(root, data); errors.extend(scan_errors)
    tracked_set = git_tracked(root); package_extra = sorted(set(files) - tracked_set) if tracked_set is not None else []
    for req in ("codex/AGENTS.md", "codex/BRIEF-TEMPLATES.md", "codex/hooks.json", "scripts/install-governance.py", "manifest.json"):
        if req not in files: errors.append("missing:" + req)
    declared = data.get("files") if isinstance(data, dict) else None
    if not isinstance(declared, dict) or any(not isinstance(k, str) or not isinstance(v, str) or not re.fullmatch(r"[0-9a-f]{64}", v) for k, v in declared.items()):
        errors.append("manifest files schema")
    expected = {f for f in files if f != "manifest.json"}
    if isinstance(declared, dict):
        if set(declared) != expected: errors.append("manifest path-set mismatch")
        for f in expected:
            if not allowed(f, data): errors.append("outside allowlist:" + f)
            elif declared.get(f) != sha(root / f): errors.append("hash mismatch:" + f)
    for req in ("codex/AGENTS.md", "codex/BRIEF-TEMPLATES.md"):
        if (root / req).exists() and (root / req).stat().st_size > 26624: errors.append("hard size limit")
    rel = root / "codex/V15_RELEASE.json"; mat = root / "codex/contracts/v14_preservation_matrix.json"
    if not rel.exists(): errors.append("missing:codex/V15_RELEASE.json")
    else:
        try:
            rd = json.loads(rel.read_text())
            if not isinstance(rd.get("version"), str) or not rd["version"].startswith("2026-07-31-v15"): errors.append("release identity")
            for x in rd.get("v14_sources", {}).values():
                if not all(k in x for k in ("sha256", "bytes", "lines")): errors.append("release baseline fields")
        except Exception: errors.append("release parse")
    if mat.exists():
        try:
            rows = json.loads(mat.read_text()).get("clauses", []); ids = [x.get("id") for x in rows]
            if len(ids) != len(set(ids)) or any(not i for i in ids) or len(rows) < 11: errors.append("matrix ids")
        except Exception: errors.append("matrix parse")
    else: errors.append("missing:matrix")
    out = {"repo": str(root), "files": len(files), "git_tracked": len(tracked_set) if tracked_set is not None else None,
           "package_extra": package_extra, "errors": errors, "status": "GREEN" if not errors else "RED"}
    print(json.dumps(out, sort_keys=True)); return 0 if not errors else 1


if __name__ == "__main__": raise SystemExit(main())

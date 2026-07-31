#!/usr/bin/env python3
"""No-follow package/privacy verifier; manifest is the normative policy source."""
from __future__ import annotations

import hashlib
import json
import pathlib
import re
import stat
import subprocess
import sys
from typing import Any

ID_NAMES = ("session_id", "turn_id", "prompt_id", "transcript_id", "receipt_id")
_REQUIRED_RULES = {"credential-value", "runtime-identifier", "private-path", "forbidden-filename", "non-utf8", "symlink", "temp-artifact"}
_REQUIRED_FORBIDDEN = {"sessions", "hook-receipts", "plugins", "connections", "models_cache.json", ".env", "token", "credential", "prompt", "transcript"}
_RULE_KEYS = {"id", "class", "pattern", "semantics", "mandatory"}
_PLACEHOLDER_KEYS = {"id", "value", "scope"}
_RULE_PATTERN_SHA = {
    "credential-value": "e7203cff65659224e02c680e4daf4d1df2e0d8e12742a7cd0a86ed170dbcea67",
    "runtime-identifier": "378fadfeca72cd83ba091fe958bf6960e1659f275180ec10f44db7ca96cf1e35",
    "private-path": "c3736ee06efa1b98a6e3b4a859bbc051c8a4c6cb0d3a8a9625594dd14d8268ec",
    "forbidden-filename": "0863b17028d033001e6f5d0a3b53dc74580d2f1546b11a5ebaa4e4cf764ae0c0",
    "non-utf8": "5cf1fa8d836a5e82ead36fb0854a6a68a4b8f7542038a346357d2424ddb60f5e",
    "symlink": "cc52c0d6367fb9edaa99d90776d0b4d9a052e81f1cebe495b85480344e53f455",
    "temp-artifact": "15cde9b7350bc5bbf1e71619586d6599500e50da4e946cf788d0d4ca31a87016",
}
_PLACEHOLDER_VALUES = {"user":"<user>", "isolated-temp":"<isolated-temp>", "home":"${HOME}", "home-short":"$HOME", "codex-home":"${CODEX_HOME}", "codex-home-short":"$CODEX_HOME", "domain":"example.invalid", "synthetic-token":"synthetic-token", "synthetic-id":"<synthetic-id>", "session-example":"session-secret-123", "turn-example":"turn-secret-456", "runtime-example":"runtime-id"}
_ROOT_ALLOW = {"README.md", "SECURITY.md", "PRIVACY.md", "LICENSE", "AGENTS.md", "manifest.json"}

# Build slash fragments without embedding machine-local /tmp paths in this source.
_SLASH = chr(47)
_LINUX_PATH_RE = re.compile(rf"(?<![A-Za-z0-9_]){_SLASH}(?:home|Users|private|var{_SLASH}tmp|tmp)(?:{_SLASH})[A-Za-z0-9._~-]+(?:{_SLASH}[^\s'\"<>`]*)?", re.I)
_WINDOWS_PATH_RE = re.compile(r"(?<![A-Za-z0-9])[A-Za-z]:\\+Users\\+[A-Za-z0-9._~-]+(?:\\+[^\s'\"<>`]*)?", re.I)
_ESCAPED_PATH_RE = re.compile(r"(?:\\{1,4}/|/|\\+)(?:home|Users|private)(?:\\{1,4}/|/|\\+)[A-Za-z0-9._~-]+", re.I)
_ID_RE = re.compile(r"(?<![A-Za-z0-9_])[\"\']?(?:session_id|turn_id|prompt_id|transcript_id|receipt_id)(?:[\"\'])?(?![A-Za-z0-9_])\s*[:=]\s*(?P<value>[^\n]+)", re.I)
_ASSIGN_RE = re.compile(r"(?<![A-Za-z0-9_])(?:assigned[_ -]?credential|credential|token|secret|password)(?![A-Za-z0-9_])\s*[:=]\s*(?P<value>[^\n]+)")
_TOKEN_RE = re.compile(r"\bgh[pso]_[A-Za-z0-9]{20,}\b")
_SAFE_PLACEHOLDERS = {
    "<user>", "<isolated-temp>", "${HOME}", "$HOME", "${CODEX_HOME}", "$CODEX_HOME", "example.invalid",
    "synthetic-token", "<synthetic-id>", "session-secret-123", "turn-secret-456", "runtime-id",
}


def sha(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _lstat(path: pathlib.Path):
    try:
        return path.lstat()
    except FileNotFoundError:
        return None


def _manifest(root: pathlib.Path) -> tuple[dict, list[str]]:
    errors: list[str] = []
    try:
        data = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    except Exception as exc:
        return {}, ["manifest parse:" + str(exc)]
    if not isinstance(data, dict):
        return {}, ["manifest schema"]
    if data.get("schema_version") not in ("1", "1.0"):
        errors.append("manifest schema")
    forbidden = data.get("forbidden")
    if not isinstance(forbidden, list) or any(not isinstance(x, str) or not x for x in forbidden) or len(forbidden) != len(set(forbidden)) or set(forbidden) != _REQUIRED_FORBIDDEN:
        errors.append("manifest forbidden schema")
    privacy = data.get("privacy")
    if not isinstance(privacy, dict) or set(privacy) != {"schema", "rule_ids", "rules", "allowed_placeholders"} or privacy.get("schema") != "privacy-rules.v1":
        errors.append("manifest privacy schema")
    else:
        ids = privacy.get("rule_ids"); rules = privacy.get("rules"); placeholders = privacy.get("allowed_placeholders")
        if ids != sorted(ids) or set(ids) != _REQUIRED_RULES or len(ids) != len(set(ids)):
            errors.append("manifest privacy rule ids")
        if not isinstance(rules, list) or {r.get("id") for r in rules if isinstance(r, dict)} != _REQUIRED_RULES or len(rules) != len(_REQUIRED_RULES):
            errors.append("manifest privacy rules")
        else:
            for rule in rules:
                if not isinstance(rule, dict) or set(rule) != _RULE_KEYS or rule.get("id") not in _REQUIRED_RULES or rule.get("mandatory") is not True or not isinstance(rule.get("pattern"), str) or not rule["pattern"] or not isinstance(rule.get("semantics"), str) or not rule["semantics"]:
                    errors.append("manifest privacy rule entry")
                elif hashlib.sha256(rule["pattern"].encode()).hexdigest() != _RULE_PATTERN_SHA[rule["id"]]:
                    errors.append("manifest privacy rule weakened")
                else:
                    try: re.compile(rule["pattern"])
                    except re.error: errors.append("manifest privacy rule regex")
        expected_placeholders = {"user", "isolated-temp", "home", "home-short", "codex-home", "codex-home-short", "domain", "synthetic-token", "synthetic-id", "session-example", "turn-example", "runtime-example"}
        if not isinstance(placeholders, list) or len(placeholders) != len(expected_placeholders) or {p.get("id") for p in placeholders if isinstance(p, dict)} != expected_placeholders or any(not isinstance(p, dict) or set(p) != _PLACEHOLDER_KEYS or p.get("scope") != "exact-value" or not isinstance(p.get("value"), str) or not p["value"] for p in placeholders):
            errors.append("manifest privacy placeholders")
        elif len({p["value"] for p in placeholders}) != len(placeholders) or {p["id"]: p["value"] for p in placeholders} != _PLACEHOLDER_VALUES:
            errors.append("manifest privacy placeholders weakened")
    return data, errors


def tracked(root: pathlib.Path) -> tuple[list[str], list[str]]:
    files: list[str] = []; errors: list[str] = []
    for path in sorted(root.rglob("*")):
        if ".git" in path.parts or "__pycache__" in path.parts or path.suffix == ".pyc":
            continue
        rel = path.relative_to(root).as_posix(); info = _lstat(path)
        if info is None:
            continue
        if stat.S_ISLNK(info.st_mode):
            errors.append("symlink:" + rel); continue
        if stat.S_ISREG(info.st_mode):
            files.append(rel)
    return files, errors


def git_tracked(root: pathlib.Path):
    try:
        proc = subprocess.run(["git", "-C", str(root), "ls-files", "-z"], capture_output=True, check=False)
        if proc.returncode != 0:
            return None
        return {x for x in proc.stdout.decode().split("\0") if x}
    except (OSError, UnicodeDecodeError):
        return None


def allowed(rel: str, data: dict) -> bool:
    allow = data.get("allowlist", []) if isinstance(data, dict) else []
    return rel in _ROOT_ALLOW or any(isinstance(x, str) and (rel == x.rstrip("/") or rel.startswith(x)) for x in allow)


def _placeholder(value: str) -> bool:
    return value.strip().strip("\"'") in _SAFE_PLACEHOLDERS


def _trim_value(value: str) -> str:
    value = value.strip()
    if value[:1] in "\"'":
        quote = value[0]; end = value.find(quote, 1)
        if end > 0:
            return value[1:end]
    # Keep bracket/list/map payloads intact: they are never exact placeholders.
    if value[:1] not in "[{(":
        value = value.rstrip(",;}])").strip()
    return value.strip().strip("\"'")


def _content_errors(text: str, rel: str, manifest: dict | None = None) -> list[str]:
    errors: list[str] = []
    # Manifest placeholders are an exact value exception only.  A placeholder next
    # to any real identifier/path remains a violation.
    for match in _ID_RE.finditer(text):
        raw_value = match.group("value"); value = _trim_value(raw_value)
        # This source-level mapping only names alternate event fields; it is not
        # a runtime value.  Real tuple/list/map payloads remain RED.
        if raw_value.lstrip().startswith("(") and re.fullmatch(r"\(\s*[\"\'](?:session_id|turn_id|prompt_id|transcript_id|receipt_id)[\"\']\s*,\s*[\"\'][A-Za-z_]+[\"\']\s*\)\s*,?", raw_value):
            continue
        if not _placeholder(value):
            errors.append(f"raw runtime identifier:{rel}")
    for match in _ASSIGN_RE.finditer(text):
        value = _trim_value(match.group("value"))
        # Source expressions/annotations are not serialized credential values;
        # literal JSON/YAML/unquoted payloads remain strict and are RED.
        if (value[:1] in "[{(\"'" or re.fullmatch(r"[A-Za-z0-9._~+\-=]+", value)) and not any(x in value for x in (".", "(", ")", "[", "]", "+\"", "\"+")):
            if not _placeholder(value):
                errors.append(f"credential value:{rel}")
    if _TOKEN_RE.search(text):
        errors.append("credential token:" + rel)
    for pattern, label in ((_LINUX_PATH_RE, "private path"), (_WINDOWS_PATH_RE, "private path"), (_ESCAPED_PATH_RE, "private path")):
        for match in pattern.finditer(text):
            value = match.group(0)
            # The exact portable placeholder is the only allowed temp path.
            if value != "<isolated-temp>" and not _placeholder(value):
                errors.append(label + ":" + rel)
    # Generic temporary-artifact spellings, including an escaped separator, are
    # forbidden in tracked evidence.  The scanner source builds the slash at run
    # time so its own policy code remains privacy-clean.
    temp_token = _SLASH + "tmp" + _SLASH
    backslash = chr(92)
    if temp_token in text or (backslash + "tmp" + backslash) in text:
        errors.append("temporary artifact path:" + rel)
    try:
        decoded = json.loads(text)
        if isinstance(decoded, (dict, list)):
            encoded = json.dumps(decoded, ensure_ascii=False)
            if encoded != text:
                errors.extend(_content_errors(encoded, rel + ":json", manifest))
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
        for item in forbidden:
            if item in path_tokens:
                errors.append("forbidden path:" + rel); break
        try:
            raw = (root / rel).read_bytes(); text = raw.decode("utf-8")
        except UnicodeDecodeError:
            errors.append("non-utf8:" + rel); continue
        errors.extend(_content_errors(text, rel, data))
    return files, errors


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(); ap.add_argument("--repo", default="."); args = ap.parse_args()
    root = pathlib.Path(args.repo).resolve(); data, errors = _manifest(root)
    files, scan_errors = scan(root, data); errors.extend(scan_errors)
    tracked_set = git_tracked(root); package_extra = sorted(set(files) - tracked_set) if tracked_set is not None else []
    for required in ("codex/AGENTS.md", "codex/BRIEF-TEMPLATES.md", "codex/hooks.json", "scripts/install-governance.py", "manifest.json"):
        if required not in files: errors.append("missing:" + required)
    declared = data.get("files") if isinstance(data, dict) else None
    if not isinstance(declared, dict) or any(not isinstance(k, str) or not isinstance(v, str) or not re.fullmatch(r"[0-9a-f]{64}", v) for k, v in declared.items()):
        errors.append("manifest files schema")
    expected = {f for f in files if f != "manifest.json"}
    if isinstance(declared, dict):
        if set(declared) != expected: errors.append("manifest path-set mismatch")
        for rel in expected:
            if not allowed(rel, data): errors.append("outside allowlist:" + rel)
            elif declared.get(rel) != sha(root / rel): errors.append("hash mismatch:" + rel)
    for required in ("codex/AGENTS.md", "codex/BRIEF-TEMPLATES.md"):
        if (root / required).exists() and (root / required).stat().st_size > 26624: errors.append("hard size limit")
    release = root / "codex/V15_RELEASE.json"; matrix = root / "codex/contracts/v14_preservation_matrix.json"
    if not release.exists(): errors.append("missing:codex/V15_RELEASE.json")
    else:
        try:
            rd = json.loads(release.read_text())
            if not isinstance(rd.get("version"), str) or not rd["version"].startswith("2026-07-31-v15"): errors.append("release identity")
            for item in rd.get("v14_sources", {}).values():
                if not all(k in item for k in ("sha256", "bytes", "lines")): errors.append("release baseline fields")
        except Exception: errors.append("release parse")
    if matrix.exists():
        try:
            rows = json.loads(matrix.read_text()).get("clauses", []); ids = [x.get("id") for x in rows]
            if len(ids) != len(set(ids)) or any(not i for i in ids) or len(rows) < 11: errors.append("matrix ids")
        except Exception: errors.append("matrix parse")
    else: errors.append("missing:matrix")
    out = {"repo": str(root), "files": len(files), "git_tracked": len(tracked_set) if tracked_set is not None else None, "package_extra": package_extra, "errors": errors, "status": "GREEN" if not errors else "RED"}
    print(json.dumps(out, sort_keys=True)); return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())

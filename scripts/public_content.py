"""Shared public-package path and content checks used by verifier and installer."""

from __future__ import annotations

import pathlib
import re


FORBIDDEN_PARTS = (
    "sessions",
    "hook-receipts",
    "plugins",
    "connections",
    "models_cache.json",
    ".env",
)
FORBIDDEN_RE = (
    re.compile(r"gh[pso]_[A-Za-z0-9]{12,}"),
    re.compile(r"(?:session|turn|task|thread)[_-]?id\s*[:=]\s*(?:/|parent/|child/|[0-9a-f]{8}-)", re.I),
    re.compile(r"/(?:home|Users|root|tmp)/[^\s`\"']+", re.I),
    re.compile(r"\b(?:qian|liang)\d{4,}\b|\b" + "mar" + "tin" + r"\b", re.I),
)


def scan_text(text: str) -> list[str]:
    """Return stable reason codes for content that must not ship publicly."""
    errors: list[str] = []
    for pattern in FORBIDDEN_RE:
        if pattern.search(text):
            errors.append(pattern.pattern)
    return errors


def scan_path(root: pathlib.Path, relative: str) -> list[str]:
    path = root / relative
    errors: list[str] = []
    lower = relative.lower()
    if any(part in lower for part in FORBIDDEN_PARTS):
        errors.append(f"forbidden path:{relative}")
    if not path.is_file() or path.is_symlink():
        return errors
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        errors.append(f"nonUTF8:{relative}")
        return errors
    errors.extend(f"forbidden content:{relative}:{reason}" for reason in scan_text(text))
    return errors

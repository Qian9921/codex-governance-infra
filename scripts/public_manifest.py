"""Strict metadata contract for the public package manifest."""

from __future__ import annotations

from typing import Any, Mapping


MANIFEST_KEYS = frozenset({"allowlist", "files", "forbidden", "package", "schema_version", "version"})
PACKAGE = "Codex Governance Infra"
SCHEMA_VERSION = "1"
VERSION = "21.1.0"
FORBIDDEN = (
    "sessions",
    "hook-receipts",
    "plugins",
    "connections",
    "models_cache.json",
    ".env",
    "token",
    "credential",
    "prompt",
    "transcript",
)


def validate_manifest_metadata(value: Any) -> list[str]:
    """Return fail-closed metadata errors without scanning intentional vocabulary."""
    if not isinstance(value, Mapping):
        return ["manifest must be an object"]
    errors: list[str] = []
    if set(value) != MANIFEST_KEYS:
        errors.append("manifest top-level keys mismatch")
    if value.get("package") != PACKAGE:
        errors.append("manifest package mismatch")
    if value.get("schema_version") != SCHEMA_VERSION:
        errors.append("manifest schema_version mismatch")
    if value.get("version") != VERSION:
        errors.append("manifest version mismatch")
    if value.get("forbidden") != list(FORBIDDEN):
        errors.append("manifest forbidden metadata mismatch")
    return errors

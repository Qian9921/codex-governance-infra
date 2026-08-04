#!/usr/bin/env python3
"""Small shared selector for adaptive versus strict hook enforcement."""

from __future__ import annotations

import os
from collections.abc import Mapping


ADAPTIVE = "adaptive"
STRICT = "strict"


def current_mode(environment: Mapping[str, str] | None = None) -> str:
    """Return a safe, deterministic mode; unknown values stay adaptive."""

    source = os.environ if environment is None else environment
    value = source.get("CODEX_GOVERNANCE_MODE", ADAPTIVE).strip().lower()
    return STRICT if value == STRICT else ADAPTIVE


def is_strict(environment: Mapping[str, str] | None = None) -> bool:
    return current_mode(environment) == STRICT

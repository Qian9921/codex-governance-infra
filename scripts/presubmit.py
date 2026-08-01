#!/usr/bin/env python3
"""Portable entrypoint for the V16 one-command presubmit."""
from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from codex.v16.presubmit import main

if __name__ == "__main__":
    raise SystemExit(main())

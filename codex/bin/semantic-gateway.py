#!/usr/bin/env python3
"""Portable launcher for the compiler-derived semantic gateway."""

from pathlib import Path
import sys

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))

from semantic_gateway.gateway import main  # noqa: E402

raise SystemExit(main())

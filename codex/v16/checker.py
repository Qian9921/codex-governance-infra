"""Structured stdlib unittest checker used by the V16 presubmit runner."""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
import unittest


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(); ap.add_argument("--start", default="tests/v16"); args = ap.parse_args(argv)
    loader = unittest.TestLoader()
    suite = loader.discover(args.start)
    result = unittest.TextTestRunner(stream=sys.stderr, verbosity=0).run(suite)
    skipped = len(getattr(result, "skipped", []))
    failed = len(result.failures) + len(result.errors)
    total = result.testsRun
    passed = total - failed - skipped
    payload = {"schema": "checker-result.v16", "total": total, "ran": passed + failed, "passed": passed, "failed": failed, "skipped": skipped, "xfail": 0, "unknown": total - (passed + failed) - skipped}
    print(json.dumps(payload, sort_keys=True))
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())

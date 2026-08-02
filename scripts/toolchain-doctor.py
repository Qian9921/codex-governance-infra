#!/usr/bin/env python3
"""Run the strict CodeGraph/Semble/rtk readiness gate."""

from __future__ import annotations

import argparse
import json
import pathlib
import sys


ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from codex.v16.tool_preflight import PreflightError, run_preflight  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only strict preflight for CodeGraph, Semble, and rtk. "
            "The semantic sentinel must describe EXPECTED_PATH without simply "
            "repeating its filename."
        )
    )
    parser.add_argument("--repo", default=".", help="target Git repository")
    parser.add_argument(
        "--semantic-query",
        required=True,
        help="repository-specific semantic sentinel query",
    )
    parser.add_argument(
        "--expected-path",
        required=True,
        help="repo-relative file that both CodeGraph and Semble must find",
    )
    parser.add_argument(
        "--config",
        help="Codex config.toml; defaults to $CODEX_HOME/config.toml or ~/.codex/config.toml",
    )
    parser.add_argument(
        "--timeout-sec",
        type=float,
        default=20.0,
        help="per-command timeout (default: 20)",
    )
    parser.add_argument(
        "--advisory",
        action="store_true",
        help="report failures as degraded instead of blocked",
    )
    args = parser.parse_args(argv)
    try:
        report = run_preflight(
            args.repo,
            semantic_query=args.semantic_query,
            expected_path=args.expected_path,
            config_path=args.config,
            strict=not args.advisory,
            timeout_sec=args.timeout_sec,
        )
    except (OSError, PreflightError) as exc:
        print(
            json.dumps(
                {
                    "schema": "tool-preflight-error.v16",
                    "status": "blocked",
                    "reason_code": type(exc).__name__,
                    "message": str(exc),
                },
                sort_keys=True,
            )
        )
        return 2
    print(json.dumps(report, sort_keys=True, separators=(",", ":")))
    return 0 if report["status"] == "ready" else 1


if __name__ == "__main__":
    raise SystemExit(main())

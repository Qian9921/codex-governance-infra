#!/usr/bin/env python3
"""Check and safely self-heal the mandatory V16 repository toolchain."""

from __future__ import annotations

import argparse
import json
import pathlib
import sys


PACKAGE_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))

from v16.tool_maintenance import ToolMaintenanceError, maintain_toolchain  # noqa: E402
from v16.tool_preflight import PreflightError  # noqa: E402


EXIT_BY_STATUS = {
    "ready": 0,
    "maintenance_required": 3,
    "external_action_required": 4,
    "blocked": 5,
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Run strict CodeGraph/Semble/rtk checks for one exact repository; "
            "initialize or synchronize only that repository's CodeGraph index "
            "at most once when a local repair is required."
        )
    )
    parser.add_argument("--repo", default=".", help="exact owning Git repository")
    parser.add_argument("--semantic-query", required=True)
    parser.add_argument("--expected-path", required=True)
    parser.add_argument("--config")
    parser.add_argument("--timeout-sec", type=float, default=30.0)
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="do not initialize or synchronize a repository index",
    )
    parser.add_argument(
        "--state-dir",
        help="private lock state; defaults to ~/.codex/tool-state",
    )
    args = parser.parse_args(argv)
    try:
        report = maintain_toolchain(
            args.repo,
            semantic_query=args.semantic_query,
            expected_path=args.expected_path,
            config_path=args.config,
            timeout_sec=args.timeout_sec,
            repair=not args.check_only,
            allow_repo_index_mutation=not args.check_only,
            state_dir=args.state_dir,
        )
    except (OSError, PreflightError, ToolMaintenanceError) as exc:
        print(json.dumps({
            "schema": "tool-maintenance-error.v16",
            "status": "blocked",
            "terminal_reason_code": type(exc).__name__,
            "message": str(exc),
        }, sort_keys=True))
        return 5
    print(json.dumps(report, sort_keys=True, separators=(",", ":")))
    return EXIT_BY_STATUS[report["status"]]


if __name__ == "__main__":
    raise SystemExit(main())

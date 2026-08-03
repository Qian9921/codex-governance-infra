#!/usr/bin/env python3
"""Record a privacy-safe Bash exit status for one expected V16 tool call."""

from __future__ import annotations

import argparse
import os
import pathlib
import sys

PACKAGE_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))

from v16.tool_runtime import ToolRuntimeError, record_tool_execution_status  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--task-id-sha256", required=True)
    parser.add_argument("--intake-id-sha256", required=True)
    parser.add_argument("--tool-use-id-sha256", required=True)
    parser.add_argument("--exit-code", required=True, type=int)
    args = parser.parse_args()
    try:
        record_tool_execution_status(
            task_id_sha256=args.task_id_sha256,
            intake_id_sha256=args.intake_id_sha256,
            tool_use_id_sha256=args.tool_use_id_sha256,
            exit_code=args.exit_code,
            state_dir=os.environ.get("CODEX_TOOL_STATE_DIR"),
        )
    except (OSError, ToolRuntimeError):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

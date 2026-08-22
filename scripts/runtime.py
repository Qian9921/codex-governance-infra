"""Select a supported Python interpreter for direct V23 script execution."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

MINIMUM_VERSION = (3, 11)


def _is_supported(executable: str) -> bool:
    """Return whether *executable* runs Python 3.11 or newer."""
    completed = subprocess.run(
        [executable, "-c", "import sys; print(f'{sys.version_info[0]}.{sys.version_info[1]}')"],
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode:
        return False
    try:
        major, minor = (int(part) for part in completed.stdout.strip().split(".", maxsplit=1))
    except ValueError:
        return False
    return (major, minor) >= MINIMUM_VERSION


def ensure_supported_python(script_path: str) -> None:
    """Re-exec a direct script with a supported local Python when possible.

    V23 requires ``tomllib``. This keeps the documented ``python3 scripts/...``
    path usable on machines whose default Python is older than 3.11, without
    changing the user's global Python selection.
    """
    if sys.version_info[:2] >= MINIMUM_VERSION:
        return

    candidates = [os.environ.get("CODEX_HARNESS_PYTHON", "")]
    candidates.extend(("python3.14", "python3.13", "python3.12", "python3.11"))
    seen: set[str] = set()
    for candidate in candidates:
        executable = candidate if os.path.isabs(candidate) else shutil.which(candidate)
        if not executable:
            continue
        resolved = str(Path(executable).resolve())
        if resolved in seen:
            continue
        seen.add(resolved)
        if _is_supported(resolved):
            os.execv(resolved, [resolved, script_path, *sys.argv[1:]])

    print(
        "error: Codex Harness Infra requires Python 3.11 or newer; set "
        "CODEX_HARNESS_PYTHON to a supported interpreter.",
        file=sys.stderr,
    )
    raise SystemExit(2)

"""Run the V23 required tool bootstrap for one submitted user prompt.

The script is installed outside repositories and invoked by the sole V23
``UserPromptSubmit`` hook. It performs one small real operation through each
required tool. It deliberately has no task database, daemon, Stop hook, or
background loop.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

CODEGRAPH_BEGIN = "# BEGIN CODEX-HARNESS-INFRA V23 CODEGRAPH"
CODEGRAPH_END = "# END CODEX-HARNESS-INFRA V23 CODEGRAPH"
REQUIRED_TOOLS = ("codegraph", "semble", "rtk")
# Keep the worst-case synchronous sequence below the 90-second native Hook
# timeout: Git discovery 4 + 4, CodeGraph 14 * 4, Semble 12, RTK 8 = 84.
GIT_DISCOVERY_TIMEOUT_SECONDS = 4
CODEGRAPH_TIMEOUT_SECONDS = 14
SEMBLE_TIMEOUT_SECONDS = 12
RTK_TIMEOUT_SECONDS = 8


@dataclass(frozen=True)
class ToolResult:
    """The concise result of one required tool operation."""

    name: str
    ok: bool
    detail: str


CommandRunner = Callable[[tuple[str, ...], Path | None, int], subprocess.CompletedProcess[str]]


def _system_runner(
    command: tuple[str, ...], cwd: Path | None, timeout_seconds: int
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        text=True,
        capture_output=True,
        check=False,
        timeout=timeout_seconds,
    )


def _compact(value: str, root: Path | None = None) -> str:
    """Return bounded hook context without leaking a full command transcript."""
    text = " ".join(value.strip().split())
    if root:
        text = text.replace(str(root), ".")
    return text[:220] if text else "completed"


def _run(
    command: tuple[str, ...],
    cwd: Path | None,
    runner: CommandRunner,
    timeout_seconds: int,
    root: Path | None = None,
) -> tuple[bool, str]:
    """Run one bounded command and retain only a short diagnostic."""
    try:
        completed = runner(command, cwd, timeout_seconds)
    except FileNotFoundError:
        return False, f"executable unavailable: {command[0]}"
    except subprocess.TimeoutExpired:
        return False, "timed out"
    except OSError as error:
        return False, str(error)
    output = completed.stdout if completed.returncode == 0 else completed.stderr or completed.stdout
    if completed.returncode:
        return False, _compact(output, root) or f"exit {completed.returncode}"
    return True, _compact(output, root)


def _resolve_executable(value: object) -> str | None:
    """Resolve a local configuration command without invoking a shell."""
    if not isinstance(value, str) or not value.strip():
        return None
    candidate = value.strip()
    path = Path(candidate).expanduser()
    if path.is_file():
        return str(path.resolve())
    return shutil.which(candidate)


def _git_root(cwd: Path, runner: CommandRunner) -> Path | None:
    ok, output = _run(
        ("git", "-C", str(cwd), "rev-parse", "--show-toplevel"),
        None,
        runner,
        GIT_DISCOVERY_TIMEOUT_SECONDS,
    )
    if not ok or not output:
        return None
    root = Path(output)
    return root.resolve() if root.is_dir() else None


def _atomic_write(path: Path, content: str) -> None:
    """Replace one local support file without partially writing it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def _ensure_codegraph_exclude(root: Path, runner: CommandRunner) -> tuple[bool, str]:
    """Ignore the V23-created CodeGraph cache using only a marked Git-local block."""
    ok, output = _run(
        ("git", "-C", str(root), "rev-parse", "--git-path", "info/exclude"),
        None,
        runner,
        GIT_DISCOVERY_TIMEOUT_SECONDS,
        root,
    )
    if not ok:
        return False, f"cannot locate Git exclude file: {output}"
    exclude = Path(output)
    if not exclude.is_absolute():
        exclude = root / exclude
    if exclude.is_symlink():
        return False, "Git exclude file is a symlink"
    existing = exclude.read_text(encoding="utf-8") if exclude.exists() else ""
    begins, ends = existing.count(CODEGRAPH_BEGIN), existing.count(CODEGRAPH_END)
    if begins != ends or begins > 1:
        return False, "V23 CodeGraph exclude marker is partial or duplicated"
    if begins == 1:
        start = existing.index(CODEGRAPH_BEGIN) + len(CODEGRAPH_BEGIN)
        finish = existing.index(CODEGRAPH_END)
        if existing[start:finish].strip() != ".codegraph/":
            return False, "V23 CodeGraph exclude block was modified"
        return True, "Git-local cache exclusion already present"
    rendered = existing.rstrip("\n")
    if rendered:
        rendered += "\n\n"
    rendered += f"{CODEGRAPH_BEGIN}\n.codegraph/\n{CODEGRAPH_END}\n"
    _atomic_write(exclude, rendered)
    return True, "added Git-local cache exclusion"


def _codegraph_result(
    executable: str | None,
    root: Path | None,
    runner: CommandRunner,
    initialize: bool,
) -> ToolResult:
    """Probe and actually query CodeGraph, creating only a V23-local cache."""
    if not executable:
        return ToolResult("CodeGraph", False, "not configured or unavailable")
    if root is None:
        ok, detail = _run((executable, "--version"), None, runner, CODEGRAPH_TIMEOUT_SECONDS)
        suffix = "non-Git directory; version probe" if ok else detail
        return ToolResult("CodeGraph", ok, suffix)
    if initialize:
        excluded, detail = _ensure_codegraph_exclude(root, runner)
        if not excluded:
            return ToolResult("CodeGraph", False, detail)
    status_ok, status = _run(
        (executable, "status", str(root)), root, runner, CODEGRAPH_TIMEOUT_SECONDS, root
    )
    # CodeGraph 1.5 reports an uninitialized project in stdout with exit 0.
    # Treat the declared state, not only the process code, as authoritative.
    if "not initialized" in status.casefold():
        status_ok = False
    if not status_ok and initialize:
        status_ok, status = _run(
            (executable, "init", str(root)), root, runner, CODEGRAPH_TIMEOUT_SECONDS, root
        )
        if not status_ok:
            return ToolResult("CodeGraph", False, f"init failed: {status}")
    if not status_ok:
        return ToolResult("CodeGraph", False, f"status failed: {status}")
    if initialize:
        synced, sync_detail = _run(
            (executable, "sync", str(root)), root, runner, CODEGRAPH_TIMEOUT_SECONDS, root
        )
        if not synced:
            return ToolResult("CodeGraph", False, f"sync failed: {sync_detail}")
    queried, detail = _run((executable, "files"), root, runner, CODEGRAPH_TIMEOUT_SECONDS, root)
    return ToolResult("CodeGraph", queried, detail if queried else f"files query failed: {detail}")


def _prompt_query(prompt: str) -> str:
    """Build a small source-search query from the user's words."""
    words = re.findall(r"[\w.-]{3,}", prompt, flags=re.UNICODE)
    return " ".join(words[:12]) or "task"


def _semble_result(
    executable: str | None, root: Path | None, cwd: Path, prompt: str, runner: CommandRunner
) -> ToolResult:
    """Search the current task scope through Semble."""
    if not executable:
        return ToolResult("Semble", False, "not configured or unavailable")
    target = root or cwd
    command = (
        executable,
        "search",
        "--content",
        "all",
        "--top-k",
        "1",
        "--max-snippet-lines",
        "4",
        _prompt_query(prompt),
        str(target),
    )
    ok, detail = _run(command, target, runner, SEMBLE_TIMEOUT_SECONDS, root)
    return ToolResult("Semble", ok, detail)


def _rtk_result(
    executable: str | None, root: Path | None, cwd: Path, runner: CommandRunner
) -> ToolResult:
    """Run one compact real workspace inspection through RTK."""
    if not executable:
        return ToolResult("RTK", False, "not configured or unavailable")
    command = (
        (executable, "git", "-C", str(root), "status", "--short", "--branch")
        if root
        else (executable, "ls", str(cwd))
    )
    ok, detail = _run(command, cwd, runner, RTK_TIMEOUT_SECONDS, root)
    return ToolResult("RTK", ok, detail)


def probe_tools(
    cwd: Path,
    prompt: str,
    tools: dict[str, object],
    *,
    runner: CommandRunner | None = None,
    initialize_codegraph: bool = True,
) -> list[ToolResult]:
    """Health-check and use each required tool once for the supplied task."""
    runner = runner or _system_runner
    cwd = cwd.resolve()
    root = _git_root(cwd, runner)
    codegraph = _codegraph_result(
        _resolve_executable(tools.get("codegraph")), root, runner, initialize_codegraph
    )
    semble = _semble_result(_resolve_executable(tools.get("semble")), root, cwd, prompt, runner)
    rtk = _rtk_result(_resolve_executable(tools.get("rtk")), root, cwd, runner)
    return [codegraph, semble, rtk]


def _load_local_config(path: Path) -> dict[str, object]:
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise ValueError(f"local configuration unavailable: {error}") from error


def _hook_context(results: list[ToolResult]) -> str:
    states = "; ".join(
        f"{result.name}=ready" if result.ok else f"{result.name}=FAILED ({result.detail})"
        for result in results
    )
    if all(result.ok for result in results):
        return f"V23 required tool bootstrap completed: {states}."
    return (
        f"V23 required tool bootstrap completed with failures: {states}. "
        "Repair the failed required tool before unrelated task work."
    )


def run_hook(cwd: Path, prompt: str, local_config: Path) -> dict[str, object]:
    """Return the documented UserPromptSubmit hook response."""
    try:
        config = _load_local_config(local_config)
        raw_tools = config.get("tools", {})
        tools = raw_tools if isinstance(raw_tools, dict) else {}
        results = probe_tools(cwd, prompt, tools)
    except (OSError, ValueError) as error:
        results = [ToolResult(name.title(), False, str(error)) for name in REQUIRED_TOOLS]
    return {
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": _hook_context(results),
        }
    }


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--local-config", type=Path, default=Path.home() / ".config/codex-harness/local.toml"
    )
    parser.add_argument("--cwd", type=Path)
    parser.add_argument("--prompt")
    args = parser.parse_args(argv)
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError:
        payload = {}
    cwd = args.cwd or Path(str(payload.get("cwd") or Path.cwd()))
    prompt = args.prompt if args.prompt is not None else str(payload.get("prompt") or "")
    print(json.dumps(run_hook(cwd, prompt, args.local_config), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

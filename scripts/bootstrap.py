#!/usr/bin/env python3
"""One-command, reversible V21 bootstrap for governance and semantic tools.

The bootstrap only mutates the two explicitly named managed roots. Host package
installation is an explicit opt-in route; it is never run implicitly (and never
needs credentials).
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import platform
import shutil
import subprocess
import sys
from typing import Any


ROOT = pathlib.Path(__file__).resolve().parents[1]
PNPM_VERSION = "9.15.4"


def _probe(name: str) -> dict[str, Any]:
    path = shutil.which(name)
    if not path:
        return {"name": name, "status": "NOT_READY", "reason": "MISSING"}
    try:
        result = subprocess.run([path, "--version"], capture_output=True, text=True,
                                timeout=20, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"name": name, "status": "PARTIAL", "reason": type(exc).__name__, "path": path}
    return {"name": name, "status": "READY" if result.returncode == 0 else "PARTIAL",
            "path": path, "version": (result.stdout or result.stderr).strip()[:200]}


def dependency_plan() -> dict[str, Any]:
    # Pyright is materialized and verified by install-semantic-tools. It is not
    # a host prerequisite and must not keep an otherwise complete bootstrap in
    # PARTIAL forever.
    host_tools = {name: _probe(name) for name in ("clangd", "node", "pnpm")}
    tools = dict(host_tools)
    tools["pyright"] = {"name": "pyright", "status": "MANAGED",
                         "owner": "install-semantic-tools.py"}
    system = platform.system().lower()
    commands = {
        "linux": [["sudo", "apt-get", "update"], ["sudo", "apt-get", "install", "-y", "clangd", "nodejs", "npm"],
                  ["corepack", "enable"], ["corepack", "prepare", f"pnpm@{PNPM_VERSION}", "--activate"]],
        "darwin": [["brew", "install", "llvm", "node"], ["corepack", "enable"],
                   ["corepack", "prepare", f"pnpm@{PNPM_VERSION}", "--activate"]],
        "windows": [["winget", "install", "--accept-source-agreements", "--accept-package-agreements", "LLVM.LLVM", "OpenJS.NodeJS"],
                    ["corepack", "enable"], ["corepack", "prepare", f"pnpm@{PNPM_VERSION}", "--activate"]],
    }
    missing = [name for name, value in host_tools.items() if value["status"] != "READY"]
    planned = commands.get(system, [])
    return {"tools": tools, "host_required": ["clangd", "node", "pnpm"],
            "managed": ["pyright"], "missing": missing, "system": system,
            "managed_route": planned or [["<host-package-manager>", "install", "clangd", "node", "pnpm"]],
            "install_opt_in": "--install-system-deps",
            "commands": planned,
            "truthful": True}


def _run(command: list[str]) -> dict[str, Any]:
    try:
        result = subprocess.run(command, capture_output=True, text=True, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        return {"command": command, "returncode": 127, "output": type(exc).__name__}
    output = (result.stdout or result.stderr).strip()[-1200:]
    return {"command": command, "returncode": result.returncode, "output": output}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Bootstrap governance plus pinned semantic tools")
    parser.add_argument("--codex-home", default=str(pathlib.Path.home() / ".codex"))
    parser.add_argument("--tools-home", default=str(pathlib.Path.home() / ".codex" / "semantic-tools"))
    parser.add_argument("--agents-home")
    parser.add_argument("--repo")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--install-system-deps", action="store_true",
                        help="explicitly execute the detected host package-manager route")
    parser.add_argument("--uninstall", action="store_true")
    parser.add_argument("--register", action="store_true", default=True)
    args = parser.parse_args(argv)
    codex_home = pathlib.Path(args.codex_home).expanduser().resolve()
    tools_home = pathlib.Path(args.tools_home).expanduser().resolve()
    agents_home = pathlib.Path(args.agents_home).expanduser().resolve() if args.agents_home else None
    repo = pathlib.Path(args.repo).expanduser().resolve() if args.repo else None
    governance = ROOT / "scripts/install-governance.py"
    semantic = ROOT / "scripts/install-semantic-tools.py"
    if args.uninstall:
        actions = []
        if tools_home.exists():
            actions.append(_run([sys.executable, str(semantic), "--tools-home", str(tools_home),
                                 "--codex-home", str(codex_home), "--uninstall"]))
        actions.append(_run([sys.executable, str(governance), "--source", str(ROOT),
                             "--codex-home", str(codex_home), *( ["--agents-home", str(agents_home)] if agents_home else []),
                             "--rollback"]))
        return_code = max((item["returncode"] for item in actions), default=0)
        print(json.dumps({"status": "UNINSTALLED" if return_code == 0 else "NOT_READY", "actions": actions}, sort_keys=True))
        return return_code
    common_governance = [sys.executable, str(governance), "--source", str(ROOT), "--codex-home", str(codex_home)]
    if agents_home:
        common_governance += ["--agents-home", str(agents_home)]
    common_semantic = [sys.executable, str(semantic), "--tools-home", str(tools_home),
                       "--codex-home", str(codex_home), "--install", "--register"]
    if repo:
        common_semantic += ["--repo", str(repo)]
    if args.dry_run:
        common_governance.append("--dry-run")
        common_semantic.append("--dry-run")
    dependencies = dependency_plan()
    dependency_actions = []
    if args.install_system_deps and dependencies["missing"] and not args.dry_run:
        # Stop at the first failed host action. Continuing after a package
        # manager failure can leave a misleadingly half-mutated environment.
        for command in dependencies["commands"]:
            action = _run(command)
            dependency_actions.append(action)
            if action["returncode"] != 0:
                break
        # Probe the host again: package-manager success is not tool readiness.
        dependencies = dependency_plan()
    actions = [_run(common_governance), _run(common_semantic)]
    failed_dependencies = [item for item in dependency_actions if item["returncode"] != 0]
    failed_actions = [item for item in actions if item["returncode"] != 0]
    if failed_actions or failed_dependencies or (args.install_system_deps and dependencies["missing"]):
        status = "NOT_READY"
    elif not dependencies["missing"]:
        status = "READY"
    else:
        status = "PARTIAL"
    print(json.dumps({"status": status, "version": "21.1.0", "actions": actions,
                      "dependency_actions": dependency_actions, "dependencies": dependencies,
                      "rollback": "bootstrap.py --uninstall ...",
                      "truthful": True}, sort_keys=True))
    return 0 if status in {"READY", "PARTIAL"} else 2


if __name__ == "__main__":
    raise SystemExit(main())

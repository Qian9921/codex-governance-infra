#!/usr/bin/env python3
"""Run a semantic backend inside bounded CPU/memory/time process scope."""

from __future__ import annotations

import argparse
import os
import shutil
import signal
import subprocess
import sys
from typing import Sequence


def build_limited_command(command: Sequence[str], *, profile: str, use_systemd: bool,
                          timeout_sec: int | float) -> list[str]:
    """Return a systemd-249-compatible transient service command when available."""
    if not use_systemd:
        return list(command)
    limits = {
        "cpp_resident": ("4", "4G", "180s"),
        "cpp_offline": ("4", "8G", "900s"),
        "python_resident": ("4", "2560M", "180s"),
    }
    cpus, memory, duration = limits[profile]
    inherited = []
    for name in ("PATH", "HOME", "GOFLAGS", "GOMAXPROCS"):
        value = os.environ.get(name)
        if value:
            inherited.extend(["--setenv", f"{name}={value}"])
    # systemd 249 rejects --scope together with --wait.  A transient service
    # owns the complete process tree, --pipe preserves the MCP stdio channel,
    # and --wait keeps this launcher synchronous for the gateway client.
    return ["systemd-run", "--user", "--quiet", "--wait", "--pipe", "--collect", "--same-dir",
            "-p", f"CPUQuota={int(cpus) * 100}%", "-p", f"MemoryMax={memory}",
            "-p", f"RuntimeMaxSec={duration}", *inherited, "--", *command]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", default="cpp_resident")
    parser.add_argument("--timeout-sec", type=float, default=180)
    parser.add_argument("--no-systemd", action="store_true")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    command = list(args.command)
    if command[:1] == ["--"]:
        command = command[1:]
    if not command:
        parser.error("backend command is required after --")
    systemd_available = sys.platform == "linux" and not args.no_systemd and bool(shutil.which("systemd-run"))
    # systemd-run already owns the process tree and timeout. For portable
    # hosts, start a fresh process group and kill the entire group on timeout.
    wrapped = build_limited_command(command, profile=args.profile,
                                    use_systemd=systemd_available, timeout_sec=args.timeout_sec)
    if systemd_available:
        return subprocess.call(wrapped)
    process = subprocess.Popen(command, stdin=sys.stdin, stdout=sys.stdout, stderr=sys.stderr,
                               start_new_session=True)
    try:
        return process.wait(timeout=args.timeout_sec)
    except subprocess.TimeoutExpired:
        # Bound both graceful shutdown and an uncooperative descendant tree.
        try:
            os.killpg(process.pid, signal.SIGTERM)
            return process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except OSError:
                pass
            return process.wait()
    finally:
        if process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except OSError:
                pass


if __name__ == "__main__":
    raise SystemExit(main())

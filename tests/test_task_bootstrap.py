from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from scripts.task_bootstrap import CODEGRAPH_BEGIN, ToolResult, probe_tools


class FakeRunner:
    """Return deterministic successful tool output while preserving every call."""

    def __init__(self, root: Path, *, timeout_codegraph: bool = False) -> None:
        self.root = root
        self.timeout_codegraph = timeout_codegraph
        self.calls: list[tuple[tuple[str, ...], Path | None]] = []
        self.timeouts: list[int] = []

    def __call__(
        self, command: tuple[str, ...], cwd: Path | None, _timeout: int
    ) -> subprocess.CompletedProcess[str]:
        self.calls.append((command, cwd))
        self.timeouts.append(_timeout)
        if command[:4] == ("git", "-C", str(self.root), "rev-parse"):
            if command[-1] == "--show-toplevel":
                return subprocess.CompletedProcess(command, 0, f"{self.root}\n", "")
            return subprocess.CompletedProcess(command, 0, ".git/info/exclude\n", "")
        if self.timeout_codegraph and command[0] == str(self.root / "codegraph"):
            raise subprocess.TimeoutExpired(command, _timeout)
        if command[1] == "status":
            return subprocess.CompletedProcess(command, 0, "Not initialized\n", "")
        return subprocess.CompletedProcess(command, 0, "ok\n", "")


class TaskBootstrapTests(unittest.TestCase):
    def test_bootstrap_uses_all_tools_and_initializes_only_git_local_cache(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            (root / ".git/info").mkdir(parents=True)
            tools = {}
            for name in ("codegraph", "semble", "rtk"):
                executable = root / name
                executable.write_text("", encoding="utf-8")
                tools[name] = str(executable)
            runner = FakeRunner(root)

            results = probe_tools(root, "Inspect the delivery adapter.", tools, runner=runner)

            self.assertEqual([result.name for result in results], ["CodeGraph", "Semble", "RTK"])
            self.assertTrue(all(result.ok for result in results))
            commands = [command for command, _ in runner.calls]
            self.assertTrue(
                any(
                    command[0] == tools["codegraph"] and command[1] == "init"
                    for command in commands
                )
            )
            self.assertTrue(
                any(
                    command[0] == tools["codegraph"] and command[1] == "files"
                    for command in commands
                )
            )
            self.assertTrue(
                any(
                    command[0] == tools["semble"] and command[1] == "search" for command in commands
                )
            )
            self.assertTrue(
                any(command[0] == tools["rtk"] and command[1] == "git" for command in commands)
            )
            exclude = (root / ".git/info/exclude").read_text(encoding="utf-8")
            self.assertIn(CODEGRAPH_BEGIN, exclude)
            self.assertIn(".codegraph/", exclude)

    def test_probe_reports_missing_tool_without_skipping_other_required_tools(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            (root / ".git/info").mkdir(parents=True)
            executable = root / "rtk"
            executable.write_text("", encoding="utf-8")
            runner = FakeRunner(root)

            results = probe_tools(root, "Check tooling.", {"rtk": str(executable)}, runner=runner)

            self.assertEqual(
                results,
                [
                    ToolResult("CodeGraph", False, "not configured or unavailable"),
                    ToolResult("Semble", False, "not configured or unavailable"),
                    ToolResult("RTK", True, "ok"),
                ],
            )

    def test_probe_rejects_a_modified_v23_codegraph_exclude_block(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            exclude = root / ".git/info/exclude"
            exclude.parent.mkdir(parents=True)
            exclude.write_text(
                f"{CODEGRAPH_BEGIN}\nuser-owned-pattern/\n# END CODEX-HARNESS-INFRA V23 CODEGRAPH\n",
                encoding="utf-8",
            )
            executable = root / "codegraph"
            executable.write_text("", encoding="utf-8")
            runner = FakeRunner(root)

            results = probe_tools(
                root, "Check CodeGraph.", {"codegraph": str(executable)}, runner=runner
            )

            self.assertFalse(results[0].ok)
            self.assertIn("modified", results[0].detail)
            self.assertFalse(any(call[0][0] == str(executable) for call in runner.calls))

    def test_codegraph_timeout_does_not_skip_semble_or_rtk(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            (root / ".git/info").mkdir(parents=True)
            tools = {}
            for name in ("codegraph", "semble", "rtk"):
                executable = root / name
                executable.write_text("", encoding="utf-8")
                tools[name] = str(executable)
            runner = FakeRunner(root, timeout_codegraph=True)

            results = probe_tools(root, "Check bounded tool timeouts.", tools, runner=runner)

            self.assertFalse(results[0].ok)
            self.assertTrue(results[1].ok)
            self.assertTrue(results[2].ok)
            commands = [command for command, _ in runner.calls]
            self.assertTrue(any(command[0] == tools["semble"] for command in commands))
            self.assertTrue(any(command[0] == tools["rtk"] for command in commands))
            self.assertLessEqual(max(runner.timeouts), 14)


if __name__ == "__main__":
    unittest.main()

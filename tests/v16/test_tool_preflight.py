import copy
import json
import pathlib
import subprocess
import tempfile
import unittest

from codex.v16.tool_preflight import (
    PreflightError,
    run_preflight,
    validate_preflight,
)


HEAD = "a" * 40


class FakeRunner:
    def __init__(self, repo):
        self.repo = pathlib.Path(repo)
        self.rtk_false_green = False
        self.stale = False
        self.wrong_semble_path = False

    def __call__(self, argv, cwd, timeout):
        del cwd, timeout
        argv = list(argv)
        command = pathlib.Path(argv[0]).name
        if command == "git" and argv[1:3] == ["rev-parse", "HEAD"]:
            return subprocess.CompletedProcess(argv, 0, HEAD + "\n", "")
        if command == "git" and argv[1:3] == ["status", "--porcelain=v1"]:
            return subprocess.CompletedProcess(argv, 0, "", "")
        if command == "codegraph" and argv[1] == "version":
            return subprocess.CompletedProcess(argv, 0, "1.5.0\n", "")
        if command == "codegraph" and argv[1:3] == ["status", "--json"]:
            payload = {
                "initialized": True,
                "projectPath": str(self.repo),
                "pendingChanges": {
                    "added": 1 if self.stale else 0,
                    "modified": 0,
                    "removed": 0,
                },
                "worktreeMismatch": None,
                "index": {
                    "state": "complete",
                    "reindexRecommended": False,
                },
            }
            return subprocess.CompletedProcess(argv, 0, json.dumps(payload), "")
        if command == "codegraph" and argv[1] == "files":
            return subprocess.CompletedProcess(
                argv,
                0,
                json.dumps([{"path": "src/router.py"}]),
                "",
            )
        if command == "codegraph" and argv[1] == "query":
            return subprocess.CompletedProcess(
                argv,
                0,
                json.dumps(
                    [{"node": {"filePath": "src/router.py"}}]
                ),
                "",
            )
        if command == "semble" and argv[1:] == ["--help"]:
            return subprocess.CompletedProcess(argv, 0, "usage: semble search\n", "")
        if command == "semble" and argv[1] == "search":
            path = "src/other.py" if self.wrong_semble_path else "src/router.py"
            return subprocess.CompletedProcess(
                argv,
                0,
                json.dumps({"results": [{"file_path": path}]}),
                "",
            )
        if command == "rtk" and argv[1:] == ["--version"]:
            return subprocess.CompletedProcess(argv, 0, "rtk 0.44.1\n", "")
        if command == "rtk" and argv[1:4] == ["git", "rev-parse", "HEAD"]:
            return subprocess.CompletedProcess(argv, 0, HEAD + "\n", "")
        if command == "rtk" and any(
            "__codex_toolchain_missing__" in item for item in argv
        ):
            return subprocess.CompletedProcess(
                argv, 0 if self.rtk_false_green else 128, "", "missing"
            )
        if command == "rtk" and argv[1:3] == ["git", "status"]:
            return subprocess.CompletedProcess(argv, 0, "", "")
        raise AssertionError(f"unexpected command: {argv}")


class ToolPreflightTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="tool-preflight-")
        self.root = pathlib.Path(self.temp.name)
        (self.root / "src").mkdir()
        (self.root / "src" / "router.py").write_text(
            "def route_tool():\n    return 'codegraph'\n",
            encoding="utf-8",
        )
        (self.root / "src" / "other.py").write_text("pass\n", encoding="utf-8")
        self.config = self.root / "config.toml"
        self.config.write_text(
            '[mcp_servers.codegraph]\n'
            'command = "codegraph"\n'
            'args = ["serve", "--mcp"]\n\n'
            '[mcp_servers.semble]\n'
            'command = "uvx"\n'
            'args = ["--from", "semble[mcp]", "semble"]\n',
            encoding="utf-8",
        )
        self.runner = FakeRunner(self.root)
        self.which = lambda tool: f"/tools/{tool}"

    def tearDown(self):
        self.temp.cleanup()

    def report(self, **kwargs):
        return run_preflight(
            self.root,
            semantic_query="deterministic inspection intent router",
            expected_path="src/router.py",
            config_path=self.config,
            runner=self.runner,
            which=self.which,
            **kwargs,
        )

    def test_strict_ready_report_has_known_three_tool_denominator(self):
        report = self.report()
        self.assertEqual(report["status"], "ready")
        self.assertEqual(report["denominator"], 3)
        self.assertEqual(
            report["counts"],
            {
                "total": 3,
                "ran": 3,
                "passed": 3,
                "failed": 0,
                "skipped": 0,
                "xfail": 0,
                "unknown": 0,
            },
        )
        self.assertEqual(
            [tool["tool"] for tool in report["tools"]],
            ["codegraph", "semble", "rtk"],
        )
        self.assertEqual(report["mutations"], [])
        self.assertEqual(validate_preflight(report), report)

    def test_stale_codegraph_blocks_strict_preflight(self):
        self.runner.stale = True
        report = self.report()
        self.assertEqual(report["status"], "blocked")
        codegraph = report["tools"][0]
        self.assertEqual(codegraph["status"], "fail")
        self.assertIn(
            "CODEGRAPH_STALE",
            [check["reason_code"] for check in codegraph["checks"]],
        )

    def test_semble_must_find_expected_current_path(self):
        self.runner.wrong_semble_path = True
        report = self.report()
        self.assertEqual(report["status"], "blocked")
        self.assertEqual(report["tools"][1]["reason_code"], "SEMBLE_SENTINEL_MISMATCH")

    def test_rtk_must_preserve_nonzero_exit_status(self):
        self.runner.rtk_false_green = True
        report = self.report()
        self.assertEqual(report["status"], "blocked")
        self.assertIn(
            "RTK_FALSE_GREEN",
            [check["reason_code"] for check in report["tools"][2]["checks"]],
        )

    def test_missing_mcp_config_blocks_both_indexed_tools(self):
        self.config.write_text("", encoding="utf-8")
        report = self.report()
        self.assertEqual(report["status"], "blocked")
        self.assertEqual(report["counts"]["failed"], 2)

    def test_advisory_failure_is_degraded_not_ready(self):
        self.runner.stale = True
        report = self.report(strict=False)
        self.assertEqual(report["status"], "degraded")

    def test_validator_rejects_forged_arithmetic_and_absolute_path_field(self):
        report = self.report()
        forged = copy.deepcopy(report)
        forged["counts"]["passed"] = 2
        with self.assertRaises(PreflightError):
            validate_preflight(forged)
        forged = copy.deepcopy(report)
        forged["repo_identity"]["root"] = str(self.root)
        with self.assertRaises(PreflightError):
            validate_preflight(forged)


if __name__ == "__main__":
    unittest.main()

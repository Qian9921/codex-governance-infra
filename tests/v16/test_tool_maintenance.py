import hashlib
import json
import pathlib
import subprocess
import tempfile
import unittest

from codex.v16.tool_maintenance import ToolMaintenanceError, maintain_toolchain
from codex.v16.tool_preflight import validate_preflight


SHA = "a" * 64
HEAD = "b" * 40


def preflight(*, failed_tool=None, reason=None):
    tools = []
    for name in ("codegraph", "semble", "rtk"):
        failed = name == failed_tool
        tool_reason = reason if failed else f"{name.upper()}_READY"
        tools.append({
            "tool": name,
            "status": "fail" if failed else "pass",
            "reason_code": tool_reason,
            "version": "test-1",
            "checks": [{
                "name": "sentinel",
                "status": "fail" if failed else "pass",
                "reason_code": tool_reason,
            }],
            "evidence_sha256": hashlib.sha256(name.encode()).hexdigest(),
        })
    failed = 1 if failed_tool else 0
    report = {
        "schema": "tool-preflight.v16",
        "status": "blocked" if failed else "ready",
        "strict": True,
        "repo_identity": {
            "root_sha256": SHA,
            "head_sha": HEAD,
            "dirty": False,
            "worktree_sha256": SHA,
        },
        "config_identity": {
            "path_sha256": SHA,
            "content_sha256": SHA,
            "present": True,
        },
        "tools": tools,
        "counts": {
            "total": 3,
            "ran": 3,
            "passed": 3 - failed,
            "failed": failed,
            "skipped": 0,
            "xfail": 0,
            "unknown": 0,
        },
        "denominator": 3,
        "denominator_known": True,
        "cache": {"key_sha256": SHA, "invalidated_by": ["test"]},
        "mutations": [],
    }
    return validate_preflight(report)


class SequencePreflight:
    def __init__(self, *reports):
        self.reports = list(reports)
        self.calls = 0

    def __call__(self, *_args, **_kwargs):
        index = min(self.calls, len(self.reports) - 1)
        self.calls += 1
        return self.reports[index]


class CommandRunner:
    def __init__(self, *, initialized=True, repair_returncode=0):
        self.initialized = initialized
        self.repair_returncode = repair_returncode
        self.calls = []

    def __call__(self, argv, _cwd, _timeout):
        argv = tuple(argv)
        self.calls.append(argv)
        if argv[1:3] == ("status", "--json"):
            return subprocess.CompletedProcess(
                argv, 0, json.dumps({"initialized": self.initialized}), ""
            )
        if argv[1] in {"init", "sync"}:
            return subprocess.CompletedProcess(
                argv, self.repair_returncode, "repair", "failure" if self.repair_returncode else ""
            )
        raise AssertionError(argv)


class ToolMaintenanceTests(unittest.TestCase):
    def run_case(self, preflights, runner=None, **kwargs):
        with tempfile.TemporaryDirectory() as repo, tempfile.TemporaryDirectory() as state:
            return maintain_toolchain(
                repo,
                semantic_query="semantic sentinel",
                expected_path="src/router.py",
                state_dir=state,
                preflight_runner=SequencePreflight(*preflights),
                command_runner=runner or CommandRunner(),
                **kwargs,
            )

    def test_ready_check_does_not_mutate(self):
        runner = CommandRunner()
        report = self.run_case([preflight()], runner)
        self.assertEqual(report["status"], "ready")
        self.assertEqual(report["terminal_reason_code"], "TOOLS_READY")
        self.assertEqual(report["repair_attempts"], 0)
        self.assertEqual(runner.calls, [])

    def test_stale_index_syncs_once_and_rechecks(self):
        runner = CommandRunner(initialized=True)
        report = self.run_case([
            preflight(failed_tool="codegraph", reason="CODEGRAPH_STALE"),
            preflight(),
        ], runner)
        self.assertEqual(report["status"], "ready")
        self.assertEqual(report["terminal_reason_code"], "REPAIRED_AND_READY")
        self.assertEqual(report["repair_attempts"], 1)
        self.assertEqual(report["mutations"], ["sync"])
        self.assertEqual(runner.calls[1][1], "sync")

    def test_uninitialized_index_uses_init_not_parent_index(self):
        runner = CommandRunner(initialized=False)
        with tempfile.TemporaryDirectory() as repo, tempfile.TemporaryDirectory() as state:
            root = pathlib.Path(repo).resolve()
            report = maintain_toolchain(
                root,
                semantic_query="semantic sentinel",
                expected_path="src/router.py",
                state_dir=state,
                preflight_runner=SequencePreflight(
                    preflight(failed_tool="codegraph", reason="CODEGRAPH_INDEX_INVALID"),
                    preflight(),
                ),
                command_runner=runner,
            )
            self.assertEqual(report["mutations"], ["init"])
            self.assertEqual(pathlib.Path(runner.calls[1][2]).resolve(), root)

    def test_repair_disabled_returns_owner_specific_maintenance_state(self):
        report = self.run_case(
            [preflight(failed_tool="codegraph", reason="CODEGRAPH_STALE")],
            repair=False,
        )
        self.assertEqual(report["status"], "maintenance_required")
        self.assertEqual(report["terminal_reason_code"], "REPO_INDEX_REPAIR_NOT_AUTHORIZED")
        self.assertEqual(report["repair_owner_role"], "assigned_execution_agent:tool_maintainer")

    def test_same_failure_after_one_repair_opens_circuit(self):
        runner = CommandRunner()
        stale = preflight(failed_tool="codegraph", reason="CODEGRAPH_STALE")
        report = self.run_case([stale, stale], runner)
        self.assertEqual(report["status"], "maintenance_required")
        self.assertEqual(report["terminal_reason_code"], "AUTO_REPAIR_NO_PROGRESS")
        self.assertEqual(report["repair_attempts"], 1)
        self.assertEqual(len([call for call in runner.calls if call[1] in {"init", "sync"}]), 1)

    def test_semble_failure_never_clears_global_cache(self):
        runner = CommandRunner()
        report = self.run_case(
            [preflight(failed_tool="semble", reason="SEMBLE_SENTINEL_MISMATCH")],
            runner,
        )
        self.assertEqual(report["status"], "external_action_required")
        self.assertEqual(report["repair_attempts"], 0)
        self.assertEqual(runner.calls, [])

    def test_failed_repair_is_terminal_after_one_attempt(self):
        runner = CommandRunner(repair_returncode=7)
        report = self.run_case(
            [preflight(failed_tool="codegraph", reason="CODEGRAPH_STALE")],
            runner,
        )
        self.assertEqual(report["status"], "maintenance_required")
        self.assertEqual(report["terminal_reason_code"], "AUTO_REPAIR_COMMAND_FAILED")
        self.assertEqual(report["repair_attempts"], 1)

    def test_same_failure_fingerprint_is_not_retried_across_invocations(self):
        stale = preflight(failed_tool="codegraph", reason="CODEGRAPH_STALE")
        with tempfile.TemporaryDirectory() as repo, tempfile.TemporaryDirectory() as state:
            first_runner = CommandRunner()
            first = maintain_toolchain(
                repo,
                semantic_query="semantic sentinel",
                expected_path="src/router.py",
                state_dir=state,
                preflight_runner=SequencePreflight(stale, stale),
                command_runner=first_runner,
            )
            second_runner = CommandRunner()
            second = maintain_toolchain(
                repo,
                semantic_query="semantic sentinel",
                expected_path="src/router.py",
                state_dir=state,
                preflight_runner=SequencePreflight(stale),
                command_runner=second_runner,
            )
        self.assertEqual(first["circuit_state"], "open")
        self.assertEqual(second["terminal_reason_code"], "AUTO_REPAIR_CIRCUIT_OPEN")
        self.assertEqual(second["repair_attempts"], 0)
        self.assertEqual(second_runner.calls, [])

    def test_corrupt_persistent_circuit_fails_closed_without_repair(self):
        stale = preflight(failed_tool="codegraph", reason="CODEGRAPH_STALE")
        with tempfile.TemporaryDirectory() as repo, tempfile.TemporaryDirectory() as state:
            root = pathlib.Path(repo).resolve()
            circuit = pathlib.Path(state) / (
                hashlib.sha256(str(root).encode()).hexdigest() + ".circuit.json"
            )
            circuit.write_text("not-json", encoding="utf-8")
            circuit.chmod(0o600)
            runner = CommandRunner()
            with self.assertRaises(ToolMaintenanceError):
                maintain_toolchain(
                    root,
                    semantic_query="semantic sentinel",
                    expected_path="src/router.py",
                    state_dir=state,
                    preflight_runner=SequencePreflight(stale),
                    command_runner=runner,
                )
        self.assertEqual(runner.calls, [])


if __name__ == "__main__":
    unittest.main()

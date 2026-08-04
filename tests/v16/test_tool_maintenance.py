import contextlib
import hashlib
import importlib.util
import io
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from codex.v16.tool_maintenance import ToolMaintenanceError, maintain_toolchain
from codex.v16.tool_preflight import validate_preflight
from codex.v16.tool_recovery import HISTORY_LIMIT, recover_toolchain


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


def preflight_with_codegraph_version(version, *, failed_tool=None, reason=None):
    report = preflight(failed_tool=failed_tool, reason=reason)
    for tool in report["tools"]:
        if tool["tool"] == "codegraph":
            tool["version"] = version
    return report


def preflight_with_tool_failures(**reasons):
    report = preflight()
    failures = 0
    for tool in report["tools"]:
        reason = reasons.get(tool["tool"])
        if reason is None:
            continue
        failures += 1
        tool["status"] = "fail"
        tool["reason_code"] = reason
        tool["checks"][0]["status"] = "fail"
        tool["checks"][0]["reason_code"] = reason
    report["status"] = "blocked" if failures else "ready"
    report["counts"]["passed"] = 3 - failures
    report["counts"]["failed"] = failures
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
        if argv[1] in {"index", "init", "sync"}:
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

    def test_invalid_file_inventory_is_repo_local_auto_repair(self):
        runner = CommandRunner(initialized=True)
        report = self.run_case([
            preflight(failed_tool="codegraph", reason="CODEGRAPH_FILES_INVALID"),
            preflight(),
        ], runner)
        self.assertEqual(report["status"], "ready")
        self.assertEqual(report["terminal_reason_code"], "REPAIRED_AND_READY")
        self.assertEqual(report["repair_attempts"], 1)

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


class ToolRecoveryTests(unittest.TestCase):
    def run_case(self, preflights, runner=None, **kwargs):
        with tempfile.TemporaryDirectory() as repo, tempfile.TemporaryDirectory() as state:
            return recover_toolchain(
                repo,
                semantic_query="semantic sentinel",
                expected_path="src/router.py",
                state_dir=state,
                preflight_runner=SequencePreflight(*preflights),
                command_runner=runner or CommandRunner(),
                **kwargs,
            )

    def test_recovery_continues_from_sync_to_index_until_health_is_ready(self):
        runner = CommandRunner(initialized=True)
        stale = preflight(failed_tool="codegraph", reason="CODEGRAPH_STALE")
        report = self.run_case([stale, stale, preflight()], runner)
        self.assertEqual(report["status"], "ready")
        self.assertEqual(report["terminal_reason_code"], "RECOVERED_AND_READY")
        self.assertEqual(report["strategy_attempts"], 2)
        self.assertEqual(report["mutations"], ["sync", "index"])
        self.assertEqual(
            [strategy["strategy_id"] for strategy in report["strategies"]],
            ["codegraph.sync", "codegraph.backup_index_rebuild"],
        )
        self.assertIsNotNone(report["strategies"][1]["lineage_parent_sha256"])
        self.assertIsNone(report["strategies"][1]["backup_evidence_sha256"])

    def test_index_rebuild_backs_up_existing_local_index_privately(self):
        runner = CommandRunner(initialized=True)
        stale = preflight(failed_tool="codegraph", reason="CODEGRAPH_INDEX_INVALID")
        with tempfile.TemporaryDirectory() as repo, tempfile.TemporaryDirectory() as state:
            index = pathlib.Path(repo) / ".codegraph"
            index.mkdir()
            (index / "graph.db").write_text("generated index", encoding="utf-8")
            report = recover_toolchain(
                repo,
                semantic_query="semantic sentinel",
                expected_path="src/router.py",
                state_dir=state,
                preflight_runner=SequencePreflight(stale, stale, preflight()),
                command_runner=runner,
            )
            backups = list(pathlib.Path(state).glob("*.backups/*.codegraph"))
            backup_text = (backups[0] / "graph.db").read_text(encoding="utf-8")
        self.assertEqual(report["status"], "ready")
        self.assertIsNotNone(report["strategies"][1]["backup_evidence_sha256"])
        self.assertEqual(len(backups), 1)
        self.assertEqual(backup_text, "generated index")

    def test_no_progress_strategy_is_not_repeated_across_invocations(self):
        stale = preflight(failed_tool="codegraph", reason="CODEGRAPH_STALE")
        with tempfile.TemporaryDirectory() as repo, tempfile.TemporaryDirectory() as state:
            first_runner = CommandRunner()
            first = recover_toolchain(
                repo,
                semantic_query="semantic sentinel",
                expected_path="src/router.py",
                state_dir=state,
                preflight_runner=SequencePreflight(stale, stale, stale),
                command_runner=first_runner,
            )
            second_runner = CommandRunner()
            second = recover_toolchain(
                repo,
                semantic_query="semantic sentinel",
                expected_path="src/router.py",
                state_dir=state,
                preflight_runner=SequencePreflight(stale),
                command_runner=second_runner,
            )
            second_calls = list(second_runner.calls)
        self.assertEqual(first["status"], "recovering")
        self.assertEqual(first["strategy_attempts"], 2)
        self.assertEqual(second["status"], "recovering")
        self.assertEqual(second["terminal_reason_code"], "NO_SAFE_UNTRIED_RECOVERY_STRATEGY")
        self.assertEqual(second["strategy_attempts"], 0)
        self.assertEqual(second["continuation_owner"], "machine")
        self.assertEqual(second["recheck_after_sec"], 300)
        self.assertEqual(second_calls, [("codegraph", "status", "--json", repo)])

    def test_non_mutating_mode_defers_repair_to_machine_continuation(self):
        report = self.run_case(
            [preflight(failed_tool="codegraph", reason="CODEGRAPH_STALE")],
            repair=False,
        )
        self.assertEqual(report["status"], "recovering")
        self.assertEqual(report["terminal_reason_code"], "NON_MUTATING_CHECK_COMPLETE")
        self.assertEqual(report["continuation_owner"], "machine")
        self.assertEqual(report["recheck_after_sec"], 300)

    def test_missing_tool_is_machine_owned_degradation_not_external_wait(self):
        report = self.run_case(
            [preflight(failed_tool="semble", reason="SEMBLE_NOT_FOUND")],
        )
        self.assertEqual(report["status"], "recovering")
        self.assertEqual(report["terminal_reason_code"], "MACHINE_OWNED_DIAGNOSTIC_RECHECK")
        self.assertEqual(report["continuation_owner"], "machine")
        self.assertEqual(report["strategy_attempts"], 0)

    def test_failed_index_rebuild_restores_the_private_backup(self):
        stale = preflight(failed_tool="codegraph", reason="CODEGRAPH_INDEX_INVALID")
        runner = CommandRunner(initialized=True, repair_returncode=7)
        with tempfile.TemporaryDirectory() as repo, tempfile.TemporaryDirectory() as state:
            index = pathlib.Path(repo) / ".codegraph"
            index.mkdir()
            (index / "graph.db").write_text("previous index", encoding="utf-8")
            report = recover_toolchain(
                repo,
                semantic_query="semantic sentinel",
                expected_path="src/router.py",
                state_dir=state,
                preflight_runner=SequencePreflight(stale, stale, stale, stale),
                command_runner=runner,
            )
            restored = (index / "graph.db").read_text(encoding="utf-8")
        self.assertEqual(report["status"], "recovering")
        self.assertEqual(report["strategies"][1]["rollback_status"], "restored")
        self.assertEqual(restored, "previous index")

    def test_healthy_codegraph_rebuild_is_not_rolled_back_for_semble_failure(self):
        initial = preflight_with_tool_failures(
            codegraph="CODEGRAPH_INDEX_INVALID", semble="SEMBLE_NOT_FOUND"
        )
        after_sync = preflight_with_tool_failures(
            codegraph="CODEGRAPH_INDEX_INVALID", semble="SEMBLE_NOT_FOUND"
        )
        after_rebuild = preflight_with_tool_failures(semble="SEMBLE_NOT_FOUND")

        class RebuildRunner(CommandRunner):
            def __call__(self, argv, cwd, timeout):
                result = super().__call__(argv, cwd, timeout)
                if argv[1] == "index":
                    (cwd / ".codegraph" / "graph.db").write_text(
                        "rebuilt index", encoding="utf-8"
                    )
                return result

        with tempfile.TemporaryDirectory() as repo, tempfile.TemporaryDirectory() as state:
            index = pathlib.Path(repo) / ".codegraph"
            index.mkdir()
            (index / "graph.db").write_text("previous index", encoding="utf-8")
            report = recover_toolchain(
                repo,
                semantic_query="semantic sentinel",
                expected_path="src/router.py",
                state_dir=state,
                preflight_runner=SequencePreflight(initial, after_sync, after_rebuild),
                command_runner=RebuildRunner(),
            )
            final_index = (index / "graph.db").read_text(encoding="utf-8")
        self.assertEqual(report["status"], "recovering")
        self.assertEqual(report["strategies"][1]["rollback_status"], "not_needed")
        self.assertEqual(final_index, "rebuilt index")

    def test_changed_codegraph_version_unlocks_no_progress_strategy(self):
        stale_v1 = preflight_with_codegraph_version(
            "test-1", failed_tool="codegraph", reason="CODEGRAPH_STALE"
        )
        stale_v2 = preflight_with_codegraph_version(
            "test-2", failed_tool="codegraph", reason="CODEGRAPH_STALE"
        )
        with tempfile.TemporaryDirectory() as repo, tempfile.TemporaryDirectory() as state:
            recover_toolchain(
                repo,
                semantic_query="semantic sentinel",
                expected_path="src/router.py",
                state_dir=state,
                preflight_runner=SequencePreflight(stale_v1, stale_v1, stale_v1),
                command_runner=CommandRunner(),
            )
            upgraded_runner = CommandRunner()
            upgraded = recover_toolchain(
                repo,
                semantic_query="semantic sentinel",
                expected_path="src/router.py",
                state_dir=state,
                preflight_runner=SequencePreflight(stale_v2, stale_v2, stale_v2),
                command_runner=upgraded_runner,
            )
        self.assertEqual(upgraded["strategy_attempts"], 2)
        self.assertEqual(upgraded_runner.calls[1][1], "sync")

    def test_changed_codegraph_evidence_does_not_unlock_no_progress_strategy(self):
        stale = preflight(failed_tool="codegraph", reason="CODEGRAPH_STALE")
        changed_evidence = preflight(
            failed_tool="codegraph", reason="CODEGRAPH_STALE"
        )
        for tool in changed_evidence["tools"]:
            if tool["tool"] == "codegraph":
                tool["evidence_sha256"] = "f" * 64
        with tempfile.TemporaryDirectory() as repo, tempfile.TemporaryDirectory() as state:
            recover_toolchain(
                repo,
                semantic_query="semantic sentinel",
                expected_path="src/router.py",
                state_dir=state,
                preflight_runner=SequencePreflight(stale, stale, stale),
                command_runner=CommandRunner(),
            )
            repeated_runner = CommandRunner()
            repeated = recover_toolchain(
                repo,
                semantic_query="semantic sentinel",
                expected_path="src/router.py",
                state_dir=state,
                preflight_runner=SequencePreflight(changed_evidence),
                command_runner=repeated_runner,
            )
        self.assertEqual(repeated["strategy_attempts"], 0)
        self.assertEqual(repeated_runner.calls, [("codegraph", "status", "--json", repo)])

    def test_action_timeout_is_a_privacy_safe_recovering_strategy_failure(self):
        stale = preflight(failed_tool="codegraph", reason="CODEGRAPH_STALE")

        class TimeoutRunner(CommandRunner):
            def __call__(self, argv, cwd, timeout):
                if argv[1:3] == ("status", "--json"):
                    return super().__call__(argv, cwd, timeout)
                raise subprocess.TimeoutExpired(
                    ["codegraph", argv[1], "/private/repository"], timeout
                )

        report = self.run_case([stale, stale, stale], TimeoutRunner())
        self.assertEqual(report["status"], "recovering")
        self.assertEqual(report["strategy_attempts"], 2)
        self.assertEqual(
            [strategy["returncode"] for strategy in report["strategies"]], [124, 124]
        )
        self.assertNotIn("/private/repository", json.dumps(report, sort_keys=True))

    def test_cli_normalizes_remaining_subprocess_errors_without_path_leak(self):
        script = pathlib.Path(__file__).parents[2] / "codex" / "bin" / "toolchain-auto.py"
        spec = importlib.util.spec_from_file_location("toolchain_auto_timeout_test", script)
        module = importlib.util.module_from_spec(spec)
        self.assertIsNotNone(spec.loader)
        spec.loader.exec_module(module)
        output = io.StringIO()
        with mock.patch.object(
            module, "recover_toolchain",
            side_effect=subprocess.TimeoutExpired(["codegraph", "/private/repository"], 1),
        ), contextlib.redirect_stdout(output):
            status = module.main([
                "--adaptive-recovery", "--semantic-query", "q", "--expected-path", "a.py",
            ])
        self.assertEqual(status, 5)
        self.assertIn("tool action failed", output.getvalue())
        self.assertNotIn("/private/repository", output.getvalue())

    def test_backup_retention_is_bounded(self):
        with tempfile.TemporaryDirectory() as repo, tempfile.TemporaryDirectory() as state:
            index = pathlib.Path(repo) / ".codegraph"
            index.mkdir()
            (index / "graph.db").write_text("generated index", encoding="utf-8")
            for version in range(4):
                stale = preflight_with_codegraph_version(
                    f"test-{version}", failed_tool="codegraph",
                    reason="CODEGRAPH_INDEX_INVALID",
                )
                recover_toolchain(
                    repo,
                    semantic_query="semantic sentinel",
                    expected_path="src/router.py",
                    state_dir=state,
                    preflight_runner=SequencePreflight(stale, stale, stale, stale),
                    command_runner=CommandRunner(),
                )
            backups = list(pathlib.Path(state).glob("*.backups/*.codegraph"))
        self.assertLessEqual(len(backups), 3)

    def test_no_progress_history_is_bounded_and_retains_the_newest_keys(self):
        with tempfile.TemporaryDirectory() as repo, tempfile.TemporaryDirectory() as state:
            for version in range((HISTORY_LIMIT // 2) + 1):
                stale = preflight_with_codegraph_version(
                    f"test-{version}", failed_tool="codegraph",
                    reason="CODEGRAPH_STALE",
                )
                recover_toolchain(
                    repo,
                    semantic_query="semantic sentinel",
                    expected_path="src/router.py",
                    state_dir=state,
                    preflight_runner=SequencePreflight(stale, stale, stale),
                    command_runner=CommandRunner(),
                )
            history_path = next(pathlib.Path(state).glob("*.recovery.json"))
            persisted = json.loads(history_path.read_text(encoding="utf-8"))["no_progress"]
            newest = preflight_with_codegraph_version(
                f"test-{HISTORY_LIMIT // 2}", failed_tool="codegraph",
                reason="CODEGRAPH_STALE",
            )
            newest_runner = CommandRunner()
            repeated = recover_toolchain(
                repo,
                semantic_query="semantic sentinel",
                expected_path="src/router.py",
                state_dir=state,
                preflight_runner=SequencePreflight(newest),
                command_runner=newest_runner,
            )
        self.assertEqual(len(persisted), HISTORY_LIMIT)
        self.assertEqual(repeated["strategy_attempts"], 0)
        self.assertEqual(newest_runner.calls, [("codegraph", "status", "--json", repo)])

    def test_cli_exposes_adaptive_and_strict_mode_selection(self):
        script = pathlib.Path(__file__).parents[2] / "codex" / "bin" / "toolchain-auto.py"
        result = subprocess.run(
            [sys.executable, str(script), "--help"],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("--adaptive-recovery", result.stdout)
        self.assertIn("--strict-maintenance", result.stdout)

    def test_cli_defaults_to_adaptive_and_keeps_strict_as_an_opt_in(self):
        script = pathlib.Path(__file__).parents[2] / "codex" / "bin" / "toolchain-auto.py"
        spec = importlib.util.spec_from_file_location("toolchain_auto_test", script)
        module = importlib.util.module_from_spec(spec)
        self.assertIsNotNone(spec.loader)
        spec.loader.exec_module(module)
        with mock.patch.object(module, "recover_toolchain", return_value={"status": "ready"}) as adaptive, mock.patch.object(
            module, "maintain_toolchain", return_value={"status": "ready"}
        ) as strict, mock.patch.dict(os.environ, {"CODEX_GOVERNANCE_MODE": "adaptive"}):
            self.assertEqual(module.main(["--semantic-query", "q", "--expected-path", "a.py"]), 0)
            adaptive.assert_called_once()
            strict.assert_not_called()
        with mock.patch.object(module, "recover_toolchain", return_value={"status": "ready"}) as adaptive, mock.patch.object(
            module, "maintain_toolchain", return_value={"status": "ready"}
        ) as strict, mock.patch.dict(os.environ, {"CODEX_GOVERNANCE_MODE": "strict"}):
            self.assertEqual(module.main(["--semantic-query", "q", "--expected-path", "a.py"]), 0)
            strict.assert_called_once()
            adaptive.assert_not_called()


if __name__ == "__main__":
    unittest.main()

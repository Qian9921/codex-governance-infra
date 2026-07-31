#!/usr/bin/env python3
"""Focused deterministic contract tests for the user-level hooks."""

from __future__ import annotations

import json
import hashlib
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path

import hook_receipt


ROOT = Path(__file__).resolve().parent
HOOKS_JSON = ROOT.parent / "hooks.json"
TEST_RECEIPT_DIR = Path(tempfile.mkdtemp(prefix="codex-hook-contract-receipts-"))


def run_hook(name: str, payload: dict, *, receipt_dir: Path | None = None) -> tuple[int, dict | None, str]:
    environment = os.environ.copy()
    # Tests must never write production receipts; runtime source is only selected
    # when the real hook process has no explicit test marker.
    environment["CODEX_HOOK_SOURCE"] = "test"
    environment["CODEX_HOOK_RECEIPT_DIR"] = str(receipt_dir or TEST_RECEIPT_DIR)
    proc = subprocess.run(
        [sys.executable, str(ROOT / name)],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        check=False,
        env=environment,
    )
    parsed = json.loads(proc.stdout) if proc.stdout.strip() else None
    return proc.returncode, parsed, proc.stderr


def receipt_records(directory: Path = TEST_RECEIPT_DIR) -> list[dict]:
    records: list[dict] = []
    for path in sorted(directory.glob("*.jsonl")):
        records.extend(json.loads(line) for line in path.read_text().splitlines() if line)
    return records


class HookContractTest(unittest.TestCase):
    def assert_bash_allowed(self, command: str, model: str = "gpt-5.6-sol") -> None:
        code, output, stderr = run_hook(
            "pre_tool_use_policy.py",
            {"model": model, "tool_name": "Bash", "tool_input": {"command": command}},
        )
        self.assertEqual((code, output, stderr), (0, None, ""), command)

    def test_config_points_at_existing_scripts(self) -> None:
        config = json.loads(HOOKS_JSON.read_text())
        self.assertIn("PreToolUse", config["hooks"])
        for group in config["hooks"].values():
            for matcher in group:
                for handler in matcher["hooks"]:
                    command = handler["command"]
                    token = command.split()[-1].strip('"')
                    path = Path(token.replace("$CODEX_HOME", str(ROOT.parent)))
                    self.assertTrue(path.exists(), path)

    def test_all_models_have_unrestricted_tool_capability(self) -> None:
        # WHY-RED: the old hook denied Sol/Terra while allowing Luna.
        cases = (
            ("Bash", {"command": "rm -f deliberate-test-path"}),
            ("apply_patch", {}),
            ("Edit", {}),
            ("Write", {}),
            ("mcp__codegraph__codegraph_explore", {}),
            ("mcp__unknown__tool", {}),
        )
        for model in ("gpt-5.6-luna", "gpt-5.6-sol", "gpt-5.6-terra"):
            for tool_name, tool_input in cases:
                with self.subTest(model=model, tool_name=tool_name):
                    code, output, stderr = run_hook(
                        "pre_tool_use_policy.py",
                        {"model": model, "tool_name": tool_name, "tool_input": tool_input},
                    )
                    self.assertEqual((code, output, stderr), (0, None, ""))

    def test_unknown_model_is_not_model_denied(self) -> None:
        code, output, stderr = run_hook(
            "pre_tool_use_policy.py",
            {"model": "gpt-unknown", "tool_name": "Write", "tool_input": {}},
        )
        self.assertEqual((code, output, stderr), (0, None, ""))

    def test_policy_does_not_emit_external_authorization(self) -> None:
        # The hook removes model gating only; platform/user approval remains outside it.
        for model in ("gpt-5.6-luna", "gpt-5.6-sol", "gpt-5.6-terra"):
            code, output, stderr = run_hook(
                "pre_tool_use_policy.py",
                {
                    "model": model,
                    "tool_name": "mcp__mail__send_message",
                    "tool_input": {"recipient": "external"},
                },
            )
            self.assertEqual((code, output, stderr), (0, None, ""))

    def test_luna_is_not_blocked_by_role_guard(self) -> None:
        code, output, stderr = run_hook(
            "pre_tool_use_policy.py",
            {"model": "gpt-5.6-luna", "tool_name": "apply_patch", "tool_input": {}},
        )
        self.assertEqual((code, output, stderr), (0, None, ""))

    def test_session_context_contains_routing_and_parity_contract(self) -> None:
        code, output, _ = run_hook("session_context.py", {"hook_event_name": "SessionStart"})
        context = output["hookSpecificOutput"]["additionalContext"]
        self.assertEqual(code, 0)
        for phrase in (
            "full tool capability",
            "platform and user authorization",
            "Semble",
            "CodeGraph",
            "rtk",
            "synthetic exact-zero",
            "ZERO_PARITY_BLOCKED",
        ):
            self.assertIn(phrase, context)

    def test_session_and_subagent_context_identifies_runtime_role(self) -> None:
        # WHY-RED R5: old context caused Sol/Terra/unknown self-refusal.
        cases = (
            (
                " GPT-5.6-LUNA ",
                "Runtime role identity: you are GPT-5.6 Luna",
            ),
            ("gpt-5.6-sol", "Runtime role identity: you are GPT-5.6 Sol"),
            ("gpt-5.6-terra", "Runtime role identity: you are GPT-5.6 Terra"),
            ("gpt-unknown", "Runtime role identity: model identity unknown"),
        )
        for event in ("SessionStart", "SubagentStart"):
            for model, role_fragment in cases:
                with self.subTest(event=event, model=model):
                    code, output, stderr = run_hook(
                        "session_context.py",
                        {"hook_event_name": event, "model": model},
                    )
                    self.assertEqual((code, stderr), (0, ""))
                    hook_output = output["hookSpecificOutput"]
                    context = hook_output["additionalContext"]
                    self.assertEqual(hook_output["hookEventName"], event)
                    self.assertIn(role_fragment, context)
                    self.assertIn("full tool capability", context)
                    self.assertIn("role selection is task-, user-, and L0-directed", context)
                    self.assertNotIn("read-only", context)
                    self.assertNotIn("sole writer/runner", context)

    def test_receipt_privacy_snapshot_and_identifier_hashes(self) -> None:
        # WHY-RED R1: raw identifiers, prompts, args, or cwd would violate the
        # privacy contract even when the policy decision itself is correct.
        run_hook(
            "session_context.py",
            {
                "hook_event_name": "SessionStart",
                "model": "gpt-5.6-sol",
                "session_id": "session-secret-123",
                "prompt": "prompt-secret",
                "cwd": "/private/worktree",
            },
        )
        run_hook(
            "pre_tool_use_policy.py",
            {
                "model": "gpt-5.6-sol",
                "tool_name": "Bash",
                "session_id": "session-secret-123",
                "turn_id": "turn-secret-456",
                "tool_call_id": "call-secret-789",
                "tool_input": {"command": "rm private-secret"},
            },
        )
        records = receipt_records()
        self.assertGreaterEqual(len(records), 2)
        for record in records[-2:]:
            self.assertEqual(record["schema_version"], "hook-receipt.v1")
            self.assertEqual(record["source"], "test")
            self.assertRegex(record["hook_snapshot_sha256"], r"^[0-9a-f]{64}$")
            serialized = json.dumps(record, sort_keys=True)
            for forbidden in (
                "session-secret-123",
                "turn-secret-456",
                "call-secret-789",
                "prompt-secret",
                "private-secret",
                "/private/worktree",
            ):
                self.assertNotIn(forbidden, serialized)
        recorded = records[-1]
        self.assertRegex(recorded["session_id_sha256"], r"^[0-9a-f]{64}$")
        self.assertRegex(recorded["turn_id_sha256"], r"^[0-9a-f]{64}$")
        self.assertRegex(recorded["tool_call_id_sha256"], r"^[0-9a-f]{64}$")
        self.assertEqual(
            recorded["session_id_sha256"],
            hashlib.sha256(b"session-secret-123").hexdigest(),
        )

    def test_receipt_permissions_append_and_snapshot_stability(self) -> None:
        # WHY-RED R2: weak permissions, truncating writes, or drifting snapshots
        # would make runtime evidence forgeable or unreadable.
        before = len(receipt_records())
        for _ in range(2):
            code, output, stderr = run_hook(
                "pre_tool_use_policy.py",
                {"model": "gpt-5.6-sol", "tool_name": "Bash", "tool_input": {"command": "pwd"}},
            )
            self.assertEqual((code, output, stderr), (0, None, ""))
        files = sorted(TEST_RECEIPT_DIR.glob("*.jsonl"))
        self.assertTrue(files)
        self.assertEqual(stat.S_IMODE(TEST_RECEIPT_DIR.stat().st_mode), 0o700)
        for path in files:
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        records = receipt_records()
        self.assertGreaterEqual(len(records), before + 2)
        self.assertEqual(len({record["hook_snapshot_sha256"] for record in records}), 1)

    def test_receipt_runtime_tagging_branch_isolated_from_production(self) -> None:
        # WHY-RED R3: runtime evidence must be distinguishable from test evidence;
        # monkeypatch the helper's default only inside this isolated test process.
        helper_code = (
            "import os, pathlib, sys; "
            f"sys.path.insert(0, {str(ROOT)!r}); "
            "import hook_receipt; "
            f"hook_receipt.DEFAULT_RECEIPT_DIR = pathlib.Path({str(TEST_RECEIPT_DIR)!r}); "
            "os.environ.pop('CODEX_HOOK_SOURCE', None); "
            "assert hook_receipt.record_receipt('PreToolUse', {'session_id':'runtime-id'}, model='gpt-5.6-sol', decision='allow', reason_code='runtime_probe')"
        )
        proc = subprocess.run(
            [sys.executable, "-c", helper_code],
            text=True,
            capture_output=True,
            check=False,
            env={key: value for key, value in os.environ.items() if key != "CODEX_HOOK_SOURCE"},
        )
        self.assertEqual((proc.returncode, proc.stderr), (0, ""))
        self.assertEqual(receipt_records()[-1]["source"], "runtime")

    def test_receipt_failure_does_not_change_policy_decision(self) -> None:
        # WHY-RED R4: receipt I/O is evidence-only and must not turn an allow into
        # a deny (or vice versa) when its directory is unavailable.
        broken_target = TEST_RECEIPT_DIR / "not-a-directory"
        broken_target.write_text("occupied")
        allowed = run_hook(
            "pre_tool_use_policy.py",
            {"model": "gpt-5.6-sol", "tool_name": "Bash", "tool_input": {"command": "pwd"}},
            receipt_dir=broken_target,
        )
        unrestricted = run_hook(
            "pre_tool_use_policy.py",
            {"model": "gpt-5.6-sol", "tool_name": "Bash", "tool_input": {"command": "rm file"}},
            receipt_dir=broken_target,
        )
        self.assertEqual(allowed, (0, None, ""))
        self.assertEqual(unrestricted, (0, None, ""))

    def test_receipt_short_write_sequence_is_written_completely(self) -> None:
        # WHY-RED R2-V2-P2-001: one os.write call can report a positive short
        # count; the old implementation returned success with truncated JSONL.
        payload = b'{"receipt":"complete"}\n'
        captured = bytearray()
        planned_counts = [2, 1, len(payload)]

        def fake_write(_descriptor: int, view: memoryview) -> int:
            count = min(planned_counts.pop(0), len(view))
            captured.extend(view[:count])
            return count

        with (
            mock.patch.object(hook_receipt.os, "open", return_value=17),
            mock.patch.object(hook_receipt.os, "fchmod"),
            mock.patch.object(hook_receipt.os, "close") as close,
            mock.patch.object(hook_receipt.os, "write", side_effect=fake_write),
        ):
            self.assertTrue(hook_receipt._append_jsonl(TEST_RECEIPT_DIR, payload, "2999-12-31"))
        self.assertEqual(bytes(captured), payload)
        self.assertEqual(planned_counts, [])
        close.assert_called_once_with(17)
        self.assertFalse((TEST_RECEIPT_DIR / "2999-12-31.jsonl").exists())

    def test_receipt_zero_write_progress_fails_closed_without_runtime_receipt(self) -> None:
        # WHY-RED R2-V2-P2-001: zero progress must not be reported as a receipt
        # success and must not create a runtime receipt artifact.
        with (
            mock.patch.object(hook_receipt.os, "open", return_value=23),
            mock.patch.object(hook_receipt.os, "fchmod"),
            mock.patch.object(hook_receipt.os, "close") as close,
            mock.patch.object(hook_receipt.os, "write", return_value=0),
        ):
            self.assertFalse(hook_receipt._append_jsonl(TEST_RECEIPT_DIR, b"{}\n", "2999-12-30"))
        close.assert_called_once_with(23)
        self.assertFalse((TEST_RECEIPT_DIR / "2999-12-30.jsonl").exists())


if __name__ == "__main__":
    unittest.main()

class V15HookContractTest(unittest.TestCase):
    def _packet(self):
        return {"schema":"delegation.v1","parent_task_id":"parent/1","child_task_id":"child/1","assigned_model":"gpt-5.3-codex-spark","role":"specialist","max_depth":1,"depth":1,"permissions":["read","write_paths"],"forbidden_permissions":["approve","approver","bash","shell","git","git_push","github","github_api","merge","merger","review","reviewer"],"lease":{"paths":["tests"]},"retry_budget":{"semantic_contamination":1},"active_mission_lock":True,"plugin_inventory":"informational","result_schema":"delegation-result.v1"}
    def _result(self, **kw):
        r={"schema":"delegation-result.v1","parent_task_id":"parent/1","child_task_id":"child/1","assigned_model":"gpt-5.3-codex-spark","task_id":"child/1","depth":1,"changed_paths":["tests/x.py"],"counts":{"total":1,"ran":1,"passed":1,"failed":0,"skipped":0,"unknown":0},"retry_used":0,"contamination":False,"status":"complete","artifact_sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","evidence_id":"fixture-evidence","attempt_id":"attempt/1","retry_transcript":[]}; r.update(kw); return r
    def test_spark_identity(self):
        code,out,err=run_hook("session_context.py",{"hook_event_name":"SubagentStart","model":"gpt-5.3-codex-spark"}); self.assertEqual(code,0); self.assertIn("GPT-5.3 Codex Spark",out["hookSpecificOutput"]["additionalContext"])
    def test_spark_unrestricted(self): self.assertEqual(run_hook("pre_tool_use_policy.py",{"model":"gpt-5.3-codex-spark","tool_name":"Write","tool_input":{}}),(0,None,""))
    def test_active_mission_lock(self):
        code,out,_=run_hook("session_context.py",{"hook_event_name":"SubagentStart","model":"gpt-5.3-codex-spark"}); self.assertIn("ACTIVE-MISSION-LOCK",out["hookSpecificOutput"]["additionalContext"])
    def test_recommended_plugins_informational(self):
        code,out,_=run_hook("session_context.py",{"hook_event_name":"SubagentStart","model":"gpt-5.3-codex-spark"}); self.assertIn("recommended_plugins inventory is informational",out["hookSpecificOutput"]["additionalContext"])
    def test_valid_luna_to_spark_packet(self):
        from delegation_contract import validate_packet; self.assertTrue(validate_packet(self._packet()))
    def test_depth_one_only(self):
        from delegation_contract import validate_packet,ContractError; p=self._packet(); p["depth"]=2; self.assertRaises(ContractError,validate_packet,p)
    def test_overlapping_lease_rejected(self):
        from delegation_contract import validate_packet,ContractError; p=self._packet(); p["lease"]["paths"]=["tests/","tests/x.py"]; self.assertRaises(ContractError,validate_packet,p)
    def test_unauthorized_child_git_github_rejected(self):
        from delegation_contract import validate_packet,ContractError
        for perm in ("git","github","review","merge"):
            p=self._packet(); p["permissions"]=[perm]; self.assertRaises(ContractError,validate_packet,p)
    def test_contaminated_result_rejected(self):
        from delegation_contract import validate_result,ContractError; self.assertRaises(ContractError,validate_result,self._result(contamination=True),self._packet())

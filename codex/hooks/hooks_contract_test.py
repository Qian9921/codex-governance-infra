#!/usr/bin/env python3
import json
import hashlib
import os
import pathlib
import stat
import subprocess
import sys
import tempfile
import unittest

try:  # Support both direct hook execution and package-based test discovery.
    from .delegation_contract import ContractError, validate_packet, validate_result
    from . import (
        hook_receipt, post_tool_use_receipt, pre_tool_use_policy,
        session_context, stop_tool_enforcement,
    )
except ImportError:  # pragma: no cover - exercised by direct script invocation.
    from delegation_contract import ContractError, validate_packet, validate_result
    import hook_receipt
    import post_tool_use_receipt
    import pre_tool_use_policy
    import session_context
    import stop_tool_enforcement


class HooksContractTests(unittest.TestCase):
    def packet(self): return {"schema":"delegation.v1","parent_task_id":"parent/1","child_task_id":"child/1","assigned_model":"gpt-5.3-codex-spark","role":"specialist","max_depth":1,"depth":1,"permissions":["read","write_paths"],"forbidden_permissions":["git","github","review","merge"],"lease":{"paths":["tests/"]},"retry_budget":{"semantic_contamination":1},"active_mission_lock":True,"plugin_inventory":"informational","result_schema":"delegation-result.v1"}
    def result(self,**kw):
        d={"schema":"delegation-result.v1","parent_task_id":"parent/1","child_task_id":"child/1","assigned_model":"gpt-5.3-codex-spark","task_id":"child/1","depth":1,"changed_paths":["tests/x.py"],"counts":{"total":1,"ran":1,"passed":1,"failed":0,"skipped":0},"retry_used":0,"contamination":False,"status":"complete"}; d.update(kw); return d
    def test_valid(self): self.assertTrue(validate_packet(self.packet())); self.assertTrue(validate_result(self.result(),self.packet()))
    def test_depth(self): p=self.packet(); p["depth"]=2; self.assertRaises(ContractError,validate_packet,p)
    def test_overlap(self): p=self.packet(); p["lease"]["paths"]=["tests/"]; self.assertTrue(validate_packet(p))
    def test_unauthorized(self): p=self.packet(); p["permissions"]=["git"]; self.assertRaises(ContractError,validate_packet,p)
    def test_contaminated(self): self.assertRaises(ContractError,validate_result,self.result(contamination=True),self.packet())

    def test_routing_context_is_explicit(self):
        context = session_context.build_context("SubagentStart", "gpt-5.3-codex-spark")
        self.assertEqual(
            context["routing"],
            {
                "known_structure": "CodeGraph",
                "unknown_semantic_or_similar": "Semble",
                "shell_display": "rtk",
                "exact_text_log_config": "rg",
            },
        )
        guidance = context["additionalContext"]
        for route in ("CodeGraph", "Semble", "rtk", "rg"):
            self.assertIn(route, guidance)
        self.assertEqual(
            context["tool_preflight"],
            {
                "required_before_repo_work": True,
                "schema": "tool-preflight.v16",
                "strict_ready_status": "ready",
                "mandatory_tools": ["codegraph", "semble", "rtk"],
                "usage_schema": "tool-usage.v16",
                "receipt_backed_usage_required": True,
                "task_contract_schema": "tool-task-contract.v16",
                "enforcement_schema": "tool-enforcement.v16",
                "maintenance_schema": "tool-maintenance.v16",
                "automatic_repo_index_repair": True,
                "repair_budget": 1,
                "repair_owner": "assigned_execution_agent:tool_maintainer",
            },
        )
        self.assertIn("TOOL-PREFLIGHT", guidance)
        self.assertEqual(context["review_runtime"]["formal_review_calls"], 1)
        self.assertEqual(
            context["review_runtime"]["duplicate_full_scope_reviews"], 0
        )
        self.assertIn("delta-only", context["review_runtime"]["delta_continuation"])
        self.assertIn("high-risk Sol high", context["review_runtime"]["delta_continuation"])
        self.assertIn("low/medium Terra high", context["review_runtime"]["delta_continuation"])
        self.assertIn("REVIEW-RUNTIME", guidance)
        self.assertIn("low/medium Terra high", guidance)

    def test_receipt_allowlists_private_fields(self):
        raw = "PRIVATE_PROMPT_VALUE /cwd /secret"
        value = hook_receipt.receipt(
            "tool_call",
            "gpt-5.6-luna",
            tool="rg",
            decision="allow",
            reason="policy_pass",
            route_code="rg",
        )
        value["args"] = raw
        value["cwd"] = raw
        value["prompt"] = raw
        with tempfile.TemporaryDirectory() as directory:
            destination = pathlib.Path(directory) / "receipt.jsonl"
            self.assertTrue(hook_receipt.write_receipt(value, destination))
            line = destination.read_text(encoding="utf-8").strip()
        persisted = json.loads(line)
        self.assertTrue(hook_receipt.has_receipt(value))
        self.assertNotIn(raw, line)
        self.assertNotIn("args", persisted)
        self.assertNotIn("cwd", persisted)
        self.assertNotIn("prompt", persisted)

    def test_hook_snapshot_paths_are_package_relative_and_complete(self):
        missing = [path for path in hook_receipt.SNAPSHOT_FILES if not path.is_file()]
        self.assertEqual(missing, [])
        self.assertEqual(
            hook_receipt.SNAPSHOT_FILES[0],
            pathlib.Path(__file__).resolve().parents[1] / "AGENTS.md",
        )

    def test_missing_receipt_is_detectable(self):
        value = hook_receipt.receipt("tool_call", "gpt-5.6-luna")
        self.assertFalse(hook_receipt.has_receipt(value))
        with tempfile.TemporaryDirectory() as directory:
            occupied = pathlib.Path(directory) / "occupied"
            occupied.write_text("not a directory", encoding="utf-8")
            self.assertFalse(hook_receipt.write_receipt(value, occupied / "receipt.jsonl"))
        self.assertEqual(value["receipt_status"], "write_failed")

    def _run_entrypoint(self, name, payload, directory):
        env = os.environ.copy()
        env["CODEX_HOOK_SOURCE"] = "test"
        env["CODEX_HOOK_RECEIPT_DIR"] = str(directory)
        return subprocess.run(
            [sys.executable, str(pathlib.Path(__file__).with_name(name))],
            input=json.dumps(payload), text=True, capture_output=True, check=False, env=env,
        )

    def test_real_session_subagent_and_pretool_entrypoints_persist_receipts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            session = self._run_entrypoint(
                "session_context.py",
                {"hook_event_name": "SessionStart", "model": "gpt-5.6-sol", "session_id": "session-secret"},
                root,
            )
            subagent = self._run_entrypoint(
                "session_context.py",
                {"hook_event_name": "SubagentStart", "model": "gpt-5.6-luna", "turn_id": "turn-secret"},
                root,
            )
            allowed = self._run_entrypoint(
                "pre_tool_use_policy.py",
                {"tool_name": "rg", "model": "gpt-5.6-terra", "tool_call_id": "call-secret", "args": {"prompt": "PRIVATE"}},
                root,
            )
            denied = self._run_entrypoint(
                "pre_tool_use_policy.py",
                {"tool_name": "git", "model": "gpt-5.6-terra"},
                root,
            )
            for proc in (session, subagent, allowed, denied):
                self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(
                json.loads(session.stdout)["hookSpecificOutput"]["hookEventName"],
                "SessionStart",
            )
            self.assertEqual(
                json.loads(subagent.stdout)["hookSpecificOutput"]["hookEventName"],
                "SubagentStart",
            )
            self.assertEqual(
                json.loads(allowed.stdout)["hookSpecificOutput"]["permissionDecision"],
                "allow",
            )
            self.assertEqual(
                json.loads(denied.stdout)["hookSpecificOutput"]["permissionDecision"],
                "deny",
            )
            files = list(root.glob("*.jsonl"))
            self.assertEqual(len(files), 1)
            self.assertEqual(stat.S_IMODE(root.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE(files[0].stat().st_mode), 0o600)
            records = [json.loads(line) for line in files[0].read_text().splitlines()]
            self.assertEqual(len(records), 4)
            self.assertEqual([record["event"] for record in records], ["SessionStart", "SubagentStart", "PreToolUse", "PreToolUse"])
            self.assertEqual([record["decision"] for record in records], ["allow", "allow", "allow", "deny"])
            for record in records:
                self.assertEqual(record["schema"], "hook-receipt.v16")
                self.assertEqual(record["schema_version"], "hook-receipt.v16")
                self.assertEqual(record["source"], "test")
                self.assertRegex(record["utc"], r"^20\d\d-\d\d-\d\dT.*Z$")
                self.assertIsInstance(record["pid"], int)
                self.assertIsInstance(record["ppid"], int)
                self.assertRegex(record["snapshot_sha256"], r"^[0-9a-f]{64}$")
            self.assertEqual(records[0]["session_id_sha256"], hashlib.sha256(b"session-secret").hexdigest())
            self.assertEqual(records[1]["turn_id_sha256"], hashlib.sha256(b"turn-secret").hexdigest())
            self.assertEqual(records[2]["tool_call_id_sha256"], hashlib.sha256(b"call-secret").hexdigest())
            serialized = files[0].read_text()
            for forbidden in ("session-secret", "turn-secret", "call-secret", "PRIVATE", "prompt"):
                self.assertNotIn(forbidden, serialized)

    def test_entrypoint_write_failure_is_visible_without_decision_change(self):
        with tempfile.TemporaryDirectory() as directory:
            occupied = pathlib.Path(directory) / "occupied"
            occupied.write_text("not a directory", encoding="utf-8")
            proc = self._run_entrypoint("pre_tool_use_policy.py", {"tool_name": "rg"}, occupied)
            self.assertEqual(proc.returncode, 0)
            parsed = json.loads(proc.stdout)
            self.assertEqual(parsed["hookSpecificOutput"]["permissionDecision"], "allow")
            self.assertIn("receipt write failed", parsed["systemMessage"])

    def test_symlink_destination_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            actual = root / "actual"
            actual.mkdir()
            link = root / "link"
            link.symlink_to(actual, target_is_directory=True)
            proc = self._run_entrypoint("session_context.py", {"hook_event_name": "SessionStart"}, link)
            self.assertEqual(proc.returncode, 0)
            self.assertIn("receipt write failed", json.loads(proc.stdout)["systemMessage"])
            self.assertEqual(list(actual.iterdir()), [])

    def test_sensitive_labels_are_normalized(self):
        value = hook_receipt.receipt("PRIVATE_PROMPT", "PRIVATE_TOKEN", tool="PRIVATE_SECRET")
        self.assertEqual(value["event"], "unknown_event")
        self.assertEqual(value["model"], "unknown_model")
        self.assertEqual(value["tool_name"], "unknown_tool")

    def test_decision_is_deterministic_without_raw_argument_inference(self):
        machine_args = {"cmd": "git status --porcelain", "input": "PRIVATE_PROMPT_VALUE"}
        first = pre_tool_use_policy.decide("exec_command", machine_args)
        second = pre_tool_use_policy.decide("exec_command", machine_args)
        self.assertEqual(first, second)
        self.assertEqual(first["decision"], "allow")
        self.assertEqual(first["route"], "unspecified")
        self.assertEqual(
            pre_tool_use_policy.decide(
                "functions.exec_command", {"cmd": "rtk git status --short"}
            )["route"],
            "rtk",
        )
        self.assertEqual(
            pre_tool_use_policy.decide(
                "functions.exec_command",
                {"cmd": "rtk codegraph impact route_tool -p ."},
            )["route"],
            "CodeGraph",
        )
        self.assertEqual(
            pre_tool_use_policy.decide(
                "functions.exec_command",
                {"cmd": "rtk semble search semantic ."},
            )["route"],
            "Semble",
        )
        self.assertEqual(
            pre_tool_use_policy.decide(
                "functions.exec_command",
                {"cmd": "rtk rg -n pattern file"},
            )["route"],
            "rg",
        )
        self.assertEqual(
            pre_tool_use_policy.decide("mcp__codegraph__callers")["route"],
            "CodeGraph",
        )
        self.assertEqual(
            pre_tool_use_policy.decide(
                "exec_command", {"command": "/usr/bin/rg -n pattern file"}
            )["route"],
            "rg",
        )
        self.assertEqual(
            pre_tool_use_policy.decide(
                "exec_command", {"cmd": "printf x | rtk rg pattern"}
            )["route"],
            "unspecified",
        )
        self.assertEqual(pre_tool_use_policy.decide("git")["decision"], "deny")
        self.assertEqual(pre_tool_use_policy.decide("rg")["decision"], "allow")
        self.assertEqual(
            pre_tool_use_policy.decide("toolchain-doctor")["route"], "preflight"
        )
        self.assertEqual(
            pre_tool_use_policy.decide(
                "Bash", {"command": "rtk python3 codex/bin/toolchain-auto.py --repo ."}
            )["route"],
            "maintenance",
        )

    def test_native_hooks_config_uses_current_codex_event_shape(self):
        path = pathlib.Path(__file__).parents[1] / "hooks.json"
        config = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(
            set(config["hooks"]),
            {"SessionStart", "SubagentStart", "PreToolUse", "PostToolUse", "Stop"},
        )
        commands = [
            hook["command"]
            for groups in config["hooks"].values()
            for group in groups
            for hook in group["hooks"]
        ]
        for name in (
            "session_context.py", "pre_tool_use_policy.py",
            "post_tool_use_receipt.py", "stop_tool_enforcement.py",
        ):
            self.assertTrue(any(name in command for command in commands), name)

    def test_post_receipts_and_stop_gate_are_success_bound_and_one_shot(self):
        marker = (
            "<!-- tool-task-contract.v16 semantic_discovery=not_applicable "
            "structural_analysis=required exact_lookup=not_applicable "
            "shell_context=not_applicable machine_exact_only=false -->"
        )
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as repo:
            root = pathlib.Path(directory)
            pathlib.Path(repo, ".git").mkdir()
            common = {"turn_id": "turn-1", "model": "gpt-5.6-luna"}
            maintenance = self._run_entrypoint(
                "post_tool_use_receipt.py",
                {
                    **common,
                    "hook_event_name": "PostToolUse",
                    "tool_name": "Bash",
                    "tool_use_id": "maintenance-call",
                    "tool_input": {"command": "rtk python3 codex/bin/toolchain-auto.py --repo ."},
                    "tool_response": {"exit_code": 0},
                },
                root,
            )
            structural = self._run_entrypoint(
                "post_tool_use_receipt.py",
                {
                    **common,
                    "hook_event_name": "PostToolUse",
                    "tool_name": "Bash",
                    "tool_use_id": "codegraph-call",
                    "tool_input": {"command": "rtk codegraph impact symbol -p ."},
                    "tool_response": {"exit_code": 0},
                },
                root,
            )
            self.assertEqual(maintenance.returncode, 0, maintenance.stderr)
            self.assertEqual(structural.returncode, 0, structural.stderr)
            passed = self._run_entrypoint(
                "stop_tool_enforcement.py",
                {
                    **common,
                    "hook_event_name": "Stop",
                    "cwd": repo,
                    "stop_hook_active": False,
                    "last_assistant_message": marker,
                },
                root,
            )
            self.assertEqual(json.loads(passed.stdout), {})

            missing_marker = marker.replace(
                "semantic_discovery=not_applicable", "semantic_discovery=required"
            )
            first = self._run_entrypoint(
                "stop_tool_enforcement.py",
                {
                    **common,
                    "hook_event_name": "Stop",
                    "cwd": repo,
                    "stop_hook_active": False,
                    "last_assistant_message": missing_marker,
                },
                root,
            )
            self.assertEqual(json.loads(first.stdout)["decision"], "block")
            second = self._run_entrypoint(
                "stop_tool_enforcement.py",
                {
                    **common,
                    "hook_event_name": "Stop",
                    "cwd": repo,
                    "stop_hook_active": True,
                    "last_assistant_message": missing_marker,
                },
                root,
            )
            second_output = json.loads(second.stdout)
            self.assertNotIn("decision", second_output)
            self.assertIn("circuit is open", second_output["systemMessage"])

    def test_post_tool_failure_is_not_accepted_as_success(self):
        self.assertFalse(post_tool_use_receipt.tool_succeeded({"exit_code": 7}))
        self.assertFalse(post_tool_use_receipt.tool_succeeded({"isError": True}))
        self.assertTrue(post_tool_use_receipt.tool_succeeded({"exit_code": 0}))


if __name__ == '__main__': unittest.main()

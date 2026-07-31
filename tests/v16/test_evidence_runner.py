import copy
import hashlib
import json
import pathlib
import subprocess
import tempfile
import unittest

from codex.v16.evidence import EvidenceError, build_envelope, validate_counts, validate_envelope, validate_row
from codex.v16.runner import GateRunner, GateRunError

ROOT = pathlib.Path(__file__).parents[2]
HEAD = "e18439c8dfe01d901895efd09b8b73b6842327a9"
TREE = "1de79a7c48e6c66f167be54ca9cf387310149f80"
LOG = "a" * 64


def row(case_id="CASE-1", semantics="semantics-1", decision="allow", head=HEAD, log_sha=LOG):
    return {
        "schema": "evidence-row.v16", "case_id": case_id, "semantics": semantics,
        "gate_id": "G-TARGETED", "stage": "targeted", "decision": decision,
        "expected_head": HEAD, "actual_head": head, "tree_sha": TREE, "dirty": False,
        "command": ["python3", "-c", "pass"], "cwd": ".", "runtime": "python-stdlib",
        "config": "fixture", "started_at": "2026-07-31T00:00:00Z", "ended_at": "2026-07-31T00:00:01Z",
        "elapsed_sec": 1, "exit_status": 0, "counts": {"total": 1, "ran": 1, "passed": 1, "failed": 0, "skipped": 0, "xfail": 0, "unknown": 0},
        "log_sha256": log_sha, "log_mode": 0o600, "log_size": 1, "reused": False, "superseded": False,
    }


class EvidenceTests(unittest.TestCase):
    def test_exact_arithmetic_and_bool_int(self):
        self.assertEqual(validate_counts({"total": 2, "ran": 2, "passed": 2, "failed": 0, "skipped": 0, "xfail": 0, "unknown": 0})["total"], 2)
        with self.assertRaises(EvidenceError):
            validate_counts({"total": True, "ran": 1, "passed": 1, "failed": 0, "skipped": 0})
        with self.assertRaises(EvidenceError):
            validate_counts({"total": 1, "ran": 1, "passed": 1, "failed": 0, "skipped": 0, "unknown": 1})

    def test_envelope_hash_and_stale_copy_privacy_guards(self):
        first = row()
        second = row("CASE-2", "semantics-2", log_sha="b" * 64)
        envelope = build_envelope("V16-PRODUCTIVITY", HEAD, TREE, [first, second], generated_at="2026-07-31T00:00:02Z")
        self.assertEqual(validate_envelope(envelope)["envelope_sha256"], envelope["envelope_sha256"])
        stale = copy.deepcopy(envelope); stale["rows"][0]["actual_head"] = "0" * 40
        with self.assertRaises(EvidenceError):
            validate_envelope(stale, expected_head=HEAD)
        copied = copy.deepcopy(envelope); copied["rows"][1]["log_sha256"] = copied["rows"][0]["log_sha256"]; copied["envelope_sha256"] = ""
        copied["envelope_sha256"] = hashlib.sha256(json.dumps({k: copied[k] for k in copied if k != "envelope_sha256"}, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        with self.assertRaises(EvidenceError):
            validate_envelope(copied)

    def test_decision_deny_exit_zero_and_missing_log(self):
        denied = row(decision="deny")
        with self.assertRaises(EvidenceError):
            validate_row(denied)
        with tempfile.TemporaryDirectory() as tmp:
            missing = row(); missing["log_path"] = "missing.log"
            with self.assertRaises(EvidenceError):
                validate_row(missing, log_root=pathlib.Path(tmp))

    def test_runner_direct_argv_and_structured_checker(self):
        head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
        plan = {
            "gate_order": ["G-TARGETED"],
            "gates": [{"id": "G-TARGETED", "stage": "targeted", "depends_on": [], "entrypoint_ids": ["EP"], "blocking": True, "reusable": True}],
            "entrypoints": [{"id": "EP", "argv": ["python3", "-c", "print('{\"total\":1,\"ran\":1,\"passed\":1,\"failed\":0,\"skipped\":0,\"xfail\":0,\"unknown\":0}')"], "cwd": ".", "env": {}, "timeout_sec": 5, "stop_conditions": ["timeout"]}],
        }
        with tempfile.TemporaryDirectory() as tmp:
            result = GateRunner(ROOT, plan, tmp).run_plan(expected_head=head)
            self.assertEqual(result["results"][0]["decision"], "allow")
            self.assertTrue(result["results"][0]["rows"][0]["log_shas"])

    def test_runner_timeout_red_and_dependents_stop(self):
        head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
        plan = {
            "gate_order": ["G-TARGETED", "G-FULL"],
            "gates": [
                {"id": "G-TARGETED", "stage": "targeted", "depends_on": [], "entrypoint_ids": ["EP"], "blocking": True, "reusable": False},
                {"id": "G-FULL", "stage": "full", "depends_on": ["G-TARGETED"], "entrypoint_ids": ["EP"], "blocking": True, "reusable": False},
            ],
            "entrypoints": [{"id": "EP", "argv": ["python3", "-c", "import time; time.sleep(1)"], "cwd": ".", "env": {}, "timeout_sec": 0.01, "stop_conditions": ["timeout"]}],
        }
        with tempfile.TemporaryDirectory() as tmp:
            result = GateRunner(ROOT, plan, tmp).run_plan(expected_head=head)
            self.assertEqual(result["results"][0]["decision"], "deny")
            self.assertEqual(result["results"][1]["decision"], "skipped")


if __name__ == "__main__":
    unittest.main()

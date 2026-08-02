import copy
import hashlib
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest import mock

from codex.v16.compiler import compile_mission, gate_ids_for_tier
from codex.v16.contracts import canonical_sha256
from codex.v16.evidence import EvidenceError, build_envelope, validate_counts, validate_envelope, validate_row
from codex.v16.metrics import BudgetLedger, RoutingError, choose_model, collect_runtime_metrics
from codex.v16.presubmit import _evidence_rows
from codex.v16.runner import GateRunner, GateRunError, content_snapshot, validate_gate_result

ROOT = pathlib.Path(__file__).parents[2]
HEAD = "e18439c8dfe01d901895efd09b8b73b6842327a9"
TREE = "1de79a7c48e6c66f167be54ca9cf387310149f80"
LOG = "a" * 64


def row(case_id="CASE-1", semantics="semantics-1", decision="allow", head=HEAD, log_sha=LOG, *, identity_mode="git-exact-object", snapshot_sha256="", dirty=False):
    return {
        "schema": "evidence-row.v16", "case_id": case_id, "semantics": semantics,
        "gate_id": "G-TARGETED", "stage": "targeted", "decision": decision,
        "expected_head": HEAD, "actual_head": head, "tree_sha": TREE,
        "identity_mode": identity_mode, "snapshot_sha256": snapshot_sha256, "dirty": dirty,
        "command": ["python3", "-c", "pass"], "cwd": ".", "runtime": "python-stdlib",
        "config": "fixture", "started_at": "2026-07-31T00:00:00Z", "ended_at": "2026-07-31T00:00:01Z",
        "elapsed_sec": 1, "exit_status": 0, "counts": {"total": 1, "ran": 1, "passed": 1, "failed": 0, "skipped": 0, "xfail": 0, "unknown": 0},
        "log_sha256": log_sha, "log_mode": 0o600, "log_size": 1, "reused": False, "superseded": False,
    }


def fast_plan():
    entry = {
        "id": "EP",
        "argv": ["python3", "-c", "print('{\"total\":1,\"ran\":1,\"passed\":1,\"failed\":0,\"skipped\":0,\"xfail\":0,\"unknown\":0}')"],
        "cwd": ".", "env": {}, "timeout_sec": 5,
        "stop_conditions": ["timeout"], "read_only": True,
    }
    return {
        "schema": "compiled-plan.v16",
        "review_policy": {
            "review_risk": "high",
            "required_stages": ["targeted", "full", "fresh"],
        },
        "gate_order": ["G-T", "G-F", "G-R"],
        "gates": [
            {"id": "G-T", "stage": "targeted", "depends_on": [], "entrypoint_ids": ["EP"], "blocking": True, "reusable": True, "read_only": True},
            {"id": "G-F", "stage": "full", "depends_on": ["G-T"], "entrypoint_ids": ["EP"], "blocking": True, "reusable": False, "read_only": True},
            {"id": "G-R", "stage": "fresh", "depends_on": ["G-F"], "entrypoint_ids": ["EP"], "blocking": True, "reusable": False, "read_only": True},
        ],
        "entrypoints": [entry],
    }


def runner_closure_receipt(plan):
    bindings = []
    for finding_id in ("P1-A", "P1-B"):
        binding = {
            "finding_id": finding_id,
            "counterexample_id": "CE-GATE",
            "executable_counterexample_id": "NF-001",
            "counterexample_sha256": (
                "a" * 64 if finding_id == "P1-A" else "b" * 64
            ),
            "gate_id": "G-T",
            "stage": "targeted",
            "evidence_row_id": "EVID-G-T-EP",
            "entrypoint_id": "EP",
            "binding_sha256": "",
        }
        binding["binding_sha256"] = canonical_sha256(binding)
        bindings.append(binding)
    receipt = {
        "schema": "closure-binding-receipt.v16",
        "mission_id": "V16-PRODUCTIVITY",
        "compiled_plan_sha256": canonical_sha256(plan),
        "closure_plan_sha256": "c" * 64,
        "closure_plan_file_sha256": "d" * 64,
        "dispatch_transcript_file_sha256": "e" * 64,
        "normalized_source_artifacts": [
            {
                "audit_id": "SPARK-A",
                "artifact_path": "codex/v16/contracts/spark_result_A.v16.json",
                "artifact_sha256": "f" * 64,
            },
        ],
        "finding_count": len(bindings),
        "bindings": bindings,
        "receipt_sha256": "",
    }
    receipt["receipt_sha256"] = canonical_sha256(receipt)
    return receipt


class EvidenceTests(unittest.TestCase):
    def test_exact_arithmetic_and_bool_int(self):
        self.assertEqual(validate_counts({"total": 2, "ran": 2, "passed": 2, "failed": 0, "skipped": 0, "xfail": 0, "unknown": 0})["total"], 2)
        with self.assertRaises(EvidenceError):
            validate_counts({"total": True, "ran": 1, "passed": 1, "failed": 0, "skipped": 0})
        with self.assertRaises(EvidenceError):
            validate_counts({"total": 1, "ran": 1, "passed": 1, "failed": 0, "skipped": 0, "unknown": 1})
        with self.assertRaises(EvidenceError):
            validate_counts({"total": 1, "ran": 0, "passed": 0, "failed": 0, "skipped": 1, "xfail": 0, "unknown": 0})
        with self.assertRaises(EvidenceError):
            validate_counts({"total": 1, "ran": 1, "passed": 1, "failed": 0, "skipped": 0, "xfail": 1, "unknown": 0})

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

    def test_non_git_envelope_binds_dirty_snapshot_and_rejects_stale_snapshot(self):
        current_snapshot = "c" * 64
        stale_snapshot = "d" * 64
        current = row(
            identity_mode="non-git-snapshot",
            snapshot_sha256=current_snapshot,
            dirty=True,
        )
        envelope = build_envelope(
            "V16-PRODUCTIVITY",
            HEAD,
            TREE,
            [current],
            generated_at="2026-07-31T00:00:01Z",
            identity_mode="non-git-snapshot",
            snapshot_sha256=current_snapshot,
            clean=False,
        )
        checked = validate_envelope(
            envelope,
            expected_head=HEAD,
            expected_tree=TREE,
            expected_identity_mode="non-git-snapshot",
            expected_snapshot_sha256=current_snapshot,
        )
        self.assertFalse(checked["clean"])
        self.assertTrue(checked["rows"][0]["dirty"])
        self.assertEqual(checked["rows"][0]["snapshot_sha256"], current_snapshot)
        with self.assertRaisesRegex(EvidenceError, "snapshot mismatch"):
            validate_envelope(
                envelope,
                expected_head=HEAD,
                expected_tree=TREE,
                expected_identity_mode="non-git-snapshot",
                expected_snapshot_sha256=stale_snapshot,
            )

    def test_decision_deny_exit_zero_and_missing_log(self):
        denied = row(decision="deny")
        with self.assertRaises(EvidenceError):
            validate_row(denied)
        with tempfile.TemporaryDirectory() as tmp:
            missing = row(); missing["log_path"] = "missing.log"
            with self.assertRaises(EvidenceError):
                validate_row(missing, log_root=pathlib.Path(tmp))
            path = pathlib.Path(tmp) / "good.log"; path.write_bytes(b"x")
            mismatched = row(); mismatched["log_path"] = "good.log"
            with self.assertRaises(EvidenceError):
                validate_row(mismatched, log_root=pathlib.Path(tmp))

    def test_row_total_must_equal_matching_acceptance_denominator(self):
        plan = {
            "gates": [{"id": "G-TARGETED", "stage": "targeted", "entrypoint_ids": ["EP"]}],
            "entrypoints": [{"id": "EP", "argv": ["python3", "-c", "pass"], "cwd": "."}],
            "acceptance": [{"gate_id": "G-TARGETED", "entrypoint_id": "EP", "denominator": 2}],
        }
        candidate = row()
        candidate["entrypoint_id"] = "EP"
        candidate["command"][0] = pathlib.Path(sys.executable).resolve().as_posix()
        with self.assertRaisesRegex(EvidenceError, "denominator"):
            validate_row(candidate, plan=plan)

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
            self.assertEqual(result["results"][0]["rows"][0]["log_modes"], [0o600, 0o600])

    def test_runner_uses_only_final_jsonl_record_after_setup_receipts(self):
        head = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True,
        ).strip()
        setup = json.dumps({"schema": "installer-result.v16", "files": 44})
        checker = json.dumps({
            "schema": "checker-result.v16",
            "total": 23,
            "ran": 23,
            "passed": 23,
            "failed": 0,
            "skipped": 0,
            "xfail": 0,
            "unknown": 0,
        })
        plan = {
            "gate_order": ["G-TARGETED"],
            "gates": [{
                "id": "G-TARGETED",
                "stage": "targeted",
                "depends_on": [],
                "entrypoint_ids": ["EP"],
                "blocking": True,
                "reusable": False,
            }],
            "entrypoints": [{
                "id": "EP",
                "argv": ["python3", "-c", f"print({setup!r}); print({checker!r})"],
                "cwd": ".",
                "env": {},
                "timeout_sec": 5,
                "stop_conditions": ["timeout"],
            }],
        }
        with tempfile.TemporaryDirectory() as tmp:
            result = GateRunner(ROOT, plan, tmp).run_plan(expected_head=head)
        gate = result["results"][0]
        self.assertEqual(gate["decision"], "allow")
        self.assertEqual(gate["rows"][0]["counts"]["total"], 23)

        invalid_tail = copy.deepcopy(plan)
        invalid_tail["entrypoints"][0]["argv"] = [
            "python3", "-c",
            f"print({checker!r}); print('{{\"status\":\"RED\"}}')",
        ]
        with tempfile.TemporaryDirectory() as tmp:
            denied = GateRunner(ROOT, invalid_tail, tmp).run_plan(
                expected_head=head,
            )
        self.assertEqual(denied["results"][0]["decision"], "deny")

    def test_runner_and_evidence_producer_bind_preexecution_closure_receipt(self):
        head = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True,
        ).strip()
        tree = subprocess.check_output(
            ["git", "rev-parse", "HEAD^{tree}"], cwd=ROOT, text=True,
        ).strip()
        plan = fast_plan()
        receipt = runner_closure_receipt(plan)
        expected_bindings = sorted(
            binding["binding_sha256"] for binding in receipt["bindings"]
        )
        with tempfile.TemporaryDirectory() as tmp:
            artifact_root = pathlib.Path(tmp)
            snapshot = content_snapshot(ROOT, "worktree")
            result = GateRunner(
                ROOT,
                plan,
                artifact_root,
                closure_binding_receipt=receipt,
            ).run_gate(
                "G-T",
                expected_head=head,
                force=True,
                snapshot_mode="worktree",
                expected_snapshot=snapshot,
            )
            self.assertEqual(
                result["closure_binding_receipt_sha256"],
                receipt["receipt_sha256"],
            )
            self.assertEqual(
                result["rows"][0]["closure_binding_sha256s"],
                expected_bindings,
            )
            validate_gate_result(
                result,
                expected_head=head,
                expected_snapshot=snapshot,
                artifact_root=artifact_root,
                plan=plan,
                closure_binding_receipt=receipt,
            )
            rows = _evidence_rows(
                {"schema": "gate-run.v16", "expected_head": head, "results": [result]},
                head=head,
                tree=tree,
                identity_mode="non-git-snapshot",
                snapshot_sha256=snapshot,
                closure_binding_receipt=receipt,
            )
            envelope = build_envelope(
                "V16-PRODUCTIVITY",
                head,
                tree,
                rows,
                generated_at=max(row["ended_at"] for row in rows),
                identity_mode="non-git-snapshot",
                snapshot_sha256=snapshot,
                clean=not any(row["dirty"] for row in rows),
                log_root=artifact_root,
                plan=plan,
                closure_binding_receipt=receipt,
            )
            self.assertEqual(
                envelope["rows"][0]["closure_binding_sha256s"],
                expected_bindings,
            )

            missing = copy.deepcopy(result)
            missing["rows"][0]["closure_binding_sha256s"].pop()
            with self.assertRaisesRegex(GateRunError, "closure binding receipt"):
                validate_gate_result(
                    missing,
                    expected_head=head,
                    expected_snapshot=snapshot,
                    artifact_root=artifact_root,
                    plan=plan,
                    closure_binding_receipt=receipt,
                )
            with self.assertRaisesRegex(GateRunError, "missing/additional"):
                validate_gate_result(
                    result,
                    expected_head=head,
                    expected_snapshot=snapshot,
                    artifact_root=artifact_root,
                    plan=plan,
                )

    def test_compiled_gate_entrypoint_denominator_is_exact_and_ordered(self):
        head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
        plan = fast_plan()
        plan["gates"] = plan["gates"][:1]
        plan["entrypoints"][0]["id"] = "EP-A"
        second = copy.deepcopy(plan["entrypoints"][0])
        second["id"] = "EP-B"
        plan["entrypoints"].append(second)
        plan["gates"][0]["entrypoint_ids"] = ["EP-A", "EP-B"]

        with tempfile.TemporaryDirectory() as tmp:
            snapshot = content_snapshot(ROOT, "worktree")
            result = GateRunner(ROOT, plan, tmp).run_gate(
                "G-T",
                expected_head=head,
                force=True,
                snapshot_mode="worktree",
                expected_snapshot=snapshot,
            )
            self.assertEqual(result["decision"], "allow")
            self.assertEqual([row["entrypoint_id"] for row in result["rows"]], ["EP-A", "EP-B"])
            validate_gate_result(
                result,
                expected_head=head,
                expected_snapshot=snapshot,
                artifact_root=pathlib.Path(tmp),
                plan=plan,
            )

            denied_plan = copy.deepcopy(plan)
            denied_plan["entrypoints"][0]["argv"] = [
                "python3", "-c",
                "print('{\"total\":1,\"ran\":1,\"passed\":0,\"failed\":1,\"skipped\":0,\"xfail\":0,\"unknown\":0}')",
            ]
            with tempfile.TemporaryDirectory() as denied_tmp:
                denied = GateRunner(ROOT, denied_plan, denied_tmp).run_gate(
                    "G-T",
                    expected_head=head,
                    force=True,
                    snapshot_mode="worktree",
                    expected_snapshot=snapshot,
                )
                self.assertEqual(denied["decision"], "deny")
                self.assertEqual([row["entrypoint_id"] for row in denied["rows"]], ["EP-A", "EP-B"])
                validate_gate_result(
                    denied,
                    expected_head=head,
                    expected_snapshot=snapshot,
                    artifact_root=pathlib.Path(denied_tmp),
                    plan=denied_plan,
                )

            mutations = (
                ("missing EP-B", lambda rows: rows.pop()),
                ("duplicate EP-A", lambda rows: rows.__setitem__(1, {**rows[1], "entrypoint_id": "EP-A"})),
                ("extra EP-C", lambda rows: rows.__setitem__(1, {**rows[1], "entrypoint_id": "EP-C"})),
                ("wrong order", lambda rows: rows.reverse()),
            )
            for label, mutate in mutations:
                candidate = copy.deepcopy(result)
                mutate(candidate["rows"])
                with self.assertRaisesRegex(GateRunError, "denominator|entrypoint"):
                    validate_gate_result(
                        candidate,
                        expected_head=head,
                        expected_snapshot=snapshot,
                        artifact_root=pathlib.Path(tmp),
                        plan=plan,
                    )

            dependent_plan = copy.deepcopy(plan)
            dependent_plan["gates"].append({
                "id": "G-F",
                "stage": "full",
                "depends_on": ["G-T"],
                "entrypoint_ids": ["EP-A", "EP-B"],
                "blocking": True,
                "reusable": False,
            })
            skipped_runner = GateRunner(ROOT, dependent_plan, tmp)
            skipped_runner.results["G-T"] = {"decision": "deny"}
            skipped = skipped_runner.run_gate(
                "G-F",
                expected_head=head,
                force=True,
                snapshot_mode="worktree",
                expected_snapshot=snapshot,
            )
            self.assertEqual(skipped["decision"], "skipped")
            self.assertEqual(skipped["rows"], [])
            validate_gate_result(
                skipped,
                expected_head=head,
                expected_snapshot=snapshot,
                artifact_root=pathlib.Path(tmp),
                plan=dependent_plan,
            )

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


class FastCostEngineTests(unittest.TestCase):
    def test_compiler_profiles_are_staged(self):
        mission = json.loads((ROOT / "codex/v16/fixtures/mission.valid.json").read_text())
        plan = compile_mission(mission)
        self.assertEqual(gate_ids_for_tier(plan, "FAST"), ["G-TARGETED"])
        self.assertEqual(gate_ids_for_tier(plan, "CANDIDATE"), ["G-TARGETED", "G-FULL"])
        self.assertEqual(gate_ids_for_tier(plan, "FINAL"), ["G-TARGETED", "G-FULL", "G-FRESH"])

    def test_fast_dirty_worktree_and_content_cache(self):
        head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
        with tempfile.TemporaryDirectory() as tmp:
            runner = GateRunner(ROOT, fast_plan(), tmp)
            snapshot = content_snapshot(ROOT, "worktree")
            first = runner.run_tier("FAST", expected_head=head, snapshot_mode="worktree")
            self.assertEqual(first["results"][0]["decision"], "allow")
            validate_gate_result(first["results"][0], expected_head=head, expected_snapshot=snapshot, artifact_root=pathlib.Path(tmp))
            with self.assertRaises(GateRunError):
                validate_gate_result(first["results"][0], expected_head=head, expected_snapshot="0" * 64, artifact_root=pathlib.Path(tmp))
            runner.results.clear()
            reused = runner.run_tier("FAST", expected_head=head, snapshot_mode="worktree")
            self.assertEqual(reused["results"][0]["decision"], "reused")
            log_path = pathlib.Path(tmp) / reused["results"][0]["rows"][0]["log_paths"][0]
            log_path.write_text("corrupted", encoding="utf-8")
            runner.results.clear()
            rerun = runner.run_tier("FAST", expected_head=head, snapshot_mode="worktree")
            self.assertEqual(rerun["results"][0]["decision"], "allow")

    def test_fast_rejects_content_mutation_during_gate(self):
        with tempfile.TemporaryDirectory() as repo_tmp, tempfile.TemporaryDirectory() as artifact_tmp:
            repo = pathlib.Path(repo_tmp)
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.email", "fixture@example.invalid"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.name", "Fixture"], cwd=repo, check=True)
            tracked = repo / "tracked.txt"
            tracked.write_text("before\n", encoding="utf-8")
            subprocess.run(["git", "add", "tracked.txt"], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-qm", "fixture"], cwd=repo, check=True)
            plan = fast_plan()
            plan["entrypoints"][0]["argv"] = [
                "python3", "-c",
                "from pathlib import Path; Path('tracked.txt').write_text('after\\n'); print('{\"total\":1,\"ran\":1,\"passed\":1,\"failed\":0,\"skipped\":0,\"xfail\":0,\"unknown\":0}')",
            ]
            head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
            result = GateRunner(repo, plan, artifact_tmp).run_tier("FAST", expected_head=head, snapshot_mode="worktree")
            gate = result["results"][0]
            self.assertEqual(gate["decision"], "deny")
            self.assertIn("CONTENT_SNAPSHOT_DRIFT", gate["reason"])
            validate_gate_result(
                gate,
                expected_head=head,
                expected_snapshot=gate["snapshot_sha256"],
                artifact_root=pathlib.Path(artifact_tmp),
            )

    def test_artifacts_must_be_outside_repository(self):
        with tempfile.TemporaryDirectory() as repo_tmp:
            repo = pathlib.Path(repo_tmp)
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            with self.assertRaisesRegex(GateRunError, "outside the repository"):
                GateRunner(repo, fast_plan(), repo / ".artifacts")

    def test_staged_snapshot_excludes_unstaged_and_untracked_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = pathlib.Path(tmp)
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            path = repo / "tracked.txt"
            path.write_text("staged\n", encoding="utf-8")
            subprocess.run(["git", "add", "tracked.txt"], cwd=repo, check=True)
            staged = content_snapshot(repo, "staged")
            path.write_text("unstaged\n", encoding="utf-8")
            (repo / "untracked.txt").write_text("untracked\n", encoding="utf-8")
            self.assertEqual(content_snapshot(repo, "staged"), staged)
            subprocess.run(["git", "add", "tracked.txt"], cwd=repo, check=True)
            self.assertNotEqual(content_snapshot(repo, "staged"), staged)

    def test_staged_fast_executes_materialized_index_not_worktree(self):
        with tempfile.TemporaryDirectory() as repo_tmp, tempfile.TemporaryDirectory() as artifact_tmp:
            repo = pathlib.Path(repo_tmp)
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.email", "fixture@example.invalid"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.name", "Fixture"], cwd=repo, check=True)
            tracked = repo / "tracked.txt"
            tracked.write_text("base\n", encoding="utf-8")
            subprocess.run(["git", "add", "tracked.txt"], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-qm", "fixture"], cwd=repo, check=True)
            tracked.write_text("staged\n", encoding="utf-8")
            subprocess.run(["git", "add", "tracked.txt"], cwd=repo, check=True)
            tracked.write_text("unstaged\n", encoding="utf-8")
            plan = fast_plan()
            plan["entrypoints"][0]["argv"] = [
                "python3", "-c",
                "from pathlib import Path; ok=Path('tracked.txt').read_text() == 'staged\\n'; print('{\"total\":1,\"ran\":1,\"passed\":%d,\"failed\":%d,\"skipped\":0,\"xfail\":0,\"unknown\":0}' % (ok, not ok))",
            ]
            head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
            gate = GateRunner(repo, plan, artifact_tmp).run_tier("FAST", expected_head=head, snapshot_mode="staged")["results"][0]
            self.assertEqual(gate["decision"], "allow")
            self.assertEqual(tracked.read_text(encoding="utf-8"), "unstaged\n")

    def test_staged_fast_rejects_materialized_content_mutation(self):
        with tempfile.TemporaryDirectory() as repo_tmp, tempfile.TemporaryDirectory() as artifact_tmp:
            repo = pathlib.Path(repo_tmp)
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.email", "fixture@example.invalid"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.name", "Fixture"], cwd=repo, check=True)
            tracked = repo / "tracked.txt"
            tracked.write_text("base\n", encoding="utf-8")
            subprocess.run(["git", "add", "tracked.txt"], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-qm", "fixture"], cwd=repo, check=True)
            tracked.write_text("staged\n", encoding="utf-8")
            subprocess.run(["git", "add", "tracked.txt"], cwd=repo, check=True)
            tracked.write_text("unstaged\n", encoding="utf-8")
            plan = fast_plan()
            plan["entrypoints"][0]["argv"] = [
                "python3", "-c",
                "from pathlib import Path; Path('tracked.txt').write_text('mutated\\n'); print('{\"total\":1,\"ran\":1,\"passed\":1,\"failed\":0,\"skipped\":0,\"xfail\":0,\"unknown\":0}')",
            ]
            head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
            gate = GateRunner(repo, plan, artifact_tmp).run_tier("FAST", expected_head=head, snapshot_mode="staged")["results"][0]
            self.assertEqual(gate["decision"], "deny")
            self.assertIn("MATERIALIZED_CONTENT_SNAPSHOT_DRIFT", gate["reason"])
            self.assertEqual(gate["rows"][0]["decision"], "deny")
            self.assertIn("materialized content snapshot drift", gate["rows"][0]["identity_error"])
            self.assertEqual(tracked.read_text(encoding="utf-8"), "unstaged\n")

    def test_compiled_read_only_declarations_reach_bounded_parallel_path(self):
        head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
        mission = json.loads((ROOT / "codex/v16/fixtures/mission.valid.json").read_text())
        mission["scope"]["exact_head"] = head
        mission["entrypoints"][0]["read_only"] = True
        second = copy.deepcopy(mission["entrypoints"][0])
        second["id"] = "EP-UNIT-SECOND"
        mission["entrypoints"].append(second)
        targeted = next(gate for gate in mission["gates"] if gate["stage"] == "targeted")
        targeted["read_only"] = True
        targeted["entrypoint_ids"].append(second["id"])
        plan = compile_mission(mission)
        compiled_targeted = next(gate for gate in plan["gates"] if gate["stage"] == "targeted")
        self.assertIs(compiled_targeted["read_only"], True)
        self.assertTrue(all(entry["read_only"] for entry in plan["entrypoints"]))
        with tempfile.TemporaryDirectory() as tmp:
            runner = GateRunner(ROOT, plan, tmp, max_workers=2)
            with mock.patch("codex.v16.runner.ThreadPoolExecutor", wraps=ThreadPoolExecutor) as executor:
                result = runner.run_tier("FAST", expected_head=head, snapshot_mode="worktree")
        self.assertTrue(executor.called)
        self.assertEqual(result["results"][0]["decision"], "allow")
        self.assertEqual(len(result["results"][0]["rows"]), 2)

        with tempfile.TemporaryDirectory() as tmp:
            staged_runner = GateRunner(ROOT, plan, tmp, max_workers=2)
            with mock.patch(
                "codex.v16.runner.ThreadPoolExecutor", wraps=ThreadPoolExecutor,
            ) as staged_executor:
                staged_result = staged_runner.run_tier(
                    "FAST", expected_head=head, snapshot_mode="staged",
                )
            staged_executor.assert_not_called()
        self.assertEqual(staged_result["results"][0]["decision"], "allow")
        self.assertEqual(len(staged_result["results"][0]["rows"]), 2)

    def test_explicit_read_only_entrypoints_can_share_a_bounded_gate(self):
        head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
        plan = fast_plan()
        second = copy.deepcopy(plan["entrypoints"][0])
        second["id"] = "EP-SECOND"
        plan["entrypoints"].append(second)
        plan["gates"][0]["entrypoint_ids"].append("EP-SECOND")
        with tempfile.TemporaryDirectory() as tmp:
            result = GateRunner(ROOT, plan, tmp, max_workers=2).run_tier("FAST", expected_head=head, snapshot_mode="worktree")
        self.assertEqual(result["results"][0]["decision"], "allow")
        self.assertEqual(len(result["results"][0]["rows"]), 2)

        dependent = fast_plan()
        dependent["entrypoints"].append(second)
        dependent["gates"][1]["entrypoint_ids"].append("EP-SECOND")
        snapshot = content_snapshot(ROOT, "worktree")
        with tempfile.TemporaryDirectory() as tmp:
            runner = GateRunner(ROOT, dependent, tmp, max_workers=2)
            runner.results["G-T"] = {"decision": "allow"}
            full = runner.run_gate("G-F", expected_head=head, snapshot_mode="worktree", expected_snapshot=snapshot)
        self.assertEqual(full["decision"], "allow")
        self.assertEqual(len(full["rows"]), 2)

    def test_cache_binds_effective_environment_and_nonreusable_never_head_reuses(self):
        head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
        plan = fast_plan()
        plan["entrypoints"][0]["env"] = {"TZ": "UTC"}
        with tempfile.TemporaryDirectory() as tmp:
            runner = GateRunner(ROOT, plan, tmp)
            first = runner.run_gate(
                "G-T", expected_head=head, snapshot_mode="worktree",
                env_overrides={"TZ": "UTC"},
            )
            second = runner.run_gate(
                "G-T", expected_head=head, snapshot_mode="worktree",
                env_overrides={"TZ": "Pacific/Kiritimati"},
            )
            third = runner.run_gate(
                "G-T", expected_head=head, snapshot_mode="worktree",
                env_overrides={"TZ": "Pacific/Kiritimati"},
            )
            self.assertEqual(first["decision"], "allow")
            self.assertEqual(second["decision"], "allow")
            self.assertEqual(third["decision"], "reused")

            frozen_plan = fast_plan()
            frozen_plan["entrypoints"][0]["env"] = {"TZ": "UTC"}
            frozen_plan["entrypoints"][0]["argv"] = [
                "python3", "-c",
                "import os; ok=os.environ.get('TZ') == 'UTC'; print('{\"total\":1,\"ran\":1,\"passed\":%d,\"failed\":%d,\"skipped\":0,\"xfail\":0,\"unknown\":0}' % (ok, not ok))",
            ]
            frozen_runner = GateRunner(ROOT, frozen_plan, tmp)
            mutable_overrides = {"TZ": "UTC"}
            original_cache_key = frozen_runner._cache_key

            def mutate_after_hash(*args, **kwargs):
                key = original_cache_key(*args, **kwargs)
                mutable_overrides["TZ"] = "Pacific/Kiritimati"
                return key

            with mock.patch.object(
                frozen_runner, "_cache_key", side_effect=mutate_after_hash,
            ):
                frozen = frozen_runner.run_gate(
                    "G-T", expected_head=head, snapshot_mode="worktree",
                    env_overrides=mutable_overrides,
                )
            self.assertEqual(mutable_overrides["TZ"], "Pacific/Kiritimati")
            self.assertEqual(frozen["decision"], "allow")

            runner.results["G-T"] = {"decision": "allow"}
            snapshot = content_snapshot(ROOT, "worktree")
            full = runner.run_gate(
                "G-F", expected_head=head, snapshot_mode="worktree",
                expected_snapshot=snapshot,
            )
            log = pathlib.Path(tmp) / full["rows"][0]["log_paths"][0]
            log.write_text("corrupt", encoding="utf-8")
            rerun = runner.run_gate(
                "G-F", expected_head=head, snapshot_mode="worktree",
                expected_snapshot=snapshot,
            )
            self.assertEqual(rerun["decision"], "allow")
            self.assertNotEqual(rerun["decision"], "reused")
            validate_gate_result(
                rerun, expected_head=head, expected_snapshot=snapshot,
                artifact_root=pathlib.Path(tmp),
            )

        with tempfile.TemporaryDirectory() as roots_tmp:
            roots = pathlib.Path(roots_tmp)
            clone_a = roots / "clone-a"
            clone_b = roots / "clone-b"
            shared_artifacts = roots / "shared-artifacts"
            subprocess.run(["git", "clone", "-q", str(ROOT), str(clone_a)], check=True)
            subprocess.run(["git", "clone", "-q", str(ROOT), str(clone_b)], check=True)
            shared_artifacts.mkdir()
            clone_head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=clone_a, text=True).strip()
            first_clone = GateRunner(clone_a, fast_plan(), shared_artifacts).run_tier(
                "FAST", expected_head=clone_head, snapshot_mode="worktree",
            )["results"][0]
            second_clone = GateRunner(clone_b, fast_plan(), shared_artifacts).run_tier(
                "FAST", expected_head=clone_head, snapshot_mode="worktree",
            )["results"][0]
            self.assertEqual(first_clone["decision"], "allow")
            self.assertEqual(second_clone["decision"], "allow")
            self.assertNotEqual(second_clone["decision"], "reused")

    def test_dynamic_authorized_routing_and_hard_budget(self):
        chosen = choose_model(
            task_kind="review", risk="high",
            authorized_models=["any-a", "any-b"],
            live_models={
                "any-a": {"available": True, "risks": ["high"], "token_cost_rank": 2},
                "any-b": {"available": True, "token_cost_rank": 1},
            },
            preferences={"any-a": {"review:high": 5}, "any-b": {"review:high": 1}},
        )
        self.assertEqual(chosen, "any-a")
        cheaper_tie = choose_model(
            task_kind="implementation", risk="medium",
            authorized_models=["any-a", "any-b"],
            live_models={
                "any-a": {"available": True, "token_cost_rank": 2},
                "any-b": {"available": True, "token_cost_rank": 1},
            },
        )
        self.assertEqual(cheaper_tie, "any-b")
        ledger = BudgetLedger({
            "max_model_calls": 2, "max_review_calls": 1,
            "max_parallel_agents": 1, "max_input_tokens": 4,
            "max_output_tokens": 6, "max_total_tokens": 10,
        })
        first = ledger.reserve(is_review=True, agents=1, input_tokens=2, output_tokens=3)
        ledger.settle(first, input_tokens=1, output_tokens=2)
        second = ledger.reserve(is_review=False, agents=1, input_tokens=2, output_tokens=3)
        with self.assertRaises(RoutingError):
            ledger.reserve(is_review=False)
        with self.assertRaises(RoutingError):
            ledger.reserve(is_review="yes")
        ledger.settle(second, input_tokens=2, output_tokens=3, counts_available=False)
        self.assertEqual(ledger.usage()["charged_total_tokens"], 8)
        self.assertEqual(ledger.usage()["token_count_basis"], "upper-bound-mixed")
        self.assertEqual(ledger.usage()["peak_parallel_agents"], 1)
        with self.assertRaises(RoutingError):
            choose_model(
                task_kind="review", risk="high",
                authorized_models="any-a",
                live_models={"any-a": {"available": True}},
            )
        with self.assertRaises(RoutingError):
            choose_model(
                task_kind="review", risk="high",
                authorized_models=["any-a"],
                live_models={"any-a": {"available": True, "risks": "high"}},
            )
        with self.assertRaises(RoutingError):
            BudgetLedger([])

    def test_budget_reservations_are_thread_safe(self):
        ledger = BudgetLedger({
            "max_model_calls": 3, "max_review_calls": 0,
            "max_parallel_agents": 3, "max_input_tokens": 3,
            "max_output_tokens": 3, "max_total_tokens": 6,
        })

        def attempt(_):
            try:
                return ledger.reserve(
                    is_review=False, agents=1,
                    input_tokens=1, output_tokens=1,
                )
            except RoutingError:
                return None

        with ThreadPoolExecutor(max_workers=10) as pool:
            reservations = [value for value in pool.map(attempt, range(10)) if value]
        self.assertEqual(len(reservations), 3)
        self.assertEqual(ledger.usage()["peak_parallel_agents"], 3)
        for reservation in reservations:
            ledger.settle(reservation, input_tokens=1, output_tokens=1)
        self.assertEqual(ledger.usage()["charged_total_tokens"], 6)
        self.assertEqual(ledger.usage()["outstanding_calls"], 0)
        self.assertIsNone(ledger.usage()["usd_cost"])

    def test_unavailable_runtime_metrics_stay_unavailable(self):
        observed = collect_runtime_metrics({"ttft_sec": None, "decode_sec": 0.2, "tool_sec": None, "quality": None})
        self.assertIsNone(observed["ttft_sec"])
        self.assertIsNone(observed["tool_sec"])
        self.assertIsNone(observed["quality"])


if __name__ == "__main__":
    unittest.main()

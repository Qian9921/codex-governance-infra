import copy
import json
import pathlib
import tempfile
import unittest

from codex.v16.compiler import CompileError, compile_mission
from codex.v16.contracts import ContractError
from codex.v16.state import ReadinessError, StateStore, initial_state, transition

ROOT = pathlib.Path(__file__).parents[2]
FIXTURE = ROOT / "codex/v16/fixtures/mission.valid.json"
BASE = "e18439c8dfe01d901895efd09b8b73b6842327a9"
TREE = "1de79a7c48e6c66f167be54ca9cf387310149f80"
CANDIDATE = "0123456789abcdef0123456789abcdef01234567"


class CompilerStateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mission = json.loads(FIXTURE.read_text(encoding="utf-8"))

    def test_compiled_plan_is_deterministic_and_nonexecuting(self):
        plan = compile_mission(self.mission)
        self.assertEqual(plan["schema"], "compiled-plan.v16")
        self.assertEqual(plan["gate_order"], ["G-TARGETED", "G-FULL", "G-FRESH"])
        self.assertFalse(plan["execution"]["shell"])

    def test_stage_jump_rejected(self):
        mission = copy.deepcopy(self.mission)
        mission["gates"][1]["depends_on"] = []
        with self.assertRaises((CompileError, ContractError)):
            compile_mission(mission)

    def test_unknown_dependency_rejected(self):
        mission = copy.deepcopy(self.mission)
        mission["gates"][0]["depends_on"] = ["G-NOPE"]
        with self.assertRaises((CompileError, ContractError)):
            compile_mission(mission)

    def test_shell_forms_rejected(self):
        mission = copy.deepcopy(self.mission)
        mission["entrypoints"][0]["argv"] = ["sh", "-c", "echo unsafe"]
        with self.assertRaises(CompileError):
            compile_mission(mission)
        mission["entrypoints"][0]["argv"] = ["echo unsafe"]
        with self.assertRaises(CompileError):
            compile_mission(mission)

    def test_readiness_no_jump_or_backdating(self):
        state = initial_state("V16-PRODUCTIVITY", BASE, TREE)
        frozen = transition(state, "COUNTEREXAMPLES_FROZEN", base_sha=BASE, head_sha=BASE, tree_sha=TREE, counterexample_ids=["CE-SCHEMA", "CE-GATE", "CE-EVIDENCE"], updated_at="9999-12-31T00:00:01Z")
        with self.assertRaises(ReadinessError):
            transition(frozen, "LOCAL_READY", base_sha=BASE, head_sha=CANDIDATE, evidence_ids=["E1"], updated_at="9999-12-31T00:00:02Z")
        with self.assertRaises(ReadinessError):
            transition(frozen, "BASELINE_REPRODUCED", base_sha=BASE, head_sha=BASE, red_counterexamples=["CE-SCHEMA"], updated_at="0001-01-01T00:00:00Z")

    def test_baseline_and_spark_disposition_requirements(self):
        state = initial_state("V16-PRODUCTIVITY", BASE, TREE)
        frozen = transition(state, "COUNTEREXAMPLES_FROZEN", base_sha=BASE, head_sha=BASE, tree_sha=TREE, counterexample_ids=["CE-SCHEMA"], updated_at="9999-12-31T00:00:01Z")
        baseline = transition(frozen, "BASELINE_REPRODUCED", base_sha=BASE, head_sha=BASE, red_counterexamples=["CE-SCHEMA"], updated_at="9999-12-31T00:00:02Z")
        implementing = transition(baseline, "IMPLEMENTING", base_sha=BASE, head_sha=CANDIDATE, updated_at="9999-12-31T00:00:03Z")
        with self.assertRaises(ReadinessError):
            transition(implementing, "INNER_AUDIT_COMPLETE", base_sha=BASE, head_sha=CANDIDATE, spark_findings=["F1"], dispositions={}, updated_at="9999-12-31T00:00:04Z")
        audited = transition(implementing, "INNER_AUDIT_COMPLETE", base_sha=BASE, head_sha=CANDIDATE, spark_findings=["F1"], dispositions={"F1": "FIXED"}, evidence_ids=["E1"], updated_at="9999-12-31T00:00:04Z")
        self.assertEqual(audited["state"], "INNER_AUDIT_COMPLETE")
        with self.assertRaises(ReadinessError):
            transition(audited, "LOCAL_READY", base_sha=BASE, head_sha="fedcba9876543210fedcba9876543210fedcba98", evidence_ids=["E2"], updated_at="9999-12-31T00:00:05Z")

    def test_state_store_atomic_and_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = StateStore(tmp)
            state = initial_state("V16-PRODUCTIVITY", BASE, TREE)
            digest = store.save(state)
            self.assertEqual(len(digest), 64)
            self.assertEqual(store.load()["state"], "DRAFT")


if __name__ == "__main__":
    unittest.main()

import copy
import hashlib
import multiprocessing
import tempfile
import unittest
from unittest import mock

from codex.v16.tool_runtime import (
    ROUTES,
    ToolRuntimeError,
    ValidatedToolUsage,
    begin_child_turn_state,
    begin_turn_state,
    build_enforcement_report,
    compile_task_contract,
    load_expected_tool_calls,
    load_current_intake,
    load_tool_call_binding,
    load_tool_call_intake,
    load_turn_contract,
    persist_task_contract,
    record_expected_tool_call,
    validate_and_bind_usage_report,
    validate_enforcement_report,
    validate_task_contract,
)


TASK_SHA = hashlib.sha256(b"task").hexdigest()
SHAPE_SHA = hashlib.sha256(b"task-shape").hexdigest()


def record_worker(state_dir, barrier, results, call_id):
    barrier.wait()
    results.put(record_expected_tool_call(
        session_id="parallel-session", turn_id="parallel-turn",
        tool_use_id=call_id, route_code="rtk", state_dir=state_dir,
    ))


def signals(**changes):
    value = {
        "unknown_semantic_entrypoint": False,
        "similar_implementation": False,
        "known_symbol_or_call": False,
        "dependency_or_blast_radius": False,
        "exact_text_error_config_log": False,
        "shell_output_for_model": False,
        "machine_exact_only": False,
    }
    value.update(changes)
    return value


def contract(**changes):
    return compile_task_contract(
        task_id_sha256=TASK_SHA,
        classifier_identity="tool-intake.v16",
        task_shape_sha256=SHAPE_SHA,
        repository_work=True,
        signals=signals(**changes),
    )


def usage(*, tools=(), routes=(), compliant=True, equivalent=True):
    return {
        "schema": "tool-usage.v16",
        "task_id_sha256": TASK_SHA,
        "routing_compliant": compliant,
        "coverage_equivalent": equivalent,
        "calls": [
            {"tool": tool, "status": "success"}
            for tool in tools
        ],
        "routes": list(routes),
    }


def bound_usage(test, value):
    with mock.patch(
        "codex.v16.tool_runtime.validate_usage_report", return_value=value
    ) as validator:
        bound = validate_and_bind_usage_report(value, authority="caller-bound")
    validator.assert_called_once_with(value, authority="caller-bound")
    test.assertEqual(bound.report, value)
    return bound


class ToolRuntimeTests(unittest.TestCase):
    def test_complete_semantic_contract_requires_only_semble(self):
        value = contract(unknown_semantic_entrypoint=True)
        required = [
            row for row in value["routes"] if row["applicability"] == "required"
        ]
        self.assertEqual(required, [{
            "route": "semantic_discovery",
            "tool": "semble",
            "applicability": "required",
            "reason_code": "TASK_REQUIRES_SEMANTIC_DISCOVERY",
        }])
        self.assertEqual(value["denominator"], 4)
        self.assertEqual(value["required_count"], 1)
        self.assertEqual(validate_task_contract(value), value)

    def test_structural_and_semantic_are_independently_applicable(self):
        value = contract(
            similar_implementation=True,
            dependency_or_blast_radius=True,
        )
        self.assertEqual(
            [row["route"] for row in value["routes"] if row["applicability"] == "required"],
            ["semantic_discovery", "structural_analysis"],
        )

    def test_contract_cannot_omit_signal_or_forge_not_applicable(self):
        missing = signals(unknown_semantic_entrypoint=True)
        missing.pop("unknown_semantic_entrypoint")
        with self.assertRaises(ToolRuntimeError):
            compile_task_contract(
                task_id_sha256=TASK_SHA,
                classifier_identity="tool-intake.v16",
                task_shape_sha256=SHAPE_SHA,
                repository_work=True,
                signals=missing,
            )
        forged = contract(unknown_semantic_entrypoint=True)
        forged = copy.deepcopy(forged)
        forged["routes"][0]["applicability"] = "not_applicable"
        with self.assertRaises(ToolRuntimeError):
            validate_task_contract(forged)

    def test_repo_work_must_have_route_or_machine_exact_declaration(self):
        with self.assertRaises(ToolRuntimeError):
            contract()
        value = contract(machine_exact_only=True)
        self.assertEqual(value["required_count"], 0)
        self.assertEqual(value["not_applicable_count"], len(ROUTES))

    def test_non_repo_task_cannot_claim_repository_signals(self):
        with self.assertRaises(ToolRuntimeError):
            compile_task_contract(
                task_id_sha256=TASK_SHA,
                classifier_identity="tool-intake.v16",
                task_shape_sha256=SHAPE_SHA,
                repository_work=False,
                signals=signals(exact_text_error_config_log=True),
            )

    def test_required_preferred_tool_is_completion_gate(self):
        task = contract(unknown_semantic_entrypoint=True)
        evidence = bound_usage(self, usage(tools=("semble",)))
        report = build_enforcement_report(task, evidence)
        self.assertEqual(report["status"], "compliant")
        self.assertTrue(report["completion_eligible"])
        self.assertEqual(report["counts"]["passed"], 4)
        self.assertEqual(validate_enforcement_report(report, task, evidence), report)

    def test_missing_required_tool_is_blocking_not_a_skip(self):
        task = contract(known_symbol_or_call=True)
        report = build_enforcement_report(task, bound_usage(self, usage()))
        self.assertEqual(report["status"], "blocked")
        self.assertFalse(report["completion_eligible"])
        self.assertEqual(
            report["violations"],
            ["REQUIRED_ROUTE_NOT_USED:structural_analysis"],
        )
        self.assertEqual(report["counts"]["unknown"], 0)

    def test_verified_fallback_is_visible_but_not_completion_equivalent(self):
        task = contract(unknown_semantic_entrypoint=True)
        report = build_enforcement_report(
            task,
            bound_usage(self, usage(
                tools=("rg",),
                routes=({"preferred_tool": "semble", "fallback": True},),
                equivalent=False,
            )),
        )
        self.assertEqual(report["status"], "degraded")
        self.assertFalse(report["completion_eligible"])
        self.assertEqual(
            report["violations"],
            ["PREFERRED_TOOL_NOT_SUCCESSFUL:semantic_discovery"],
        )

    def test_noncompliant_usage_cannot_become_degraded_completion(self):
        task = contract(exact_text_error_config_log=True)
        report = build_enforcement_report(
            task,
            bound_usage(self, usage(tools=("rg",), compliant=False, equivalent=False)),
        )
        self.assertEqual(report["status"], "blocked")
        self.assertFalse(report["completion_eligible"])
        self.assertIn("TOOL_USAGE_NOT_ROUTING_COMPLIANT", report["violations"])

    def test_validator_rejects_a_self_consistent_but_forged_green_report(self):
        task = contract(known_symbol_or_call=True)
        actual_usage = usage()
        bound = bound_usage(self, actual_usage)
        forged = build_enforcement_report(task, bound)
        forged = copy.deepcopy(forged)
        forged["status"] = "compliant"
        forged["completion_eligible"] = True
        forged["satisfied_routes"] = ["structural_analysis"]
        forged["violations"] = []
        forged["counts"]["passed"] = 4
        forged["counts"]["failed"] = 0
        with self.assertRaises(ToolRuntimeError):
            validate_enforcement_report(forged, task, bound)

    def test_plain_or_self_constructed_usage_cannot_enter_enforcement(self):
        task = contract(known_symbol_or_call=True)
        with self.assertRaises(ToolRuntimeError):
            build_enforcement_report(task, usage())
        with self.assertRaises(ToolRuntimeError):
            ValidatedToolUsage(usage(), object())

    def test_turn_contract_is_prompt_bound_immutable_and_activity_bound(self):
        with tempfile.TemporaryDirectory() as directory:
            intake = begin_turn_state(
                session_id="session", turn_id="turn", prompt="inspect symbol",
                state_dir=directory,
            )
            self.assertRegex(intake["intake_id_sha256"], r"^[0-9a-f]{64}$")
            value = compile_task_contract(
                task_id_sha256=intake["task_id_sha256"],
                classifier_identity="test-classifier",
                task_shape_sha256=intake["task_shape_sha256"],
                repository_work=True,
                signals=signals(known_symbol_or_call=True),
            )
            self.assertEqual(
                persist_task_contract(
                    value,
                    intake_id_sha256=intake["intake_id_sha256"],
                    state_dir=directory,
                ),
                value,
            )
            self.assertEqual(
                load_turn_contract(
                    session_id="session", turn_id="turn", state_dir=directory
                ),
                value,
            )
            changed = compile_task_contract(
                task_id_sha256=intake["task_id_sha256"],
                classifier_identity="test-classifier",
                task_shape_sha256=intake["task_shape_sha256"],
                repository_work=True,
                signals=signals(unknown_semantic_entrypoint=True),
            )
            with self.assertRaises(ToolRuntimeError):
                persist_task_contract(
                    changed,
                    intake_id_sha256=intake["intake_id_sha256"],
                    state_dir=directory,
                )
            self.assertTrue(record_expected_tool_call(
                session_id="session", turn_id="turn", tool_use_id="call-1",
                route_code="codegraph",
                state_dir=directory,
            ))
            self.assertEqual(
                load_expected_tool_calls(
                    session_id="session", turn_id="turn", state_dir=directory
                ),
                [hashlib.sha256(b"call-1").hexdigest()],
            )
            self.assertEqual(
                load_tool_call_intake(
                    session_id="session", turn_id="turn", tool_use_id="call-1",
                    state_dir=directory,
                )["intake_id_sha256"],
                intake["intake_id_sha256"],
            )
            self.assertEqual(
                load_tool_call_binding(
                    session_id="session", turn_id="turn", tool_use_id="call-1",
                    state_dir=directory,
                )["route_code"],
                "codegraph",
            )

    def test_stable_task_id_gets_new_immutable_intake_generation_per_prompt(self):
        with tempfile.TemporaryDirectory() as directory:
            first = begin_turn_state(
                session_id="stable-session", turn_id="stable-turn",
                prompt="first prompt", state_dir=directory,
            )
            first_contract = compile_task_contract(
                task_id_sha256=first["task_id_sha256"],
                classifier_identity="test-classifier",
                task_shape_sha256=first["task_shape_sha256"],
                repository_work=True,
                signals=signals(known_symbol_or_call=True),
            )
            persist_task_contract(
                first_contract,
                intake_id_sha256=first["intake_id_sha256"],
                state_dir=directory,
            )
            self.assertTrue(record_expected_tool_call(
                session_id="stable-session", turn_id="stable-turn",
                tool_use_id="first-call", route_code="codegraph",
                state_dir=directory,
            ))

            second = begin_turn_state(
                session_id="stable-session", turn_id="stable-turn",
                prompt="second prompt", state_dir=directory,
            )
            self.assertEqual(first["task_id_sha256"], second["task_id_sha256"])
            self.assertNotEqual(first["intake_id_sha256"], second["intake_id_sha256"])
            self.assertNotEqual(first["task_shape_sha256"], second["task_shape_sha256"])
            second_contract = compile_task_contract(
                task_id_sha256=second["task_id_sha256"],
                classifier_identity="test-classifier",
                task_shape_sha256=second["task_shape_sha256"],
                repository_work=True,
                signals=signals(unknown_semantic_entrypoint=True),
            )
            self.assertEqual(
                persist_task_contract(
                    second_contract,
                    intake_id_sha256=second["intake_id_sha256"],
                    state_dir=directory,
                ),
                second_contract,
            )
            self.assertEqual(
                load_turn_contract(
                    session_id="stable-session", turn_id="stable-turn",
                    state_dir=directory,
                ),
                second_contract,
            )
            self.assertEqual(
                load_current_intake(
                    session_id="stable-session", turn_id="stable-turn",
                    state_dir=directory,
                ),
                second,
            )
            self.assertEqual(
                load_expected_tool_calls(
                    session_id="stable-session", turn_id="stable-turn",
                    state_dir=directory,
                ),
                [],
            )
            self.assertEqual(
                load_tool_call_intake(
                    session_id="stable-session", turn_id="stable-turn",
                    tool_use_id="first-call", state_dir=directory,
                )["intake_id_sha256"],
                first["intake_id_sha256"],
            )
            with self.assertRaises(ToolRuntimeError):
                persist_task_contract(
                    first_contract,
                    intake_id_sha256=second["intake_id_sha256"],
                    state_dir=directory,
                )

    def test_subagent_intake_inherits_parent_shape_and_records_opaque_lineage(self):
        with tempfile.TemporaryDirectory() as directory:
            parent = begin_turn_state(
                session_id="session", turn_id="turn", prompt="parent prompt",
                state_dir=directory,
            )
            child = begin_child_turn_state(
                session_id="session", turn_id="child-turn", agent_id="child-agent",
                state_dir=directory,
            )
            self.assertEqual(child["task_shape_sha256"], parent["task_shape_sha256"])
            self.assertEqual(
                child["parent_intake_id_sha256"], parent["intake_id_sha256"]
            )
            self.assertNotEqual(child["task_id_sha256"], parent["task_id_sha256"])
            self.assertNotEqual(child["intake_id_sha256"], parent["intake_id_sha256"])
            self.assertRegex(child["agent_id_sha256"], r"^[0-9a-f]{64}$")
            child_contract = compile_task_contract(
                task_id_sha256=child["task_id_sha256"],
                classifier_identity="test-classifier",
                task_shape_sha256=child["task_shape_sha256"],
                repository_work=True,
                signals=signals(machine_exact_only=True),
            )
            persist_task_contract(
                child_contract,
                intake_id_sha256=child["intake_id_sha256"],
                state_dir=directory,
            )
            self.assertEqual(
                load_turn_contract(
                    session_id="session", turn_id="child-turn",
                    state_dir=directory,
                ),
                child_contract,
            )
            with self.assertRaises(ToolRuntimeError):
                begin_child_turn_state(
                    session_id="missing", turn_id="parent", agent_id="child-agent",
                    state_dir=directory,
                )
            with self.assertRaises(ToolRuntimeError):
                begin_child_turn_state(
                    session_id="session", turn_id="turn", agent_id="child-agent",
                    state_dir=directory,
                )

    def test_concurrent_activity_records_preserve_exact_denominator(self):
        with tempfile.TemporaryDirectory() as directory:
            begin_turn_state(
                session_id="parallel-session", turn_id="parallel-turn",
                prompt="parallel repository calls", state_dir=directory,
            )
            context = multiprocessing.get_context("fork")
            barrier = context.Barrier(9)
            results = context.Queue()
            call_ids = [f"parallel-call-{index}" for index in range(8)]
            workers = [
                context.Process(
                    target=record_worker,
                    args=(directory, barrier, results, call_id),
                )
                for call_id in call_ids
            ]
            for worker in workers:
                worker.start()
            barrier.wait()
            for worker in workers:
                worker.join(10)
                self.assertEqual(worker.exitcode, 0)
            self.assertEqual([results.get(timeout=2) for _ in workers], [True] * 8)
            self.assertEqual(
                load_expected_tool_calls(
                    session_id="parallel-session", turn_id="parallel-turn",
                    state_dir=directory,
                ),
                sorted(hashlib.sha256(call_id.encode()).hexdigest() for call_id in call_ids),
            )


if __name__ == "__main__":
    unittest.main()

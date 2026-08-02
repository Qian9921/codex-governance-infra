import copy
import hashlib
import unittest

from codex.v16.tool_runtime import (
    ROUTES,
    ToolRuntimeError,
    build_enforcement_report,
    compile_task_contract,
    validate_enforcement_report,
    validate_task_contract,
)


TASK_SHA = hashlib.sha256(b"task").hexdigest()
SHAPE_SHA = hashlib.sha256(b"task-shape").hexdigest()


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
        report = build_enforcement_report(task, usage(tools=("semble",)))
        self.assertEqual(report["status"], "compliant")
        self.assertTrue(report["completion_eligible"])
        self.assertEqual(report["counts"]["passed"], 4)
        self.assertEqual(validate_enforcement_report(report, task, usage(tools=("semble",))), report)

    def test_missing_required_tool_is_blocking_not_a_skip(self):
        task = contract(known_symbol_or_call=True)
        report = build_enforcement_report(task, usage())
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
            usage(
                tools=("rg",),
                routes=({"preferred_tool": "semble", "fallback": True},),
                equivalent=False,
            ),
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
            usage(tools=("rg",), compliant=False, equivalent=False),
        )
        self.assertEqual(report["status"], "blocked")
        self.assertFalse(report["completion_eligible"])
        self.assertIn("TOOL_USAGE_NOT_ROUTING_COMPLIANT", report["violations"])

    def test_validator_rejects_a_self_consistent_but_forged_green_report(self):
        task = contract(known_symbol_or_call=True)
        actual_usage = usage()
        forged = build_enforcement_report(task, actual_usage)
        forged = copy.deepcopy(forged)
        forged["status"] = "compliant"
        forged["completion_eligible"] = True
        forged["satisfied_routes"] = ["structural_analysis"]
        forged["violations"] = []
        forged["counts"]["passed"] = 4
        forged["counts"]["failed"] = 0
        with self.assertRaises(ToolRuntimeError):
            validate_enforcement_report(forged, task, actual_usage)


if __name__ == "__main__":
    unittest.main()

import pathlib
import sys
import unittest


ROOT = pathlib.Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "codex" / "hooks"))

from model_roles import (  # noqa: E402
    LUNA,
    SOL,
    TERRA,
    ModelRoleError,
    route_mission,
    validate_controller_request,
    validate_final_review,
    validate_nested_delegation,
    validate_receipt_identity,
)
import session_context  # noqa: E402


def identity(model, role, task_name, *, requested=None, fallback="none"):
    return {
        "requested_model": requested or model,
        "actual_model": model,
        "role": role,
        "fallback_reason": fallback,
        "task_name": task_name,
    }


class ModelRolePolicyTests(unittest.TestCase):
    def test_hook_context_exposes_same_machine_policy(self):
        context = session_context.build_context("SessionStart", LUNA)
        self.assertEqual(context["model_roles"]["controller"], LUNA)
        self.assertEqual(context["model_roles"]["sol_contract_gate"], "R2+")
        self.assertTrue(context["model_roles"]["no_ping_pong"])

    def test_routine_work_is_luna_without_sol_inner_loop(self):
        route = route_mission("R1")
        self.assertEqual(route["controller_model"], LUNA)
        self.assertEqual(route["execution_model"], LUNA)
        self.assertFalse(route["sol_contract_gate"])
        self.assertFalse(route["sol_inner_loop_required"])
        self.assertTrue(route["review"]["required"])
        self.assertEqual(route["review"]["model"], SOL)

    def test_r2_routes_sol_gate_luna_execution_and_fresh_review(self):
        route = route_mission("R2")
        self.assertTrue(route["sol_contract_gate"])
        self.assertEqual(route["execution_model"], LUNA)
        self.assertEqual(route["review"]["model"], SOL)
        self.assertTrue(route["review"]["fresh"])
        self.assertTrue(route["review"]["counterexample_required"])

    def test_r4_keeps_sol_research_interpretation_explicit(self):
        route = route_mission("R4")
        self.assertTrue(route["sol_contract_gate"])
        self.assertTrue(route["sol_research_interpretation"])
        self.assertEqual(route["execution_model"], LUNA)

    def test_high_risk_cannot_disable_final_review(self):
        with self.assertRaises(ModelRoleError):
            route_mission("R2", profile="QUICK", review_required=False)

    def test_terra_is_not_a_universal_controller(self):
        with self.assertRaises(ModelRoleError):
            validate_controller_request(TERRA, luna_available=True)
        with self.assertRaises(ModelRoleError):
            validate_controller_request(TERRA, luna_available=False)
        self.assertEqual(
            validate_controller_request(
                TERRA, luna_available=False, fallback_reason="luna_unavailable"
            ),
            "terra",
        )
        with self.assertRaises(ModelRoleError):
            validate_controller_request(SOL, luna_available=True)

    def test_luna_unavailable_has_explicit_terra_fallback(self):
        route = route_mission("R1", luna_available=False)
        self.assertEqual(route["requested_controller_model"], LUNA)
        self.assertEqual(route["controller_model"], TERRA)
        self.assertEqual(route["fallback_reason"], "luna_unavailable")
        self.assertEqual(route["controller_role"], "continuity_fallback")
        self.assertFalse(route["universal_controller"])
        self.assertEqual(validate_controller_request(LUNA, luna_available=False, fallback_reason="capacity"), "terra")

    def test_sol_can_delegate_bounded_mechanical_work_to_luna(self):
        result = validate_nested_delegation(
            identity(SOL, "contract_gate", "sol-contract-gate"),
            identity(LUNA, "mechanical", "luna-mechanical-extract"),
            depth=1,
            parent_scope=["codex/"],
            child_scope=["codex/hooks/"],
            lineage=["sol"],
        )
        self.assertEqual(result["child_model"], "luna")

    def test_luna_can_escalate_one_narrow_sol_consultation(self):
        result = validate_nested_delegation(
            identity(LUNA, "execution", "luna-execution"),
            identity(SOL, "consultant", "sol-consultant-vjp"),
            depth=1,
            parent_scope=["src/solver/"],
            child_scope=["src/solver/vjp.cc"],
            lineage=["luna"],
            uncertainty_id="vjp-sign-1",
        )
        self.assertEqual(result["uncertainty_id"], "vjp-sign-1")

    def test_nested_depth_scope_ping_pong_and_dedup_are_rejected(self):
        parent = identity(LUNA, "execution", "luna-execution")
        child = identity(SOL, "consultant", "sol-consultant")
        with self.assertRaises(ModelRoleError):
            validate_nested_delegation(
                parent, child, depth=3, parent_scope=["src/"], child_scope=["src/a"],
                lineage=["luna"], uncertainty_id="u1",
            )
        with self.assertRaises(ModelRoleError):
            validate_nested_delegation(
                parent, child, depth=1, parent_scope=["src/a"], child_scope=["tests/"],
                lineage=["luna"], uncertainty_id="u2",
            )
        with self.assertRaises(ModelRoleError):
            validate_nested_delegation(
                parent, child, depth=2, parent_scope=["src/"], child_scope=["src/a"],
                lineage=["sol", "luna"], uncertainty_id="u3",
            )
        with self.assertRaises(ModelRoleError):
            validate_nested_delegation(
                parent, child, depth=1, parent_scope=["src/"], child_scope=["src/a"],
                lineage=["luna"], uncertainty_id="u4", seen_uncertainties=["u4"],
            )
        with self.assertRaises(ModelRoleError):
            validate_nested_delegation(
                parent, child, depth=1, parent_scope=["src/"], child_scope=["src/"],
                lineage=["luna"], uncertainty_id="u5",
            )
        with self.assertRaises(ModelRoleError):
            validate_nested_delegation(
                parent, child, depth=1, parent_scope=["src/", "tests/"],
                child_scope=["tests/", "src/"], lineage=["luna"], uncertainty_id="u6",
            )

    def test_author_lineage_sol_consultant_cannot_be_final_reviewer(self):
        review = {
            "reviewer_model": SOL,
            "reviewer_task_id": "sol-consultant-vjp",
            "parent_task_id": "mission-1",
            "controller_task_id": "luna-controller",
            "reviewer_parent_task_id": "mission-1",
            "author_lineage_id": "author-root",
            "reviewer_lineage_id": "review-root",
            "fresh": True,
            "read_only": True,
            "reviewer_is_writer": False,
            "round": 1,
            "source_ref": "src/gn_step.cpp:57-104",
            "contract_ref": "docs/contract.md:12-24",
            "tests_ref": "tests.cpp:88-116",
            "source_derived_counterexample_ref": "review.md:F-1",
        }
        with self.assertRaises(ModelRoleError):
            validate_final_review(
                review, risk="R2", author_lineage=["luna-controller", "author-root", "sol-consultant-vjp"]
            )

    def test_r2_review_requires_source_derived_counterexample(self):
        review = {
            "reviewer_model": SOL,
            "reviewer_task_id": "sol-reviewer-1",
            "parent_task_id": "mission-1",
            "controller_task_id": "luna-controller",
            "reviewer_parent_task_id": "mission-1",
            "author_lineage_id": "author-root",
            "reviewer_lineage_id": "review-root",
            "fresh": True,
            "read_only": True,
            "reviewer_is_writer": False,
            "round": 1,
            "source_ref": "src/gn_step.cpp:57-104",
            "contract_ref": "docs/contract.md:12-24",
            "tests_ref": "tests.cpp:88-116",
        }
        with self.assertRaises(ModelRoleError):
            validate_final_review(
                review, risk="R2", author_lineage=["luna-controller", "author-root"]
            )

    def test_delta_only_reuses_same_reviewer_and_is_limited_to_two_rounds(self):
        review = {
            "reviewer_model": SOL,
            "reviewer_task_id": "sol-reviewer-1",
            "reviewer_continuity_id": "sol-reviewer-1",
            "parent_task_id": "mission-1",
            "controller_task_id": "luna-controller",
            "reviewer_parent_task_id": "mission-1",
            "author_lineage_id": "author-root",
            "reviewer_lineage_id": "review-root",
            "fresh": False,
            "read_only": True,
            "reviewer_is_writer": False,
            "round": 2,
            "delta_only": True,
            "source_ref": "src/gn_step.cpp:57-104",
            "contract_ref": "docs/contract.md:12-24",
            "tests_ref": "tests.cpp:88-116",
            "boundary_analysis_ref": "review.md:B-2",
        }
        self.assertEqual(
            validate_final_review(
                review, risk="R2", author_lineage=["luna-controller", "author-root"]
            )["round"],
            2,
        )
        review["round"] = 3
        with self.assertRaises(ModelRoleError):
            validate_final_review(
                review, risk="R2", author_lineage=["luna-controller", "author-root"]
            )

    def test_receipt_requires_actual_model_and_role_in_task_name(self):
        value = identity(LUNA, "execution", "luna-execution-a")
        self.assertEqual(validate_receipt_identity(value)["actual_model"], LUNA)
        fallback = identity(
            TERRA, "continuity_fallback", "terra-fallback-a",
            requested=LUNA, fallback="luna_unavailable",
        )
        self.assertEqual(validate_receipt_identity(fallback)["actual_model"], TERRA)
        with self.assertRaises(ModelRoleError):
            validate_receipt_identity({**value, "task_name": "luna-execution-a", "actual_model": TERRA, "fallback_reason": "luna_unavailable"})
        with self.assertRaises(ModelRoleError):
            validate_receipt_identity({**value, "task_name": "luna-terra-execution-a"})
        with self.assertRaises(ModelRoleError):
            validate_receipt_identity({**fallback, "fallback_reason": " none "})


if __name__ == "__main__":
    unittest.main()

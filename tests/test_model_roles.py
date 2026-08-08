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
    validate_code_mission_tool_policy,
    normalize_receipt_identity,
    route_execution_task,
    route_mission,
    validate_controller_request,
    validate_final_review,
    validate_nested_delegation,
    validate_receipt_identity,
    validate_terra_bridge_request,
    validate_terra_bridge_result,
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


def terra_bridge(kind="TERRA_REPLAN", *, permissions=None, risk="R1", **overrides):
    value = {
        "bridge_kind": kind,
        "parent_task_id": "luna-parent-1",
        "parent_model": LUNA,
        "bridge_task_id": "terra-bridge-1",
        "requested_model": TERRA,
        "actual_model": TERRA,
        "fallback_reason": "none",
        "role": kind.lower(),
        "task_name": "terra-" + kind.lower().removeprefix("terra_") + "-bridge",
        "risk": risk,
        "parent_scope": ["codex/"],
        "scope": ["codex/hooks/"],
        "permissions": permissions or ["read", "plan"],
        "max_duration_sec": 300,
        "max_tool_calls": 12,
        "max_output_tokens": 2048,
        "handoff_reason": "Luna needs bounded medium-depth synthesis",
        "return_to_model": LUNA,
        "return_to_task_id": "luna-parent-1",
        "final_verdict": False,
        "can_write": False,
        "can_git": False,
        "can_review": False,
        "can_merge": False,
        "can_spawn": False,
        "long_listener": False,
        "continuation": False,
        "retry_allowed": False,
        "control_returned": False,
    }
    value.update(overrides)
    return value


class ModelRolePolicyTests(unittest.TestCase):
    def test_hook_context_exposes_same_machine_policy(self):
        context = session_context.build_context("SessionStart", LUNA)
        self.assertEqual(context["model_roles"]["controller"], LUNA)
        self.assertEqual(context["model_roles"]["sol_contract_gate"], "R2+")
        self.assertTrue(context["model_roles"]["no_ping_pong"])
        self.assertEqual(
            context["model_roles"]["terra_bridge_roles"],
            ["TERRA_REPLAN", "TERRA_TRIAGE"],
        )
        self.assertEqual(context["model_roles"]["terra_bridge_parent"], LUNA)
        self.assertIn("direct Luna parent", context["model_roles"]["terra_bridge_return"])
        self.assertEqual(
            context["tool_index_policy"]["schema"],
            "code-mission-tool-index-policy.v1",
        )
        self.assertFalse(context["tool_index_policy"]["quota_enforced"])
        self.assertEqual(
            context["tool_index_policy"]["evidence_schema"],
            "code-mission-evidence.v1",
        )

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
        for reason in (" ", "none", " null ", "n/a", "NA"):
            with self.assertRaises(ModelRoleError):
                validate_controller_request(
                    TERRA, luna_available=False, fallback_reason=reason
                )
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
        for reason in (" ", "none", "null", "n/a", "na"):
            with self.assertRaises(ModelRoleError):
                validate_controller_request(
                    LUNA, luna_available=False, fallback_reason=reason
                )

    def test_terra_bridge_routes_are_explicit_and_direct(self):
        for kind in ("TERRA_REPLAN", "TERRA_TRIAGE"):
            route = route_mission("R1", terra_bridge=kind)
            self.assertEqual(route["controller_model"], LUNA)
            self.assertTrue(route["terra_bridge"]["enabled"])
            self.assertEqual(route["terra_bridge"]["selected"], kind)
            self.assertEqual(route["terra_bridge"]["parent_model"], LUNA)
            self.assertEqual(route["terra_bridge"]["return_to_model"], LUNA)
            self.assertFalse(route["terra_bridge"]["final_verdict"])
            self.assertEqual(
                route["tool_index_policy"]["semantic_before_development"],
                "Semble semantic/similar discovery",
            )

    def test_terra_bridge_request_and_result_return_to_luna(self):
        for kind in ("TERRA_REPLAN", "TERRA_TRIAGE"):
            request = terra_bridge(kind, permissions=["read"] if kind == "TERRA_TRIAGE" else None)
            normalized = validate_terra_bridge_request(request)
            self.assertEqual(normalized["bridge_kind"], kind)
            result = validate_terra_bridge_result(
                {
                    "bridge_task_id": request["bridge_task_id"],
                    "parent_task_id": request["parent_task_id"],
                    "actual_model": TERRA,
                    "status": "complete",
                    "return_to_model": LUNA,
                    "return_to_task_id": request["parent_task_id"],
                    "control_returned": True,
                    "final_verdict": False,
                    "can_write": False,
                    "can_git": False,
                    "can_review": False,
                    "can_merge": False,
                    "can_spawn": False,
                    "spawned_children": False,
                    "retry_used": 0,
                    "long_listener": False,
                    "elapsed_sec": 12.5,
                    "tool_calls": 4,
                    "output_tokens": 256,
                },
                request,
            )
            self.assertTrue(result["control_returned"])

    def test_terra_bridge_rejects_fallbacks_high_risk_and_unsafe_authority(self):
        with self.assertRaises(ModelRoleError):
            validate_terra_bridge_request(terra_bridge(requested_model=LUNA))
        for field, value in (
            ("parent_model", SOL),
            ("risk", "R2"),
            ("scope", ["tests/"]),
            ("permissions", ["read", "write"]),
            ("can_review", True),
            ("can_spawn", True),
            ("long_listener", True),
            ("retry_allowed", True),
        ):
            with self.assertRaises(ModelRoleError):
                validate_terra_bridge_request(terra_bridge(**{field: value}))
        with self.assertRaises(ModelRoleError):
            route_mission("R2", terra_bridge="TERRA_REPLAN")
        with self.assertRaises(ModelRoleError):
            route_mission("R1", luna_available=False, terra_bridge="TERRA_TRIAGE")

    def test_terra_triage_is_read_only_and_bridge_cannot_chain(self):
        with self.assertRaises(ModelRoleError):
            validate_terra_bridge_request(
                terra_bridge("TERRA_TRIAGE", permissions=["read", "plan"])
            )
        with self.assertRaises(ModelRoleError):
            validate_terra_bridge_request(
                terra_bridge(task_name="terra-fallback-bridge")
            )
        with self.assertRaises(ModelRoleError):
            validate_terra_bridge_request(
                terra_bridge(parent_scope=["codex/hooks/"], scope=["codex/hooks/"])
            )
        with self.assertRaises(ModelRoleError):
            validate_terra_bridge_request(
                terra_bridge(return_to_model=TERRA)
            )
        result = {
            "bridge_task_id": "terra-bridge-1",
            "parent_task_id": "luna-parent-1",
            "actual_model": TERRA,
            "status": "complete",
            "return_to_model": LUNA,
            "return_to_task_id": "luna-parent-1",
            "control_returned": True,
            "final_verdict": False,
            "can_write": False,
            "can_git": False,
            "can_review": False,
            "can_merge": False,
            "can_spawn": False,
            "spawned_children": True,
            "retry_used": 0,
            "long_listener": False,
            "elapsed_sec": 1,
            "tool_calls": 1,
            "output_tokens": 1,
        }
        with self.assertRaises(ModelRoleError):
            validate_terra_bridge_result(result, terra_bridge())

    def test_terra_bridge_requires_exact_identity_schema_and_budgets(self):
        for field, value in (
            ("requested_model", "terra"),
            ("requested_model", TERRA + " "),
            ("actual_model", "Gpt-5.6-terra"),
            ("fallback_reason", " none "),
            ("fallback_reason", "NONE"),
        ):
            with self.assertRaises(ModelRoleError):
                validate_terra_bridge_request(terra_bridge(**{field: value}))
        with self.assertRaises(ModelRoleError):
            validate_terra_bridge_request(terra_bridge(unexpected_field=False))

        request = terra_bridge()
        base_result = {
            "bridge_task_id": request["bridge_task_id"],
            "parent_task_id": request["parent_task_id"],
            "actual_model": TERRA,
            "status": "complete",
            "return_to_model": LUNA,
            "return_to_task_id": request["parent_task_id"],
            "control_returned": True,
            "final_verdict": False,
            "can_write": False,
            "can_git": False,
            "can_review": False,
            "can_merge": False,
            "can_spawn": False,
            "spawned_children": False,
            "retry_used": 0,
            "long_listener": False,
            "elapsed_sec": 1,
            "tool_calls": 1,
            "output_tokens": 1,
        }
        for field, value in (
            ("actual_model", TERRA + " "),
            ("elapsed_sec", request["max_duration_sec"] + 1),
            ("tool_calls", request["max_tool_calls"] + 1),
            ("output_tokens", request["max_output_tokens"] + 1),
            ("can_git", True),
            ("can_spawn", True),
        ):
            result = dict(base_result)
            result[field] = value
            with self.assertRaises(ModelRoleError):
                validate_terra_bridge_result(result, request)
        result = dict(base_result)
        result["unexpected_field"] = False
        with self.assertRaises(ModelRoleError):
            validate_terra_bridge_result(result, request)

    def test_large_code_tool_index_policy_requires_identity_and_routes(self):
        policy = {
            "schema": "code-mission-tool-index-policy.v1",
            "mission_kind": "large_code",
            "repository_work": True,
            "repo_root_sha256": "a" * 64,
            "git_head_sha": "b" * 40,
            "git_tree_sha": "c" * 40,
            "worktree_sha256": "d" * 64,
            "codegraph_index_sha256": "e" * 64,
            "semble_index_sha256": "f" * 64,
            "codegraph_health": "HEALTHY",
            "semble_health": "HEALTHY",
            "semble_semantic_discovery": True,
            "codegraph_structural_evidence": True,
            "semble_semantic_evidence_ref": {
                "schema": "code-mission-evidence.v1",
                "kind": "semantic_discovery",
                "ref": "semble://evidence/" + "1" * 64,
                "receipt_sha256": "1" * 64,
                "query_sha256": "2" * 64,
                "repo_root_sha256": "a" * 64,
                "git_head_sha": "b" * 40,
                "git_tree_sha": "c" * 40,
                "worktree_sha256": "d" * 64,
                "index_sha256": "f" * 64,
            },
            "codegraph_structural_evidence_ref": {
                "schema": "code-mission-evidence.v1",
                "kind": "structural_blast",
                "ref": "codegraph://evidence/" + "3" * 64,
                "receipt_sha256": "3" * 64,
                "query_sha256": "4" * 64,
                "repo_root_sha256": "a" * 64,
                "git_head_sha": "b" * 40,
                "git_tree_sha": "c" * 40,
                "worktree_sha256": "d" * 64,
                "index_sha256": "e" * 64,
            },
            "candidate_ready": True,
            "n_a_reason": None,
            "repair_owner": "Luna",
            "repair_state": "HEALTHY",
            "dependent_claim_blocked": False,
            "quota_enforced": False,
        }
        self.assertEqual(
            validate_code_mission_tool_policy(policy)["mission_kind"], "large_code"
        )
        for field, value in (
            ("semble_semantic_discovery", False),
            ("codegraph_structural_evidence", False),
            ("repo_root_sha256", None),
            ("codegraph_health", "DEGRADED"),
            ("quota_enforced", True),
        ):
            invalid = dict(policy)
            invalid[field] = value
            with self.assertRaises(ModelRoleError):
                validate_code_mission_tool_policy(invalid)
        invalid = dict(policy)
        invalid["semble_semantic_evidence_ref"] = None
        with self.assertRaises(ModelRoleError):
            validate_code_mission_tool_policy(invalid)
        for evidence_field, identity_field, value in (
            ("semble_semantic_evidence_ref", "git_head_sha", "9" * 40),
            ("codegraph_structural_evidence_ref", "index_sha256", "9" * 64),
        ):
            invalid = dict(policy)
            invalid[evidence_field] = dict(policy[evidence_field])
            invalid[evidence_field][identity_field] = value
            with self.assertRaises(ModelRoleError):
                validate_code_mission_tool_policy(invalid)
        invalid = dict(policy)
        invalid["repair_state"] = "RECOVERING"
        invalid["dependent_claim_blocked"] = True
        with self.assertRaises(ModelRoleError):
            validate_code_mission_tool_policy(invalid)

    def test_non_code_tool_index_policy_requires_explicit_na_reason(self):
        policy = {
            "schema": "code-mission-tool-index-policy.v1",
            "mission_kind": "non_code",
            "repository_work": False,
            "repo_root_sha256": None,
            "git_head_sha": None,
            "git_tree_sha": None,
            "worktree_sha256": None,
            "codegraph_index_sha256": None,
            "semble_index_sha256": None,
            "codegraph_health": "N/A",
            "semble_health": "N/A",
            "semble_semantic_discovery": False,
            "codegraph_structural_evidence": False,
            "semble_semantic_evidence_ref": None,
            "codegraph_structural_evidence_ref": None,
            "candidate_ready": False,
            "n_a_reason": "pure non-code explanation",
            "repair_owner": "none",
            "repair_state": "N/A",
            "dependent_claim_blocked": False,
            "quota_enforced": False,
        }
        self.assertEqual(
            validate_code_mission_tool_policy(policy)["n_a_reason"],
            "pure non-code explanation",
        )
        invalid = dict(policy)
        invalid["n_a_reason"] = " "
        with self.assertRaises(ModelRoleError):
            validate_code_mission_tool_policy(invalid)

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
        with self.assertRaises(ModelRoleError):
            validate_nested_delegation(
                parent, child, depth=1, parent_scope=["codex/hooks/"],
                child_scope=["codex/hooks"], lineage=["luna"], uncertainty_id="u7",
            )
        with self.assertRaises(ModelRoleError):
            validate_nested_delegation(
                parent, child, depth=1, parent_scope=["codex/./hooks/"],
                child_scope=["codex/hooks"], lineage=["luna"], uncertainty_id="u8",
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

    def test_adaptive_identity_normalizes_followup_model_drift(self):
        normalized = normalize_receipt_identity(
            identity(
                SOL,
                "execution",
                "followup-2",
                requested=LUNA,
                fallback="none",
            )
        )
        self.assertEqual(normalized["status"], "advisory")
        self.assertIn("sol-execution-followup-2", normalized["normalized_task_name"])
        self.assertIn("requested_actual_family_drift", normalized["advisory"])

    def test_luna_named_task_with_sol_actual_is_identity_misrepresentation(self):
        with self.assertRaises(ModelRoleError):
            normalize_receipt_identity(
                identity(
                    SOL,
                    "execution",
                    "luna-execution-followup",
                    requested=LUNA,
                    fallback="luna_unavailable",
                )
            )

    def test_execution_route_is_bounded_luna_and_review_stays_sol(self):
        execution = route_execution_task(task_name="luna-execution-build")
        self.assertEqual(execution["model"], LUNA)
        self.assertTrue(execution["execution"])
        self.assertFalse(execution["context"]["full_history"])
        self.assertEqual(execution["identity"]["status"], "ok")

        review = route_execution_task(
            requested_model=SOL,
            actual_model=SOL,
            role="independent_final_reviewer",
            task_name="sol-independent-final-review",
        )
        self.assertEqual(review["model"], SOL)
        self.assertFalse(review["execution"])


if __name__ == "__main__":
    unittest.main()

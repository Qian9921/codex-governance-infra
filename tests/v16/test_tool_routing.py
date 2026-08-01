import json
import unittest
from unittest import mock

from codex.v16.tool_routing import (
    HEALTH_SCHEMA,
    Intent,
    ROUTE_SCHEMA,
    RoutingError,
    ToolObservation,
    ToolRouter,
    health_report,
    route_tool,
    tooling_doctor,
    validate_health_report,
    validate_route_decision,
)


class ToolRoutingTests(unittest.TestCase):
    def test_generators_use_distinct_validated_schema_ids(self):
        route_decision = route_tool("known_symbol", observations={"codegraph": True})
        report = health_report(observations={tool: True for tool in ("codegraph", "semble", "rtk", "rg")})
        self.assertEqual(route_decision["schema"], ROUTE_SCHEMA)
        self.assertEqual(report["schema"], HEALTH_SCHEMA)
        self.assertEqual(validate_route_decision(route_decision, expected_intent="known_symbol"), route_decision)
        self.assertEqual(validate_health_report(report), report)

    def test_route_validator_rejects_forged_fields_and_mapping(self):
        decision = route_tool("known_symbol", observations={"codegraph": True})
        forged = dict(decision)
        forged["selected_tool"] = "rg"
        with self.assertRaises(RoutingError):
            validate_route_decision(forged)
        forged = dict(decision)
        forged["extra"] = "forged"
        with self.assertRaises(RoutingError):
            validate_route_decision(forged)
        fallback = route_tool(
            "known_symbol",
            observations={"codegraph": False, "rg": True},
            attempted_preferred=True,
            preferred_failed=True,
            failure_reason_code="CODEGRAPH_DOWN",
            evidence_ref="obs:cg:1",
        )
        self.assertEqual(validate_route_decision(fallback, observations={"codegraph": False, "rg": True}), fallback)

    def test_health_validator_rejects_forged_arithmetic_and_schema(self):
        observations = {"codegraph": True, "semble": False, "rtk": True, "rg": True}
        report = health_report(observations=observations)
        self.assertEqual(validate_health_report(report, observations=observations), report)
        forged = dict(report)
        forged["counts"] = dict(report["counts"])
        forged["counts"]["passed"] += 1
        with self.assertRaises(RoutingError):
            validate_health_report(forged)
        forged = dict(report)
        forged["schema"] = ROUTE_SCHEMA
        with self.assertRaises(RoutingError):
            validate_health_report(forged)
    def test_structural_intents_prefer_codegraph(self):
        for intent in ("known_symbol", "known_call", "blast-radius", Intent.BLAST_RADIUS):
            result = route_tool(intent, observations={"codegraph": {"available": True}})
            self.assertEqual(result["decision"], "route")
            self.assertEqual(result["selected_tool"], "codegraph")
            self.assertFalse(result["fallback"])

    def test_semantic_and_similar_intents_prefer_semble(self):
        for intent in ("semantic_entry", "unknown-entry", "similar_implementation"):
            result = route_tool(intent, observations={"semble": True})
            self.assertEqual(result["selected_tool"], "semble")
            self.assertEqual(result["reason_code"], "PREFERRED_AVAILABLE")

    def test_shell_and_exact_intents_route_to_distinct_tools(self):
        self.assertEqual(route_tool("shell_output", observations={"rtk": True})["selected_tool"], "rtk")
        self.assertEqual(route_tool("exact_error", observations={"rg": True})["selected_tool"], "rg")
        self.assertEqual(route_tool("config", observations={"rg": True})["selected_tool"], "rg")

    def test_fallback_requires_attempt_reason_and_evidence(self):
        observations = {"codegraph": {"available": False}, "rg": True}
        no_attempt = route_tool("known_symbol", observations=observations)
        self.assertEqual(no_attempt["decision"], "blocked")
        self.assertEqual(no_attempt["reason_code"], "PREFERRED_NOT_ATTEMPTED")

        no_evidence = route_tool(
            "known_symbol", observations=observations, attempted_preferred=True,
        )
        self.assertEqual(no_evidence["reason_code"], "FALLBACK_EVIDENCE_REQUIRED")

        fallback = route_tool(
            "known_symbol",
            observations=observations,
            attempted_preferred=True,
            preferred_failed=True,
            failure_reason_code="CODEGRAPH_UNAVAILABLE",
            evidence_ref="obs:codegraph:001",
        )
        self.assertEqual(fallback["decision"], "fallback")
        self.assertEqual(fallback["selected_tool"], "rg")
        self.assertTrue(fallback["fallback"])
        self.assertEqual(fallback["evidence_ref"], "obs:codegraph:001")

        observed_failure = route_tool(
            "known_symbol",
            observations={
                "codegraph": {
                    "available": False,
                    "reason_code": "CODEGRAPH_NOT_FOUND",
                    "evidence_ref": "probe:codegraph:missing",
                },
                "rg": True,
            },
        )
        self.assertEqual(observed_failure["decision"], "fallback")
        self.assertEqual(observed_failure["selected_tool"], "rg")

    def test_fallback_is_blocked_when_fallback_tool_is_not_usable(self):
        result = route_tool(
            "similar_implementation",
            observations={"semble": False, "rg": {"available": False}},
            attempted_preferred=True,
            failure_reason_code="SEMBLE_NOT_FOUND",
            evidence_ref="obs:semble:missing",
        )
        self.assertEqual(result["decision"], "blocked")
        self.assertEqual(result["reason_code"], "FALLBACK_UNAVAILABLE")

    def test_undeclared_intent_is_not_a_false_hard_block(self):
        result = route_tool("future_tooling_intent", declared=False)
        self.assertEqual(result["decision"], "not_declared")
        self.assertEqual(result["reason_code"], "INTENT_NOT_DECLARED")
        self.assertIsNone(result["selected_tool"])

    def test_declared_unknown_intent_is_rejected(self):
        with self.assertRaises(RoutingError):
            route_tool("future_tooling_intent")

    def test_observation_validation_is_strict(self):
        with self.assertRaises(RoutingError):
            ToolObservation("not-a-tool", True)
        with self.assertRaises(RoutingError):
            route_tool("known_symbol", observations={"codegraph": {"available": 1}})
        with self.assertRaises(RoutingError):
            route_tool("known_symbol", observations={"codegraph": {"available": True, "extra": 1}})

    def test_health_report_has_known_denominator_and_stable_order(self):
        report = health_report(
            observations={
                "codegraph": {"available": True, "evidence_ref": "probe:cg"},
                "semble": {"available": False, "reason_code": "NOT_FOUND", "evidence_ref": "probe:s"},
            }
        )
        self.assertEqual(report["tools"], ["codegraph", "semble", "rtk", "rg"])
        self.assertEqual(report["status"], "degraded")
        self.assertEqual(report["denominator"], 4)
        self.assertTrue(report["denominator_known"])
        self.assertEqual(report["counts"], {"total": 4, "ran": 2, "passed": 1, "failed": 1, "skipped": 0, "xfail": 0, "unknown": 2})
        self.assertEqual([item["tool"] for item in report["checks"]], report["tools"])
        self.assertEqual(report["mutations"], [])
        # Reports are JSON-safe and deterministic for the same observations.
        self.assertEqual(json.dumps(report, sort_keys=True), json.dumps(health_report(observations={"codegraph": {"available": True, "evidence_ref": "probe:cg"}, "semble": {"available": False, "reason_code": "NOT_FOUND", "evidence_ref": "probe:s"}}), sort_keys=True))

    def test_doctor_probe_flag_is_read_only_and_injectable(self):
        report = tooling_doctor(observations={tool: True for tool in ("codegraph", "semble", "rtk", "rg")}, probe=False)
        self.assertEqual(report["status"], "healthy")
        self.assertEqual(report["counts"]["total"], 4)
        self.assertEqual(ToolRouter({"rg": True}).route("log")["selected_tool"], "rg")

    def test_health_failure_takes_precedence_over_unknown(self):
        report = health_report(observations={"codegraph": {"available": False, "reason_code": "DOWN"}})
        self.assertEqual(report["status"], "degraded")
        self.assertEqual(report["counts"]["failed"], 1)
        self.assertEqual(report["counts"]["unknown"], 3)
        self.assertEqual(validate_health_report(report), report)

    def test_probe_false_sources_distinguish_injected_and_missing(self):
        report = health_report(observations={"codegraph": True}, probe=False)
        self.assertEqual(report["probe_sources"]["codegraph"], "injected observation")
        self.assertEqual(report["probe_sources"]["semble"], "missing observation")
        self.assertEqual(report["probe_sources"]["rtk"], "missing observation")
        self.assertEqual(report["probe_sources"]["rg"], "missing observation")
        self.assertEqual(validate_health_report(report, observations={"codegraph": True}), report)
        forged = dict(report)
        forged["probe_sources"] = dict(report["probe_sources"])
        forged["probe_sources"]["codegraph"] = "shutil.which probe"
        with self.assertRaises(RoutingError):
            validate_health_report(forged, observations={"codegraph": True})

    def test_mixed_live_probe_and_injected_sources_are_bound(self):
        def which(tool):
            return None if tool == "semble" else f"/readonly/{tool}"

        with mock.patch("codex.v16.tool_routing.shutil.which", side_effect=which):
            report = health_report(probe=True, observations={"rg": True})
            self.assertEqual(report["probe_sources"]["codegraph"], "shutil.which probe")
            self.assertEqual(
                report["probe_sources"]["semble"],
                "shutil.which probe; Semble MCP capability unverified",
            )
            self.assertEqual(report["probe_sources"]["rtk"], "shutil.which probe")
            self.assertEqual(report["probe_sources"]["rg"], "injected observation")
            self.assertEqual(validate_health_report(report, observations={"rg": True}), report)

    def test_live_probe_does_not_call_missing_semble_mcp_capability_failed(self):
        def which(tool):
            return None if tool == "semble" else f"/readonly/{tool}"

        with mock.patch("codex.v16.tool_routing.shutil.which", side_effect=which):
            report = health_report(probe=True)
        semble = next(item for item in report["checks"] if item["tool"] == "semble")
        self.assertEqual(semble["status"], "unknown")
        self.assertEqual(semble["reason_code"], "MCP_CAPABILITY_UNVERIFIED")
        self.assertEqual(semble["evidence_ref"], "probe:semble:mcp-unverified")
        self.assertEqual(report["counts"]["failed"], 0)
        self.assertIn("MCP capability unverified", report["probe_sources"]["semble"])


if __name__ == "__main__":
    unittest.main()

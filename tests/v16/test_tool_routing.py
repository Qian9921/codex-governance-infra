import copy
import hashlib
import json
import os
import pathlib
import tempfile
import unittest
from unittest import mock

from codex.hooks import hook_receipt
from codex.v16.tool_routing import (
    HEALTH_SCHEMA,
    Intent,
    ROUTE_SCHEMA,
    RoutingError,
    ToolObservation,
    ToolRouter,
    build_usage_report,
    health_report,
    route_tool,
    tooling_doctor,
    validate_health_report,
    validate_route_decision,
    validate_usage_report,
)

TASK_SHA = hashlib.sha256(b"task").hexdigest()
SNAPSHOT_SHA = hashlib.sha256(b"hook-snapshot").hexdigest()


def _preflight():
    tools = []
    for tool in ("codegraph", "semble", "rtk"):
        tools.append({
            "tool": tool,
            "status": "pass",
            "reason_code": f"{tool.upper()}_READY",
            "version": "1",
            "checks": [{
                "name": "ready",
                "status": "pass",
                "reason_code": f"{tool.upper()}_READY",
            }],
            "evidence_sha256": hashlib.sha256(tool.encode()).hexdigest(),
        })
    return {
        "schema": "tool-preflight.v16",
        "status": "ready",
        "strict": True,
        "repo_identity": {
            "root_sha256": "1" * 64,
            "head_sha": "2" * 40,
            "dirty": False,
            "worktree_sha256": "3" * 64,
        },
        "config_identity": {
            "path_sha256": "4" * 64,
            "content_sha256": "5" * 64,
            "present": True,
        },
        "tools": tools,
        "counts": {
            "total": 3, "ran": 3, "passed": 3, "failed": 0,
            "skipped": 0, "xfail": 0, "unknown": 0,
        },
        "denominator": 3,
        "denominator_known": True,
        "cache": {
            "key_sha256": "6" * 64,
            "invalidated_by": ["host_or_runtime_change"],
        },
        "mutations": [],
    }


def _receipt(tool, tool_call_id, *, task=TASK_SHA, snapshot=SNAPSHOT_SHA):
    tool_name = {
        "codegraph": "mcp__codegraph__codegraph_explore",
        "semble": "mcp__semble__search",
        "rtk": "functions.exec_command",
        "rg": "functions.exec_command",
    }[tool]
    value = {
        "schema": "hook-receipt.v16",
        "schema_version": "hook-receipt.v16",
        "utc": "2026-08-02T00:00:00Z",
        "event": "PreToolUse",
        "model": "gpt-5.6-luna",
        "tool_name": tool_name,
        "decision": "allow",
        "reason": "policy_pass",
        "reason_code": "policy_pass",
        "route": tool,
        "route_code": tool,
        "snapshot_sha256": snapshot,
        "identifiers_sha256": task,
        "session_id_sha256": None,
        "turn_id_sha256": None,
        "tool_call_id_sha256": tool_call_id,
        "source": "test",
        "pid": 1,
        "ppid": 1,
        "receipt_status": "written",
    }
    digest = hashlib.sha256(
        (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n").encode()
    ).hexdigest()
    return value, digest


def _usage_inputs(routes):
    purpose = {
        "known_symbol": "structure",
        "semantic_entry": "discovery",
        "shell_output": "context_display",
    }
    calls = []
    receipts = []
    evidence = {}
    for route in routes:
        intent = route["intent"]
        tool = route["selected_tool"]
        call_id = hashlib.sha256(f"call:{intent}".encode()).hexdigest()
        receipt, receipt_sha = _receipt(tool, call_id)
        evidence_ref = f"evidence:{intent}"
        evidence_sha = hashlib.sha256(evidence_ref.encode()).hexdigest()
        receipts.append(receipt)
        evidence[evidence_ref] = evidence_sha
        calls.append({
            "intent": intent,
            "tool": tool,
            "status": "success",
            "evidence_ref": evidence_ref,
            "evidence_sha256": evidence_sha,
            "receipt_sha256": receipt_sha,
            "tool_call_id_sha256": call_id,
            "used_for": purpose[intent],
        })
    return calls, receipts, evidence


def _write_usage_authority(directory, preflight, receipts, evidence):
    root = pathlib.Path(directory)
    root.mkdir(parents=True, exist_ok=True)
    preflight_path = root / "preflight.json"
    preflight_bytes = (
        json.dumps(preflight, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode()
    preflight_path.write_bytes(preflight_bytes)
    preflight_path.chmod(0o600)

    receipt_path = root / "receipts.jsonl"
    receipt_bytes = "".join(
        json.dumps(item, sort_keys=True, separators=(",", ":")) + "\n"
        for item in receipts
    ).encode()
    receipt_path.write_bytes(receipt_bytes)
    receipt_path.chmod(0o600)

    evidence_paths = {}
    for index, (reference, digest) in enumerate(sorted(evidence.items())):
        path = root / f"evidence-{index}.bin"
        payload = reference.encode()
        path.write_bytes(payload)
        path.chmod(0o600)
        evidence_paths[reference] = path
    return {
        "preflight_artifact": preflight_path,
        "expected_preflight_artifact_sha256": hashlib.sha256(preflight_bytes).hexdigest(),
        "receipt_artifacts": [receipt_path],
        "expected_receipt_artifact_sha256s": [
            hashlib.sha256(receipt_bytes).hexdigest()
        ],
        "evidence_artifacts": evidence_paths,
        "expected_evidence_sha256": evidence,
    }


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

    def test_usage_report_requires_actual_receipt_backed_matching_calls(self):
        routes = [
            route_tool("known_symbol", observations={"codegraph": True}),
            route_tool("semantic_entry", observations={"semble": True}),
            route_tool("shell_output", observations={"rtk": True}),
        ]
        calls, receipts, evidence = _usage_inputs(routes)
        preflight = _preflight()
        with tempfile.TemporaryDirectory() as directory:
            authority = _write_usage_authority(
                directory, preflight, receipts, evidence
            )
            report = build_usage_report(
                hook_snapshot_sha256=SNAPSHOT_SHA,
                task_id_sha256=TASK_SHA,
                routes=routes,
                calls=calls,
                **authority,
            )
            self.assertEqual(report["status"], "compliant")
            self.assertTrue(report["routing_compliant"])
            self.assertTrue(report["coverage_equivalent"])
            self.assertEqual(report["counts"]["passed"], 3)
            self.assertEqual(
                validate_usage_report(report, **authority),
                report,
            )
            forged = copy.deepcopy(report)
            forged["preflight_cache_key_sha256"] = "f" * 64
            with self.assertRaises(RoutingError):
                validate_usage_report(forged, **authority)

            forged_preflight = copy.deepcopy(preflight)
            forged_preflight["cache"]["key_sha256"] = "f" * 64
            forged_path = pathlib.Path(directory) / "forged-preflight.json"
            forged_path.write_text(
                json.dumps(forged_preflight, sort_keys=True, separators=(",", ":"))
                + "\n",
                encoding="utf-8",
            )
            forged_path.chmod(0o600)
            with self.assertRaisesRegex(RoutingError, "authority hash"):
                build_usage_report(
                    preflight_artifact=forged_path,
                    expected_preflight_artifact_sha256=authority[
                        "expected_preflight_artifact_sha256"
                    ],
                    receipt_artifacts=authority["receipt_artifacts"],
                    expected_receipt_artifact_sha256s=authority[
                        "expected_receipt_artifact_sha256s"
                    ],
                    evidence_artifacts=authority["evidence_artifacts"],
                    expected_evidence_sha256=authority[
                        "expected_evidence_sha256"
                    ],
                    hook_snapshot_sha256=SNAPSHOT_SHA,
                    task_id_sha256=TASK_SHA,
                    routes=routes,
                    calls=calls,
                )

        negative_cases = []
        missing_receipt_calls = copy.deepcopy(calls)
        missing_receipt_calls[0]["receipt_sha256"] = "a" * 64
        negative_cases.append(("missing receipt", missing_receipt_calls, receipts, evidence))

        unwritten = copy.deepcopy(receipts)
        unwritten[0]["receipt_status"] = "not_written"
        negative_cases.append(("unwritten receipt", calls, unwritten, evidence))

        stale_snapshot = copy.deepcopy(receipts)
        stale_snapshot[0]["snapshot_sha256"] = "b" * 64
        negative_cases.append(("stale snapshot", calls, stale_snapshot, evidence))

        wrong_task = copy.deepcopy(receipts)
        wrong_task[0]["identifiers_sha256"] = "c" * 64
        negative_cases.append(("wrong task", calls, wrong_task, evidence))

        wrong_call = copy.deepcopy(receipts)
        wrong_call[0]["tool_call_id_sha256"] = "d" * 64
        negative_cases.append(("wrong tool call", calls, wrong_call, evidence))

        unspecified = copy.deepcopy(receipts)
        unspecified[0]["route"] = unspecified[0]["route_code"] = "unspecified"
        negative_cases.append(("unspecified route", calls, unspecified, evidence))

        wrong_tool_name = copy.deepcopy(receipts)
        wrong_tool_name[0]["tool_name"] = "test_codegraph"
        negative_cases.append(("wrong tool name", calls, wrong_tool_name, evidence))

        negative_cases.append(("absent evidence", calls, receipts, {}))

        mismatched_evidence = dict(evidence)
        mismatched_evidence[calls[0]["evidence_ref"]] = "e" * 64
        negative_cases.append(("mismatched evidence", calls, receipts, mismatched_evidence))

        for label, candidate_calls, candidate_receipts, candidate_evidence in negative_cases:
            with self.subTest(label=label), tempfile.TemporaryDirectory() as directory:
                authority = _write_usage_authority(
                    directory, preflight, candidate_receipts, candidate_evidence
                )
                with self.assertRaises(RoutingError):
                    build_usage_report(
                        hook_snapshot_sha256=SNAPSHOT_SHA,
                        task_id_sha256=TASK_SHA,
                        routes=routes,
                        calls=candidate_calls,
                        **authority,
                    )

    def test_usage_report_accepts_exact_persisted_receipt_record(self):
        route = route_tool("known_symbol", observations={"codegraph": True})
        raw_call_id = "call:known_symbol"
        call_id = hashlib.sha256(raw_call_id.encode()).hexdigest()
        with tempfile.TemporaryDirectory() as directory, mock.patch.dict(
            os.environ,
            {"CODEX_TASK_ID": "task", "CODEX_HOOK_SOURCE": "test"},
        ):
            root = pathlib.Path(directory)
            destination = root / "receipt.jsonl"
            value = hook_receipt.receipt(
                "PreToolUse",
                "gpt-5.6-luna",
                tool="mcp__codegraph__codegraph_explore",
                decision="allow",
                reason_code="policy_pass",
                route_code="codegraph",
                snapshot_sha256=SNAPSHOT_SHA,
                identifiers={"tool_call_id": raw_call_id},
            )
            self.assertTrue(hook_receipt.write_receipt(value, destination))
            line = destination.read_bytes()
            receipt_sha = hashlib.sha256(line).hexdigest()
            evidence_ref = "evidence:persisted"
            evidence_sha = hashlib.sha256(evidence_ref.encode()).hexdigest()
            authority = _write_usage_authority(
                root / "authority",
                _preflight(),
                [json.loads(line)],
                {evidence_ref: evidence_sha},
            )
            authority["receipt_artifacts"] = [destination]
            authority["expected_receipt_artifact_sha256s"] = [receipt_sha]
            report = build_usage_report(
                hook_snapshot_sha256=SNAPSHOT_SHA,
                task_id_sha256=TASK_SHA,
                routes=[route],
                calls=[{
                    "intent": "known_symbol",
                    "tool": "codegraph",
                    "status": "success",
                    "evidence_ref": evidence_ref,
                    "evidence_sha256": evidence_sha,
                    "receipt_sha256": receipt_sha,
                    "tool_call_id_sha256": call_id,
                    "used_for": "structure",
                }],
                **authority,
            )
            self.assertEqual(report["status"], "compliant")

    def test_usage_report_blocks_noop_or_wrong_tool_and_marks_fallback_degraded(self):
        route = route_tool("known_symbol", observations={"codegraph": True})
        call_id = hashlib.sha256(b"call:wrong-tool").hexdigest()
        receipt, receipt_sha = _receipt("rg", call_id)
        evidence_ref = "evidence:wrong-tool"
        evidence_sha = hashlib.sha256(evidence_ref.encode()).hexdigest()
        with tempfile.TemporaryDirectory() as directory:
            authority = _write_usage_authority(
                directory,
                _preflight(),
                [receipt],
                {evidence_ref: evidence_sha},
            )
            wrong = build_usage_report(
                hook_snapshot_sha256=SNAPSHOT_SHA,
                task_id_sha256=TASK_SHA,
                routes=[route],
                calls=[
                    {
                        "intent": "known_symbol",
                        "tool": "rg",
                        "status": "success",
                        "evidence_ref": evidence_ref,
                        "evidence_sha256": evidence_sha,
                        "receipt_sha256": receipt_sha,
                        "tool_call_id_sha256": call_id,
                        "used_for": "structure",
                    }
                ],
                **authority,
            )
        self.assertEqual(wrong["status"], "blocked")
        self.assertEqual(wrong["violations"], ["ROUTE_TOOL_MISMATCH:known_symbol"])

        fallback_route = route_tool(
            "known_symbol",
            observations={
                "codegraph": {
                    "available": False,
                    "reason_code": "CODEGRAPH_DOWN",
                    "evidence_ref": "probe:cg:down",
                },
                "rg": True,
            },
        )
        fallback_calls, fallback_receipts, fallback_evidence = _usage_inputs(
            [fallback_route]
        )
        with tempfile.TemporaryDirectory() as directory:
            authority = _write_usage_authority(
                directory,
                _preflight(),
                fallback_receipts,
                fallback_evidence,
            )
            fallback = build_usage_report(
                hook_snapshot_sha256=SNAPSHOT_SHA,
                task_id_sha256=TASK_SHA,
                routes=[fallback_route],
                calls=fallback_calls,
                **authority,
            )
        self.assertEqual(fallback["status"], "degraded")
        self.assertTrue(fallback["routing_compliant"])
        self.assertFalse(fallback["coverage_equivalent"])


if __name__ == "__main__":
    unittest.main()

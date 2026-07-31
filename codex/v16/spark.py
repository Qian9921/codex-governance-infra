"""Bounded Spark inner-loop audit packet/result protocol."""
from __future__ import annotations

import hashlib
import json
import math
import pathlib
import re
import subprocess
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from .contracts import (
    ContractError,
    _id,
    _int,
    _relative_path,
    _sha,
    _str,
    canonical_json,
    canonical_sha256,
    counterexample_sha256,
    validate_closure_binding_receipt,
)

MAX_SPARK_AUDITS = 3
HEX64_RE = re.compile(r"[0-9a-f]{64}\Z")
HISTORICAL_FINDINGS = tuple([f"A-F{i}" for i in range(1, 9)] + [f"B-F{i}" for i in range(1, 7)] + [f"C-F{i}" for i in range(1, 6)])

# These are the sanitized, platform-delivered facts captured before the first
# production edit.  They are deliberately immutable: a caller cannot replace a
# task identity and simply recompute the outer transcript digest.
EXPECTED_HISTORICAL = (
    ("SPARK-A-DAG", "/root/v16_productivity_writer/v16_spark_audit_dag", ["A-F1", "A-F2", "A-F3", "A-F4", "A-F5", "A-F6", "A-F7", "A-F8"]),
    ("SPARK-B-EVIDENCE", "/root/v16_productivity_writer/v16_spark_audit_evidence", ["B-F1", "B-F2", "B-F3", "B-F4", "B-F5", "B-F6"]),
    ("SPARK-C-METRICS", "/root/v16_productivity_writer/v16_spark_audit_metrics", ["C-F1", "C-F2", "C-F3", "C-F4", "C-F5"]),
)
EXPECTED_CURRENT = (
    {"audit_id": "RE-AUDIT-A", "task_id": "/root/v16_productivity_remediation_writer/v16_reaudit_orchestration", "parent_task_id": "/root/v16_productivity_remediation_writer", "scope": ["orchestration", "compiler", "readiness", "runner"], "finding_ids": ["A-1", "A-2", "A-3", "A-4", "A-5", "A-6"], "spawn_request_sha256": "81a95c5ec6756337ff5b851114394ffe489b3e1f52e93cda4ff5560be7330e57", "spawn_response_sha256": "0bc389634dba001373bbde8af189bc02bed2d523abd122e543769ae7505a7270", "initial_request_sha256": "81a95c5ec6756337ff5b851114394ffe489b3e1f52e93cda4ff5560be7330e57", "initial_final_envelope_sha256": "93cce4b2a7045b7ed711231e0e79983a3d952d59c3369d3ad1316225592f4bed", "corrective_request_sha256": "809bc08add4df6e94135b3e4c8aa84dc916535b6691db9b58d8deb3a5df8db0a", "corrective_final_envelope_sha256": "5d19dc0794088096df5030474fc1ae3e74e2ec850e18237ca6dd9cb399496079", "snapshot_path_hash_set_sha256": "1b850c7b10c2d8ae4eb74448c41cec023941ab0b41a10452b33337080427a7f1", "normalized_artifact_path": "codex/v16/contracts/spark_result_A.v16.json", "normalized_artifact_sha256": "cc20b004f5bbbb08e7ec8cfc5b3bb5dabc083be13e89d31ee78189bf01b98936", "raw_platform_sha256": "5d19dc0794088096df5030474fc1ae3e74e2ec850e18237ca6dd9cb399496079", "author_closure_plan_path": "codex/v16/contracts/author_closure_plan.v16.json", "author_closure_plan_sha256": "d434591880ed4b77114823e3aa809ae5a8489b7aed87ee89924fe4c61e005508"},
    {"audit_id": "RE-AUDIT-B", "task_id": "/root/v16_productivity_remediation_writer/v16_reaudit_evidence", "parent_task_id": "/root/v16_productivity_remediation_writer", "scope": ["evidence", "privacy", "lineage", "trace"], "finding_ids": ["B-1", "B-2", "B-3", "B-4", "B-5", "B-6", "B-7"], "spawn_request_sha256": "4a30adc995b2f032384ede8024423ef4ef880cef8fd78f266707f95d5605328c", "spawn_response_sha256": "e2629d354ac8c6bd87d4455e9427172d8e2d874f1268b5a6649b29a6fd0377c6", "initial_request_sha256": "4a30adc995b2f032384ede8024423ef4ef880cef8fd78f266707f95d5605328c", "initial_final_envelope_sha256": "9504621e8eec0e9383f4c376cb9d2f8010d12b3baba9792d6ee05e5992c85dcd", "corrective_request_sha256": "c016df84bd7f62892b85d2fbf8351377e39d18d608435f43a5b588c19615a7e2", "corrective_final_envelope_sha256": "26459969e27a38c3ce23eaa0e5a03d3dd3be7cc0fd70985fc015769408e9671a", "snapshot_path_hash_set_sha256": "1b850c7b10c2d8ae4eb74448c41cec023941ab0b41a10452b33337080427a7f1", "normalized_artifact_path": "codex/v16/contracts/spark_result_B.v16.json", "normalized_artifact_sha256": "5b838139fa190d341f35efc08f120caa156ac7e06dc695894c54b453af5e958e", "raw_platform_sha256": "26459969e27a38c3ce23eaa0e5a03d3dd3be7cc0fd70985fc015769408e9671a", "author_closure_plan_path": "codex/v16/contracts/author_closure_plan.v16.json", "author_closure_plan_sha256": "d434591880ed4b77114823e3aa809ae5a8489b7aed87ee89924fe4c61e005508"},
    {"audit_id": "RE-AUDIT-C", "task_id": "/root/v16_productivity_remediation_writer/v16_reaudit_metrics", "parent_task_id": "/root/v16_productivity_remediation_writer", "scope": ["metrics", "manifest", "fresh-portability"], "finding_ids": ["C-RA-METRICS-001", "C-RA-METRICS-002", "C-RA-METRICS-003", "C-RA-MANIFEST-001", "C-RA-FRESH-001"], "spawn_request_sha256": "f57b135272a39d430436908dc78d3a391e541edb7c054b7d571bf4a87cb82b24", "spawn_response_sha256": "b708e06cc060309cc2658fcb92630b01b9cae56a28c46e9896a96c2979498856", "initial_request_sha256": "f57b135272a39d430436908dc78d3a391e541edb7c054b7d571bf4a87cb82b24", "initial_final_envelope_sha256": "ec78df0bfd046cdaf26a395ae70b53ea2bf9498e265743b27a110851a2551a41", "corrective_request_sha256": "ddebe354b8420e087fcfd389c841523424cb891dce922e2c4797e1e57d06afad", "corrective_final_envelope_sha256": "df6da9eb3b530042da54ed8fce264bc44d5ee2d1c9dee80ccd5423ca9a6de1d7", "snapshot_path_hash_set_sha256": "1b850c7b10c2d8ae4eb74448c41cec023941ab0b41a10452b33337080427a7f1", "normalized_artifact_path": "codex/v16/contracts/spark_result_C.v16.json", "normalized_artifact_sha256": "d682405be1d024af37d473ab71a8f6f09867386689acdfb644a34924bc0e915b", "raw_platform_sha256": "df6da9eb3b530042da54ed8fce264bc44d5ee2d1c9dee80ccd5423ca9a6de1d7", "author_closure_plan_path": "codex/v16/contracts/author_closure_plan.v16.json", "author_closure_plan_sha256": "d434591880ed4b77114823e3aa809ae5a8489b7aed87ee89924fe4c61e005508"},
)


class SparkAuditError(ContractError):
    pass


NORMALIZED_FINDING_IDS = {
    "RE-AUDIT-A": [f"A-{i}" for i in range(1, 7)],
    "RE-AUDIT-B": [f"B-{i}" for i in range(1, 8)],
    "RE-AUDIT-C": ["C-RA-METRICS-001", "C-RA-METRICS-002", "C-RA-METRICS-003", "C-RA-MANIFEST-001", "C-RA-FRESH-001"],
}
EXPECTED_RAW_PLATFORM_SHA256 = {
    "RE-AUDIT-A": "5d19dc0794088096df5030474fc1ae3e74e2ec850e18237ca6dd9cb399496079",
    "RE-AUDIT-B": "26459969e27a38c3ce23eaa0e5a03d3dd3be7cc0fd70985fc015769408e9671a",
    "RE-AUDIT-C": "df6da9eb3b530042da54ed8fce264bc44d5ee2d1c9dee80ccd5423ca9a6de1d7",
}
AUTHOR_FINDING_IDS = tuple(NORMALIZED_FINDING_IDS["RE-AUDIT-A"] + NORMALIZED_FINDING_IDS["RE-AUDIT-B"] + NORMALIZED_FINDING_IDS["RE-AUDIT-C"])
_PRIVATE_ARTIFACT_RE = re.compile(r"(?:gh[pso]_[A-Za-z0-9]{12,}|/" + r"home/|/" + r"Users/|\x00)", re.I)


def _hash64(value: Any, path: str) -> str:
    value = _str(value, path, max_len=64, public=True)
    if not HEX64_RE.fullmatch(value):
        raise SparkAuditError("SHA-256 artifact identity required", path)
    return value


def _validate_normalized_result(value: Any, *, audit_id: str, expected_raw_sha: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise SparkAuditError("normalized Spark result object required")
    fields = {"schema", "audit_id", "task_id", "scope", "raw_platform_sha256", "status", "finding_count", "findings", "dispositions", "artifact_sha256"}
    if set(value) != fields:
        raise SparkAuditError("strict normalized result fields")
    if value["schema"] != "spark-normalized-result.v16" or value["audit_id"] != audit_id or value["status"] != "AUDIT_COMPLETE":
        raise SparkAuditError("normalized result identity/status mismatch")
    _str(value["task_id"], "$.task_id", max_len=512, public=True)
    if not isinstance(value["scope"], list) or not value["scope"] or any(not isinstance(s, str) or not s for s in value["scope"]):
        raise SparkAuditError("normalized result scope required")
    if value["raw_platform_sha256"] != expected_raw_sha:
        raise SparkAuditError("raw platform result hash mismatch")
    _hash64(value["raw_platform_sha256"], "$.raw_platform_sha256")
    ids = NORMALIZED_FINDING_IDS.get(audit_id, [])
    findings = value["findings"]
    if not isinstance(findings, list) or value["finding_count"] != len(ids) or len(findings) != len(ids):
        raise SparkAuditError("normalized finding denominator mismatch")
    seen: set[str] = set()
    for index, finding in enumerate(findings):
        if not isinstance(finding, dict):
            raise SparkAuditError("normalized finding object required", f"$.findings[{index}]")
        if finding.get("id") not in ids or finding["id"] in seen:
            raise SparkAuditError("normalized finding identity mismatch", f"$.findings[{index}].id")
        seen.add(finding["id"])
        # A/B normalizations carry explicit raw ``status``/``disposition``
        # fields; C's source packet records those facts in the top-level
        # dispositions map.  Enforce the fields when present, while requiring
        # the map below for every finding so neither representation can drift.
        if finding.get("severity") != "P1" or finding.get("label") != "BLOCKING":
            raise SparkAuditError("raw corrective finding/FOLLOW_UP fact changed", f"$.findings[{index}]")
        if "status" in finding and finding["status"] != "OPEN":
            raise SparkAuditError("raw corrective finding status changed", f"$.findings[{index}]")
        if "disposition" in finding and finding["disposition"] != "FOLLOW_UP":
            raise SparkAuditError("raw corrective finding disposition changed", f"$.findings[{index}]")
        if _PRIVATE_ARTIFACT_RE.search(canonical_json(finding)):
            raise SparkAuditError("privacy-sensitive normalized finding", f"$.findings[{index}]")
    if not isinstance(value["dispositions"], dict) or set(value["dispositions"]) != set(ids) or any(v != "FOLLOW_UP" for v in value["dispositions"].values()):
        raise SparkAuditError("raw per-finding FOLLOW_UP dispositions required")
    unsigned = dict(value); unsigned["artifact_sha256"] = ""
    if value["artifact_sha256"] != canonical_sha256(unsigned):
        raise SparkAuditError("normalized result digest mismatch")
    return dict(value)


def validate_author_closure_plan(value: Any, *, root: str | pathlib.Path | None = None) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise SparkAuditError("author closure plan object required")
    fields = {"schema", "mission_id", "audited_head_sha", "finding_count", "findings", "candidate_binding", "plan_sha256"}
    if set(value) != fields or value.get("schema") != "author-closure-plan.v16" or value.get("mission_id") != "V16-PRODUCTIVITY" or value.get("candidate_binding") != "external_evidence_envelope":
        raise SparkAuditError("strict author closure plan fields")
    if value["audited_head_sha"] != "9a87e2e9c8d55e067979d335d69f7a2c525ce303":
        raise SparkAuditError("closure plan audited parent mismatch")
    # Git object identities are 40-hex SHAs, not SHA-256 artifact digests.
    _sha(value["audited_head_sha"], "$.audited_head_sha")
    if value["finding_count"] != len(AUTHOR_FINDING_IDS) or not isinstance(value["findings"], list) or len(value["findings"]) != len(AUTHOR_FINDING_IDS):
        raise SparkAuditError("closure plan denominator must be 18")
    required = {"finding_id", "severity", "counterexample_id", "executable_counterexample_id", "source_location", "gate_id", "stage", "evidence_row_id", "acceptance"}
    seen: set[str] = set()
    for index, item in enumerate(value["findings"]):
        if not isinstance(item, dict) or set(item) != required:
            raise SparkAuditError("strict closure plan finding fields", f"$.findings[{index}]")
        fid = item["finding_id"]
        if fid not in AUTHOR_FINDING_IDS or fid in seen:
            raise SparkAuditError("closure plan finding identity mismatch", f"$.findings[{index}].finding_id")
        seen.add(fid)
        if item["severity"] != "P1" or item["counterexample_id"] not in {"CE-SCHEMA", "CE-GATE", "CE-EVIDENCE"} or not re.fullmatch(r"NF-[0-9]{3}", item["executable_counterexample_id"]):
            raise SparkAuditError("closure plan executable counterexample binding required", f"$.findings[{index}]")
        location = item["source_location"]
        if not isinstance(location, str) or ":" not in location or location.startswith(("/", "~")) or _PRIVATE_ARTIFACT_RE.search(location):
            raise SparkAuditError("portable closure plan source location required", f"$.findings[{index}].source_location")
        if item["stage"] not in {"targeted", "full", "fresh"} or item["gate_id"] not in {"G-TARGETED", "G-FULL", "G-FRESH"} or item["stage"] != {"G-TARGETED": "targeted", "G-FULL": "full", "G-FRESH": "fresh"}[item["gate_id"]]:
            raise SparkAuditError("closure plan gate/stage mismatch", f"$.findings[{index}]")
        if item["evidence_row_id"] != f"EVID-{item['gate_id']}-EP-UNIT" or not isinstance(item["acceptance"], str) or not item["acceptance"]:
            raise SparkAuditError("closure plan evidence/acceptance binding required", f"$.findings[{index}]")
    if seen != set(AUTHOR_FINDING_IDS):
        raise SparkAuditError("closure plan must cover all 18 findings")
    unsigned = dict(value); unsigned["plan_sha256"] = ""
    if value["plan_sha256"] != canonical_sha256(unsigned):
        raise SparkAuditError("closure plan digest mismatch")
    if root is not None:
        root_path = pathlib.Path(root).resolve()
        for item in value["findings"]:
            path = (root_path / item["source_location"].split(":", 1)[0]).resolve()
            try:
                path.relative_to(root_path)
            except ValueError:
                raise SparkAuditError("closure plan source escapes candidate root")
            if not path.is_file() or path.is_symlink():
                raise SparkAuditError("closure plan source location missing")
    return dict(value)


def build_closure_binding_receipt(
    closure_plan: Mapping[str, Any],
    *,
    compiled_plan: Mapping[str, Any],
    spark_requests: Sequence[Mapping[str, Any]],
    spark_results: Sequence[Mapping[str, Any]],
    closure_plan_file_sha256: str,
    dispatch_transcript_file_sha256: str,
    normalized_source_artifact_paths: Mapping[str, str],
    root: str | pathlib.Path | None = None,
) -> dict[str, Any]:
    """Freeze finding-to-execution bindings before any closure-aware gate runs."""
    plan = validate_author_closure_plan(dict(closure_plan), root=root)
    if not isinstance(compiled_plan, Mapping) or compiled_plan.get("schema") != "compiled-plan.v16":
        raise SparkAuditError("compiled mission plan required for closure binding receipt")
    if compiled_plan.get("mission_id") != plan["mission_id"]:
        raise SparkAuditError("closure/compiled mission mismatch")
    compiled_plan_sha256 = canonical_sha256(compiled_plan)
    for value, path in (
        (closure_plan_file_sha256, "$.closure_plan_file_sha256"),
        (dispatch_transcript_file_sha256, "$.dispatch_transcript_file_sha256"),
    ):
        _hash64(value, path)

    gates = {
        gate.get("id"): gate
        for gate in compiled_plan.get("gates", [])
        if isinstance(gate, Mapping) and isinstance(gate.get("id"), str)
    }
    entries = {
        entry.get("id"): entry
        for entry in compiled_plan.get("entrypoints", [])
        if isinstance(entry, Mapping) and isinstance(entry.get("id"), str)
    }
    source_findings: dict[str, Mapping[str, Any]] = {}
    source_artifacts: list[dict[str, str]] = []
    if not isinstance(normalized_source_artifact_paths, Mapping):
        raise SparkAuditError("normalized Spark source path mapping required")
    checked_results = validate_bundle(spark_requests, spark_results)
    seen_audit_ids: set[str] = set()
    for result_index, result in enumerate(checked_results):
        if result.get("mission_id") != plan["mission_id"]:
            raise SparkAuditError("closure/Spark mission mismatch")
        audit_id = _id(result.get("audit_id"), f"$.spark_results[{result_index}].audit_id")
        artifact_sha256 = _hash64(
            result.get("artifact_sha256"),
            f"$.spark_results[{result_index}].artifact_sha256",
        )
        try:
            artifact_path = _relative_path(
                normalized_source_artifact_paths.get(audit_id),
                f"$.normalized_source_artifact_paths.{audit_id}",
            )
        except ContractError as exc:
            raise SparkAuditError(
                "normalized Spark source artifact path required"
            ) from exc
        source_artifacts.append({
            "audit_id": audit_id,
            "artifact_path": artifact_path,
            "artifact_sha256": artifact_sha256,
        })
        seen_audit_ids.add(audit_id)
        findings = result.get("findings")
        if not isinstance(findings, list):
            raise SparkAuditError(
                "Spark result findings array required",
                f"$.spark_results[{result_index}].findings",
            )
        for finding_index, finding in enumerate(findings):
            path = f"$.spark_results[{result_index}].findings[{finding_index}]"
            if not isinstance(finding, Mapping):
                raise SparkAuditError("Spark finding object required", path)
            finding_id = _id(finding.get("id"), f"{path}.id")
            if finding_id in source_findings:
                raise SparkAuditError("duplicate authoritative Spark finding", f"{path}.id")
            _str(finding.get("counterexample"), f"{path}.counterexample", public=True)
            source_findings[finding_id] = finding
    source_artifacts.sort(key=lambda item: item["audit_id"])
    if len({item["audit_id"] for item in source_artifacts}) != len(source_artifacts):
        raise SparkAuditError("duplicate authoritative Spark source artifact")
    if set(normalized_source_artifact_paths) != seen_audit_ids:
        raise SparkAuditError(
            "normalized Spark source path set differs from validated results"
        )

    plan_finding_ids = {item["finding_id"] for item in plan["findings"]}
    if set(source_findings) != plan_finding_ids:
        raise SparkAuditError("closure plan and authoritative Spark finding sets differ")
    bindings: list[dict[str, str]] = []
    for item in plan["findings"]:
        row_prefix = f"EVID-{item['gate_id']}-"
        if not item["evidence_row_id"].startswith(row_prefix):
            raise SparkAuditError("closure evidence row cannot resolve entrypoint")
        entrypoint_id = item["evidence_row_id"][len(row_prefix):]
        gate = gates.get(item["gate_id"])
        if (
            not entrypoint_id
            or gate is None
            or entrypoint_id not in entries
            or gate.get("stage") != item["stage"]
            or entrypoint_id not in gate.get("entrypoint_ids", [])
        ):
            raise SparkAuditError("closure binding is outside compiled mission plan")
        binding = {
            "finding_id": item["finding_id"],
            "counterexample_id": item["counterexample_id"],
            "executable_counterexample_id": item["executable_counterexample_id"],
            "counterexample_sha256": counterexample_sha256(
                source_findings[item["finding_id"]]["counterexample"],
                f"$.spark_findings.{item['finding_id']}.counterexample",
            ),
            "gate_id": item["gate_id"],
            "stage": item["stage"],
            "evidence_row_id": item["evidence_row_id"],
            "entrypoint_id": entrypoint_id,
            "binding_sha256": "",
        }
        binding["binding_sha256"] = canonical_sha256(binding)
        bindings.append(binding)
    bindings.sort(key=lambda item: item["finding_id"])
    receipt = {
        "schema": "closure-binding-receipt.v16",
        "mission_id": plan["mission_id"],
        "compiled_plan_sha256": compiled_plan_sha256,
        "closure_plan_sha256": plan["plan_sha256"],
        "closure_plan_file_sha256": closure_plan_file_sha256,
        "dispatch_transcript_file_sha256": dispatch_transcript_file_sha256,
        "normalized_source_artifacts": source_artifacts,
        "finding_count": len(bindings),
        "bindings": bindings,
        "receipt_sha256": "",
    }
    receipt["receipt_sha256"] = canonical_sha256(receipt)
    return validate_closure_binding_receipt(
        receipt,
        expected_compiled_plan_sha256=compiled_plan_sha256,
        expected_closure_plan_sha256=plan["plan_sha256"],
        expected_closure_plan_file_sha256=closure_plan_file_sha256,
        expected_dispatch_transcript_file_sha256=dispatch_transcript_file_sha256,
    )


def validate_author_closure(value: Any, *, root: str | pathlib.Path | None = None, evidence_rows: Sequence[Mapping[str, Any]] | None = None) -> dict[str, Any]:
    """Validate the separate author-side closure for all actual 18 findings.

    The author closure is never allowed to rewrite the raw Spark disposition.
    It is a candidate-side claim, and is accepted only when every finding has
    a concrete executable counterexample, source location, gate/evidence row,
    and deterministic log hash.  Runtime callers additionally pass the actual
    evidence rows so the row/log bindings are checked rather than merely named.
    """
    if not isinstance(value, dict):
        raise SparkAuditError("author closure object required")
    fields = {"schema", "mission_id", "candidate_head_sha", "candidate_tree_sha", "audited_head_sha", "plan_sha256", "finding_count", "findings", "disposition_summary", "artifact_sha256"}
    if set(value) != fields or value.get("schema") != "author-closure-final.v16" or value.get("mission_id") != "V16-PRODUCTIVITY":
        raise SparkAuditError("strict author closure fields")
    # Candidate/audited heads are Git object identities (40 hex); only the
    # plan and row/artifact bindings below use 64-hex SHA-256 digests.
    _sha(value["audited_head_sha"], "$.audited_head_sha"); _sha(value["candidate_head_sha"], "$.candidate_head_sha")
    if not isinstance(value["candidate_tree_sha"], str) or not re.fullmatch(r"[0-9a-f]{40}", value["candidate_tree_sha"]):
        raise SparkAuditError("candidate tree SHA required")
    _hash64(value["plan_sha256"], "$.plan_sha256")
    if value["audited_head_sha"] != "9a87e2e9c8d55e067979d335d69f7a2c525ce303" or value["finding_count"] != len(AUTHOR_FINDING_IDS):
        raise SparkAuditError("author closure denominator must be 18")
    findings = value["findings"]
    if not isinstance(findings, list) or len(findings) != len(AUTHOR_FINDING_IDS):
        raise SparkAuditError("author closure finding denominator")
    required_finding_fields = {"finding_id", "severity", "disposition", "counterexample_id", "executable_counterexample_id", "source_location", "gate_id", "stage", "evidence_row_id", "evidence_row_sha256", "log_sha256"}
    seen: set[str] = set()
    expected_rows: dict[str, Mapping[str, Any]] = {}
    if evidence_rows is not None:
        for row in evidence_rows:
            if not isinstance(row, Mapping) or row.get("case_id") in expected_rows:
                raise SparkAuditError("duplicate/malformed evidence row binding")
            expected_rows[str(row.get("case_id"))] = row
    for index, item in enumerate(findings):
        if not isinstance(item, dict) or set(item) != required_finding_fields:
            raise SparkAuditError("strict author closure finding fields", f"$.findings[{index}]")
        fid = item["finding_id"]
        if fid not in AUTHOR_FINDING_IDS or fid in seen:
            raise SparkAuditError("author closure finding identity mismatch", f"$.findings[{index}].finding_id")
        seen.add(fid)
        if item["severity"] != "P1" or item["disposition"] not in {"FIXED", "DISAGREE"}:
            raise SparkAuditError("P1 author closure must be FIXED or DISAGREE", f"$.findings[{index}]")
        if item["counterexample_id"] not in {"CE-SCHEMA", "CE-GATE", "CE-EVIDENCE"} or not re.fullmatch(r"NF-[0-9]{3}", item["executable_counterexample_id"]):
            raise SparkAuditError("executable counterexample binding required", f"$.findings[{index}]")
        location = item["source_location"]
        if not isinstance(location, str) or ":" not in location or location.startswith(("/", "~")) or _PRIVATE_ARTIFACT_RE.search(location):
            raise SparkAuditError("portable current source location required", f"$.findings[{index}].source_location")
        if item["stage"] not in {"targeted", "full", "fresh"} or not re.fullmatch(r"G-(?:TARGETED|FULL|FRESH)", item["gate_id"]):
            raise SparkAuditError("gate/stage closure binding required", f"$.findings[{index}]")
        if item["stage"] != {"G-TARGETED": "targeted", "G-FULL": "full", "G-FRESH": "fresh"}[item["gate_id"]]:
            raise SparkAuditError("gate/stage mismatch", f"$.findings[{index}]")
        if item["evidence_row_id"] != f"EVID-{item['gate_id']}-EP-UNIT":
            raise SparkAuditError("evidence row identity mismatch", f"$.findings[{index}].evidence_row_id")
        _hash64(item["log_sha256"], f"$.findings[{index}].log_sha256")
        _hash64(item["evidence_row_sha256"], f"$.findings[{index}].evidence_row_sha256")
        if evidence_rows is not None:
            row = expected_rows.get(item["evidence_row_id"])
            if row is None or row.get("log_sha256") != item["log_sha256"] or row.get("stage") != item["stage"] or row.get("gate_id") != item["gate_id"] or canonical_sha256(dict(row)) != item["evidence_row_sha256"]:
                raise SparkAuditError("author closure evidence/log binding mismatch", f"$.findings[{index}]")
    if seen != set(AUTHOR_FINDING_IDS):
        raise SparkAuditError("all 18 actual findings require closure")
    summary = value["disposition_summary"]
    if summary != {"FIXED": 18, "DISAGREE": 0, "FOLLOW_UP": 0}:
        raise SparkAuditError("author closure summary must have no FOLLOW_UP")
    unsigned = dict(value); unsigned["artifact_sha256"] = ""
    if value["artifact_sha256"] != canonical_sha256(unsigned):
        raise SparkAuditError("author closure digest mismatch")
    if root is not None:
        root_path = pathlib.Path(root).resolve()
        try:
            current_head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=str(root_path), text=True, stderr=subprocess.PIPE).strip()
            current_tree = subprocess.check_output(["git", "rev-parse", "HEAD^{tree}"], cwd=str(root_path), text=True, stderr=subprocess.PIPE).strip()
        except (OSError, subprocess.CalledProcessError) as exc:
            raise SparkAuditError("candidate identity unavailable") from exc
        if current_head != value["candidate_head_sha"] or current_tree != value["candidate_tree_sha"]:
            raise SparkAuditError("author closure candidate identity mismatch")
        for item in findings:
            rel = pathlib.PurePosixPath(item["source_location"].split(":", 1)[0])
            path = (root_path / rel).resolve()
            try:
                path.relative_to(root_path)
            except ValueError:
                raise SparkAuditError("closure source escapes candidate root")
            if not path.is_file() or path.is_symlink():
                raise SparkAuditError("closure source location missing")
    return dict(value)


def _bool(value: Any, path: str) -> bool:
    if type(value) is not bool:
        raise SparkAuditError("boolean required", path)
    return value


def _safe_text(value: Any, path: str, *, max_len: int = 4096) -> str:
    """Validate finding prose while permitting literal domain words.

    Raw corrective counterexamples may accurately mention a dispatch
    transcript or an argv token.  Those words are not credentials; private
    absolute paths and control bytes remain forbidden.
    """
    value = _str(value, path, max_len=max_len, public=False)
    if _PRIVATE_ARTIFACT_RE.search(value):
        raise SparkAuditError("privacy-sensitive finding text", path)
    return value


def _timestamp(value: Any, path: str) -> str:
    value = _str(value, path, public=True)
    if not value.endswith("Z"):
        raise SparkAuditError("UTC timestamp required", path)
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise SparkAuditError("RFC3339 timestamp required", path) from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise SparkAuditError("UTC timestamp required", path)
    return value


def audit_requests(mission: Mapping[str, Any]) -> list[dict[str, Any]]:
    audits = mission.get("spark_audits")
    if not isinstance(audits, list) or len(audits) > MAX_SPARK_AUDITS:
        raise SparkAuditError("zero to three Spark audits required")
    mission_id = _id(mission["mission_id"], "$.mission_id")
    requests: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, audit in enumerate(audits):
        aid = _id(audit["id"], f"$.spark_audits[{index}].id")
        if aid in seen:
            raise SparkAuditError("duplicate Spark audit ID", f"$.spark_audits[{index}].id")
        seen.add(aid)
        requests.append({
            "schema": "spark-audit-request.v16", "audit_id": aid, "mission_id": mission_id,
            "domain": _str(audit["domain"], f"$.spark_audits[{index}].domain", public=True),
            "scope": list(audit["scope"]), "max_findings": _int(audit["max_findings"], f"$.spark_audits[{index}].max_findings", minimum=1, maximum=16),
            "assigned_model": "gpt-5.3-codex-spark", "role": "inner-auditor", "permissions": ["read"],
            "fork_turns": "none", "context_mode": "zero-context", "report_only": True, "spawn_index": index + 1,
        })
    return requests


def validate_request(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise SparkAuditError("request object required")
    fields = {"schema", "audit_id", "mission_id", "domain", "scope", "max_findings", "assigned_model", "role", "permissions", "fork_turns", "context_mode", "report_only", "spawn_index"}
    if set(value) != fields:
        raise SparkAuditError("missing/additional request fields")
    if value["schema"] != "spark-audit-request.v16":
        raise SparkAuditError("schema")
    if value["assigned_model"] != "gpt-5.3-codex-spark":
        raise SparkAuditError("Spark model required")
    if value["role"] != "inner-auditor" or value["permissions"] != ["read"]:
        raise SparkAuditError("report-only read permission required")
    if value["fork_turns"] != "none" or value["context_mode"] != "zero-context" or value["report_only"] is not True:
        raise SparkAuditError("fresh report-only context required")
    _id(value["audit_id"], "$.audit_id"); _id(value["mission_id"], "$.mission_id"); _str(value["domain"], "$.domain", public=True)
    if not isinstance(value["scope"], list) or not value["scope"] or any(not isinstance(v, str) for v in value["scope"]):
        raise SparkAuditError("non-empty scope required", "$.scope")
    _int(value["max_findings"], "$.max_findings", minimum=1, maximum=16)
    _int(value["spawn_index"], "$.spawn_index", minimum=1, maximum=MAX_SPARK_AUDITS)
    return dict(value)


def _finding(value: Any, path: str, allowed_scope: Sequence[str]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise SparkAuditError("finding object required", path)
    fields = {"id", "severity", "label", "scope", "requirement", "location", "counterexample", "impact", "smallest_outcome", "acceptance_case", "attribution"}
    if set(value) != fields:
        raise SparkAuditError("missing/additional finding fields", path)
    fid = _id(value["id"], f"{path}.id")
    severity = _str(value["severity"], f"{path}.severity", public=True)
    if severity not in {"P1", "P2", "P3"}:
        raise SparkAuditError("severity", f"{path}.severity")
    label = _str(value["label"], f"{path}.label", public=True)
    if label not in {"BLOCKING", "NON_BLOCKING", "NIT", "QUESTION", "FOLLOW_UP", "CONTRACT_CHALLENGE"}:
        raise SparkAuditError("finding label", f"{path}.label")
    if severity == "P1" and label != "BLOCKING":
        raise SparkAuditError("P1 must be BLOCKING", f"{path}.label")
    if severity != "P1" and label == "BLOCKING":
        raise SparkAuditError("only P1 may be BLOCKING", f"{path}.label")
    scope = _str(value["scope"], f"{path}.scope", public=True)
    if scope not in allowed_scope:
        raise SparkAuditError("finding out of audit scope", f"{path}.scope")
    return {"id": fid, "severity": severity, "label": label, "scope": scope, "requirement": _safe_text(value["requirement"], f"{path}.requirement"), "location": _safe_text(value["location"], f"{path}.location"), "counterexample": _safe_text(value["counterexample"], f"{path}.counterexample"), "impact": _safe_text(value["impact"], f"{path}.impact"), "smallest_outcome": _safe_text(value["smallest_outcome"], f"{path}.smallest_outcome"), "acceptance_case": _safe_text(value["acceptance_case"], f"{path}.acceptance_case"), "attribution": _safe_text(value["attribution"], f"{path}.attribution")}


def validate_result(value: Any, request: Mapping[str, Any]) -> dict[str, Any]:
    request = validate_request(request)
    if not isinstance(value, dict):
        raise SparkAuditError("result object required")
    fields = {"schema", "audit_id", "mission_id", "task_id", "assigned_model", "reasoning_effort", "fork_turns", "context_mode", "report_only", "scope", "findings", "dispositions", "started_at", "ended_at", "elapsed_sec"}
    optional = {"artifact_sha256"}
    if set(value) - fields - optional or fields - set(value):
        raise SparkAuditError("missing/additional result fields")
    if value["schema"] != "spark-audit-result.v16":
        raise SparkAuditError("schema")
    if value["audit_id"] != request["audit_id"] or value["mission_id"] != request["mission_id"]:
        raise SparkAuditError("request/result identity mismatch")
    if value["assigned_model"] != "gpt-5.3-codex-spark" or value["fork_turns"] != "none" or value["context_mode"] != "zero-context" or value["report_only"] is not True:
        raise SparkAuditError("Spark report-only identity required")
    task_id = _str(value["task_id"], "$.task_id", max_len=512, public=True)
    if not (task_id.startswith("/root/") or re.fullmatch(r"spark-task-[0-9]+", task_id)):
        raise SparkAuditError("canonical platform task path required", "$.task_id")
    if value["reasoning_effort"] != "high":
        raise SparkAuditError("high reasoning effort required")
    scope = _str(value["scope"], "$.scope", public=True)
    if scope not in request["scope"]:
        raise SparkAuditError("result scope outside request")
    findings = value["findings"]
    if not isinstance(findings, list) or len(findings) > request["max_findings"]:
        raise SparkAuditError("finding count exceeds bounded request")
    checked = [_finding(item, f"$.findings[{i}]", request["scope"]) for i, item in enumerate(findings)]
    if len({f["id"] for f in checked}) != len(checked):
        raise SparkAuditError("duplicate findings")
    dispositions = value["dispositions"]
    if not isinstance(dispositions, dict) or set(dispositions) != {f["id"] for f in checked}:
        raise SparkAuditError("every finding must be dispositioned exactly once")
    if any(v not in {"FIXED", "DISAGREE", "FOLLOW_UP"} for v in dispositions.values()):
        raise SparkAuditError("invalid disposition")
    elapsed = value["elapsed_sec"]
    if type(elapsed) not in (int, float) or isinstance(elapsed, bool) or not math.isfinite(float(elapsed)) or elapsed < 0:
        raise SparkAuditError("finite elapsed required", "$.elapsed_sec")
    _timestamp(value["started_at"], "$.started_at"); _timestamp(value["ended_at"], "$.ended_at")
    started = datetime.fromisoformat(value["started_at"][:-1] + "+00:00")
    ended = datetime.fromisoformat(value["ended_at"][:-1] + "+00:00")
    if ended < started or abs(float(elapsed) - (ended - started).total_seconds()) > max(0.05, (ended - started).total_seconds() * 0.05):
        raise SparkAuditError("elapsed/timestamp mismatch", "$.elapsed_sec")
    if "artifact_sha256" in value:
        _hash64(value["artifact_sha256"], "$.artifact_sha256")
    result = dict(value); result["findings"] = checked; result["dispositions"] = dict(dispositions)
    return result


def validate_bundle(requests: Sequence[Mapping[str, Any]], results: Sequence[Mapping[str, Any]], *, budget: int = MAX_SPARK_AUDITS) -> list[dict[str, Any]]:
    if type(budget) is not int or not 0 <= budget <= MAX_SPARK_AUDITS:
        raise SparkAuditError("Spark spawn budget must be in [0,3]")
    if len(requests) > budget or len(results) != len(requests):
        raise SparkAuditError("Spark results must exactly match the bounded request set")
    checked_requests = [validate_request(r) for r in requests]
    if len({r["audit_id"] for r in checked_requests}) != len(checked_requests):
        raise SparkAuditError("duplicate Spark audit request")
    by_id = {r["audit_id"]: r for r in checked_requests}
    checked_results = []
    for result in results:
        aid = result.get("audit_id") if isinstance(result, dict) else None
        if aid not in by_id:
            raise SparkAuditError("missing/duplicate/out-of-scope Spark result")
        if any(r.get("audit_id") == aid for r in checked_results):
            raise SparkAuditError("duplicate Spark result")
        checked_results.append(validate_result(result, by_id[aid]))
    if {r["audit_id"] for r in checked_results} != set(by_id):
        raise SparkAuditError("missing Spark result")
    return checked_results


def validate_dispatch_transcript(value: Any, *, expected_head: str | None = None, expected_tree: str | None = None, root: str | pathlib.Path | None = None) -> dict[str, Any]:
    """Validate the immutable Route-2 dispatch transcript.

    Historical records are retained as explicitly unverified facts.  Exactly
    three replacement task identities are accepted, each with request,
    canonical response, snapshot, two-run ordering, result, and disposition
    hashes.  Backend instance/run IDs may be ``unavailable`` only because the
    control plane does not expose them; every other identity is mandatory.
    """
    if not isinstance(value, dict):
        raise SparkAuditError("dispatch transcript object required")
    fields = {"schema", "transcript_version", "lineage_mode", "mission_id", "base_sha", "base_tree", "mission_scope_sha256", "compiled_plan_sha256", "reviewed_head_sha", "reviewed_tree_sha", "snapshot", "audited_input_snapshot", "candidate_binding", "historical_original_audits", "accepted_current_audits", "finding_dispositions", "ordering", "historical_spawn_count", "accepted_current_spawn_count", "transcript_sha256"}
    if set(value) != fields:
        raise SparkAuditError("missing/additional transcript fields")
    if value["schema"] != "dispatch-transcript.v16" or type(value["transcript_version"]) is not int or value["transcript_version"] < 2 or value["lineage_mode"] != "DISPATCH_TRANSCRIPT":
        raise SparkAuditError("transcript schema/version")
    _id(value["mission_id"], "$.mission_id")
    base = _sha(value["base_sha"], "$.base_sha"); base_tree = _sha(value["base_tree"], "$.base_tree"); head = _sha(value["reviewed_head_sha"], "$.reviewed_head_sha"); tree = _sha(value["reviewed_tree_sha"], "$.reviewed_tree_sha")
    if base != "e18439c8dfe01d901895efd09b8b73b6842327a9" or base_tree != "1de79a7c48e6c66f167be54ca9cf387310149f80":
        raise SparkAuditError("frozen base identity changed")
    _hash64(value["mission_scope_sha256"], "$.mission_scope_sha256")
    _hash64(value["compiled_plan_sha256"], "$.compiled_plan_sha256")
    if value["mission_scope_sha256"] != "002c0df679d1238aa32b8a3a838bb1c8af87b0a87af3ef2a8f7ffdb5aacf125b" or value["compiled_plan_sha256"] != "b17e7960c87f160539f14cc76f84d2590af581b7df2c9b0e1cad25e8bbc3a4b9":
        raise SparkAuditError("mission scope/compiled plan identity changed")
    # ``reviewed_head_sha``/``reviewed_tree_sha`` are the immutable audited
    # input snapshot.  ``expected_head``/``expected_tree`` identify the runtime
    # candidate supplied by the caller and are intentionally checked only in
    # the repository-backed binding below; equating them here would make the
    # committed transcript self-referential and defeat the transition contract.
    audited = value["audited_input_snapshot"]
    if not isinstance(audited, dict) or set(audited) != {"identity_mode", "head_sha", "tree_sha", "path_hash_set_sha256"}:
        raise SparkAuditError("strict audited input snapshot fields", "$.audited_input_snapshot")
    if audited["identity_mode"] != "git-exact-object":
        raise SparkAuditError("audited input identity mode", "$.audited_input_snapshot.identity_mode")
    audited_head = _sha(audited["head_sha"], "$.audited_input_snapshot.head_sha")
    audited_tree = _sha(audited["tree_sha"], "$.audited_input_snapshot.tree_sha")
    _hash64(audited["path_hash_set_sha256"], "$.audited_input_snapshot.path_hash_set_sha256")
    if audited_head != "9a87e2e9c8d55e067979d335d69f7a2c525ce303" or audited_tree != "0c27408ee09a6d2b069a0004ef8afade2eb33bda":
        raise SparkAuditError("audited remediation parent identity changed", "$.audited_input_snapshot")
    binding = value["candidate_binding"]
    if not isinstance(binding, dict) or set(binding) != {"mode", "required", "relation"}:
        raise SparkAuditError("strict candidate binding fields", "$.candidate_binding")
    if binding["mode"] != "external_evidence_envelope" or binding["required"] is not True or binding["relation"] != "descendant_of_audited_input":
        raise SparkAuditError("candidate transition binding mismatch", "$.candidate_binding")
    snapshot = value["snapshot"]
    if not isinstance(snapshot, dict) or set(snapshot) != {"identity_mode", "head_sha", "tree_sha", "path_count", "path_hash_set_sha256", "path_hash_set_artifact", "path_hash_set_artifact_sha256"}:
        raise SparkAuditError("strict snapshot fields", "$.snapshot")
    if snapshot["identity_mode"] != "git-exact-object" or snapshot["head_sha"] != head or snapshot["tree_sha"] != tree:
        raise SparkAuditError("snapshot identity mismatch", "$.snapshot")
    if head != audited_head or tree != audited_tree or snapshot["path_hash_set_sha256"] != audited["path_hash_set_sha256"]:
        raise SparkAuditError("reviewed snapshot must equal audited input snapshot", "$.snapshot")
    _int(snapshot["path_count"], "$.snapshot.path_count", minimum=1); _hash64(snapshot["path_hash_set_sha256"], "$.snapshot.path_hash_set_sha256"); _str(snapshot["path_hash_set_artifact"], "$.snapshot.path_hash_set_artifact", public=True); _hash64(snapshot["path_hash_set_artifact_sha256"], "$.snapshot.path_hash_set_artifact_sha256")
    historical = value["historical_original_audits"]; current = value["accepted_current_audits"]
    if not isinstance(historical, list) or len(historical) != 3 or not isinstance(current, list) or len(current) != 3:
        raise SparkAuditError("exactly three historical and three accepted audits required")
    def check_common(item: Mapping[str, Any], path: str) -> None:
        if not isinstance(item, dict): raise SparkAuditError("audit record object required", path)
        for key in ("audit_id", "task_id", "parent_task_id", "assigned_model", "reasoning_effort", "fork_turns", "context_mode"):
            _str(item.get(key), f"{path}.{key}", public=True)
        _str(item["task_id"], f"{path}.task_id", max_len=512, public=True)
        if item["assigned_model"] != "gpt-5.3-codex-spark" or item["reasoning_effort"] != "high" or item["fork_turns"] != "none" or item["context_mode"] != "zero-context" or item.get("report_only") is not True:
            raise SparkAuditError("Spark identity mismatch", path)
    for i, item in enumerate(historical):
        check_common(item, f"$.historical_original_audits[{i}]")
        if set(item) != {"audit_id", "global_spawn_index", "task_id", "parent_task_id", "assigned_model", "reasoning_effort", "fork_turns", "context_mode", "report_only", "status", "accepted", "finding_ids", "missing_facts"}:
            raise SparkAuditError("strict historical audit fields", f"$.historical_original_audits[{i}]")
        expected_id, expected_task, expected_findings = EXPECTED_HISTORICAL[i]
        if (item["audit_id"], item["task_id"], item["finding_ids"]) != (expected_id, expected_task, expected_findings):
            raise SparkAuditError("historical task identity changed", f"$.historical_original_audits[{i}]")
        if item.get("global_spawn_index") != i + 1 or item.get("status") != "HISTORICAL_UNVERIFIED" or item.get("accepted") is not False:
            raise SparkAuditError("historical audit identity/status mismatch", f"$.historical_original_audits[{i}]")
        if not isinstance(item.get("finding_ids"), list) or not item["finding_ids"] or not isinstance(item.get("missing_facts"), list) or not item["missing_facts"]:
            raise SparkAuditError("historical missing-fact record required", f"$.historical_original_audits[{i}]")
    task_ids: set[str] = set()
    for i, item in enumerate(current):
        path = f"$.accepted_current_audits[{i}]"; check_common(item, path)
        if set(item) != {"audit_id", "global_spawn_index", "accepted_spawn_index", "task_id", "parent_task_id", "assigned_model", "reasoning_effort", "fork_turns", "context_mode", "report_only", "scope", "spawn_request_sha256", "spawn_response_sha256", "canonical_response_task_id", "initial_run", "corrective_run", "result_artifact_sha256", "sender_final_envelope_sha256", "snapshot_path_hash_set_sha256", "completion_before_first_production_fix", "finding_ids", "finding_dispositions", "normalized_artifact_path", "normalized_artifact_sha256", "raw_platform_sha256", "author_closure_plan_path", "author_closure_plan_sha256"}:
            raise SparkAuditError("strict accepted audit fields", path)
        if item.get("global_spawn_index") != i + 4 or item.get("accepted_spawn_index") != i + 1 or item["task_id"] in task_ids:
            raise SparkAuditError("accepted spawn index/task identity mismatch", path)
        task_ids.add(item["task_id"])
        expected = EXPECTED_CURRENT[i]
        for key in ("audit_id", "task_id", "parent_task_id", "scope", "finding_ids", "spawn_request_sha256", "spawn_response_sha256", "snapshot_path_hash_set_sha256"):
            if item.get(key) != expected[key]:
                raise SparkAuditError("accepted audit fact changed", f"{path}.{key}")
        if item.get("canonical_response_task_id") != item["task_id"]:
            raise SparkAuditError("canonical response task mismatch", f"{path}.canonical_response_task_id")
        for key in ("spawn_request_sha256", "spawn_response_sha256", "result_artifact_sha256", "sender_final_envelope_sha256", "snapshot_path_hash_set_sha256", "normalized_artifact_sha256", "raw_platform_sha256", "author_closure_plan_sha256"):
            _hash64(item.get(key), f"{path}.{key}")
        if item["snapshot_path_hash_set_sha256"] != snapshot["path_hash_set_sha256"]:
            raise SparkAuditError("child snapshot hash mismatch", path)
        if item["snapshot_path_hash_set_sha256"] != audited["path_hash_set_sha256"]:
            raise SparkAuditError("accepted result is not bound to audited input snapshot", path)
        for run_name in ("initial_run", "corrective_run"):
            run = item.get(run_name)
            if not isinstance(run, dict) or set(run) != {"request_sha256", "final_envelope_sha256", "status", "run_order"}:
                raise SparkAuditError("strict child run fields", f"{path}.{run_name}")
            _hash64(run["request_sha256"], f"{path}.{run_name}.request_sha256"); _hash64(run["final_envelope_sha256"], f"{path}.{run_name}.final_envelope_sha256")
            _int(run["run_order"], f"{path}.{run_name}.run_order", minimum=1)
        if item["initial_run"]["status"] != "INVALID_AUDIT" or item["corrective_run"]["status"] != "AUDIT_COMPLETE" or item["corrective_run"]["run_order"] != 2:
            raise SparkAuditError("initial/corrective run ordering required", path)
        if item["initial_run"]["run_order"] != 1:
            raise SparkAuditError("initial run must precede corrective run", path)
        if item["result_artifact_sha256"] != item["corrective_run"]["final_envelope_sha256"] or item["sender_final_envelope_sha256"] != item["result_artifact_sha256"]:
            raise SparkAuditError("result/final envelope hash mismatch", path)
        if "backend_instance_id" in item or "backend_run_id" in item:
            raise SparkAuditError("backend IDs are not present in the dispatch transcript", path)
        if item["initial_run"]["request_sha256"] != expected["initial_request_sha256"] or item["initial_run"]["final_envelope_sha256"] != expected["initial_final_envelope_sha256"] or item["corrective_run"]["request_sha256"] != expected["corrective_request_sha256"] or item["corrective_run"]["final_envelope_sha256"] != expected["corrective_final_envelope_sha256"]:
            raise SparkAuditError("corrective audit facts changed", path)
        if item.get("completion_before_first_production_fix") is not True:
            raise SparkAuditError("audit completion must precede production fix", path)
        finding_dispositions = item.get("finding_dispositions")
        if not isinstance(finding_dispositions, dict) or set(finding_dispositions) != set(item["finding_ids"]) or any(value != "FOLLOW_UP" for value in finding_dispositions.values()):
            raise SparkAuditError("raw current finding FOLLOW_UP disposition binding required", path)
        if item["normalized_artifact_path"] != f"codex/v16/contracts/spark_result_{i + 1 and chr(65 + i)}.v16.json" or item["author_closure_plan_path"] != "codex/v16/contracts/author_closure_plan.v16.json":
            raise SparkAuditError("normalized/closure-plan path binding changed", path)
        if item["raw_platform_sha256"] != EXPECTED_RAW_PLATFORM_SHA256[item["audit_id"]]:
            raise SparkAuditError("raw platform hash binding changed", path)
        expected_normalized_ids = NORMALIZED_FINDING_IDS[item["audit_id"]]
        if item["finding_ids"] != expected_normalized_ids:
            raise SparkAuditError("actual normalized finding IDs changed", path)
        if item["author_closure_plan_sha256"] != "d434591880ed4b77114823e3aa809ae5a8489b7aed87ee89924fe4c61e005508":
            raise SparkAuditError("closure plan hash binding changed", path)
    if value["historical_spawn_count"] != 6 or value["accepted_current_spawn_count"] != 3:
        raise SparkAuditError("historical/current spawn denominator mismatch")
    if set(task_ids) != {item["task_id"] for item in current}:
        raise SparkAuditError("duplicate accepted task IDs")
    dispositions = value["finding_dispositions"]
    if not isinstance(dispositions, dict) or set(dispositions) != set(HISTORICAL_FINDINGS):
        raise SparkAuditError("all nineteen individual finding dispositions required", "$.finding_dispositions")
    if any(v not in {"HISTORICAL_UNVERIFIED", "FOLLOW_UP", "FIXED", "DISAGREE", "INFRA_BLOCKED"} for v in dispositions.values()):
        raise SparkAuditError("invalid finding disposition", "$.finding_dispositions")
    if any(dispositions[fid] != "HISTORICAL_UNVERIFIED" for fid in HISTORICAL_FINDINGS):
        raise SparkAuditError("historical finding dispositions are immutable", "$.finding_dispositions")
    ordering = value["ordering"]
    if not isinstance(ordering, dict) or ordering.get("baseline_reproduction_completed_before_first_edit") is not True or ordering.get("replacement_audits_completed_before_first_production_fix") is not True:
        raise SparkAuditError("completion ordering mismatch", "$.ordering")
    digest_value = dict(value); digest_value["transcript_sha256"] = ""
    expected_digest = hashlib.sha256(canonical_json(digest_value).encode("utf-8")).hexdigest()
    if value["transcript_sha256"] != expected_digest:
        raise SparkAuditError("transcript SHA mismatch", "$.transcript_sha256")
    if root is not None:
        root_path = pathlib.Path(root).resolve()
        if expected_head is None or expected_tree is None:
            raise SparkAuditError("runtime candidate head/tree required when root is supplied")
        try:
            current_head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=str(root_path), text=True, stderr=subprocess.PIPE).strip()
            current_tree = subprocess.check_output(["git", "rev-parse", "HEAD^{tree}"], cwd=str(root_path), text=True, stderr=subprocess.PIPE).strip()
        except (OSError, subprocess.CalledProcessError) as exc:
            raise SparkAuditError("candidate Git identity unavailable") from exc
        if current_head != expected_head or current_tree != expected_tree:
            raise SparkAuditError("runtime candidate identity mismatch")
        audited_object_tree = subprocess.check_output(["git", "rev-parse", f"{audited_head}^{{tree}}"], cwd=str(root_path), text=True, stderr=subprocess.PIPE).strip()
        if audited_object_tree != audited_tree:
            raise SparkAuditError("audited head/tree object mismatch")
        base_object_tree = subprocess.check_output(["git", "rev-parse", f"{base}^{{tree}}"], cwd=str(root_path), text=True, stderr=subprocess.PIPE).strip()
        if base_object_tree != base_tree:
            raise SparkAuditError("base head/tree object mismatch")
        if subprocess.run(["git", "merge-base", "--is-ancestor", base, audited_head], cwd=str(root_path), stdout=subprocess.PIPE, stderr=subprocess.PIPE).returncode != 0:
            raise SparkAuditError("audited input is not a descendant of frozen base")
        if subprocess.run(["git", "merge-base", "--is-ancestor", audited_head, expected_head], cwd=str(root_path), stdout=subprocess.PIPE, stderr=subprocess.PIPE).returncode != 0:
            raise SparkAuditError("candidate is not a descendant of audited input snapshot")
        mission_path = root_path / "codex/v16/fixtures/mission.valid.json"
        try:
            mission = json.loads(mission_path.read_text(encoding="utf-8"))
            from .compiler import compile_mission
            from .contracts import canonical_sha256, validate_counterexample_linkage, validate_mission
            checked_mission = validate_mission(mission); validate_counterexample_linkage(checked_mission)
            if checked_mission["scope"]["exact_head"] != base or checked_mission["scope"].get("tree_sha") != base_tree or canonical_sha256(checked_mission["scope"]) != value["mission_scope_sha256"]:
                raise SparkAuditError("mission scope binding mismatch")
            plan = compile_mission(checked_mission)
            if canonical_sha256(plan) != value["compiled_plan_sha256"]:
                raise SparkAuditError("compiled plan binding mismatch")
        except SparkAuditError:
            raise
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise SparkAuditError("mission scope/plan binding unavailable") from exc
        manifest_path = root_path / "manifest.json"
        transcript_path = root_path / "codex/v16/contracts/v16_dispatch_transcript.json"
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            declared_hash = manifest["files"]["codex/v16/contracts/v16_dispatch_transcript.json"]
            actual_hash = hashlib.sha256(transcript_path.read_bytes()).hexdigest()
        except (OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
            raise SparkAuditError("manifest/transcript binding unavailable") from exc
        if declared_hash != actual_hash:
            raise SparkAuditError("manifest transcript hash mismatch")
        snapshot_artifact = root_path / snapshot["path_hash_set_artifact"]
        if snapshot_artifact.is_symlink() or not snapshot_artifact.is_file() or hashlib.sha256(snapshot_artifact.read_bytes()).hexdigest() != snapshot["path_hash_set_artifact_sha256"]:
            raise SparkAuditError("snapshot path/hash-set artifact mismatch")
        if hashlib.sha256(snapshot_artifact.read_bytes()).hexdigest() != snapshot["path_hash_set_sha256"]:
            raise SparkAuditError("snapshot path/hash-set content digest mismatch")
        for index, item in enumerate(current):
            normalized_path = root_path / item["normalized_artifact_path"]
            if normalized_path.is_symlink() or not normalized_path.is_file() or hashlib.sha256(normalized_path.read_bytes()).hexdigest() != item["normalized_artifact_sha256"]:
                raise SparkAuditError("normalized Spark artifact hash/path mismatch", f"$.accepted_current_audits[{index}]")
            try:
                normalized = json.loads(normalized_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise SparkAuditError("normalized Spark artifact unreadable", f"$.accepted_current_audits[{index}]") from exc
            _validate_normalized_result(normalized, audit_id=item["audit_id"], expected_raw_sha=item["raw_platform_sha256"])
            plan_path = root_path / item["author_closure_plan_path"]
            if plan_path.is_symlink() or not plan_path.is_file() or hashlib.sha256(plan_path.read_bytes()).hexdigest() != item["author_closure_plan_sha256"]:
                raise SparkAuditError("author closure plan hash/path mismatch", f"$.accepted_current_audits[{index}]")
            try:
                plan = json.loads(plan_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise SparkAuditError("author closure plan unreadable", f"$.accepted_current_audits[{index}]") from exc
            validate_author_closure_plan(plan, root=root_path)
    return dict(value)


validate_spark_request = validate_request
validate_spark_result = validate_result

"""Strict, versioned V16 productivity contracts.

The validators intentionally avoid third-party JSON-schema dependencies so a
fresh clone on Python 3.9 can validate packets before any gate executes.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from typing import Any, Iterable, Mapping, Sequence

SCHEMA_VERSION = "16"
ID_RE = re.compile(r"[A-Za-z][A-Za-z0-9_.:/-]{1,127}\Z")
SHA_RE = re.compile(r"[0-9a-f]{40}\Z")
HEX_RE = re.compile(r"[0-9a-fA-F]+\Z")
FORBIDDEN_TEXT_RE = re.compile(
    r"(?:gh[pso]_[A-Za-z0-9]{12,}|/home/|/Users/|prompt|token|credential|session[_-]?id|transcript)",
    re.I,
)


class ContractError(ValueError):
    """Raised when a contract violates its strict schema or invariants."""

    def __init__(self, message: str, path: str = "$") -> None:
        self.path = path
        super().__init__(f"{path}: {message}")


def canonical_json(value: Any) -> str:
    """Return deterministic UTF-8 JSON without executable content."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _obj(value: Any, path: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise ContractError("object required", path)
    return value


def _keys(value: Mapping[str, Any], required: Iterable[str], allowed: Iterable[str], path: str) -> None:
    required_set, allowed_set = set(required), set(allowed)
    missing = sorted(required_set - set(value))
    extra = sorted(set(value) - allowed_set)
    if missing:
        raise ContractError("missing field(s): " + ",".join(missing), path)
    if extra:
        raise ContractError("additionalProperties forbidden: " + ",".join(extra), path)


def _str(value: Any, path: str, *, nonempty: bool = True, max_len: int = 4096, public: bool = False) -> str:
    if not isinstance(value, str):
        raise ContractError("string required", path)
    if nonempty and not value:
        raise ContractError("non-empty string required", path)
    if len(value) > max_len:
        raise ContractError("string too long", path)
    if any(ord(c) < 0x20 and c not in "\t\n" for c in value):
        raise ContractError("control characters forbidden", path)
    if public and FORBIDDEN_TEXT_RE.search(value):
        raise ContractError("privacy-sensitive text forbidden", path)
    return value


def _bool(value: Any, path: str) -> bool:
    if type(value) is not bool:  # bool is an int subclass; reject bool-as-int everywhere.
        raise ContractError("boolean required", path)
    return value


def _int(value: Any, path: str, *, minimum: int | None = None, maximum: int | None = None) -> int:
    if type(value) is not int:
        raise ContractError("integer required (bool is not an integer)", path)
    if minimum is not None and value < minimum:
        raise ContractError(f"must be >= {minimum}", path)
    if maximum is not None and value > maximum:
        raise ContractError(f"must be <= {maximum}", path)
    return value


def _number(value: Any, path: str, *, minimum: float | None = None) -> float | int:
    if type(value) not in (int, float) or isinstance(value, bool):
        raise ContractError("finite number required", path)
    if not math.isfinite(float(value)):
        raise ContractError("NaN/Inf forbidden", path)
    if minimum is not None and value < minimum:
        raise ContractError(f"must be >= {minimum}", path)
    return value


def _list(value: Any, path: str, *, nonempty: bool = False) -> list[Any]:
    if not isinstance(value, list):
        raise ContractError("array required", path)
    if nonempty and not value:
        raise ContractError("non-empty array required", path)
    return value


def _id(value: Any, path: str) -> str:
    value = _str(value, path, max_len=128, public=True)
    if not ID_RE.fullmatch(value):
        raise ContractError("invalid canonical ID", path)
    return value


def _unique(values: Sequence[str], path: str) -> None:
    if len(set(values)) != len(values):
        raise ContractError("duplicate IDs/semantics", path)


def _sha(value: Any, path: str, *, allow_empty: bool = False) -> str:
    value = _str(value, path, nonempty=not allow_empty, max_len=64, public=True)
    if allow_empty and value == "":
        return value
    if not SHA_RE.fullmatch(value):
        raise ContractError("40-hex Git SHA required", path)
    return value


def _relative_path(value: Any, path: str, *, allow_dot: bool = False) -> str:
    value = _str(value, path, max_len=512, public=True)
    if value.startswith(("/", "~")) or "\\" in value or "\x00" in value:
        raise ContractError("portable relative path required", path)
    parts = value.split("/")
    if any(p in ("", ".", "..") for p in parts):
        if allow_dot and value == ".":
            return value
        raise ContractError("path traversal/empty component forbidden", path)
    return value


def _strings(value: Any, path: str, *, nonempty: bool = False, public: bool = False) -> list[str]:
    values = _list(value, path, nonempty=nonempty)
    result = [_str(v, f"{path}[{i}]", public=public) for i, v in enumerate(values)]
    _unique(result, path)
    return result


def _map_str_str(value: Any, path: str) -> dict[str, str]:
    value = _obj(value, path)
    result: dict[str, str] = {}
    for key, item in value.items():
        key = _str(key, f"{path}.<key>", max_len=128, public=True)
        result[key] = _str(item, f"{path}.{key}", public=True)
    return result


def validate_scope(value: Any, path: str = "$.scope") -> dict[str, Any]:
    obj = _obj(value, path)
    _keys(obj, ("paths", "exact_head"), ("paths", "exact_head", "tree_sha"), path)
    paths = [_relative_path(v, f"{path}.paths[{i}]", allow_dot=True) for i, v in enumerate(_list(obj["paths"], f"{path}.paths", nonempty=True))]
    _unique(paths, f"{path}.paths")
    exact_head = _sha(obj["exact_head"], f"{path}.exact_head")
    result = {"paths": paths, "exact_head": exact_head}
    if "tree_sha" in obj:
        tree = _str(obj["tree_sha"], f"{path}.tree_sha", max_len=64, public=True)
        if not HEX_RE.fullmatch(tree) or len(tree) != 40:
            raise ContractError("40-hex tree SHA required", f"{path}.tree_sha")
        result["tree_sha"] = tree.lower()
    return result


def validate_reviewer(value: Any, path: str = "$.reviewer_separation") -> dict[str, Any]:
    obj = _obj(value, path)
    _keys(obj, ("independent_model", "fork_turns", "report_only"), ("independent_model", "fork_turns", "report_only"), path)
    if _str(obj["independent_model"], f"{path}.independent_model", public=True) != "gpt-5.6-sol":
        raise ContractError("Independent Sol reviewer required", f"{path}.independent_model")
    if _str(obj["fork_turns"], f"{path}.fork_turns", public=True) != "none":
        raise ContractError("fresh zero-context fork_turns=none required", f"{path}.fork_turns")
    if _bool(obj["report_only"], f"{path}.report_only") is not True:
        raise ContractError("reviewer must be report-only", f"{path}.report_only")
    return dict(obj)


def validate_invariant(value: Any, path: str = "$.invariants[]") -> dict[str, Any]:
    obj = _obj(value, path)
    _keys(obj, ("id", "description", "blocking", "counterexample_ids"), ("id", "description", "blocking", "counterexample_ids"), path)
    result = {
        "id": _id(obj["id"], f"{path}.id"),
        "description": _str(obj["description"], f"{path}.description", public=True),
        "blocking": _bool(obj["blocking"], f"{path}.blocking"),
        "counterexample_ids": _strings(obj["counterexample_ids"], f"{path}.counterexample_ids", nonempty=True, public=True),
    }
    return result


def validate_counterexample(value: Any, path: str = "$.counterexamples[]") -> dict[str, Any]:
    obj = _obj(value, path)
    fields = ("id", "semantics", "description", "entrypoint_id", "gate_id", "why_red", "cost", "denominator", "expected")
    _keys(obj, fields, fields, path)
    expected = _str(obj["expected"], f"{path}.expected", public=True)
    if expected not in {"RED", "GREEN"}:
        raise ContractError("expected must be RED or GREEN", f"{path}.expected")
    denominator = _int(obj["denominator"], f"{path}.denominator", minimum=1)
    return {
        "id": _id(obj["id"], f"{path}.id"),
        "semantics": _str(obj["semantics"], f"{path}.semantics", public=True),
        "description": _str(obj["description"], f"{path}.description", public=True),
        "entrypoint_id": _id(obj["entrypoint_id"], f"{path}.entrypoint_id"),
        "gate_id": _id(obj["gate_id"], f"{path}.gate_id"),
        "why_red": _str(obj["why_red"], f"{path}.why_red", public=True),
        "cost": _str(obj["cost"], f"{path}.cost", public=True),
        "denominator": denominator,
        "expected": expected,
    }


def validate_entrypoint(value: Any, path: str = "$.entrypoints[]") -> dict[str, Any]:
    obj = _obj(value, path)
    fields = ("id", "argv", "cwd", "env", "timeout_sec", "stop_conditions")
    _keys(obj, fields, fields, path)
    argv = _list(obj["argv"], f"{path}.argv", nonempty=True)
    argv_result = []
    for i, arg in enumerate(argv):
        arg = _str(arg, f"{path}.argv[{i}]", max_len=4096)
        if not arg or "\x00" in arg:
            raise ContractError("unsafe argv item", f"{path}.argv[{i}]")
        argv_result.append(arg)
    cwd = _relative_path(obj["cwd"], f"{path}.cwd", allow_dot=True)
    env = _map_str_str(obj["env"], f"{path}.env")
    timeout = _number(obj["timeout_sec"], f"{path}.timeout_sec", minimum=0.001)
    stops = _strings(obj["stop_conditions"], f"{path}.stop_conditions", nonempty=True, public=True)
    return {"id": _id(obj["id"], f"{path}.id"), "argv": argv_result, "cwd": cwd, "env": env, "timeout_sec": timeout, "stop_conditions": stops}


def validate_gate(value: Any, path: str = "$.gates[]") -> dict[str, Any]:
    obj = _obj(value, path)
    fields = ("id", "stage", "depends_on", "entrypoint_ids", "blocking", "reusable")
    _keys(obj, fields, fields, path)
    stage = _str(obj["stage"], f"{path}.stage", public=True)
    if stage not in {"targeted", "full", "fresh"}:
        raise ContractError("stage must be targeted/full/fresh", f"{path}.stage")
    depends = _strings(obj["depends_on"], f"{path}.depends_on", public=True)
    entrypoints = _strings(obj["entrypoint_ids"], f"{path}.entrypoint_ids", nonempty=True, public=True)
    return {"id": _id(obj["id"], f"{path}.id"), "stage": stage, "depends_on": depends, "entrypoint_ids": entrypoints, "blocking": _bool(obj["blocking"], f"{path}.blocking"), "reusable": _bool(obj["reusable"], f"{path}.reusable")}


def validate_acceptance(value: Any, path: str = "$.acceptance[]") -> dict[str, Any]:
    obj = _obj(value, path)
    fields = ("id", "invariant_id", "counterexample_id", "entrypoint_id", "gate_id", "blocking", "why_red", "cost", "denominator", "red_meaning", "green_meaning")
    _keys(obj, fields, fields, path)
    return {
        "id": _id(obj["id"], f"{path}.id"),
        "invariant_id": _id(obj["invariant_id"], f"{path}.invariant_id"),
        "counterexample_id": _id(obj["counterexample_id"], f"{path}.counterexample_id"),
        "entrypoint_id": _id(obj["entrypoint_id"], f"{path}.entrypoint_id"),
        "gate_id": _id(obj["gate_id"], f"{path}.gate_id"),
        "blocking": _bool(obj["blocking"], f"{path}.blocking"),
        "why_red": _str(obj["why_red"], f"{path}.why_red", public=True),
        "cost": _str(obj["cost"], f"{path}.cost", public=True),
        "denominator": _int(obj["denominator"], f"{path}.denominator", minimum=1),
        "red_meaning": _str(obj["red_meaning"], f"{path}.red_meaning", public=True),
        "green_meaning": _str(obj["green_meaning"], f"{path}.green_meaning", public=True),
    }


def validate_spark_audit(value: Any, path: str = "$.spark_audits[]") -> dict[str, Any]:
    obj = _obj(value, path)
    fields = ("id", "domain", "scope", "max_findings", "required", "request_schema")
    _keys(obj, fields, fields, path)
    scope = _strings(obj["scope"], f"{path}.scope", nonempty=True, public=True)
    max_findings = _int(obj["max_findings"], f"{path}.max_findings", minimum=1, maximum=16)
    request_schema = _str(obj["request_schema"], f"{path}.request_schema", public=True)
    if request_schema != "spark-audit-request.v16":
        raise ContractError("wrong Spark request schema", f"{path}.request_schema")
    return {"id": _id(obj["id"], f"{path}.id"), "domain": _str(obj["domain"], f"{path}.domain", public=True), "scope": scope, "max_findings": max_findings, "required": _bool(obj["required"], f"{path}.required"), "request_schema": request_schema}


def validate_evidence_budget(value: Any, path: str = "$.evidence_budget") -> dict[str, Any]:
    obj = _obj(value, path)
    _keys(obj, ("checks",), ("checks",), path)
    checks = _list(obj["checks"], f"{path}.checks", nonempty=True)
    result = []
    ids: list[str] = []
    for i, item in enumerate(checks):
        ipath = f"{path}.checks[{i}]"
        o = _obj(item, ipath)
        fields = ("id", "why_red", "cost", "denominator")
        _keys(o, fields, fields, ipath)
        cid = _id(o["id"], f"{ipath}.id")
        ids.append(cid)
        result.append({"id": cid, "why_red": _str(o["why_red"], f"{ipath}.why_red", public=True), "cost": _str(o["cost"], f"{ipath}.cost", public=True), "denominator": _int(o["denominator"], f"{ipath}.denominator", minimum=1)})
    _unique(ids, f"{path}.checks")
    return {"checks": result}


def validate_mission(value: Any) -> dict[str, Any]:
    path = "$"
    obj = _obj(value, path)
    fields = ("schema", "mission_id", "milestone", "objective", "owner", "assigned_model", "role", "permissions", "scope", "reviewer_separation", "operating_domain", "invariants", "counterexamples", "entrypoints", "gates", "acceptance", "non_goals", "evidence_budget", "rollback", "stop_conditions", "spark_audits")
    _keys(obj, fields, fields, path)
    if _str(obj["schema"], "$.schema", public=True) != "mission.v16":
        raise ContractError("schema must be mission.v16", "$.schema")
    assigned = _str(obj["assigned_model"], "$.assigned_model", public=True)
    if assigned != "gpt-5.6-luna":
        raise ContractError("Luna is the sole writer/execution model", "$.assigned_model")
    role = _str(obj["role"], "$.role", public=True)
    if role != "writer":
        raise ContractError("mission role must be writer", "$.role")
    result: dict[str, Any] = {
        "schema": "mission.v16",
        "mission_id": _id(obj["mission_id"], "$.mission_id"),
        "milestone": _str(obj["milestone"], "$.milestone", public=True),
        "objective": _str(obj["objective"], "$.objective", public=True),
        "owner": _id(obj["owner"], "$.owner"),
        "assigned_model": assigned,
        "role": role,
        "permissions": _strings(obj["permissions"], "$.permissions", nonempty=True, public=True),
        "scope": validate_scope(obj["scope"]),
        "reviewer_separation": validate_reviewer(obj["reviewer_separation"]),
        "operating_domain": _str(obj["operating_domain"], "$.operating_domain", public=True),
        "invariants": [validate_invariant(v, f"$.invariants[{i}]") for i, v in enumerate(_list(obj["invariants"], "$.invariants", nonempty=True))],
        "counterexamples": [validate_counterexample(v, f"$.counterexamples[{i}]") for i, v in enumerate(_list(obj["counterexamples"], "$.counterexamples", nonempty=True))],
        "entrypoints": [validate_entrypoint(v, f"$.entrypoints[{i}]") for i, v in enumerate(_list(obj["entrypoints"], "$.entrypoints", nonempty=True))],
        "gates": [validate_gate(v, f"$.gates[{i}]") for i, v in enumerate(_list(obj["gates"], "$.gates", nonempty=True))],
        "acceptance": [validate_acceptance(v, f"$.acceptance[{i}]") for i, v in enumerate(_list(obj["acceptance"], "$.acceptance", nonempty=True))],
        "non_goals": _strings(obj["non_goals"], "$.non_goals", nonempty=True, public=True),
        "evidence_budget": validate_evidence_budget(obj["evidence_budget"]),
        "rollback": _str(obj["rollback"], "$.rollback", public=True),
        "stop_conditions": _strings(obj["stop_conditions"], "$.stop_conditions", nonempty=True, public=True),
        "spark_audits": [validate_spark_audit(v, f"$.spark_audits[{i}]") for i, v in enumerate(_list(obj["spark_audits"], "$.spark_audits", nonempty=True))],
    }
    for key in ("invariants", "counterexamples", "entrypoints", "gates", "acceptance", "spark_audits"):
        _unique([v["id"] for v in result[key]], f"$.{key}")
    _unique([v["semantics"] for v in result["counterexamples"]], "$.counterexamples.semantics")
    _unique([v["id"] for v in result["evidence_budget"]["checks"]], "$.evidence_budget.checks")
    return result


def validate_counterexample_linkage(mission: Mapping[str, Any]) -> None:
    """Validate cross-object linkage after strict per-object schema checks."""
    inv = {x["id"]: x for x in mission["invariants"]}
    ce = {x["id"]: x for x in mission["counterexamples"]}
    ep = {x["id"] for x in mission["entrypoints"]}
    gates = {x["id"] for x in mission["gates"]}
    accepts = {x["id"]: x for x in mission["acceptance"]}
    for item in mission["invariants"]:
        if not item["counterexample_ids"]:
            raise ContractError("invariant must have counterexamples", f"$.invariants[{item['id']}]")
        for cid in item["counterexample_ids"]:
            if cid not in ce:
                raise ContractError("unknown counterexample", f"$.invariants[{item['id']}].counterexample_ids")
    for item in mission["counterexamples"]:
        if item["entrypoint_id"] not in ep:
            raise ContractError("unknown entrypoint", f"$.counterexamples[{item['id']}]..entrypoint_id")
        if item["gate_id"] not in gates:
            raise ContractError("unknown gate", f"$.counterexamples[{item['id']}].gate_id")
    for item in mission["acceptance"]:
        if item["invariant_id"] not in inv or item["counterexample_id"] not in ce:
            raise ContractError("acceptance references unknown invariant/counterexample", f"$.acceptance[{item['id']}]")
        if item["entrypoint_id"] not in ep or item["gate_id"] not in gates:
            raise ContractError("acceptance references unknown entrypoint/gate", f"$.acceptance[{item['id']}]")
        if item["blocking"] and not inv[item["invariant_id"]]["blocking"]:
            raise ContractError("blocking acceptance must map to blocking invariant", f"$.acceptance[{item['id']}]")
    covered = {a["counterexample_id"] for a in mission["acceptance"]}
    missing = sorted(set(ce) - covered)
    if missing:
        raise ContractError("counterexample not covered by acceptance", "$.acceptance")
    for gate in mission["gates"]:
        for dep in gate["depends_on"]:
            if dep not in gates:
                raise ContractError("unknown gate dependency", f"$.gates[{gate['id']}].depends_on")
        for eid in gate["entrypoint_ids"]:
            if eid not in ep:
                raise ContractError("unknown gate entrypoint", f"$.gates[{gate['id']}].entrypoint_ids")
    # Acyclic dependency check (the compiler also emits the deterministic order).
    graph = {g["id"]: set(g["depends_on"]) for g in mission["gates"]}
    visiting: set[str] = set(); visited: set[str] = set()
    def visit(node: str) -> None:
        if node in visiting:
            raise ContractError("cyclic gate dependency", f"$.gates[{node}].depends_on")
        if node in visited:
            return
        visiting.add(node)
        for dep in sorted(graph[node]):
            visit(dep)
        visiting.remove(node); visited.add(node)
    for gid in sorted(graph):
        visit(gid)


def validate_schema_document(value: Any, expected_schema: str | None = None) -> dict[str, Any]:
    obj = _obj(value, "$")
    schema = obj.get("schema")
    if expected_schema and schema != expected_schema:
        raise ContractError("unexpected schema", "$.schema")
    if schema == "mission.v16":
        result = validate_mission(value); validate_counterexample_linkage(result); return result
    validators = {
        "invariant.v16": validate_invariant,
        "counterexample.v16": validate_counterexample,
        "gate.v16": validate_gate,
        "acceptance.v16": validate_acceptance,
        "spark-audit-request.v16": validate_spark_audit,
    }
    if schema in validators:
        return validators[schema](value)
    raise ContractError("unknown schema", "$.schema")

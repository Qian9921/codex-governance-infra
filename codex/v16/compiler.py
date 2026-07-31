"""Mission compiler: deterministic validation and gate-DAG planning only."""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
from typing import Any, Mapping

from .contracts import ContractError, canonical_sha256, canonical_json, validate_mission, validate_counterexample_linkage


class CompileError(ContractError):
    pass


def _topological_order(gates: list[Mapping[str, Any]]) -> list[str]:
    by_id = {g["id"]: g for g in gates}
    indegree = {gid: len(g["depends_on"]) for gid, g in by_id.items()}
    children: dict[str, list[str]] = {gid: [] for gid in by_id}
    for gid, gate in by_id.items():
        for dep in gate["depends_on"]:
            if dep not in by_id:
                raise CompileError("unknown gate dependency", f"$.gates[{gid}].depends_on")
            children[dep].append(gid)
    ready = sorted(gid for gid, degree in indegree.items() if degree == 0)
    result: list[str] = []
    while ready:
        node = ready.pop(0)
        result.append(node)
        for child in sorted(children[node]):
            indegree[child] -= 1
            if indegree[child] == 0:
                ready.append(child)
                ready.sort()
    if len(result) != len(by_id):
        raise CompileError("cyclic gate dependency", "$.gates")
    return result


def compile_mission(mission: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and compile a mission without executing any entrypoint."""
    normalized = validate_mission(mission)
    validate_counterexample_linkage(normalized)
    by_gate = {g["id"]: g for g in normalized["gates"]}
    by_entry = {e["id"]: e for e in normalized["entrypoints"]}
    by_ce = {c["id"]: c for c in normalized["counterexamples"]}
    by_acceptance = {a["id"]: a for a in normalized["acceptance"]}

    # Three bounded Spark briefs are mandatory for this milestone. A mission may
    # describe fewer only for non-V16 schemas, which strict validation rejects.
    audits = normalized["spark_audits"]
    if len(audits) != 3:
        raise CompileError("exactly three bounded Spark audit briefs required", "$.spark_audits")
    if any(not a["required"] for a in audits):
        raise CompileError("all Spark audits must be required", "$.spark_audits")

    # Gate order is a staged DAG: targeted -> full -> fresh. A stage cannot
    # depend on a later stage or run a full/fresh gate without lower stages.
    rank = {"targeted": 0, "full": 1, "fresh": 2}
    for gate in normalized["gates"]:
        for dep in gate["depends_on"]:
            if rank[by_gate[dep]["stage"]] > rank[gate["stage"]]:
                raise CompileError("gate depends on a later stage", f"$.gates[{gate['id']}]..depends_on")
        if gate["stage"] == "full" and not gate["depends_on"]:
            raise CompileError("full gate cannot run before targeted gate", f"$.gates[{gate['id']}]..depends_on")
        if gate["stage"] == "fresh" and not gate["depends_on"]:
            raise CompileError("fresh gate cannot run before lower stages", f"$.gates[{gate['id']}]..depends_on")
    order = _topological_order(normalized["gates"])
    if not any(by_gate[gid]["stage"] == "targeted" for gid in order):
        raise CompileError("at least one targeted gate required", "$.gates")
    if any(by_gate[gid]["stage"] == "full" for gid in order) and not any(by_gate[gid]["stage"] == "fresh" for gid in order):
        raise CompileError("full/fresh gate contract incomplete", "$.gates")
    def ancestors(gid: str) -> set[str]:
        seen: set[str] = set(); pending = list(by_gate[gid]["depends_on"])
        while pending:
            dep = pending.pop()
            if dep in seen:
                continue
            seen.add(dep); pending.extend(by_gate[dep]["depends_on"])
        return seen
    fresh_ids = [g["id"] for g in normalized["gates"] if g["stage"] == "fresh"]
    full_ids = {g["id"] for g in normalized["gates"] if g["stage"] == "full"}
    targeted_ids = {g["id"] for g in normalized["gates"] if g["stage"] == "targeted"}
    if not fresh_ids:
        raise CompileError("fresh gate required", "$.gates")
    connected: set[str] = set(fresh_ids)
    for fresh_id in fresh_ids:
        closure = ancestors(fresh_id)
        if not closure & full_ids or not closure & targeted_ids:
            raise CompileError("fresh gate must transitively depend on full and targeted", f"$.gates[{fresh_id}].depends_on")
        connected.update(closure)
    if connected != set(by_gate):
        raise CompileError("disconnected gate component", "$.gates")

    # Ensure every entrypoint is a direct argv array. The compiler never turns a
    # shell string into argv and never permits shell metacharacter command text.
    for entry in normalized["entrypoints"]:
        argv = entry["argv"]
        if not isinstance(argv, list) or not argv:
            raise CompileError("argv array required", f"$.entrypoints[{entry['id']}].argv")
        shell_interpreters = {"sh", "bash", "dash", "zsh", "fish", "ksh", "csh", "tcsh", "cmd", "powershell", "pwsh"}
        executable = os.path.basename(argv[0]).lower()
        if executable in shell_interpreters or executable.endswith((".sh", ".bat", ".cmd")):
            raise CompileError("shell interpreter execution forbidden", f"$.entrypoints[{entry['id']}].argv")
        python_names = {"python", "python3", "python3.9", "python3.10", "python3.11", "python3.12"}
        if argv[0].startswith("/") and executable not in python_names:
            raise CompileError("absolute executable outside Python allowlist", f"$.entrypoints[{entry['id']}].argv")
        # Relative paths are not accepted as executables: a repository-local
        # file named ``python3`` or ``codex-*`` would otherwise bypass the
        # positive allowlist through PATH/current-directory lookup.
        if "/" in argv[0] and not argv[0].startswith("/"):
            raise CompileError("relative executable path forbidden", f"$.entrypoints[{entry['id']}].argv")
        if executable not in python_names and not executable.startswith("codex-"):
            raise CompileError("executable not in positive allowlist", f"$.entrypoints[{entry['id']}].argv")
        if executable in python_names:
            resolved = pathlib.Path(sys.executable).resolve()
            if not resolved.is_file() or not os.access(resolved, os.X_OK):
                raise CompileError("resolved Python interpreter is not executable", f"$.entrypoints[{entry['id']}].argv[0]")
            # Preserve the logical token in the compiled plan.  GateRunner
            # resolves it to the current executable only at execution time so
            # the plan hash remains portable across clean Python 3.9+ clones.
        for i, arg in enumerate(argv):
            if not isinstance(arg, str) or "\x00" in arg:
                raise CompileError("unsafe argv item", f"$.entrypoints[{entry['id']}].argv[{i}]")
            if any(x in arg for x in (";", "&&", "||", "|", ">", "<", "`", "$(", "\n", "\r")):
                raise CompileError("shell metacharacter execution forbidden", f"$.entrypoints[{entry['id']}].argv[{i}]")
        if len(argv) == 1 and any(ch.isspace() for ch in argv[0]):
            raise CompileError("shell-string execution forbidden", f"$.entrypoints[{entry['id']}].argv")
        if entry["cwd"] == ".." or entry["cwd"].startswith("../"):
            raise CompileError("cwd traversal forbidden", f"$.entrypoints[{entry['id']}].cwd")

    # Explicit acceptance must map each blocking invariant/counterexample to a
    # production entrypoint and gate, not merely name a prose requirement.
    blocking_inv = {i["id"] for i in normalized["invariants"] if i["blocking"]}
    for invariant_id in sorted(blocking_inv):
        rows = [a for a in normalized["acceptance"] if a["invariant_id"] == invariant_id]
        if not any(a["blocking"] for a in rows):
            raise CompileError("blocking invariant requires a blocking acceptance row", f"$.acceptance[{invariant_id}]")
    for acceptance in normalized["acceptance"]:
        if acceptance["blocking"] and acceptance["invariant_id"] not in blocking_inv:
            raise CompileError("blocking acceptance maps to non-blocking invariant", f"$.acceptance[{acceptance['id']}]..invariant_id")
    for ce in normalized["counterexamples"]:
        matches = [a for a in normalized["acceptance"] if a["counterexample_id"] == ce["id"]]
        if not matches:
            raise CompileError("counterexample lacks acceptance mapping", f"$.counterexamples[{ce['id']}]..acceptance")
        if any(a["entrypoint_id"] not in by_entry or a["gate_id"] not in by_gate for a in matches):
            raise CompileError("acceptance maps to unknown production entrypoint/gate", f"$.counterexamples[{ce['id']}]..acceptance")

    plan = {
        "schema": "compiled-plan.v16",
        "mission_id": normalized["mission_id"],
        "mission_sha256": canonical_sha256(normalized),
        "base_sha": normalized["scope"]["exact_head"],
        "tree_sha": normalized["scope"].get("tree_sha", ""),
        "gate_order": order,
        "gates": normalized["gates"],
        "entrypoints": normalized["entrypoints"],
        "acceptance": normalized["acceptance"],
        "counterexample_ids": sorted(by_ce),
        "spark_audit_ids": [a["id"] for a in audits],
        "execution": {"shell": False, "background": False, "network": False, "packages": False},
    }
    return plan


def compile_file(path: str | pathlib.Path, output: str | pathlib.Path | None = None) -> dict[str, Any]:
    source = pathlib.Path(path)
    mission = json.loads(source.read_text(encoding="utf-8"))
    plan = compile_mission(mission)
    payload = {"plan": plan, "plan_sha256": canonical_sha256(plan)}
    text = canonical_json(payload) + "\n"
    if output:
        pathlib.Path(output).write_text(text, encoding="utf-8")
    return payload


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("mission")
    ap.add_argument("-o", "--output")
    args = ap.parse_args(argv)
    try:
        payload = compile_file(args.mission, args.output)
    except (ContractError, OSError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "RED", "error": str(exc)}, sort_keys=True))
        return 2
    print(json.dumps({"status": "GREEN", "plan_sha256": payload["plan_sha256"], "gate_order": payload["plan"]["gate_order"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())

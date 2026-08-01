"""Mission compiler: deterministic validation and gate-DAG planning only."""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
from typing import Any, Mapping

from .contracts import ContractError, canonical_sha256, canonical_json, validate_mission, validate_counterexample_linkage
from .review_policy import RISK_LEVELS, STAGE_ORDER, resolve_review_policy


class CompileError(ContractError):
    pass


# These profiles deliberately map onto the frozen v16 gate stages instead of
# extending the mission schema.  A mission remains portable/strict, while a
# caller can choose the smallest evidence tier appropriate to its risk.
EXECUTION_TIERS: dict[str, tuple[str, ...]] = {
    "FAST": ("targeted",),
    "CANDIDATE": ("targeted", "full"),
    "FINAL": ("targeted", "full", "fresh"),
}


def gate_ids_for_tier(plan: Mapping[str, Any], tier: str) -> list[str]:
    """Return ordered gates for a named staged-evidence tier.

    FAST intentionally has no commit/clean-tree precondition: GateRunner
    binds it to an exact staged or worktree content snapshot.  Each tier is
    intersected with the plan's frozen required-stage prefix, so a lower-risk
    route never acquires later gates merely because the caller chose FINAL.
    """
    if tier not in EXECUTION_TIERS:
        raise CompileError("unknown execution tier", "$.tier")
    if not isinstance(plan, Mapping):
        raise CompileError("compiled gate plan required", "$.plan")

    # A plan emitted by compile_mission always carries the resolved policy.
    # Do not resolve a missing policy here: executing an arbitrary plan without
    # a frozen route could silently broaden its evidence budget.  Legacy plans
    # remain supported when they carry the explicit resolved high-risk route.
    policy = plan.get("review_policy")
    if not isinstance(policy, Mapping):
        raise CompileError("review policy object required", "$.review_policy")
    if policy.get("review_risk") not in RISK_LEVELS:
        raise CompileError("review policy risk missing or invalid", "$.review_policy.review_risk")
    raw_stages = policy.get("required_stages")
    if not isinstance(raw_stages, list) or not raw_stages:
        raise CompileError("review policy required_stages must be a non-empty array", "$.review_policy.required_stages")
    if any(not isinstance(stage, str) for stage in raw_stages):
        raise CompileError("review policy stages must be strings", "$.review_policy.required_stages")
    if tuple(raw_stages) != tuple(STAGE_ORDER[: len(raw_stages)]):
        raise CompileError("review policy required_stages must be an ordered stage prefix", "$.review_policy.required_stages")
    required_stages = tuple(raw_stages)

    gates = plan.get("gates")
    order = plan.get("gate_order")
    if not isinstance(gates, list) or not isinstance(order, list):
        raise CompileError("compiled gate plan required", "$.plan")

    by_id: dict[str, Mapping[str, Any]] = {}
    for index, gate in enumerate(gates):
        if not isinstance(gate, Mapping):
            raise CompileError("gate object required", f"$.gates[{index}]")
        gate_id = gate.get("id")
        if not isinstance(gate_id, str) or not gate_id:
            raise CompileError("gate id required", f"$.gates[{index}].id")
        if gate_id in by_id:
            raise CompileError("duplicate gate id", f"$.gates[{index}].id")
        if gate.get("stage") not in STAGE_ORDER:
            raise CompileError("unknown gate stage", f"$.gates[{index}].stage")
        dependencies = gate.get("depends_on")
        if not isinstance(dependencies, list) or any(not isinstance(dep, str) for dep in dependencies):
            raise CompileError("gate dependencies must be an array of ids", f"$.gates[{index}].depends_on")
        by_id[gate_id] = gate

    if len(order) != len(set(order)) or any(not isinstance(gate_id, str) for gate_id in order):
        raise CompileError("gate order must contain unique string ids", "$.gate_order")
    if set(order) != set(by_id):
        raise CompileError("gate order must contain every gate exactly once", "$.gate_order")
    positions = {gate_id: position for position, gate_id in enumerate(order)}
    for gate_id, gate in by_id.items():
        for dependency in gate["depends_on"]:
            if dependency not in by_id:
                raise CompileError("unknown gate dependency", f"$.gates[{gate_id}].depends_on")
            if positions[dependency] >= positions[gate_id]:
                raise CompileError("gate order violates dependency", f"$.gates[{gate_id}].depends_on")

    # The tier is an execution budget; the policy is the frozen route.  Their
    # intersection prevents FAST from demanding full/fresh and prevents
    # CANDIDATE from running fresh even on a high-risk plan.
    stages = tuple(stage for stage in EXECUTION_TIERS[tier] if stage in required_stages)
    selected = [gate_id for gate_id in order if by_id[gate_id].get("stage") in stages]
    if not selected or any(stage not in {by_id[gate_id].get("stage") for gate_id in selected} for stage in stages):
        raise CompileError("tier lacks required staged gate", "$.gates")
    selected_ids = set(selected)
    for gate_id in selected:
        if any(dependency not in selected_ids for dependency in by_id[gate_id]["depends_on"]):
            raise CompileError("tier selected gate lacks selected dependencies", f"$.gates[{gate_id}].depends_on")
    return selected


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
    review_policy = resolve_review_policy(normalized)
    required_stages = tuple(review_policy["required_stages"])
    by_gate = {g["id"]: g for g in normalized["gates"]}
    by_entry = {e["id"]: e for e in normalized["entrypoints"]}
    by_ce = {c["id"]: c for c in normalized["counterexamples"]}
    by_acceptance = {a["id"]: a for a in normalized["acceptance"]}

    # Inner audits are risk-selected and token-bounded.  The frozen current
    # milestone declares three, while low-risk missions may legitimately
    # declare fewer (including zero).
    audits = normalized["spark_audits"]
    if len(audits) > 3:
        raise CompileError("at most three bounded Spark audit briefs allowed", "$.spark_audits")
    if any(not a["required"] for a in audits):
        raise CompileError("all Spark audits must be required", "$.spark_audits")

    # Gate order is a staged DAG: targeted -> full -> fresh. A stage cannot
    # depend on a later stage or run a required full/fresh gate without lower
    # stages. The evidence route is frozen independently from review risk.
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
    stage_ids = {
        stage: [gate_id for gate_id in order if by_gate[gate_id]["stage"] == stage]
        for stage in rank
    }
    for stage in required_stages:
        if not stage_ids[stage]:
            raise CompileError(f"required {stage} gate missing for review route", "$.gates")
    if not stage_ids["targeted"]:
        raise CompileError("at least one targeted gate required", "$.gates")
    active_stages = set(required_stages)
    for gate in normalized["gates"]:
        if gate["stage"] not in active_stages and gate["blocking"]:
            raise CompileError(
                "blocking gate cannot sit outside the frozen evidence route",
                f"$.gates[{gate['id']}].blocking",
            )
    def ancestors(gid: str) -> set[str]:
        seen: set[str] = set(); pending = list(by_gate[gid]["depends_on"])
        while pending:
            dep = pending.pop()
            if dep in seen:
                continue
            seen.add(dep); pending.extend(by_gate[dep]["depends_on"])
        return seen
    full_ids = {g["id"] for g in normalized["gates"] if g["stage"] == "full"}
    targeted_ids = {g["id"] for g in normalized["gates"] if g["stage"] == "targeted"}
    if "full" in required_stages:
        for full_id in stage_ids["full"]:
            if not ancestors(full_id) & targeted_ids:
                raise CompileError("full gate must transitively depend on targeted", f"$.gates[{full_id}].depends_on")
    if "fresh" in required_stages:
        for fresh_id in stage_ids["fresh"]:
            closure = ancestors(fresh_id)
            if not closure & full_ids or not closure & targeted_ids:
                raise CompileError("fresh gate must transitively depend on full and targeted", f"$.gates[{fresh_id}].depends_on")

    # Every gate at or below the selected evidence tier belongs to a component
    # terminating at a required stage. Later declared stages are retained in
    # the plan but are intentionally outside this route's validation envelope.
    highest_required_rank = rank[required_stages[-1]]
    active_ids = {
        gate_id for gate_id, gate in by_gate.items() if rank[gate["stage"]] <= highest_required_rank
    }
    terminal_ids = set(stage_ids[required_stages[-1]])
    connected: set[str] = set()
    for terminal_id in terminal_ids:
        connected.add(terminal_id)
        connected.update(ancestors(terminal_id))
    if connected != active_ids:
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
        if acceptance["blocking"] and by_gate[acceptance["gate_id"]]["stage"] not in active_stages:
            raise CompileError(
                "blocking acceptance cannot sit outside the frozen evidence route",
                f"$.acceptance[{acceptance['id']}].gate_id",
            )
    blocking_counterexamples = {
        counterexample_id
        for invariant in normalized["invariants"]
        if invariant["blocking"]
        for counterexample_id in invariant["counterexample_ids"]
    }
    for ce in normalized["counterexamples"]:
        matches = [a for a in normalized["acceptance"] if a["counterexample_id"] == ce["id"]]
        if not matches:
            raise CompileError("counterexample lacks acceptance mapping", f"$.counterexamples[{ce['id']}]..acceptance")
        if any(a["entrypoint_id"] not in by_entry or a["gate_id"] not in by_gate for a in matches):
            raise CompileError("acceptance maps to unknown production entrypoint/gate", f"$.counterexamples[{ce['id']}]..acceptance")
        if ce["id"] in blocking_counterexamples and not any(
            acceptance["blocking"] and by_gate[acceptance["gate_id"]]["stage"] in active_stages
            for acceptance in matches
        ):
            raise CompileError(
                "blocking counterexample lacks an active-route acceptance",
                f"$.counterexamples[{ce['id']}].acceptance",
            )

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
        "review_policy": review_policy,
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

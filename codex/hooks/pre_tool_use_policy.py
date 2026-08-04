#!/usr/bin/env python3
"""Deterministic pre-tool decision and non-invasive routing signal.

The normalized tool name owns policy.  For a generic execution tool, a direct
``rtk <evidence-tool>`` transport is classified as the substantive evidence
route.  Executable labels and path-like targets are inspected transiently;
raw arguments are never persisted.
"""

from __future__ import annotations

import json
import hashlib
import os
import pathlib
import shlex
import sys
from collections.abc import Mapping
from typing import Any

try:  # Support both direct hook execution and package-based test discovery.
    from . import hook_receipt
    from .governance_mode import is_strict
except ImportError:  # pragma: no cover - exercised by direct script invocation.
    import hook_receipt
    from governance_mode import is_strict

PACKAGE_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))

from v16.tool_runtime import (  # noqa: E402
    ToolRuntimeError,
    load_current_intake,
    load_turn_contract,
    record_expected_tool_call,
)


FORBIDDEN_CHILD = frozenset({"git", "github", "merge", "review", "approve"})
ROUTE_BY_TOOL = {
    "toolchain-doctor": "preflight",
    "toolchain_doctor": "preflight",
    "tool_preflight": "preflight",
    "toolchain-auto": "maintenance",
    "toolchain_auto": "maintenance",
    "tool_maintenance": "maintenance",
    "codegraph": "CodeGraph",
    "codegraph_explore": "CodeGraph",
    "mcp__codegraph__codegraph_explore": "CodeGraph",
    "semble": "Semble",
    "semble_search": "Semble",
    "mcp__semble__search": "Semble",
    "mcp__semble__find_related": "Semble",
    "rtk": "rtk",
    "rg": "rg",
    "ripgrep": "rg",
    "grep": "rg",
}
REPO_ACTIVITY_TOOLS = frozenset({
    "bash", "exec_command", "functions.exec_command", "apply_patch", "edit", "write",
})
REPOSITORY_ONLY_ROUTES = frozenset({
    "preflight", "maintenance", "codegraph", "semble",
})
REPOSITORY_MUTATION_TOOLS = frozenset({"apply_patch", "edit", "write"})


def _tool_key(tool: Any) -> str:
    """Normalize only a tool label; never stringify or inspect arguments."""

    return tool.strip().lower() if isinstance(tool, str) else ""


def _direct_shell_route(tool: Any, args: Any) -> str | None:
    if _tool_key(tool) not in {
        "exec_command", "functions.exec_command", "bash", "shell",
    } or not isinstance(args, Mapping):
        return None
    command = args.get("cmd", args.get("command"))
    if not isinstance(command, str) or not command.strip():
        return None
    try:
        tokens = shlex.split(command, posix=True)
    except ValueError:
        return None
    if not tokens:
        return None
    executable = pathlib.PurePosixPath(tokens[0]).name.lower()
    if executable == "rg":
        return "rg"
    if executable != "rtk":
        return None
    if len(tokens) < 2:
        return "rtk"
    nested = pathlib.PurePosixPath(tokens[1]).name.lower()
    if nested in {"python", "python3"} and len(tokens) >= 3:
        script = pathlib.PurePosixPath(tokens[2]).name.lower()
        if script in {"toolchain-auto.py", "toolchain_auto.py"}:
            return "contract" if "--record-task-contract" in tokens else "maintenance"
        if script in {"toolchain-doctor.py", "tool_preflight.py"}:
            return "preflight"
    return {
        "codegraph": "CodeGraph",
        "semble": "Semble",
        "rg": "rg",
    }.get(nested, "rtk")


def _inside_repo(cwd: Any) -> bool:
    if not isinstance(cwd, str) or not cwd:
        return False
    try:
        current = pathlib.Path(cwd).resolve(strict=True)
    except (OSError, RuntimeError):
        return False
    return any((parent / ".git").exists() for parent in (current, *current.parents))


def _inside_managed_user_state(path: pathlib.Path) -> bool:
    """Keep Codex and agent state available to machine/plugin inventory tasks."""

    configured = os.environ.get("CODEX_HOME")
    codex_home = pathlib.Path(configured).expanduser() if configured else pathlib.Path.home() / ".codex"
    try:
        resolved_home = codex_home.resolve(strict=False)
        resolved_path = path.resolve(strict=False)
    except (OSError, RuntimeError):
        return False
    roots = (resolved_home, resolved_home.parent / ".agents")
    return any(resolved_path == root or root in resolved_path.parents for root in roots)


def _path_targets_repo(value: str, cwd: Any) -> bool:
    """Return true when one transient path token resolves inside a Git repo."""

    candidate = value.strip("'\";,|&<>()[]{}")
    if not candidate or "://" in candidate:
        return False
    if "=" in candidate and candidate.startswith("--"):
        candidate = candidate.split("=", 1)[1]
    candidate = candidate.lstrip(">")
    if not candidate or candidate.startswith("-"):
        return False
    if not (candidate.startswith(("/", "./", "../", "~")) or "/" in candidate):
        return False
    try:
        path = pathlib.Path(candidate).expanduser()
        if not path.is_absolute():
            if not isinstance(cwd, str) or not cwd:
                return False
            path = pathlib.Path(cwd) / path
        resolved = path.resolve(strict=False)
    except (OSError, RuntimeError):
        return False
    if _inside_managed_user_state(resolved):
        return False
    probe = resolved if resolved.exists() else resolved.parent
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    return _inside_repo(str(probe))


def _argument_strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, Mapping):
        return [item for nested in value.values() for item in _argument_strings(nested)]
    if isinstance(value, (list, tuple)):
        return [item for nested in value for item in _argument_strings(nested)]
    return []


def _nonrepo_repository_access(
    payload: Mapping[str, Any], tool: Any, args: Any, route: str,
) -> bool:
    """Detect repository-only tools or explicit repository targets.

    This is intentionally transient: no command, argument, or resolved path is
    written to receipts.  It closes the outside-cwd/absolute-target bypass
    without preventing machine inventory under the installed Codex home.
    """

    key = _tool_key(tool)
    route_key = route.lower()
    if key in REPOSITORY_MUTATION_TOOLS or route_key in REPOSITORY_ONLY_ROUTES:
        return True
    strings = _argument_strings(args)
    for value in strings:
        try:
            tokens = shlex.split(value, posix=True)
        except ValueError:
            tokens = value.split()
        executables = [pathlib.PurePosixPath(token).name.lower() for token in tokens[:2]]
        if "git" in executables or "codegraph" in executables or "semble" in executables:
            return True
        if any(_path_targets_repo(token, payload.get("cwd")) for token in tokens):
            return True
    return False


def _contract_recorder(tool: Any, args: Any) -> bool:
    if _tool_key(tool) not in {"bash", "exec_command", "functions.exec_command", "shell"}:
        return False
    if not isinstance(args, Mapping):
        return False
    command = args.get("cmd", args.get("command"))
    if not isinstance(command, str):
        return False
    try:
        tokens = shlex.split(command, posix=True)
    except ValueError:
        return False
    return "--record-task-contract" in tokens and any(
        pathlib.PurePosixPath(token).name in {"toolchain-auto.py", "toolchain_auto.py"}
        for token in tokens
    )


def _bash_command(tool: Any, args: Any) -> str | None:
    if _tool_key(tool) not in {"bash", "exec_command", "functions.exec_command", "shell"}:
        return None
    if not isinstance(args, Mapping):
        return None
    command = args.get("command", args.get("cmd"))
    return command if isinstance(command, str) and command.strip() else None


def _wrap_bash_command(
    command: str, *, intake: Mapping[str, Any], tool_use_id: Any,
) -> str | None:
    """Inject a status-only recorder without persisting command or output.

    Public Codex PostToolUse payloads carry model-facing output and deliberately
    do not guarantee a Bash exit-code field.  The original command therefore
    runs in a subshell; an installed helper records only its integer status
    against the already-created opaque activity identity.
    """

    if not isinstance(tool_use_id, (str, int)) or not str(tool_use_id):
        return None
    task_id = intake.get("task_id_sha256")
    intake_id = intake.get("intake_id_sha256")
    if not isinstance(task_id, str) or not isinstance(intake_id, str):
        return None
    call_id = hashlib.sha256(str(tool_use_id).encode("utf-8")).hexdigest()
    recorder = pathlib.Path(__file__).resolve().with_name("tool_execution_status.py")
    recorder_command = " ".join((
        shlex.quote(sys.executable), shlex.quote(os.fspath(recorder)),
        "--task-id-sha256", shlex.quote(task_id),
        "--intake-id-sha256", shlex.quote(intake_id),
        "--tool-use-id-sha256", shlex.quote(call_id),
        "--exit-code", '"$__codex_v16_exit_code"',
    ))
    return (
        "(\n" + command + "\n)\n"
        "__codex_v16_exit_code=$?\n"
        + recorder_command + "\n"
        "__codex_v16_status_recorder=$?\n"
        'if [ "$__codex_v16_status_recorder" -ne 0 ]; then exit 125; fi\n'
        'exit "$__codex_v16_exit_code"'
    )


def _repo_activity(payload: Mapping[str, Any], tool: Any, route: str) -> bool:
    return _inside_repo(payload.get("cwd")) and (
        _tool_key(tool) in REPO_ACTIVITY_TOOLS
        or route.lower() in {"preflight", "maintenance", "codegraph", "semble", "rtk", "rg"}
    )


def _governed_activity(payload: Mapping[str, Any], tool: Any, route: str) -> bool:
    """Gate hook-observable activity whenever the runtime supplies a cwd.

    Repository scope is determined by the bound task contract.  A
    non-repository task may execute machine-only calls from a cwd outside every
    Git repository, subject to the separate repository-target boundary.
    """

    cwd = payload.get("cwd")
    return isinstance(cwd, str) and bool(cwd) and (
        _tool_key(tool) in REPO_ACTIVITY_TOOLS
        or route.lower() in {"preflight", "maintenance", "codegraph", "semble", "rtk", "rg"}
    )


def route_for(tool: Any, args: Any = None) -> str:
    """Return an explicit route hint, or ``unspecified`` when not known."""

    key = _tool_key(tool)
    if key.startswith("mcp__codegraph__") or key.startswith("codegraph_"):
        return "CodeGraph"
    if key.startswith("mcp__semble__") or key.startswith("semble_"):
        return "Semble"
    return ROUTE_BY_TOOL.get(key, _direct_shell_route(tool, args) or "unspecified")


def decide(tool: Any, args: Any = None) -> dict[str, str]:
    """Return a stable allow/deny result.

    ``args`` has no policy effect. It may supply only the direct executable
    route hint described by :func:`_direct_shell_route`.
    Parent authorization remains required for the explicit child-action tools
    in :data:`FORBIDDEN_CHILD`.
    """

    key = _tool_key(tool)
    route = route_for(tool, args)
    if key in FORBIDDEN_CHILD:
        return {
            "decision": "deny",
            "reason": "child action requires parent authorization",
            "reason_code": "child_action_requires_parent_authorization",
            "route": route,
            "route_code": route.lower(),
        }
    return {
        "decision": "allow",
        "reason": "policy-pass",
        "reason_code": "policy_pass",
        "route": route,
        "route_code": route.lower(),
    }


if __name__ == "__main__":
    try:
        x = json.load(sys.stdin)
    except (json.JSONDecodeError, OSError):
        x = {}
    if not isinstance(x, dict):
        x = {}
    tool_name = x.get("tool_name", x.get("tool", ""))
    tool_input = x.get("tool_input", x.get("args"))
    result = decide(tool_name, tool_input)
    governed_activity = _governed_activity(x, tool_name, result["route"])
    strict = is_strict()
    intake: Mapping[str, Any] | None = None
    updated_command: str | None = None
    bash_command = _bash_command(tool_name, tool_input)
    if result["decision"] == "allow" and governed_activity and strict:
        try:
            intake = load_current_intake(
                session_id=x.get("session_id"), turn_id=x.get("turn_id"),
                agent_id=x.get("agent_id"),
            )
        except (OSError, ToolRuntimeError):
            result = {
                **result,
                "decision": "deny",
                "reason": "current intake identity and bound V16 task contract are unavailable",
                "reason_code": "current_intake_required",
            }
        if not _contract_recorder(tool_name, tool_input):
            if result["decision"] == "allow":
                try:
                    contract = load_turn_contract(
                        session_id=x.get("session_id"), turn_id=x.get("turn_id"),
                        agent_id=x.get("agent_id"),
                        intake_id_sha256=intake["intake_id_sha256"] if intake else None,
                    )
                except (OSError, ToolRuntimeError):
                    result = {
                        **result,
                        "decision": "deny",
                        "reason": "record the bound V16 task contract before repository tools",
                        "reason_code": "task_contract_required",
                    }
                else:
                    if (
                        contract["repository_work"] is False
                        and _nonrepo_repository_access(
                            x, tool_name, tool_input, result["route"],
                        )
                    ):
                        result = {
                            **result,
                            "decision": "deny",
                            "reason": (
                                "non-repository contract cannot authorize repository "
                                "activity or targets; start a repository-scoped intake"
                            ),
                            "reason_code": "non_repository_scope_expansion",
                        }
        if result["decision"] == "allow" and not record_expected_tool_call(
            session_id=x.get("session_id"),
            turn_id=x.get("turn_id"),
            agent_id=x.get("agent_id"),
            intake_id_sha256=intake["intake_id_sha256"] if intake else None,
            tool_use_id=x.get("tool_use_id", x.get("tool_call_id")),
            route_code=result["route_code"],
            tool_name=tool_name,
            wrapped_bash=bash_command is not None,
        ):
            result = {
                **result,
                "decision": "deny",
                "reason": "current-turn tool activity state is unavailable",
                "reason_code": "tool_activity_state_unavailable",
            }
        if result["decision"] == "allow" and intake is not None:
            if bash_command is not None:
                updated_command = _wrap_bash_command(
                    bash_command,
                    intake=intake,
                    tool_use_id=x.get("tool_use_id", x.get("tool_call_id")),
                )
                if updated_command is None:
                    result = {
                        **result,
                        "decision": "deny",
                        "reason": "Bash execution status binding is unavailable",
                        "reason_code": "tool_execution_binding_unavailable",
                    }
    receipt_value = hook_receipt.receipt(
        "PreToolUse",
        x.get("model", os.environ.get("CODEX_MODEL", "unknown")),
        tool=tool_name,
        decision=result["decision"],
        reason_code=result["reason_code"],
        route_code=result["route_code"],
        identifiers=x,
        task_id_sha256=intake.get("task_id_sha256") if intake else None,
        intake_id_sha256=intake.get("intake_id_sha256") if intake else None,
        parent_intake_id_sha256=(
            intake.get("parent_intake_id_sha256") if intake else None
        ),
        agent_id_sha256=intake.get("agent_id_sha256") if intake else None,
    )
    written = hook_receipt.write_receipt(receipt_value)
    if governed_activity and strict and not written:
        result = {
            **result,
            "decision": "deny",
            "reason": "V16 runtime-proof receipt persistence failed",
            "reason_code": "hook_receipt_write_failed",
        }
    specific = {
        "hookEventName": "PreToolUse",
        "permissionDecision": result["decision"],
        "permissionDecisionReason": result["reason"],
    }
    if result["decision"] == "allow" and updated_command is not None:
        specific["updatedInput"] = {"command": updated_command}
    output = {"hookSpecificOutput": specific}
    if not written:
        output["systemMessage"] = (
            "V16 hook receipt write failed; "
            + ("governed tool call denied fail-closed." if governed_activity and strict else
               "runtime-proof acceptance is unavailable.")
        )
    print(json.dumps(output, sort_keys=True))

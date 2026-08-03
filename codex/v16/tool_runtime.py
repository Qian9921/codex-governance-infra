"""Fail-closed task applicability and tool-use enforcement for V16.

The routing primitives decide which tool owns one declared intent.  This
module closes the earlier omission loophole: every repository task must first
classify the complete, finite route surface.  Required routes must then be
present in an independently validated ``tool-usage.v16`` report; routes that
are not relevant remain explicit ``not_applicable`` rows instead of ceremonial
tool calls.

The classifier consumes only structured booleans and hashes.  It never stores
the user prompt, source text, absolute paths, or tool arguments.
"""

from __future__ import annotations

import hashlib
import json
import os
import pathlib
import re
import secrets
import stat
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .tool_routing import validate_usage_report


TASK_SCHEMA = "tool-task-contract.v16"
ENFORCEMENT_SCHEMA = "tool-enforcement.v16"
ROUTES = ("semantic_discovery", "structural_analysis", "exact_lookup", "shell_context")
ROUTE_TO_TOOL = {
    "semantic_discovery": "semble",
    "structural_analysis": "codegraph",
    "exact_lookup": "rg",
    "shell_context": "rtk",
}
ACTIVITY_ROUTES = frozenset({
    "preflight", "maintenance", "codegraph", "semble", "rtk", "rg",
    "contract", "unspecified",
})
SIGNALS = (
    "unknown_semantic_entrypoint",
    "similar_implementation",
    "known_symbol_or_call",
    "dependency_or_blast_radius",
    "exact_text_error_config_log",
    "shell_output_for_model",
    "machine_exact_only",
)
TASK_FIELDS = frozenset({
    "schema", "task_id_sha256", "classifier_identity", "task_shape_sha256",
    "repository_work", "classification_complete", "signals", "routes",
    "required_count", "not_applicable_count", "denominator",
    "denominator_known", "contract_sha256",
})
ROUTE_FIELDS = frozenset({"route", "tool", "applicability", "reason_code"})
ENFORCEMENT_FIELDS = frozenset({
    "schema", "status", "completion_eligible", "task_contract_sha256",
    "task_id_sha256", "usage_report_sha256", "required_routes",
    "satisfied_routes", "not_applicable_routes", "counts", "denominator",
    "denominator_known", "violations",
})
COUNT_FIELDS = frozenset(
    {"total", "ran", "passed", "failed", "skipped", "xfail", "unknown"}
)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_VALIDATED_USAGE_TOKEN = object()


class ToolRuntimeError(ValueError):
    """Raised when task applicability or enforcement evidence is invalid."""


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _sha256(value: str | bytes) -> str:
    if isinstance(value, str):
        value = value.encode("utf-8")
    return hashlib.sha256(value).hexdigest()


def _require_sha(value: Any, field: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ToolRuntimeError(f"{field} must be a SHA-256 digest")
    return value


def _require_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 160:
        raise ToolRuntimeError(f"{field} must be bounded non-empty text")
    return value.strip()


def _state_dir(value: str | os.PathLike[str] | None = None) -> pathlib.Path:
    if value is None:
        codex_home = pathlib.Path(
            os.environ.get("CODEX_HOME", os.fspath(pathlib.Path.home() / ".codex"))
        ).expanduser()
        value = os.environ.get("CODEX_TOOL_STATE_DIR", os.fspath(codex_home / "tool-state"))
    root = pathlib.Path(value).expanduser()
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    metadata = root.lstat()
    if root.is_symlink() or not root.is_dir() or metadata.st_uid != os.geteuid():
        raise ToolRuntimeError("tool runtime state directory ownership/type invalid")
    if stat.S_IMODE(metadata.st_mode) != 0o700:
        os.chmod(root, 0o700)
    return root


def turn_task_id(session_id: Any, turn_id: Any) -> str:
    if not isinstance(session_id, (str, int)) or not str(session_id):
        raise ToolRuntimeError("session_id required for task state")
    if not isinstance(turn_id, (str, int)) or not str(turn_id):
        raise ToolRuntimeError("turn_id required for task state")
    return _sha256(f"{session_id}\0{turn_id}")


def _event_task_id(session_id: Any, turn_id: Any, agent_id: Any = None) -> str:
    # PreToolUse/PostToolUse do not carry agent_id on the public Codex wire.
    # The session+turn pair is therefore the only lifecycle identity shared by
    # SubagentStart and the child's later tool/stop events.  agent_id remains
    # hashed lineage metadata on the intake, never a lookup requirement.
    return turn_task_id(session_id, turn_id)


def _state_path(root: pathlib.Path, task_id: str, suffix: str) -> pathlib.Path:
    return root / f"{_require_sha(task_id, 'task_id_sha256')}.{suffix}.json"


def _write_private_json(path: pathlib.Path, value: Mapping[str, Any]) -> None:
    if path.exists() and path.is_symlink():
        raise ToolRuntimeError("tool runtime state symlink rejected")
    temporary = path.with_name(path.name + f".{os.getpid()}.tmp")
    descriptor = os.open(
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(_canonical_json(value) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _read_private_json(path: pathlib.Path) -> dict[str, Any]:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise ToolRuntimeError("tool runtime state unavailable") from exc
    if path.is_symlink() or not path.is_file() or metadata.st_uid != os.geteuid():
        raise ToolRuntimeError("tool runtime state ownership/type invalid")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ToolRuntimeError("tool runtime state unreadable") from exc
    if not isinstance(value, dict):
        raise ToolRuntimeError("tool runtime state must be an object")
    return value


def _validate_intake(value: Mapping[str, Any]) -> dict[str, Any]:
    fields = {
        "schema", "task_id_sha256", "intake_id_sha256", "task_shape_sha256",
        "intake_kind", "parent_intake_id_sha256", "agent_id_sha256",
    }
    if not isinstance(value, Mapping) or set(value) != fields:
        raise ToolRuntimeError("tool intake fields must match the exact v16 schema")
    result = dict(value)
    if result["schema"] != "tool-turn-intake.v16":
        raise ToolRuntimeError("task intake schema mismatch")
    _require_sha(result["task_id_sha256"], "task_id_sha256")
    _require_sha(result["intake_id_sha256"], "intake_id_sha256")
    _require_sha(result["task_shape_sha256"], "task_shape_sha256")
    if result["intake_kind"] not in {"user_prompt", "subagent"}:
        raise ToolRuntimeError("task intake kind invalid")
    parent = result["parent_intake_id_sha256"]
    agent = result["agent_id_sha256"]
    if result["intake_kind"] == "user_prompt":
        if parent is not None or agent is not None:
            raise ToolRuntimeError("user prompt intake cannot carry child lineage")
    else:
        _require_sha(parent, "parent_intake_id_sha256")
        _require_sha(agent, "agent_id_sha256")
    return result


def _current_pointer(root: pathlib.Path, task_id: str) -> pathlib.Path:
    return _state_path(root, task_id, "current")


def _session_parent_pointer(root: pathlib.Path, session_id: Any) -> pathlib.Path:
    if not isinstance(session_id, (str, int)) or not str(session_id):
        raise ToolRuntimeError("session_id required for parent intake lineage")
    return _state_path(root, _sha256(str(session_id)), "parent-current")


def _intake_path(root: pathlib.Path, task_id: str, intake_id: str) -> pathlib.Path:
    return _state_path(
        root,
        task_id,
        "intake." + _require_sha(intake_id, "intake_id_sha256"),
    )


def _write_new_intake(root: pathlib.Path, intake: Mapping[str, Any]) -> dict[str, Any]:
    validated = _validate_intake(intake)
    task_id = validated["task_id_sha256"]
    intake_id = validated["intake_id_sha256"]
    path = _intake_path(root, task_id, intake_id)
    if path.exists():
        raise ToolRuntimeError("opaque intake identity collision")
    _write_private_json(path, validated)
    _write_private_json(_current_pointer(root, task_id), {
        "schema": "tool-current-intake.v16",
        "task_id_sha256": task_id,
        "intake_id_sha256": intake_id,
    })
    return validated


def _load_intake(
    root: pathlib.Path, task_id: str, intake_id: str,
) -> dict[str, Any]:
    intake = _validate_intake(_read_private_json(_intake_path(root, task_id, intake_id)))
    if intake["task_id_sha256"] != task_id or intake["intake_id_sha256"] != intake_id:
        raise ToolRuntimeError("task intake identity mismatch")
    return intake


def load_current_intake(
    *, session_id: Any, turn_id: Any, agent_id: Any = None,
    state_dir: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    task_id = _event_task_id(session_id, turn_id, agent_id)
    root = _state_dir(state_dir)
    pointer = _read_private_json(_current_pointer(root, task_id))
    if set(pointer) != {"schema", "task_id_sha256", "intake_id_sha256"}:
        raise ToolRuntimeError("current intake pointer fields invalid")
    if pointer.get("schema") != "tool-current-intake.v16":
        raise ToolRuntimeError("current intake pointer schema mismatch")
    if pointer.get("task_id_sha256") != task_id:
        raise ToolRuntimeError("current intake pointer task mismatch")
    intake_id = _require_sha(pointer.get("intake_id_sha256"), "intake_id_sha256")
    return _load_intake(root, task_id, intake_id)


def begin_turn_state(
    *, session_id: Any, turn_id: Any, prompt: Any,
    state_dir: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    if not isinstance(prompt, str):
        raise ToolRuntimeError("prompt required for task shape binding")
    task_id = turn_task_id(session_id, turn_id)
    intake = {
        "schema": "tool-turn-intake.v16",
        "task_id_sha256": task_id,
        "intake_id_sha256": secrets.token_hex(32),
        "task_shape_sha256": _sha256(prompt),
        "intake_kind": "user_prompt",
        "parent_intake_id_sha256": None,
        "agent_id_sha256": None,
    }
    root = _state_dir(state_dir)
    validated = _write_new_intake(root, intake)
    _write_private_json(_session_parent_pointer(root, session_id), {
        "schema": "tool-parent-intake-pointer.v16",
        "task_id_sha256": validated["task_id_sha256"],
        "intake_id_sha256": validated["intake_id_sha256"],
    })
    return validated


def begin_child_turn_state(
    *, session_id: Any, turn_id: Any, agent_id: Any,
    state_dir: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    """Create a child intake from parent lineage without inventing a prompt."""

    root = _state_dir(state_dir)
    pointer = _read_private_json(_session_parent_pointer(root, session_id))
    if set(pointer) != {"schema", "task_id_sha256", "intake_id_sha256"}:
        raise ToolRuntimeError("parent intake pointer fields invalid")
    if pointer.get("schema") != "tool-parent-intake-pointer.v16":
        raise ToolRuntimeError("parent intake pointer schema mismatch")
    parent_task_id = _require_sha(pointer.get("task_id_sha256"), "task_id_sha256")
    parent_intake_id = _require_sha(
        pointer.get("intake_id_sha256"), "parent_intake_id_sha256"
    )
    parent = _load_intake(root, parent_task_id, parent_intake_id)
    if parent["intake_kind"] != "user_prompt":
        raise ToolRuntimeError("child intake parent must be a user prompt intake")
    task_id = _event_task_id(session_id, turn_id, agent_id)
    if task_id == parent_task_id:
        raise ToolRuntimeError("child turn identity collides with parent turn")
    intake = {
        "schema": "tool-turn-intake.v16",
        "task_id_sha256": task_id,
        "intake_id_sha256": secrets.token_hex(32),
        "task_shape_sha256": parent["task_shape_sha256"],
        "intake_kind": "subagent",
        "parent_intake_id_sha256": parent["intake_id_sha256"],
        "agent_id_sha256": _sha256(str(agent_id)),
    }
    return _write_new_intake(root, intake)


def persist_task_contract(
    contract: Mapping[str, Any], *,
    intake_id_sha256: str,
    state_dir: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    validated = validate_task_contract(contract)
    root = _state_dir(state_dir)
    intake_id = _require_sha(intake_id_sha256, "intake_id_sha256")
    task_id = validated["task_id_sha256"]
    intake = _load_intake(root, task_id, intake_id)
    pointer = _read_private_json(_current_pointer(root, task_id))
    if pointer.get("intake_id_sha256") != intake_id:
        raise ToolRuntimeError("task contract intake is not current")
    if intake.get("task_shape_sha256") != validated["task_shape_sha256"]:
        raise ToolRuntimeError("task contract is not bound to current prompt shape")
    path = _state_path(root, task_id, f"contract.{intake_id}")
    if path.exists():
        existing = validate_task_contract(_read_private_json(path))
        if existing != validated:
            raise ToolRuntimeError("current-turn task contract is immutable")
        return existing
    _write_private_json(path, validated)
    return validated


def load_turn_contract(
    *, session_id: Any, turn_id: Any, agent_id: Any = None,
    intake_id_sha256: str | None = None,
    state_dir: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    task_id = _event_task_id(session_id, turn_id, agent_id)
    root = _state_dir(state_dir)
    current = load_current_intake(
        session_id=session_id, turn_id=turn_id, agent_id=agent_id, state_dir=root,
    )
    intake_id = (
        current["intake_id_sha256"]
        if intake_id_sha256 is None
        else _require_sha(intake_id_sha256, "intake_id_sha256")
    )
    if intake_id != current["intake_id_sha256"]:
        raise ToolRuntimeError("turn contract intake is not current")
    intake = _load_intake(root, task_id, intake_id)
    contract = validate_task_contract(
        _read_private_json(_state_path(root, task_id, f"contract.{intake_id}"))
    )
    if contract["task_id_sha256"] != task_id:
        raise ToolRuntimeError("turn contract identity mismatch")
    if contract["task_shape_sha256"] != intake.get("task_shape_sha256"):
        raise ToolRuntimeError("turn contract shape mismatch")
    return contract


def record_expected_tool_call(
    *, session_id: Any, turn_id: Any, tool_use_id: Any, agent_id: Any = None,
    intake_id_sha256: str | None = None, route_code: str,
    tool_name: Any, wrapped_bash: bool,
    state_dir: str | os.PathLike[str] | None = None,
) -> bool:
    if not isinstance(tool_use_id, (str, int)) or not str(tool_use_id):
        return False
    if not isinstance(route_code, str) or route_code not in ACTIVITY_ROUTES:
        return False
    if not isinstance(tool_name, (str, int)) or not str(tool_name):
        return False
    if type(wrapped_bash) is not bool:
        return False
    try:
        task_id = _event_task_id(session_id, turn_id, agent_id)
        root = _state_dir(state_dir)
        current = load_current_intake(
            session_id=session_id, turn_id=turn_id, agent_id=agent_id, state_dir=root,
        )
        intake_id = current["intake_id_sha256"]
        if intake_id_sha256 is not None and (
            _require_sha(intake_id_sha256, "intake_id_sha256") != intake_id
        ):
            return False
        call_id = _sha256(str(tool_use_id))
        tool_name_sha256 = _sha256(str(tool_name).strip().lower())
        path = _state_path(root, task_id, f"activity.{intake_id}.{call_id}")
        if path.exists():
            value = _read_private_json(path)
            return value == {
                "schema": "tool-turn-activity.v16",
                "task_id_sha256": task_id,
                "intake_id_sha256": intake_id,
                "tool_use_id_sha256": call_id,
                "route_code": route_code,
                "tool_name_sha256": tool_name_sha256,
                "wrapped_bash": wrapped_bash,
            }
        _write_private_json(path, {
            "schema": "tool-turn-activity.v16",
            "task_id_sha256": task_id,
            "intake_id_sha256": intake_id,
            "tool_use_id_sha256": call_id,
            "route_code": route_code,
            "tool_name_sha256": tool_name_sha256,
            "wrapped_bash": wrapped_bash,
        })
        return True
    except (OSError, ToolRuntimeError):
        return False


def record_tool_execution_status(
    *, task_id_sha256: str, intake_id_sha256: str,
    tool_use_id_sha256: str, exit_code: int,
    state_dir: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    """Persist the exit status produced by the PreToolUse-injected Bash wrapper.

    Codex's public PostToolUse wire intentionally exposes model-facing tool
    output, not a portable Bash exit-code field.  The wrapper runs after the
    original command and records only hashed lifecycle identities plus the
    integer exit status.  It never receives or persists the raw command or its
    output.
    """

    task_id = _require_sha(task_id_sha256, "task_id_sha256")
    intake_id = _require_sha(intake_id_sha256, "intake_id_sha256")
    call_id = _require_sha(tool_use_id_sha256, "tool_use_id_sha256")
    if type(exit_code) is not int:
        raise ToolRuntimeError("tool execution exit_code must be an integer")
    root = _state_dir(state_dir)
    activity_path = _state_path(root, task_id, f"activity.{intake_id}.{call_id}")
    activity = _read_private_json(activity_path)
    if activity != {
        "schema": "tool-turn-activity.v16",
        "task_id_sha256": task_id,
        "intake_id_sha256": intake_id,
        "tool_use_id_sha256": call_id,
        "route_code": activity.get("route_code"),
        "tool_name_sha256": activity.get("tool_name_sha256"),
        "wrapped_bash": activity.get("wrapped_bash"),
    } or (
        activity.get("route_code") not in ACTIVITY_ROUTES
        or not isinstance(activity.get("tool_name_sha256"), str)
        or _SHA256.fullmatch(activity["tool_name_sha256"]) is None
        or type(activity.get("wrapped_bash")) is not bool
    ):
        raise ToolRuntimeError("tool execution has no matching expected activity")
    value = {
        "schema": "tool-execution-status.v16",
        "task_id_sha256": task_id,
        "intake_id_sha256": intake_id,
        "tool_use_id_sha256": call_id,
        "exit_code": exit_code,
    }
    path = _state_path(root, task_id, f"execution.{intake_id}.{call_id}")
    if path.exists():
        existing = _read_private_json(path)
        if existing != value:
            raise ToolRuntimeError("tool execution status is immutable")
        return existing
    _write_private_json(path, value)
    return value


def load_tool_execution_status(
    *, session_id: Any, turn_id: Any, tool_use_id: Any,
    intake_id_sha256: str, agent_id: Any = None,
    state_dir: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    """Load one wrapper-authored Bash completion bound to the current intake."""

    if not isinstance(tool_use_id, (str, int)) or not str(tool_use_id):
        raise ToolRuntimeError("tool_use_id required for execution binding")
    task_id = _event_task_id(session_id, turn_id, agent_id)
    intake_id = _require_sha(intake_id_sha256, "intake_id_sha256")
    call_id = _sha256(str(tool_use_id))
    root = _state_dir(state_dir)
    value = _read_private_json(
        _state_path(root, task_id, f"execution.{intake_id}.{call_id}")
    )
    if value != {
        "schema": "tool-execution-status.v16",
        "task_id_sha256": task_id,
        "intake_id_sha256": intake_id,
        "tool_use_id_sha256": call_id,
        "exit_code": value.get("exit_code"),
    } or type(value.get("exit_code")) is not int:
        raise ToolRuntimeError("tool execution status invalid")
    return value


def load_expected_tool_calls(
    *, session_id: Any, turn_id: Any, agent_id: Any = None,
    state_dir: str | os.PathLike[str] | None = None,
) -> list[str]:
    task_id = _event_task_id(session_id, turn_id, agent_id)
    root = _state_dir(state_dir)
    intake = load_current_intake(
        session_id=session_id, turn_id=turn_id, agent_id=agent_id, state_dir=root,
    )
    intake_id = intake["intake_id_sha256"]
    calls: list[str] = []
    for path in root.glob(f"{task_id}.activity.{intake_id}.*.json"):
        value = _read_private_json(path)
        call_id = value.get("tool_use_id_sha256")
        if (
            value.get("schema") != "tool-turn-activity.v16"
            or value.get("task_id_sha256") != task_id
            or value.get("intake_id_sha256") != intake_id
            or value.get("route_code") not in ACTIVITY_ROUTES
            or not isinstance(value.get("tool_name_sha256"), str)
            or _SHA256.fullmatch(value["tool_name_sha256"]) is None
            or type(value.get("wrapped_bash")) is not bool
            or not isinstance(call_id, str)
            or _SHA256.fullmatch(call_id) is None
            or path.name != f"{task_id}.activity.{intake_id}.{call_id}.json"
        ):
            raise ToolRuntimeError("tool activity state invalid")
        calls.append(call_id)
    return sorted(set(calls))


def load_tool_call_binding(
    *, session_id: Any, turn_id: Any, tool_use_id: Any, agent_id: Any = None,
    state_dir: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    if not isinstance(tool_use_id, (str, int)) or not str(tool_use_id):
        raise ToolRuntimeError("tool_use_id required for activity binding")
    task_id = _event_task_id(session_id, turn_id, agent_id)
    call_id = _sha256(str(tool_use_id))
    root = _state_dir(state_dir)
    paths = list(root.glob(f"{task_id}.activity.*.{call_id}.json"))
    if len(paths) != 1:
        raise ToolRuntimeError("tool activity intake binding unavailable or ambiguous")
    value = _read_private_json(paths[0])
    intake_id = _require_sha(value.get("intake_id_sha256"), "intake_id_sha256")
    route_code = value.get("route_code")
    if value != {
        "schema": "tool-turn-activity.v16",
        "task_id_sha256": task_id,
        "intake_id_sha256": intake_id,
        "tool_use_id_sha256": call_id,
        "route_code": route_code,
        "tool_name_sha256": value.get("tool_name_sha256"),
        "wrapped_bash": value.get("wrapped_bash"),
    } or (
        route_code not in ACTIVITY_ROUTES
        or not isinstance(value.get("tool_name_sha256"), str)
        or _SHA256.fullmatch(value["tool_name_sha256"]) is None
        or type(value.get("wrapped_bash")) is not bool
        or paths[0].name != f"{task_id}.activity.{intake_id}.{call_id}.json"
    ):
        raise ToolRuntimeError("tool activity state invalid")
    return {
        "intake": _load_intake(root, task_id, intake_id),
        "route_code": route_code,
        "tool_name_sha256": value["tool_name_sha256"],
        "wrapped_bash": value["wrapped_bash"],
    }


def load_tool_call_intake(
    *, session_id: Any, turn_id: Any, tool_use_id: Any, agent_id: Any = None,
    state_dir: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    return load_tool_call_binding(
        session_id=session_id,
        turn_id=turn_id,
        tool_use_id=tool_use_id,
        agent_id=agent_id,
        state_dir=state_dir,
    )["intake"]


@dataclass(frozen=True)
class ValidatedToolUsage:
    report: Mapping[str, Any]
    _token: object

    def __post_init__(self) -> None:
        if self._token is not _VALIDATED_USAGE_TOKEN:
            raise ToolRuntimeError("validated tool usage cannot be constructed directly")


def validate_and_bind_usage_report(
    usage_report: Mapping[str, Any], **authority: Any
) -> ValidatedToolUsage:
    validated = validate_usage_report(usage_report, **authority)
    return ValidatedToolUsage(validated, _VALIDATED_USAGE_TOKEN)


def _required_routes(signals: Mapping[str, bool]) -> set[str]:
    required: set[str] = set()
    if signals["unknown_semantic_entrypoint"] or signals["similar_implementation"]:
        required.add("semantic_discovery")
    if signals["known_symbol_or_call"] or signals["dependency_or_blast_radius"]:
        required.add("structural_analysis")
    if signals["exact_text_error_config_log"]:
        required.add("exact_lookup")
    if signals["shell_output_for_model"]:
        required.add("shell_context")
    return required


def _reason_code(route: str, required: bool) -> str:
    stem = {
        "semantic_discovery": "SEMANTIC_DISCOVERY",
        "structural_analysis": "STRUCTURAL_ANALYSIS",
        "exact_lookup": "EXACT_LOOKUP",
        "shell_context": "SHELL_CONTEXT",
    }[route]
    return f"TASK_REQUIRES_{stem}" if required else f"{stem}_NOT_APPLICABLE"


def compile_task_contract(
    *,
    task_id_sha256: str,
    classifier_identity: str,
    task_shape_sha256: str,
    repository_work: bool,
    signals: Mapping[str, bool],
) -> dict[str, Any]:
    """Compile the complete route matrix for one privacy-safe task shape.

    The caller classifies semantic facts once.  The compiler then deterministically
    derives every route, so a caller cannot omit Semble or CodeGraph by simply
    leaving the corresponding route out of a hand-written list.
    """

    task_id = _require_sha(task_id_sha256, "task_id_sha256")
    task_shape = _require_sha(task_shape_sha256, "task_shape_sha256")
    classifier = _require_text(classifier_identity, "classifier_identity")
    if type(repository_work) is not bool:
        raise ToolRuntimeError("repository_work must be boolean")
    if not isinstance(signals, Mapping) or set(signals) != set(SIGNALS):
        raise ToolRuntimeError("signals must cover the exact task-shape denominator")
    normalized: dict[str, bool] = {}
    for name in SIGNALS:
        value = signals[name]
        if type(value) is not bool:
            raise ToolRuntimeError(f"signal {name} must be boolean")
        normalized[name] = value
    if not repository_work and any(normalized.values()):
        raise ToolRuntimeError("non-repository task cannot declare repository tool signals")
    required = _required_routes(normalized) if repository_work else set()
    if repository_work and not required and not normalized["machine_exact_only"]:
        raise ToolRuntimeError(
            "repository work must require a tool route or explicitly be machine_exact_only"
        )
    if normalized["machine_exact_only"] and required:
        raise ToolRuntimeError("machine_exact_only cannot coexist with model-context routes")
    rows = [
        {
            "route": route,
            "tool": ROUTE_TO_TOOL[route],
            "applicability": "required" if route in required else "not_applicable",
            "reason_code": _reason_code(route, route in required),
        }
        for route in ROUTES
    ]
    value: dict[str, Any] = {
        "schema": TASK_SCHEMA,
        "task_id_sha256": task_id,
        "classifier_identity": classifier,
        "task_shape_sha256": task_shape,
        "repository_work": repository_work,
        "classification_complete": True,
        "signals": normalized,
        "routes": rows,
        "required_count": len(required),
        "not_applicable_count": len(ROUTES) - len(required),
        "denominator": len(ROUTES),
        "denominator_known": True,
        "contract_sha256": "",
    }
    value["contract_sha256"] = _sha256(_canonical_json(value))
    return validate_task_contract(value)


def validate_task_contract(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != TASK_FIELDS:
        raise ToolRuntimeError("task contract fields must match the exact v16 schema")
    result = dict(value)
    if result["schema"] != TASK_SCHEMA:
        raise ToolRuntimeError("task contract schema mismatch")
    task_id = _require_sha(result["task_id_sha256"], "task_id_sha256")
    task_shape = _require_sha(result["task_shape_sha256"], "task_shape_sha256")
    classifier = _require_text(result["classifier_identity"], "classifier_identity")
    signals = result["signals"]
    if not isinstance(signals, Mapping) or set(signals) != set(SIGNALS):
        raise ToolRuntimeError("signals must cover the exact task-shape denominator")
    normalized: dict[str, bool] = {}
    for name in SIGNALS:
        if type(signals[name]) is not bool:
            raise ToolRuntimeError(f"signal {name} must be boolean")
        normalized[name] = signals[name]
    repository_work = result["repository_work"]
    if type(repository_work) is not bool:
        raise ToolRuntimeError("repository_work must be boolean")
    if not repository_work and any(normalized.values()):
        raise ToolRuntimeError("non-repository task cannot declare repository tool signals")
    required = _required_routes(normalized) if repository_work else set()
    if repository_work and not required and not normalized["machine_exact_only"]:
        raise ToolRuntimeError(
            "repository work must require a tool route or explicitly be machine_exact_only"
        )
    if normalized["machine_exact_only"] and required:
        raise ToolRuntimeError("machine_exact_only cannot coexist with model-context routes")
    rows = [
        {
            "route": route,
            "tool": ROUTE_TO_TOOL[route],
            "applicability": "required" if route in required else "not_applicable",
            "reason_code": _reason_code(route, route in required),
        }
        for route in ROUTES
    ]
    if result["classification_complete"] is not True:
        raise ToolRuntimeError("task classification must be complete")
    if result["routes"] != rows:
        raise ToolRuntimeError("task route matrix does not match structured signals")
    if (
        result["required_count"] != len(required)
        or result["not_applicable_count"] != len(ROUTES) - len(required)
        or result["denominator"] != len(ROUTES)
        or result["denominator_known"] is not True
    ):
        raise ToolRuntimeError("task route denominator mismatch")
    unsigned = dict(result)
    digest = _require_sha(unsigned.pop("contract_sha256"), "contract_sha256")
    unsigned["contract_sha256"] = ""
    if digest != _sha256(_canonical_json(unsigned)):
        raise ToolRuntimeError("task contract digest mismatch")
    result["task_id_sha256"] = task_id
    result["task_shape_sha256"] = task_shape
    result["classifier_identity"] = classifier
    result["signals"] = normalized
    return result


def build_enforcement_report(
    task_contract: Mapping[str, Any],
    usage: ValidatedToolUsage,
) -> dict[str, Any]:
    """Require actual preferred-tool use for every applicable route.

    ``usage`` is an opaque value created only by
    :func:`validate_and_bind_usage_report`, which invokes the caller-bound
    authoritative validator.  A plain author-supplied mapping is rejected.
    """

    contract = validate_task_contract(task_contract)
    if not isinstance(usage, ValidatedToolUsage) or usage._token is not _VALIDATED_USAGE_TOKEN:
        raise ToolRuntimeError("enforcement requires caller-bound validated tool usage")
    report = _derive_enforcement_report(contract, usage.report)
    return validate_enforcement_report(report, contract, usage)


def _derive_enforcement_report(
    contract: Mapping[str, Any],
    usage_report: Mapping[str, Any],
) -> dict[str, Any]:
    """Derive the only valid enforcement report for two validated inputs."""

    if not isinstance(usage_report, Mapping) or usage_report.get("schema") != "tool-usage.v16":
        raise ToolRuntimeError("enforcement requires a validated tool-usage.v16 report")
    if usage_report.get("task_id_sha256") != contract["task_id_sha256"]:
        raise ToolRuntimeError("task contract and usage report identity mismatch")
    usage_digest = _sha256(_canonical_json(usage_report))
    successful_tools = {
        call.get("tool")
        for call in usage_report.get("calls", [])
        if isinstance(call, Mapping) and call.get("status") == "success"
    }
    fallback_tools = {
        route.get("preferred_tool")
        for route in usage_report.get("routes", [])
        if isinstance(route, Mapping) and route.get("fallback") is True
    }
    required = [row["route"] for row in contract["routes"] if row["applicability"] == "required"]
    not_applicable = [row["route"] for row in contract["routes"] if row["applicability"] == "not_applicable"]
    satisfied: list[str] = []
    violations: list[str] = []
    degraded = False
    hard_violation = False
    for route in required:
        tool = ROUTE_TO_TOOL[route]
        if tool in successful_tools:
            satisfied.append(route)
        elif tool in fallback_tools:
            violations.append(f"PREFERRED_TOOL_NOT_SUCCESSFUL:{route}")
            degraded = True
        else:
            violations.append(f"REQUIRED_ROUTE_NOT_USED:{route}")
            hard_violation = True
    if usage_report.get("routing_compliant") is not True:
        violations.append("TOOL_USAGE_NOT_ROUTING_COMPLIANT")
        hard_violation = True
    if usage_report.get("coverage_equivalent") is not True:
        degraded = True
    passed = len(satisfied) + len(not_applicable)
    failed = len(ROUTES) - passed
    status = (
        "blocked" if hard_violation
        else ("degraded" if violations or degraded else "compliant")
    )
    completion_eligible = status == "compliant" and failed == 0
    return {
        "schema": ENFORCEMENT_SCHEMA,
        "status": status,
        "completion_eligible": completion_eligible,
        "task_contract_sha256": contract["contract_sha256"],
        "task_id_sha256": contract["task_id_sha256"],
        "usage_report_sha256": usage_digest,
        "required_routes": required,
        "satisfied_routes": satisfied,
        "not_applicable_routes": not_applicable,
        "counts": {
            "total": len(ROUTES),
            "ran": len(ROUTES),
            "passed": passed,
            "failed": failed,
            "skipped": 0,
            "xfail": 0,
            "unknown": 0,
        },
        "denominator": len(ROUTES),
        "denominator_known": True,
        "violations": sorted(set(violations)),
    }


def validate_enforcement_report(
    value: Mapping[str, Any],
    task_contract: Mapping[str, Any],
    usage: ValidatedToolUsage,
) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != ENFORCEMENT_FIELDS:
        raise ToolRuntimeError("enforcement fields must match the exact v16 schema")
    result = dict(value)
    if result["schema"] != ENFORCEMENT_SCHEMA:
        raise ToolRuntimeError("enforcement schema mismatch")
    counts = result["counts"]
    if not isinstance(counts, Mapping) or set(counts) != COUNT_FIELDS:
        raise ToolRuntimeError("enforcement counts fields mismatch")
    if any(type(item) is not int or item < 0 for item in counts.values()):
        raise ToolRuntimeError("enforcement counts must be non-negative integers")
    if counts["total"] != counts["passed"] + counts["failed"]:
        raise ToolRuntimeError("enforcement count arithmetic mismatch")
    if counts["ran"] != counts["passed"] + counts["failed"]:
        raise ToolRuntimeError("enforcement ran arithmetic mismatch")
    if any(counts[name] for name in ("skipped", "xfail", "unknown")):
        raise ToolRuntimeError("enforcement cannot skip applicability rows")
    if result["denominator"] != len(ROUTES) or result["denominator_known"] is not True:
        raise ToolRuntimeError("enforcement denominator mismatch")
    contract = validate_task_contract(task_contract)
    if not isinstance(usage, ValidatedToolUsage) or usage._token is not _VALIDATED_USAGE_TOKEN:
        raise ToolRuntimeError("enforcement requires caller-bound validated tool usage")
    usage_report = usage.report
    if result["task_contract_sha256"] != contract["contract_sha256"]:
        raise ToolRuntimeError("enforcement task contract mismatch")
    if result["task_id_sha256"] != contract["task_id_sha256"]:
        raise ToolRuntimeError("enforcement task identity mismatch")
    _require_sha(result["usage_report_sha256"], "usage_report_sha256")
    if result["usage_report_sha256"] != _sha256(_canonical_json(usage_report)):
        raise ToolRuntimeError("enforcement usage report mismatch")
    for field in ("required_routes", "satisfied_routes", "not_applicable_routes", "violations"):
        if not isinstance(result[field], list) or any(not isinstance(item, str) for item in result[field]):
            raise ToolRuntimeError(f"enforcement {field} must be a string list")
    if result["status"] not in {"compliant", "degraded", "blocked"}:
        raise ToolRuntimeError("enforcement status invalid")
    if type(result["completion_eligible"]) is not bool:
        raise ToolRuntimeError("completion_eligible must be boolean")
    expected_eligible = (
        result["status"] == "compliant"
        and not result["violations"]
        and counts["failed"] == 0
    )
    if result["completion_eligible"] != expected_eligible:
        raise ToolRuntimeError("completion eligibility mismatch")
    expected = _derive_enforcement_report(contract, usage_report)
    if result != expected:
        raise ToolRuntimeError("enforcement report does not match deterministic derivation")
    return result


__all__ = [
    "ENFORCEMENT_SCHEMA", "ROUTES", "ROUTE_TO_TOOL", "SIGNALS", "TASK_SCHEMA",
    "ToolRuntimeError", "ValidatedToolUsage", "begin_child_turn_state", "begin_turn_state",
    "build_enforcement_report", "compile_task_contract", "load_expected_tool_calls",
    "load_current_intake", "load_tool_call_intake", "load_turn_contract",
    "persist_task_contract", "record_expected_tool_call",
    "turn_task_id", "validate_and_bind_usage_report", "validate_enforcement_report",
    "load_tool_call_binding",
    "validate_task_contract",
]

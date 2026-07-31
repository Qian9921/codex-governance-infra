#!/usr/bin/env python3
"""Receipt-preserving PreToolUse capability router."""

from __future__ import annotations

import json
import os
import re
import sys
import pathlib
import stat
from dataclasses import dataclass
from typing import Any

from hook_receipt import record_receipt
import delegation_contract as _dc


SIMPLE_READ_COMMANDS = {
    "cat",
    "basename",
    "cmp",
    "diff",
    "dirname",
    "du",
    "file",
    "head",
    "ls",
    "pwd",
    "readlink",
    "realpath",
    "sha256sum",
    "shasum",
    "stat",
    "tail",
    "test",
    "true",
    "false",
    "uptime",
    "wc",
    "grep",
}
TRUSTED_ABSOLUTE_READ_COMMANDS = {
    f"/usr/bin/{name}" for name in SIMPLE_READ_COMMANDS | {"find", "rg", "sed"}
}
TRUSTED_ABSOLUTE_READ_COMMANDS.update(
    {"/bin/ls", "/bin/pwd", "/bin/true", "/bin/false", "/usr/bin/git"}
)
TRUSTED_ABSOLUTE_RTK_WRAPPER = os.environ.get("RTK_WRAPPER", "rtk")

@dataclass(frozen=True)
class Token:
    kind: str
    value: str


def _command(tool_input: Any) -> str:
    if isinstance(tool_input, dict):
        value = tool_input.get("command", "")
        return value if isinstance(value, str) else ""
    return ""


def _deny(
    reason: str,
    *,
    payload: dict[str, Any] | None = None,
    model: str = "unknown",
    tool_name: str = "",
    reason_code: str = "policy_deny",
) -> int:
    record_receipt(
        "PreToolUse",
        payload,
        model=model,
        tool_name=tool_name,
        decision="deny",
        reason_code=reason_code,
    )
    sys.stdout.write(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": reason,
                }
            },
            ensure_ascii=False,
        )
        + "\n"
    )
    return 0


# Native entrypoint schemas are intentionally closed.  Model names never affect
# this map; task packet permissions and the lease do.
_NATIVE_READ = {"Read", "Grep", "Glob"}
_NATIVE_WRITE = {"Write", "Edit", "apply_patch"}
_MCP_SEMBLE_READ = {"mcp__semble__search", "mcp__semble__find", "mcp__semble__query"}
_MCP_CODEGRAPH_READ = {"mcp__codegraph__codegraph_explore", "mcp__codegraph__query", "mcp__codegraph__find_symbol"}
_PATH_KEYS = {"path", "file_path"}
_PATH_ALIASES = {"paths", "files", "filePath", "project_path", "projectPath", "repo", "repository", "root"}


def _extract_native_path(tool_name: str, tool_input: Any) -> tuple[list[str], str]:
    if not isinstance(tool_input, dict):
        return [], "tool input must be an object"
    if tool_name == "Read":
        allowed = {"path", "file_path", "offset", "limit"}
    elif tool_name == "Grep":
        allowed = {"path", "file_path", "pattern", "query", "include", "glob", "max_results"}
    elif tool_name == "Glob":
        allowed = {"path", "file_path", "pattern", "ignore"}
    elif tool_name in _NATIVE_WRITE:
        allowed = {"path", "file_path", "content", "old_string", "new_string", "patch", "edits"}
    else:
        return [], "unknown native tool"
    if any(k in tool_input for k in _PATH_ALIASES if k not in allowed):
        return [], "unrecognized or conflicting path field"
    found = [tool_input[k] for k in ("path", "file_path") if k in tool_input]
    if len(found) != 1 or not isinstance(found[0], str) or not found[0]:
        return [], "exactly one native path/file_path is required"
    # A write may carry a patch/edit body but no executable command.
    if any(k not in allowed for k in tool_input):
        return [], "unknown native tool field"
    return [found[0]], ""


def _canonical_rel(root: pathlib.Path, value: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value:
        raise ValueError("noncanonical requested path")
    candidate = pathlib.Path(value)
    if candidate.is_absolute():
        # Absolute paths are accepted only when they are exactly below the pinned
        # repository root.  The root itself is not a lease file.
        root_abs = pathlib.Path(os.path.abspath(os.fspath(root)))
        abs_value = pathlib.Path(os.path.abspath(value))
        try:
            rel = abs_value.relative_to(root_abs).as_posix()
        except ValueError as exc:
            raise ValueError("requested path outside repository") from exc
    else:
        rel = value
    parts = rel.split("/")
    if not rel or any(x in {"", ".", ".."} for x in parts):
        raise ValueError("noncanonical requested path")
    # Every existing component is lstat-checked; final symlinks are never followed.
    cur = root
    for i, part in enumerate(parts):
        cur = cur / part
        try:
            info = cur.lstat()
        except FileNotFoundError:
            if i != len(parts) - 1:
                raise ValueError("missing requested ancestor")
            break
        if stat.S_ISLNK(info.st_mode):
            raise ValueError("symlink requested path")
        if i != len(parts) - 1 and not stat.S_ISDIR(info.st_mode):
            raise ValueError("non-directory requested ancestor")
    return "/".join(parts)


def _tool_paths(tool_input: Any) -> list[str]:
    """Extract only native path fields; MCP schemas are handled explicitly below."""
    if not isinstance(tool_input, dict):
        raise ValueError("tool input must be an object")
    found = []
    for key in ("path", "file_path"):
        if key in tool_input:
            value = tool_input[key]
            if not isinstance(value, str):
                raise ValueError("invalid tool path field")
            found.append(value)
    if len(found) > 1:
        raise ValueError("duplicate/conflicting tool path")
    return found


def _lease_path_allowed(root: pathlib.Path, leases: list[str], value: str) -> bool:
    try:
        rel = _canonical_rel(root, value)
    except ValueError:
        return False
    return any(rel == lease or rel.startswith(lease + "/") for lease in leases)


def _delegation_tool_allowed(packet: dict[str, Any], payload: dict[str, Any], state: dict[str, Any], rec: dict[str, Any], tool_name: str) -> tuple[bool, str]:
    if rec.get("phase") != "STARTED" or not any(x.get("key") == _dc.state_key(packet) for x in state.get("active", [])):
        return False, "delegation packet is not active STARTED"
    tool_input = payload.get("tool_input")
    if not isinstance(tool_name, str) or not tool_name:
        return False, "tool identity required"
    lowered = tool_name.lower()
    if tool_name in {"Bash", "bash", "Shell", "Git", "GitHub"} or any(x in lowered for x in ("bash", "shell", "git", "github", "review", "approve", "merge")):
        return False, "delegated shell/git/external tool denied"
    leases = _dc._paths(packet["lease"]["paths"])
    root = pathlib.Path(packet["repo_root"])
    if tool_name in _NATIVE_READ or tool_name in _NATIVE_WRITE:
        paths, reason = _extract_native_path(tool_name, tool_input)
        if reason:
            return False, reason
        try:
            normalized = [_canonical_rel(root, p) for p in paths]
        except ValueError as exc:
            return False, str(exc)
        if not all(any(p == lease or p.startswith(lease + "/") for lease in leases) for p in normalized):
            return False, "tool path outside delegated lease"
        if tool_name in _NATIVE_WRITE:
            if "write_paths" not in packet["permissions"]:
                return False, "write permission absent"
        elif "read" not in packet["permissions"]:
            return False, "read permission absent"
        return True, ""
    if tool_name in _MCP_SEMBLE_READ:
        if not isinstance(tool_input, dict) or set(k for k in tool_input if k in _PATH_ALIASES) != {"repo"} or not isinstance(tool_input.get("repo"), str):
            return False, "Semble requires exact repo field"
        try:
            if pathlib.Path(os.path.abspath(tool_input["repo"])) != pathlib.Path(os.path.abspath(packet["repo_root"])):
                return False, "Semble repo mismatch"
            _canonical_rel(root, leases[0])
        except ValueError:
            return False, "Semble repo is not canonical"
        return (True, "") if "read" in packet["permissions"] else (False, "read permission absent")
    if tool_name in _MCP_CODEGRAPH_READ:
        if not isinstance(tool_input, dict) or not isinstance(tool_input.get("projectPath"), str) or any(k in tool_input for k in _PATH_ALIASES if k != "projectPath"):
            return False, "CodeGraph requires exact projectPath field"
        if pathlib.Path(os.path.abspath(tool_input["projectPath"])) != pathlib.Path(os.path.abspath(packet["repo_root"])):
            return False, "CodeGraph projectPath mismatch"
        return (True, "") if "read" in packet["permissions"] else (False, "read permission absent")
    if tool_name.startswith("mcp__"):
        return False, "unknown MCP tool"
    return False, "delegation tool is not classified"

def _tokenize_shell(command: str) -> list[Token] | None:
    """Tokenize the supported shell subset while preserving quoted metacharacters."""
    if not command or "\n" in command or "\r" in command:
        return None

    tokens: list[Token] = []
    word: list[str] = []
    quote: str | None = None
    i = 0

    def flush_word() -> None:
        if word:
            tokens.append(Token("word", "".join(word)))
            word.clear()

    while i < len(command):
        char = command[i]
        if quote == "'":
            if char == "'":
                quote = None
            else:
                word.append(char)
            i += 1
            continue
        if quote == '"':
            if char == '"':
                quote = None
            elif char in {"$", "`"}:
                return None
            elif char == "\\" and i + 1 < len(command):
                i += 1
                word.append(command[i])
            else:
                word.append(char)
            i += 1
            continue

        if char in {"'", '"'}:
            quote = char
        elif char == "\\":
            if i + 1 >= len(command):
                return None
            i += 1
            word.append(command[i])
        elif char.isspace():
            flush_word()
        elif char in {"$", "`", "(", ")", "{", "}"}:
            return None
        elif char in {";", "&", "|"}:
            flush_word()
            operator = char
            if i + 1 < len(command) and command[i + 1] == char:
                operator += char
                i += 1
            if operator == "&":
                return None
            tokens.append(Token("operator", operator))
        elif char in {">", "<"}:
            fd = ""
            if word and "".join(word).isdigit():
                fd = "".join(word)
                word.clear()
            else:
                flush_word()
            operator = char
            if i + 1 < len(command) and command[i + 1] == char:
                operator += char
                i += 1
            tokens.append(Token("redirect", fd + operator))
        else:
            word.append(char)
        i += 1

    if quote is not None:
        return None
    flush_word()
    return tokens


def _segments(tokens: list[Token]) -> list[list[str]] | None:
    """Return command segments after accepting only stderr-to-/dev/null redirects."""
    segments: list[list[str]] = []
    current: list[str] = []
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if token.kind == "redirect":
            return None
        if token.kind == "operator":
            return None
        else:
            current.append(token.value)
        i += 1
    if not current:
        return None
    segments.append(current)
    return segments


def _safe_options(
    arguments: list[str],
    *,
    flags: set[str],
    value_options: set[str] | None = None,
    value_patterns: tuple[str, ...] = (),
) -> bool:
    """Accept operands and only explicitly enumerated options."""
    value_options = value_options or set()
    after_separator = False
    i = 0
    while i < len(arguments):
        argument = arguments[i]
        if after_separator:
            i += 1
            continue
        if argument == "--":
            after_separator = True
        elif argument in flags:
            pass
        elif argument in value_options:
            if i + 1 >= len(arguments):
                return False
            i += 1
        elif any(re.fullmatch(pattern, argument) for pattern in value_patterns):
            pass
        elif argument.startswith("-"):
            return False
        i += 1
    return True


def _safe_git(arguments: list[str]) -> bool:
    if not arguments:
        return False
    subcommand, options = arguments[0], arguments[1:]
    if subcommand == "branch":
        return options == ["--show-current"]
    if subcommand == "status":
        return _safe_options(
            options,
            flags={
                "-b",
                "-s",
                "--ahead-behind",
                "--branch",
                "--no-ahead-behind",
                "--porcelain",
                "--short",
                "--show-stash",
            },
            value_patterns=(r"--porcelain=v[12]", r"--untracked-files=(?:all|normal|no)"),
        )
    if subcommand in {"log", "show"}:
        return _safe_options(
            options,
            flags={
                "--all",
                "--decorate",
                "--name-only",
                "--name-status",
                "--no-decorate",
                "--no-patch",
                "--oneline",
                "--shortstat",
                "--stat",
            },
            value_options={"-n", "--format", "--max-count", "--pretty", "--since", "--until"},
            value_patterns=(
                r"-\d+",
                r"--format=.+",
                r"--max-count=\d+",
                r"--pretty=.+",
                r"--since=.+",
                r"--until=.+",
            ),
        )
    if subcommand == "diff":
        return _safe_options(
            options,
            flags={
                "-p",
                "--cached",
                "--check",
                "--merge-base",
                "--name-only",
                "--name-status",
                "--no-color",
                "--numstat",
                "--patch",
                "--shortstat",
                "--staged",
                "--stat",
                "--summary",
            },
            value_options={"-U", "--unified"},
            value_patterns=(r"-U\d+", r"--color=(?:always|auto|never)", r"--unified=\d+"),
        )
    if subcommand == "name-rev":
        return _safe_options(
            options,
            flags={"--all", "--name-only", "--tags"},
            value_patterns=(r"--refs=.+",),
        )
    if subcommand == "rev-parse":
        return _safe_options(
            options,
            flags={
                "--absolute-git-dir",
                "--git-dir",
                "--is-bare-repository",
                "--is-inside-work-tree",
                "--show-cdup",
                "--show-object-format",
                "--show-prefix",
                "--show-superproject-working-tree",
                "--show-toplevel",
                "--symbolic-full-name",
                "--verify",
            },
            value_options={"--abbrev-ref", "--short"},
            value_patterns=(r"--abbrev-ref=(?:loose|strict)", r"--short=\d+"),
        )
    return False


def _safe_sed(arguments: list[str]) -> bool:
    if len(arguments) < 3 or arguments[0] != "-n":
        return False
    script = arguments[1]
    return re.fullmatch(r"\d+(?:,\d+|,\$)?p", script) is not None


def _safe_find(arguments: list[str]) -> bool:
    if not arguments:
        return False
    value_predicates = {
        "-iname",
        "-ipath",
        "-maxdepth",
        "-mindepth",
        "-mmin",
        "-mtime",
        "-name",
        "-newer",
        "-path",
        "-size",
        "-type",
    }
    flag_predicates = {"!", "-a", "-empty", "-false", "-o", "-print", "-print0", "-readable", "-true"}
    i = 0
    saw_root = False
    while i < len(arguments):
        argument = arguments[i]
        if not argument.startswith("-") and argument != "!":
            if saw_root:
                return False
            saw_root = True
        elif argument in value_predicates:
            if i + 1 >= len(arguments):
                return False
            i += 1
        elif argument not in flag_predicates:
            return False
        i += 1
    return saw_root


def _safe_diff(arguments: list[str]) -> bool:
    return _safe_options(
        arguments,
        flags={"-N", "-q", "-r", "-s", "-u", "--brief", "--new-file", "--recursive", "--report-identical-files"},
        value_options={"-U", "--unified"},
        value_patterns=(r"-U\d+", r"--unified=\d+"),
    )


def _safe_simple_command(command: str, arguments: list[str]) -> bool:
    if command in {"true", "false"}:
        return not arguments
    if command == "cat":
        return bool(arguments) and all(argument == "--" or not argument.startswith("-") for argument in arguments)
    if command == "grep":
        return _safe_rg(arguments)
    if command == "test":
        return True
    if command in {"basename", "dirname", "sha256sum", "shasum"}:
        return bool(arguments) and all(argument == "--" or not argument.startswith("-") for argument in arguments)
    if command == "cmp":
        return _safe_options(arguments, flags={"-s", "--quiet", "--silent"})
    if command == "du":
        return _safe_options(
            arguments,
            flags={"-a", "-h", "-s", "--all", "--human-readable", "--summarize"},
            value_options={"-d", "--max-depth"},
            value_patterns=(r"--max-depth=\d+",),
        )
    if command == "file":
        return _safe_options(
            arguments,
            flags={
                "-L",
                "-b",
                "-h",
                "-i",
                "-z",
                "--brief",
                "--dereference",
                "--mime",
                "--mime-encoding",
                "--mime-type",
                "--no-dereference",
                "--uncompress",
            },
        )
    if command in {"head", "tail"}:
        return _safe_options(
            arguments,
            flags={"-q", "-v", "--quiet", "--verbose"},
            value_options={"-c", "-n", "--bytes", "--lines"},
            value_patterns=(r"-\d+", r"--bytes=[+-]?\d+", r"--lines=[+-]?\d+"),
        )
    if command == "ls":
        return _safe_options(
            arguments,
            flags={"--almost-all", "--directory", "--human-readable"},
            value_patterns=(r"-[1RadhilrstS]+", r"--color=(?:always|auto|never)"),
        )
    if command == "pwd":
        return _safe_options(arguments, flags={"-L", "-P"})
    if command in {"readlink", "realpath"}:
        return _safe_options(arguments, flags={"-e", "-f", "-m", "-n", "-q", "-v", "-z"})
    if command == "stat":
        return _safe_options(
            arguments,
            flags={"-L", "-f", "-t", "--dereference", "--file-system", "--terse"},
            value_options={"-c", "--format"},
            value_patterns=(r"--format=.+",),
        )
    if command == "uptime":
        return _safe_options(arguments, flags={"-p", "-s", "--pretty", "--since"})
    if command == "wc":
        return _safe_options(
            arguments,
            flags={"--bytes", "--chars", "--lines", "--max-line-length", "--words"},
            value_patterns=(r"-[clmwL]+",),
        )
    return False


def _safe_rg(arguments: list[str]) -> bool:
    """Allow only minimal query-only ripgrep syntax; no helper-bearing long options."""
    i = 0
    while i < len(arguments) and arguments[i] in {"-F", "-c", "-i", "-l", "-n"}:
        i += 1
    used_separator = i < len(arguments) and arguments[i] == "--"
    if used_separator:
        i += 1
    positional = arguments[i:]
    if len(positional) < 2:
        return False
    return used_separator or not any(argument.startswith("-") for argument in positional)


def _safe_rtk_read(arguments: list[str]) -> bool:
    i = 0
    used_separator = False
    while i < len(arguments):
        argument = arguments[i]
        if argument == "--":
            used_separator = True
            i += 1
            break
        if argument in {"-n", "-v", "-vv", "-vvv", "--line-numbers", "--ultra-compact"}:
            i += 1
            continue
        if argument in {"-l", "--level"}:
            if i + 1 >= len(arguments) or arguments[i + 1] not in {"none", "minimal", "aggressive"}:
                return False
            i += 2
            continue
        if argument in {"-m", "--max-lines", "--tail-lines"}:
            if i + 1 >= len(arguments) or not arguments[i + 1].isdigit():
                return False
            i += 2
            continue
        if argument.startswith("-"):
            return False
        break
    paths = arguments[i:]
    return len(paths) == 1 and (used_separator or not paths[0].startswith("-"))


def _safe_rtk_grep(arguments: list[str]) -> bool:
    i = 0
    while i < len(arguments):
        argument = arguments[i]
        if argument == "--":
            i += 1
            break
        if argument in {"-n", "-v", "-vv", "-vvv", "--context-only", "--line-numbers", "--ultra-compact"}:
            i += 1
            continue
        if argument in {"-l", "--max-len", "-m", "--max"}:
            if i + 1 >= len(arguments) or not arguments[i + 1].isdigit():
                return False
            i += 2
            continue
        if argument in {"-t", "--file-type"}:
            if i + 1 >= len(arguments) or re.fullmatch(r"[A-Za-z0-9_+-]+", arguments[i + 1]) is None:
                return False
            i += 2
            continue
        if argument.startswith("-"):
            return False
        break
    positional = arguments[i:]
    if len(positional) < 2:
        return False
    # rtk forwards trailing arguments to rg, so even post-`--` option-looking
    # values are denied instead of relying on wrapper delimiter preservation.
    return not any(argument.startswith("-") for argument in positional)


def _safe_rtk(arguments: list[str]) -> bool:
    if not arguments:
        return False
    subcommand = arguments[0]
    if subcommand == "read":
        return _safe_rtk_read(arguments[1:])
    if subcommand == "grep":
        return _safe_rtk_grep(arguments[1:])
    if subcommand in {"stat", "sha256sum", "wc", "cat"}:
        return _safe_simple_command(subcommand, arguments[1:])
    if subcommand == "git":
        return _safe_git(arguments[1:])
    return False


def _safe_segment(arguments: list[str]) -> bool:
    if not arguments:
        return False
    executable = arguments[0]
    basename = executable.rsplit("/", 1)[-1]
    # This is the one trusted absolute RTK wrapper; normalize only this exact
    # path to the existing RTK grammar without broadening absolute binaries.
    if executable == TRUSTED_ABSOLUTE_RTK_WRAPPER:
        return _safe_rtk(arguments[1:])
    if executable not in TRUSTED_ABSOLUTE_READ_COMMANDS:
        return False
    if basename == "git":
        return _safe_git(arguments[1:])
    if basename == "rg":
        return _safe_rg(arguments[1:])
    if basename == "sed":
        return _safe_sed(arguments[1:])
    if basename == "find":
        return _safe_find(arguments[1:])
    if basename == "diff":
        return _safe_diff(arguments[1:])
    return _safe_simple_command(basename, arguments[1:])


def _safe_shell_command(command: str) -> bool:
    tokens = _tokenize_shell(command)
    if tokens is None:
        return False
    segments = _segments(tokens)
    return segments is not None and all(_safe_segment(segment) for segment in segments)


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, OSError):
        return _deny("Malformed PreToolUse payload", payload={}, reason_code="malformed_payload")
    if not isinstance(payload, dict):
        return _deny("PreToolUse payload must be an object", payload={}, reason_code="non_object_payload")

    model = payload.get("model") if isinstance(payload.get("model"), str) else "unknown"
    tool_name = payload.get("tool_name") if isinstance(payload.get("tool_name"), str) else ""
    if os.environ.get("CODEX_DELEGATION_REQUIRED") == "1":
        try:
            packet_path = pathlib.Path(os.environ["CODEX_DELEGATION_PACKET"])
            state_root = pathlib.Path(os.environ["CODEX_DELEGATION_STATE_ROOT"])
            expected_packet = os.environ.get("CODEX_DELEGATION_PACKET_SHA256")
            with _dc.locked_snapshot(state_root, packet_path) as tx:
                if expected_packet != tx.packet_sha256:
                    return _deny("delegation packet self-hash mismatch", payload=payload, model=model, tool_name=tool_name, reason_code="delegation_identity")
                payload_model = payload.get("model") if isinstance(payload.get("model"), str) else ""
                exposed_task = payload.get("task_id", payload.get("agent_id", payload.get("child_task_id", "")))
                if not payload_model or not isinstance(exposed_task, str) or payload_model != tx.packet.get("assigned_model") or exposed_task != tx.packet.get("child_task_id"):
                    return _deny("delegation payload identity mismatch", payload=payload, model=model, tool_name=tool_name, reason_code="delegation_identity")
                tx.revalidate(expected_phase="STARTED")
                rec = tx.state["packets"][ _dc.state_key(tx.packet) ]
                ok, reason = _delegation_tool_allowed(tx.packet, payload, tx.state, rec, tool_name)
                if not ok:
                    return _deny(reason, payload=payload, model=model, tool_name=tool_name, reason_code="delegation_permission")
        except Exception:
            return _deny("invalid active delegation context", payload=payload, model=model, tool_name=tool_name, reason_code="delegation_context")
    # Outside a delegated child, capability remains model-unrestricted; platform,
    # user, and task-level approvals are intentionally not synthesized here.
    record_receipt("PreToolUse", payload, model=model, tool_name=tool_name, decision="allow", reason_code="model_permissions_unrestricted")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Receipt-preserving PreToolUse capability router."""

from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass
from typing import Any

from hook_receipt import record_receipt


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
    # Tool capability is no longer model-gated here.  Role selection and any
    # consequential-action approval remain task/user/L0/platform concerns.
    record_receipt(
        "PreToolUse",
        payload,
        model=model,
        tool_name=tool_name,
        decision="allow",
        reason_code="model_permissions_unrestricted",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

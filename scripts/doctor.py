#!/usr/bin/env python3
"""Report whether a V23 local installation is usable without changing it."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Iterable

from scripts.install import CONFIG_KIND, LOCAL_KIND, PORTABLE_KIND, active_global_agents, block_body, read_toml


def _result(name: str, ok: bool, detail: str) -> dict[str, object]:
    return {"name": name, "ok": ok, "detail": detail}


def _github_login(config_dir: str) -> tuple[bool, str]:
    if not config_dir:
        return False, "not configured"
    if not Path(config_dir).is_dir():
        return False, "configured GH_CONFIG_DIR does not exist"
    completed = subprocess.run(
        ["gh", "api", "user", "--jq", ".login"],
        env={**os.environ, "GH_CONFIG_DIR": config_dir},
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode:
        return False, "GitHub CLI identity check failed"
    return True, completed.stdout.strip()


def _agent_chain(project: Path) -> list[str]:
    """Return the effective project instruction candidates in Codex order."""
    project = project.resolve()
    root = project
    completed = subprocess.run(
        ["git", "-C", str(project), "rev-parse", "--show-toplevel"],
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode == 0 and completed.stdout.strip():
        root = Path(completed.stdout.strip()).resolve()
    directories = [root]
    cursor = project
    while cursor != root and root in cursor.parents:
        directories.append(cursor)
        cursor = cursor.parent
    directories = [root, *reversed(directories[1:])]
    candidates: list[str] = []
    for directory in directories:
        for name in ("AGENTS.override.md", "AGENTS.md"):
            target = directory / name
            if target.is_file() and target.read_text().strip():
                candidates.append(str(target))
                break
    return candidates


def _agent_check(path: Path, expected_name: str, expected_model: str, expected_effort: str) -> tuple[bool, str]:
    if not path.is_file() or path.is_symlink():
        return False, f"missing or unsafe: {path}"
    try:
        agent = tomllib.loads(path.read_text())
    except (OSError, tomllib.TOMLDecodeError) as error:
        return False, f"invalid TOML: {error}"
    for field in ("name", "description", "developer_instructions"):
        if not isinstance(agent.get(field), str) or not agent[field].strip():
            return False, f"missing {field}"
    if agent["name"] != expected_name:
        return False, "name does not match registration"
    if agent.get("model") != expected_model or agent.get("model_reasoning_effort") != expected_effort:
        return False, "model mapping does not match local configuration"
    return True, str(path)


def _profile_check(path: Path, primary: str, effort: str, reviewer: str) -> tuple[bool, str]:
    if not path.is_file() or path.is_symlink():
        return False, f"missing or unsafe: {path}"
    try:
        profile = tomllib.loads(path.read_text())
    except (OSError, tomllib.TOMLDecodeError) as error:
        return False, f"invalid TOML: {error}"
    if (
        profile.get("model") != primary
        or profile.get("model_reasoning_effort") != effort
        or profile.get("review_model") != reviewer
    ):
        return False, "profile model mapping does not match local configuration"
    return True, str(path)


def doctor(codex_home: Path, local_config: Path, project: Path, check_github: bool = True) -> dict:
    checks: list[dict[str, object]] = []
    codex_home = codex_home.resolve()
    agents_path = active_global_agents(codex_home)
    global_text = agents_path.read_text() if agents_path.exists() else ""
    for kind in (PORTABLE_KIND, LOCAL_KIND):
        try:
            present = block_body(global_text, kind) is not None
            checks.append(_result(f"global_{kind.lower()}", present, str(agents_path)))
        except Exception as error:  # doctor must report malformed ownership safely.
            checks.append(_result(f"global_{kind.lower()}", False, str(error)))
    config_path = codex_home / "config.toml"
    config_text = config_path.read_text() if config_path.exists() else ""
    runtime_config: dict = {}
    try:
        runtime_config = tomllib.loads(config_text)
        checks.append(_result("codex_config_syntax", True, str(config_path)))
    except (OSError, tomllib.TOMLDecodeError) as error:
        checks.append(_result("codex_config_syntax", False, str(error)))
    try:
        registered = block_body(config_text, CONFIG_KIND) is not None
        checks.append(_result("agent_registration", registered, str(config_path)))
    except Exception as error:
        checks.append(_result("agent_registration", False, str(error)))
    skill = codex_home / "skills/engineering-delivery/SKILL.md"
    checks.append(_result("engineering_delivery_skill", skill.is_file() and not skill.is_symlink(), str(skill)))
    try:
        local = read_toml(local_config)
    except Exception as error:
        local = {}
        checks.append(_result("local_config", False, str(error)))
    else:
        models = local.get("models", {})
        configured = isinstance(models, dict) and all(models.get(key) for key in ("primary", "executor", "reviewer"))
        checks.append(_result("local_config", configured, str(local_config)))
        opening = local.get("opening", {})
        opening_ok = isinstance(opening, dict) and isinstance(opening.get("instruction"), str) and bool(opening["instruction"].strip())
        checks.append(_result("local_opening", opening_ok, "configured outside the repository"))
        if configured:
            profile_ok, profile_detail = _profile_check(
                codex_home / "v23-primary.config.toml",
                models["primary"],
                models.get("primary_effort", "medium"),
                models["reviewer"],
            )
            checks.append(_result("primary_profile", profile_ok, profile_detail))
            expected_agents = (
                ("v23-executor.toml", "v23_executor", models["executor"], models.get("executor_effort", "medium")),
                ("v23-reviewer.toml", "v23_reviewer", models["reviewer"], "high"),
            )
            registered = runtime_config.get("agents", {}) if isinstance(runtime_config, dict) else {}
            for filename, name, model, effort in expected_agents:
                ok, detail = _agent_check(codex_home / "agents" / filename, name, model, effort)
                configured_agent = registered.get(name, {}) if isinstance(registered, dict) else {}
                registration_ok = isinstance(configured_agent, dict) and configured_agent.get("config_file") == f"agents/{filename}"
                checks.append(_result(f"agent_{name}", ok and registration_ok, detail if ok else detail))
        tools = local.get("tools", {})
        if not isinstance(tools, dict):
            checks.append(_result("tools_config", False, "[tools] is not a TOML table"))
            tools = {}
        for tool, command in tools.items():
            if command in (None, ""):
                continue
            found = bool(isinstance(command, str) and command and (Path(command).is_file() or shutil.which(command)))
            checks.append(_result(f"tool_{tool}", found, "configured" if found else "not configured or unavailable"))
        if check_github:
            github = local.get("github", {})
            if not isinstance(github, dict):
                checks.append(_result("github_config", False, "[github] is not a TOML table"))
                github = {}
            for role in ("author", "reviewer"):
                ok, detail = _github_login(github.get(f"{role}_config_dir", ""))
                expected = github.get(f"{role}_login", "")
                identity_ok = ok and bool(expected) and detail.casefold() == expected.casefold()
                checks.append(_result(f"github_{role}", identity_ok, detail if ok else detail))
            author, reviewer = github.get("author_login", ""), github.get("reviewer_login", "")
            checks.append(_result("github_audit_identity_split", bool(author and reviewer and author.casefold() != reviewer.casefold()), "same-machine audit identities must differ"))
    return {
        "ok": all(bool(check["ok"]) for check in checks),
        "active_global_instruction": str(agents_path),
        "project_instruction_candidates": _agent_chain(project),
        "primary_profile_start": "codex --profile v23-primary",
        "checks": checks,
    }


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--codex-home", type=Path, default=Path.home() / ".codex")
    parser.add_argument("--local-config", type=Path, default=Path.home() / ".config/codex-harness/local.toml")
    parser.add_argument("--project", type=Path, default=Path.cwd())
    parser.add_argument("--skip-github", action="store_true")
    args = parser.parse_args(argv)
    result = doctor(args.codex_home, args.local_config, args.project, not args.skip_github)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

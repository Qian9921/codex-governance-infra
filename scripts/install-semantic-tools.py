#!/usr/bin/env python3
"""Clone, build, verify, register, and doctor the pinned semantic backend."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
from typing import Any


UPSTREAM_URL = "https://github.com/samchon/graph.git"
UPSTREAM = {"name": "@samchon/graph", "head": "95e20c9540e85fef542466172484229356d3d0d8",
            "tree": "e9ce033e380d77265c601579e436218502a6ccbd"}
VERSION = "21.0.0"
MANIFEST = "semantic-tools.v21.json"
PYRIGHT_VERSION = "1.1.390"
REGISTRATION = "semantic-gateway-mcp.json"
CONFIG_TOML = "config.toml"
CONFIG_BACKUP = "config.toml.semantic-gateway.v21.backup"
REGISTRATION_STATE = "semantic-gateway-registration.v21.json"
MCP_SECTION = "[mcp_servers.codex-semantic-gateway]"


def _sha(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _run(command: list[str], cwd: pathlib.Path | None = None, timeout: int = 900) -> tuple[int, str]:
    try:
        result = subprocess.run(command, cwd=str(cwd) if cwd else None, capture_output=True,
                                text=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 127, type(exc).__name__
    return result.returncode, (result.stdout or result.stderr).strip()[-600:]


def _git(path: pathlib.Path, *args: str) -> str | None:
    code, value = _run(["git", *args], path, timeout=30)
    return value if code == 0 and value else None


def _derive_workset(repo: pathlib.Path, explicit: tuple[str, ...] = ()) -> tuple[str, ...]:
    """Select a deterministic resident input, capped at the C++ contract."""
    if explicit:
        candidates = list(explicit)
    else:
        try:
            raw = subprocess.check_output(["git", "ls-files", "-z"], cwd=repo)
            candidates = [item for item in raw.decode("utf-8", errors="surrogateescape").split("\0") if item]
        except (OSError, subprocess.CalledProcessError):
            candidates = []
        candidates = [item for item in candidates if pathlib.Path(item).suffix.lower() in
                      {".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp", ".hxx", ".py"}]
    selected: list[str] = []
    for item in candidates:
        path = (repo / item).resolve()
        try:
            path.relative_to(repo)
        except ValueError:
            raise SystemExit("workset escapes repository:" + item)
        if not path.is_file() or path.is_symlink():
            raise SystemExit("workset file is missing or unsafe:" + item)
        selected.append(item)
    return tuple(sorted(dict.fromkeys(selected))[:64])


def _provider(name: str, executable: str | None = None) -> dict[str, Any]:
    path = executable or shutil.which(name)
    if not path:
        return {"name": name, "status": "NOT_READY", "reason": "PROVIDER_MISSING"}
    code, version = _run([path, "--version"], timeout=30)
    return {"name": name, "status": "READY" if code == 0 else "PARTIAL",
            "path": path, "version": version if code == 0 else None,
            "binary_sha256": _sha(pathlib.Path(path)) if code == 0 else None}


def _backend_entrypoint(checkout: pathlib.Path) -> pathlib.Path | None:
    # The pinned upstream package exposes its MCP CLI through this package bin;
    # keeping the path explicit prevents a successful build from being mistaken
    # for a runnable semantic backend.
    candidates = (checkout / "packages" / "graph" / "lib" / "bin.js",
                  checkout / "dist" / "inspect-code-graph.js", checkout / "dist" / "mcp.js",
                  checkout / "dist" / "index.js", checkout / "bin" / "inspect-code-graph")
    return next((path for path in candidates if path.is_file() and not path.is_symlink()), None)


def _backend_command(checkout: pathlib.Path, entrypoint: pathlib.Path | None,
                     tools_home: pathlib.Path) -> list[str] | None:
    if not entrypoint:
        return None
    runner = pathlib.Path(__file__).resolve().parents[1] / "codex" / "bin" / "semantic-backend-launcher.py"
    if entrypoint.suffix == ".js":
        return ["python3", str(runner), "--profile", "cpp_resident", "--", "node", str(entrypoint)]
    return ["python3", str(runner), "--profile", "cpp_resident", "--", str(entrypoint)]


def _pyright_executable(tools_home: pathlib.Path | None) -> pathlib.Path | None:
    if not tools_home:
        return None
    candidates = (tools_home / "pyright" / "bin" / "pyright",
                  tools_home / "pyright" / "pyright")
    return next((path for path in candidates if path.is_file() and not path.is_symlink()), None)


def _write_pyright_launcher(tools_home: pathlib.Path) -> pathlib.Path:
    target = tools_home / "pyright" / "bin" / "pyright"
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = target.with_name(target.name + ".tmp")
    temporary.write_text("#!/usr/bin/env python3\nimport os, pathlib, sys\nroot = pathlib.Path(__file__).resolve().parents[1]\nos.environ['PYTHONPATH'] = str(root) + os.pathsep + os.environ.get('PYTHONPATH', '')\nos.execv(sys.executable, [sys.executable, '-m', 'pyright', *sys.argv[1:]])\n", encoding="utf-8")
    temporary.chmod(0o700)
    os.replace(temporary, target)
    return target


def _write_backend_config(tools_home: pathlib.Path, command: list[str] | None,
                          pyright: pathlib.Path | None, clangd: dict[str, Any],
                          checkout: pathlib.Path | None = None,
                          entrypoint: pathlib.Path | None = None,
                          workset: tuple[str, ...] = ()) -> pathlib.Path:
    target = tools_home / "semantic-gateway-config.json"
    value = {"version": VERSION, "upstream": UPSTREAM, "profile": "cpp_resident",
             "workset": list(workset),
             "backend_command": command or [],
             "provider_commands": {"cpp": clangd.get("path", "clangd"),
                                   "python": str(pyright) if pyright else "pyright"},
             "backend_identity": {"checkout_head": _git(checkout, "rev-parse", "HEAD") if checkout else None,
                                   "checkout_tree": _git(checkout, "rev-parse", "HEAD^{tree}") if checkout else None,
                                   "binary_sha256": _sha(entrypoint) if entrypoint else None}}
    temporary = target.with_name(target.name + ".tmp")
    temporary.write_text(json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    os.replace(temporary, target)
    return target


def inspect(tools_home: pathlib.Path | None, codex_home: pathlib.Path | None = None,
            repo: pathlib.Path | None = None, workset: tuple[str, ...] = (),
            known_answer_symbol: str = "__codex_semantic_gateway_probe__") -> dict[str, Any]:
    checkout = tools_home / "samchon-graph" if tools_home else None
    upstream = None
    entrypoint = None
    if checkout and checkout.is_dir():
        entrypoint = _backend_entrypoint(checkout)
        upstream = {"path": str(checkout), "head": _git(checkout, "rev-parse", "HEAD"),
                    "tree": _git(checkout, "rev-parse", "HEAD^{tree}")}
    pinned = bool(upstream and upstream["head"] == UPSTREAM["head"] and upstream["tree"] == UPSTREAM["tree"])
    providers = {"clangd": _provider("clangd"), "pyright": _provider("pyright")}
    bundled = _pyright_executable(tools_home)
    if bundled:
        providers["pyright"] = _provider("pyright", str(bundled))
    build = {"lockfile": bool(checkout and (checkout / "pnpm-lock.yaml").is_file()),
             "entrypoint": str(entrypoint) if entrypoint else None,
             "entrypoint_sha256": _sha(entrypoint) if entrypoint else None,
             "built": bool(entrypoint)}
    registration = codex_home / REGISTRATION if codex_home else None
    config_toml = codex_home / CONFIG_TOML if codex_home else None
    ready = pinned and build["built"] and build["lockfile"] and all(value["status"] == "READY" for value in providers.values())
    status = "READY" if ready else "PARTIAL"
    selected_workset = _derive_workset(repo, workset) if repo else tuple(workset)
    result = {"schema": MANIFEST, "version": VERSION, "status": status,
            "upstream": {**UPSTREAM, "url": UPSTREAM_URL, "checkout": upstream, "pinned": pinned},
            "build": build, "providers": providers,
            "backend": {"command": _backend_command(checkout, entrypoint, tools_home) if checkout else None,
                         "identity": {"checkout_head": upstream.get("head") if upstream else None,
                                       "checkout_tree": upstream.get("tree") if upstream else None,
                                       "binary_sha256": build["entrypoint_sha256"]}},
            "registration": {"path": str(registration) if registration else None,
                             "present": bool(registration and registration.is_file()),
                             "config_toml": str(config_toml) if config_toml else None,
                             "config_present": bool(config_toml and config_toml.is_file() and
                                                     MCP_SECTION in config_toml.read_text(encoding="utf-8"))},
            "fallback": None if ready else "bounded_exact_evidence", "truthful": True,
            "workset": list(selected_workset)}
    if repo and result["backend"]["command"]:
        try:
            package_root = pathlib.Path(__file__).resolve().parents[1]
            if str(package_root) not in sys.path:
                sys.path.insert(0, str(package_root))
            from codex.semantic_gateway.gateway import Gateway, GatewayConfig
            result["semantic_doctor"] = Gateway(GatewayConfig(
                repo=repo, workset=selected_workset,
                backend_command=tuple(result["backend"]["command"]),
                provider_commands={"cpp": providers["clangd"].get("path", "clangd"),
                                   "python": providers["pyright"].get("path", "pyright")},
                known_answer_symbol=known_answer_symbol)).doctor()
        except Exception as exc:
            result["semantic_doctor"] = {"status": "PARTIAL", "reason": type(exc).__name__,
                                          "truthful": True}
    return result


def _write_registration(codex_home: pathlib.Path, tools_home: pathlib.Path) -> pathlib.Path:
    codex_home.mkdir(mode=0o700, parents=True, exist_ok=True)
    target = codex_home / REGISTRATION
    command = codex_home / "bin" / "semantic-gateway-mcp.py"
    config = tools_home / "semantic-gateway-config.json"
    value = {"name": "codex-semantic-gateway", "command": str(command),
             "args": ["--config", str(config)],
             "config": str(config),
             "transport": "stdio", "managed_by": MANIFEST, "upstream": UPSTREAM}
    descriptor, temporary = tempfile.mkstemp(prefix=target.name + ".", dir=codex_home)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, sort_keys=True, separators=(",", ":")); handle.write("\n"); handle.flush(); os.fsync(handle.fileno())
        os.replace(temporary, target)
    finally:
        try: os.unlink(temporary)
        except FileNotFoundError: pass
    return target


def _upsert_mcp_config(codex_home: pathlib.Path, tools_home: pathlib.Path) -> pathlib.Path:
    """Add exactly one managed TOML section while preserving other sections."""
    target = codex_home / CONFIG_TOML
    backup = codex_home / CONFIG_BACKUP
    state = codex_home / REGISTRATION_STATE
    prior_state: dict[str, Any] = {}
    if state.is_file():
        try:
            loaded = json.loads(state.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                prior_state = loaded
        except (OSError, json.JSONDecodeError):
            prior_state = {}
    existed = target.is_file()
    # Once registration has started, the metadata—not the presence of the
    # managed file—owns whether an original config existed. This prevents a
    # clean CODEX_HOME's second registration from backing up its own section.
    original_existed = bool(prior_state.get("config_existed", existed))
    original = target.read_text(encoding="utf-8") if existed else ""
    if original_existed and not backup.exists():
        shutil.copy2(target, backup)
    lines = original.splitlines(keepends=True)
    start = next((i for i, line in enumerate(lines) if line.strip() == MCP_SECTION), None)
    if start is None:
        updated = original.rstrip("\n") + ("\n\n" if original else "")
        config = tools_home / "semantic-gateway-config.json"
        updated += MCP_SECTION + "\ncommand = " + json.dumps(str(codex_home / "bin" / "semantic-gateway-mcp.py")) + "\nargs = " + json.dumps(["--config", str(config)]) + "\n"
    else:
        end = next((i for i in range(start + 1, len(lines)) if lines[i].lstrip().startswith("[")), len(lines))
        config = tools_home / "semantic-gateway-config.json"
        replacement = [MCP_SECTION + "\n",
                       "command = " + json.dumps(str(codex_home / "bin" / "semantic-gateway-mcp.py")) + "\n",
                       "args = " + json.dumps(["--config", str(config)]) + "\n"]
        updated = "".join(lines[:start] + replacement + lines[end:])
    temporary = target.with_name(target.name + ".tmp")
    temporary.write_text(updated, encoding="utf-8")
    os.replace(temporary, target)
    state.write_text(json.dumps({"version": VERSION, "config_existed": original_existed,
                                 "config": str(target), "backup": str(backup) if original_existed else None},
                                sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    return target


def _remove_mcp_config(codex_home: pathlib.Path) -> list[str]:
    target = codex_home / CONFIG_TOML
    backup = codex_home / CONFIG_BACKUP
    state = codex_home / REGISTRATION_STATE
    removed: list[str] = []
    if backup.is_file():
        os.replace(backup, target)
        removed.append(CONFIG_BACKUP)
    elif target.is_file():
        lines = target.read_text(encoding="utf-8").splitlines(keepends=True)
        start = next((i for i, line in enumerate(lines) if line.strip() == MCP_SECTION), None)
        if start is not None:
            end = next((i for i in range(start + 1, len(lines)) if lines[i].lstrip().startswith("[")), len(lines))
            updated = "".join(lines[:start] + lines[end:]).lstrip("\n")
            if updated:
                target.write_text(updated, encoding="utf-8")
            else:
                target.unlink()
                removed.append(CONFIG_TOML)
    if state.is_file():
        state.unlink(); removed.append(REGISTRATION_STATE)
    return removed


def install(tools_home: pathlib.Path, *, dry_run: bool, codex_home: pathlib.Path | None = None,
            register: bool = False, repo: pathlib.Path | None = None,
            workset: tuple[str, ...] = (), known_answer_symbol: str = "__codex_semantic_gateway_probe__") -> dict[str, Any]:
    result = inspect(tools_home, codex_home, repo, workset, known_answer_symbol)
    result.update({"operation": "DRY_RUN" if dry_run else "INSTALL", "planned": {
        "tools_home": "$SEMANTIC_TOOLS_HOME" if dry_run else str(tools_home),
        "clone": {"url": UPSTREAM_URL, "checkout": UPSTREAM["head"], "verify_tree": UPSTREAM["tree"]},
        "build": ["pnpm install --frozen-lockfile", "pnpm run build"],
        "pyright": f"install and verify pyright=={PYRIGHT_VERSION}",
        "clangd": "locate and verify host clangd; portable system installation is not claimed",
        "launcher": "semantic-backend-launcher.py with systemd-run scope or process-group timeout fallback",
        "register": {"json": str(codex_home / REGISTRATION) if codex_home and register and not dry_run else "$CODEX_HOME/semantic-gateway-mcp.json" if register else "guidance-only",
                     "config_toml": str(codex_home / CONFIG_TOML) if codex_home and register and not dry_run else "[mcp_servers.codex-semantic-gateway]" if register else "guidance-only"},
    }})
    if dry_run:
        return result
    tools_home.mkdir(mode=0o700, parents=True, exist_ok=True)
    checkout = tools_home / "samchon-graph"
    if checkout.exists() and not checkout.is_dir():
        raise SystemExit("unsafe semantic tools checkout")
    if not checkout.exists():
        code, output = _run(["git", "clone", "--filter=blob:none", UPSTREAM_URL, str(checkout)])
        if code != 0: raise SystemExit("unable to clone pinned semantic upstream:" + output)
    if _git(checkout, "rev-parse", "--is-inside-work-tree") != "true":
        raise SystemExit("semantic upstream is not a Git worktree")
    code, output = _run(["git", "fetch", "--depth", "1", "origin", UPSTREAM["head"]], checkout, 300)
    if code != 0: raise SystemExit("unable to fetch pinned semantic upstream:" + output)
    code, output = _run(["git", "checkout", "--detach", UPSTREAM["head"]], checkout, 60)
    if code != 0: raise SystemExit("unable to checkout pinned semantic upstream:" + output)
    if _git(checkout, "rev-parse", "HEAD^{tree}") != UPSTREAM["tree"]:
        raise SystemExit("pinned upstream tree mismatch")
    if not (checkout / "pnpm-lock.yaml").is_file():
        raise SystemExit("pinned upstream pnpm-lock.yaml is missing")
    for command in (("pnpm", "install", "--frozen-lockfile"), ("pnpm", "run", "build")):
        code, output = _run(list(command), checkout, 900)
        if code != 0: raise SystemExit("semantic backend build failed:" + output)
    pyright_dir = tools_home / "pyright"
    code, output = _run(["python3", "-m", "pip", "install", "--disable-pip-version-check", "--target", str(pyright_dir), f"pyright=={PYRIGHT_VERSION}"], tools_home, 900)
    if code != 0: raise SystemExit("pyright installation failed:" + output)
    pyright_executable = _write_pyright_launcher(tools_home)
    entrypoint = _backend_entrypoint(checkout)
    backend_command = _backend_command(checkout, entrypoint, tools_home)
    selected_workset = _derive_workset(repo, workset) if repo else tuple(workset)
    config_path = _write_backend_config(tools_home, backend_command, pyright_executable,
                                        _provider("clangd"), checkout, entrypoint, selected_workset)
    result = inspect(tools_home, codex_home, repo, selected_workset, known_answer_symbol)
    result["config"] = {"path": str(config_path), "present": config_path.is_file(),
                         "sha256": _sha(config_path)}
    if register and codex_home:
        _write_registration(codex_home, tools_home)
        _upsert_mcp_config(codex_home, tools_home)
        result = inspect(tools_home, codex_home, repo, selected_workset, known_answer_symbol)
        result["config"] = {"path": str(config_path), "present": config_path.is_file(),
                             "sha256": _sha(config_path)}
    manifest_path = tools_home / MANIFEST
    descriptor, temporary = tempfile.mkstemp(prefix=MANIFEST + ".", dir=tools_home)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(result, handle, sort_keys=True, separators=(",", ":")); handle.write("\n"); handle.flush(); os.fsync(handle.fileno())
        os.replace(temporary, manifest_path)
    finally:
        try: os.unlink(temporary)
        except FileNotFoundError: pass
    return result


def uninstall(tools_home: pathlib.Path, codex_home: pathlib.Path | None = None) -> dict[str, Any]:
    manifest = tools_home / MANIFEST
    if not manifest.is_file() or manifest.is_symlink(): raise SystemExit("no managed semantic-tools manifest")
    value = json.loads(manifest.read_text(encoding="utf-8"))
    if value.get("upstream", {}).get("head") != UPSTREAM["head"]: raise SystemExit("managed semantic upstream identity mismatch")
    checkout = tools_home / "samchon-graph"
    if checkout.exists(): shutil.rmtree(checkout)
    pyright_dir = tools_home / "pyright"
    if pyright_dir.exists(): shutil.rmtree(pyright_dir)
    config = tools_home / "semantic-gateway-config.json"
    if config.exists(): config.unlink()
    manifest.unlink()
    removed = ["samchon-graph", "pyright", "semantic-gateway-config.json", MANIFEST]
    if codex_home and (codex_home / REGISTRATION).is_file():
        (codex_home / REGISTRATION).unlink(); removed.append(REGISTRATION)
    if codex_home:
        removed.extend(_remove_mcp_config(codex_home))
    return {"schema": MANIFEST, "version": VERSION, "status": "UNINSTALLED", "removed": removed}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Install or inspect pinned semantic gateway dependencies")
    parser.add_argument("--tools-home", required=True); parser.add_argument("--codex-home"); parser.add_argument("--repo")
    parser.add_argument("--workset", action="append", default=[], help="relative resident source path; repeatable")
    parser.add_argument("--known-answer-symbol", default="__codex_semantic_gateway_probe__")
    parser.add_argument("--doctor", action="store_true"); parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--install", action="store_true"); parser.add_argument("--register", action="store_true")
    parser.add_argument("--uninstall", action="store_true")
    args = parser.parse_args(argv)
    tools_home = pathlib.Path(args.tools_home).expanduser().resolve()
    codex_home = pathlib.Path(args.codex_home).expanduser().resolve() if args.codex_home else None
    repo = pathlib.Path(args.repo).expanduser().resolve() if args.repo else None
    if sum(bool(item) for item in (args.doctor, args.install, args.uninstall)) > 1: parser.error("choose one lifecycle action")
    workset = tuple(args.workset)
    result = uninstall(tools_home, codex_home) if args.uninstall else install(tools_home, dry_run=args.dry_run, codex_home=codex_home, register=args.register, repo=repo, workset=workset, known_answer_symbol=args.known_answer_symbol) if args.install else inspect(tools_home, codex_home, repo, workset, args.known_answer_symbol)
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0 if result.get("status") in {"READY", "PARTIAL", "DRY_RUN", "UNINSTALLED"} else 2


if __name__ == "__main__": raise SystemExit(main())

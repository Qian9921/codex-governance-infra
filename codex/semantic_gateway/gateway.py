"""Compiler-backed semantic gateway.

The gateway owns lifecycle, identity and bounded routing. Language semantics
remain owned by the pinned ``@samchon/graph`` backend, which is spoken through
its MCP ``inspect_code_graph`` tool. A provider version string is never treated
as semantic proof.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import selectors
import shutil
import signal
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence


SCHEMA = "semantic-gateway.v1"
VERSION = "21.1.0"
UPSTREAM = {
    "name": "@samchon/graph",
    "head": "95e20c9540e85fef542466172484229356d3d0d8",
    "tree": "e9ce033e380d77265c601579e436218502a6ccbd",
}
OPERATIONS = (
    "resolve_symbol", "definition", "declaration", "references", "callers",
    "callees", "inheritance", "type_relations", "impact",
)
PROFILES: dict[str, dict[str, Any]] = {
    "cpp_resident": {"languages": ["c++", "c", "objective-c++"], "working_set_tus": 64,
                     "concurrency": 2, "cpus": 4, "memory_gib": 4, "timeout_sec": 180,
                     "mode": "resident"},
    "cpp_offline": {"languages": ["c++", "c", "objective-c++"], "working_set_tus": "full",
                    "concurrency": 1, "cpus": 4, "memory_gib": 8, "timeout_sec": 900,
                    "mode": "offline_static"},
    "python_resident": {"languages": ["python"], "working_set_files": "configured",
                         "concurrency": 1, "cpus": 4, "memory_gib": 2.5, "timeout_sec": 180,
                         "mode": "resident"},
}
_SNAPSHOTS: dict[str, "Gateway"] = {}
# Request-scoped MCP calls construct fresh Gateway objects. Keep only the
# content evidence needed to notice a graph edit across those objects; this is
# deliberately process-local and never a resident watcher or repo state file.
_BUILD_GRAPH_EVIDENCE: dict[str, str] = {}


class GatewayError(ValueError):
    """A malformed request or unsafe gateway configuration."""


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _run(command: Sequence[str], cwd: pathlib.Path, timeout: float = 5.0) -> tuple[int, str]:
    try:
        result = subprocess.run(command, cwd=str(cwd), capture_output=True, text=True,
                                timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 127, type(exc).__name__
    return result.returncode, (result.stdout or result.stderr).strip()[:400]


def _run_full(command: Sequence[str], cwd: pathlib.Path, timeout: float = 30.0) -> tuple[int, str]:
    """Run an identity-producing command without truncating byte-bearing output."""
    try:
        result = subprocess.run(command, cwd=str(cwd), capture_output=True, text=False,
                                timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 127, type(exc).__name__
    output = result.stdout if result.stdout else result.stderr
    return result.returncode, output.decode("utf-8", errors="surrogateescape")


def _git(repo: pathlib.Path, *args: str) -> str | None:
    code, output = _run(["git", *args], repo)
    return output if code == 0 and output else None


def _git_full(repo: pathlib.Path, *args: str) -> str | None:
    code, output = _run_full(["git", *args], repo)
    return output if code == 0 and output else None


def _canonical_repo(value: str | os.PathLike[str]) -> pathlib.Path:
    path = pathlib.Path(value).expanduser().resolve(strict=True)
    if _git(path, "rev-parse", "--git-dir") is None:
        raise GatewayError("repository is not a Git worktree")
    return path


@dataclass
class GatewayConfig:
    repo: pathlib.Path
    build_dir: pathlib.Path | None = None
    cpp_provider: str = "clangd"
    python_provider: str = "pyright"
    provider_commands: Mapping[str, str] = field(default_factory=dict)
    upstream: Mapping[str, str] = field(default_factory=lambda: dict(UPSTREAM))
    profile: str = "cpp_resident"
    workset: tuple[str, ...] = ()
    mcp_name: str = "codex-semantic-gateway"
    backend_command: tuple[str, ...] = ()
    backend_cwd: pathlib.Path | None = None
    backend_identity: Mapping[str, Any] = field(default_factory=dict)
    config_path: pathlib.Path | None = None
    known_answer_symbol: str = "__codex_semantic_gateway_probe__"
    auto_refresh_build: bool = False
    build_refresh_command: tuple[str, ...] = ()
    workset_limit: int = 64

    def __post_init__(self) -> None:
        if self.profile not in PROFILES:
            raise GatewayError("unknown resource profile:" + self.profile)
        if not self.cpp_provider or not self.python_provider:
            raise GatewayError("compiler providers are required")
        if any(not item for item in self.backend_command):
            raise GatewayError("backend command contains an empty argument")
        if self.workset_limit < 1 or self.workset_limit > 64:
            raise GatewayError("workset_limit must be between 1 and 64")
        if len(self.workset) > self.workset_limit:
            raise GatewayError("WORKSET_EXCEEDS_RESIDENT_LIMIT")
        if any(not item for item in self.build_refresh_command):
            raise GatewayError("build_refresh_command contains an empty argument")


def load_config(path: str | os.PathLike[str] | None,
                repo: str | os.PathLike[str] | None = None,
                language: str | None = None) -> GatewayConfig:
    raw: dict[str, Any] = {}
    config_path = pathlib.Path(path).expanduser().resolve(strict=True) if path else None
    if config_path:
        try:
            raw = json.loads(config_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise GatewayError("invalid semantic gateway config") from exc
        if not isinstance(raw, dict):
            raise GatewayError("semantic gateway config must be an object")
    root = _canonical_repo(repo or raw.get("repo") or os.getcwd())
    build_dir = None if raw.get("build_dir") in (None, "") else (root / str(raw["build_dir"])).resolve()
    if build_dir is None and (selected := next((candidate.parent for candidate in (
            root / "compile_commands.json", root / "build/compile_commands.json") if candidate.is_file()), None)):
        build_dir = selected
    if build_dir:
        try:
            build_dir.relative_to(root)
        except ValueError as exc:
            raise GatewayError("build_dir escapes repository") from exc
    selected_language = "python" if language == "python" else "cpp"
    workset = raw.get("workset", [])
    if not isinstance(workset, list) or not all(isinstance(item, str) for item in workset):
        raise GatewayError("workset must be a list of relative paths")
    for item in workset:
        try:
            (root / item).resolve().relative_to(root)
        except ValueError as exc:
            raise GatewayError("workset escapes repository") from exc
    if not workset:
        suffixes = {".py", ".pyi"} if selected_language == "python" else {
            ".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp", ".hxx"}
        tracked = (_git_full(root, "ls-files", "-z") or "").split("\0")
        workset = [item for item in tracked if item and pathlib.Path(item).suffix.lower() in suffixes
                   and (root / item).is_file()][:64]
    upstream = raw.get("upstream", UPSTREAM)
    if not isinstance(upstream, dict) or any(upstream.get(k) != UPSTREAM[k] for k in UPSTREAM):
        raise GatewayError("upstream identity must match the pinned @samchon/graph revision")
    backend = raw.get("backend_command", [])
    if not isinstance(backend, list) or not all(isinstance(item, str) for item in backend):
        raise GatewayError("backend_command must be a string list")
    backend_commands = raw.get("backend_commands", {})
    if not isinstance(backend_commands, dict) or any(
            key not in {"cpp", "python"} or not isinstance(value, list)
            or not all(isinstance(item, str) for item in value)
            for key, value in backend_commands.items()):
        raise GatewayError("backend_commands must map cpp/python to string lists")
    if selected_language in backend_commands:
        backend = backend_commands[selected_language]
    profiles = raw.get("profiles", {})
    if not isinstance(profiles, dict) or any(key not in {"cpp", "python"} or not isinstance(value, str)
                                             for key, value in profiles.items()):
        raise GatewayError("profiles must map cpp/python to resource profiles")
    backend_cwd = raw.get("backend_cwd")
    if backend_cwd:
        backend_cwd = (root / str(backend_cwd)).resolve()
        try:
            backend_cwd.relative_to(root)
        except ValueError as exc:
            raise GatewayError("backend_cwd escapes repository") from exc
    providers = raw.get("provider_commands", {})
    if not isinstance(providers, dict) or not all(isinstance(k, str) and isinstance(v, str)
                                                   for k, v in providers.items()):
        raise GatewayError("provider_commands must be an object of strings")
    refresh_command = raw.get("build_refresh_command", [])
    if not isinstance(refresh_command, list) or not all(isinstance(item, str) for item in refresh_command):
        raise GatewayError("build_refresh_command must be a string list")
    # Installed configs may omit the switch. CMake projects get the safe,
    # bounded refresh route by default; an explicit boolean remains sovereign.
    auto_refresh_build = raw.get("auto_refresh_build", (root / "CMakeLists.txt").is_file())
    if type(auto_refresh_build) is not bool:
        raise GatewayError("auto_refresh_build must be boolean")
    workset_limit = raw.get("workset_limit", 64)
    if type(workset_limit) is not int or not 1 <= workset_limit <= 64:
        raise GatewayError("workset_limit must be between 1 and 64")
    return GatewayConfig(
        repo=root, build_dir=build_dir,
        cpp_provider=str(raw.get("cpp_provider", "clangd")),
        python_provider=str(raw.get("python_provider", "pyright")),
        provider_commands=providers, upstream=upstream,
        profile=str(profiles.get(selected_language, raw.get("profile", "cpp_resident"))), workset=tuple(workset),
        mcp_name=str(raw.get("mcp_name", "codex-semantic-gateway")),
        backend_command=tuple(backend), backend_cwd=backend_cwd,
        backend_identity=raw.get("backend_identity", {}), config_path=config_path,
        known_answer_symbol=str(raw.get("known_answer_symbol", "__codex_semantic_gateway_probe__")),
        auto_refresh_build=auto_refresh_build, build_refresh_command=tuple(refresh_command),
        workset_limit=workset_limit,
    )


def _content_identity(root: pathlib.Path) -> dict[str, Any]:
    tracked = _git_full(root, "ls-files", "-z") or ""
    tracked_paths = [item for item in tracked.split("\0") if item]
    untracked_raw = _git_full(root, "ls-files", "--others", "--exclude-standard", "-z") or ""
    untracked_paths = [item for item in untracked_raw.split("\0") if item]
    entries: list[dict[str, str]] = []
    for item in sorted(set(tracked_paths + untracked_paths)):
        path = root / item
        if path.is_file() and not path.is_symlink():
            entries.append({"path": item, "sha256": _sha256(path.read_bytes())})
    diff = _git_full(root, "diff", "HEAD", "--binary") or ""
    status = _git_full(root, "status", "--porcelain=v1") or ""
    return {"files": entries, "tracked_unstaged_staged_diff_sha256": _sha256(diff.encode()),
            "status_sha256": _sha256(status.encode()),
            "untracked_paths": sorted(untracked_paths),
            "digest": _sha256(json.dumps(entries, sort_keys=True).encode() + diff.encode() + status.encode())}


def _provider(name: str, command: str | None, cwd: pathlib.Path) -> dict[str, Any]:
    executable = command or shutil.which(name)
    if not executable:
        return {"name": name, "status": "NOT_READY", "reason": "PROVIDER_MISSING"}
    resolved = shutil.which(executable) or executable
    code, version = _run([resolved, "--version"], cwd)
    if code != 0:
        return {"name": name, "status": "PARTIAL", "reason": "PROVIDER_VERSION_FAILED", "path": resolved}
    try:
        binary_hash = _sha256(pathlib.Path(resolved).read_bytes())
    except OSError:
        binary_hash = None
    return {"name": name, "status": "READY", "path": resolved, "version": version,
            "binary_sha256": binary_hash}


class BackendClient:
    """One bounded MCP lifecycle: initialize, tools/list, tools/call, close."""

    def __init__(self, config: GatewayConfig):
        self.config = config
        self.process: subprocess.Popen[str] | None = None
        self._request_id = 0
        self.tools: list[dict[str, Any]] = []
        self._scope: tempfile.TemporaryDirectory[str] | None = None
        self._scope_root: pathlib.Path | None = None

    def _command(self) -> list[str]:
        if self.config.backend_command:
            return list(self.config.backend_command)
        raise GatewayError("BACKEND_NOT_CONFIGURED")

    def _prepare_scope(self) -> pathlib.Path:
        """Materialize a bounded resident input; never index a full large repo."""
        if PROFILES[self.config.profile]["mode"] == "resident" and not self.config.workset:
            raise GatewayError("WORKSET_REQUIRED_FOR_RESIDENT")
        if PROFILES[self.config.profile]["mode"] != "resident" or not self.config.workset:
            return self.config.backend_cwd or self.config.repo
        if self.config.profile == "cpp_resident" and len(self.config.workset) > int(PROFILES["cpp_resident"]["working_set_tus"]):
            raise GatewayError("WORKSET_EXCEEDS_RESIDENT_LIMIT")
        self._scope = tempfile.TemporaryDirectory(prefix="codex-semantic-scope-")
        root = pathlib.Path(self._scope.name)
        for relative in self.config.workset:
            source = (self.config.repo / relative).resolve()
            try:
                source.relative_to(self.config.repo)
            except ValueError as exc:
                raise GatewayError("WORKSET_ESCAPES_REPOSITORY") from exc
            if not source.is_file() or source.is_symlink():
                raise GatewayError("WORKSET_FILE_MISSING:" + relative)
            target = root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        # A compile database is build input, not source scope, and is required
        # by clangd to produce compiler-derived facts for a selected TU.
        compile_db = self.config.build_dir / "compile_commands.json" if self.config.build_dir else None
        if compile_db and compile_db.is_file():
            shutil.copy2(compile_db, root / "compile_commands.json")
        self._scope_root = root
        return root

    @staticmethod
    def _upstream_props(operation: str, symbol: str) -> dict[str, Any]:
        """Map the stable gateway contract to @samchon/graph's request union."""
        if operation in {"resolve_symbol", "definition", "declaration"}:
            request: dict[str, Any] = {"type": "lookup", "query": symbol, "limit": 5}
            request_type = "lookup"
        elif operation in {"inheritance", "type_relations"}:
            request = {"type": "details", "handles": [symbol], "neighbors": True,
                       "neighborLimit": 16, "dependencyLimit": 16}
            request_type = "details"
        else:
            direction = "forward"
            focus = "execution"
            if operation in {"references", "callers"}:
                direction = "reverse"
            elif operation == "impact":
                direction, focus = "impact", "all"
            request = {"type": "trace", "from": symbol, "direction": direction,
                       "focus": focus, "maxDepth": 8, "maxNodes": 32}
            request_type = "trace"
        return {
            "question": f"{operation} for {symbol}",
            "draft": {"reason": "The semantic gateway selected the smallest pinned graph request.",
                       "type": request_type},
            "review": "The request is bounded to compiler-backed graph facts; exact source evidence remains the fallback.",
            "request": request,
        }

    def _read(self, timeout: float) -> dict[str, Any]:
        if not self.process or not self.process.stdout:
            raise GatewayError("BACKEND_NOT_RUNNING")
        selector = selectors.DefaultSelector()
        selector.register(self.process.stdout, selectors.EVENT_READ)
        events = selector.select(timeout)
        selector.close()
        if not events:
            raise GatewayError("BACKEND_TIMEOUT")
        line = self.process.stdout.readline()
        if not line:
            raise GatewayError("BACKEND_EOF")
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise GatewayError("BACKEND_INVALID_JSON") from exc
        if not isinstance(value, dict):
            raise GatewayError("BACKEND_INVALID_RESPONSE")
        return value

    def _request(self, method: str, params: Mapping[str, Any] | None = None) -> dict[str, Any]:
        if not self.process or not self.process.stdin:
            raise GatewayError("BACKEND_NOT_RUNNING")
        self._request_id += 1
        self.process.stdin.write(json.dumps({"jsonrpc": "2.0", "id": self._request_id,
                                             "method": method, "params": params or {}}) + "\n")
        self.process.stdin.flush()
        while True:
            response = self._read(PROFILES[self.config.profile]["timeout_sec"])
            if response.get("id") == self._request_id:
                if "error" in response:
                    raise GatewayError("BACKEND_RPC_ERROR:" + str(response["error"]))
                return response.get("result", {})

    def start(self) -> dict[str, Any]:
        if self.process:
            return {"status": "READY", "tools": self.tools}
        try:
            backend_cwd = self._prepare_scope()
            self.process = subprocess.Popen(
                self._command(), cwd=str(backend_cwd),
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, start_new_session=True,
            )
            initialize = self._request("initialize", {"protocolVersion": "2025-06-18",
                                                       "capabilities": {}, "clientInfo": {"name": "codex-semantic-gateway", "version": VERSION}})
            if not isinstance(initialize, dict):
                raise GatewayError("BACKEND_INITIALIZE_INVALID")
            self.process.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}}) + "\n")
            self.process.stdin.flush()
            listed = self._request("tools/list")
            self.tools = listed.get("tools", []) if isinstance(listed, dict) else []
            if not any(isinstance(tool, dict) and tool.get("name") == "inspect_code_graph" for tool in self.tools):
                raise GatewayError("BACKEND_INSPECT_TOOL_MISSING")
            return {"status": "READY", "initialize": initialize, "tools": self.tools}
        except Exception:
            self.close()
            raise

    def inspect(self, operation: str, symbol: str, language: str) -> dict[str, Any]:
        self.start()
        result = self._request("tools/call", {"name": "inspect_code_graph", "arguments": self._upstream_props(operation, symbol)})
        if not isinstance(result, dict):
            raise GatewayError("BACKEND_FACT_INVALID")
        # MCP tools commonly return JSON in a text content block.
        content = result.get("content")
        if isinstance(content, list):
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    try:
                        parsed = json.loads(item.get("text", ""))
                    except (TypeError, json.JSONDecodeError):
                        continue
                    if isinstance(parsed, dict):
                        result = parsed
                        break
        structured = result.get("structuredContent")
        if isinstance(structured, dict):
            result = dict(structured)
        facts = result.get("facts", result.get("result"))
        if facts in (None, [], {}, ""):
            raise GatewayError("BACKEND_EMPTY_FACT")
        result.setdefault("provenance", {"provider": "@samchon/graph", "audit": result.get("audit"),
                                          "next": result.get("next"), "request": self._upstream_props(operation, symbol)})
        result.setdefault("proved_families", [operation])
        return result

    def close(self) -> None:
        process, self.process = self.process, None
        if not process:
            return
        try:
            if process.stdin:
                process.stdin.write(json.dumps({"jsonrpc": "2.0", "id": -1, "method": "shutdown", "params": {}}) + "\n")
                process.stdin.flush()
        except OSError:
            pass
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGTERM)
                process.wait(timeout=1)
            except (OSError, subprocess.TimeoutExpired):
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except OSError:
                    pass
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream:
                try:
                    stream.close()
                except OSError:
                    pass
        if self._scope:
            self._scope.cleanup()
            self._scope = None
            self._scope_root = None


class Gateway:
    def __init__(self, config: GatewayConfig):
        self.config = config
        self.started_at = time.time()
        self._closed = False
        self._build_graph_signature: str | None = None
        self._build_refresh: dict[str, Any] = {"status": "NOT_RUN", "command": None}

    def _language_suffixes(self) -> set[str]:
        return {".py", ".pyi"} if self.config.profile.startswith("python") else {
            ".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp", ".hxx"
        }

    def _refresh_workset(self) -> None:
        """Reconcile the bounded input set at request time; never start a watcher."""
        suffixes = self._language_suffixes()
        tracked = (_git_full(self.config.repo, "ls-files", "-z") or "").split("\0")
        untracked = (_git_full(self.config.repo, "ls-files", "--others", "--exclude-standard", "-z") or "").split("\0")
        relevant = sorted({item for item in tracked + untracked if item and pathlib.Path(item).suffix.lower() in suffixes
                           and (self.config.repo / item).is_file()})
        status_lines = (_git_full(self.config.repo, "status", "--porcelain=v1") or "").splitlines()
        changed = sorted({line[3:] for line in status_lines if len(line) >= 4 and line[3:] in relevant})
        configured = [item for item in self.config.workset if item in relevant and item not in changed]
        selected = changed + configured
        selected.extend(item for item in relevant if item not in selected)
        selected = selected[: self.config.workset_limit]
        if tuple(selected) != self.config.workset:
            self.config = GatewayConfig(**{**self.config.__dict__, "workset": tuple(selected)})

    def _canonical_compile_entry(self, entry: Mapping[str, Any], compile_db: pathlib.Path) -> str | None:
        raw_file = entry.get("file")
        if not isinstance(raw_file, str) or not raw_file:
            return None
        candidate = pathlib.Path(raw_file)
        if not candidate.is_absolute():
            directory = entry.get("directory")
            base = pathlib.Path(directory) if isinstance(directory, str) and directory else self.config.repo
            if not base.is_absolute():
                base = (compile_db.parent / base)
            candidate = base / candidate
        try:
            return candidate.resolve(strict=False).as_posix()
        except OSError:
            return None

    def _canonical_selected_tu(self, relative: str) -> str:
        return (self.config.repo / relative).resolve(strict=False).as_posix()

    def _build_graph_state(self) -> tuple[str, list[str], list[str], bool]:
        graph_files: list[str] = []
        for candidate in ("CMakeLists.txt", "CMakeCache.txt"):
            path = self.config.repo / candidate
            if path.is_file():
                graph_files.append(candidate)
        graph_files.extend(sorted(path.relative_to(self.config.repo).as_posix() for path in self.config.repo.rglob("*.cmake")
                                  if path.is_file() and ".git" not in path.parts))
        compile_db = self.config.build_dir / "compile_commands.json" if self.config.build_dir else None
        entries: list[str] = []
        if compile_db and compile_db.is_file():
            try:
                raw = json.loads(compile_db.read_text(encoding="utf-8"))
                entries = [canonical for item in raw if isinstance(item, dict)
                           for canonical in [self._canonical_compile_entry(item, compile_db)]
                           if canonical] if isinstance(raw, list) else []
            except (OSError, json.JSONDecodeError):
                entries = []
        payload = []
        newest_graph_mtime = 0
        for relative in graph_files:
            path = self.config.repo / relative
            payload.append((relative, _sha256(path.read_bytes())))
            newest_graph_mtime = max(newest_graph_mtime, path.stat().st_mtime_ns)
        compile_mtime = compile_db.stat().st_mtime_ns if compile_db and compile_db.is_file() else 0
        graph_stale = bool(graph_files and compile_db and compile_db.is_file() and newest_graph_mtime > compile_mtime)
        return _sha256(json.dumps(payload, sort_keys=True).encode()), graph_files, entries, graph_stale

    def _refresh_build_if_needed(self) -> None:
        if not self.config.profile.startswith("cpp"):
            return
        signature, _graph_files, entries, graph_stale = self._build_graph_state()
        evidence_key = str((self.config.repo, self.config.build_dir))
        previous_signature = _BUILD_GRAPH_EVIDENCE.get(evidence_key)
        graph_changed = previous_signature is not None and previous_signature != signature
        _BUILD_GRAPH_EVIDENCE[evidence_key] = signature
        compile_db = self.config.build_dir / "compile_commands.json" if self.config.build_dir else None
        selected_cpp = [item for item in self.config.workset if pathlib.Path(item).suffix.lower() in {".c", ".cc", ".cpp", ".cxx"}]
        # A plain source checkout without a configured build graph is still a
        # valid semantic-provider configuration. There is no compile database
        # refresh claim to make until the user supplies a build directory,
        # CMake project, or explicit bounded route.
        if (compile_db is None and not (self.config.repo / "CMakeLists.txt").is_file()
                and not self.config.build_refresh_command):
            self._build_refresh = {"status": "NOT_NEEDED", "command": None}
            self._build_graph_signature = signature
            return
        absent = bool(selected_cpp) and (not entries or any(
            self._canonical_selected_tu(item) not in entries for item in selected_cpp))
        changed = graph_stale or graph_changed or (
            self._build_graph_signature is not None and signature != self._build_graph_signature)
        self._build_graph_signature = signature
        if not absent and not changed:
            if self._build_refresh.get("status") not in {"READY", "FAILED"}:
                self._build_refresh = {"status": "NOT_NEEDED", "command": None}
            return
        if not self.config.auto_refresh_build and not self.config.build_refresh_command:
            self._build_refresh = {"status": "NOT_CONFIGURED", "command": None,
                                   "reason": "COMPILE_COMMANDS_REFRESH_REQUIRED" if absent or changed else None}
            return
        if self.config.build_refresh_command:
            command = list(self.config.build_refresh_command)
        elif self.config.build_dir and (self.config.repo / "CMakeLists.txt").is_file():
            command = ["cmake", "-S", str(self.config.repo), "-B", str(self.config.build_dir),
                       "-DCMAKE_EXPORT_COMPILE_COMMANDS=ON"]
        else:
            self._build_refresh = {"status": "NOT_CONFIGURED", "command": None,
                                   "reason": "SAFE_CMAKE_ROUTE_UNAVAILABLE"}
            return
        timeout = int(PROFILES[self.config.profile]["timeout_sec"])
        code, output = _run(command, self.config.repo, timeout=timeout)
        self._build_refresh = {"status": "READY" if code == 0 else "FAILED", "command": command,
                               "returncode": code, "output": output}
        if code == 0:
            self._build_graph_signature, _files, refreshed_entries, _stale = self._build_graph_state()
            still_absent = bool(selected_cpp) and (not refreshed_entries or any(
                self._canonical_selected_tu(item) not in refreshed_entries for item in selected_cpp))
            if still_absent:
                self._build_refresh.update({"status": "PARTIAL",
                                            "reason": "COMPILE_COMMANDS_INCOMPLETE"})

    def _receipt(self) -> dict[str, Any]:
        identity = _content_identity(self.config.repo)
        compile_db = self.config.build_dir / "compile_commands.json" if self.config.build_dir else None
        build_inputs = {"directory": str(self.config.build_dir) if self.config.build_dir else None,
                        "configured": self.config.build_dir is not None,
                        "compile_commands": {"path": str(compile_db) if compile_db else None,
                                              "present": bool(compile_db and compile_db.is_file()),
                                              "sha256": _sha256(compile_db.read_bytes()) if compile_db and compile_db.is_file() else None}}
        build_inputs["sha256"] = _sha256(json.dumps(build_inputs, sort_keys=True).encode())
        build_inputs["refresh"] = dict(self._build_refresh)
        scope = {"profile": self.config.profile, "resources": PROFILES[self.config.profile],
                 "workset": [{"path": item, "sha256": _sha256((self.config.repo / item).read_bytes())
                              if (self.config.repo / item).is_file() else None} for item in self.config.workset]}
        providers = {"cpp": _provider(self.config.cpp_provider, self.config.provider_commands.get("cpp"), self.config.repo),
                     "python": _provider(self.config.python_provider, self.config.provider_commands.get("python"), self.config.repo)}
        backend = {"command": list(self.config.backend_command), "configured": bool(self.config.backend_command),
                   "identity": dict(self.config.backend_identity)}
        config_hash = _sha256(self.config.config_path.read_bytes()) if self.config.config_path and self.config.config_path.is_file() else None
        generation = _sha256(json.dumps({"content": identity, "build": build_inputs, "scope": scope,
                                         "backend": backend, "config": {"path": str(self.config.config_path), "sha256": config_hash}}, sort_keys=True).encode())
        return {"repo": {"path": str(self.config.repo), "head": _git(self.config.repo, "rev-parse", "HEAD"),
                          "tree": _git(self.config.repo, "rev-parse", "HEAD^{tree}"),
                          "parent": _git(self.config.repo, "rev-parse", "HEAD^"),
                          "dirty": bool(_git(self.config.repo, "status", "--porcelain")),
                          "content": identity},
                "repo_digest": identity["digest"], "build_inputs": build_inputs,
                "providers": providers, "provider_versions": {k: v.get("version") for k, v in providers.items()},
                "backend": backend, "config": {"path": str(self.config.config_path) if self.config.config_path else None, "sha256": config_hash},
                "scope_manifest": scope, "generation": generation,
                "identity_contract": {"stable": "backend compiler canonical global/member identity only",
                                       "ephemeral": "locals and lambdas are snapshot-scoped"}}

    def _result(self, status: str, reason: str, receipt: dict[str, Any], **extra: Any) -> dict[str, Any]:
        requested = list(OPERATIONS)
        proved = list(extra.get("result", {}).get("proved_families", [])) if isinstance(extra.get("result"), dict) else []
        missing = [] if status == "READY" else ["backend_handshake", "backend_query_facts"]
        fallback = None if status == "READY" else "exact_evidence"
        receipt.update({"status": status, "requested_families": requested, "proved_families": proved,
                        "facts": extra.get("result", {}).get("facts", []) if isinstance(extra.get("result"), dict) else [],
                        "missing": missing, "fallback": fallback, "resources": PROFILES[self.config.profile]})
        provenance = receipt.get("provenance")
        if provenance is None and isinstance(extra.get("result"), dict):
            provenance = extra["result"].get("provenance")
        return {"schema": SCHEMA, "version": VERSION, "status": status, "reason": reason,
                "receipt": receipt, "repo_digest": receipt["repo_digest"], "build_inputs": receipt["build_inputs"],
                "providers": receipt["providers"], "provider_versions": receipt["provider_versions"],
                "backend": receipt["backend"], "scope_manifest": receipt["scope_manifest"],
                "generation": receipt["generation"], "requested_families": requested,
                "proved_families": proved, "facts": receipt["facts"], "missing": missing,
                "fallback": fallback, "resources": PROFILES[self.config.profile],
                "identity": receipt["identity_contract"], "provenance": provenance,
                "routing_contract": {"unknown": "Semble", "known_structural": "semantic_gateway",
                                     "exact": "bounded_exact_evidence"}, **extra}

    def doctor(self, repo: str | os.PathLike[str] | None = None, profile: str | None = None) -> dict[str, Any]:
        if repo is not None or profile is not None:
            self.config = GatewayConfig(**{**self.config.__dict__, "repo": _canonical_repo(repo or self.config.repo),
                                           "profile": profile or self.config.profile})
        self._refresh_workset()
        self._refresh_build_if_needed()
        receipt = self._receipt()
        language = "python" if self.config.profile.startswith("python") else "cpp"
        required = receipt["providers"][language]
        if required["status"] != "READY":
            return self._result("PARTIAL", "LANGUAGE_PROVIDER_UNAVAILABLE", receipt, truthful=True)
        refresh_status = self._build_refresh.get("status")
        if refresh_status in {"FAILED", "PARTIAL", "NOT_CONFIGURED"}:
            reason = {"FAILED": "COMPILE_COMMANDS_REFRESH_FAILED",
                      "PARTIAL": "COMPILE_COMMANDS_INCOMPLETE",
                      "NOT_CONFIGURED": "COMPILE_COMMANDS_REFRESH_NOT_CONFIGURED"}[refresh_status]
            return self._result("PARTIAL", reason, receipt, truthful=True)
        client = BackendClient(self.config)
        try:
            handshake = client.start()
            facts = client.inspect("resolve_symbol", self.config.known_answer_symbol, language)
            result = {"facts": facts.get("facts", facts), "proved_families": facts.get("proved_families", ["resolve_symbol"]),
                      "provenance": facts.get("provenance", {"backend": "@samchon/graph"}), "handshake": handshake}
            return self._result("READY", "BACKEND_HANDSHAKE_AND_KNOWN_ANSWER", receipt, result=result, truthful=True)
        except GatewayError as exc:
            return self._result("PARTIAL", str(exc), receipt, truthful=True)
        finally:
            client.close()

    def sync(self, repo: str | os.PathLike[str] | None = None, scope: Mapping[str, Any] | None = None,
             profile: str | None = None) -> dict[str, Any]:
        workset = tuple(scope.get("workset", self.config.workset)) if scope else self.config.workset
        self.config = GatewayConfig(**{**self.config.__dict__, "repo": _canonical_repo(repo or self.config.repo),
                                       "profile": profile or self.config.profile, "workset": workset})
        result = self.doctor()
        result["operation"] = "sync"
        snapshot_id = "sgw-" + result["generation"]
        _SNAPSHOTS[snapshot_id] = self
        result["snapshot_id"] = snapshot_id
        return result

    def query(self, snapshot_id: str, operation: str, symbol: str = "", language: str = "cpp") -> dict[str, Any]:
        if operation not in OPERATIONS:
            raise GatewayError("unsupported operation:" + operation)
        if not symbol.strip():
            raise GatewayError("symbol is required")
        gateway = _SNAPSHOTS.get(snapshot_id)
        if not gateway:
            return self._result("NOT_READY", "SNAPSHOT_NOT_FOUND", self._receipt(), snapshot_id=snapshot_id,
                                operation=operation, query={"symbol": symbol, "language": language}, result=None)
        if gateway.config.repo != self.config.repo:
            return self._result("STALE", "SNAPSHOT_REPOSITORY_MISMATCH", self._receipt(),
                                snapshot_id=snapshot_id, operation=operation,
                                query={"symbol": symbol, "language": language}, result=None,
                                requested_repo=str(self.config.repo))
        expected_profile = "python_resident" if language == "python" else "cpp_resident"
        if gateway.config.profile != expected_profile:
            return gateway._result("STALE", "SNAPSHOT_LANGUAGE_MISMATCH", gateway._receipt(),
                                   snapshot_id=snapshot_id, operation=operation,
                                   query={"symbol": symbol, "language": language}, result=None)
        gateway._refresh_workset()
        gateway._refresh_build_if_needed()
        receipt = gateway._receipt()
        refresh_status = gateway._build_refresh.get("status")
        if refresh_status in {"FAILED", "PARTIAL", "NOT_CONFIGURED"}:
            reason = {"FAILED": "COMPILE_COMMANDS_REFRESH_FAILED",
                      "PARTIAL": "COMPILE_COMMANDS_INCOMPLETE",
                      "NOT_CONFIGURED": "COMPILE_COMMANDS_REFRESH_NOT_CONFIGURED"}[refresh_status]
            return gateway._result("PARTIAL", reason, receipt, snapshot_id=snapshot_id,
                                   operation=operation, query={"symbol": symbol, "language": language},
                                   result=None, truthful=True)
        if snapshot_id != "sgw-" + receipt["generation"]:
            return gateway._result("STALE", "SNAPSHOT_IDENTITY_CHANGED", receipt, snapshot_id=snapshot_id,
                                   operation=operation, query={"symbol": symbol, "language": language}, result=None)
        required = receipt["providers"]["python" if language == "python" else "cpp"]
        if required["status"] != "READY":
            return gateway._result("NOT_READY", "LANGUAGE_PROVIDER_UNAVAILABLE", receipt, snapshot_id=snapshot_id,
                                   operation=operation, query={"symbol": symbol, "language": language}, result=None)
        client = BackendClient(gateway.config)
        try:
            handshake = client.start()
            facts = client.inspect(operation, symbol, language)
            result = {"facts": facts.get("facts", facts), "proved_families": facts.get("proved_families", [operation]),
                      "provenance": facts.get("provenance", {"backend": "@samchon/graph"})}
            return gateway._result("READY", "BACKEND_FACTS", receipt, snapshot_id=snapshot_id,
                                   operation=operation, query={"symbol": symbol, "language": language},
                                   result=result, handshake=handshake, truthfully_proved=True)
        except GatewayError as exc:
            return gateway._result("PARTIAL", str(exc), receipt, snapshot_id=snapshot_id,
                                   operation=operation, query={"symbol": symbol, "language": language}, result=None)
        finally:
            client.close()

    def close(self) -> dict[str, Any]:
        self._closed = True
        return {"schema": SCHEMA, "version": VERSION, "status": "READY", "operation": "close", "closed": True}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Compiler-derived semantic gateway")
    parser.add_argument("operation", choices=("doctor", "sync", "close", *OPERATIONS))
    parser.add_argument("--repo", default="."); parser.add_argument("--config"); parser.add_argument("--symbol")
    parser.add_argument("--snapshot-id"); parser.add_argument("--language", default="cpp")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        gateway = Gateway(load_config(args.config, args.repo, args.language))
        if args.operation == "doctor": result = gateway.doctor()
        elif args.operation == "sync": result = gateway.sync()
        elif args.operation == "close": result = gateway.close()
        else: result = gateway.query(args.snapshot_id or gateway.sync()["snapshot_id"], args.operation, args.symbol or "", args.language)
    except (GatewayError, OSError) as exc:
        result = {"schema": SCHEMA, "version": VERSION, "status": "NOT_READY", "reason": type(exc).__name__, "message": str(exc), "truthful": True}
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0 if result.get("status") in {"READY", "PARTIAL"} else 2


def doctor(repo: str | os.PathLike[str], profile: str = "cpp_resident") -> dict[str, Any]:
    return Gateway(load_config(None, repo)).doctor(profile=profile)


def sync(repo: str | os.PathLike[str], scope: Mapping[str, Any] | None = None, profile: str = "cpp_resident") -> dict[str, Any]:
    return Gateway(load_config(None, repo)).sync(scope=scope, profile=profile)


def query(snapshot_id: str, operation: str, symbol: str = "", language: str = "cpp") -> dict[str, Any]:
    gateway = _SNAPSHOTS.get(snapshot_id)
    if not gateway: raise GatewayError("snapshot not found:" + snapshot_id)
    return gateway.query(snapshot_id, operation, symbol, language)


def close(repo: str | os.PathLike[str]) -> dict[str, Any]:
    root = _canonical_repo(repo)
    for snapshot_id, gateway in list(_SNAPSHOTS.items()):
        if gateway.config.repo == root: del _SNAPSHOTS[snapshot_id]
    return Gateway(load_config(None, root)).close()


if __name__ == "__main__":
    raise SystemExit(main())

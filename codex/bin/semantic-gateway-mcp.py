#!/usr/bin/env python3
"""MCP stdio server for the semantic gateway (JSON-RPC 2.0 over newline IO)."""

from __future__ import annotations

import json
import argparse
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

DEFAULT_CONFIG: str | None = None
SHIM_VERSION = "21.3.0"
IMPLEMENTATION = Path(__file__).resolve().parents[1] / "semantic_gateway" / "gateway.py"


PROTOCOL_VERSION = "2025-06-18"
TOOL = {
    "name": "inspect_semantic_graph",
    "description": "Inspect compiler-derived C++/Python graph facts via pinned @samchon/graph.",
    "inputSchema": {
        "type": "object",
        "required": ["repo", "operation", "symbol"],
        "properties": {
            "repo": {"type": "string"},
            "operation": {"type": "string", "enum": [
                "resolve_symbol", "definition", "declaration", "references", "callers",
                "callees", "inheritance", "type_relations", "impact"]},
            "symbol": {"type": "string"}, "language": {"type": "string", "default": "cpp"},
            "target_paths": {"type": "array", "items": {"type": "string"}},
            "profile": {"type": "string", "default": "cpp_resident"},
            "config": {"type": "string"}, "snapshot_id": {"type": "string"},
        },
    },
}


def _response(request_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def _call_tool(arguments: dict[str, Any]) -> dict[str, Any]:
    repo = arguments.get("repo")
    if not isinstance(repo, str):
        raise GatewayError("repo is required")
    if not isinstance(arguments.get("operation"), str) or not isinstance(arguments.get("symbol", ""), str):
        raise ValueError("operation and symbol are required")
    request = dict(arguments)
    request["repo"] = repo
    request["config"] = arguments.get("config") or DEFAULT_CONFIG
    # The shim deliberately imports no gateway implementation. Each call
    # executes the current installed file, making an upgrade effective while
    # the long-lived MCP stdio process remains alive.
    try:
        completed = subprocess.run([sys.executable, str(IMPLEMENTATION), "--mcp-call"],
                                   input=json.dumps(request), text=True, capture_output=True,
                                   timeout=55.0, check=False)
    except subprocess.TimeoutExpired:
        result = {"schema": "semantic-gateway.v1", "version": SHIM_VERSION,
                  "status": "SEMANTIC_CAPABILITY_BLOCKED", "reason": "SEMANTIC_CAPABILITY_BLOCKED",
                  "facts": [], "proved_families": [], "usage_allowed": False,
                  "dependent_only": True, "truthful": True}
    else:
        try:
            result = json.loads(completed.stdout.strip().splitlines()[-1])
        except (IndexError, json.JSONDecodeError):
            result = {"schema": "semantic-gateway.v1", "version": SHIM_VERSION,
                      "status": "SEMANTIC_CAPABILITY_BLOCKED", "reason": "IMPLEMENTATION_INVALID_RESPONSE",
                      "facts": [], "proved_families": [], "usage_allowed": False,
                      "dependent_only": True, "truthful": True}
    if result.get("version") != SHIM_VERSION:
        result = {"schema": "semantic-gateway.v1", "version": SHIM_VERSION,
                  "status": "SEMANTIC_CAPABILITY_BLOCKED", "reason": "SEMANTIC_VERSION_MISMATCH",
                  "facts": [], "proved_families": [], "usage_allowed": False,
                  "dependent_only": True, "truthful": True}
    return {"content": [{"type": "text", "text": json.dumps(result, sort_keys=True, separators=(",", ":"))}],
            "structuredContent": result, "isError": result.get("status") != "READY"}


def main() -> int:
    global DEFAULT_CONFIG
    parser = argparse.ArgumentParser(description="Codex semantic gateway MCP stdio adapter")
    parser.add_argument("--config", help="default semantic gateway config for tools/call")
    DEFAULT_CONFIG = parser.parse_args().config
    initialized = False
    for raw in sys.stdin:
        try:
            request = json.loads(raw)
            if not isinstance(request, dict):
                raise ValueError("request must be an object")
            method = request.get("method")
            request_id = request.get("id")
            # MCP notifications have no id and must not receive a response.
            if method == "notifications/initialized":
                initialized = True
                continue
            if method == "initialize":
                initialized = True
                result = {"protocolVersion": PROTOCOL_VERSION,
                          "capabilities": {"tools": {"listChanged": False}},
                          "serverInfo": {"name": "codex-semantic-gateway", "version": SHIM_VERSION}}
                print(json.dumps(_response(request_id, result), separators=(",", ":")), flush=True)
            elif method == "tools/list":
                if not initialized:
                    raise GatewayError("initialize is required")
                print(json.dumps(_response(request_id, {"tools": [TOOL]}), separators=(",", ":")), flush=True)
            elif method == "tools/call":
                if not initialized:
                    raise GatewayError("initialize is required")
                params = request.get("params", {})
                if not isinstance(params, dict) or params.get("name") != TOOL["name"]:
                    raise GatewayError("inspect_semantic_graph is the only supported tool")
                arguments = params.get("arguments", {})
                if not isinstance(arguments, dict):
                    raise GatewayError("tool arguments must be an object")
                print(json.dumps(_response(request_id, _call_tool(arguments)), separators=(",", ":")), flush=True)
            elif method == "ping":
                print(json.dumps(_response(request_id, {}), separators=(",", ":")), flush=True)
            else:
                if request_id is not None:
                    print(json.dumps(_error(request_id, -32601, "method not found"), separators=(",", ":")), flush=True)
        except Exception as exc:
            if isinstance(locals().get("request"), dict) and request.get("id") is not None:
                print(json.dumps(_error(request.get("id"), -32000, str(exc)), separators=(",", ":")), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

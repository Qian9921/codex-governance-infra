#!/usr/bin/env python3
"""MCP stdio server for the semantic gateway (JSON-RPC 2.0 over newline IO)."""

from __future__ import annotations

import json
import argparse
import sys
from pathlib import Path
from typing import Any

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))

from semantic_gateway.gateway import GatewayError, load_config  # noqa: E402
from semantic_gateway.gateway import Gateway  # noqa: E402


DEFAULT_CONFIG: str | None = None


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
    profile = arguments.get("profile")
    operation = arguments.get("operation")
    symbol = arguments.get("symbol", "")
    language = arguments.get("language", "cpp")
    if not isinstance(operation, str) or not isinstance(symbol, str) or not isinstance(language, str):
        raise GatewayError("operation, symbol, and language are required")
    gateway = Gateway(load_config(arguments.get("config") or DEFAULT_CONFIG, repo, language))
    if profile is None:
        profile = gateway.config.profile
    snapshot_id = arguments.get("snapshot_id")
    if not isinstance(snapshot_id, str):
        snapshot_id = gateway.sync(profile=profile).get("snapshot_id")
    result = gateway.query(snapshot_id, operation, symbol, language)
    return {"content": [{"type": "text", "text": json.dumps(result, sort_keys=True, separators=(",", ":"))}],
            "structuredContent": result, "isError": result.get("status") not in {"READY", "PARTIAL"}}


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
                          "serverInfo": {"name": "codex-semantic-gateway", "version": "21.0.0"}}
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

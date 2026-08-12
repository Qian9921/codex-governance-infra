"""Compiler-backed semantic gateway for the Codex governance overlay.

The gateway is deliberately an orchestration surface: clangd and Pyright own
language semantics, while this package owns identity, routing, bounded resource
profiles, and truthful status receipts.
"""

from .gateway import Gateway, GatewayConfig, GatewayError, load_config

__all__ = ["Gateway", "GatewayConfig", "GatewayError", "load_config"]

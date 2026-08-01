# Deployment

Use an isolated `CODEX_HOME` for verification.
`install-governance.py --dry-run` prints the allowlisted plan; a real install
copies only `codex/` with atomic temp replacement and a
timestamp-independent sibling backup. `--rollback` restores the backup. Live
global Codex-home deployment requires a separately authorized governance lane
and exact manifest/hash review.

The package installs routing policy and validators; it does not vendor or
silently install CodeGraph, Semble, `rtk`, or `rg`. Before acceptance, the host
adapter supplies `codex.v16.tool_routing.tooling_doctor` with real capability
observations. Local CLI presence can be probed read-only; Semble is normally an
MCP capability and remains `unknown` until the orchestrator provides a current
observation. CodeGraph index build/sync is a separate, project-local authorized
mutation. Unknown or degraded health stays visible and fallback requires a real
reason code plus evidence reference.

# Deployment

Use an isolated `CODEX_HOME` for verification first.
`install-governance.py --dry-run` prints the allowlisted plan; a real install
is a manifest-bound managed overlay. It atomically replaces only package-owned
files, leaves every unrelated destination path untouched, and stores prior
managed files under `.governance-v16-backup`. `--rollback` restores those files
and removes managed files that did not previously exist. Live global Codex-home
deployment requires the exact manifest/hash review and the applicable
authorization lane.

Upgrade backup publication is generation-safe: the current rollback generation
is atomically renamed to `.governance-v16-backup.previous` before the new one is
published. The previous generation remains recoverable until every managed
replacement completes. A later installer invocation detects and either
finishes or rolls back an interrupted rotation before starting new work.

The package installs routing policy and validators; it does not vendor or
silently install CodeGraph, Semble, `rtk`, or `rg`. Before acceptance, the host
adapter supplies `codex.v16.tool_routing.tooling_doctor` with real capability
observations. Local CLI presence can be probed read-only; Semble is normally an
MCP capability and remains `unknown` until the orchestrator provides a current
observation. CodeGraph index build/sync is a separate, project-local authorized
mutation. Unknown or degraded health stays visible and fallback requires a real
reason code plus evidence reference.

# Deployment

Use an isolated `CODEX_HOME` for verification first.
`install-governance.py --dry-run` prints the allowlisted plan; a real install
is a manifest-bound managed overlay. It atomically replaces only package-owned
files, leaves every unrelated destination path untouched, and stores prior
managed files under `.governance-v16-backup`. `--rollback` restores those files
and removes managed files that did not previously exist. Live global Codex-home
deployment requires the exact manifest/hash review and the applicable
authorization lane.

The V19.1 personal overlay has two disjoint destinations: normal package files
under the selected `CODEX_HOME`, and V19 Skills under the sibling
`.agents/skills` root required by current Codex discovery. One backup generation
inside `CODEX_HOME` covers both roots. A custom `--agents-home` must be supplied
again for rollback; V19 binds both resolved roots into private backup metadata
and fails closed before recovery, upgrade, or rollback on root drift. Empty
installer-created skill directories are pruned, while unrelated `.agents` files
remain untouched. The managed `.agents/skills` root must be a physical
directory, not a symlink.

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

## V19 native model routing overlay

V19 keeps the adaptive policy separate from the retained V16 strict evidence
engine. Reversible machine-local model routing is a `STANDARD` operation. The
adaptive role contract also exposes bounded `TERRA_REPLAN` and `TERRA_TRIAGE`
bridges: R0/R1 advisory slices return directly to Luna and cannot review, merge,
spawn, listen, retry, or issue a final verdict. `TERRA_CONTINUITY` remains a
distinct Luna-unavailable fallback.

`codex/bin/refresh-model-catalog.py` discovers the current catalog through the
installed Codex binary in an isolated temporary home, normalizes only the
allowlisted Luna/Spark multi-agent backend selector, validates the required
Luna route, and atomically publishes a private catalog. It never copies or
prints authentication content. A valid prior catalog remains available if a
later network refresh fails.

`scripts/configure-model-routing.py` adds the supported top-level
`model_catalog_json` setting and a user-systemd `ExecStartPre` drop-in. It
backs up the preexisting config and drop-in with hashes, is idempotent, and has
an explicit rollback. The drop-in resolves the stable `standalone/current`
binary path supplied at installation, so a Codex package update is picked up on
the next app-server start.

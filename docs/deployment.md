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
Luna route, and atomically publishes a private catalog. POSIX hosts symlink
`auth.json`; Windows temporarily copies it into the private temporary home when
unprivileged symlink creation is unavailable. The temporary home is removed,
authentication content is never printed, and a valid prior catalog remains
available if a later network refresh fails.

`scripts/configure-model-routing.py` adds the supported top-level
`model_catalog_json` setting and, only when `--systemd-user-dir` is supplied, a
user-systemd `ExecStartPre` drop-in. Omitting that option selects the portable
on-demand backend: no launchd plist, Windows Task Scheduler task, or other
startup file is created. This is the supported mode for macOS and Windows 11,
and for Linux installations without a user-systemd app-server.

The script backs up the preexisting config, catalog, and (when enabled)
systemd drop-in with hashes, is idempotent, and has an explicit rollback. The
catalog and config writes use atomic replacement. The optional drop-in resolves
the stable `standalone/current` binary path supplied at installation, so a
Codex package update is picked up on the next app-server start. The script does
not restart any process: after on-demand configuration, fully quit and restart
the affected Codex CLI/Desktop/app server manually; after systemd
configuration, run `systemctl --user daemon-reload` and restart the service.

Use the same mode for rollback. On-demand configuration and rollback omit
`--systemd-user-dir`; a systemd rollback passes the same user-systemd
directory. The state metadata rejects changing modes during an existing install
so rollback cannot remove unrelated startup state.

Before configuration, record the current client version and live catalog:

```bash
codex --version
codex debug models
```

On Windows PowerShell, resolve the supported command first and invoke it with
the call operator:

```powershell
$ACTIVE_CODEX_BIN = (Get-Command codex -ErrorAction Stop).Source
& $ACTIVE_CODEX_BIN --version
& $ACTIVE_CODEX_BIN debug models
```

The portable setup does not guarantee that `gpt-5.6-luna` is currently exposed
on every Codex surface. Verify (1) the live model catalog, (2) the generated
`model-catalogs/multi-agent-v2.json` selection, and (3) an actual native
`spawn_agent` using `agent_type="luna_execution"`, `task_name`, and `message`.
The installed role file pins `gpt-5.6-luna`; no redundant model override is
needed. If the live surface rejects it, report the limitation and do not
substitute another model family.

The filesystem tests simulate Linux, macOS, and Windows branches on the current
host. This verification did not run on a native Windows host, so native Windows
filesystem behavior remains an explicit limitation.

Repeat the same version and live-catalog commands after the manual client
restart or Linux systemd restart. Version, live catalog, generated overlay, and
actual native spawn are separate evidence layers; none substitutes for the
next layer.

Rollback records per-target progress and retains its state until config,
drop-in, and catalog restoration all validate. A local filesystem fault can be
recovered by rerunning the same rollback command; already-restored targets are
validated and skipped.

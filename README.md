# Codex Governance Infra V21

[简体中文](README.zh-CN.md) · English

A Codex-only V21 policy for researcher-engineers who need fast implementation,
trustworthy evidence, convergent review, clean code, and durable knowledge—without
turning every task into a release ceremony.

## What it does

```text
Outcome -> Reuse scan -> Luna executes (optional bounded Terra bridge) -> Affected evidence
        -> Initial Sol review -> at most one delta -> configured PR trace -> Knowledge
```

- **Personal progressive infrastructure:** a bounded always-loaded kernel,
  on-demand skills, single-role subagents, compact hooks, and command rules.
- **Adaptive profiles:** `QUICK` and `STANDARD`; V21 balances risk, recovery,
  time, evidence, complexity, and communication cost.
- **Code health:** project rules first, official Google guidance as the default,
  and a `REUSE|EXTEND|NEW` decision before meaningful new abstractions.
- **Three daily lanes:** Semble discovery; the compiler-derived semantic gateway
  for known C++/Python semantics; and bounded exact source/Git/compiler/build/
  test/benchmark evidence. Legacy V16 tools remain outside the daily lanes.
- **Large-code evidence contract:** `code-mission-tool-index-policy.v1` binds
  exact repo/worktree/revision identity and healthy Semble plus compiler-gateway
  evidence. Semble precedes development; the semantic gateway proves known
  structure/impact and exact evidence proves source/Git/compiler/build/test/
  benchmark facts. Only pure non-code or exact-mechanical work may use `N/A`
  with a reason. Evidence is carried by canonical privacy-safe refs and hashes,
  not booleans; no per-turn/count quota is imposed.
- **Convergent review:** one initial independent review and at most one delta
  review; a third round requires explicit replan.
- **Safe installation:** manifest-bound, dry-run capable, atomic, backed up,
  hash-verified, and rollback-capable across the personal `.codex` and `.agents`
  roots.

The package installs personal configuration only. See the
[personal infrastructure and context budget](docs/personal-infra.md).

## Compiler-derived semantic gateway

The V21 gateway is a real CLI/MCP-compatible surface, not a documentation
placeholder. Use `codex/bin/semantic-gateway.py doctor --repo .` to inspect
repository/build/provider identity, `sync` to create a snapshot, and one of
`resolve_symbol`, `definition`, `declaration`, `references`, `callers`,
`callees`, `inheritance`, `type_relations`, or `impact` with
`--snapshot-id`. The normalized receipt reports `READY`, `PARTIAL`, `STALE`,
or `NOT_READY`, compiler/provider hashes, scope/resource limits, generation,
and a named bounded exact-evidence fallback. It never invents symbols when a
compiler protocol is unavailable.

The pinned upstream identity is `@samchon/graph` HEAD
`95e20c9540e85fef542466172484229356d3d0d8`, tree
`e9ce033e380d77265c601579e436218502a6ccbd`. Resident C++ is limited to 64
translation units, concurrency 2, 4 CPUs, 4 GiB, and 180 seconds; offline C++
is 4 CPUs, 8 GiB, and 15 minutes; resident Python is 4 CPUs, 2.5 GiB, and
180 seconds. `scripts/bootstrap.py` is the one-command clone-to-ready entrypoint;
it runs governance and semantic installation, registers MCP, verifies host
prerequisites, and prints an executable platform package route for missing
clangd/Node/pnpm without silently mutating system state. The lower-level
`scripts/install-semantic-tools.py` remains a separate idempotent,
dry-run-capable installer/doctor for the pinned source checkout and host
provider observations. Missing tools are truthful PARTIAL/NOT_READY states,
not fake compiler proof.

This repository supports Codex only. It does not claim compatibility with
Claude Code, Kimi Code, Zcode, or other agent runtimes.

## Daily profiles

| Profile | Use | Evidence and review | Hooks |
|---|---|---|---|
| `QUICK` | explanations, inventory, docs, reversible mechanics | targeted; formal review optional | advisory |
| `STANDARD` | ordinary reversible development and research engineering | affected-first; one initial plus at most one delta review | advisory |
Adaptive mode is the default. The retained V16 compatibility engine is an
advanced path and activates only after an explicit user request; it is not part
of daily installation or routing.
# V21 is the product policy. The existing `$v19-*` skill IDs and paths remain
stable compatibility APIs, and `codex/v16` remains only the backward-compatible
strict compatibility engine. Ordinary reversible installer, hook, and
model-routing repairs use the V21 `STANDARD` contract.

### V21 STANDARD contract

Freeze acceptance, rollback, time/evidence budgets, and known limitations before
execution. A blocker must map to frozen acceptance and weigh user impact,
likelihood, recoverability, repair cost, and complexity cost; a theoretical
counterexample without that mapping is `FOLLOW_UP`. Known bounded limitations
are legal completion states when documented and outside acceptance. Run only
checks that can change the decision. If budget expires, or defensive/recovery
logic exceeds the core feature or keeps introducing state, simplify and replan.

## Model roles

- Luna is the lifecycle controller, execution lead, recovery owner, and Git/CI
  operator by default. `R0`/`R1` work stays in the Luna loop without a
  mandatory Sol inner loop.
- `R2`/`R3` math, numerical, public-API, and new-algorithm work gets one short
  Sol contract gate, then returns to Luna. `R4` research interpretation is
  Sol-led when interpretation is material.
- Sol performs the fresh, read-only final review. High-risk review reads source,
  contract, and tests and adds a source-derived counterexample; stable fixes
  return to the same reviewer for at most one delta round. A third round needs
  explicit replan.
- Spark remains catalog-supported for legacy or explicitly selected contracts;
  it is disabled by this role policy and is never part of the default flow.
- Terra: explicit short-lived `TERRA_REPLAN`/`TERRA_TRIAGE` bridges for bounded
  R0/R1 advisory synthesis/triage, returning directly to Luna; continuity
  fallback remains separate and only applies when Luna is unavailable. Bridges
  cannot review, merge, spawn, listen, retry, or issue a final verdict.

Spawn task names expose the actual model family and role (`luna-execution-*`,
`spark-audit-*`). Fallback names expose the actual fallback family and never
retain a `luna-` prefix for Sol/Terra. Receipts and reports record
`requested_model`, `actual_model`, `role`, and `fallback_reason`; this telemetry
is advisory unless model identity is deliberately misrepresented.

Sol audits recovery evidence. Roles are routing defaults, not capability bans.

Nested help is allowed only when useful: Sol may delegate bounded mechanical
work to Luna, and Luna may ask Sol one narrow math/sign/shape/numerical
consultation. Child scope only narrows, depth is at most two below the
controller, and Luna/Sol ping-pong or duplicate uncertainty consultations are
rejected. A Sol consultant in the author lineage cannot be the final reviewer.
The executable policy is `codex/hooks/model_roles.py`; hooks remain advisory in
`QUICK`/`STANDARD` except identity, lease, safety, and privacy violations.

## Self-healing execution

Luna repairs a required capability through distinct, evidence-producing
strategies until it is usable **and exercised by the real dependent task
slice**. A stable no-progress strategy opens a circuit and is never repeated;
the recovery mission continues with a materially different safe strategy.
Optional failure may degrade unrelated work, but creates owned repair debt.
Required failure blocks only the claim or slice that depends on it.

`HEALTHY`, `RECOVERING`, `DEGRADED`, `EXTERNAL_WAIT`,
`USER_ACTION_REQUIRED`, and `UNRECOVERABLE` are defined in the
[toolchain contract](docs/TOOLCHAIN.md#self-healing-capability-recovery).
`UNRECOVERABLE` requires evidence that the whole permitted recovery graph is
exhausted—not that one strategy or controller budget ended. `EXTERNAL_WAIT`
uses bounded-backoff rechecks of a genuinely outside dependency.
Involve the user only for scientific/product choices, credentials or licensing,
irreversible/shared-state actions, material unapproved cost, privacy, or a
genuine external impossibility. A check-only/no-mutation result is not a user
action; normal machine repair remains Luna's work. `QUICK` and `STANDARD`
remain advisory; the retained V16 compatibility path is advanced and
explicit-user-only.

## Ten-minute setup

### 1. Clone and bootstrap (primary path)

```bash
git clone https://github.com/Qian9921/codex-governance-infra.git
cd codex-governance-infra

# Defaults are ~/.codex and ~/.codex/semantic-tools; --dry-run is safe to preview.
python3 scripts/bootstrap.py --repo "$PWD" --dry-run
python3 scripts/bootstrap.py --repo "$PWD"
```

The bootstrap installs and verifies governance plus the pinned semantic tools,
registers the MCP server, and preserves unrelated Codex state. If clangd,
Node, or pnpm is missing, it reports the exact host route without mutating the
host. To explicitly authorize that package-manager route, use
`python3 scripts/bootstrap.py --repo "$PWD" --install-system-deps`; no sudo
password is embedded or captured. Defaults can be overridden with
`--codex-home` and `--tools-home`.

### 2. Verify the installation

```bash
python3 scripts/verify-governance.py --repo .
python3 -m unittest discover -s tests -p 'test_*.py'
python3 -m unittest discover -s tests/v16 -p 'test_*.py'
```

Continue only with verifier status `GREEN` and zero test failures/errors.

### Advanced: lower-level installers

Use these commands only when composing a non-default deployment. They are the
same idempotent, reversible operations invoked by `bootstrap.py`:

```bash
python3 scripts/install-semantic-tools.py --tools-home "$HOME/.codex/semantic-tools" \
  --codex-home "$HOME/.codex" --install --register
python3 scripts/install-governance.py --source . --codex-home "$HOME/.codex"
```

### Advanced V16 compatibility pointer

The retained V16 compatibility profile may use CodeGraph, `rg`, and `rtk` only
when the user explicitly requests that compatibility path. It is not a V21
daily lane and is not required for QUICK or STANDARD work.

### 3. Inspect the managed overlay

```bash
ACTIVE_CODEX_HOME="${CODEX_HOME:-$HOME/.codex}"

python3 scripts/install-governance.py \
  --source . \
  --codex-home "$ACTIVE_CODEX_HOME" \
  --dry-run
```

### 4. Configure roles, accounts, and the agent surface

The public defaults are placeholders. Configure two distinct account labels
before generating review packets; these labels are metadata only and never
contain tokens:

```bash
export CODEX_GOV_AUTHOR_ACCOUNT="your-developer-account"
export CODEX_GOV_REVIEWER_ACCOUNT="your-reviewer-account"
```

Use the Codex hook files for Codex CLI/Desktop. For another agent runtime,
reuse the documented policy concepts and invoke the repository verifier, but do
not copy the Codex hook overlay blindly; this package does not claim native
compatibility with Claude Code or other agents. The retained compatibility
configuration is portable and contains no provider, credential, or
machine-specific setting.

Luna is the default execution/recovery lead. Sol supplies the short contract
gate for R2/R3 work and the independent review; Terra bridges are explicit
bounded R0/R1 advisory handoffs that return to Luna, while continuity fallback
is only for a genuinely unavailable Luna; Spark is disabled in the default flow.
Route unknown semantics to Semble, known structural semantics to the compiler
semantic gateway, and exact source/Git/compiler/build/test/benchmark facts to
the bounded exact-evidence lane. Legacy V16 tools are outside the daily flow.

### 5. Verify hooks and run the first task

```bash
python3 scripts/toolchain-doctor.py --repo .
python3 scripts/verify-governance.py --repo .
export CODEX_GOVERNANCE_MODE=adaptive
```

Start with a QUICK explanation or STANDARD implementation task. If installation
fails, preserve the dry-run output, fix the
reported prerequisite, and rerun the verifier; the rollback command below is
safe and scoped to the managed overlay.

The overlay owns only manifest-listed paths in the selected personal `.codex`
root and the stable V19 compatibility skills under the sibling `.agents/skills`
root. It preserves configuration,
credentials, plugins, memories, sessions, connections, caches, receipts, and
all unrelated files.

### 6. Install

```bash
python3 scripts/install-governance.py \
  --source . \
  --codex-home "$ACTIVE_CODEX_HOME"
```

### 7. Configure native multi-agent routing for Luna and Spark

Codex may show Luna and Spark in its general catalog while their upstream
`multi_agent_version` metadata does not yet expose them to native V2
`spawn_agent`. The routing script refreshes the private catalog, adds the
top-level `model_catalog_json` setting, and optionally installs a Linux
user-systemd `ExecStartPre` drop-in. It never claims that a model is available:
verify the live catalog and then exercise the real native spawn surface.
The refresher uses an isolated temporary Codex home; POSIX hosts symlink
`auth.json`, while Windows temporarily copies it there because unprivileged
symlink creation is commonly unavailable. The temporary home is removed and
authentication content is never printed.

Before configuration, record the client version and live catalog separately.
On Linux/macOS:

```bash
codex --version
codex debug models
```

On Windows PowerShell:

```powershell
$ACTIVE_CODEX_BIN = (Get-Command codex -ErrorAction Stop).Source
& $ACTIVE_CODEX_BIN --version
& $ACTIVE_CODEX_BIN debug models
```

On-demand mode is portable and does not create launchd, Windows Task Scheduler,
or other startup files. Use it on macOS, Windows 11, Linux without an app-server
service, or whenever you prefer to restart the client yourself:

```bash
python3 scripts/configure-model-routing.py \
  --codex-home "$ACTIVE_CODEX_HOME" \
  --codex-bin "$(command -v codex)"
```

On Windows PowerShell, discover the installed binary instead of assuming an
installation directory:

```powershell
$ACTIVE_CODEX_HOME = if ($env:CODEX_HOME) { $env:CODEX_HOME } else { Join-Path $env:USERPROFILE ".codex" }
$ACTIVE_CODEX_BIN = (Get-Command codex -ErrorAction Stop).Source
python scripts/configure-model-routing.py `
  --codex-home $ACTIVE_CODEX_HOME `
  --codex-bin $ACTIVE_CODEX_BIN
```

Linux user-systemd is optional. Supply the directory only when the app-server
is actually managed by user-systemd; the script does not invoke `systemctl`:

```bash
USER_SYSTEMD_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
python3 scripts/configure-model-routing.py \
  --codex-home "$ACTIVE_CODEX_HOME" \
  --codex-bin "$(command -v codex)" \
  --systemd-user-dir "$USER_SYSTEMD_DIR"
systemctl --user daemon-reload
systemctl --user restart codex-app-server.service
```

Without systemd, fully quit and restart the affected Codex CLI/Desktop/app
server after configuration. The script never restarts a process. If an
app-server needs a network wrapper, set `EXEC_WRAPPER` to a repo-local
executable and add `--exec-wrapper "$EXEC_WRAPPER"` (Linux systemd mode only).

Verify in three layers: confirm the current model catalog exposes Luna,
confirm `model-catalogs/multi-agent-v2.json` was generated and selected, then
run an actual native `spawn_agent` using the installed `luna_execution` agent
type (whose role file pins `gpt-5.6-luna`). The current collaboration schema
uses `agent_type`, `task_name`, and `message`:

```text
spawn_agent(
  agent_type="luna_execution",
  task_name="routing_smoke_check",
  message="Run the bounded routing smoke check and return one exact status line."
)
```

The current catalog or native surface may still reject Luna; retain that result
as a capability limitation rather than substituting Sol or Terra.
The platform filesystem tests simulate Linux, macOS, and Windows branches on
the current host; this verification did not run on a native Windows host.

After the manual client restart, or after the Linux systemd daemon reload and
service restart, run the same version and live-catalog commands again. A version
match confirms the restarted client; a changed `debug models` response confirms
the live catalog, while only the generated overlay and actual native spawn prove
the configured route.

Model-routing rollback uses the same platform mode used for configuration. For
on-demand mode, omit `--systemd-user-dir`; for Linux systemd mode, pass the
same directory:

```bash
python3 scripts/configure-model-routing.py \
  --codex-home "$ACTIVE_CODEX_HOME" \
  --codex-bin "$(command -v codex)" \
  --rollback
```

Rollback checkpoints config, drop-in, and catalog restoration and keeps its
state until all three targets validate. If a local filesystem fault interrupts
rollback, rerun the same command to resume safely.

Ensure `[features] hooks = true`, restart the affected Codex CLI/Desktop/app
server, open `/hooks`, and trust the new exact hook hash.

Rollback:

```bash
python3 scripts/install-governance.py \
  --source . \
  --codex-home "$ACTIVE_CODEX_HOME" \
  --rollback
```

## Daily use

Tell Codex the desired result. The installed policy then:

1. selects a mission slice and V21 profile;
2. discovers existing ownership before creating abstractions;
3. routes execution and only relevant tools;
4. runs affected evidence, including representative synthetic/real data when
   the capability requires both;
5. performs one initial independent review and at most one delta-only closure;
6. leaves author/reviewer history and reusable knowledge.

Replies lead with the conclusion and default to at most three short points or
paragraphs. During long work, report only a new milestone, blocker, or scope
change; request detail when the compact result is insufficient.

Tool calls are not a checklist. Verify Semble or the semantic gateway before
relying on its answer; use exact evidence for source/Git/compiler/build/test/
benchmark facts. A missing semantic provider reports a named exact-evidence
fallback and does not block unrelated STANDARD work.

## Code standards

Repository architecture and formatter/linter/compiler configuration are
authoritative. Applicable official Google language guidance is the default
baseline. Meaningful new abstractions require a short `REUSE`, `EXTEND`, or
`NEW` decision. Prefer composition; do not create parallel frameworks,
speculative generic layers, or god classes.

See [Code health](docs/code-health.md).

## GitHub responsibilities

- your-developer-account authors, pushes, opens the PR, and answers findings.
- your-reviewer-account independently comments, reviews the exact head, approves, and
  merges with expected-head protection.

The PR retains objective, evidence summary, findings, dispositions, limitations,
and final verdict. It never contains raw prompts, sessions, credentials, private
paths, or private data.

## Validation

During development, run the smallest affected tests. Before review:

```bash
python3 scripts/verify-governance.py --repo .
python3 scripts/presubmit.py --repo .
git diff --check
```

Tracked additions, deletions, and content changes require a manifest update:

```bash
python3 scripts/update-manifest.py
```

## Documents

- [Architecture](docs/architecture.md)
- [Code health and Google baseline](docs/code-health.md)
- [Review workflow](docs/review-workflow.md)
- [Toolchain details](docs/TOOLCHAIN.md)
- [Deployment](docs/deployment.md)
- [Privacy threat model](docs/privacy-threat-model.md)

## Privacy boundary

Never commit credentials, authentication state, Codex sessions, prompts,
transcripts, memories, receipts, plugin/connection/model caches, private paths,
or private repository/data content. See [SECURITY.md](SECURITY.md) and
[PRIVACY.md](PRIVACY.md).

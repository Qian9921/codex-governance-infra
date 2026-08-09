# Codex Governance Infra V19

[简体中文](README.zh-CN.md) · English

A Codex-only starter for researcher-engineers who need fast implementation,
trustworthy evidence, convergent review, clean code, and durable knowledge—without
turning every task into a release ceremony.

## What it does

```text
Outcome -> Reuse scan -> Luna executes (optional bounded Terra bridge) -> Affected evidence
        -> One Sol review -> configured author/reviewer PR trace -> Knowledge
```

- **Personal progressive infrastructure:** a bounded always-loaded kernel,
  on-demand skills, single-role subagents, compact hooks, command rules, and an
  opt-in strict profile.
- **Adaptive profiles:** `QUICK`, `STANDARD`, and opt-in `STRICT`.
- **Code health:** project rules first, official Google guidance as the default,
  and a `REUSE|EXTEND|NEW` decision before meaningful new abstractions.
- **Relevant tool routing:** Semble for semantic discovery, CodeGraph for known
  structure/impact, `rg` for exact facts, and `rtk` for shell context.
- **Large-code evidence contract:** `code-mission-tool-index-policy.v1` binds
  exact repo/worktree/revision identity and healthy revision-matching Semble /
  CodeGraph evidence. Semble precedes development, CodeGraph precedes
  `CANDIDATE_READY`; only pure non-code or exact-mechanical work may use `N/A`
  with a reason. Evidence is carried by canonical privacy-safe refs and hashes,
  not booleans; candidate readiness also requires healthy, unblocked state and
  no per-turn/count quota is imposed.
- **Convergent review:** one independent reviewer; stable fixes are delta-only.
- **Safe installation:** manifest-bound, dry-run capable, atomic, backed up,
  hash-verified, and rollback-capable across the personal `.codex` and `.agents`
  roots.

The package installs personal configuration only. See the
[personal infrastructure and context budget](docs/personal-infra.md).

This repository supports Codex only. It does not claim compatibility with
Claude Code, Kimi Code, Zcode, or other agent runtimes.

## Profiles

| Profile | Use | Evidence and review | Hooks |
|---|---|---|---|
| `QUICK` | explanations, inventory, docs, reversible mechanics | targeted; formal review optional | advisory |
| `STANDARD` | normal development and research engineering | affected-first; one independent review | advisory |
| `STRICT` | security/privacy, exact math, public contracts, irreversible changes, production releases | retained V16 FAST/CANDIDATE/FINAL proof | fail-closed integrity |

Adaptive mode is the default. To run the installed hooks in strict mode, start
the relevant Codex surface with:

```bash
export CODEX_GOVERNANCE_MODE=strict
```

Strict mode is intentional, not an automatic penalty for every repository task.
# V19 is the public adaptive policy. The `codex/v16` package remains only as the
backward-compatible strict compatibility engine; ordinary reversible installer,
hook, and model-routing repairs use `STANDARD`.

## Model roles

- Luna is the lifecycle controller, execution lead, recovery owner, and Git/CI
  operator by default. `R0`/`R1` work stays in the Luna loop without a
  mandatory Sol inner loop.
- `R2`/`R3` math, numerical, public-API, and new-algorithm work gets one short
  Sol contract gate, then returns to Luna. `R4` research interpretation is
  Sol-led when interpretation is material.
- Sol performs the fresh, read-only final review. High-risk review reads source,
  contract, and tests and adds a source-derived counterexample; stable fixes
  return to the same reviewer delta-only, for at most two rounds.
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
remain advisory; V16 receipts and fail-closed gates remain explicit `STRICT`
opt-in.

## Ten-minute setup

### 1. Clone and verify

```bash
git clone https://github.com/your-org/codex-governance-infra.git
cd codex-governance-infra

python3 -m pip install --user -r requirements.txt
python3 scripts/verify-governance.py --repo .
python3 -m unittest discover -s tests -p 'test_*.py'
python3 -m unittest discover -s tests/v16 -p 'test_*.py'
```

Continue only with verifier status `GREEN` and zero test failures/errors.

### 2. Install and configure the preferred tools if missing

```bash
npm install -g @colbymchenry/codegraph
uv tool install semble
cargo install --git https://github.com/rtk-ai/rtk

codegraph install --target codex --location global --yes
semble install --agent codex --type mcp --yes
rtk init --codex --global --dry-run
rtk init --codex --global
```

Upstream projects: [CodeGraph](https://github.com/colbymchenry/codegraph),
[Semble](https://github.com/MinishLab/semble), and
[rtk](https://github.com/rtk-ai/rtk).

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
compatibility with Claude Code or other agents. The shipped strict profile is
portable and contains no provider, credential, or machine-specific setting.

Luna is the default execution/recovery lead. Sol supplies the short contract
gate for R2/R3 work and the independent review; Terra bridges are explicit
bounded R0/R1 advisory handoffs that return to Luna, while continuity fallback
is only for a genuinely unavailable Luna; Spark is disabled in the default flow.
Route unknown semantics to
Semble, known structure/impact to CodeGraph, exact text to `rg`, and shell
context through `rtk`.

### 5. Verify hooks and run the first task

```bash
python3 scripts/toolchain-doctor.py --repo .
python3 scripts/verify-governance.py --repo .
export CODEX_GOVERNANCE_MODE=adaptive   # use strict only when explicitly required
```

Start with a QUICK explanation or STANDARD implementation task. Use STRICT for
security/privacy, public-contract, irreversible, production-release, or exact
parity work. If installation fails, preserve the dry-run output, fix the
reported prerequisite, and rerun the verifier; the rollback command below is
safe and scoped to the managed overlay.

The overlay owns only manifest-listed paths in the selected personal `.codex`
root and the V19 skills under the sibling `.agents/skills` root. It preserves configuration,
credentials, plugins, memories, sessions, connections, caches, receipts, and
all unrelated files.

### 6. Install

```bash
python3 scripts/install-governance.py \
  --source . \
  --codex-home "$ACTIVE_CODEX_HOME"
```

### 7. Keep Luna and Spark available to native multi-agent V2

Codex currently advertises Luna and Spark in the general model catalog while
their upstream `multi_agent_version` metadata can exclude them from native V2
`spawn_agent`. Install the supported startup catalog overlay after the managed
files are present:

```bash
ACTIVE_CODEX_BIN="$(command -v codex)"
USER_SYSTEMD_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"

if command -v systemctl >/dev/null 2>&1 \
  && systemctl --user status codex-app-server.service >/dev/null 2>&1; then
  python3 scripts/configure-model-routing.py \
    --codex-home "$ACTIVE_CODEX_HOME" \
    --codex-bin "$ACTIVE_CODEX_BIN" \
    --systemd-user-dir "$USER_SYSTEMD_DIR"
  systemctl --user daemon-reload
  systemctl --user restart codex-app-server.service
else
  echo "No user-systemd app-server detected; keep model routing on-demand."
fi
```

The refresher uses an isolated temporary Codex home, never copies or prints
credentials, changes only the allowlisted multi-agent backend fields, publishes
atomically, and retains a last-known-good catalog. If the app-server service
requires a network wrapper, set `EXEC_WRAPPER` to a repo-local executable and add
`--exec-wrapper "$EXEC_WRAPPER"`.

Model-routing rollback:

```bash
python3 scripts/configure-model-routing.py \
  --codex-home "$ACTIVE_CODEX_HOME" \
  --codex-bin "$ACTIVE_CODEX_BIN" \
  --systemd-user-dir "$USER_SYSTEMD_DIR" \
  --rollback
```

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

1. selects a mission slice and profile;
2. discovers existing ownership before creating abstractions;
3. routes execution and only relevant tools;
4. runs affected evidence, including representative synthetic/real data when
   the capability requires both;
5. performs one independent review and delta-only closure;
6. leaves author/reviewer history and reusable knowledge.

Tool calls are not a checklist. Verify CodeGraph or Semble before relying on
its answer. The bounded V16 controller may repair the exact owning-repository
index once; that circuit is one recovery strategy, not the end of Luna's
evidence-backed recovery mission. A broken optional tool reports degraded
coverage and repair debt; it blocks only the dependent claim.

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

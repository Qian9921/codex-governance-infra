# Codex Adaptive Governance Infra

[简体中文](README.zh-CN.md) · English

A Codex-only starter for researcher-engineers who need fast implementation,
trustworthy evidence, convergent review, clean code, and durable knowledge—without
turning every task into a release ceremony.

## What it does

```text
Outcome -> Reuse scan -> Luna executes -> Affected evidence
        -> One Sol review -> Qian/Liang PR trace -> Knowledge
```

- **Thin global policy:** outcome, roles, relevant tools, code health, evidence,
  review, privacy, and completion.
- **Adaptive profiles:** `QUICK`, `STANDARD`, and opt-in `STRICT`.
- **Code health:** project rules first, official Google guidance as the default,
  and a `REUSE|EXTEND|NEW` decision before meaningful new abstractions.
- **Relevant tool routing:** Semble for semantic discovery, CodeGraph for known
  structure/impact, `rg` for exact facts, and `rtk` for shell context.
- **Convergent review:** one independent reviewer; stable fixes are delta-only.
- **Safe installation:** manifest-bound, dry-run capable, atomic, backed up,
  hash-verified, and rollback-capable.

This repository supports Codex only. It does not claim compatibility with
Claude Code, Kimi Code, Zcode, or other agent runtimes.

## Profiles

| Profile | Use | Evidence and review | Hooks |
|---|---|---|---|
| `QUICK` | explanations, inventory, docs, reversible mechanics | targeted; formal review optional | advisory |
| `STANDARD` | normal development and research engineering | affected-first; one independent review | advisory |
| `STRICT` | security/privacy, exact math, public contracts, irreversible changes, installers/hooks/releases | V16 FAST/CANDIDATE/FINAL proof | fail-closed integrity |

Adaptive mode is the default. To run the installed hooks in strict mode, start
the relevant Codex surface with:

```bash
export CODEX_GOVERNANCE_MODE=strict
```

Strict mode is intentional, not an automatic penalty for every repository task.

## Model roles

- Sol: planning, architecture, synthesis, independent review.
- Luna: default execution lead, tools, implementation, tests, data, Git/GitHub.
- Spark: short bounded work delegated by Luna.
- Terra: execution fallback only when Luna is unavailable.

Roles are routing defaults, not capability bans.

## Ten-minute setup

### 1. Clone and verify

```bash
git clone https://github.com/Qian9921/codex-governance-infra.git
cd codex-governance-infra

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

The overlay owns only manifest-listed paths. It preserves configuration,
credentials, plugins, memories, sessions, connections, caches, receipts, and
all unrelated files.

### 4. Install

```bash
python3 scripts/install-governance.py \
  --source . \
  --codex-home "$ACTIVE_CODEX_HOME"
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
its answer. The execution lead may repair the exact owning-repository index
once. A broken optional tool reports degraded coverage; it blocks only when the
missing fact is essential to the decision.

## Code standards

Repository architecture and formatter/linter/compiler configuration are
authoritative. Applicable official Google language guidance is the default
baseline. Meaningful new abstractions require a short `REUSE`, `EXTEND`, or
`NEW` decision. Prefer composition; do not create parallel frameworks,
speculative generic layers, or god classes.

See [Code health](docs/code-health.md).

## GitHub responsibilities

- Qian9921 authors, pushes, opens the PR, and answers findings.
- Liang9921 independently comments, reviews the exact head, approves, and
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

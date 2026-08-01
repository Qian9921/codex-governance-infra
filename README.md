# Codex Governance Infrastructure v16

[简体中文](README.zh-CN.md) · English

A portable, privacy-safe governance layer for **OpenAI Codex users** who want
faster development and review without weakening correctness, evidence, or
approval boundaries.

This repository packages Codex-wide instructions, mission contracts,
affected-first evidence gates, deterministic tool routing, bounded multi-agent
audits, risk-routed independent review, privacy-safe hooks, and a reproducible
presubmit. It is a Codex-only project. It does not claim compatibility with
Claude Code, Kimi Code, Zcode, or other agent runtimes.

> **Preview status**
>
> V16 is suitable for source review, isolated installation, deterministic
> verification, and controlled team trials. The included installer replaces
> the destination it is given. For a trial, point it only at a newly created
> isolated `CODEX_HOME`—never at your active `~/.codex`. Live adoption requires
> a separately reviewed merge/deployment procedure that preserves local
> authentication, sessions, plugins, memories, and machine-specific settings.

## Why this exists

Coding-agent governance often fails in one of two directions:

- it is too loose, so results are fast but unsupported, unauditable, or unsafe;
- it is too heavy, so every review rebuilds everything, repeats prior work, and
  spends time and tokens without improving the decision.

V16 treats correctness and evidence as hard gates, then optimizes:

1. time to a correct decision or merge;
2. monotonic review closure with no repeated full-scope review;
3. token and call cost.

The design is intentionally evidence-based. A green claim must be falsifiable,
current, independently checkable, and backed by a known non-zero denominator.
Skipped, stale, copied, unknown, NaN/Inf, or identity-mismatched evidence is not
a pass.

## What you get

| Layer | What it provides |
|---|---|
| Global policy | `codex/AGENTS.md` defines authorization, evidence, Git, review, model-role, and tool-routing boundaries. |
| Mission briefs | `codex/BRIEF-TEMPLATES.md` turns work into explicit scope, owner, model, permissions, invariants, non-goals, gates, budgets, and stop conditions. |
| Contracts | `codex/v16/contracts.py` and the registry enforce strict mission, evidence, review, lineage, runtime, and metrics shapes. |
| Execution engine | FAST, CANDIDATE, and FINAL stages run content-addressed affected gates against exact identities. |
| Review engine | One risk-routed independent reviewer performs the formal gate; stable fixes reuse the same reviewer for delta-only closure. |
| Tool routing | Known structure uses CodeGraph, semantic discovery uses Semble, exact text uses `rg`, and shell output shown to the model uses `rtk`. |
| Hooks | Session and pre-tool hooks emit bounded guidance and privacy-safe receipts without storing prompts, raw arguments, cwd, tokens, or credentials. |
| Verification | The manifest verifier, unit/negative fixtures, and full presubmit make package claims reproducible. |
| Privacy | Sessions, credentials, tokens, receipts, plugin/cache state, model caches, memories, and user data are excluded from the package. |

## What this repository does not do

- It does not install or authenticate Codex.
- It does not make a model available when the current Codex control plane or
  account does not expose that model.
- It does not vendor or silently install CodeGraph, Semble, `rtk`, or `rg`.
- It does not modify GitHub accounts, create PRs, approve, or merge.
- It does not copy sessions, memories, plugins, connections, tokens, or
  machine-specific configuration.
- It does not provide a safe one-command overlay onto an existing live
  `~/.codex` yet.
- It does not replace repository-local `AGENTS.md`, tests, domain contracts, or
  project ownership rules.

## Prerequisites

Required for the source and isolated trial:

- Git;
- Python 3.9 or newer;
- an OpenAI Codex installation for eventual interactive use.

Required before accepting the full tool-routing contract on a workstation:

- a revision-matching, project-local CodeGraph capability;
- a current Semble agent/MCP capability;
- `rtk` on `PATH`;
- `rg` on `PATH`.

The package is implemented with the Python standard library only. The current
trial is verified on Linux. Other platforms should be treated as unverified
until their installer, hook, path, permission, and process behavior has been
tested explicitly.

Official Codex references:

- [Codex CLI](https://developers.openai.com/codex/cli)
- [Codex configuration basics](https://developers.openai.com/codex/config-basic)
- [Codex configuration reference](https://developers.openai.com/codex/config-reference)
- [AGENTS.md and customization](https://developers.openai.com/codex/concepts/customization)
- [Hooks](https://developers.openai.com/codex/config-advanced#hooks)

## Five-minute safe trial

### 1. Clone the preview branch

```bash
git clone \
  --branch codex/v16-productivity-engine \
  --single-branch \
  https://github.com/Qian9921/codex-governance-infra.git
cd codex-governance-infra
git rev-parse HEAD
git status --short
```

Record the 40-character commit. Review and evidence are meaningful only for the
exact snapshot you tested.

### 2. Verify the source package before installation

```bash
python3 scripts/verify-governance.py --repo .
python3 -m unittest discover -s tests -p 'test_*.py'
python3 -m unittest discover -s tests/v16 -p 'test_*.py'
```

The verifier must report `"status": "GREEN"` with an empty error list. The test
suite must report zero failures and zero errors. Do not install a package with a
manifest mismatch or privacy-scan failure.

### 3. Preview the install plan

```bash
GOV_TRIAL_ROOT="$(mktemp -d)"
GOV_TRIAL_HOME="$GOV_TRIAL_ROOT/.codex"
mkdir -p "$GOV_TRIAL_HOME"

python3 scripts/install-governance.py \
  --source . \
  --codex-home "$GOV_TRIAL_HOME" \
  --dry-run
```

The dry run prints a JSON plan with the destination placeholder, file count,
and SHA-256 for every managed file. It does not write the destination.

### 4. Install only into the isolated trial home

```bash
python3 scripts/install-governance.py \
  --source . \
  --codex-home "$GOV_TRIAL_HOME"

test -f "$GOV_TRIAL_HOME/AGENTS.md"
test -f "$GOV_TRIAL_HOME/BRIEF-TEMPLATES.md"
test -f "$GOV_TRIAL_HOME/hooks/hooks.json"
test -f "$GOV_TRIAL_HOME/v16/contracts.py"
test ! -e "$GOV_TRIAL_HOME/codex"
```

The package contents under repository `codex/` are intentionally flattened
into the isolated `CODEX_HOME`. Repository contribution instructions,
documentation, tests, Git state, and generated `__pycache__` files are not
installed.

### 5. Smoke-test the hooks without exposing real task data

```bash
GOV_RECEIPTS="$GOV_TRIAL_ROOT/receipts"
mkdir -p "$GOV_RECEIPTS"

CODEX_HOOK_SOURCE=test \
CODEX_HOOK_RECEIPT_DIR="$GOV_RECEIPTS" \
python3 "$GOV_TRIAL_HOME/hooks/session_context.py" </dev/null

printf '%s\n' '{"tool_name":"rg","model":"trial"}' |
  CODEX_HOOK_SOURCE=test \
  CODEX_HOOK_RECEIPT_DIR="$GOV_RECEIPTS" \
  python3 "$GOV_TRIAL_HOME/hooks/pre_tool_use_policy.py"
```

Expected results:

- `session_context.py` returns bounded V16 context and
  `"receipt_status": "success"`;
- the `rg` probe returns `"decision": "allow"` and route `"rg"`;
- receipt files are written only under the temporary trial directory;
- no real prompt, command arguments, cwd, credential, or session identifier is
  supplied.

### 6. Roll back the isolated installation

```bash
python3 scripts/install-governance.py \
  --source . \
  --codex-home "$GOV_TRIAL_HOME" \
  --rollback
```

Rollback restores the destination state saved in the sibling
`.codex.v16-backup`. It is part of the isolated installer test; it is not a
substitute for a reviewed live-home migration.

## Do not install directly into your live Codex home

This is deliberately unsupported during the preview:

```bash
# Do not run this during the trial.
python3 scripts/install-governance.py --source . --codex-home "$HOME/.codex"
```

The installer performs destination replacement. Pointing it at an active home
would temporarily replace unrelated Codex state. A live deployment must
instead preserve and verify, at minimum:

- authentication and account state;
- `config.toml` and machine-specific project trust;
- installed plugins, skills, MCP servers, and connector state;
- sessions, memories, caches, and shell state;
- existing hooks and locally owned rules;
- file ownership, modes, rollback identity, and the active Codex process.

Until a dedicated overlay installer has its own acceptance lock, negative
fixtures, independent review, and rollback proof, use only the isolated flow.

## Operating model

### Model roles are task-routed, not capability bans

All models remain subject to the same authorization and evidence rules. The
mission chooses a writer from models actually available and authorized in the
current Codex runtime. V16 does not pretend that editing a local model list can
grant provider or control-plane access.

Default formal-review routes are:

| Frozen risk | Formal reviewer | Context |
|---|---|---|
| Low or medium | `gpt-5.6-terra`, high effort | fresh `independent_clean_room` |
| High or unresolved | `gpt-5.6-sol`, xhigh effort | fresh `independent_clean_room` |
| Stable fix after complete coverage | same reviewer and model, high effort | `delta_continuation` |

High risk includes math/numerics, exact parity, security/privacy, public
contracts, schemas/data formats, irreversible migrations, supply-chain or
installer changes, production runtime, formal research/release, and
hook/reviewer/model-routing governance.

V16 may select zero to three bounded, report-only
`gpt-5.3-codex-spark` inner audits when Spark is available. These audits find
risks before the formal gate; they do not approve, merge, or replace the single
independent reviewer.

### Review convergence

The first formal review receives a compact, hash-bound clean-room packet rather
than the author’s full conversation. It reviews the exact diff/snapshot, direct
dependencies, affected tests, evidence denominators, invariants, non-goals,
limitations, and acceptance envelope.

Ordinary fixes keep reviewer continuity and send only:

- the old and new exact identities;
- the exact delta;
- prior findings and author dispositions;
- new or reused evidence;
- directly affected boundaries.

A fresh reviewer is used only for explicit escalation: contract/domain/scope
drift, material rewrite, independence or lineage loss, new falsifiable P1
evidence, non-convergence, governance changes, or invalidated evidence.

`APPROVE` requires complete coverage, an empty unreviewed scope, no active P1
or `BLOCKING` finding, matching lineage, and a matching independent artifact.
`APPROVE` is not permission to merge and is not a readiness `GO`.

### Affected-first evidence

| Stage | Purpose |
|---|---|
| FAST | Small, targeted checks that can turn red because of the current change. |
| CANDIDATE | The remaining frozen affected route on a clean exact candidate. |
| FINAL | Any still-required fresh portability evidence plus the single formal review gate. |

Every executable check declares WHY-RED, expected cost, denominator, and what
red or green proves. Valid evidence is content-addressed and may be reused only
when the complete identity still matches.

### Tool routing

| Intent | First tool | Important boundary |
|---|---|---|
| Known symbol, call, dependency, impact | CodeGraph | Use the revision-matching child-repository index. Building/syncing an index is an authorized mutation. |
| Unknown semantic entrypoint, similar implementation | Semble | Treat results as candidate recall; confirm important structure in source or CodeGraph. |
| Exact string, error, config, log | `rg` or bounded exact read | Use it for literal truth, not semantic or dependency claims. |
| Shell output shown to the model | `rtk` | Raw output is reserved for downstream machine input or exact denominators. |

Fallback is allowed only after a real preferred-tool failure or unavailability,
with a stable reason code and evidence reference. A fallback never claims
equivalent semantic or structural coverage.

## Full repository validation

Run the complete presubmit only on a frozen, clean candidate:

```bash
git status --short
python3 scripts/presubmit.py --repo .
```

The presubmit compiles the mission, runs positive and mandatory negative
contracts, checks ordering and identity drift, validates evidence arithmetic
and privacy, renders sanitized trace artifacts, derives metrics, and verifies a
fresh archive. It does not call GitHub or switch GitHub identity.

For a documentation-only working-tree change, use affected checks first:

```bash
python3 scripts/verify-governance.py --repo .
python3 -m unittest tests.test_installer tests.test_privacy -v
git diff --check
```

The manifest is an exact path-and-hash boundary. Any tracked file addition,
deletion, or content change requires a corresponding manifest update before the
verifier can be green.

## Hooks and receipts

The package contains:

- `SessionStart` and `SubagentStart` context generation;
- `PreToolUse` allow/deny and normalized route signaling;
- parent pre-dispatch, subagent mission-lock, post-result, and dispatch
  transcript checks for nested delegation;
- best-effort privacy-safe JSONL receipts.

Receipts contain normalized event/model/tool/decision/reason codes, a combined
hook snapshot hash, source, PID/PPID, and hashed identifiers. They exclude raw
prompts, tool arguments, cwd, tokens, credentials, and private identifiers.
Receipt-write failure remains visible and cannot support runtime-proof
acceptance.

Review the exact commit and hook source before trusting hooks in Codex. After a
trusted hook or global-rule change, start a fresh Codex task; existing tasks may
retain creation-time context.

## Security and privacy

Never add:

- API keys, GitHub tokens, OAuth state, cookies, or provider credentials;
- Codex sessions, prompts, histories, transcripts, memories, or shell
  snapshots;
- hook receipt JSONL;
- plugin caches, connections, model caches, browser profiles, or user data;
- personal absolute paths or private repository content.

Before sharing a change, run:

```bash
python3 scripts/verify-governance.py --repo .
git diff --check
```

See [SECURITY.md](SECURITY.md), [PRIVACY.md](PRIVACY.md), and
[the privacy threat model](docs/privacy-threat-model.md).

## Updating a trial clone

Treat every update as a new evidence identity:

```bash
git fetch origin
git status --short
git log --oneline --decorate HEAD..origin/codex/v16-productivity-engine
git pull --ff-only
python3 scripts/verify-governance.py --repo .
python3 -m unittest discover -s tests -p 'test_*.py'
python3 -m unittest discover -s tests/v16 -p 'test_*.py'
```

Do not pull over local changes without reviewing them. Do not reuse old
evidence after the commit, file set, manifest, hook snapshot, acceptance
envelope, command, runtime, or denominator changes. Codex CLI, Codex Desktop,
and a remote app-server may refresh on different lifecycles; this repository
does not manage their upgrades or model catalogs. Restart the affected Codex
surface and begin a fresh task after changing installed governance.

## Troubleshooting

| Symptom | Meaning | Action |
|---|---|---|
| `Unknown model ...` | The current runtime/catalog does not expose that model. | Use an available model allowed by the frozen mission, or stop as an infrastructure blocker when the contract requires an unavailable exact model. The repository cannot grant access. |
| Luna or Spark appears in one surface but not another | CLI, Desktop, remote host, or app-server catalog state differs. | Check the catalog on the exact host/surface, refresh that process, and repeat a fresh exact probe. |
| Hook is not loaded | The surface has not trusted/reloaded it, or its hook configuration differs. | Review the hook source, trust it, restart the affected Codex surface, and start a new task. |
| `receipt_status=write_failed` | Runtime receipt persistence failed. | Check the isolated receipt directory, ownership, permissions, and no-follow constraints; do not claim runtime-proof acceptance. |
| CodeGraph is missing or stale | Structural evidence is unavailable or not revision-matching. | Obtain authorization to build/sync the child-repository index, then query that index. |
| Semble is `unknown` | CLI probing cannot prove an MCP/agent capability. | Supply a current orchestrator capability observation; do not silently treat it as installed. |
| Manifest verifier is RED | A tracked path/hash, privacy rule, UTF-8 rule, or required file failed. | Stop installation, inspect every reported error, update the package and manifest through review, then rerun. |
| FINAL/presubmit rejects a dirty tree | The frozen clean identity is not satisfied. | Use affected checks during development; create a clean candidate before FINAL. |
| Review repeats the whole repository | The review packet or continuity mode is wrong. | Freeze one exact scope; use `delta_continuation` for ordinary fixes and escalate only on declared triggers. |

## Trial acceptance checklist

A teammate trial is successful only when all applicable items are recorded:

- exact repository commit and clean/dirty state;
- verifier status and file denominator;
- unit-test total, failures, errors, skips, and expected failures;
- isolated installer file count and destination;
- hook smoke-test decisions and receipt status;
- current availability/health for CodeGraph, Semble, `rtk`, and `rg`;
- models actually exposed by the tested Codex surface;
- limitations, unknowns, and rollback result.

Source presence is not proof that runtime routing occurred. A successful local
review is not a GitHub approval. A green synthetic fixture is not a production
or research claim.

## Repository map

```text
.
├── codex/
│   ├── AGENTS.md
│   ├── BRIEF-TEMPLATES.md
│   ├── hooks/
│   ├── contracts/
│   └── v16/
├── docs/
├── scripts/
│   ├── install-governance.py
│   ├── verify-governance.py
│   └── presubmit.py
├── tests/
├── manifest.json
├── SECURITY.md
└── PRIVACY.md
```

Detailed design documents:

- [Architecture](docs/architecture.md)
- [Deployment model](docs/deployment.md)
- [Review workflow](docs/review-workflow.md)
- [V16 contract registry](codex/v16/contracts/README.md)

## Contribution and release policy

- Keep changes small, coherent, portable, and privacy-safe.
- Use the repository’s exact manifest and mandatory negative fixtures.
- `Qian9921` owns development commits and PR authoring.
- `Liang9921` owns independent governance review, approval, and merge.
- The author must not review or approve their own change.
- Do not push directly to `main`.
- Do not publish a release from a dirty tree, incomplete review, unknown
  denominator, or stale evidence.

The current package is a private preview. Public release requires a separate
security, privacy, licensing, binary/artifact, portability, support, and
documentation review.

## License

See [LICENSE](LICENSE).

# Codex Governance Infrastructure v16

[简体中文](README.zh-CN.md) · English

A Codex-only starter that makes development and review faster without turning
correctness, evidence, or GitHub approval into guesswork.

V16 provides:

- durable Codex rules and mission briefs;
- affected-first tests instead of automatic full rebuilds;
- mandatory CodeGraph, Semble, and `rtk` readiness plus actual-use evidence;
- one risk-routed independent reviewer with delta-only follow-up;
- privacy-safe hook receipts;
- deterministic package verification and an isolated trial installer.

It does **not** claim compatibility with Claude Code, Kimi Code, Zcode, or other
agent runtimes.

> **Safety boundary**
>
> The installer replaces the destination passed to it. Use only a newly created
> isolated `CODEX_HOME`. Never point it at an active `~/.codex`. This repository
> never copies credentials, sessions, memories, plugins, connections, model
> caches, or private user data.

## Ten-minute teammate setup

### 1. Clone and verify

```bash
git clone https://github.com/Qian9921/codex-governance-infra.git
cd codex-governance-infra

git rev-parse HEAD
git status --short
python3 scripts/verify-governance.py --repo .
python3 -m unittest discover -s tests -p 'test_*.py'
python3 -m unittest discover -s tests/v16 -p 'test_*.py'
```

Continue only when the verifier reports `"status":"GREEN"` and the tests have
zero failures and errors.

### 2. Install the three tools if missing

Review upstream instructions before installing software:

```bash
# CodeGraph
npm install -g @colbymchenry/codegraph

# Semble
uv tool install semble

# rtk
cargo install --git https://github.com/rtk-ai/rtk
```

Upstream projects:

- [CodeGraph](https://github.com/colbymchenry/codegraph)
- [Semble](https://github.com/MinishLab/semble)
- [rtk](https://github.com/rtk-ai/rtk)

### 3. Configure Codex

```bash
codegraph install --target codex --location global --yes
semble install --agent codex --type mcp --yes

rtk init --codex --global --dry-run
rtk init --codex --global
```

The Semble command installs only MCP configuration, avoiding a second long
instruction block. Review configuration changes, restart the affected Codex
CLI/Desktop/app-server, and open a fresh task.

### 4. Prepare this repository's CodeGraph index

```bash
codegraph status --json .
```

If the repository is not initialized and indexing is authorized:

```bash
codegraph init .
```

After structural edits, synchronize only when authorized:

```bash
codegraph sync .
```

An index belongs to its owning repository. Never use a parent workspace graph
as child-repository truth.

### 5. Run the strict toolchain doctor

```bash
python3 scripts/toolchain-doctor.py \
  --repo . \
  --semantic-query "deterministic inspection intent router" \
  --expected-path codex/v16/tool_routing.py
```

The only passing result is exit `0`, `"status":"ready"`, and `3/3`:

- CodeGraph is configured, bound to this repo, complete, fresh, and finds the
  expected current source;
- Semble is configured, callable, repo-scoped, and returns the expected source
  from a semantic query;
- `rtk` reproduces the current Git identity and preserves a deterministic
  non-zero failure.

Binary presence alone is not readiness. The doctor is read-only and stores only
hashes/reason codes, never raw output, absolute paths, prompts, environment
variables, or credentials.

### 6. Try the governance package in isolation

```bash
GOV_TRIAL_ROOT="$(mktemp -d)"
GOV_TRIAL_HOME="$GOV_TRIAL_ROOT/.codex"

python3 scripts/install-governance.py \
  --source . \
  --codex-home "$GOV_TRIAL_HOME" \
  --dry-run

python3 scripts/install-governance.py \
  --source . \
  --codex-home "$GOV_TRIAL_HOME"

CODEX_HOOK_SOURCE=test \
CODEX_HOOK_RECEIPT_DIR="$GOV_TRIAL_ROOT/receipts" \
python3 "$GOV_TRIAL_HOME/hooks/session_context.py" <<'JSON'
{"hook_event_name":"SessionStart","model":"trial"}
JSON
```

Expected:

- dry-run reports the managed file denominator;
- installed files live only under the isolated directory;
- hook output contains `"receipt_status":"success"`;
- no active Codex home is modified.

Rollback:

```bash
python3 scripts/install-governance.py \
  --source . \
  --codex-home "$GOV_TRIAL_HOME" \
  --rollback
```

Rollback is available only when an earlier destination was backed up.

### 7. Give Codex this one prompt

```text
Read this repository's README and AGENTS.md. Verify the package, inspect the
current CodeGraph/Semble/rtk configuration, prepare the owning-repo CodeGraph
index only with my authorization, run the strict toolchain doctor with a
repository-specific semantic sentinel, and use the resulting current preflight
receipt. For the task, use Semble for unknown semantic discovery, CodeGraph for
known structure/impact, rtk for shell output shown to context, rg for exact
text, and raw commands for hashes/parsers/exact denominators. Record actual
receipt-backed tool usage; do not perform irrelevant check-box calls.
```

That is enough for a teammate to let Codex drive the remaining setup while
keeping mutations and failures visible.

## What “mandatory tools” means

There are two separate gates.

### Gate 1: readiness

`tool-preflight.v16` is bound to the current host/runtime, tool versions, Codex
configuration, repository root, Git head, worktree, CodeGraph index, and
semantic sentinel. Any identity change invalidates the cached receipt.

### Gate 2: actual use

`tool-usage.v16` binds each declared route to a successful task-relevant call,
evidence reference, and privacy-safe hook receipt hash.

| Task intent | Required route |
|---|---|
| Unknown semantic entrypoint or similar implementation | Semble |
| Known symbol, call, dependency, or blast radius | CodeGraph |
| Shell output shown to the model | `rtk` |
| Exact string, error, config, or log | `rg`/bounded exact read |
| Hash, parser input, byte identity, exact denominator | Raw command |

Calling every tool once without using its result is a violation. Fallback is
allowed only after a real preferred-tool failure with a reason code and evidence
reference; it never claims equivalent semantic or structural coverage.

Detailed contract and remediation:
[Mandatory toolchain](docs/TOOLCHAIN.md).

## Development and review model

1. Freeze objective, scope, invariants, non-goals, exact identity, and evidence
   budget.
2. Run only affected checks with a concrete WHY-RED and known denominator.
3. Use one independent reviewer:
   - low/medium risk: Terra high;
   - high risk: fresh Sol xhigh.
4. Stable fixes return to the same reviewer for delta-only closure.
5. Approve only with complete coverage, empty unreviewed scope, no active P1 or
   `BLOCKING` finding, matching evidence, and exact-head identity.
6. Merge with an expected-head/match-head guard.

Correctness and evidence are hard gates. The first optimization target is time
to the correct decision or merge; token/call cost is second.

## Full validation

During development, run the smallest affected checks. On a frozen clean
candidate:

```bash
git status --short
python3 scripts/presubmit.py --repo .
git diff --check
```

The manifest is an exact tracked path-and-hash boundary. Every tracked addition,
deletion, or content change requires a matching manifest update.

## Privacy and limitations

Never commit:

- API/GitHub tokens, OAuth state, cookies, or credentials;
- Codex sessions, prompts, transcripts, memories, or receipts;
- plugin/connection/model caches or browser profiles;
- personal absolute paths or private repository content.

The package cannot grant a model or tool that the current Codex surface does not
expose. CLI, Desktop, remote hosts, and app-server processes may refresh on
different lifecycles. Restart the affected surface and create a fresh task after
governance, MCP, hook, or model-routing changes.

See [SECURITY.md](SECURITY.md), [PRIVACY.md](PRIVACY.md), and
[the privacy threat model](docs/privacy-threat-model.md).

## Troubleshooting

| Symptom | Action |
|---|---|
| `CODEGRAPH_WRONG_PROJECT` | Stop and point the doctor/query at the owning child repo. |
| `CODEGRAPH_STALE` | Review the changes, authorize, and run `codegraph sync .`. |
| `SEMBLE_MCP_NOT_CONFIGURED` | Run the reviewed Semble MCP configuration command and restart Codex. |
| `SEMBLE_SENTINEL_MISMATCH` | Improve the semantic query or repair repo/index scope; do not claim readiness. |
| `RTK_FALSE_GREEN` | Hard stop; repair rtk before accepting shell evidence. |
| `receipt_status=write_failed` | Repair the private receipt directory; runtime-proof acceptance is blocked. |
| `Unknown model ...` | Check the exact host/surface catalog; this repo cannot grant model access. |
| Manifest verifier is RED | Stop, inspect every mismatch, update through review, and rerun. |

## Repository map

```text
codex/                     installable governance package
  AGENTS.md
  BRIEF-TEMPLATES.md
  hooks/
  v16/
docs/TOOLCHAIN.md          detailed tool readiness and routing contract
scripts/toolchain-doctor.py
scripts/install-governance.py
scripts/verify-governance.py
scripts/presubmit.py
tests/
manifest.json              exact tracked path/hash boundary
```

## Official Codex references

- [Codex CLI](https://developers.openai.com/codex/cli)
- [Configuration basics](https://developers.openai.com/codex/config-basic)
- [Configuration reference](https://developers.openai.com/codex/config-reference)
- [AGENTS.md and customization](https://developers.openai.com/codex/concepts/customization)
- [Hooks](https://developers.openai.com/codex/config-advanced#hooks)

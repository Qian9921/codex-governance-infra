# Codex Governance Infrastructure v16 — Productivity Engine

Portable, privacy-safe global Codex governance policy, V16 mission contracts,
foreground evidence gates, bounded Spark audits, and artifact-derived
productivity metrics. This private repository is a source package only: it does
not deploy to a live `$CODEX_HOME` during development.

## V16 workflow

1. Freeze `codex/v16/ACCEPTANCE-LOCK.md` and the exact base/head/tree identity.
2. Compile a mission without executing it:
   `python3 -m codex.v16.compiler codex/v16/fixtures/mission.valid.json`.
3. During development, run `GateRunner.run_tier("FAST", ..., snapshot_mode=...)`
   for targeted content-addressed checks. Promote a clean candidate through
   `CANDIDATE`; reserve `python3 scripts/presubmit.py --repo .` for the frozen
   `FINAL` required-stage workflow.
4. Select zero to three bounded, zero-context Spark audits from the actual risk
   and scope. The frozen V16 mission selects three and records their sanitized
   findings/dispositions in `codex/v16/contracts/`.
5. One risk-routed report-only reviewer owns the final verdict:
   `gpt-5.6-terra` high for classified low/medium risk and `gpt-5.6-sol` xhigh
   for high or unresolved risk. Initial review is clean-room; ordinary fixes
   preserve reviewer continuity and only explicit escalation triggers replace
   the reviewer. The mission chooses its writer from authorized live models;
   the renderer never calls GitHub or switches identity. A review `APPROVE` is
   not a merge/GO claim.

Every check declares WHY-RED, cost, and a known non-zero denominator. Exact
arithmetic, current identity, clean state, log hash, and privacy checks are
required. Unknown/skip/xfail/stale/copied evidence is RED; there are no manual
count supplements. A dirty FAST receipt is acceptable only when its recorded
content SHA-256 matches the caller-supplied snapshot identity.

## Tool routing is infrastructure

The preferred lookup path is deterministic and applies to every model:

| Intent | Required first tool |
|---|---|
| Known symbol, call path, dependency, or blast radius | revision-matching child-repository CodeGraph |
| Unknown semantic entrypoint or similar implementation | Semble |
| Exact string, error, configuration, or log lookup | `rg` or a bounded exact read |
| Shell output rendered into model context | `rtk` |

`codex.v16.tool_routing` validates this mapping and emits a denominator-known
health report. The session and pre-tool hooks reinforce the same route and
write privacy-safe normalized receipts; they never persist prompts, raw tool
arguments, cwd, tokens, credentials, or private identifiers. A preferred tool
may be bypassed only after a real unavailable/failed attempt, with a stable
reason code and evidence reference. Semantic intent cannot be inferred safely
from every low-level call, so hooks do not invent brittle blanket denials:
selection is contractual, availability/fallback is mechanically auditable.

CodeGraph state is project-local generated data (`.codegraph/`) and is ignored
by Git. Index creation or synchronization is a deliberate authorized mutation;
after that authorization, use incremental sync after edits and never substitute
a parent/workspace graph for a child repository. Semble is an agent/MCP
capability rather than vendored source. Raw shell output remains valid when it
is machine input or an exact denominator; `rtk` is required when that output is
being placed into model context.

## Correctness, latency, and token control

Each mission brief includes a routing/usage sidecar with hard caps for model
calls, review calls, active parallel agents, and reserved input/output/total
tokens. An orchestrator must reserve against
`codex.v16.metrics.BudgetLedger` before each dispatch and settle it afterward;
the thread-safe ledger fails closed when a cap would be exceeded and emits a
privacy-safe aggregate usage receipt. It is a portable enforcement primitive,
not an automatic provider billing meter. Exact-snapshot cache reuse avoids
paying twice for unchanged work.

The router uses current authorization, availability, task/risk preferences, and
relative `token_cost_rank`; it has no model-slug capability bans. A ChatGPT
subscription price is not an API per-token price, so USD attribution remains
`unavailable` unless the provider supplies an exact plan-specific mapping.
Correctness and valid evidence are hard gates. Primary optimization is time to
a correct verdict/merge and monotonic closure; token/call usage is secondary.
Adjudicated false blockers, reopened scope, missed P1s, and evidence reuse
measure review quality. Raw tokens per second and first-pass approval are
diagnostics, not delivery targets.

## Layout

- `codex/AGENTS.md` — compact V16 policy and role matrix.
- `codex/BRIEF-TEMPLATES.md` — mission and gate brief templates.
- `codex/v16/` — strict schemas, mission compiler, readiness state, foreground
  runner, evidence engine, tool-routing contract, Spark protocol, trace
  renderer, metrics, and presubmit orchestration.
- `codex/contracts/` — prior delegation examples retained for compatibility.
- `scripts/` — dry-run/atomic installer, verifier, and `presubmit.py`.
- `docs/` — architecture, deployment, review, and privacy model.
- `tests/v16/` — deterministic positive and mandatory negative fixtures.

Run `python3 scripts/presubmit.py --repo .` only for the complete frozen
clean-candidate evidence envelope.
Run `python3 scripts/verify-governance.py --repo .` for a deterministic read-only
source scan. Use the installer only with an isolated `CODEX_HOME`; live
deployment requires a separately authorized governance lane.

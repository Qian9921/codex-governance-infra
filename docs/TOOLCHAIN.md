# Toolchain: V21 three-lane routing and strict proof

This document is the operational contract for the V21 three-lane surface.
Daily `QUICK`/`STANDARD` work uses Semble discovery, the compiler-derived
semantic gateway, and bounded exact source/Git/compiler/build/test/benchmark
evidence. CodeGraph, `rg`, and `rtk` are retained only for explicit V16
`STRICT` compatibility.
The short path is in [README.md](../README.md).

The gateway launcher is `codex/bin/semantic-gateway.py`; its normalized
receipt is `semantic-gateway.v1`. `doctor` and `sync` may be PARTIAL or
NOT_READY when clangd/Pyright or the configured workset are absent. That state
is truthful and routes the dependent fact to one named bounded exact-evidence
fallback; it does not block unrelated STANDARD work.

In `QUICK` and `STANDARD`, use only task-relevant routes and verify a semantic
or structural tool before relying on its answer. Missing contracts and optional
receipts are advisory. The full four-contract chain below is mandatory only for
an explicitly `STRICT` mission. In every profile, a tool blocks completion only
when its missing fact is essential to the correctness claim.

## Large-code machine contract

The hook and `route_mission` expose `code-mission-tool-index-policy.v1`, which
is validated by `validate_code_mission_tool_policy`. A `large_code` packet must
carry opaque hashes for the exact repository root, `HEAD`, `HEAD^{tree}` and
worktree, plus revision-matching Semble and CodeGraph index identities. Semble
semantic/similar discovery is required before development; CodeGraph structural
or blast-radius evidence is required before `CANDIDATE_READY`. Luna owns
distinct-strategy repair of an unhealthy required capability, and only the
dependent claim is blocked while that repair is in progress. The contract never
imposes a per-turn or per-call quota.

The two evidence fields are objects, not checklist booleans. Each uses
`code-mission-evidence.v1` with an exact `kind`, a canonical privacy-safe
`semble://evidence/<receipt_sha256>` or
`codegraph://evidence/<receipt_sha256>` ref, a non-empty query hash, and the
frozen repository/root, `HEAD`, tree, worktree, and corresponding index hashes.
The validator rejects missing refs, arbitrary `true` flags, and any mismatch
between an evidence object and the frozen packet identity. `candidate_ready`
is monotonic: it requires both bound evidence objects, healthy Semble and
CodeGraph, healthy repair state, and an unblocked dependent claim.

Only `non_code` or pure `exact_mechanical` packets may mark these routes `N/A`,
and they must provide a non-empty `n_a_reason` while carrying no repository or
index identity. This is a lightweight adaptive evidence contract; it does not
revive repository preflight or the V16 four-contract ceremony for every task.

## Self-healing capability recovery

This policy covers required tools, libraries, datasets, environments, and their
task-specific configuration/access—not just the V16 CodeGraph controller.
Luna owns recovery; Sol audits the evidence; Terra may execute an explicit,
bounded bridge when its Luna parent is available. The separate continuity
fallback is only for genuine Luna unavailability, with the requested and actual
model plus reason recorded.

The adaptive role contract also permits two explicit, short-lived Terra bridges:
`TERRA_REPLAN` for bounded R0/R1 planning synthesis and `TERRA_TRIAGE` for
read-only R0/R1 triage. Both require a Luna parent, a strict child scope, a
900-second/32-call/8192-token maximum budget, and direct return to that parent.
They cannot write, review, merge, spawn, listen, retry, or issue a final verdict.
They are not continuity fallback and never replace the independent Sol review;
`TERRA_CONTINUITY` remains reserved for genuine Luna unavailability.

| State | Meaning and required handling |
|---|---|
| `HEALTHY` | Usable and exercised by the real dependent task slice; retain the task-relevant evidence. |
| `RECOVERING` | A relevant capability has a machine-owned scheduled continuation; Luna owns its bounded next evidence-producing strategy, so do not claim the dependent capability yet. |
| `DEGRADED` | An **explicitly optional** capability failed; unrelated work may continue with explicitly lost coverage and owned repair debt. |
| `EXTERNAL_WAIT` | A genuinely outside service, provider, or dependency must change; retain the exact failed boundary and automatically recheck with bounded backoff. |
| `USER_ACTION_REQUIRED` | Only a scientific/product choice, credential/licensing decision, irreversible/shared-state action, material unapproved cost, or privacy decision is needed from the user. A check-only/no-mutation result is not this state. |
| `UNRECOVERABLE` | Evidence proves the whole permitted recovery graph is exhausted; block only the dependent claim or slice and report the evidence boundary. A controller or one-strategy budget ending is never sufficient. |

Start with the least invasive strategy that can produce useful evidence. If it
fails or makes no progress, record the stable failure fingerprint, never repeat
that strategy, and continue the recovery mission with a materially distinct,
safe strategy. Examples include correcting an exact repository binding,
repairing a local index, using an already-authorized compatible environment,
repairing a package/configuration, or resolving an approved dataset route.
Each successful repair must be exercised by the real dependent task, not merely
detected as installed. Preserve unrelated user state and rollback reversible
repairs on failure.

Normal authorized machine repair remains Luna's execution-owner work; a
check-only/no-mutation invocation is evidence, not a user boundary. Use
`EXTERNAL_WAIT` only for a genuinely outside dependency and schedule bounded
backoff rechecks. A circuit record or exhausted controller budget closes one
strategy only. It becomes `UNRECOVERABLE` only after evidence shows the whole
permitted recovery graph has no remaining distinct, safe strategy.

The adaptive `tool-recovery.v1` report records the owner and schedule of an
ongoing recovery as `continuation_owner` and `recheck_after_sec`. A relevant
capability with those machine-owned continuation fields is `RECOVERING`, not
`DEGRADED`. Use `DEGRADED` only when the mission explicitly classifies the
capability as optional. This adaptive status report does not alter the separate
V16 receipts, contracts, or enforcement required by explicit `STRICT`.

Required failure blocks only its dependent claim/slice; it is not permission to
stop unrelated work. Optional failure becomes `DEGRADED` with an owner, failed
strategy/fingerprint, lost coverage, next distinct strategy, and revisit
condition as repair debt. `QUICK` and `STANDARD` keep this lightweight in the
normal task evidence or status update—no new hook gate. Explicit `STRICT`
continues to use the V16 task-contract, receipts, and fail-closed enforcement
below; its controller circuit opens one strategy, not the whole recovery
mission.

## The four contracts and reliability plane

1. `tool-preflight.v16` proves the tools are current and trustworthy for one
   exact repository identity.
2. `tool-task-contract.v16` classifies the complete four-route task
   applicability denominator and explicitly selects repository or
   non-repository scope.
3. `tool-usage.v16` proves every task-declared route was actually used and
   produced a task-relevant, receipt-backed result.
4. `tool-enforcement.v16` proves every applicable preferred route was
   satisfied before completion.

Default/adaptive maintenance uses `tool-recovery.v1`: a finite sequence of
distinct, exact-repository recovery strategies with health evidence after each.
`tool-maintenance.v16` remains the separate explicit-`STRICT` reliability
plane: it performs at most one allowlisted exact-repo CodeGraph repair under a
lock, rechecks, and persists the stable failure fingerprint. Neither is an
evidence gate or changes the acceptance criteria.

A binary on `PATH` is not readiness. One irrelevant call to each tool is not
usage compliance.

Strict repository source/read/write tasks record `--repository-work`, declare the
applicable route signals, and run readiness before routing. A plugin, model,
user-configuration, service, or machine inventory that performs no repository
read or write records `--non-repository-task` with no route signals and skips
repository readiness. The recorder requires exactly one scope flag. If a
non-repository task expands into repository work, start a new intake and bind a
repository contract; do not reuse the narrower contract. Non-repository tool
calls may originate from a Desktop task attached to a repository, because that
session cwd is not the execution target. `PreToolUse` instead rejects explicit
repository targets and repository-only tools under the narrower contract.
Target inspection is transient; raw command arguments and resolved paths are
never persisted. Installed state below `CODEX_HOME` and its sibling `.agents`
remains available to machine/plugin/skill inventory and maintenance.

## Configure Codex

Review every command before running it. These commands configure tools that are
already installed; they do not make an unavailable product capability appear.

```bash
# CodeGraph MCP for Codex
codegraph install --target codex --location global --yes

# Semble MCP only; avoid injecting a second long instruction block
semble install --agent codex --type mcp --yes

# Preview, then install rtk guidance for Codex
rtk init --codex --global --dry-run
rtk init --codex --global
```

Restart the affected Codex CLI/Desktop/app-server surface after configuration
changes and start a fresh task.

## Prepare a repository

CodeGraph state belongs to the owning repository. Never use a parent workspace
graph as child-repository truth.

```bash
cd /path/to/repository

# Read-only inspection
codegraph status --json .

# Manual equivalents of the controller's repo-local repair
codegraph init .

codegraph sync .
```

The installed maintenance policy pre-authorizes only exact owning-repo
`init/sync` for the current execution lane's model-independent
`tool_maintainer` role. A doctor run never mutates. Package/config/system
repair remains external.

## Run the strict doctor

Choose a behavior description and a current repo-relative source path that
implements it. Do not use only the filename as the query.

```bash
python3 scripts/toolchain-doctor.py \
  --repo . \
  --semantic-query "deterministic inspection intent router" \
  --expected-path codex/v16/tool_routing.py
```

Exit `0` and `"status":"ready"` require a known denominator of `3/3`:

- CodeGraph MCP configured, correct project, complete and fresh index, safe
  indexed paths, and matching sentinel query;
- Semble MCP configured, callable command surface, repository-scoped results,
  and the expected live-source sentinel;
- `rtk` callable, current Git head reproduced, repository command successful,
  and a deterministic missing-ref command remains non-zero.

The report stores hashes and reason codes, not raw command output, absolute
paths, prompts, environment variables, or credentials. It performs no writes.

## Run adaptive recovery (default)

```bash
python3 codex/bin/toolchain-auto.py \
  --repo . \
  --semantic-query "deterministic inspection intent router" \
  --expected-path codex/v16/tool_routing.py
```

Without `--strict-maintenance` (and outside strict mode), this produces a
`tool-recovery.v1` report. The controller checks health first, then for the
exact canonical repository tries the appropriate `sync` or `init` strategy.
It checks health after that action. If needed, it makes a private owner-only backup
and performs the distinct exact-repository index-rebuild strategy,
again checking health; an unhealthy rebuild is rolled back from that backup.
Every strategy records privacy-safe evidence, and a strategy-specific
no-progress fingerprint is never replayed.

An incomplete adaptive recovery reports machine continuation through
`continuation_owner` and `recheck_after_sec`; it is `RECOVERING` rather than a
claim that the capability is unavailable. A controller/strategy budget ends
only that strategy set, never the wider recovery mission.

It never:

- repairs a parent or sibling repository;
- repeats the same no-progress repair;
- clears the global Semble cache;
- installs or updates packages;
- edits user Codex configuration;
- uses sudo, background processes, or shell command strings.

The adaptive report distinguishes `HEALTHY`, `RECOVERING`, `DEGRADED`,
`EXTERNAL_WAIT`, `USER_ACTION_REQUIRED`, and `UNRECOVERABLE` as defined above.
Ordinary stale indexes are never `EXEC_INFRA_BLOCKED`.

If a strategy fails or makes no progress, the controller records an owner-only
circuit entry containing hashes and a reason code only, then selects an untried
safe strategy or schedules machine continuation. A changed fingerprint can
receive a new bounded strategy attempt.

## Run strict V16 maintenance (unchanged)

Use this path only for an explicit strict maintenance operation:

```bash
python3 codex/bin/toolchain-auto.py \
  --strict-maintenance \
  --repo . \
  --semantic-query "deterministic inspection intent router" \
  --expected-path codex/v16/tool_routing.py
```

`CODEX_GOVERNANCE_MODE=strict` selects the same `tool-maintenance.v16` path
unless `--adaptive-recovery` is explicitly supplied. Strict maintenance checks
first, performs at most one allowlisted `init` or `sync` exact-repository
repair under its lock, and rechecks. Its legacy terminal states
`ready`, `maintenance_required`, `external_action_required`, and
security/policy `blocked`, plus the one-attempt reason codes below, apply only
to this strict V16 artifact—not to the adaptive recovery mission.

## Cache and invalidation

A preflight receipt may be reused only while its `cache.key_sha256` remains
bound to the same:

- host/runtime and tool versions;
- Codex configuration;
- repository root, Git head, and worktree;
- CodeGraph index;
- semantic query and expected path.

Any change invalidates the receipt. Do not rerun the doctor before every tool
call; rerun it on identity change and before formal approval if the current
receipt is stale.

## Mandatory routing after readiness

Compile the complete task contract from seven structured booleans:

- unknown semantic entrypoint;
- similar implementation;
- known symbol or call;
- dependency or blast radius;
- exact text/error/config/log;
- shell output for model context;
- machine-exact-only processing.

The V21 daily contract derives exactly three rows:
`discovery|semantic_gateway|exact_evidence`. Every row is
`required|not_applicable`; omission is invalid. The older four-row contract
below is retained only inside explicit V16 `STRICT` compatibility.

| Intent | Required first tool | Result that must be retained |
|---|---|---|
| Unknown semantic entrypoint or similar implementation | Semble | Candidate path/line used to choose the next inspection |
| Known symbol, call, dependency, or blast radius | semantic gateway | Compiler/provider receipt and result |
| Source/Git/compiler/build/test/benchmark fact | bounded exact evidence | Unmodified machine evidence |
| Unknown semantic or similar implementation | Semble | Candidate path/line used to choose next inspection |

Fallback requires a real failed preferred attempt, a stable reason code, and an
evidence reference. It never claims equivalent structural or semantic coverage.

At closure, `tool-usage.v16` binds each declared route to:

- the selected tool;
- success/failure;
- a task-relevant purpose;
- evidence reference;
- privacy-safe hook receipt hash;
- the current preflight cache key.

Missing, wrong-tool, failed, undeclared, receipt-free, or irrelevant check-box
calls are violations.

`tool-enforcement.v16` then compares successful preferred-tool calls to all
four applicability rows. Only `completion_eligible=true`, four adjudicated
rows, zero failed/skipped/xfail/unknown, and no violations support completion.
A proved fallback may support bounded exploration but remains
`DEGRADED_COVERAGE`; it never becomes equivalent acceptance evidence.

## Native lifecycle enforcement

The package installs the current Codex hook configuration at
`~/.codex/hooks.json`; executable handlers remain under `~/.codex/hooks/`.
`SessionStart` and `SubagentStart` inject compact routing guidance.
`UserPromptSubmit` persists a privacy-safe prompt-shape hash and injects the
exact task/shape hashes needed by the one-time contract recorder.
`PreToolUse` denies hook-observable calls until that validated, prompt-bound,
immutable `tool-task-contract.v16` exists, then records the exact expected call
id. `PostToolUse` records only explicit supported success shapes.
`Stop` compares the contract and expected ids with successful current-turn,
current-hook-snapshot receipts. Assistant-authored message text is not trusted.

The Stop gate applies to hook-observable activity. For a repository contract it
requires a successful current strict preflight or maintenance receipt and every
route derived as `required`. For an explicit non-repository contract it still
requires matching current-snapshot PostToolUse receipts for every expected
call, but it does not invent repository readiness or route requirements. Missing
intake, contract, expected-call, PostToolUse, or hook-snapshot state fails
closed. On the first
failure it returns `decision=block`, which asks Codex to continue once. When
`stop_hook_active=true`, it emits `TOOL_ENFORCEMENT_BLOCKED` and does not request
another continuation. This is a circuit breaker, not a false pass.

Codex trusts non-managed hooks by exact definition hash. After installing or
updating this package, review the changed hooks with `/hooks`; an untrusted hook
is skipped and therefore cannot provide runtime-proof acceptance. Tool hooks
are a strong lifecycle guardrail, but the Codex platform documents that some
specialized tool paths can bypass the default hook path. Formal acceptance
therefore still requires the caller-bound task, usage, and enforcement
artifacts rather than claiming universal interception.

For CLI transport, the hook recognizes `rtk codegraph ...`,
`rtk semble ...`, and `rtk rg ...` as the substantive evidence route while
persisting no raw command or path. Plain `rtk <shell-command>` remains the
shell-context route. MCP tools are recognized by stable server prefixes.

## Strict V16 reason codes and one-attempt remediation

| Reason code | Meaning | Remedy |
|---|---|---|
| `CODEGRAPH_NOT_FOUND` | Binary is unavailable | Install CodeGraph, then configure Codex |
| `CODEGRAPH_MCP_NOT_CONFIGURED` | Codex config has no CodeGraph MCP table | Run the reviewed CodeGraph install command |
| `CODEGRAPH_WRONG_PROJECT` | Index belongs to another repository | Strict controller initializes the exact owning repo once |
| `CODEGRAPH_STALE` | Indexed state differs from the worktree | Strict controller runs one exact-repo `codegraph sync` and rechecks |
| `CODEGRAPH_INDEX_INVALID` | Index is missing/incomplete/reindex-required | Strict controller chooses exact-repo `init` or `sync` once |
| `CODEGRAPH_SENTINEL_MISMATCH` | Current expected source was not found | Check index, query, path, and revision |
| `SEMBLE_NOT_FOUND` | CLI capability is unavailable | Install Semble and configure MCP |
| `SEMBLE_MCP_NOT_CONFIGURED` | Codex config has no Semble MCP table | Run the reviewed Semble MCP command |
| `SEMBLE_SCOPE_CONTAMINATION` | Search returned unsafe/out-of-repo paths | Stop; repair repository scope |
| `SEMBLE_SENTINEL_MISMATCH` | Expected live source was not returned | Refine the semantic sentinel or repair search state |
| `RTK_NOT_FOUND` | `rtk` is unavailable | Install it and initialize Codex guidance |
| `RTK_OUTPUT_MISMATCH` | Wrapped Git identity differs from raw Git | Do not use rtk as evidence until repaired |
| `RTK_FALSE_GREEN` | A failing command became successful | Hard stop; rtk cannot support acceptance |
| `AUTO_REPAIR_NO_PROGRESS` | One strict allowlisted repair did not shrink the failure set | Open strict circuit; return `MAINTENANCE_REQUIRED`, do not retry that strict strategy |
| `AUTO_REPAIR_CIRCUIT_OPEN` | The same stable failure already consumed its strict repair budget | Do not retry that strict strategy; change the underlying state or route to the named external owner |
| `EXTERNAL_TOOL_REPAIR_REQUIRED` | Package/config/system action is needed | Return the exact owner/action; do not relabel as model infra failure |

Use `--advisory` only for diagnosis. Advisory failures return `degraded`, never
`ready`, and cannot satisfy a formal gate.

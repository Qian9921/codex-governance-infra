# Mandatory toolchain: readiness before routing

This document is the operational contract for CodeGraph, Semble, and `rtk`.
The short path is in [README.md](../README.md).

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

`tool-maintenance.v16` is the separate reliability plane: it wraps the
read-only preflight, performs at most one allowlisted exact-repo CodeGraph
repair under a lock, rechecks, and persists the stable failure fingerprint. It
is not an evidence gate and never changes success criteria.

A binary on `PATH` is not readiness. One irrelevant call to each tool is not
usage compliance.

Repository source/read/write tasks record `--repository-work`, declare the
applicable route signals, and run readiness before routing. A plugin, model,
user-configuration, service, or machine inventory that performs no repository
read or write records `--non-repository-task` with no route signals and skips
repository readiness. The recorder requires exactly one scope flag. If a
non-repository task expands into repository work, start a new intake and bind a
repository contract; do not reuse the narrower contract. Non-repository tool
calls execute from a cwd outside every Git repository. `PreToolUse` rejects an
inside-repository call under the narrower contract without inspecting or
persisting raw command arguments.

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

## Run the automatic bounded controller

```bash
python3 codex/bin/toolchain-auto.py \
  --repo . \
  --semantic-query "deterministic inspection intent router" \
  --expected-path codex/v16/tool_routing.py
```

The controller always checks first. When CodeGraph is stale, invalid,
uninitialized, or bound to the wrong project, it selects `init` or `sync`
for this exact canonical repo, acquires a private owner-only single-flight
lock, executes one direct-argv repair, and reruns strict preflight.

It never:

- repairs a parent or sibling repository;
- repeats the same no-progress repair;
- clears the global Semble cache;
- installs or updates packages;
- edits user Codex configuration;
- uses sudo, background processes, or shell command strings.

Terminal states are `ready`, `maintenance_required`,
`external_action_required`, and security/policy `blocked`. Ordinary stale
indexes are never `EXEC_INFRA_BLOCKED`.

If the repair fails or makes no progress, the controller writes an owner-only
circuit record containing hashes and a reason code only. Re-invoking it for the
same repo/config/revision/query/path failure returns
`AUTO_REPAIR_CIRCUIT_OPEN` with zero repair attempts. A changed fingerprint can
receive one new bounded attempt.

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

The compiler derives exactly four rows:
`semantic_discovery|structural_analysis|exact_lookup|shell_context`. Every row
is `required|not_applicable`; omission is invalid. A repository task with no
required row is invalid unless it is explicitly machine-exact-only.

| Intent | Required first tool | Result that must be retained |
|---|---|---|
| Unknown semantic entrypoint or similar implementation | Semble | Candidate path/line used to choose the next inspection |
| Known symbol, call, dependency, or blast radius | CodeGraph | Current structural path/impact result |
| Shell output shown to the model | `rtk` | Compact human-context output |
| Exact string, error, config, or log | `rg`/bounded exact read | Literal match |
| Hash, parser input, byte identity, exact denominator | Raw command | Unmodified machine evidence |

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

## Reason codes and remediation

| Reason code | Meaning | Remedy |
|---|---|---|
| `CODEGRAPH_NOT_FOUND` | Binary is unavailable | Install CodeGraph, then configure Codex |
| `CODEGRAPH_MCP_NOT_CONFIGURED` | Codex config has no CodeGraph MCP table | Run the reviewed CodeGraph install command |
| `CODEGRAPH_WRONG_PROJECT` | Index belongs to another repository | Controller initializes the exact owning repo once |
| `CODEGRAPH_STALE` | Indexed state differs from the worktree | Controller runs one exact-repo `codegraph sync` and rechecks |
| `CODEGRAPH_INDEX_INVALID` | Index is missing/incomplete/reindex-required | Controller chooses exact-repo `init` or `sync` once |
| `CODEGRAPH_SENTINEL_MISMATCH` | Current expected source was not found | Check index, query, path, and revision |
| `SEMBLE_NOT_FOUND` | CLI capability is unavailable | Install Semble and configure MCP |
| `SEMBLE_MCP_NOT_CONFIGURED` | Codex config has no Semble MCP table | Run the reviewed Semble MCP command |
| `SEMBLE_SCOPE_CONTAMINATION` | Search returned unsafe/out-of-repo paths | Stop; repair repository scope |
| `SEMBLE_SENTINEL_MISMATCH` | Expected live source was not returned | Refine the semantic sentinel or repair search state |
| `RTK_NOT_FOUND` | `rtk` is unavailable | Install it and initialize Codex guidance |
| `RTK_OUTPUT_MISMATCH` | Wrapped Git identity differs from raw Git | Do not use rtk as evidence until repaired |
| `RTK_FALSE_GREEN` | A failing command became successful | Hard stop; rtk cannot support acceptance |
| `AUTO_REPAIR_NO_PROGRESS` | One allowlisted repair did not shrink the failure set | Open circuit; return `MAINTENANCE_REQUIRED`, do not retry/spawn |
| `AUTO_REPAIR_CIRCUIT_OPEN` | The same stable failure already consumed its repair budget | Do not retry; change the underlying state or route to the named external owner |
| `EXTERNAL_TOOL_REPAIR_REQUIRED` | Package/config/system action is needed | Return the exact owner/action; do not relabel as model infra failure |

Use `--advisory` only for diagnosis. Advisory failures return `degraded`, never
`ready`, and cannot satisfy a formal gate.

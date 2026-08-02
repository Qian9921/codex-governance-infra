# Mandatory toolchain: readiness before routing

This document is the operational contract for CodeGraph, Semble, and `rtk`.
The short path is in [README.md](../README.md).

## The two gates

1. `tool-preflight.v16` proves the tools are current and trustworthy for one
   exact repository identity.
2. `tool-usage.v16` proves every task-declared route was actually used and
   produced a task-relevant, receipt-backed result.

A binary on `PATH` is not readiness. One irrelevant call to each tool is not
usage compliance.

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

# Only when the repository is not initialized and indexing is authorized
codegraph init .

# After structural edits, when synchronization is authorized
codegraph sync .
```

Indexing and synchronization are persistent mutations. A doctor run never
performs them.

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

## Reason codes and remediation

| Reason code | Meaning | Remedy |
|---|---|---|
| `CODEGRAPH_NOT_FOUND` | Binary is unavailable | Install CodeGraph, then configure Codex |
| `CODEGRAPH_MCP_NOT_CONFIGURED` | Codex config has no CodeGraph MCP table | Run the reviewed CodeGraph install command |
| `CODEGRAPH_WRONG_PROJECT` | Index belongs to another repository | Stop; point at the owning repo |
| `CODEGRAPH_STALE` | Indexed state differs from the worktree | Authorize and run `codegraph sync .` |
| `CODEGRAPH_SENTINEL_MISMATCH` | Current expected source was not found | Check index, query, path, and revision |
| `SEMBLE_NOT_FOUND` | CLI capability is unavailable | Install Semble and configure MCP |
| `SEMBLE_MCP_NOT_CONFIGURED` | Codex config has no Semble MCP table | Run the reviewed Semble MCP command |
| `SEMBLE_SCOPE_CONTAMINATION` | Search returned unsafe/out-of-repo paths | Stop; repair repository scope |
| `SEMBLE_SENTINEL_MISMATCH` | Expected live source was not returned | Refine the semantic sentinel or repair search state |
| `RTK_NOT_FOUND` | `rtk` is unavailable | Install it and initialize Codex guidance |
| `RTK_OUTPUT_MISMATCH` | Wrapped Git identity differs from raw Git | Do not use rtk as evidence until repaired |
| `RTK_FALSE_GREEN` | A failing command became successful | Hard stop; rtk cannot support acceptance |

Use `--advisory` only for diagnosis. Advisory failures return `degraded`, never
`ready`, and cannot satisfy a formal gate.

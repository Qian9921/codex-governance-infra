# Architecture

The package has four layers: normative policy and templates; versioned JSON contracts;
privacy-safe hook helpers; and descriptor-relative installer/verifier boundaries. A
persistent parent owns each mission. Parent pre-dispatch validation plus SubagentStart
ACTIVE-MISSION-LOCK and post-result validation enforce nested delegation because
collaboration spawn is not assumed PreToolUse-observable. Spark is a specialist identity,
not a capability restriction.

## Connected delegation enforcement

`codex/hooks/delegation_contract.py` is both validator and CLI bridge. Every CLI transition
enters `locked_snapshot()`, acquires the state lock before reading packet or state, opens each
regular file with `O_NOFOLLOW`, reads packet bytes exactly once, and derives raw packet hash,
parsed packet, and canonical mission hash from that same byte sequence. `delegation-state.v3`
has exact top-level and nested key sets; state publication uses a descriptor-relative temporary
and atomic `replaceat` operation. Before pre-dispatch, SubagentStart, PreToolUse, or result
ingestion, the locked snapshot revalidates canonical repository root, nonzero current HEAD,
packet hash, mission hash, lease, and the complete state shape. Collaboration spawning is not
claimed to be observable by PreToolUse; enforcement is bounded to the parent bridge,
SubagentStart input, and result ingestion.

### Delegation state and evidence

Delegation state is parent-owned, flock-protected, descriptor-relative, and fsynced under a
0700 state root with a 0600 lock/state file. The persisted state machine is
`REGISTERED -> STARTED -> RETRY_AVAILABLE -> ACCEPTED` or `TERMINAL_REJECTED`; first
contamination is written before the rejecting process exits and only one distinct correlated
clean retry can be consumed. A second contamination is terminal only when its retry number,
attempt ID, and one-entry transcript naming the prior contamination are valid. Malformed
second inputs leave state bytes and the active lease unchanged. Result counts and the
counterexample matrix are derived from observed test objects, never constants; unknown,
skipped, failed, or duplicate cases make the evidence red.

The runner also exposes bounded test-only bug reintroductions. `bug_r1_001`, `bug_r1_003`,
`bug_r1_004`, `bug_r1_006`, `bug_r1_007`, and `bug_r1_008` each record their finding, changed
case/adapter field, target case denominator, and observed RED arithmetic; the retry mutation
monkeypatches only the in-memory production ingest dependency. Existing
`duplicate_semantic`, `expected_flip`, and `boundary_downgrade` probes remain available. No
mutation branch is read by production hooks or installer code.

## Native PreTool boundary

`codex/hooks.json` registers `Read`, `Grep`, `Glob`, `Bash`, `apply_patch`, `Edit`, `Write`, and
MCP events. Delegated PreToolUse uses an explicit closed schema map: native reads/writes carry
exact path/file_path fields, Semble reads carry the repository field, and CodeGraph reads carry
`projectPath`. Every requested path is canonicalized and lstat-checked without following
symlinks and must fall inside the packet lease; pathless, conflicting, unknown MCP, shell,
Git/GitHub, review, approval, merge, and external actions are denied. SessionStart and
SubagentStart invoke the same bridge before context is emitted.

## Installer transaction

`scripts/install-governance.py` opens canonical destination/source ancestors one component at a
time with `O_DIRECTORY|O_NOFOLLOW|O_CLOEXEC`. Package reads, managed targets, backup files,
state, temporary files, replacement, unlink, and cleanup use pinned directory descriptors;
regular files are checked with `fstat` and modes are applied with `fchmod`. The complete manifest-
owned ledger is validated before mutation. Rollback prevalidates every backup type/hash/mode and
both installed bytes and mode before the first restore. Backup/state recovery assets are deleted
only after exact restoration; any restore/roll-forward failure leaves them available for a later
operator.

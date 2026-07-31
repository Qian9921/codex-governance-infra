# Architecture

The package has four layers: normative policy and templates; JSON contracts; privacy-safe hook helpers; and the allowlisted installer/verifier. A persistent parent owns each mission. Parent pre-dispatch validation plus SubagentStart ACTIVE-MISSION-LOCK and post-result validation enforce nested delegation because collaboration spawn is not assumed PreToolUse-observable. Spark is a specialist identity, not a capability restriction.

## Connected delegation enforcement

`codex/hooks/delegation_contract.py` is both validator and CLI bridge. `pre-dispatch` validates a packet, computes a canonical mission hash, and records active sibling leases under a configurable task-owned state root. `subagent-start` consumes `CODEX_DELEGATION_PACKET_SHA256` and fails closed on missing/wrong identity. `ingest-result` validates typed evidence and updates the parent-owned attempt ledger. Collaboration spawning is not claimed to be observable by PreToolUse; enforcement is bounded to the parent bridge, SubagentStart input, and result ingestion.

### Delegation state and evidence

Delegation state is parent-owned, flock-protected, atomically replaced, and fsynced under a 0700
state root with 0600 files. The persisted state machine is `REGISTERED -> STARTED ->
CONTAMINATED_RECORDED/RETRY_AVAILABLE -> ACCEPTED` or `TERMINAL_REJECTED`; contamination is
written before the rejecting process exits and only one distinct clean retry can be consumed.
Result counts and the counterexample matrix are derived from observed test objects, never constants;
unknown, skipped, failed, or duplicate cases make the evidence red.

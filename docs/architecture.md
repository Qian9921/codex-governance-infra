# Architecture

The package has four layers: normative policy and templates; JSON contracts; privacy-safe hook helpers; and the allowlisted installer/verifier. A persistent parent owns each mission. Parent pre-dispatch validation plus SubagentStart ACTIVE-MISSION-LOCK and post-result validation enforce nested delegation because collaboration spawn is not assumed PreToolUse-observable. Spark is a specialist identity, not a capability restriction.

## Connected delegation enforcement

`codex/hooks/delegation_contract.py` is both validator and CLI bridge. `pre-dispatch` validates a packet, computes a canonical mission hash, and records active sibling leases under a configurable task-owned state root. `subagent-start` consumes `CODEX_DELEGATION_PACKET_SHA256` and fails closed on missing/wrong identity. `ingest-result` validates typed evidence and updates the parent-owned attempt ledger. Collaboration spawning is not claimed to be observable by PreToolUse; enforcement is bounded to the parent bridge, SubagentStart input, and result ingestion.

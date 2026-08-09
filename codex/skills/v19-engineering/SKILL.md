---
name: v19-engineering
description: Execute a non-STRICT implementation or research-engineering mission with V19 ownership, reuse, recovery, evidence, and review boundaries. Use for building, fixing, refactoring, tool repair, data runs, or repository changes; do not use for explanation-only tasks, formal GitHub delivery, or STRICT proof work.
---

# V19 Engineering

## Freeze the slice

1. State the outcome, owner, producer/consumer boundary, operating domain,
   invariants, non-goals, rollback, evidence budget, usage budget, and stop
   conditions.
2. Choose `QUICK` only for explanation, inventory, documentation, or reversible
   mechanics; otherwise use `STANDARD`. Upgrade to `$v19-strict-proof` when a
   strict trigger appears.
3. Record the pre-mortem: likely failure, affected boundary, and cheapest check
   that would turn red.

## Discover and execute

1. Find the owning implementation before creating code. Use Semble for unknown
   semantics or similar code, revision-matching CodeGraph for known structure or
   impact, `rg` for exact text, and `rtk` for shell output.
2. For a meaningful abstraction, record `REUSE`, `EXTEND`, or `NEW`. A `NEW`
   owner needs a real consumer, valid dependency direction, focused tests, and
   less complexity than duplication.
3. Give Luna exclusive paths and the smallest executable slice. Use at most two
   concurrent writers and one Git owner. Preserve unrelated user work.
4. When a required capability fails, do not repeat a no-progress strategy.
   Continue a materially different safe recovery strategy until the dependent
   slice exercises it. Optional failures become owned repair debt.

## Prove and close

1. Run affected checks first. Add a deterministic synthetic case and a
   representative real-data slice when the capability has both domains.
2. Reject parity claims with an unknown denominator, skipped required case,
   NaN/Inf, stale identity, or missing oracle.
3. Send one compact exact-snapshot packet to `sol_reviewer` when `STANDARD`
   review is required. Stable findings return to the same reviewer delta-only.
4. Finish only when the frozen envelope is met, blocking findings are closed,
   and no required work remains. Use `$v19-github-delivery` for publication.

## Context discipline

- Read only task-relevant contracts and references.
- Keep raw logs and large evidence in artifacts; return hashes, denominators,
  decisive excerpts, and paths.
- Do not load the V16 strict corpus unless the task actually upgrades to
  `STRICT`.

---
name: v19-engineering
description: V21 STANDARD execution with stable v19-engineering ID: bounded ownership, reuse, recovery, evidence, and review for implementation or data work; not explanation, GitHub delivery, or STRICT proof.
---

# V21 STANDARD Engineering (stable ID: v19-engineering)

## Freeze the slice

1. State the outcome, owner, producer/consumer boundary, operating domain,
   invariants, non-goals, rollback, evidence budget, usage budget, and stop
   conditions.
2. Choose `QUICK` only for explanation, inventory, documentation, or reversible
   mechanics; otherwise use `STANDARD`. Upgrade to `$v19-strict-proof` when a
   strict trigger appears.
3. Record the pre-mortem: likely failure, affected boundary, and cheapest check
   that would turn red.

4. For `STANDARD`, freeze time/evidence budgets and map blockers to acceptance,
   user impact, likelihood, recoverability, repair cost, and complexity cost.
   Theoretical counterexamples default to `FOLLOW_UP` without that mapping;
   documented bounded limitations may be a legal completion state.

## Discover and execute

1. Find the owner first. Use Semble for unknown/similar code, the compiler
   semantic gateway for known C++/Python structure/impact, and bounded exact
   evidence for source/Git/compiler/build/test/benchmark facts. CodeGraph, `rg`,
   and `rtk` are explicit V16 `STRICT` compatibility tools only.
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
   review is required. Allow one initial review and at most one delta review;
   a third round requires explicit replan.
4. Finish only when the frozen envelope is met, blocking findings are closed,
   and no required work remains. Use `$v19-github-delivery` for publication.

5. Run decision-changing checks. If recovery logic grows beyond the feature,
   simplify and replan.

## Context discipline

- Read only task-relevant contracts/references; keep raw logs in artifacts and
  return hashes, denominators, decisive excerpts, and paths.
- Do not load the V16 strict corpus unless the task actually upgrades to
  `STRICT`.

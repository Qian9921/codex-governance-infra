# V16 Productivity Acceptance Lock

This lock is the immutable starting contract for the V16 productivity milestone.
It is intentionally separate from the paused `codex/v15-milestone-spark-infra`
line. A changed base/head/tree, scope, denominator, or operating domain requires
a new lock and a fresh Independent Sol review.

- **Objective:** maximize engineering throughput and first-pass correctness while
  preserving Google-grade evidence and identity separation.
- **Owner/writer:** Qian9921 author lane; one persistent GPT-5.6 Luna writer with
  the permissions in the mission packet.
- **Reviewer:** one fresh zero-context GPT-5.6 Sol, `fork_turns=none`,
  report-only; Sol is architecture/final-review only and never an iterative
  debugger.
- **Inner audit:** exactly three bounded zero-context GPT-5.3 Spark high
  report-only audits (DAG/state, evidence/privacy/identity, metrics/PR trace).
  Their sanitized identities, findings, and dispositions are recorded in
  `contracts/v16_spark_audit_closure.json`; local tooling validates packets but
  does not fake backend spawning.
- **Base identity:** `e18439c8dfe01d901895efd09b8b73b6842327a9` /
  tree `1de79a7c48e6c66f167be54ca9cf387310149f80`.
- **Operating domain:** Linux user-level Python >=3.9, stdlib-only, portable
  fresh clone, private repository, foreground execution only.
- **Non-goals:** PR#1 residuals; global `~/.codex` deployment; merge/push to
  `main`; GitHub calls from local renderers; Astverd/Fusion work; credentials,
  raw prompts, sessions, tokens, private logs, or model-name capability bans.
- **Evidence gate:** every denominator is known and non-zero; exact arithmetic
  and current identity are mandatory. Any skip, xfail, unknown, stale, privacy
  red, copied count, missing log, or head drift is blocking.
- **Readiness order:** `DRAFT` → `COUNTEREXAMPLES_FROZEN` →
  `BASELINE_REPRODUCED` → `IMPLEMENTING` → `INNER_AUDIT_COMPLETE` →
  `LOCAL_READY` → `FRESH_READY` → `REVIEW_READY`.
- **Rollback:** remove only V16 files/docs and restore the pre-V16 manifest;
  never alter the paused PR#1 branch or live user state.

The lock is a source artifact, not a claim of implementation completion. A final
`APPROVE`/`GO` decision is impossible until the exact current head, evidence
bundle, and fresh Independent Sol review satisfy the contract.

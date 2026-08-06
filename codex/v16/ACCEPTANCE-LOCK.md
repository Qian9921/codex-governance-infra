# V16 Productivity Acceptance Lock (V18 public compatibility fixture)

This file is a synthetic legacy-V16 compatibility fixture shipped in the
public V18 package. Identities and hashes below are placeholders; they do not
describe a live run, private repository, or private deployment.

This lock is the immutable starting contract for the V16 productivity milestone.
It is intentionally separate from older compatibility lines. A changed
base/head/tree, scope, denominator, operating domain, or review
policy requires a new lock and an `escalated_fresh` Independent review.

- **Objective:** preserve correctness/evidence validity as hard gates, minimize
  time to a correct decision/merge, then minimize token/call cost.
- **Owner/writer:** your-developer-account author lane; one persistent GPT-5.6 Luna writer with
  the permissions in the mission packet.
- **Review risk:** `high`, trigger `hook_reviewer_model_routing`; required
  evidence stages `targeted`, `full`, `fresh`.
- **Reviewer:** one fresh GPT-5.6 Sol xhigh `independent_clean_room` task,
  `fork_turns=none`, report-only, with a curated hash-bound packet; Sol is never
  an iterative debugger.
- **Inner audit:** exactly three bounded zero-context GPT-5.3 Spark high
  report-only audits (DAG/state, evidence/privacy/identity, metrics/PR trace).
  Their sanitized identities, findings, and dispositions are recorded in
  `contracts/v16_spark_audit_closure.json`; local tooling validates packets but
  does not fake backend spawning.
- **Base identity:** `0000000000000000000000000000000000000000` /
  tree `0000000000000000000000000000000000000000`.
- **Operating domain:** Linux user-level Python >=3.9, stdlib-only, portable
  public fresh clone, foreground execution only.
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

The lock is a source artifact, not a claim of implementation completion. A
final `APPROVE`/`GO` decision is impossible until the exact current identity,
evidence bundle, packet-bound findings, and risk-routed Independent review
satisfy the contract.

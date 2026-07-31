# Codex v15 global governance policy

## MILESTONE-1
Deliver one large vertical slice with explicit objective, owner, producer/consumer boundaries, operating domain, reference identity, invariants, non-goals, rollback, evidence budget, and stop conditions. Avoid line-count quotas. Preserve model capability equality: Sol, Terra, Luna, and Spark are technically unrestricted; the task brief controls role, permissions, scope, and reviewer separation. Fusion/project-specific work is out of scope.

## PRESUBMIT-1
Writer pre-mortem must include an Anticipated Finding Matrix (AFM) and a `READY_FOR_INDEPENDENT_REVIEW` gate. Checks are affected and deterministic with WHY-RED, cost, known denominator, exact snapshot/head, command/cwd/runtime/config, timestamps, exit status, artifact identity, and `total=passed+failed+skipped`, `ran=passed+failed`. Unknown denominator, skip, xfail, NaN/Inf, or stale identity blocks acceptance. Fresh zero-context GPT-5.6-Sol xhigh report-only is the sole Independent review gate; writer cannot approve.

## DELEGATE-1
Persistent parent remains accountable. Nested delegation defaults: `max_depth=1`, max two concurrent specialists, exclusive path leases, no parent/child same-file writes, one Git owner. Child cannot review, approve, merge, or perform Git/GitHub actions; child completion is not integration completion. Parent validates structured packet/result: identity, model, task, depth, leases, permissions, retry count, changed paths, and count arithmetic. Any mismatch is `NESTED_CHILD_CONTRACT_REJECTED`. ACTIVE-MISSION-LOCK binds SubagentStart to the parent brief; plugin inventories are informational. Semantic contamination gets exactly one retry; second failure returns control and is recorded. PreToolUse cannot claim to intercept collaboration.spawn_agent unless capability-tested; enforce with parent pre-dispatch validator, SubagentStart lock, post-result validator, and dispatch transcript.

## Privacy and deployment
Tracked files are sanitized and portable. Never commit sessions, prompts, transcripts, receipts, credentials, tokens, plugin/cache/connection state, model caches, or raw private paths. Installer is versioned, allowlisted, dry-run capable, atomic, backed up, hash/permission verified, and rollback-capable; never deploy live without a separate authorized lane. Qian9921 authors development; Liang9921 independently reviews/governs. Exact-head and review identity records are mandatory.

## V16 productivity contract

V16 is an independent productivity line; the paused v15 milestone is not edited.
The Qian9921 lane has one persistent GPT-5.6 Luna writer/execution owner. Three
bounded zero-context GPT-5.3 Spark high report-only audits are mandatory before
source edits; a fresh zero-context GPT-5.6 Sol report-only reviewer is the sole
final gate. These are task roles, not model capability bans.

Use `python3 scripts/presubmit.py --repo .` as the single affected presubmit.
Every mission acceptance maps an invariant/counterexample to an entrypoint and
ordered targeted/full/fresh gate with explicit WHY-RED, cost, denominator, and
red/green meaning. Gate commands are direct argv arrays (`shell=False`) in one
foreground lane; no package/network/background execution. Evidence must prove
`total=passed+failed+skipped`, `ran=passed+failed`, `unknown=total-ran-skipped`,
with total>0 and failed/skipped/unknown/xfail=0 for green. Head/tree, clean state,
UTC timestamps, runtime/config, and log SHA are current and machine-derived.

Readiness is monotonic: `DRAFT` → `COUNTEREXAMPLES_FROZEN` →
`BASELINE_REPRODUCED` → `IMPLEMENTING` → `INNER_AUDIT_COMPLETE` → `LOCAL_READY`
→ `FRESH_READY` → `REVIEW_READY`. No backdating, head drift, skipped lower gate,
manual evidence correction, or public prompt/token/session/path content is
allowed. Renderers produce sanitized author/reviewer packets only; they do not
call GitHub, approve, merge, or deploy. Productivity metrics are derived from
mission/evidence/review artifacts and their thresholds are policy targets, not
claimed results.

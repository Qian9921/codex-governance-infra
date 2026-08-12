# Adaptive brief templates

Use the smallest template that changes a decision. `QUICK` work may need only
the first four mission lines. `STRICT` work compiles the existing V16 JSON
contracts after this human-readable brief is frozen.

## Mission card

```text
Outcome:
Owner:
Profile: QUICK | STANDARD | STRICT
Scope and owning repository:
Producer -> consumer:
Operating domain / reference identity:
Must remain true:
Non-goals:
Rollback:
Evidence and usage budget:
Stop when:
```

Choose a vertical slice by coupling, independent usefulness, rollback, and
validation cost. Do not use line quotas or repeatedly raise a frozen target.

## Model routing card

```text
Planner / architect: Sol
Execution lead: Luna
Spark tasks: <0..N bounded independent tasks>
Terra bridge: <TERRA_REPLAN | TERRA_TRIAGE | none>
Terra continuity fallback: <yes only if Luna unavailable | no>
Bridge budgets: <duration/tool calls/output tokens; direct return to Luna>
Independent reviewer: Sol
Max writers / review calls / model calls / tokens:
```

Record requested model, actual model, and fallback reason. One Git owner
integrates the work. Parallel writers require exclusive path leases.

## Tool intent card

```text
Unknown semantics or similar implementation: Semble | N/A
Known C++/Python semantics, dependency, or impact: semantic gateway | N/A
Exact source/Git/compiler/build/test/benchmark fact: bounded exact evidence | N/A
Essential decision if a preferred tool fails:
```

Calls must be task-relevant. Verify Semble and compiler/provider/repository
identity before relying on their answers. A missing gateway provider routes one
named fact to bounded exact evidence; it does not stop unrelated STANDARD work.
The old CodeGraph/rg/rtk routes and receipt-backed enforcement are reserved for
explicit `STRICT` compatibility missions.

## Reuse decision

```text
Intent:
Existing candidates:
Decision: REUSE | EXTEND | NEW
Contract match or mismatch:
Owning module and dependency direction:
Evidence references:
```

Use this only for meaningful new abstractions. Prefer composition and existing
domain ownership; do not build speculative frameworks.

## Evidence card

```text
Acceptance claim:
Counterexample / WHY-RED:
Exact snapshot, runtime, config, reference, and data identity:
Affected check and known denominator:
Synthetic sample: result | N/A
Representative real-data sample: result | N/A
Pass condition / tolerance:
Cost and reusable evidence hash:
```

Required arithmetic is `total=passed+failed+skipped+unknown` and
`ran=passed+failed`. A required unknown, skip, xfail, NaN/Inf, stale identity,
or missing oracle cannot support a green parity claim.

## Review packet

```text
Context: independent_clean_room | delta_continuation | escalated_fresh
Objective and non-goals:
Exact base/head/tree/diff or snapshot:
Acceptance envelope and risk:
Reuse decision:
Affected evidence and denominators:
Known limitations:
Prior findings and author dispositions:
Requested verdict: APPROVE | REQUEST_CHANGES
```

The initial reviewer receives the compact clean-room packet, not the author's
full chat. Stable fixes return to the same reviewer with only finding lineage,
exact delta, new evidence, and direct boundaries. A fresh reviewer requires an
explicit escalation trigger.

Review feedback uses `BLOCKING`, `SHOULD_FIX`, `NIT`, `QUESTION`, or
`FOLLOW_UP`. Only the first category prevents approval.

## GitHub trace

```text
Author: your-developer-account
Reviewer / approver / merger: your-reviewer-account
PR objective:
Evidence summary:
Findings and dispositions:
Limitations / follow-ups:
Exact reviewed head:
```

## Knowledge deposit

```text
Decision or root cause:
Reusable abstraction / fixture / test:
Reference and data identity:
Accepted limitation:
Follow-up owner:
```

Do not retain raw prompts, sessions, transcripts, credentials, receipts, or
private paths as project knowledge.

## Strict compatibility

The V16 modules under `codex/v16/` remain the opt-in machine-verifiable engine
for high-risk and release work. `STRICT` missions may compile their full mission,
FAST/CANDIDATE/FINAL evidence, review-runtime, tool-preflight, usage, and
enforcement artifacts. Adaptive profiles do not fabricate those artifacts and
do not treat their absence as a correctness failure.

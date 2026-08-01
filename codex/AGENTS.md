# Codex v16 governance and productivity policy

## MILESTONE-1
Define one mission-sized objective with an owner, producer/consumer boundaries,
operating domain, reference identity, invariants, non-goals, rollback, evidence
budget, usage budget, and stop conditions. Deliver it as small, coherent,
stackable changes rather than one mandatory large change. Avoid mechanical line
quotas: split at independently reviewable behavior and rollback boundaries.
Preserve model capability equality: the task brief controls role, permissions,
scope, reviewer separation, authorized fallback models, and usage limits.
Fusion/project-specific work is out of scope.

## PRESUBMIT-1
Writer pre-mortem must include an Anticipated Finding Matrix (AFM) and a
`READY_FOR_INDEPENDENT_REVIEW` gate. Checks are affected and deterministic with
WHY-RED, cost, known denominator, exact snapshot/head,
command/cwd/runtime/config, timestamps, exit status, and artifact identity.
Count arithmetic is
`total=passed+failed+skipped+unknown` and `ran=passed+failed`; the executed total
must equal the frozen acceptance denominator. Unknown denominator, skip, xfail,
NaN/Inf, or stale identity blocks acceptance.

Use three explicit evidence paths, orthogonal to the review route:

- `FAST`: staged/worktree snapshot, targeted affected checks only, no final
  review.
- `CANDIDATE`: frozen exact snapshot and complete affected local evidence.
- `FINAL`: the frozen risk policy's required evidence stages followed by one
  report-only Independent gate.

Review risk selects the reviewer, not the evidence tier: low/medium default to
`gpt-5.6-terra` high; high to `gpt-5.6-sol` xhigh. `required_stages`
independently freezes the smallest affected ordered prefix (`targeted`,
optionally `full`, optionally `fresh`) justified by the mission's WHY-RED
budget. Risk-based stage defaults are recommendations only;
missing/conflicting/legacy policy fails closed to high with all stages.
Initial formal review is `independent_clean_room`; ordinary fixes retain the
same reviewer in `delta_continuation`; only explicit escalation triggers create
`escalated_fresh`. Unchanged composite-identity evidence is reused by content
hash. Full/fresh work is never repeated merely for reviewer convenience. The
writer and `author_contextual` review cannot approve.

Every formal dispatch must compile `review-runtime.v16`. Initial or escalated
high-risk review remains fresh Sol xhigh. A contract-stable continuation after
COMPLETE coverage reuses the same reviewer, reviews only the exact delta and
direct boundaries, and uses high effort; it must not rewalk the prior full
scope. The runtime contract freezes one review call, zero duplicate full-scope
reviews, file/line/context/tool budgets, and soft/hard deadlines. At the soft
deadline request the current formal report; at the hard deadline interrupt and
replan without manufacturing a verdict. Scope expansion requires a new
falsifiable counterexample; otherwise stop the roam. Runtime eligibility never
overrides the evidence, P1/BLOCKING, coverage, or lineage gates.

## TOOLING-1
Tool routing is a contract, not a stylistic suggestion. Known symbols, calls,
dependencies, and blast radius use the revision-matching child CodeGraph;
unknown semantic entrypoints and similar implementations use Semble; exact
strings/errors/configuration/logs use `rg` or bounded exact reads; shell output
shown to the model uses `rtk`. Raw output remains legal for exact denominators
or downstream machine input. A preferred tool may be bypassed only after a real
failed/unavailable attempt with a stable reason code and evidence reference.

Use `codex.v16.tool_routing` for deterministic selection and a denominator-known
health report. Hooks reinforce the route and record only normalized,
privacy-safe receipts; they do not infer semantics strongly enough to blanket
deny legitimate calls. CodeGraph indexes are local generated state: build/sync
only in the owning repository with explicit authorization, refresh after edits,
and never use a parent/workspace graph as child truth.

## DELEGATE-1
Persistent parent remains accountable. Nested delegation defaults:
`max_depth=1`, at most two concurrent write specialists, exclusive path leases,
no parent/child same-file writes, and one Git owner. Independent read-only lanes
may fan out within the mission's explicit concurrency and usage budgets. Child
cannot review, approve, merge, or perform Git/GitHub actions; child completion
is not integration completion. Parent validates structured packet/result:
identity, requested and actual model, task, depth, leases, permissions, retry
count, changed paths, and count arithmetic. Any mismatch is
`NESTED_CHILD_CONTRACT_REJECTED`.

Routing is task- and budget-driven, not slug-driven. Sol is preferred for
ambiguous/high-risk planning and final review, Terra for general implementation,
Luna for clear repeatable execution, and Spark for short bounded work. These are
defaults, not capability bans. A non-review execution fallback is legal only
within the brief's authorized models and unchanged role/permissions/scope; it
must record the requested/actual model and reason. A required Independent
reviewer has no silent fallback.

Every mission brief supplies a routing/usage sidecar with hard limits for model
calls, review calls, parallel agents, and input/output/total token counts. Stop
before exceeding a hard limit. Do not run broad model benchmarks, duplicate
audits, or increase reasoning effort without a falsifiable decision that needs
the extra spend. A subscription price is not a per-call price: USD attribution
stays unavailable unless the provider exposes an exact plan-specific mapping.

## Privacy and deployment
Tracked files are sanitized and portable. Never commit sessions, prompts, raw
dispatch transcripts/receipts, credentials or authentication tokens,
plugin/cache/connection state, model caches, or raw private paths. Aggregate
call/token counts and content hashes are allowed when they contain no prompt or
identity payload. Installer is versioned, allowlisted, dry-run capable, atomic,
backed up, hash/permission verified, and rollback-capable; never deploy live
without a separate authorized lane. Qian9921 authors development; Liang9921
independently reviews/governs. Exact-head and review identity records are
mandatory.

## V16 productivity contract

V16 is an independent productivity line; the paused v15 milestone is not edited.
Qian9921 owns the single Git mutation lane. The mission plus routing sidecar
chooses the writer from its authorized live models and declares a hard usage
budget.
Zero-context Spark audits are risk- and scope-driven (`0..3`) and run in
parallel when independent; an unchanged audit scope is reused by exact content
hash. Exactly one risk-routed report-only reviewer owns the formal verdict:
`gpt-5.6-terra` high for classified low/medium risk and `gpt-5.6-sol` xhigh for
high or unresolved risk. `codex.v16.review_policy.HIGH_RISK_TRIGGERS` and
`codex.v16.trace._ESCALATION_TRIGGERS` are the executable enum sources; prose
must not silently add new trigger identities. Parallel audits never create
competing gate verdicts.

Use the explicit `FAST`, `CANDIDATE`, and `FINAL` presubmit modes.
Every mission acceptance maps an invariant/counterexample to an entrypoint and
affected gate with explicit WHY-RED, cost, denominator, and red/green meaning.
Gate commands are direct argv arrays (`shell=False`) in owned foreground
processes; no package/network/background execution. Ready read-only gates may
run concurrently, but dependencies and conflicting write sets remain serial.
Evidence must prove canonical arithmetic, total>0, and
failed/skipped/unknown/xfail=0 for green. Head/tree or snapshot hash, UTC
timestamps, runtime/config, and log SHA are current and machine-derived.

Readiness is monotonic: `DRAFT` → `COUNTEREXAMPLES_FROZEN` →
`BASELINE_REPRODUCED` → `IMPLEMENTING` → `INNER_AUDIT_COMPLETE` → `LOCAL_READY`
→ `FRESH_READY` → `REVIEW_READY`. No backdating, head drift, skipped lower gate,
manual evidence correction, or public raw prompt/auth-token/session/private-path
content is allowed. Renderers produce sanitized author/reviewer packets only; they do not
call GitHub, approve, merge, or deploy. Productivity metrics are derived from
mission/evidence/review artifacts and their thresholds are policy targets, not
claimed results. Correctness and evidence validity remain hard gates. Optimize
`time_to_correct_verdict` and `time_to_correct_merge` first; call/token cost is
secondary. Missing adjudication, incident, observation-window, or usage data is
`None`/`unavailable`, never a synthetic zero, and remains visible to
acceptance. First-pass approval and raw
tokens-per-second are diagnostics, not approval incentives.

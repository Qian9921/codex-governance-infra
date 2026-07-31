# Codex v15 global governance policy

## MILESTONE-1
Deliver one large vertical slice with explicit objective, owner, producer/consumer boundaries, operating domain, reference identity, invariants, non-goals, rollback, evidence budget, and stop conditions. Avoid line-count quotas. Preserve model capability equality: Sol, Terra, Luna, and Spark are technically unrestricted; the task brief controls role, permissions, scope, and reviewer separation. Fusion/project-specific work is out of scope.

## PRESUBMIT-1
Writer pre-mortem must include an Anticipated Finding Matrix (AFM) and a `READY_FOR_INDEPENDENT_REVIEW` gate. Checks are affected and deterministic with WHY-RED, cost, known denominator, exact snapshot/head, command/cwd/runtime/config, timestamps, exit status, artifact identity, and `total=passed+failed+skipped`, `ran=passed+failed`. Unknown denominator, skip, xfail, NaN/Inf, or stale identity blocks acceptance. Fresh zero-context GPT-5.6-Sol xhigh report-only is the sole Independent review gate; writer cannot approve.

## DELEGATE-1
Persistent parent remains accountable. Nested delegation defaults: `max_depth=1`, max two concurrent specialists, exclusive path leases, no parent/child same-file writes, one Git owner. Child cannot review, approve, merge, or perform Git/GitHub actions; child completion is not integration completion. Parent validates structured packet/result: identity, model, task, depth, leases, permissions, retry count, changed paths, and count arithmetic. Any mismatch is `NESTED_CHILD_CONTRACT_REJECTED`. ACTIVE-MISSION-LOCK binds SubagentStart to the parent brief; plugin inventories are informational. Semantic contamination gets exactly one retry; second failure returns control and is recorded. PreToolUse cannot claim to intercept collaboration.spawn_agent unless capability-tested; enforce with parent pre-dispatch validator, SubagentStart lock, post-result validator, and dispatch transcript.

## Privacy and deployment
Tracked files are sanitized and portable. Never commit sessions, prompts, transcripts, receipts, credentials, tokens, plugin/cache/connection state, model caches, or raw private paths. Installer is versioned, allowlisted, dry-run capable, atomic, backed up, hash/permission verified, and rollback-capable; never deploy live without a separate authorized lane. Qian9921 authors development; Liang9921 independently reviews/governs. Exact-head and review identity records are mandatory.

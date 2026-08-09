---
name: v19-github-delivery
description: Deliver a V19 change through formal review, GitHub PR, approval, expected-head merge, and optional personal-overlay deployment. Use when the user requests review, publication, PR handling, merge, release, or global personal installation.
---

# V19 GitHub Delivery

## Freeze identity

Record remote, base SHA/tree, head SHA/tree, direct-parent lineage, merge base,
two-dot scope, three-dot scope, changed paths, and working-tree status. A head
change invalidates the verdict. Do not publish prompts, sessions, credentials,
receipts, private paths, or raw private data.

## Review once

Send one fresh read-only `sol_reviewer` a compact clean-room packet: exact
snapshot/diff, objective, invariants, non-goals, reference identity, affected
evidence, known limitations, and prior dispositions. Findings use
`BLOCKING|SHOULD_FIX|NIT|QUESTION|FOLLOW_UP` and
`INTRODUCED|PRE_EXISTING` with exact locations and falsifiable counterexamples.

Stable fixes return to the same reviewer with only finding lineage, exact delta,
disposition, new evidence, and directly affected boundaries. Escalate to a new
reviewer only for material contract/risk/scope drift, a large rewrite,
incomplete coverage, a new P1 counterexample, review-governance changes, or two
non-converging rounds.

## Publish and integrate

1. `your-developer-account` authors, pushes, opens the PR, and responds to every
   finding.
2. `your-reviewer-account` independently comments or reviews the exact head,
   approves, and merges with expected-head protection.
3. Keep objective, evidence summary, findings, dispositions, limitations, and
   verdict in the PR.
4. Verify the merge tree equals the reviewed tree. If installing the personal
   overlay, use manifest dry-run, atomic install, exact installed hashes, a
   rollback path, and a fresh post-restart process where a service is involved.

Never call publication complete before the exact reviewed head is integrated
and required deployment evidence is current.

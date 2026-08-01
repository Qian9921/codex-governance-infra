# V16 contract registry

`schema_registry.v16.json` is the portable field inventory. Each entry names the
stdlib validator that owns strict types, uniqueness, arithmetic, privacy, and
cross-object linkage, together with a `validation_mode`:

- `standalone` means the listed fields can be checked without another artifact.
- `source-bound` means the validator must recompute values from the listed
  `external_inputs` source bundle; a packet alone is not an acceptance claim.
- `caller-bound` means expected identity, plan, filesystem, or reviewer-policy
  arguments must be supplied by the caller. The inventory intentionally does
  not advertise these validators as standalone JSON-schema validators.

The Python inventory (`codex.v16.contracts.SCHEMA_REGISTRY`) and this JSON file
are kept structurally identical by `tests/v16/test_schema_registry_extensions.py`.
Nested mission records omit their discriminator; standalone JSON documents
include it and are accepted by `validate_schema_document` only where the
validator is genuinely standalone. Conditional metadata records fields that
become mandatory for approval (for example, `review-packet.v16.decision_basis`
and readiness-state approval hashes) without weakening the author-side packet
shape.

`tool-route-decision.v16` and `tool-health.v16` are the two read-only
tool-routing contracts. They are validated by the public
`codex.v16.tool_routing.validate_route_decision` and
`codex.v16.tool_routing.validate_health_report` APIs. Both are standalone
shape/arithmetic checks with `observation_mode: injected-observation`: callers
may provide deterministic observations to bind a decision/report, but the
inventory does not claim that either artifact proves a tool was executed.

`review-runtime.v16` is a caller-bound dispatch/SLO contract derived from the
frozen review policy. It fixes the reviewer route/effort, fresh-versus-
continuation identity, delta-only behavior, one-call/zero-duplicate rule, and
file/line/context/tool/deadline budgets. `review-runtime-progress.v16`
recomputes the controller action from current privacy-safe time, file,
context-character, tool-call, review-call, and duplicate-full-scope counts.
Both validators independently require the frozen review policy, so a coherent
route rewrite plus rehash cannot weaken it. They also require caller-owned
exact context-mode, delta-count, review-identity, prior-artifact, and
reviewer-continuity expectations so understated deltas and false continuity are
rejected. These schemas govern routing and interruption only: they cannot turn
a partial report, invalid evidence, active blocker, or lineage mismatch into
approval.

Formal review packets and Independent artifacts carry the frozen review-policy
identity (`required_stages`, classifier/triggers, and `review_policy_sha256`)
plus component hashes for reference/domain/threshold/invariant/non-goal scope.
Continuation artifacts additionally carry a hashed `closure_matrix`; an active
prior finding may only disappear with a matching evidence reference and
counterexample recheck.  The closure evidence reference is structured, not an
opaque description: it resolves `case_id`, `evidence_row_sha256`, and
`log_sha256` in the caller-supplied current evidence envelope.  A fixed finding
also carries an `EXECUTABLE_RESULT` recheck with the original
`counterexample_sha256`, the same row/log identity, and the executable
`result_sha256`. Formal `FIXED` closure additionally requires a
`closure-binding-receipt.v16` created before execution from the committed
author closure plan, exact compiled plan, transcript-pinned normalized Spark
sources, and explicit file hashes. Each receipt binding freezes the finding,
original counterexample digest, expected evidence case, gate, stage, and
entrypoint. GateRunner includes the receipt in its cache identity and emits
the exact sorted binding-digest set for each gate/row; shared rows retain every
binding exactly once. Evidence envelopes copy those runner-owned fields and
bind the receipt and closure-plan hashes. Immediately after validating the
receipt and before GateRunner executes, presubmit derives the strict
`pre-execution-closure-authority.v16`. It commits the receipt, compiled plan,
canonical and file closure plan, dispatch file, normalized source-artifact
path/hash pairs,
exact finding denominator, and canonical binding-set digest. The complete
authority and its self-hash are embedded in the review decision basis.
Formal ingestion derives the expected receipt, plan, dispatch, source, finding,
and binding identities only from that basis authority; retained receipt/plan
compatibility arguments are equality assertions and never roots of authority.
Caller-authored row labels and self-consistent resealing are not authority.
Missing, stale, copied, relabeled, wrong-plan, wrong-source, or non-green
results are rejected.

For this milestone, the trusted root is the exact pre-run authority record
that binds the dispatch identity and is embedded in the review decision basis,
together with independent review of that exact snapshot. This local validator
does not claim cryptographic protection against an actor able to rewrite the
pre-run authority, dispatch record, candidate packet, evidence, and
reviewer/control-plane record as one coordinated forgery. Signatures or an
external transparency/control-plane trust anchor are separate scope. A future
reviewer may record that stronger property as `CONTRACT_CHALLENGE` or
`FOLLOW_UP`; it is not a new blocking condition for this frozen milestone.
`escalation_evidence_ref` is empty for ordinary/derived escalations. Non-
derivable escalation identities (`REVIEWER_PARTICIPATED`, `LINEAGE_LOSS`, and
review-hook routing governance change) require a caller-bound SHA-256 value;
governance changes must bind it exactly to the changed diff hash and changed
policy identity.

Review identity is a strict union.  `git-exact-object` keeps the 40-hex Git
head/tree contract and empty content snapshots; `non-git-snapshot` keeps the
Git context but binds a 64-hex current and prior content snapshot plus the
canonical old/new `delta_sha256`.  Git continuations require a new head;
non-Git continuations may keep the same head only when the snapshots differ
and the caller supplies the matching delta.  Legacy Git packets without the
union remain readable, but are normalized to the empty-snapshot Git mode and
cannot be used to authenticate a Non-Git approval.

Canonical evidence envelopes and their rows carry the same `identity_mode` and
current `snapshot_sha256`. Git-object evidence requires an empty snapshot and a
clean worktree. Non-Git evidence requires a 64-hex snapshot and may represent a
dirty materialized snapshot only when the envelope and every row bind that
same snapshot and their clean/dirty flags agree. Formal Non-Git closure rejects
bare row arrays and any prior-snapshot envelope, even when Git head/tree are
unchanged.

Closure receipt fields are conditional. Generic evidence and gate receipts
without a closure plan retain the strict legacy field set. Once a
`closure-binding-receipt.v16` is supplied, its two receipt fields are mandatory
on every emitted gate/row and evidence row, while the evidence envelope must
carry both `closure_binding_receipt_sha256` and `closure_plan_sha256`. A review
decision basis either omits closure authority entirely for a generic
non-closure flow or carries the complete strict
`pre-execution-closure-authority.v16`; partial authority fields are rejected.

The executable positive mission is `../fixtures/mission.valid.json`; intentionally
invalid schema, bool/int, extra-field, and cyclic-DAG fixtures live beside it.
The Spark audit closure matrix is `v16_spark_audit_closure.json` and records only
sanitized task identities, findings, acceptance cases, and dispositions.

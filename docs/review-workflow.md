# Review workflow

Correctness and valid evidence are hard gates. Within that boundary, optimize
time to a correct decision and merge; optimize token/call cost second. Freeze
the Acceptance Envelope, exact Git head or non-Git snapshot, review risk,
required evidence stages, and one formal reviewer before dispatch. Qian9921 is
the author identity and Liang9921 the governance identity. The writer cannot
review, approve, or merge.

## Risk route

- `low`/`medium`: fresh `gpt-5.6-sol` high
  `independent_clean_room`.
- `high`: fresh `gpt-5.6-sol` xhigh `independent_clean_room`.

High risk includes math/numerics, exact parity, security/privacy, public
contracts, schema/data formats, irreversible migration, supply chain/installer,
production runtime, formal research/release, and hook/reviewer/model routing.
Unknown, missing, invalid, ambiguous, or disputed classification fails closed
to high. Review route and evidence tier are separate axes. Independently freeze
`required_stages` as targeted, targeted+full, or targeted+full+fresh from the
affected WHY-RED plan; no reviewer may add unbudgeted full/fresh execution
merely for convenience. Missing/legacy policy uses all stages fail-closed.
The executable enum source is
`codex.v16.review_policy.HIGH_RISK_TRIGGERS`; fresh-review escalation identities
come from `codex.v16.trace._ESCALATION_TRIGGERS`. Prose cannot silently add
either kind of trigger.

## Runtime and convergence budget

Compile `review-runtime.v16` before every formal dispatch. This is a routing and
latency contract, not an acceptance threshold:

- Initial high-risk and `escalated_fresh` review remains fresh Sol xhigh.
- A contract-stable `delta_continuation` after prior `COMPLETE` coverage reuses
  the same reviewer, uses Sol high for high-risk work, and receives only the
  exact delta, prior finding/closure lineage, reused evidence, and direct
  affected boundaries.
- Delta continuation is bounded to 12 files, 800 changed lines, 12,000 context
  characters, eight read-only tool calls, a 90-second soft report deadline, and
  a 240-second hard deadline. Exceeding its static size route selects
  `escalated_fresh`; it is not a blocker or a reason to relax acceptance.
- Initial low/medium Sol review uses a 180/480-second soft/hard budget; initial or
  escalated high-risk review uses 300/900 seconds. Exactly one formal review
  call and zero duplicate full-scope reviews are permitted per identity.
- At the soft deadline the controller requests the current formal report. At
  the hard deadline or a file/context/tool/review-call/duplicate-full-scope
  budget breach it interrupts and replans; partial coverage cannot approve.
  New falsifiable evidence in a continuation selects `escalated_fresh`;
  unsupported scope expansion stops.

`review-runtime-progress.v16` mechanically derives `CONTINUE`,
`REQUEST_REPORT`, `ACCEPT_REPORT`, `RETURN_PARTIAL`, `INTERRUPT_REPLAN`,
`ESCALATE_FRESH`, or `STOP_SCOPE_EXPANSION`. Runtime eligibility only says the
report is complete enough to ingest. The independent evidence, lineage,
coverage, P1/BLOCKING, and verdict gates remain authoritative.
The progress validator receives the frozen review policy independently and
records observed context characters, review calls, and duplicate full-scope
reviews. Runtime and progress validation also receive caller-owned exact
context-mode, changed-file/line, review-identity, prior-artifact, and
reviewer-continuity expectations; the runtime payload and its digest are not
self-authenticating authority.

## Clean-room packet and artifact

The initial gate is a distinct, report-only task with `fork_turns=none`. It
receives a curated, hash-bound packet—not the author's full chat—containing the
Acceptance Envelope, reference/domain/invariants/non-goals, exact identity and
diff, direct dependencies, affected tests, evidence envelopes and denominators,
known limitations, and prior findings/dispositions when applicable. Exclude
persuasive conclusions, unverified guesses, stale diffs, and irrelevant logs.

The Independent artifact must bind the packet/envelope hashes, exact
base/head/tree/diff or snapshot, reviewed scope, evidence hashes and
denominators, reviewer-owned findings, limitations, risk route, context mode,
escalation reason, dispatch lineage, and verdict. Ingestion rejects a bare
verdict, self-authenticated lineage, or author-created findings presented as the
reviewer's decision basis.

Identity is a strict union: `git-exact-object` uses the 40-hex Git object
identity and empty content snapshots, while `non-git-snapshot` carries caller-
bound 64-hex current/prior snapshots and a canonical old/new `delta_sha256`.
Git continuations require a new head; a Non-Git continuation may keep the same
head only when the snapshots differ and the caller's delta matches.

## Monotonic closure

Initial review uses `independent_clean_room`. Ordinary in-contract fixes reuse
the original reviewer in `delta_continuation`; every new head gets a distinct
run receipt and new verdict. Supply only the old/new identities, exact delta,
prior finding/disposition matrix, reused evidence hashes, and directly affected
boundaries. The reviewer must retest the original counterexample logically or
through a structured current-bundle case/row/log reference and executable
result hash. A closure-aware run first creates a strict
`closure-binding-receipt.v16` from the committed author closure plan, exact
compiled plan, transcript-pinned normalized findings, and their file hashes.
Before executing any gate, that receipt freezes each finding’s original
counterexample hash and expected evidence case/gate/stage/entrypoint.
Presubmit immediately derives a strict
`pre-execution-closure-authority.v16` from the validated receipt, still before
GateRunner. The authority commits the receipt hash, compiled-plan hash,
canonical and file closure-plan hashes, dispatch-file hash, exact sorted
normalized-source artifact path/hash set, finding denominator, binding-set
hash, and its own canonical hash. The complete authority is included in the
review decision basis.
GateRunner emits the receipt hash and exact sorted binding-digest set on each
gate/row; the evidence producer copies those runner-owned fields. Formal
ingestion receives the exact receipt but derives every expected receipt, plan,
dispatch, source, finding, and binding identity from the candidate decision
basis authority. Legacy expected-receipt/plan arguments, when supplied, are
equality assertions only. They cannot replace or override the basis authority.
`FIXED` without that authority is RED.
A reviewer-authored recheck, caller row annotation, another valid green case,
or a completely resealed row/envelope/packet/artifact is not authority. An
author `FIXED` label, copied description, stale bundle, wrong plan, or opaque
placeholder is not proof.

The frozen milestone trust root is the exact pre-run authority record binding
the dispatch identity and embedded in the review decision basis, plus
independent review of that exact snapshot. The local validator does not claim
cryptographic resistance to an actor who can coordinately rewrite the pre-run
authority record, dispatch record, candidate packet, evidence, and
reviewer/control-plane record. Signatures, transparency logs, or another
external control-plane trust anchor are separate scope. Reviewers must classify
that stronger property as `CONTRACT_CHALLENGE` or `FOLLOW_UP`, not silently
promote it to a blocking requirement for this milestone.

Use `escalated_fresh` only when the Acceptance Envelope/reference/domain/
threshold/invariants/non-goals or path set drifts; risk rises to high; a local
fix becomes a material rewrite; reviewer independence or lineage is lost; an
`ORIGINAL_SCOPE_MISSED` P1, new falsifiable P1 evidence, or post-review incident
appears; the same finding fails to converge for two rounds; author and reviewer
dispute the contract; review/hook/routing governance changes; or packet/evidence
identity is invalidated.

Coverage is monotonic. After first `COMPLETE`, every new blocker is admitted as
`DELTA_INTRODUCED`, `ORIGINAL_SCOPE_MISSED`, or
`NEW_FALSIFIABLE_EVIDENCE`. Unchanged closed findings stay closed. `APPROVE`
requires complete coverage, empty unreviewed scope, no active P1/BLOCKING, and
a matching Independent artifact; otherwise the verdict is `REQUEST_CHANGES` or
`null` for infrastructure failure.

## Tool route

Freeze applicable inspection intents independently from review risk and
evidence stages. Known symbols/calls/dependencies/blast radius use the
revision-matching child CodeGraph; unknown semantic entrypoints/similar
implementations use Semble; exact strings/errors/config/logs use `rg`; shell
output shown to the model uses `rtk`. Raw output is reserved for exact
denominators or downstream machine input.

`codex.v16.tool_routing` validates the choice and four-tool health denominator.
Fallback requires an observed preferred-tool failure/unavailability, a stable
reason code, and an evidence reference, and never claims equivalent semantic or
structural coverage. CodeGraph CLI state is locally probeable; Semble is an MCP
capability and stays `unknown` until the orchestrator supplies a real
observation. Hooks record normalized route/reason codes without prompts, raw
arguments, cwd, tokens, credentials, or private identifiers.

## Evidence, trace, and metrics

`FAST` runs targeted evidence and never formal review. `CANDIDATE` runs the
frozen affected evidence route. `FINAL` runs any still-required stage once and
then the single formal gate. Evidence is reusable only when the composite
identity—head/snapshot, command, runtime/config, reference/oracle, Acceptance
Envelope, denominator, and artifact/log hashes—matches exactly.

Every canonical evidence envelope and row explicitly carries
`identity_mode` plus `snapshot_sha256`. Git-object evidence uses an empty
snapshot and clean rows. Snapshot-bound Non-Git evidence may carry a dirty
materialized worktree, but the envelope and all rows must bind the same 64-hex
snapshot and consistent clean/dirty state. Same-head evidence from a prior
snapshot and bare Non-Git row arrays are rejected by formal ingestion.
Closure receipt fields are conditional: ordinary evidence keeps the strict
legacy shape, while a closure-aware run requires the exact receipt/plan hashes
on the envelope and the exact receipt hash plus sorted binding set on every
row. A shared execution row must contain all and only the bindings frozen for
its case/gate/entrypoint. Likewise, a generic review decision basis omits
closure authority; a closure-aware basis carries the complete strict authority
object and its recomputed canonical hash. Partial or mismatched authority is
RED.

`python3 scripts/presubmit.py --repo .` compiles the mission, exercises
positive/negative contracts, checks gate ordering and identity drift, validates
evidence arithmetic/privacy, renders sanitized trace packets, derives metrics,
and verifies a fresh archive. It never calls GitHub or changes identity.

Primary metrics are time to first actionable finding, correct verdict, and
correct merge; review-round count, adjudicated false-blocker rate, reopened
scope, missed P1s, and evidence reuse measure convergence and correctness.
Token/call usage is secondary. Correctness-dependent metrics remain
`unavailable` until an external adjudication, accepted closure, incident, or
declared observation window supplies a real denominator; missing telemetry is
always explicit `None`/`unavailable` and never encoded as zero. Such missingness
remains visible to acceptance rather than being silently dropped. First-pass
approval is diagnostic, not a target.

# Architecture

The package has six layers:

1. normative policy and brief templates;
2. strict mission/evidence/review contracts;
3. deterministic CodeGraph/Semble/rtk/rg tool routing and health evidence;
4. a content-addressed FAST/CANDIDATE/FINAL execution engine;
5. capability-neutral model routing with hard call/concurrency/token budgets;
   and
6. privacy-safe hooks plus the allowlisted installer/verifier.

A persistent parent owns each mission. Parent pre-dispatch validation,
SubagentStart ACTIVE-MISSION-LOCK, and post-result validation enforce nested
delegation because collaboration spawn is not assumed PreToolUse-observable.
Model names identify dispatch targets, not global capabilities.

FAST runs only targeted affected gates against an exact staged or worktree
snapshot. A dirty receipt is valid only when the runner records the snapshot
mode/hash and the consumer supplies the same hash independently. CANDIDATE adds
the frozen route's remaining local gates on a clean exact candidate. FINAL adds
any still-required fresh portability flow and one risk-routed Independent
review. Low/medium review defaults to `gpt-5.6-terra` high; high and unresolved
risk use `gpt-5.6-sol` xhigh. Review route and evidence stages are orthogonal.
Reusable gates are cached by content snapshot, compiled plan, argv, complete
non-secret effective environment inputs, cwd, timeout, interpreter, and runtime
version. Runner artifacts live outside the repository so receipt writes cannot
contaminate the content identity; the runner re-hashes the selected snapshot
after execution and rejects drift.
Staged mode executes an isolated local materialization whose HEAD, index and
worktree identities must remain unchanged; it never executes unstaged
worktree bytes. Staged materializations stay serial because gates at the same
snapshot would otherwise contend for one deterministic execution root. Mission
entrypoints and gates may opt into bounded parallelism only through strict
`read_only: true` declarations on both in worktree or clean-candidate modes.

Only explicitly read-only gates and entrypoints may share a dependency layer,
and concurrency remains bounded. Write-capable or dependent work stays serial.
The usage ledger counts calls cumulatively, holds conservative outstanding token
maxima before dispatch, settles provider-reported actual input/output afterward,
tracks active and peak concurrency separately, and never fabricates USD cost.

Tool routing is intentionally separate from model routing. A declared known
symbol/call/dependency/blast-radius intent selects the revision-matching child
CodeGraph; semantic discovery and similar implementations select Semble; exact
text selects `rg`; shell display selects `rtk`. The stdlib routing primitive
does not launch tools or mutate indexes. It validates selection, a four-tool
health denominator, and evidence-backed fallback. CLI probes cover
CodeGraph/rtk/rg; Semble is an MCP capability and remains `unknown` until the
orchestrator supplies a real capability observation. Hooks record normalized
route/reason codes but never raw prompts, arguments, cwd, tokens, credentials,
or private identifiers. Semantic intent is not reliably inferable from every
low-level call, so enforcement combines explicit brief selection with
mechanical availability/fallback validation instead of blanket denial.

Formal review has four context modes. `author_contextual` is non-gating.
`independent_clean_room` starts a fresh report-only reviewer with a compact
hash-bound packet. `delta_continuation` preserves reviewer continuity while
binding a new run, identity, delta, evidence, and verdict.
`escalated_fresh` replaces the reviewer only on explicit risk, contract, scope,
lineage, incident, P1, governance, or non-convergence triggers. The resulting
artifact binds the actual review packet, evidence denominators, reviewer-owned
findings/limitations, lineage, and verdict; a bare verdict cannot pass
readiness.

`codex.v16.review_policy.HIGH_RISK_TRIGGERS` and
`codex.v16.trace._ESCALATION_TRIGGERS` are the executable sources for risk and
fresh-review escalation identities. Documentation may explain those enums but
cannot extend them.

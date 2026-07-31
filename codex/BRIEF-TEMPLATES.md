# Brief templates

## Mission packet

`assigned_model` is selected from the authorized live models for this task. It
is not a capability declaration.

```json
{
  "schema": "mission.v1",
  "milestone": "HARDENING",
  "objective": "<one vertical slice>",
  "owner": "<task>",
  "assigned_model": "<authorized-live-model>",
  "role": "execution",
  "permissions": ["read", "write", "test"],
  "scope": {"paths": ["<exact paths>"]},
  "reviewer_separation": {
    "independent": "<resolved-by-review-risk>",
    "fork_turns": "none",
    "report_only": true
  },
  "review_policy": {
    "review_risk": "medium",
    "reasons": ["<bounded internal behavior change>"],
    "classifier_identity": "<classifier/version>",
    "high_risk_triggers": [],
    "required_stages": ["targeted", "full"],
    "context_mode": "independent_clean_room"
  },
  "operating_domain": "<units/frame/runtime>",
  "invariants": ["<required>"],
  "non_goals": ["<excluded>"],
  "evidence_budget": {
    "checks": [{
      "name": "<check>",
      "why_red": "<failure mechanism>",
      "cost": "<estimate>",
      "denominator": "<known>"
    }]
  },
  "rollback": "<reversible action>"
}
```

## Tool routing sidecar

Declare only the inspection intents the mission actually needs. Resolve every
declared row through `codex.v16.tool_routing.route_tool`; do not hand-author a
successful decision.

```json
{
  "declared_intents": [
    {"intent": "known_symbol", "preferred_tool": "codegraph"},
    {"intent": "semantic_entry", "preferred_tool": "semble"},
    {"intent": "shell_output", "preferred_tool": "rtk"},
    {"intent": "exact_error", "preferred_tool": "rg"}
  ],
  "fallback_contract": {
    "requires_preferred_attempt": true,
    "requires_reason_code": true,
    "requires_evidence_ref": true,
    "silent_fallback": false
  }
}
```

Known symbol/call/dependency/blast-radius work routes to a revision-matching
child CodeGraph. Unknown semantic entrypoints and similar implementations route
to Semble. Exact strings/errors/configuration/logs route to `rg` or a bounded
exact read. Shell output shown to the model routes through `rtk`; raw output is
allowed for downstream machine input or exact denominator computation. A
missing intent is `not_declared`, not a fabricated blocker. A declared
preferred tool may fall back only after a real failure/unavailable observation
with a stable reason code and evidence reference; the fallback does not claim
equivalent structural or semantic coverage.

## Routing and usage sidecar

This sidecar is the input to `codex.v16.metrics.choose_model` and
`BudgetLedger`. Scores and `token_cost_rank` are relative, current control-plane
metadata. They are not hard-coded model capabilities or invented dollar
prices.

```json
{
  "task_kind": "implementation",
  "risk": "medium",
  "authorized_models": ["<model-a>", "<model-b>"],
  "live_models": {
    "<model-a>": {
      "available": true,
      "risks": ["low", "medium", "high"],
      "token_cost_rank": 1
    },
    "<model-b>": {
      "available": true,
      "risks": ["low", "medium", "high"],
      "token_cost_rank": 2
    }
  },
  "preferences": {
    "<model-a>": {"implementation:medium": 10, "default": 0},
    "<model-b>": {"implementation:medium": 8, "default": 0}
  },
  "limits": {
    "max_model_calls": 4,
    "max_review_calls": 1,
    "max_parallel_agents": 2,
    "max_input_tokens": 60000,
    "max_output_tokens": 20000,
    "max_total_tokens": 80000
  }
}
```

Reserve the conservative per-call maximum before dispatch. On completion,
`settle` that reservation with provider-reported input/output counts; this also
releases its active-agent slot. If counts are unavailable, settle at the
reserved maxima rather than inventing a smaller number. Persist only the
aggregate `BudgetLedger.usage()` receipt. For a ChatGPT subscription,
`usd_cost` remains `null` unless the provider exposes exact plan-specific
attribution. Do not infer API prices or divide the monthly subscription by
guessed calls.

## Nested delegation packet

```json
{
  "schema": "delegation.v1",
  "parent_task_id": "<parent>",
  "child_task_id": "<child>",
  "assigned_model": "<authorized-live-model>",
  "role": "specialist",
  "max_depth": 1,
  "depth": 1,
  "permissions": ["read", "write_paths"],
  "forbidden_permissions": ["git", "github", "review", "merge"],
  "lease": {"paths": ["<exclusive path>"]},
  "retry_budget": {"semantic_contamination": 1},
  "active_mission_lock": true,
  "plugin_inventory": "informational",
  "result_schema": "delegation-result.v1"
}
```

## Review packet

Freeze exact Git head or non-Git snapshot, Acceptance Envelope, risk decision,
coverage, direct dependencies, evidence envelopes with known denominators,
externally delivered lineage, and prior findings/dispositions. Hash the packet
before dispatch. The reviewer artifact must bind that packet hash, envelope,
base/head/tree/diff or snapshot identity, reviewed scope, evidence artifact
hashes, reviewer-owned findings/limitations, context mode, escalation reason,
and verdict. A bare verdict or author-supplied finding set is never sufficient.

Context modes are:

- `author_contextual`: writer pre-mortem only; never a gate.
- `independent_clean_room`: fresh initial report-only gate with
  `fork_turns=none` and a curated packet.
- `delta_continuation`: same reviewer continuity identity, distinct new run and
  verdict, old→new delta, prior findings/dispositions, and affected evidence.
- `escalated_fresh`: new reviewer after contract/risk/scope drift, material
  rewrite, missed/new P1 evidence, incident, lineage loss, governance change,
  dispute, or two non-converging rounds.

`APPROVE` requires complete coverage, empty unreviewed scope, no active
P1/BLOCKING, a matching caller-bound Independent artifact, and valid evidence.
Otherwise use `REQUEST_CHANGES` or `null` for infrastructure failure.

## V16 productivity mission

The strict `mission.v16` JSON uses the same fields as
`codex/v16/fixtures/mission.valid.json`. Set `assigned_model` to the model chosen
by the routing sidecar. Add an explicit `review_policy` for new missions:

```json
{
  "review_risk": "high",
  "reasons": [],
  "high_risk_triggers": ["hook_reviewer_model_routing"],
  "required_stages": ["targeted", "full", "fresh"],
  "context_mode": "independent_clean_room",
  "fork_turns": "none",
  "report_only": true
}
```

Allowed high-risk triggers are `math_numeric`, `exact_parity`, `security`,
`privacy`, `public_contract`, `schema_data_format`, `irreversible_migration`,
`supply_chain_installer`, `production_runtime`, `formal_research_release`, and
`hook_reviewer_model_routing`. Low/medium routes must not contain one; high must
contain at least one. Missing, invalid, ambiguous, or legacy policy resolves
fail-closed to high. The resolver, not the writer, fixes the reviewer route:
low/medium → `gpt-5.6-terra` high; high → `gpt-5.6-sol` xhigh.
`required_stages` is an independent frozen evidence route and must be one
ordered prefix: targeted; targeted+full; or targeted+full+fresh. Its default is
risk-informed, but an explicit route follows the mission's affected WHY-RED
plan rather than reviewer convenience. Executable risk and escalation enum
sources are `codex.v16.review_policy.HIGH_RISK_TRIGGERS` and
`codex.v16.trace._ESCALATION_TRIGGERS`; prose does not create new identities.

Select zero to three bounded Spark audits from actual risk and scope; the
current frozen V16 acceptance lock happens to select three. Do not add audits
merely to fill a quota.

Every blocking invariant/counterexample maps to an entrypoint and gate with
WHY-RED, cost, a known denominator, and red/green meaning. The observed gate
total must equal the mapped acceptance denominator.

Compile only (never executes argv):

```text
python3 -m codex.v16.compiler codex/v16/fixtures/mission.valid.json -o plan.json
```

Execution tiers:

- `FAST`: targeted gates against an exact staged/worktree content snapshot;
  dirty worktrees are allowed only with an externally supplied matching
  snapshot hash. Put runner artifacts outside the repository and require the
  before/after snapshot hashes to match. Staged mode runs an isolated local
  materialization of the index, not the unstaged worktree.
- `CANDIDATE`: targeted plus full gates on the exact clean candidate.
- `FINAL`: the risk policy's required current evidence followed by exactly one
  `independent_clean_room` report-only review. Low/medium use
  `gpt-5.6-terra` high; high and fail-closed policy use `gpt-5.6-sol` xhigh.

The complete clean-candidate/fresh workflow remains:

```text
python3 scripts/presubmit.py --repo .
```

The writer records inner-audit dispositions and generated trace bodies. The
formal reviewer covers the frozen exact scope once at `REVIEW_READY`. Ordinary
fixes return to the same reviewer as `delta_continuation`; escalation triggers
select a fresh reviewer. Review is not iterative debugging, and
`REQUEST_CHANGES` cannot be waived by changing thresholds or denominators.

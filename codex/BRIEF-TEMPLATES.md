# v15 brief templates

## Mission packet
```json
{"schema":"mission.v1","milestone":"HARDENING","objective":"<one vertical slice>","owner":"<task>","assigned_model":"gpt-5.6-luna","role":"execution","permissions":["read","write","test"],"scope":{"paths":["<exact paths>"]},"reviewer_separation":{"independent":"gpt-5.6-sol","fork_turns":"none","report_only":true},"operating_domain":"<units/frame/runtime>","invariants":["<required>"],"non_goals":["<excluded>"],"evidence_budget":{"checks":[{"name":"<check>","why_red":"<failure mechanism>","cost":"<estimate>","denominator":"<known>"}]},"rollback":"<reversible action>"}
```

## Nested delegation packet
```json
{"schema":"delegation.v1","parent_task_id":"<parent>","child_task_id":"<child>","assigned_model":"gpt-5.3-codex-spark","role":"specialist","max_depth":1,"depth":1,"permissions":["read","write_paths"],"forbidden_permissions":["git","github","review","merge"],"lease":{"paths":["<exclusive path>"]},"retry_budget":{"semantic_contamination":1},"active_mission_lock":true,"plugin_inventory":"informational","result_schema":"delegation-result.v1"}
```

## Review packet
Freeze exact Git head or non-Git snapshot, acceptance envelope, coverage, evidence envelopes, lineage, and findings. `APPROVE` requires complete coverage, empty unreviewed scope, no P1/BLOCKING. Otherwise `REQUEST_CHANGES` or `null` for infrastructure failure.

## V16 productivity mission

```json
{"schema":"mission.v16","mission_id":"<stable-id>","milestone":"HARDENING","objective":"<throughput + first-pass correctness>","owner":"Qian9921/<task>","assigned_model":"gpt-5.6-luna","role":"writer","permissions":["read","write","test"],"scope":{"paths":["<exact relative paths>"],"exact_head":"<40-hex base>","tree_sha":"<40-hex tree>"},"reviewer_separation":{"independent_model":"gpt-5.6-sol","fork_turns":"none","report_only":true},"operating_domain":"Linux Python>=3.9 stdlib-only","invariants":[{"id":"INV-1","description":"<invariant>","blocking":true,"counterexample_ids":["CE-1"]}],"counterexamples":[{"id":"CE-1","semantics":"<unique case>","description":"<known negative>","entrypoint_id":"EP-1","gate_id":"G-TARGETED","why_red":"<failure mechanism>","cost":"<estimate>","denominator":1,"expected":"RED"}],"entrypoints":[{"id":"EP-1","argv":["python3","-m","<checker>"],"cwd":".","env":{},"timeout_sec":60,"stop_conditions":["timeout","nonzero"]}],"gates":[{"id":"G-TARGETED","stage":"targeted","depends_on":[],"entrypoint_ids":["EP-1"],"blocking":true,"reusable":true},{"id":"G-FULL","stage":"full","depends_on":["G-TARGETED"],"entrypoint_ids":["EP-1"],"blocking":true,"reusable":false},{"id":"G-FRESH","stage":"fresh","depends_on":["G-FULL"],"entrypoint_ids":["EP-1"],"blocking":true,"reusable":false}],"acceptance":[{"id":"AC-1","invariant_id":"INV-1","counterexample_id":"CE-1","entrypoint_id":"EP-1","gate_id":"G-TARGETED","blocking":true,"why_red":"<why>","cost":"<cost>","denominator":1,"red_meaning":"<red>","green_meaning":"<green>"}],"non_goals":["live deployment"],"evidence_budget":{"checks":[{"id":"CHK-1","why_red":"<directly affected failure>","cost":"<cost>","denominator":1}]},"rollback":"<reversible action>","stop_conditions":["unknown denominator","privacy red","head drift"],"spark_audits":[{"id":"SPARK-A","domain":"<domain>","scope":["<scope>"],"max_findings":8,"required":true,"request_schema":"spark-audit-request.v16"},{"id":"SPARK-B","domain":"<domain>","scope":["<scope>"],"max_findings":8,"required":true,"request_schema":"spark-audit-request.v16"},{"id":"SPARK-C","domain":"<domain>","scope":["<scope>"],"max_findings":8,"required":true,"request_schema":"spark-audit-request.v16"}]}
```

Compile only (never executes argv):

```text
python3 -m codex.v16.compiler codex/v16/fixtures/mission.valid.json -o plan.json
```

Run the complete machine presubmit and fresh archive check:

```text
python3 scripts/presubmit.py --repo .
```

The writer records Spark dispositions and generated trace bodies. Independent
Sol reviews the frozen exact scope once at `REVIEW_READY`; Sol is not an
iterative debugger, and a `REQUEST_CHANGES` result cannot be waived by changing
thresholds or denominators.

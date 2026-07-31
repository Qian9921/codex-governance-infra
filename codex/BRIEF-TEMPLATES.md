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

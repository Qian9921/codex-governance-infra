# V16 contract registry

`schema_registry.v16.json` is the portable field inventory. Each entry names the
stdlib validator that owns strict types, uniqueness, arithmetic, privacy, and
cross-object linkage. Nested mission records omit their discriminator; standalone
JSON documents include it and are accepted by `validate_schema_document`.

The executable positive mission is `../fixtures/mission.valid.json`; intentionally
invalid schema, bool/int, extra-field, and cyclic-DAG fixtures live beside it.
The Spark audit closure matrix is `v16_spark_audit_closure.json` and records only
sanitized task identities, findings, acceptance cases, and dispositions.

# Codex Governance Infrastructure v16 — Productivity Engine

Portable, privacy-safe global Codex governance policy, V16 mission contracts,
foreground evidence gates, bounded Spark audits, and artifact-derived
productivity metrics. This private repository is a source package only: it does
not deploy to a live `$CODEX_HOME` during development.

## V16 workflow

1. Freeze `codex/v16/ACCEPTANCE-LOCK.md` and the exact base/head/tree identity.
2. Compile a mission without executing it:
   `python3 -m codex.v16.compiler codex/v16/fixtures/mission.valid.json`.
3. Run the one-command affected presubmit:
   `python3 scripts/presubmit.py --repo .`.
4. Keep the three bounded, zero-context Spark audits report-only and record their
   sanitized findings/dispositions in `codex/v16/contracts/`.
5. A fresh zero-context GPT-5.6-Sol report-only reviewer is the sole final review
   gate. Luna is the persistent writer; the renderer never calls GitHub or
   switches identity. A review `APPROVE` is not a merge/GO claim.

Every check declares WHY-RED, cost, and a known non-zero denominator. Exact
arithmetic, current identity, clean state, log hash, and privacy checks are
required. Unknown/skip/xfail/stale/copied evidence is RED; there are no manual
count supplements.

## Layout

- `codex/AGENTS.md` — compact V16 policy and role matrix.
- `codex/BRIEF-TEMPLATES.md` — mission and gate brief templates.
- `codex/v16/` — strict schemas, mission compiler, readiness state, foreground
  runner, evidence engine, Spark protocol, trace renderer, metrics, and
  presubmit orchestration.
- `codex/contracts/` — prior delegation examples retained for compatibility.
- `scripts/` — dry-run/atomic installer, verifier, and `presubmit.py`.
- `docs/` — architecture, deployment, review, and privacy model.
- `tests/v16/` — deterministic positive and mandatory negative fixtures.

Run `python3 scripts/presubmit.py --repo .` for the complete machine envelope.
Run `python3 scripts/verify-governance.py --repo .` for a deterministic read-only
source scan. Use the installer only with an isolated `CODEX_HOME`; live
deployment requires a separately authorized governance lane.

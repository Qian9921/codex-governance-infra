# Codex Governance Infrastructure v15

Portable, privacy-safe global Codex governance policy, task contracts, hook validators, and an allowlisted installer. This private repository is a source package only: it does not deploy to a live `$CODEX_HOME` during development.

## Layout

- `codex/AGENTS.md` — compact normative v15 policy.
- `codex/BRIEF-TEMPLATES.md` — mission and nested delegation templates.
- `codex/contracts/` — machine-readable examples and validation schema.
- `codex/hooks/` — hook configuration, receipt, and delegation validators.
- `scripts/` — dry-run/atomic installer and verifier.
- `docs/` — architecture, deployment, review, and privacy model.
- `tests/` — deterministic stdlib contract and privacy fixtures.

Run `python3 scripts/verify-governance.py --repo .` for a deterministic read-only check. Use the installer only with an isolated `CODEX_HOME`; live deployment requires a separately authorized governance lane.

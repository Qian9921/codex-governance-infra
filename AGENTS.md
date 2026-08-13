# Repository contribution rules

- Keep governance portable and privacy-safe. Never commit credentials, live
  sessions, prompts, receipts, private paths, or machine-specific state.
- Python implementation uses the standard library only.
- Public release is Codex Governance Infra V21.2.0. Preserve the existing V16 strict contracts as an explicit-user opt-in compatibility
  proof engine. STRICT is never inferred from risk, production, or release context; the default adaptive path must remain non-blocking for missing
  ceremony while preserving real safety, correctness, and privacy blockers. Terra
  may be used only through bounded TERRA_REPLAN/TERRA_TRIAGE bridges or as a
  Luna-unavailable continuity fallback; it is never a universal controller.
- Before new implementation, search for an existing owner and record
  `REUSE|EXTEND|NEW` when the change introduces a meaningful abstraction.
- Daily work uses exactly three lanes: Semble for discovery, the compiler-derived
  semantic gateway for known C++/Python semantics, and bounded exact evidence
  for source/Git/compiler/build/test/benchmark facts. CodeGraph, `rg`, and `rtk`
  remain retained V16 STRICT compatibility routes only.
- Run the smallest affected tests during development and
  `python3 scripts/verify-governance.py --repo .` before final review.
- One independent review owns the formal verdict. Contract-stable fixes receive
  delta-only follow-up; do not duplicate full evidence or full-scope review.
- your-developer-account authors repository changes. your-reviewer-account independently reviews,
  approves, and merges the exact reviewed head.

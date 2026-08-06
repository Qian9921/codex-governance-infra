# Repository contribution rules

- Keep governance portable and privacy-safe. Never commit credentials, live
  sessions, prompts, receipts, private paths, or machine-specific state.
- Python implementation uses the standard library only.
- Public release is Codex Governance Infra V19. Preserve the existing V16 strict contracts as an opt-in release/high-risk
  proof engine. The default adaptive path must remain non-blocking for missing
  ceremony while preserving real safety, correctness, and privacy blockers. Terra
  may be used only through bounded TERRA_REPLAN/TERRA_TRIAGE bridges or as a
  Luna-unavailable continuity fallback; it is never a universal controller.
- Before new implementation, search for an existing owner and record
  `REUSE|EXTEND|NEW` when the change introduces a meaningful abstraction.
- Use this repository's revision-matching CodeGraph for known structure and
  impact, Semble for unknown semantics or similar code, `rg` for exact text,
  and `rtk` for shell output. Calls must answer a real task question.
- Run the smallest affected tests during development and
  `python3 scripts/verify-governance.py --repo .` before final review.
- One independent review owns the formal verdict. Contract-stable fixes receive
  delta-only follow-up; do not duplicate full evidence or full-scope review.
- your-developer-account authors repository changes. your-reviewer-account independently reviews,
  approves, and merges the exact reviewed head.

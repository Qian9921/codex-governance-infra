# Repository contribution rules

- Keep governance portable and privacy-safe; never commit live sessions, receipts, secrets, or machine paths.
- Python implementation uses the standard library only.
- Changes must preserve the v15 acceptance envelope, exact identity, lease, and review gates.
- Known symbol/call/dependency/impact work uses this repository's CodeGraph;
  unknown semantic entrypoints/similar implementations use Semble; exact text
  uses `rg`; shell output shown to the model uses `rtk`. Fall back only after a
  real failure with a reason code and evidence reference.
- `.codegraph/` is local generated state. Build or sync only with explicit
  authorization, then refresh after edits; never substitute another project's
  index.
- Run `python3 scripts/verify-governance.py --repo .` and the contract test suite before review.
- Compile and obey `codex.v16.review_runtime`: one formal review call, bounded
  delta/context/tool scope, soft report deadline, hard interrupt-and-replan
  deadline, and no duplicate full-scope review. Runtime budgets select routing;
  they never waive correctness or evidence.
- The repository is authored by Qian9921; Liang9921 is the independent governance reviewer.

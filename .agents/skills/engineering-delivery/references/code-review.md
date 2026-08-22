# Code review

Review the current head as an independent reviewer. Start from the request, changed files, relevant tests, and exact `reviewed_sha`; do not rely on the author's summary when the diff or repository can answer the question.

Prioritize findings that affect:

- correctness or unmet requirements;
- security, data loss, or permission boundaries;
- regressions, compatibility, or operational failure;
- tests that do not exercise the changed behavior.

Treat style preferences and speculative future improvements as non-blocking unless the repository explicitly makes them required. The goal is improved code health, not theoretical perfection. Prefer one precise finding with a concrete failure mode over a list of generic concerns.

Return:

```text
reviewed_sha: <exact commit>
verdict: approve | request_changes
blocking_findings:
  - file:line — concrete failure and smallest useful fix
nonblocking_notes:
  - optional note
```

If the head changes, discard the verdict and review the new head. Do not approve an older SHA because the code appears unchanged.

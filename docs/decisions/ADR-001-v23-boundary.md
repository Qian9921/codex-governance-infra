# ADR-001: Keep the harness portable and thin

## Status

Accepted

## Context

Codex already provides the agent, permission, Skill, and Subagent primitives needed for normal work. GitHub already provides Pull Request history, review state, checks, and merge rules. Duplicating those responsibilities in a second runtime would enlarge permanent context, add local failure modes, and make ordinary tasks slower.

The repository must work for more than one machine while keeping account, model, credential, greeting, and path facts local. It must also preserve user-authored local configuration outside its ownership boundary.

## Decision

Keep the portable repository limited to a short policy, logical role templates, on-demand delivery guidance, and small helpers for installation and GitHub delivery. Use the Pull Request and its current head as the durable delivery record. Keep local mappings and greetings out of the repository. Use same-machine author/reviewer identities as an auditable workflow separation without claiming operating-system isolation.

Do not add a mechanism unless a concrete failure is identified and the existing Codex or GitHub capability cannot address it with less complexity.

## Consequences

- New tasks start with a small context and load detail only when relevant.
- The author and reviewer boundary is visible and reviewable on GitHub.
- Installation can preserve unrelated personal state.
- Interruption recovery starts from the Pull Request instead of a second workflow record.
- The helper surface remains small enough to understand and recover manually.
- Future additions must carry a concrete failure case and a clear ownership boundary.

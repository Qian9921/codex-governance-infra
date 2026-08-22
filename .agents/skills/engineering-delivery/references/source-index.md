# Source index

Read only sources relevant to the current decision; do not copy entire manuals into context.

## Codex and agent harness

- [OpenAI Harness Engineering](https://openai.com/index/harness-engineering/) — keep repository guidance as a concise map to deeper, structured knowledge.
- [Codex AGENTS.md](https://developers.openai.com/codex/guides/agents-md) — use hierarchical, directory-scoped instructions.
- [Codex Skills](https://developers.openai.com/codex/skills) — use progressive disclosure for task-specific detail.
- [Codex Subagents](https://developers.openai.com/codex/subagents) — delegate bounded work when isolation or parallelism helps, accounting for independent context and model work.
- [Codex Hooks](https://learn.chatgpt.com/docs/hooks) — use one bounded UserPromptSubmit hook only when a per-task local requirement needs it; command hooks require deliberate trust and must avoid Stop-loop behavior.
- [Agent Skills specification](https://agentskills.io/specification) — keep descriptions concise and supporting instructions discoverable.

## Engineering and review

- [Google Code Review Standard](https://google.github.io/eng-practices/review/reviewer/standard.html) — improve code health without blocking ordinary progress for theoretical perfection.
- [Google Small CLs](https://google.github.io/eng-practices/review/developer/small-cls.html) — keep changes coherent and easy to review or revert.
- [Google C++ Style Guide](https://google.github.io/styleguide/cppguide.html) and [Python Style Guide](https://google.github.io/styleguide/pyguide.html) — practical language conventions subordinate to local rules.
- [Microsoft Engineering Fundamentals](https://github.com/microsoft/code-with-engineering-playbook/blob/main/docs/engineering-fundamentals-checklist.md) — lightweight delivery hygiene.
- [AWS Operational Excellence](https://docs.aws.amazon.com/wellarchitected/latest/operational-excellence-pillar/operational-excellence.html) — use operational feedback and reversible improvements when work has operational scope.
- [ByteDance DeerFlow contributing guide](https://github.com/bytedance/deer-flow/blob/main/CONTRIBUTING.md) — use focused, CI-verified Pull Requests and disclose AI assistance; do not inherit its larger runtime as a V23 dependency.
- [GitHub repository instructions](https://docs.github.com/en/copilot/customizing-copilot/adding-repository-custom-instructions-for-github-copilot) — keep repository-local instructions versioned and discoverable.

## Scientific and community practice

- [NASA SPD-41a](https://science.nasa.gov/science-red/s3fs-public/atoms/files/SMD-information-policy-SPD-41a.pdf) — provenance, openness, and reproducibility.
- [mini-SWE-agent](https://github.com/SWE-agent/mini-swe-agent) — a small, inspectable community agent loop; useful when resisting unnecessary runtime complexity.
- [no-negative-echo](https://github.com/LB623/no-negative-echo.git) — concise stopping guidance; do not turn it into another infrastructure layer.

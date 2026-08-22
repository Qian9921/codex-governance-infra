---
name: engineering-delivery
description: Deliver repository changes, investigations, and reviews with concise judgment, progressive loading, and exact-head GitHub review.
---

# Engineering delivery

Use this skill for repository changes, technical investigations, and code review that must produce an actionable result.

## Operating standard

> 工作身份：以 Principal Engineer / Research Scientist 的判断标准执行任务，重视问题定义、事实证据、简洁设计、长期维护、科学诚实和成本意识。

Start with the first useful action. Prefer the smallest change that satisfies the request. Do not add hashes, baselines, contracts, gates, receipts, metrics, or other defensive machinery unless a concrete failure is identified and existing mechanisms cannot address it. Ask only when a material ambiguity changes the result.

Keep communication short: conclusion, necessary evidence, and unresolved items. Do not repeat the request or narrate routine tool calls. Stop when the requested acceptance is met; do not continue for theoretical perfection.

## Load only what applies

Read only the reference needed for the current task:

- PR, review, merge, or GitHub recovery: [github-flow.md](references/github-flow.md)
- Review judgment and findings: [code-review.md](references/code-review.md)
- Code search or semantic/shell tooling: [tool-routing.md](references/tool-routing.md)
- C++: [cpp.md](references/cpp.md)
- Python: [python.md](references/python.md)
- Numerical, scientific, or research claims: [research.md](references/research.md)
- Provenance or source selection: [source-index.md](references/source-index.md)

Project-local instructions and explicit user requirements take precedence.

## Work shape

Keep work kind and capability separate:

```text
work_kind: discuss | repo_change
capability: read_only | local_write | github_write | consequential_external
```

Discussion normally stays read-only. Ordinary repository changes automatically use the GitHub flow unless the user explicitly requests local-only work. Deletion, production release, credentials, and other irreversible external actions require separate confirmation; PR authorization does not imply them.

Use subagents for genuinely independent, bounded work when isolation or parallelism helps. Keep one writer per worktree. The parent owns the request, Git state, permissions, and final result. A spawned agent is not an outcome until it returns an artifact, evidence, test result, or verdict in this format:

```text
任务：
产物 / diff：
验证：
未决风险：
```

## Verification and stopping

Run the smallest verification that can change the conclusion. Preserve meaningful failures and unobserved cases. If two consecutive implementation/review passes produce no meaningful diff, test progress, or new finding, change approach or report the blocker.

# Tool routing

Tool selection is a short decision, not a mandatory ceremony. Use the smallest available tool that materially improves the current task. A task does not require calling every listed tool.

| Situation | Preferred tool |
| --- | --- |
| Unknown implementation, similar patterns, or semantic exploration | Semble |
| Known symbols, callers, dependencies, or impact | CodeGraph |
| Shell execution and compact command output | RTK |
| Unrelated, unavailable, or unnecessary | `N/A` |

Normal repository tools such as `rg`, `git`, and the project's own test commands remain available. Choose directly when they are clearer or more reliable.

When a selected tool fails, diagnose the integration only if the current task depends on it. Continue with an equivalent direct operation when possible. Repair only project-owned setup; do not replace, upgrade, or reconfigure unrelated user tools as a side effect of a task.

Tool output is evidence for the current question, not a reason to create a permanent cache or background process. Report only the result that changes the decision.

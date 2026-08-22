# Tool routing

Choose the least expensive tool that answers the current question. Routing is a decision, not a requirement to call every installed tool.

| Need | Preferred tool | Use it for |
| --- | --- | --- |
| Unknown implementation, similar patterns, broad discovery | Semble | Semantic exploration and analogous code |
| Known symbols, callers, dependencies, impact | CodeGraph | Symbol and relationship queries |
| Shell commands or large output | RTK | Running or compressing terminal output |
| Straightforward file or text work | `rg`, `git`, project tools | Direct local operations |
| None of the above | N/A | Do not force a call |

Select the route before acting, then use the selected tool when available and relevant. Do not run ceremonial health checks for unrelated tools. Investigate a first-use failure only when the tool is a real dependency of the task. Automatically repair only paths and registrations owned by this harness; do not silently reinstall, upgrade, or alter an independent user tool.

Tool output is evidence, not a conclusion. Cross-check source, build, test, or runtime results when the task depends on them. Never put credentials or private machine paths in PR evidence.

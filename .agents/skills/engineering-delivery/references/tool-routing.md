# Required tool bootstrap and routing

Before task work, V23's native UserPromptSubmit hook must health-check and
actually use CodeGraph, Semble, and RTK once. This is a user-specific hard
requirement, not general defensive process. The hook is intentionally the only
V23 hook and is never a Stop hook.

| Need | Required tool | Use it for |
| --- | --- | --- |
| Every new task | CodeGraph | Index status/sync plus a real file query |
| Every new task | Semble | A bounded semantic search using the user prompt |
| Every new task | RTK | A compact Git workspace status or directory inspection |
| Straightforward file or text work | `rg`, `git`, project tools | Direct local operations |
| Follow-on investigation | Best-fit tool | Expand only if it changes the decision |

The CodeGraph cache is Git-local and ignored through a V23-marked
info/exclude block; it is not committed and no daemon is started. In a non-Git
directory, CodeGraph can only perform an executable probe because no project
graph exists.

If any required tool fails, repair the named tool before unrelated task work.
Only repair V23-owned setup automatically; never silently reinstall, upgrade,
or alter independent user tools. Tool output is evidence, not a conclusion.
Cross-check source, build, test, or runtime results when the task depends on
them. Never put credentials or private machine paths in PR evidence.

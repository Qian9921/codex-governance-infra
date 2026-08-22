# Required tool bootstrap and routing

V23 runs one small, real operation through CodeGraph, Semble, and RTK when each
new user task is submitted. This is an explicit local operating requirement,
not a generic recommendation. The native UserPromptSubmit hook supplies the
bootstrap; it uses no Stop hook, scheduler, daemon, or task database.

| Situation | Preferred tool |
| --- | --- |
| Every task: source index/query | CodeGraph |
| Every task: semantic search | Semble |
| Every task: compact workspace command | RTK |
| Follow-on investigation | The tool whose result best changes the decision |

For a Git checkout, the bootstrap keeps a V23-marked .codegraph/ cache
exclusion in that checkout's Git-local info/exclude; it never changes the
repository's .gitignore or enables a CodeGraph daemon. The first use may
create the local cache, then later tasks sync and query it. In a non-Git
directory, CodeGraph performs a version probe because there is no repository
source graph to initialize.

Normal repository tools such as `rg`, `git`, and the project's own test
commands remain available for the actual task. The bootstrap does not replace
their use.

When a required tool fails, repair only V23-owned setup automatically. Do not
silently reinstall, upgrade, or reconfigure an independent user tool. Report
the specific blocker and repair it before unrelated implementation. Tool output
is evidence, not a conclusion.

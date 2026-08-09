# Personal Codex infrastructure and context budget

V19 installs only personal Codex infrastructure. It does not create repository,
team, administrator, or managed-enterprise configuration. The installer owns
only manifest-listed files below the selected `CODEX_HOME` and the three V19
skills below its sibling personal `.agents/skills` root.

## Native ownership map

| Concern | Personal Codex surface | Load behavior | V19 owner |
|---|---|---|---|
| Durable constitution and routing | `~/.codex/AGENTS.md` | always loaded | thin kernel |
| Execution, contract, review, triage roles | `~/.codex/agents/*.toml` | selected subagent | four single-role agents |
| Conditional workflows | `~/.agents/skills/*/SKILL.md` | metadata first, body on match | three V19 skills |
| Lifecycle integrity and task binding | `~/.codex/hooks.json` plus scripts | matching event only | V19 hooks |
| Catastrophic command safety | `~/.codex/rules/*.rules` | runtime policy, not prompt | safety rules |
| Explicit strict runtime defaults | `~/.codex/governance-strict.config.toml` | selected profile only | strict profile |
| Tool and data integrations | existing `~/.codex/config.toml` MCP tables | tool metadata on enablement | user configuration |
| Useful cross-session context | generated `~/.codex/memories` state | selected recall | Codex memory runtime |

The managed overlay deliberately does not rewrite the user's main
`config.toml`, install plugin caches, edit generated memories, or revive
deprecated custom prompts. Existing MCP servers, plugins, providers, permissions,
memory choices, and unrelated custom agents or rules remain user-owned.

## Context budget

Codex skill discovery uses progressive disclosure: the initial context contains
skill names and descriptions, and the full `SKILL.md` is loaded only after a
match. The official skill catalog itself is bounded to 2% of the model context,
or 8,000 characters when the context size is unknown. Installing every useful-
looking workflow globally can therefore shorten or evict the descriptions that
matter most.

V19 uses these budgets for a 256k-context subscription account:

- installed `AGENTS.md`: at most 10 KiB;
- SessionStart/SubagentStart context: at most 700 characters;
- per-prompt intake context: at most 900 characters, including strict identity;
- each V19 `SKILL.md`: at most 3 KiB;
- three globally discoverable V19 skills and four single-role subagents;
- no repeated checklist or long command block in the always-loaded kernel.

The Addy Osmani `agent-skills` project informed the workflow/persona split and
progressive-disclosure design. V19 does not install that entire catalog: its
large collection would compete with existing personal, system, and plugin skill
descriptions, while several workflows overlap V19 ownership. V19 reuses the
useful shape—short activation metadata, focused workflow bodies, scripts for
deterministic work—without creating a second governance framework.

## Failure modes this design prevents

| Failure | Prevention |
|---|---|
| Global instructions consume every turn | fixed kernel budget; conditional detail moves to skills |
| Hooks repeat the constitution on every prompt | hooks emit only mode, integrity, and strict intake identity |
| A role prompt becomes a workflow router | each agent has one role; skills own the workflow |
| All skills activate or descriptions are truncated | only three non-overlapping V19 skills are installed |
| Deterministic safety depends on model obedience | hooks and `.rules` own mechanical enforcement |
| Strict ceremony penalizes ordinary work | strict proof and profile remain explicit and lazy |
| Local tools or memories become package truth | config, generated state, caches, and credentials remain outside the overlay |
| Installation corrupts either personal root | both roots are allowlisted, backed up, atomically replaced, and rolled back together |

## Profile use

The default remains adaptive. Start an explicit strict session with:

```bash
CODEX_GOVERNANCE_MODE=strict codex --profile governance-strict
```

The strict profile increases reasoning, uses on-request approvals, narrows the
sandbox to workspace-write, and keeps hooks and multi-agent enabled. It does not
replace the user's model, provider, MCP servers, plugins, or memory policy.

## Sources

- [OpenAI Codex customization](https://learn.chatgpt.com/docs/customization/overview)
- [OpenAI Codex skills](https://learn.chatgpt.com/docs/build-skills)
- [OpenAI Codex subagents](https://learn.chatgpt.com/docs/agent-configuration/subagents)
- [OpenAI Codex hooks](https://learn.chatgpt.com/docs/hooks)
- [OpenAI Codex rules](https://learn.chatgpt.com/docs/agent-configuration/rules)
- [Addy Osmani agent-skills](https://github.com/addyosmani/agent-skills)

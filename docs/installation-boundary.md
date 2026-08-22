# Installation boundary

Installation is deliberately narrow. It makes the portable policy and logical role templates available to Codex, installs the single delivery Skill and its references, and configures the small local adapters needed for the required task bootstrap and authorized GitHub delivery.

## Ownership

Every inserted block has an explicit project ownership marker. Every generated asset has an entry and exact digest in the V23 local manifest. The installer may create or update only:

- a project-owned file that is absent or still owned by this project;
- a marked block whose start and end markers are both present and intact;
- the project-owned local state needed by the helper itself.

If a target exists without the expected marker and contains user content, installation stops instead of overwriting it. A broken or partially edited marker also stops and reports the exact target for manual repair. The installer never restores an old snapshot over current user configuration.

## Preserved content

User-authored rules, unrelated agents, credentials, personal tools, existing integrations, and unrelated configuration remain untouched. Local account mappings, model mappings, tool paths, and the local greeting are environment data; they are not part of the portable repository.

The project does not manage or automatically start external tools that happen to be available on the machine. The sole exception is one UserPromptSubmit hook, installed as a marked inline config block, which runs a bounded CodeGraph, Semble, and RTK bootstrap for each new user task. Each operation has a small fixed budget whose worst-case sequence stays inside the Hook budget, so a stalled CodeGraph probe still leaves time for Semble and RTK. It does not use a Stop hook, daemon, background service, or project-tracked index. CodeGraph's cache exclusion is Git-local and marked before a V23-created cache is initialized.

## Upgrade and uninstall

An upgrade updates only the project's marked content and owned files. It does not scan the home directory or infer ownership from names. If the effective global instruction path changes (for example, an override is introduced), it stops rather than moving ownership between files. Uninstall removes a V23 installation only when its dependent blocks and generated assets are all still intact; if any owned item was edited, it preserves the complete unit and reports the conflict for manual resolution.

## Verification

After installation, use Codex's hook browser once to review and trust the V23 UserPromptSubmit command. Codex requires explicit trust for non-managed command hooks; the installer never bypasses that protection. The doctor command reports the effective policy source, local marked blocks, prompt hook, logical role files, real bounded tool probes, and configured GitHub adapter. Its first CodeGraph probe can create the V23-marked Git-local cache exclusion and index, just as the task hook can. Its local terminal output may name local files for repair, but it never prints credentials and that output must not be committed. Start a new Codex task after changed instructions or hook configuration are installed.

The primary mapping is a native local profile named `v23-primary`; launch it with `codex --profile v23-primary`. Its top-level model, reasoning effort, and `review_model` are rendered from local configuration. Executor and reviewer remain separate native custom agents.

## Recovery

If installation stops, preserve the target and its current contents. Read the reported path, repair or remove only the conflicting marker with the user's intent, and run the installer again. Do not use broad deletion or restore commands.

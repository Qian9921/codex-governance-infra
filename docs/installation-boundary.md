# Installation boundary

Installation is deliberately narrow. It makes the portable policy and logical role templates available to Codex, installs the single delivery Skill and its references, and configures only the small local adapter needed for authorized GitHub delivery.

## Ownership

Every inserted block has an explicit project ownership marker. Every generated asset has an entry and exact digest in the V23 local manifest. The installer may create or update only:

- a project-owned file that is absent or still owned by this project;
- a marked block whose start and end markers are both present and intact;
- the project-owned local state needed by the helper itself.

If a target exists without the expected marker and contains user content, installation stops instead of overwriting it. A broken or partially edited marker also stops and reports the exact target for manual repair. The installer never restores an old snapshot over current user configuration.

## Preserved content

User-authored rules, unrelated agents, credentials, personal tools, existing integrations, and unrelated configuration remain untouched. Local account mappings, model mappings, tool paths, and the local greeting are environment data; they are not part of the portable repository.

The project does not manage or automatically start external tools that happen to be available on the machine. It does not install or enable hook, daemon, or index behavior by default.

## Upgrade and uninstall

An upgrade updates only the project's marked content and owned files. It does not scan the home directory or infer ownership from names. If the effective global instruction path changes (for example, an override is introduced), it stops rather than moving ownership between files. Uninstall removes a V23 installation only when its dependent blocks and generated assets are all still intact; if any owned item was edited, it preserves the complete unit and reports the conflict for manual resolution.

## Verification

After installation, the doctor command reports the effective policy source, local marked blocks, logical role files, and configured GitHub adapter. Its local terminal output may name local files for repair, but it never prints credentials and that output must not be committed. A new Codex task may be required before changed instructions are loaded.

The primary mapping is a native local profile named `v23-primary`; launch it with `codex --profile v23-primary`. Its top-level model, reasoning effort, and `review_model` are rendered from local configuration. Executor and reviewer remain separate native custom agents.

## Recovery

If installation stops, preserve the target and its current contents. Read the reported path, repair or remove only the conflicting marker with the user's intent, and run the installer again. Do not use broad deletion or restore commands.

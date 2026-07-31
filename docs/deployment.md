# Deployment

Use an isolated `CODEX_HOME` for verification. `install-governance.py --dry-run` prints the
allowlisted plan; a real install overlays only managed `codex/` files with descriptor-relative
atomic replacement and a sibling backup. `--rollback` restores the backup after validating
bytes, modes, object types, and canonical ledger paths. Live global Codex home deployment
requires a separately authorized governance lane and exact manifest/hash review.

## Transaction contract

Installation overlays managed files at `$CODEX_HOME/AGENTS.md`, `$CODEX_HOME/BRIEF-TEMPLATES.md`,
`$CODEX_HOME/hooks.json`, `$CODEX_HOME/hooks/`, and `$CODEX_HOME/contracts/`; it never replaces
the entire home. A task-owned transaction state and private managed backup record exact previous
hashes and modes. Existing unrelated files remain byte-for-byte. Backup/state collisions,
symlink ancestors, non-regular files, and noncanonical ledger paths are refused. Failure
injection after every discovered mutation restores the pre-install snapshot. Rollback verifies
current managed hashes and modes, refuses unexpected edits, restores previous managed files,
removes only newly created managed files, and retains unrelated/post-install files. A forced
restore failure leaves state, backup, and any partial-failure assets actionable instead of
silently deleting recovery material.

## Native delegation boundary

The platform's native collaboration spawn event is not universally observable by these files.
The bridge therefore accepts only an exact `SubagentStart` payload whose model/task identity,
packet self-hash, canonical repository snapshot, lease, and persisted mission state match the
registered packet. In delegation mode `pre_tool_use_policy.py` is the enforceable fail-closed
boundary: only explicit native read/write and recognized CodeGraph/Semble observation schemas
are allowed inside the lease; pathless/unknown tools, shell/Bash, Git/GitHub, review,
approval, merge, and external mutation capabilities are denied. Ordinary non-delegated hooks
retain their existing model-unrestricted routing behavior.

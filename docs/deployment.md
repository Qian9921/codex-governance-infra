# Deployment

Use an isolated `CODEX_HOME` for verification. `install-governance.py --dry-run` prints the allowlisted plan; a real install copies only `codex/` with atomic temp replacement and a timestamp-independent sibling backup. `--rollback` restores the backup. Live the live global Codex home deployment requires a separately authorized governance lane and exact manifest/hash review.

## Transaction contract

Installation overlays managed files at `$CODEX_HOME/AGENTS.md`, `$CODEX_HOME/BRIEF-TEMPLATES.md`, `$CODEX_HOME/hooks.json`, `$CODEX_HOME/hooks/`, and `$CODEX_HOME/contracts/`; it never replaces the entire home. A task-owned transaction state and private managed backup record exact previous hashes. Existing unrelated files remain byte-for-byte. Backup/state collisions are refused. Failure injection after each mutation restores the pre-install snapshot. Rollback verifies current managed hashes, refuses unexpected edits, restores previous managed files, removes only newly created managed files, and retains unrelated/post-install files.

# Deployment

Use an isolated `CODEX_HOME` for verification. `install-governance.py --dry-run` prints the allowlisted plan; a real install copies only `codex/` with atomic temp replacement and a timestamp-independent sibling backup. `--rollback` restores the backup. Live the live global Codex home deployment requires a separately authorized governance lane and exact manifest/hash review.

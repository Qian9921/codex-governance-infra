# Privacy

The tracked package intentionally excludes sessions, prompts, transcripts, receipt JSONL, credentials, tokens, plugin/cache/connection state, model caches, and user data. `$CODEX_HOME` is represented only as a placeholder. `scripts/verify-governance.py` scans tracked files for forbidden paths and token/session/receipt patterns and verifies the manifest. Sanitized evidence records counts and hashes, never raw runtime data.

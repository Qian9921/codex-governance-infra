# Security policy

## Scope

This repository contains portable workflow guidance and narrowly scoped local-installation helpers. Treat credentials, account configuration, local paths, and external system actions as environment data rather than repository data.

## Reporting

Do not open a public issue for a suspected credential exposure or security-sensitive defect. Report it privately to the repository owner with the affected revision, reproduction, impact, and safe mitigation when available.

## Development rules

- Never commit credentials, tokens, private keys, or machine-specific authentication files.
- Keep author and reviewer authentication contexts separate on the local machine.
- Do not place private local paths or secret command output in Pull Requests.
- Review external or irreversible actions separately from ordinary repository work.
- Prefer least privilege and read-only inspection when write access is unnecessary.

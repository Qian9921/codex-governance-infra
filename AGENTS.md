# Repository contribution rules

- Keep governance portable and privacy-safe; never commit live sessions, receipts, secrets, or machine paths.
- Python implementation uses the standard library only.
- Changes must preserve the v15 acceptance envelope, exact identity, lease, and review gates.
- Run `python3 scripts/verify-governance.py --repo .` and the contract test suite before review.
- The repository is authored by Qian9921; Liang9921 is the independent governance reviewer.

# Privacy threat model

Threats include accidental session/receipt export, credential/token leakage, machine-path disclosure, and installer overreach. Controls: explicit tracked-file scanner, forbidden path/pattern checks, sanitized placeholders, allowlisted copy set, hash manifest, no runtime secret reads, isolated deployment, and rollback. Residual risk: a novel secret format not covered by the scanner; review must inspect any new data-bearing fixture.

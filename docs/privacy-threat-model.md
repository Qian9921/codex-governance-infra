# Privacy threat model

Threats include accidental session/receipt export, credential/token/secret/password leakage,
raw runtime identifiers, machine-path disclosure, local temporary-artifact residue, forbidden
filenames, symlink traversal, and installer overreach. `manifest.json` is the mechanical privacy
source of truth: it carries an exact versioned rule set and exact scoped placeholder values.
`scripts/verify-governance.py` validates the manifest schema and immutable rule fingerprints,
then applies those rules to every tracked byte, filename, encoding, and symlink. Raw JSON/YAML or
unquoted IDs (including bracket/list/map forms), generic assigned credential values, tokens,
portable Linux/macOS/Windows raw or escaped paths, decorated forbidden names, and local temporary
paths are RED. A placeholder is GREEN only when it is the complete field value; adjacent real
content remains RED. Negative matrix payloads are assembled at runtime so the tracked manifest
contains no private corpus. Residual risk is limited to a novel secret format not represented by
the versioned mandatory classes; changing or weakening a class fails verification.

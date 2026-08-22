# Python delivery

Inspect the repository's formatter, type checker, supported Python versions, and test commands before choosing checks.

- Keep modules and functions focused; preserve the public API unless the request changes it.
- Prefer explicit, typed boundaries and ordinary data structures over clever indirection.
- Use context managers for resources and preserve exception context; do not catch broadly without a concrete recovery policy.
- Keep imports, dependency changes, and compatibility shims minimal and justified by the target runtime.
- Add or update the narrowest test that demonstrates the changed behavior, then run relevant project checks.
- Do not mix formatting-only churn, broad typing migrations, and functional changes.

Use the [Google Python Style Guide](https://google.github.io/styleguide/pyguide.html) and [PEP 8](https://peps.python.org/pep-0008/) as references, subject to project-local rules.

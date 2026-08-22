# C++ delivery

Read the target project's existing style and build instructions first; local conventions win over generic preferences.

- Keep interfaces small and ownership explicit. Prefer RAII and value semantics when they make lifetime clear.
- Use `const` and references deliberately; avoid unclear ownership and lifetime.
- Preserve established namespaces, error handling, build targets, and ABI/API constraints.
- Make the smallest behavior change. Do not mix formatting churn or speculative refactors into a bug fix.
- Test the changed contract at the narrowest useful level, then run the relevant project build or integration target.
- Treat numerical tolerance, dimensions, units, and edge cases as part of the interface.

Use the [Google C++ Style Guide](https://google.github.io/styleguide/cppguide.html) for readability and interface design, subject to project-local rules.

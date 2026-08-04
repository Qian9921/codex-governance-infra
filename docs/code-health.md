# Code health and reuse baseline

## Authority order

1. explicit repository architecture, ownership, and API contracts;
2. repository formatter, linter, compiler, and contribution rules;
3. applicable official Google language/style guidance;
4. reviewer preference, which is non-blocking unless promoted by an owned rule.

This order prevents a generic standard from silently changing a project's
language version, exception policy, formatting, or public contract.

## Official source registry

| Area | Canonical source | Use |
|---|---|---|
| Language style index | https://google.github.io/styleguide/ | Select the task language |
| C++ | https://google.github.io/styleguide/cppguide.html | C++ readability, ownership, classes, inheritance, naming |
| Python | https://google.github.io/styleguide/pyguide.html | Python language and style rules |
| Java | https://google.github.io/styleguide/javaguide.html | Java source style |
| Code review | https://google.github.io/eng-practices/review/ | Review standard and workflow |
| Review checklist | https://google.github.io/eng-practices/review/reviewer/looking-for.html | Design, functionality, complexity, tests, naming, comments, style |
| Sustainable rules | https://abseil.io/resources/swe-book/html/ch08.html | Rule design, automation, readability, consistency |

The installer should not inject these documents into every prompt. The global
policy carries the stable summary; an agent opens only the relevant upstream
section when a decision needs it. A future updater may cache permitted content
with URL, retrieval time, license metadata, and content hash, but the task hot
path remains retrieval-based and bounded.

## Reuse before creation

Run this decision for a meaningful new class, module, algorithm, adapter,
service, schema helper, or utility—not for every local variable or test helper.

```text
Intent:       capability being added
Candidates:   existing owners and similar implementations
Decision:     REUSE | EXTEND | NEW
Contract:     semantics, units, lifetime, errors, performance, compatibility
Boundary:     owning module and allowed dependency direction
Evidence:     query or source references that support the choice
```

### REUSE

Choose when semantics, ownership, units, lifetime, errors, and supported domain
match. Prefer the established public path over a second convenience wrapper.

### EXTEND

Choose when the existing component owns the responsibility but lacks one
cohesive capability. Preserve substitutability and dependency direction. Add a
small API only when it simplifies real consumers.

### NEW

Choose only when no matching owner exists. The new abstraction needs:

- one clear responsibility and owner;
- a real current consumer or vertical slice;
- a valid dependency direction;
- a smaller maintenance burden than duplicated logic;
- focused tests and a rollback boundary.

Do not generalize for hypothetical consumers.

## Inheritance and shared foundations

Existing base classes and shared domain utilities are assets when their contract
matches. They are not mandatory merely because they exist. Prefer composition
when behavior can be owned directly. Use inheritance for a genuine `is-a`
relationship or an established interface. Avoid multiple implementation
inheritance, deep hierarchies, public mutable state, and god classes.

When an existing abstraction does not match, record the mismatch instead of
silently forking similar logic. Domain-owned parsers, math, units, coordinate
frames, data transformations, error models, and fixtures are especially strong
reuse candidates.

## Architecture cleanliness

For each changed boundary, check:

- code lives in the module that owns the responsibility;
- dependencies point toward stable ownership, not back from lower layers;
- public API surface is no larger than the current outcome needs;
- names communicate domain meaning and units;
- state, lifetime, error handling, and concurrency are explicit;
- tests use shared fixtures/builders when their contracts match;
- functional changes are not mixed with broad formatting or unrelated cleanup.

## Enforcement split

Machines own deterministic checks: formatting, imports/includes, compiler
warnings, lint, static analysis, forbidden dependencies, cycles, and tests.

The writer owns reuse discovery and implementation evidence. The reviewer owns
design fit, excessive complexity, contract correctness, and finding priority.
`NIT` and personal style do not block. A change is ready when it improves the
codebase and meets its frozen acceptance envelope; perfection is not required.

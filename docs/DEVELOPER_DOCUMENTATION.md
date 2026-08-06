# Developer Documentation

The project uses Doxygen to publish one navigable reference for architecture
documents and every deployed Python function.

## Build The Reference

Install Doxygen and run:

```bash
make docs
```

Open `build/doxygen/html/index.html` after the command succeeds. The build also
creates XML output in `build/doxygen/xml` for automated validation.

`make docs-check` runs the same reproducible build and completeness gate used
by CI.

## Source Coverage

The production source set consists of:

- the complete `venus_evcharger` package
- the six root service and command entry points
- operational Python tools in `scripts/ops`

Tests, mutation worktrees, virtual environments, and developer-only scripts
are excluded.

`EXTRACT_ALL`, `EXTRACT_PRIVATE`, and `EXTRACT_STATIC` expose public and private
implementation details. A build-only Python filter adds a grammatical,
identifier-derived English Doxygen brief to every callable that lacks native
documentation. A generated inventory additionally records every function,
method, and nested callable with its source path and line. Nested functions are
listed in the inventory because Doxygen does not emit them as separate members.
Neither mechanism modifies deployed files or increases runtime memory use on
Venus OS.

## Writing Documentation

Meaningful behavior, contracts, invariants, units, failure modes, and side
effects belong in native English docstrings close to the code. Prefer a concise
summary followed by the details a maintainer needs:

```python
def select_budget(load: float, available: float) -> float:
    """Return the usable power budget in watts.

    The result never exceeds the available power and never becomes negative.

    Args:
        load: Current controlled load in watts.
        available: Power currently available for charging in watts.

    Returns:
        The non-negative charging budget in watts.
    """
```

Doxygen understands Python docstrings and explicit commands such as `@brief`,
`@param`, `@return`, and `@raises`. Use explicit commands only when they make a
contract clearer; normal Python docstrings remain the preferred source format.

Generated briefs are a completeness fallback, not a replacement for explaining
non-trivial domain behavior. New public APIs and complex private logic should
therefore receive native English docstrings during implementation.

## Quality Contract

The documentation gate verifies that:

- every production Python file can be parsed
- every production callable appears in the generated inventory
- Doxygen emits HTML and XML output
- the exact number of emitted Doxygen members matches the non-nested callable
  count in the inventory
- every emitted function has an English build-time brief or richer source
  documentation

The generated `build/` tree is intentionally not committed.

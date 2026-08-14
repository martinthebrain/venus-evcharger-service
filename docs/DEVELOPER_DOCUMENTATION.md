# Developer Documentation

The project uses Doxygen to publish one navigable reference for architecture
documents and every deployed Python function. Rust APIs use Rustdoc, whose
parser and link checker understand Rust syntax and documentation semantics.

## Build The Reference

Install Doxygen and run:

```bash
make docs
```

Open `build/doxygen/html/index.html` after the command succeeds. The build also
creates XML output in `build/doxygen/xml` for automated validation.

`make docs-check` runs the same reproducible build and completeness gate used
by CI.

Build and validate the Rust observer reference with:

```bash
bash rust/forensic-observer/scripts/check.sh
```

That gate runs Rustdoc with warnings denied. The observer README and contracts
are also included in the Doxygen architecture reference.

## Source Coverage

The production source set consists of:

- the complete `venus_evcharger` package
- the five root service and command entry points
- operational Python tools in `scripts/ops`
- the native forensic observer under `rust/forensic-observer`, documented by
  Rustdoc

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

For Rust, use `///` on items and `//!` for module-level contracts. Public Rust
items must pass the warnings-as-errors Rustdoc build.

## Quality Contract

The documentation gate verifies that:

- every production Python file can be parsed
- every production callable appears in the generated inventory
- Doxygen emits HTML and XML output
- the exact number of emitted Doxygen members matches the non-nested callable
  count in the inventory
- every emitted function has an English build-time brief or richer source
  documentation
- every native observer API passes Rustdoc with warnings denied

The generated `build/` tree is intentionally not committed.

## DBus Gateway Boundary

Only modules below `venus_evcharger/dbus_adapter` may depend on Victron DBus.
Transport primitives have narrower owners inside that package: the connection
manager owns the private system-bus connection and is the only component that
unfolds a `DbusWireRequest` into the concrete `dbus-python.call_async` API. The
asynchronous broker owns single-flight execution and callback lifecycle, the
process loop owns GLib integration, and the publication registry owns the
local `VeDbusService` instances. Repository architecture checks enforce these
ownership rules.

External reads, writes, discovery calls, and introspection calls use the
callback-based broker. The broker permits one external operation at a time,
applies a monotonic deadline, cancels overdue pending calls when possible, and
ignores callbacks from obsolete operation generations. Local publication to
the gateway's own EV charger service remains synchronous because it does not
wait on an external device.

Durable command files remain owned by the queue until their asynchronous
completion callback reports `applied` or `dropped`. A `deferred` result keeps
the command available for a later retry. Every dispatched command accepts only
its first completion result. Rewrites and retirement use the mailbox revision
captured at dispatch time, so a late callback cannot replace or remove a newer
coalesced command generation. Broker cancellation clears transport ownership
without invoking normal command-error callbacks, leaving durable work queued
for the next process generation. New gateway operations must preserve this
lifecycle and use the semantic IPC contracts rather than exposing DBus service
names or object paths to backend modules.

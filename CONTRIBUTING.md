# Contributing

Thanks for helping shape `venus-evcharger-service`.

This project grows through real charging setups, careful field feedback, solid
tests, and small changes that keep the outward behavior crisp on Venus OS.

## Good Contribution Areas

Typical contributions include:

- new meter, switch, and charger backends
- better support for real wallbox topologies
- Auto and Scheduled policy refinements
- GX deployment and updater improvements
- diagnostics and troubleshooting improvements
- documentation and configuration guidance

## Local Verification

Run the main checks before opening a PR:

```bash
bash ./scripts/dev/check_all.sh
```

Useful extra commands:

```bash
./scripts/dev/run_lint.sh
./scripts/dev/run_pylint_audit.sh
./scripts/dev/run_security_audit.sh
./scripts/dev/run_spellcheck.sh
./scripts/dev/run_quality_audit.sh
./scripts/dev/run_optional_audits.sh
./scripts/dev/run_typecheck.sh
./scripts/dev/run_stress_tests.sh
make lint
make pylint-audit
make security-audit
make spellcheck
make shell-audit
make quality-audit
make audit
make check
make typecheck
make stress
```

For DBus gateway or GUI-visible EVCS changes, run the Raspberry-Pi release gate
when the Pi test target is available:

```bash
python3 scripts/dev/pi_gateway_release_gate.py \
  --pi root@192.168.142.129 \
  --deploy \
  --configure-host \
  --start-host-shelly \
  --restart
```

This deploys the current workspace to the Pi, drives a host-side Shelly
simulator over the real network path, runs offline gateway chaos scenarios on
the Pi, checks gateway health/lifecycle logs, and verifies GUI-visible DBus
values and writes.

The optional audit target runs the focused pylint audit, a high-severity Bandit
security audit, spellcheck, and the shell audit when `shellcheck` and `shfmt`
are installed. The individual `make shell-audit` target requires both system
tools explicitly.

For documentation-only changes, mention that the change is documentation-only
and whether runtime verification was skipped.

## Architecture References

These documents carry the main project contracts:

- [README.md](README.md)
- [INSTALL.md](INSTALL.md)
- [CONFIGURATION.md](CONFIGURATION.md)
- [CHARGER_BACKENDS.md](CHARGER_BACKENDS.md)
- [SHELLY_PROFILES.md](SHELLY_PROFILES.md)
- [DIAGNOSTICS.md](DIAGNOSTICS.md)
- [TROUBLESHOOTING.md](TROUBLESHOOTING.md)
- [STATE_MODEL.md](STATE_MODEL.md)
- [DBUS_GATEWAY.md](DBUS_GATEWAY.md)
- [VENUS_DBUS_SURFACE.md](VENUS_DBUS_SURFACE.md)

When a change touches behavior, these documents are part of the review surface.

## Project Rules For Behavioral Changes

Keep these rules stable:

- outward truth follows a fixed priority order
- explicit sources win over heuristics
- user-facing runtime intent and safety degradations may persist
- transient transport and retry noise stays transient
- runtime overrides are for policy and tuning values
- topology rules stay explicit and test-backed

The compact normative spec lives in [STATE_MODEL.md](STATE_MODEL.md).

## What To Add For Different Change Types

### New Backend

A backend contribution should usually include:

- backend registration
- config validation or probe coverage
- realistic test coverage for read and write behavior
- at least one integration-style path when the backend affects runtime flow
- documentation in the relevant backend guide

### Policy Change

A policy contribution should usually include:

- one or more scenario tests
- outward-state or priority coverage
- persistence/restart thought-through behavior
- documentation update when a user-visible path or policy changes

### GX / Bootstrap / Updater Change

A GX-focused contribution should usually include:

- shell-tool compatibility for Venus OS
- update or rollback coverage when deployment flow changes
- install or troubleshooting documentation updates

### Documentation Change

A documentation contribution should aim for:

- `README.md` as the entry point
- deeper operational details in the focused documents
- concise, direct language
- quick scanning from a real user’s point of view

## Test Philosophy

This repository benefits from several kinds of tests:

- unit-scale behavior tests
- scenario-based end-to-end tests
- conflict-matrix tests for composed backends
- configuration-space validation tests
- outward-state contract tests
- restart and persistence tests

When you add a feature, choose the test type that best proves the new truth.

## CodeQL And Coverage Hygiene

Treat CodeQL and Codecov comments as review input, not as noise to suppress.
Preferred fixes are small, explicit, and test-backed:

- remove or minimize sensitive data at rest before adding suppression comments
- keep temporary wizard/live-check files redacted and owner-only when secrets are involved
- sanitize or allowlist HTTP headers instead of passing caller-provided names through
- add regression tests for every security helper that exists because of a scanner finding
- keep patch coverage high by testing both the safe path and the rejected/sanitized path

If a finding is intentionally accepted, document why the behavior is required on
Venus OS and what limits the blast radius.

## Questions To Answer Before Opening A Behavioral PR

For a feature that changes runtime behavior, answer these questions clearly:

1. Which truth is authoritative when signals disagree?
2. What persists across restart, and what stays transient?
3. Which configurations remain explicitly supported for this change?
4. Which tests prove the intended behavior?
5. Which document now explains the feature to users or operators?

These answers often matter more than the code diff itself.

## Practical Style Notes

- keep changes local where possible
- prefer explicit contracts over hidden coupling
- keep the outward state easy to reason about
- group documentation by user task, not by internal file layout
- prefer concrete examples over abstract wording

## Pull Request Notes

A helpful PR description usually includes:

- what setup or use case motivated the change
- what changed at runtime
- which topologies were considered
- which tests were run
- which docs were updated

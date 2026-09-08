# Rust Forensic Observer

This crate implements the external read-only forensic observer used by the
Venus EV charger service. It replaces the former Python observer while keeping
the incident policy and artifact contract. The process has no DBus dependency;
all GX state crosses the semantic gateway-diagnostics boundary.

Run the native checks:

```sh
./scripts/check.sh
```

Build the ARMv7 Venus OS binary:

```sh
./scripts/build-armv7.sh
```

The release build uses the digest-pinned Rust container and pinned GNU ARM
cross-toolchain packages by default so local and CI builds use the same
toolchain. An explicitly non-release host build can be requested with
`VENUS_EVCHARGER_OBSERVER_USE_HOST_TOOLCHAIN=1` when Cargo and the ARM GNU
cross-linker are installed. The cross-build writes
`deploy/venus/bin/venus-evcharger-forensic-observer` at the repository root.
The regular Venus installer validates and starts that binary; target devices do
not require a Rust toolchain.

Validate a deployed configuration without starting the observation loop:

```sh
venus-evcharger-forensic-observer --validate-config \
  /data/dbus-venus-evcharger/config.venus_evcharger.ini
```

The observer stores incident bundles only on recognized removable storage. It
does not use internal flash as a fallback. One uninterrupted failure episode
creates one bundle. After confirmed recovery, the observer adds one atomic
`recovery.json` record to that bundle instead of producing periodic duplicate
incidents.

## Incident Retention

The main INI file accepts `ForensicRetentionDays=30` and
`ForensicMaxTotalMiB=100` in `[DEFAULT]`. Missing keys use these defaults.
Both values must be positive integers (at most 36500 days and 1048576 MiB).
Invalid values fail configuration validation; configuration changes need no
rebuild and are read by the running observer.

After the initial observation and then at most once per hour, cleanup removes
completed bundles oldest-first, based on their validated `recovery.json`
timestamp. A bundle becomes eligible at the configured age since recovery,
or earlier when the size budget is exceeded. The budget applies separately to
each recognized removable-storage forensic directory and counts direct regular
file sizes in `incident-*` directories, not filesystem allocation overhead.
At most 128 bundles are removed per directory and pass; candidate memory is
bounded to those 128 entries.

Active episodes, bundles without a valid recovery record, future-dated records,
unknown files, nested directories and symlinks are never deleted. Therefore the
size budget is best-effort: protected content or a large backlog can keep usage
above it. Unfinished bundles from previous process runs remain protected too.
An interrupted deletion can leave a protected incomplete bundle for manual
inspection. Cleanup errors do not restart the observer.

Cleanup uses a non-blocking exclusive maintenance lease, rechecks mount presence,
and pins filesystem operations to opened directories. It never creates an
artifact directory or falls back to internal flash. Writers or storage
maintenance defer cleanup until a later hourly pass; no new persistent counter
or cleanup log is written.

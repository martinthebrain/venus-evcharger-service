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

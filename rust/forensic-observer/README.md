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

The cross-build writes `deploy/venus/bin/venus-evcharger-forensic-observer` at the repository root. The regular Venus installer validates and starts that binary; target devices do not require a Rust toolchain.

Validate a deployed configuration without starting the observation loop:

```sh
venus-evcharger-forensic-observer --validate-config \
  /data/dbus-venus-evcharger/config.venus_evcharger.ini
```

The observer stores incident bundles only on recognized removable storage. It
does not use internal flash as a fallback.

# Release Process

Production releases are immutable signed bundles. A moving branch archive is a
development input, not a customer release.

## 1. Prepare The Candidate

1. Update `version.txt` and the release notes.
2. Commit all changes and create a clean, exact candidate commit.
3. Provision `/data/venus-evcharger-testbed` once on the dedicated Pi. The gate
   refuses every target without that marker, before deploying anything.
4. Run the `Release candidate hardware gate` workflow, or run locally:

   ```bash
   bash scripts/dev/run_release_candidate_gate.sh \
     --pi root@192.168.142.129 \
     --receipt /tmp/venus-evcharger-release-candidate.json
   ```

The gate runs host checks, audits and both coverage profiles. It then deploys
only to the dedicated Pi, runs policy and stress invariants, DBus gateway chaos,
the host-side Shelly simulator over the real network, and GUI-visible read/write
checks. Never place the testbed marker on a customer GX.

## 2. Tag The Exact Candidate

Create a signed `vX.Y.Z` tag whose version matches `version.txt`. The tag must
point to the exact commit recorded by the successful hardware-gate receipt.

## 3. Publish Signed Assets

The environment-protected `Publish signed release` workflow requires the tag
and the tested candidate commit. It refuses a mismatched tag or version, builds
the release bundle, signs both the manifest and bootstrap installer, verifies
them against `deploy/venus/bootstrap_manifest.pub`, and publishes immutable
release assets. The manifest authenticates the code bundle, updater, and every
shell library loaded by that updater.

Configure the protected `release` GitHub environment with reviewer approval and
the `VENUS_EVCHARGER_BOOTSTRAP_SIGNING_KEY_B64` secret. The private key is used
only on the ephemeral hosted release runner and is never copied to the Pi or a
GX device.

The production documentation pins the SHA-256 fingerprint of
`deploy/venus/bootstrap_manifest.pub`. A signing-key rotation therefore needs
an explicit documentation change and independent communication of the new
fingerprint; publishing a different key beside a release is not sufficient.

For an offline build, provide the private key path directly:

```bash
VENUS_EVCHARGER_BOOTSTRAP_SIGNING_KEY=/secure/release.key \
VENUS_EVCHARGER_SOURCE_COMMIT=$(git rev-parse HEAD) \
bash scripts/dev/build_signed_release.sh \
  /tmp/venus-evcharger-release \
  https://github.com/martinthebrain/venus-evcharger-service/releases/download/vX.Y.Z
```

## 4. Install Deliberately

Authenticate the initial release bundle before extracting it. Existing
installations use `VENUS_EVCHARGER_INSTALL_PROFILE=production` with the explicit
release manifest. Restore the production `noUpdate` marker after the deliberate
maintenance window.

Release evidence consists of the hardware-gate receipt, exact source commit,
signed manifest, bundle hash, updater hash and deployment receipt written by
the GX updater.

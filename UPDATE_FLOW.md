# Update Flow

This guide explains how the GX bootstrap and updater flow works, what it
updates, and how `noUpdate` changes that behavior.

## Goal

The update flow is designed for a simple first deployment on a GX device:

- place a small bootstrap script under `/data`
- run it there
- let it materialize or refresh the wallbox tree
- let it start the regular Venus installer from the refreshed tree

This keeps the first install lightweight while still supporting repeatable
updates and rollback-ready release directories.

The running service uses the same bootstrap entrypoint for remote availability
checks, DBus-triggered refresh runs, and the delayed post-boot update pass.

## Main Pieces

### `install.sh`

The bootstrap entrypoint.

Responsibilities:

- determine the local working directory
- check for `noUpdate`
- ensure a local updater is available
- refresh the codebase when updates are enabled
- run the updater at reduced CPU and I/O priority on constrained GX hardware
- atomically refresh this outer bootstrap after a verified promotion
- run the regular Venus installer from the resulting tree

The service calls this same script when a user triggers an update through DBus
or MQTT.

### `deploy/venus/bootstrap_updater.sh`

The updater that fetches or refreshes the wallbox tree.

Responsibilities:

- read update inputs from local sources or manifests
- refresh the target tree
- preserve live runit service-directory inodes during direct-layout updates
- preserve local deployment files
- prepare versioned release directories
- keep rollback targets available

### `deploy/venus/install_venus_evcharger_service.sh`

The regular Venus installer.

Responsibilities:

- restore executable bits
- register the runit service
- refresh the service wiring
- remove project processes left in deleted service directories by older updaters
- start the DBus adapter before the core and observer
- complete the local deployment

## What Gets Updated

The updater refreshes the wallbox codebase in the selected target directory.

That includes:

- Python service code
- deployment scripts
- runit service files
- documentation
- backend and policy code

The service checks for newer releases once per week. The default flow prefers
the bootstrap manifest and falls back to `version.txt` when needed.

When `noUpdate` is absent, the service also schedules one update run about one
hour after a GX reboot.

The updater also supports staged release layouts such as:

- `releases/<version>/`
- `current/`
- `previous/`

## What Stays Local

The updater keeps the local deployment shape intact where it matters for GX
operation.

Typical preserved items include:

- the local wallbox config
- channel markers such as `update-channel`
- the release pointers used for rollback

The wallbox config is preserved with an additive merge:

- existing local values stay unchanged
- missing sections and keys from the refreshed template are added
- newly introduced release options therefore appear automatically
- local comments and formatting stay intact for unchanged parts of the file
- if a merge rewrite is needed, the updater writes a timestamped
  `config.venus_evcharger.ini.bak-<timestamp>` backup first
- malformed local configs are left untouched rather than being rewritten during
  the merge step, and the updater records that merge skip reason in its status
  metadata

The shipped template also carries a config schema version. The updater uses
that schema value as the anchor for explicit future migration steps when keys
are renamed or their semantics change.

Before a refreshed tree is activated, the updater validates the resulting full
wallbox config. If validation fails, the update run aborts and the new release
is not promoted.

The updater records the latest apply run under `.bootstrap-state/`:

- `update_status.json` for the latest result
- `update_audit.log` as a compact append-only history
- `deployment_receipt.json` as the atomic identity and lightweight integrity
  record for the installed tree
- `installed_source_commit`, `installed_bundle_sha256`, and
  `installed_version` as small machine-readable identity markers

These artifacts include:

- old and new version
- whether the config merge changed anything
- which keys and sections were added
- whether validation passed
- whether the active `current/` release was kept
- why promotion was aborted when an update failed

Normal GitHub updates resolve the channel to one concrete commit before the
archive is downloaded. The updater then downloads that commit-specific archive
and records its SHA-256. Downloads have bounded connection/runtime attempts, so
a broken resolver or unreachable GitHub endpoint produces a failed update
instead of an indefinitely waiting installer.

The bootstrap launches the updater with a low CPU scheduling priority and, when
`ionice` is available, idle I/O priority. Child operations such as archive
decompression inherit those priorities. This keeps the GX services responsive
while an update is prepared; it does not weaken bundle or manifest validation.
The updater also applies these priorities to itself when invoked by an older
bootstrap, so the one-time transition to this update flow is resource-aware.

Updater archives, extraction trees, and staging layouts use an adaptive work
location. They are removed after every completed or failed run. Selection is:

1. RAM-backed `tmpfs` when both available system RAM and free filesystem space
   have a conservative safety margin.
2. A writable mounted SD card under `/media` or `/run/media`.
3. `<target>/.bootstrap-state/work` on `/data` as the reliable fallback.

This avoids internal-flash writes on capable systems, prefers removable media
under memory pressure, and never holds several complete code trees in scarce
RAM. A specific location can be selected with
`VENUS_EVCHARGER_UPDATER_WORK_ROOT`. The selected storage class and path are
recorded as `work_storage` and `work_root` in `update_status.json`.
The RAM margins are configurable through
`VENUS_EVCHARGER_UPDATER_RAM_MIN_MEM_AVAILABLE_KB` and
`VENUS_EVCHARGER_UPDATER_RAM_MIN_FILESYSTEM_AVAILABLE_KB`; an explicitly
managed SD work directory can be supplied through
`VENUS_EVCHARGER_UPDATER_SD_WORK_ROOT`.
The outer bootstrap's small updater-script downloads use `/tmp` independently;
if that location is unavailable, they fall back to the bootstrap state on
`/data`.

Only one updater may run at a time. Before hashing, extracting, staging, or
promoting a tree, it checks load averages, available RAM, and free work-volume
space. Heavy hash and extraction subprocesses are monitored while they run. If
pressure persists, the updater exits with a resource-pressure reason while the
previous validated installation remains active. The conservative defaults can
be adjusted for a known platform through the `VENUS_EVCHARGER_UPDATER_MAX_LOAD1`,
`VENUS_EVCHARGER_UPDATER_MAX_LOAD5`, `VENUS_EVCHARGER_UPDATER_MAX_LOAD15`,
`VENUS_EVCHARGER_UPDATER_MIN_MEM_AVAILABLE_KB`, and
`VENUS_EVCHARGER_UPDATER_MIN_DISK_AVAILABLE_KB` environment variables.

The service remains online while the bundle is downloaded, unpacked, merged,
validated, and promoted. Direct-layout promotion updates the existing runit
directories in place, so a supervisor cannot retain a deleted working
directory. Manifest updates never replace an existing release directory with
the same version; they select a hash-qualified release path instead. The
regular installer then performs one short, ordered service restart.

After a successful promotion, the updater atomically replaces the standalone
bootstrap that launched it. This matters for older field installations: the
next update therefore inherits current resource controls even when no formal
release bundle was built. `update_status.json` exposes this as
`bootstrap_entrypoint_path` and `bootstrap_refreshed`.

## Resource-Conscious Verification

The default deployment check reads the receipt and hashes only a small set of
critical entrypoint, gateway, contract, and installer files:

```bash
python3 scripts/ops/verify_venus_evcharger_deployment.py \
  /data/venus-evcharger/dbus-venus-evcharger
```

This is the normal GX check. It does not walk the full Python tree.

For a suspected corruption incident, create the expected full manifest on a
development host and transfer only that small JSON file to the GX:

```bash
python3 scripts/ops/verify_venus_evcharger_deployment.py \
  --create-manifest "$PWD" \
  --manifest-output /tmp/venus-evcharger-main-files.json
```

Then request the deliberately slow full comparison:

```bash
python3 scripts/ops/verify_venus_evcharger_deployment.py \
  /data/venus-evcharger/dbus-venus-evcharger \
  --full-manifest /tmp/venus-evcharger-main-files.json
```

The full mode hashes small batches, pauses between them, and defers work while
load per CPU or memory availability crosses its configured resource gates.

## Manifest-Based Updates

The bootstrap can consume a manifest-driven update flow.

Typical inputs:

- bundle location
- bundle hash
- updater location
- updater hash
- detached manifest signature

This allows one small bootstrap script to bring in a full prepared release.

## Signed Manifests

The update flow supports detached signatures for manifests through `openssl`.

Useful inputs:

- `VENUS_EVCHARGER_MANIFEST_SOURCE`
- `VENUS_EVCHARGER_BOOTSTRAP_PUBKEY`
- `VENUS_EVCHARGER_REQUIRE_SIGNED_MANIFEST`

This makes it possible to require a signed manifest before a refresh is
accepted.

## Release Layout And Rollback

The updater can prepare a versioned release directory first and then move the
runtime pointer to the new release.

Typical layout:

- `releases/1.0.0/`
- `releases/1.1.0/`
- `current/`
- `previous/`

If the freshly selected installer fails, the bootstrap can start the installer
from `previous/` and restore the last known good release path.

## `noUpdate`

Place a file named `noUpdate` next to the bootstrap script to freeze the local
installation at its current code state.

When `noUpdate` is present:

- the bootstrap skips the updater phase
- the runtime service reports update blocking on DBus
- manual DBus and MQTT update requests are rejected
- the local code tree is used as-is
- the regular Venus installer still runs from the local tree

This is useful when:

- a system should stay on a pinned code version
- field testing should continue without refreshes
- you want to inspect or patch the local tree manually before allowing updates

## Typical Update Variables

- `VENUS_EVCHARGER_TARGET_DIR`
- `VENUS_EVCHARGER_CHANNEL`
- `VENUS_EVCHARGER_SOURCE_COMMIT`
- `VENUS_EVCHARGER_DOWNLOAD_TIMEOUT_SECONDS`
- `VENUS_EVCHARGER_DOWNLOAD_ATTEMPTS`
- `VENUS_EVCHARGER_UPDATER_NICE_LEVEL`
- `VENUS_EVCHARGER_BOOTSTRAP_ENTRYPOINT`
- `VENUS_EVCHARGER_SOURCE_DIR`
- `VENUS_EVCHARGER_UPDATER_SOURCE`
- `VENUS_EVCHARGER_UPDATER_HASH_SOURCE`
- `VENUS_EVCHARGER_MANIFEST_SOURCE`
- `VENUS_EVCHARGER_BOOTSTRAP_PUBKEY`
- `VENUS_EVCHARGER_REQUIRE_SIGNED_MANIFEST`

## Practical Examples

### Regular bootstrap run

```bash
cd /data/bootstrap-venus-evcharger
./install.sh
```

### Preview an update without changing the target tree

```bash
bash deploy/venus/bootstrap_updater.sh --dry-run /data/shellyWB
```

The dry-run prints a JSON summary that includes:

- the detected current and candidate version
- which config keys and sections would be added
- whether validation would pass
- whether a backup would be created during a real apply run

### Freeze updates with `noUpdate`

```bash
cd /data/bootstrap-venus-evcharger
touch noUpdate
./install.sh
```

### Use a local source tree

```bash
VENUS_EVCHARGER_SOURCE_DIR=/data/src/venus-evcharger-service ./install.sh
```

### Trigger an update through DBus or MQTT

Write `1` to:

- `/Auto/SoftwareUpdateRun`

Useful companion paths:

- `/Auto/SoftwareUpdateAvailable`
- `/Auto/SoftwareUpdateState`
- `/Auto/SoftwareUpdateStateCode`
- `/Auto/SoftwareUpdateDetail`
- `/Auto/SoftwareUpdateCurrentVersion`
- `/Auto/SoftwareUpdateAvailableVersion`
- `/Auto/SoftwareUpdateNoUpdateActive`

`/Auto/SoftwareUpdateState` uses a fixed outward vocabulary. A particularly
useful value is `available-blocked`, which means the service found a newer
release and the local `noUpdate` marker currently blocks installation.

`installed` means the update run completed successfully and the service
initiated the restart handoff. The next service instance starts again from
`idle`, so `installed` is a transient completion state rather than a statement
that the currently running process is already the new version.

## Where To Read Next

- [INSTALL.md](INSTALL.md)
- [TROUBLESHOOTING.md](TROUBLESHOOTING.md)
- [DIAGNOSTICS.md](DIAGNOSTICS.md)

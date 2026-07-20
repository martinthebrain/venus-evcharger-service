# Install

This guide covers the usual installation paths for Venus OS and Cerbo GX.

## Prerequisites

- GX device or other Venus OS system with writable `/data`
- Python 3 as shipped with current Venus OS versions
- network reachability to the selected meter, switch, charger, or Shelly device
- a free `DeviceInstance` for the EV charger service

## Standard Install

1. Download the repository archive into a writable path under `/data`. Venus OS
   images usually do not include `git`, so the device install path uses `wget`
   or `curl`:

   ```bash
   mkdir -p /data/venus-evcharger
   cd /data/venus-evcharger
   wget -O venus-evcharger-service.tar.gz https://codeload.github.com/martinthebrain/venus-evcharger-service/tar.gz/refs/heads/main
   tar -xzf venus-evcharger-service.tar.gz
   cd venus-evcharger-service-main
   ```

   If `wget` is not available but `curl` is, replace the download line with:

   ```bash
   curl -fsSL -o venus-evcharger-service.tar.gz https://codeload.github.com/martinthebrain/venus-evcharger-service/tar.gz/refs/heads/main
   ```

2. Edit or generate the configuration:

   ```bash
   deploy/venus/config.venus_evcharger.ini
   ```

   Do this before the first live start on a GX device. For a relay-backed
   installation, verify that the configured device is the intended switching
   path before enabling Auto mode.

3. Set the core deployment values:

   - `Host`
   - `DeviceInstance`
   - `Phase`
   - `Username`, `Password`, `DigestAuth` when required
   - battery / PV / grid DBus service selectors when you pin them manually

4. Run the installer:

   ```bash
   ./deploy/venus/install_venus_evcharger_service.sh
   ```

5. Optional: run the guided setup wizard when you want a profile-based starting
   config instead of editing the INI by hand:

   ```bash
   ./deploy/venus/configure_venus_evcharger_service.sh
   ```

   The wizard writes the generated config, optional adapter files, and a small
   result/audit trail beside the main config. It can also clone defaults from
   an existing config or resume from the last wizard result and supports
   guarded non-interactive overwrites via `--force`. For split topologies it
   can now separate meter, switch, and charger hosts/BaseUrls instead of
   forcing one shared start value. Optional live probing is available through
   `--live-check` or targeted `--probe-role`, and interactive password prompts
   now keep typed secrets hidden:

   - `config.venus_evcharger.ini.wizard-result.json`
   - `config.venus_evcharger.ini.wizard-audit.jsonl`
   - `config.venus_evcharger.ini.wizard-topology.txt`

6. Restart the service:

   ```bash
   svc -t /service/dbus-venus-evcharger
   ```

7. Verify the service:

   ```bash
   svstat /service/dbus-venus-evcharger
   python3 scripts/ops/gateway_cache_read.py /Connected
   ```

8. Optional: use the deploy-local API helper for quick diagnostics or manual
   control on the target system:

   ```bash
   ./deploy/venus/venus_evchargerctl.sh health
   ./deploy/venus/venus_evchargerctl.sh --token READ-TOKEN state summary
   ./deploy/venus/venus_evchargerctl.sh --unix-socket /run/venus-evcharger-control.sock --token CONTROL-TOKEN command set-mode 1
   ```

   CLI exit codes:

   - `0` successful `2xx` API response
   - `1` API request failed or was rejected
   - `2` local CLI usage error

9. Optional: keep the prepared GX smoke-test skeleton for the day you have a
   real target device at hand again:

   ```bash
   ./deploy/venus/gx_api_smoke_test_skeleton.sh
   ```

   It is intentionally a manual target-side checklist, not part of the normal
   PC-side suite and not an automatic post-install step. For authenticated
   checks, provide tokens through environment variables such as
   `CONTROL_API_READ_TOKEN`, `CONTROL_API_CONTROL_TOKEN`, and set `TRY_WRITE=1`
   only when you intentionally want to attempt one safe live write.

## Installed Before Configuration

If the service is already installed with placeholder values, stop it before
setting a real Shelly or charger host:

```bash
svc -d /service/dbus-venus-evcharger
```

Then edit `deploy/venus/config.venus_evcharger.ini` or run the wizard from the
installed code directory. Re-run the installer to restore links and executable
bits, then restart the service:

```bash
./deploy/venus/install_venus_evcharger_service.sh
svc -t /service/dbus-venus-evcharger
svstat /service/dbus-venus-evcharger
```

## One-File Bootstrap Install

`install.sh` supports a small bootstrap flow for GX deployments.

Typical flow:

1. copy or download `install.sh` into a directory under `/data`
2. run it there
3. let it materialize the working tree
4. let it call the regular Venus installer from the refreshed tree

Example:

```bash
mkdir -p /data/venus-evcharger
cd /data/venus-evcharger
wget -O install.sh https://raw.githubusercontent.com/martinthebrain/venus-evcharger-service/main/install.sh
chmod +x install.sh
./install.sh
```

Bootstrap highlights:

- refreshes the local updater
- supports manifest-based updates
- supports detached-signature verification
- keeps release directories under `releases/<version>/`
- advances `current/` after the target release is ready
- keeps `previous/` available for rollback

Useful variables:

- `VENUS_EVCHARGER_TARGET_DIR`
- `VENUS_EVCHARGER_CHANNEL`
- `VENUS_EVCHARGER_SOURCE_DIR`
- `VENUS_EVCHARGER_MANIFEST_SOURCE`
- `VENUS_EVCHARGER_BOOTSTRAP_PUBKEY`
- `VENUS_EVCHARGER_REQUIRE_SIGNED_MANIFEST`

The full bootstrap and updater behavior, including `noUpdate`, is documented in
[UPDATE_FLOW.md](UPDATE_FLOW.md).

## Development Checkout

On a development machine with `git`, you can work from a normal checkout and
copy the result to a GX device when needed:

```bash
git clone https://github.com/martinthebrain/venus-evcharger-service.git
cd venus-evcharger-service
```

## Configuration Patterns

### Combined relay + meter

Good fit for a single backend that represents the visible wallbox path.

### Split relay + meter

Good fit when metering and switching come from different devices.

### Native charger

Good fit when the charger handles enable and current directly.

### Native charger + external phase switch

Good fit when current control and phase layout live in different devices.

Further backend examples live in [CHARGER_BACKENDS.md](CHARGER_BACKENDS.md) and
[SHELLY_PROFILES.md](SHELLY_PROFILES.md).

## Wizard CLI Examples

Interactive guided setup:

```bash
./deploy/venus/configure_venus_evcharger_service.sh
```

Preview only, no writes, JSON result:

```bash
./deploy/venus/configure_venus_evcharger_service.sh --dry-run --json
```

Reuse the current config as defaults, but only preview the result:

```bash
./deploy/venus/configure_venus_evcharger_service.sh --clone-current --dry-run --json
```

Role-specific non-interactive host override flags:

- `--meter-host`
- `--switch-host`
- `--charger-host`
- `--charger-preset`
- `--live-check`
- `--probe-role`
- `--resume-last`

Preset/policy tuning flags:

- `--request-timeout-seconds`
- `--switch-group-phase-layout`
- `--auto-start-surplus-watts`
- `--auto-stop-surplus-watts`
- `--auto-min-soc`
- `--auto-resume-soc`
- `--scheduled-enabled-days`
- `--scheduled-latest-end-time`
- `--scheduled-night-current-amps`

Non-interactive native `go-e` charger:

```bash
./deploy/venus/configure_venus_evcharger_service.sh \
  --non-interactive \
  --force \
  --profile native-charger \
  --charger-backend goe_charger \
  --request-timeout-seconds 4.5 \
  --host goe.local \
  --phase 3P \
  --policy-mode auto \
  --auto-start-surplus-watts 2100 \
  --auto-stop-surplus-watts 1650 \
  --auto-min-soc 35 \
  --auto-resume-soc 39
```

Non-interactive native Modbus charger over TCP:

```bash
./deploy/venus/configure_venus_evcharger_service.sh \
  --non-interactive \
  --force \
  --profile native-charger \
  --charger-backend modbus_charger \
  --transport tcp \
  --transport-host 192.168.1.91 \
  --transport-unit-id 7 \
  --host 192.168.1.90
```

Non-interactive ABB Terra AC preset over Modbus TCP:

```bash
./deploy/venus/configure_venus_evcharger_service.sh \
  --non-interactive \
  --force \
  --profile native-charger \
  --charger-backend modbus_charger \
  --charger-preset abb-terra-ac-modbus \
  --transport tcp \
  --transport-host terra.local \
  --transport-port 502 \
  --transport-unit-id 1 \
  --host terra.local
```

Non-interactive cFos Power Brain preset over Modbus TCP:

```bash
./deploy/venus/configure_venus_evcharger_service.sh \
  --non-interactive \
  --force \
  --profile native-charger \
  --charger-backend modbus_charger \
  --charger-preset cfos-power-brain-modbus \
  --transport tcp \
  --transport-host cfos.local \
  --transport-port 4701 \
  --transport-unit-id 1 \
  --host cfos.local
```

Non-interactive openWB secondary preset over Modbus TCP:

```bash
./deploy/venus/configure_venus_evcharger_service.sh \
  --non-interactive \
  --force \
  --profile native-charger \
  --charger-backend modbus_charger \
  --charger-preset openwb-modbus-secondary \
  --transport tcp \
  --transport-host openwb.local \
  --transport-port 1502 \
  --transport-unit-id 1 \
  --host openwb.local
```

Non-interactive split topology using only template adapters:

```bash
./deploy/venus/configure_venus_evcharger_service.sh \
  --non-interactive \
  --force \
  --profile split-topology \
  --split-preset template-stack \
  --host adapter.local
```

Non-interactive split topology with Shelly meter and native `go-e` charger:

```bash
./deploy/venus/configure_venus_evcharger_service.sh \
  --non-interactive \
  --force \
  --profile split-topology \
  --split-preset shelly-meter-goe \
  --meter-host 192.168.1.24 \
  --charger-host goe.local
```

Non-interactive split topology with `go-e` plus external 3-phase switch group:

```bash
./deploy/venus/configure_venus_evcharger_service.sh \
  --non-interactive \
  --force \
  --profile split-topology \
  --split-preset goe-external-switch-group \
  --switch-group-phase-layout P1,P1_P2_P3 \
  --switch-host http://switch.local \
  --charger-host goe.local
```

Non-interactive split topology with Shelly meter, `go-e`, and external 3-phase switch group:

```bash
./deploy/venus/configure_venus_evcharger_service.sh \
  --non-interactive \
  --force \
  --profile split-topology \
  --split-preset shelly-meter-goe-switch-group \
  --meter-host 192.168.1.24 \
  --switch-host http://switch.local \
  --charger-host goe.local
```

Non-interactive split topology with Shelly meter/switch plus Modbus charger:

```bash
./deploy/venus/configure_venus_evcharger_service.sh \
  --non-interactive \
  --force \
  --profile split-topology \
  --split-preset shelly-io-modbus-charger \
  --meter-host 192.168.1.20 \
  --switch-host 192.168.1.21 \
  --transport tcp \
  --transport-host 192.168.1.93 \
  --transport-unit-id 8 \
  --host 192.168.1.92
```

Non-interactive split topology with Shelly meter, Modbus charger, and external 3-phase switch group:

```bash
./deploy/venus/configure_venus_evcharger_service.sh \
  --non-interactive \
  --force \
  --profile split-topology \
  --split-preset shelly-meter-modbus-switch-group \
  --meter-host 192.168.1.24 \
  --switch-host http://switch.local \
  --transport tcp \
  --transport-host 192.168.1.95 \
  --transport-unit-id 9 \
  --host 192.168.1.94
```

Non-interactive split topology with Shelly meter plus cFos Power Brain preset:

```bash
./deploy/venus/configure_venus_evcharger_service.sh \
  --non-interactive \
  --force \
  --profile split-topology \
  --split-preset shelly-meter-modbus-charger \
  --charger-preset cfos-power-brain-modbus \
  --meter-host 192.168.1.24 \
  --charger-host cfos.local \
  --transport tcp \
  --transport-host cfos.local \
  --transport-port 4701 \
  --transport-unit-id 1 \
  --host 192.168.1.94
```

Clone an existing config from another path and adjust only the host:

```bash
./deploy/venus/configure_venus_evcharger_service.sh \
  --non-interactive \
  --import-config /data/etc/old-wallbox.ini \
  --host 192.168.1.120 \
  --config-path ./deploy/venus/config.venus_evcharger.ini
```

Resume from the last wizard result next to the target config and only preview:

```bash
./deploy/venus/configure_venus_evcharger_service.sh \
  --non-interactive \
  --dry-run \
  --json \
  --resume-last
```

## Validation Before First Start

Validate the full wallbox configuration:

```bash
python3 -m venus_evcharger.backend.probe validate-wallbox deploy/venus/config.venus_evcharger.ini
```

Validate adapter files individually:

```bash
python3 -m venus_evcharger.backend.probe validate /data/etc/wallbox-meter.ini
python3 -m venus_evcharger.backend.probe validate /data/etc/wallbox-switch.ini
python3 -m venus_evcharger.backend.probe validate /data/etc/wallbox-charger.ini
```

## After Installation

Useful commands:

```bash
svstat /service/dbus-venus-evcharger
svc -t /service/dbus-venus-evcharger
tail -f /var/volatile/log/dbus-venus-evcharger/current
tail -f /var/volatile/log/dbus-venus-evcharger/auto-reasons.log
```

Live diagnostics and DBus paths are collected in
[DIAGNOSTICS.md](DIAGNOSTICS.md).

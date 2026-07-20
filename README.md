# Venus OS EV Charger Service

[![CI](https://github.com/martinthebrain/venus-evcharger-service/actions/workflows/ci.yml/badge.svg)](https://github.com/martinthebrain/venus-evcharger-service/actions/workflows/ci.yml)
[![codecov](https://codecov.io/gh/martinthebrain/venus-evcharger-service/graph/badge.svg)](https://codecov.io/gh/martinthebrain/venus-evcharger-service)

`venus-evcharger-service` turns a flexible EV charging installation into a
native Victron Venus OS EV charger service. It publishes the wallbox on DBus,
shows up in the Venus GUI, supports manual, automatic, and scheduled charging,
and can coordinate meters, relays, native chargers, external energy sources,
and phase switching as one installation.

The project is built for real field setups: portable EVSEs behind contactors,
charger-native wallboxes, Shelly-based devices, Tuya- and Tasmota-style local
HTTP bridges, template HTTP adapters, Modbus devices, hybrid inverter data, and
installations where measuring, switching, and charging are handled by different
devices.

## Why It Exists

Many practical EV charging setups do not look like one fixed wallbox with one
API. A safe and useful controller often needs to answer questions such as:

- Which device measures each phase?
- Which device is allowed to switch each phase?
- Does the charger itself support enable/current control?
- Can Auto mode use PV surplus, battery SOC, and grid import at the same time?
- How should the system recover when one device is temporarily unavailable?

This service models the installation by roles and capabilities. Devices can be
used as meters, switches, chargers, or members of a group, depending on what
they can actually do.

## What It Can Do

- Publish a Victron-compatible EV charger service on Venus OS / Cerbo GX
- Control charging through the Venus GUI, DBus, MQTT, or an optional local HTTP API
- Run manual, PV surplus Auto, and Scheduled charging modes
- Use battery SOC, grid import/export, PV power, and external energy sources
- Coordinate separate meter, switch, charger, and feedback roles
- Support one-phase, multi-phase, and externally switched phase setups
- Store wizard-managed device profiles, device instances, and role bindings
- Preserve generated configuration and inventory files across service updates
- Publish diagnostics for topology, runtime state, charger health, retries, faults, recovery, scheduling, and phase handling
- Install, update, reset, and configure the service through GX-friendly scripts

## Hardware Model

The service is centered around topology and capabilities:

| Role | What it represents |
| --- | --- |
| Meter | Reads power, energy, or phase-specific measurements |
| Switch | Enables or disconnects one or more charging phases |
| Charger | Controls enable/current through a native charger API |
| Group | Combines several devices into one logical meter or switch role |
| Energy source | Adds battery, PV, grid, or inverter context for Auto mode |

Typical installations include:

| Setup | Main idea |
| --- | --- |
| Relay-controlled EVSE | A contactor or relay switches a portable charging brick |
| Native charger | A charger backend handles enable and current setpoints |
| Native charger plus external switch | The charger regulates current, external hardware switches phases |
| Multi-device wallbox | Separate devices provide metering, switching, charging, and feedback |
| Hybrid energy setup | External inverter or battery data influences Auto mode |

## Integration Families

The project includes native and template-based adapters for common installation
shapes:

- Shelly relay, switch, meter, and contactor-style roles
- Tuya-prepared meter and switch roles through the same HTTP template surface
- Tasmota-prepared meter and switch roles through the same HTTP template surface
- HTTP template adapters for custom meters, switches, and chargers
- Native charger backends such as `goe_charger`, `simpleevse_charger`, `smartevse_charger`, and `modbus_charger`
- `switch_group` for phase-aware external switching
- External energy connectors including `dbus`, `template_http`, `modbus`, `command_json`, and `opendtu_http`
- Huawei-focused energy source profiles for supported inverter families

Backend details live in [CHARGER_BACKENDS.md](CHARGER_BACKENDS.md) and
[SHELLY_PROFILES.md](SHELLY_PROFILES.md). Full configuration examples live in
[CONFIGURATION.md](CONFIGURATION.md).

## Charging Modes

Mode values are intentionally simple:

- `Mode=0` is Manual charging: user control directly enables or disables the charger/switch.
- `Mode=1` is Auto charging: charge only when the configured PV surplus, grid, SOC, timing, and safety rules allow it.
- `Mode=2` is Scheduled charging: use the same Auto surplus logic during the day and add a target-day night/fallback charge window after the configured daytime window.

### Manual

Manual mode follows direct user control from the Venus EV charger tile, DBus,
MQTT, or the optional local HTTP API. The published state mirrors the real
relay, charger, or feedback state as closely as the configured devices allow.

### Auto

Auto mode can combine:

- PV surplus
- grid import/export limits
- battery SOC
- external battery or inverter sources
- start/stop delays
- minimum runtime and off-time
- high-SOC behavior
- learned charge-power behavior
- phase switching and phase mismatch protection

### Scheduled

Scheduled mode does not redefine Auto. It builds on the same surplus policy and
adds day-aware fallback charging after the daytime PV window, up to the
configured latest end time.

### Session Energy

Some meters, including Shelly devices, report a lifetime energy counter. The
service keeps that raw counter internal and publishes session energy to the
Venus EV charger UI, so unplugging and replugging starts a fresh displayed
charging session.

## Setup Wizard

The setup wizard can generate the main config, adapter files, topology summary,
and a persistent device inventory:

```bash
./deploy/venus/configure_venus_evcharger_service.sh
```

For Venus OS systems, the recommended workflow is preview first, write second:

```bash
./deploy/venus/configure_venus_evcharger_service.sh --dry-run
./deploy/venus/configure_venus_evcharger_service.sh
```

The preview shows the selected preset, the hardware responsibility split,
the initial charging policy, risk-ranked warnings, and a short post-install
checklist before any files are changed.

Common guided presets include:

| Preset | Use it when |
| --- | --- |
| Shelly PM/PM1 Gen4 measures and switches | One Shelly-compatible device measures energy and switches the EVSE relay/contactor |
| Shelly meter + Cerbo GX Relay switch | A Shelly measures energy while one of the Cerbo GX relays switches the contactor |
| Shelly meter + native go-e charger | A Shelly provides independent metering and the go-e charger owns charger control |
| Shelly meter + native Modbus wallbox | A Shelly provides independent metering and a Modbus wallbox owns charger control |
| Tuya PM/relay measures and switches | A Tuya-compatible local HTTP bridge measures energy and switches the relay |
| Tuya meter + native go-e or Modbus wallbox | A Tuya-compatible meter provides measurements while the charger backend owns control |
| Tasmota PM/relay measures and switches | A Tasmota HTTP device measures energy and switches the relay |
| Tasmota meter + native go-e or Modbus wallbox | A Tasmota meter provides measurements while the charger backend owns control |

The inventory stores local device profiles, concrete device instances, and
role/phase bindings. That means a locally described device can be reused later,
and multiple physical devices can share the same profile.

## Quick Start

1. On Venus OS, download the repository archive into a writable path under
   `/data`. Venus OS does not need `git` for this path:

   ```bash
   mkdir -p /data/venus-evcharger
   cd /data/venus-evcharger
   wget -O venus-evcharger-service.tar.gz https://codeload.github.com/martinthebrain/venus-evcharger-service/tar.gz/refs/heads/main
   tar -xzf venus-evcharger-service.tar.gz
   cd venus-evcharger-service-main
   ```

   If your image has `curl` instead of `wget`, use
   `curl -fsSL -o venus-evcharger-service.tar.gz https://codeload.github.com/martinthebrain/venus-evcharger-service/tar.gz/refs/heads/main`.

2. Configure the installation:

   ```bash
   ./deploy/venus/configure_venus_evcharger_service.sh --dry-run
   ./deploy/venus/configure_venus_evcharger_service.sh
   ```

   You can also edit [deploy/venus/config.venus_evcharger.ini](deploy/venus/config.venus_evcharger.ini)
   directly. Configure the Shelly/charger details before installing the service
   on a live GX device.

3. Install the service:

   ```bash
   ./deploy/venus/install_venus_evcharger_service.sh
   ```

4. Restart and check it:

   ```bash
   svc -t /service/dbus-venus-evcharger
   svstat /service/dbus-venus-evcharger
   python3 scripts/ops/gateway_cache_read.py /Connected
   ```

   In the Venus GUI, also check the EV charger tile: `Mode`, `StartStop`,
   `AutoStart`, relay state, and current charging power should match the
   physical installation.

For a detailed installation walkthrough, see [INSTALL.md](INSTALL.md).

If the service was installed before configuration, stop it first, adjust the
config, then restart it:

```bash
svc -d /service/dbus-venus-evcharger
./deploy/venus/configure_venus_evcharger_service.sh
./deploy/venus/install_venus_evcharger_service.sh
svc -t /service/dbus-venus-evcharger
```

On a development machine with `git`, cloning the repository is also fine:

```bash
git clone https://github.com/martinthebrain/venus-evcharger-service.git
cd venus-evcharger-service
```

## Control And State APIs

- DBus is the primary Venus OS integration surface.
- MQTT mirrors published paths through the Venus MQTT bridge.
- The optional local HTTP API provides structured control and state endpoints
  for local automation.

The stable control contract lives in [CONTROL_API.md](CONTROL_API.md). The
stable read/state contract lives in [STATE_API.md](STATE_API.md).

<!-- BEGIN:README_LOCAL_HTTP_CONTROL_API_GETTING_STARTED -->
Quick start:

- `python3 ./venus_evchargerctl.py --token READ-TOKEN health`
- `python3 ./venus_evchargerctl.py --token READ-TOKEN doctor`
- `python3 ./venus_evchargerctl.py --token READ-TOKEN capabilities`
- `python3 ./venus_evchargerctl.py --token READ-TOKEN state automation`
- `python3 ./venus_evchargerctl.py --token CONTROL-TOKEN safe-write set-mode 1`
- `python3 ./venus_evchargerctl.py --unix-socket /run/venus-evcharger-control.sock --token READ-TOKEN watch --kind command --once`

For direct HTTP usage, `curl` snippets, optimistic concurrency with `If-Match`,
and a small Python example, see [CONTROL_API.md](CONTROL_API.md).
For a short local developer runbook on a normal PC, see
[DEV_API_WORKFLOW.md](DEV_API_WORKFLOW.md).
<!-- END:README_LOCAL_HTTP_CONTROL_API_GETTING_STARTED -->

## Reliability And Operations

The defaults keep frequently written runtime files in RAM-backed locations such
as `/run` and `/var/volatile/log`. Persistent files are reserved for
configuration, wizard output, update state, and explicit backups.

Operational helpers include:

- installer and boot helper for `/data` deployments
- bootstrap updater with rollback support
- reset script for returning to the shipped default config
- probe commands for adapter and full wallbox validation
- detailed DBus diagnostics for runtime and field debugging

See [DIAGNOSTICS.md](DIAGNOSTICS.md), [TROUBLESHOOTING.md](TROUBLESHOOTING.md),
and [UPDATE_FLOW.md](UPDATE_FLOW.md) for operator details.

## Project Quality

The repository keeps a tight local quality gate:

- unit tests
- branch coverage
- type checks
- complexity checks
- file-size guardrails

The usual local check is:

```bash
./scripts/dev/check_all.sh
```

## Documentation Map

- [INSTALL.md](INSTALL.md): installation walkthrough
- [CONFIGURATION.md](CONFIGURATION.md): main configuration guide
- [CHARGER_BACKENDS.md](CHARGER_BACKENDS.md): native and template charger adapters
- [SHELLY_PROFILES.md](SHELLY_PROFILES.md): Shelly role presets and examples
- [CONTROL_API.md](CONTROL_API.md): HTTP control API
- [STATE_API.md](STATE_API.md): stable outward state contract
- [HUAWEI_INTEGRATION.md](HUAWEI_INTEGRATION.md): Huawei energy source guide
- [DIAGNOSTICS.md](DIAGNOSTICS.md): live checks and troubleshooting paths
- [TROUBLESHOOTING.md](TROUBLESHOOTING.md): field debugging guide
- [UPDATE_FLOW.md](UPDATE_FLOW.md): bootstrap, update, rollback, and `noUpdate`
- [CONTRIBUTING.md](CONTRIBUTING.md): contributor guidance

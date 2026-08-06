# Configuration

This guide explains how to shape a Venus EV charger installation from
`deploy/venus/config.venus_evcharger.ini`.

The most useful way to read the config is by decision area:

1. service identity
2. hardware topology
3. charger control style
4. Auto policy
5. Scheduled policy
6. runtime control and persistence
7. optional local HTTP control API

## Core Deployment Values

These are the first values to review in a fresh install:

- `Host`
- `DeviceInstance`
- `Phase`
- `Username`
- `Password`
- `DigestAuth`

Then review the pinned DBus selectors when you prefer explicit services for:

- battery
- PV
- grid

## Topology Decisions

The most useful mental model is:

- what switches or regulates the load
- what measures the load
- whether a native charger backend is present
- whether phase control is internal or external

The runtime reasons in those roles first. The deployed INI describes many
modular setups through `[Backends]` plus `Mode=split`.

### Direct Primary Host Path

Use `Host=...` when one primary RPC-capable device represents the switching
path directly.

Typical fit:

- one Shelly-based relay path
- one device that exposes both switching and visible load behavior
- simple smoke tests against the network emulator

### Role-Based Backend Path

Use explicit backend roles when the installation is composed from separate
adapters or when you want a modular description on disk.

Current file format:

- `Mode=split`
- `MeterType=...`
- `SwitchType=...`
- `ChargerType=...`

Typical fit:

- separate meter and relay devices
- charger-native setups
- charger plus external phase switching
- multi-device installations with explicit feedback and interlock inputs

## Backend Role Patterns

The examples below use the deployed `[Backends]` format.

### Relay + meter path

Typical shape:

```ini
[Backends]
Mode=split
MeterType=shelly_meter
SwitchType=shelly_switch
```

### Charger-native path

Typical shape:

```ini
[Backends]
Mode=split
MeterType=none
SwitchType=none
ChargerType=goe_charger
```

### Charger + external phase switch

Typical shape:

```ini
[Backends]
Mode=split
MeterType=none
SwitchType=switch_group
ChargerType=simpleevse_charger
```

For backend-specific examples, see:

- [CHARGER_BACKENDS.md](CHARGER_BACKENDS.md)
- [SHELLY_PROFILES.md](SHELLY_PROFILES.md)

## Phase Configuration

The visible phase layout is shaped by:

- wallbox-level `Phase`
- backend capabilities
- optional phase-switching policy
- optional feedback and observed-phase diagnostics

Relevant runtime-tunable paths:

- `/PhaseSelection`
- `/Auto/PhaseSwitching`
- `/Auto/PhasePreferLowestWhenIdle`
- `/Auto/PhaseUpshiftDelaySeconds`
- `/Auto/PhaseDownshiftDelaySeconds`
- `/Auto/PhaseUpshiftHeadroomWatts`
- `/Auto/PhaseDownshiftMarginWatts`

## Auto Policy

Auto policy is built from:

- surplus start/stop thresholds
- SOC thresholds
- start/stop delays
- minimum runtime and off-time
- grid recovery timing
- learned charging power
- stop smoothing
- optional high-SOC profile
- optional automatic phase switching

Useful primary controls:

- `AutoStartSurplusWatts`
- `AutoStopSurplusWatts`
- `AutoMinSoc`
- `AutoResumeSoc`
- `AutoStartDelaySeconds`
- `AutoStopDelaySeconds`
- `AutoGridRecoveryStartSeconds`
- `AutoReferenceChargePowerWatts`

Useful advanced controls:

- learned-power window and alpha values
- stop-volatility thresholds
- phase upshift/downshift timing
- phase mismatch and lockout tuning

### Multiple Energy Sources

Auto mode can aggregate more than one battery or hybrid-like DBus source.

Main keys:

- `AutoUseCombinedBatterySoc`
- `AutoEnergySources`
- `AutoEnergySource.<id>.Profile`
- `AutoEnergySource.<id>.Role`
- `AutoEnergySource.<id>.Type`
- `AutoEnergySource.<id>.ConfigPath`
- `AutoEnergySource.<id>.Service`
- `AutoEnergySource.<id>.ServicePrefix`
- `AutoEnergySource.<id>.SocPath`
- `AutoEnergySource.<id>.UsableCapacityWh`
- `AutoEnergySource.<id>.BatteryPowerPath`
- `AutoEnergySource.<id>.AcPowerPath`
- `AutoEnergySource.<id>.PvPowerPath`
- `AutoEnergySource.<id>.GridInteractionPath`
- `AutoEnergySource.<id>.OperatingModePath`

Roles:

- `battery`
- `hybrid-inverter`
- `inverter`

Connector types:

- `dbus`
- `template_http`
- `modbus`
- `command_json`

Named profiles:

- `dbus-battery`
- `dbus-hybrid`
- `template-http-hybrid`
- `modbus-hybrid`
- `command-json-hybrid`
- `opendtu-pvinverter`
- `huawei_ma_native_ap`
- `huawei_ma_native_lan`
- `huawei_ma_sdongle`
- `huawei_ma_smartlogger_modbus_tcp`
- `huawei_mb_native_ap`
- `huawei_mb_native_lan`
- `huawei_mb_sdongle`
- `huawei_mb_smartlogger_modbus_tcp`
- `huawei_mb_unit1`
- `huawei_mb_unit2`
- `huawei_l1_*`
- `huawei_lc0_*`
- `huawei_lb0_*`
- `huawei_m1_*`
- `huawei_map0_*`
- `huawei_mb0_*`
- `huawei_smartlogger_modbus_tcp`

You can also use short aliases such as `battery`, `hybrid`, `http-hybrid`,
`modbus`, or `helper`. A profile provides safe defaults for role, connector
type, and common path fields. Explicit `AutoEnergySource.<id>.*` values still
win over the profile defaults.

The first vendor-specific presets are Huawei-oriented. They model:

- platform families `MA`, `MB`, and `smartlogger`
- model families `L1`, `LC0`, `LB0`, `M1`, `MAP0`, and `MB0`
- access modes `native_ap`, `native_lan`, `sdongle`, `smartlogger`, and MB `unit1` / `unit2`
- default probe candidates for host, port, and unit-id discovery

The Huawei validation CLI also emits a ready-to-copy
`recommendation.config_snippet` for the main config plus a compact
`recommendation.wizard_hint_block` that can be pasted into operator notes.

Huawei family-specific profiles are first-class names. That includes:

- `huawei_l1_native_ap`, `huawei_lc0_native_ap`, `huawei_lb0_native_ap`, `huawei_m1_native_ap`
- `huawei_l1_native_lan`, `huawei_lc0_native_lan`, `huawei_lb0_native_lan`, `huawei_m1_native_lan`
- `huawei_l1_sdongle`, `huawei_lc0_sdongle`, `huawei_lb0_sdongle`, `huawei_m1_sdongle`
- `huawei_l1_smartlogger_modbus_tcp`, `huawei_lc0_smartlogger_modbus_tcp`, `huawei_lb0_smartlogger_modbus_tcp`, `huawei_m1_smartlogger_modbus_tcp`
- `huawei_map0_native_ap`, `huawei_mb0_native_ap`
- `huawei_map0_native_lan`, `huawei_mb0_native_lan`
- `huawei_map0_sdongle`, `huawei_mb0_sdongle`
- `huawei_map0_smartlogger_modbus_tcp`, `huawei_mb0_smartlogger_modbus_tcp`
- `huawei_map0_unit1`, `huawei_map0_unit2`, `huawei_mb0_unit1`, `huawei_mb0_unit2`

Regional `SUN5000` family names are accepted as aliases for the
matching `LB0` and `MAP0` profiles.

There is also a direct OpenDTU preset for PV-only sources:

- `opendtu-pvinverter`

Useful aliases:

- `opendtu`
- `opendtu-inverter`
- `growatt-opendtu`

Aggregation rules:

- `combined_soc` is capacity-weighted when one or more sources provide both
  valid `soc` and `usable_capacity_wh`
- `AutoEnergySource.<id>.PhysicalId` identifies one physical battery across
  multiple observation paths; equal non-empty IDs contribute only the newest
  online observation to capacity-weighted SOC
- an omitted `PhysicalId` keeps sources independent for backward-compatible,
  deterministic aggregation
- `effective_soc` falls back to one readable source SOC when no weighted
  aggregate can be formed
- charge, discharge, net battery power, and AC power are summed across sources
- optional PV input power and grid interaction values are also summed across
  sources when configured
- sources without usable capacity still contribute power visibility, but not a
  weighted `combined_soc`

`AutoUseCombinedBatterySoc=1` tells Auto mode to use the aggregated effective
SOC instead of only the first configured source.

The reserved `victron` source is populated from the semantic DBus-gateway
snapshot. The auto-input helper never reads Venus DBus directly. A legacy
`dbus` profile/type is accepted only for that reserved semantic alias and is
rejected for external source IDs.

`template_http` sources read from one small external HTTP/JSON adapter file
referenced through `AutoEnergySource.<id>.ConfigPath`. That keeps external
device specifics out of the main wallbox config and gives us one clear
connector layer for non-DBus energy sources.

`modbus` sources read one compact energy snapshot through a dedicated Modbus
config file. This is a good fit for external hybrid systems that already expose
SOC and battery power on TCP or RTU.

For vendor-sensitive TCP setups such as Huawei, you can actively test the
configured Modbus read section against host/port/unit-id candidates:

```bash
python3 -m venus_evcharger.energy.probe detect-modbus-energy /data/etc/huawei-ma-modbus.ini --profile huawei_ma_native_ap
```

That command uses the configured read mapping from the INI file, expands the
candidate host/port/unit-id set from the selected profile, and returns the
first successful combination plus the failed attempts before it.

For Huawei-specific field validation on a real endpoint, you can then run:

```bash
python3 -m venus_evcharger.energy.probe validate-huawei-energy /data/etc/huawei-mb-modbus.ini --profile huawei_mb_sdongle --host 192.168.8.1
```

That validation run checks the configured field set plus the Huawei meter block
around `37100` and reports which reads succeeded on the current access path.
For the compact operator workflow, see
[HUAWEI_INTEGRATION.md](HUAWEI_INTEGRATION.md).

The repository now also ships first read-only Huawei starter templates:

- [template-energy-source-huawei-ma-modbus.ini](deploy/venus/template-energy-source-huawei-ma-modbus.ini)
- [template-energy-source-huawei-mb-modbus.ini](deploy/venus/template-energy-source-huawei-mb-modbus.ini)
- [template-energy-source-huawei-mb-unit1-modbus.ini](deploy/venus/template-energy-source-huawei-mb-unit1-modbus.ini)
- [template-energy-source-huawei-mb-unit2-modbus.ini](deploy/venus/template-energy-source-huawei-mb-unit2-modbus.ini)

These templates currently cover the first officially verified baseline fields:

- battery SOC
- battery charge/discharge power
- rated charge/discharge power where officially documented
- device active power
- aggregate PV input power from Huawei's documented `Total input power` register
- meter-backed grid interaction power
- battery working mode with semantic text labels where officially documented

The shipped Huawei templates intentionally set `BatteryPowerRead Scale=-1`
because Huawei's documented battery-power sign is the inverse of this repo's
internal `net_battery_power_w` convention.
The current Huawei starters now set aggregate `PvInputPowerRead` from the
Huawei `Total input power` register (`32064`).
They also set `GridInteractionRead` from the Huawei meter active-power register
(`37113`) with sign normalization into the repo-internal import/export
convention.
For the MB `unit1` / `unit2` variants, `AcPowerRead`, `PvInputPowerRead`, and
`GridInteractionRead` remain inverter-global or meter-global values. Those are
now deduplicated automatically through the shipped aggregation scope keys, so
parallel `unit1` + `unit2` setups do not double-count them.

`command_json` sources run one local helper command that returns a JSON object.
This is the intended bridge point for custom scripts, vendor SDK wrappers, or
MQTT consumers that should stay outside the wallbox core.

When the semantic gateway and an external connector both observe the same
physical battery, assign the same stable ID:

```ini
AutoEnergySources=victron,huawei
AutoEnergySource.victron.Profile=dbus-battery
AutoEnergySource.victron.PhysicalId=house-battery
AutoEnergySource.huawei.Profile=huawei_ma_native_ap
AutoEnergySource.huawei.PhysicalId=house-battery
```

This deduplicates only the capacity-weighted SOC contribution. Independent
power channels retain their existing scope-key aggregation rules.

Single-source keys are used when `AutoEnergySources` is empty:

- `AutoBatteryService`
- `AutoBatteryServicePrefix`
- `AutoBatterySocPath`
- `AutoBatteryCapacityWh`
- `AutoBatteryCapacityWhPath`
- `AutoBatteryCapacityAhPath`
- `AutoBatteryVoltagePath`

Power, AC throughput, PV contribution, grid interaction, and operating mode are
part of the multi-source model. Configure them as
`AutoEnergySource.<id>.BatteryPowerPath`, `AutoEnergySource.<id>.AcPowerPath`,
`AutoEnergySource.<id>.PvPowerPath`,
`AutoEnergySource.<id>.GridInteractionPath`, and
`AutoEnergySource.<id>.OperatingModePath`.

Additional per-source field meanings:

- `BatteryPowerPath` is the signed battery-side power used to derive charge and
  discharge activity
- `AcPowerPath` is the visible AC-side output or throughput of the external
  source
- `PvPowerPath` is optional PV-side contribution for hybrid or inverter sources
- `GridInteractionPath` is optional signed import/export influence of the
  source against the grid
- `OperatingModePath` is optional textual mode visibility such as
  `self-consumption`, `support`, or vendor-specific operating states

The richer energy model uses these fields both for operator-facing state and
for runtime learning. In particular, the service now learns:

- observed active charge/discharge levels
- observed PV/grid extrema
- response delay when an external source wakes up or flips direction
- directional bias for import support vs charging and export charging vs
  discharge
- conservative charge/discharge headroom from learned maxima
- small near-term import/export estimates from current grid interaction plus
  learned response and bias metrics
- `AutoBatteryCapacityWh`
- `AutoEnergySource.<id>.BatteryPowerPath`
- `AutoEnergySource.<id>.AcPowerPath`

### Companion DBus Bridge

When you want aggregated external energy visibility to start showing up as
separate Venus-style services, you can enable the optional companion bridge.

Main keys:

- `CompanionDbusBridgeEnabled`
- `CompanionBatteryServiceEnabled`
- `CompanionPvInverterServiceEnabled`
- `CompanionGridServiceEnabled`
- `CompanionGridAuthoritativeSource`
- `CompanionSourceServicesEnabled`
- `CompanionSourceGridServicesEnabled`
- `CompanionBatteryDeviceInstance`
- `CompanionPvInverterDeviceInstance`
- `CompanionGridDeviceInstance`
- `CompanionSourceBatteryDeviceInstanceBase`
- `CompanionSourcePvInverterDeviceInstanceBase`
- `CompanionSourceGridDeviceInstanceBase`
- `CompanionGridHoldSeconds`
- `CompanionGridSmoothingAlpha`
- `CompanionGridSmoothingMaxJumpWatts`
- `CompanionSourceGridHoldSeconds`
- `CompanionSourceGridSmoothingAlpha`
- `CompanionSourceGridSmoothingMaxJumpWatts`
- `CompanionBatteryServiceName`
- `CompanionPvInverterServiceName`
- `CompanionGridServiceName`
- `CompanionSourceBatteryServicePrefix`
- `CompanionSourcePvInverterServicePrefix`
- `CompanionSourceGridServicePrefix`

Current scope:

- one aggregated battery-like companion service
- one aggregated PV-inverter-like companion service
- one optional aggregated grid-meter-like companion service
- optional per-source battery companion services for `battery` and
  `hybrid-inverter` roles
- optional per-source PV-inverter companion services for `hybrid-inverter` and
  `inverter` roles
- optional per-source grid companion services for normalized sources that
  currently expose `grid_interaction_w`
- both stay separate from the EV charger service identity

The aggregated services keep one stable Venus-style summary view. The per-source
services mirror normalized entries from `battery_sources` and give the Venus
GUI individual external devices to render without folding foreign-energy
modeling into the main EV charger DBus service.
The grid companion path is intentionally opt-in, so a site can expose an
external meter view such as a Huawei-backed grid reading only when that should
act as the local single source of truth.

When `CompanionGridAuthoritativeSource` is set to an `AutoEnergySource`
`source_id`, the aggregated external grid companion stops using the combined
grid sum and instead follows only that one normalized source. This is useful
for topologies where one external meter path should be the explicit grid truth,
for example `huawei` in a Huawei + OpenDTU + Victron installation.

Grid hardening knobs:

- `CompanionGridHoldSeconds` keeps the last valid aggregated grid reading alive
  for a short outage window before the aggregated grid companion flips to
  disconnected/`0`
- `CompanionGridSmoothingAlpha` optionally smooths fresh aggregated grid
  updates; `1.0` means no smoothing, lower values increasingly damp abrupt
  jumps
- `CompanionGridSmoothingMaxJumpWatts` limits smoothing to smaller aggregated
  jumps; `0` keeps smoothing active for all jumps, positive values let larger
  step changes pass through immediately
- `CompanionSourceGridHoldSeconds` applies the same short-outage hold behavior
  to per-source grid companion services
- `CompanionSourceGridSmoothingAlpha` applies the same optional smoothing to
  per-source grid companion services
- `CompanionSourceGridSmoothingMaxJumpWatts` applies the same “only smooth
  smaller jumps” rule to per-source grid companion services

For sites where the external meter path is generally stable but Modbus or HTTP
can blip for a second or two, a small hold window such as `5` seconds and a
neutral smoothing alpha such as `1.0` usually avoids visible grid-power
flutter without hiding longer faults.

### Battery discharge-balance policy

When `AutoEnergySources` aggregates more than one battery-like source, the
service also derives a discharge-balance error in watts. A small optional
policy layer can use that error in two ways:

- raise a visible warning in the operational state once the imbalance grows
  beyond a configured threshold
- add a soft extra surplus penalty to Auto mode so charging becomes more
  conservative while one ESS is carrying noticeably more discharge than its
  current fair-share target

Main keys:

- `AutoBatteryDischargeBalancePolicyEnabled`
- `AutoBatteryDischargeBalanceWarnErrorWatts`
- `AutoBatteryDischargeBalanceBiasStartErrorWatts`
- `AutoBatteryDischargeBalanceBiasMaxPenaltyWatts`
- `AutoBatteryDischargeBalanceBiasMode`
- `AutoBatteryDischargeBalanceBiasReserveMarginSoc`
- `AutoBatteryDischargeBalanceCoordinationEnabled`
- `AutoBatteryDischargeBalanceCoordinationSupportMode`
- `AutoBatteryDischargeBalanceCoordinationStartErrorWatts`
- `AutoBatteryDischargeBalanceCoordinationMaxPenaltyWatts`
- `AutoBatteryDischargeBalanceVictronBiasEnabled`
- `AutoBatteryDischargeBalanceVictronBiasSourceId`
- `AutoBatteryDischargeBalanceVictronBiasService`
- `AutoBatteryDischargeBalanceVictronBiasPath`
- `AutoBatteryDischargeBalanceVictronBiasBaseSetpointWatts`
- `AutoBatteryDischargeBalanceVictronBiasDeadbandWatts`
- `AutoBatteryDischargeBalanceVictronBiasActivationMode`
- `AutoBatteryDischargeBalanceVictronBiasSupportMode`
- `AutoBatteryDischargeBalanceVictronBiasKp`
- `AutoBatteryDischargeBalanceVictronBiasKi`
- `AutoBatteryDischargeBalanceVictronBiasKd`
- `AutoBatteryDischargeBalanceVictronBiasIntegralLimitWatts`
- `AutoBatteryDischargeBalanceVictronBiasMaxAbsWatts`
- `AutoBatteryDischargeBalanceVictronBiasRampRateWattsPerSecond`
- `AutoBatteryDischargeBalanceVictronBiasMinUpdateSeconds`
- `AutoBatteryDischargeBalanceVictronBiasAutoApplyEnabled`
- `AutoBatteryDischargeBalanceVictronBiasAutoApplyMinConfidence`
- `AutoBatteryDischargeBalanceVictronBiasAutoApplyMinProfileSamples`
- `AutoBatteryDischargeBalanceVictronBiasAutoApplyMinStabilityScore`
- `AutoBatteryDischargeBalanceVictronBiasAutoApplyBlend`
- `AutoBatteryDischargeBalanceVictronBiasObservationWindowSeconds`
- `AutoBatteryDischargeBalanceVictronBiasOscillationLockoutEnabled`
- `AutoBatteryDischargeBalanceVictronBiasOscillationLockoutWindowSeconds`
- `AutoBatteryDischargeBalanceVictronBiasOscillationLockoutMinDirectionChanges`
- `AutoBatteryDischargeBalanceVictronBiasOscillationLockoutDurationSeconds`
- `AutoBatteryDischargeBalanceVictronBiasRollbackEnabled`
- `AutoBatteryDischargeBalanceVictronBiasRollbackMinStabilityScore`
- `AutoBatteryDischargeBalanceVictronBiasTelemetryRequireCleanPhases`

`AutoBatteryDischargeBalanceBiasMode` supports:

- `always`
- `export_only`
- `above_reserve_band`
- `export_and_above_reserve_band`

`AutoBatteryDischargeBalanceBiasReserveMarginSoc` defines how far the combined
SOC should sit above the learned reserve-band floor before reserve-gated bias
becomes active.

`AutoBatteryDischargeBalanceCoordinationEnabled` adds a second, even more
conservative penalty stage, but only when at least two configured battery or
hybrid sources are currently classified as control-ready. This is still not a
foreign ESS setpoint path. It only tells Auto mode to back off a bit more when
real multi-ESS coordination looks realistic for the current source mix.

`AutoBatteryDischargeBalanceCoordinationSupportMode` supports:

- `supported_only`
- `allow_experimental`

Use `supported_only` when you only want this second-stage penalty to react to
profiles that advertise a supported write path. Use `allow_experimental` when
experimental profile write paths may also count as coordination-ready for this
soft penalty stage.

`AutoBatteryDischargeBalanceVictronBiasEnabled` enables a first experimental
indirect coordination path for mixed ESS setups. This path does not write a
foreign ESS. Instead it adjusts one local Victron GX DBus setpoint so the
shared grid value nudges the second ESS to back off or contribute more through
its own controller.

Main Victron-bias keys:

- `AutoBatteryDischargeBalanceVictronBiasSourceId`
- `AutoBatteryDischargeBalanceVictronBiasService`
- `AutoBatteryDischargeBalanceVictronBiasPath`
- `AutoBatteryDischargeBalanceVictronBiasBaseSetpointWatts`
- `AutoBatteryDischargeBalanceVictronBiasDeadbandWatts`
- `AutoBatteryDischargeBalanceVictronBiasActivationMode`
- `AutoBatteryDischargeBalanceVictronBiasSupportMode`
- `AutoBatteryDischargeBalanceVictronBiasKp`
- `AutoBatteryDischargeBalanceVictronBiasKi`
- `AutoBatteryDischargeBalanceVictronBiasKd`
- `AutoBatteryDischargeBalanceVictronBiasIntegralLimitWatts`
- `AutoBatteryDischargeBalanceVictronBiasMaxAbsWatts`
- `AutoBatteryDischargeBalanceVictronBiasRampRateWattsPerSecond`
- `AutoBatteryDischargeBalanceVictronBiasMinUpdateSeconds`
- `AutoBatteryDischargeBalanceVictronBiasAutoApplyEnabled`
- `AutoBatteryDischargeBalanceVictronBiasAutoApplyMinConfidence`
- `AutoBatteryDischargeBalanceVictronBiasAutoApplyMinProfileSamples`
- `AutoBatteryDischargeBalanceVictronBiasAutoApplyMinStabilityScore`
- `AutoBatteryDischargeBalanceVictronBiasAutoApplyBlend`
- `AutoBatteryDischargeBalanceVictronBiasObservationWindowSeconds`
- `AutoBatteryDischargeBalanceVictronBiasOscillationLockoutEnabled`
- `AutoBatteryDischargeBalanceVictronBiasOscillationLockoutWindowSeconds`
- `AutoBatteryDischargeBalanceVictronBiasOscillationLockoutMinDirectionChanges`
- `AutoBatteryDischargeBalanceVictronBiasOscillationLockoutDurationSeconds`
- `AutoBatteryDischargeBalanceVictronBiasRollbackEnabled`
- `AutoBatteryDischargeBalanceVictronBiasRollbackMinStabilityScore`
- `AutoBatteryDischargeBalanceVictronBiasTelemetryRequireCleanPhases`

`AutoBatteryDischargeBalanceVictronBiasSupportMode` supports:

- `supported_only`
- `allow_experimental`

The default target is `com.victronenergy.settings` at
`/Settings/CGwacs/AcPowerSetPoint`. Keep this path disabled until the local GX
side really owns that setpoint for the installation. The controller includes a
deadband, integral clamp, output clamp, ramp-rate limit, and minimum update
interval so brief balance noise does not translate into setpoint chatter.

`AutoBatteryDischargeBalanceVictronBiasActivationMode` supports:

- `always`
- `export_only`
- `above_reserve_band`
- `export_and_above_reserve_band`

The learning and recommendation path is now profiled by:

- action asymmetry: `more_export` vs `less_export`
- site regime: `export` vs `import`
- day phase: `day` vs `night`
- reserve phase: `reserve_band` vs `above_reserve_band`

Each learned profile carries a small formal snapshot with:

- `typical_response_delay_seconds`
- `effective_gain`
- `safe_ramp_rate_watts_per_second`
- `preferred_bias_limit_watts`

`AutoBatteryDischargeBalanceVictronBiasAutoApplyEnabled` lets the service
apply learned recommendations at runtime without editing the base config.
Guardrails are controlled through:

- `AutoBatteryDischargeBalanceVictronBiasAutoApplyMinConfidence`
- `AutoBatteryDischargeBalanceVictronBiasAutoApplyMinProfileSamples`
- `AutoBatteryDischargeBalanceVictronBiasAutoApplyMinStabilityScore`
- `AutoBatteryDischargeBalanceVictronBiasAutoApplyBlend`

Auto-apply is intentionally guarded and only runs when enough good telemetry
has accumulated for the currently active learning profile.

The current hardening stage adds four extra guardrails around that loop:

- anti-oscillation lockout after repeated `more_export`/`less_export` flips
- rollback to the last stable learned tuning
- learning only in relatively clean telemetry phases
- stepwise auto-apply with an observation window between changes

The relevant keys are:

- `AutoBatteryDischargeBalanceVictronBiasObservationWindowSeconds`
- `AutoBatteryDischargeBalanceVictronBiasOscillationLockoutEnabled`
- `AutoBatteryDischargeBalanceVictronBiasOscillationLockoutWindowSeconds`
- `AutoBatteryDischargeBalanceVictronBiasOscillationLockoutMinDirectionChanges`
- `AutoBatteryDischargeBalanceVictronBiasOscillationLockoutDurationSeconds`
- `AutoBatteryDischargeBalanceVictronBiasRollbackEnabled`
- `AutoBatteryDischargeBalanceVictronBiasRollbackMinStabilityScore`
- `AutoBatteryDischargeBalanceVictronBiasTelemetryRequireCleanPhases`

Suggested starting point:

```ini
AutoBatteryDischargeBalancePolicyEnabled=1
AutoBatteryDischargeBalanceWarnErrorWatts=500
AutoBatteryDischargeBalanceBiasStartErrorWatts=750
AutoBatteryDischargeBalanceBiasMaxPenaltyWatts=300
AutoBatteryDischargeBalanceBiasMode=export_and_above_reserve_band
AutoBatteryDischargeBalanceBiasReserveMarginSoc=5
AutoBatteryDischargeBalanceCoordinationEnabled=1
AutoBatteryDischargeBalanceCoordinationSupportMode=supported_only
AutoBatteryDischargeBalanceCoordinationStartErrorWatts=1000
AutoBatteryDischargeBalanceCoordinationMaxPenaltyWatts=200
AutoBatteryDischargeBalanceVictronBiasEnabled=1
AutoBatteryDischargeBalanceVictronBiasSourceId=victron
AutoBatteryDischargeBalanceVictronBiasService=com.victronenergy.settings
AutoBatteryDischargeBalanceVictronBiasPath=/Settings/CGwacs/AcPowerSetPoint
AutoBatteryDischargeBalanceVictronBiasBaseSetpointWatts=50
AutoBatteryDischargeBalanceVictronBiasDeadbandWatts=100
AutoBatteryDischargeBalanceVictronBiasActivationMode=export_and_above_reserve_band
AutoBatteryDischargeBalanceVictronBiasSupportMode=supported_only
AutoBatteryDischargeBalanceVictronBiasKp=0.2
AutoBatteryDischargeBalanceVictronBiasKi=0.02
AutoBatteryDischargeBalanceVictronBiasKd=0.0
AutoBatteryDischargeBalanceVictronBiasIntegralLimitWatts=250
AutoBatteryDischargeBalanceVictronBiasMaxAbsWatts=500
AutoBatteryDischargeBalanceVictronBiasRampRateWattsPerSecond=50
AutoBatteryDischargeBalanceVictronBiasMinUpdateSeconds=2
AutoBatteryDischargeBalanceVictronBiasAutoApplyEnabled=0
AutoBatteryDischargeBalanceVictronBiasAutoApplyMinConfidence=0.85
AutoBatteryDischargeBalanceVictronBiasAutoApplyMinProfileSamples=3
AutoBatteryDischargeBalanceVictronBiasAutoApplyMinStabilityScore=0.75
AutoBatteryDischargeBalanceVictronBiasAutoApplyBlend=0.25
AutoBatteryDischargeBalanceVictronBiasObservationWindowSeconds=30
AutoBatteryDischargeBalanceVictronBiasOscillationLockoutEnabled=1
AutoBatteryDischargeBalanceVictronBiasOscillationLockoutWindowSeconds=120
AutoBatteryDischargeBalanceVictronBiasOscillationLockoutMinDirectionChanges=3
AutoBatteryDischargeBalanceVictronBiasOscillationLockoutDurationSeconds=180
AutoBatteryDischargeBalanceVictronBiasRollbackEnabled=1
AutoBatteryDischargeBalanceVictronBiasRollbackMinStabilityScore=0.45
AutoBatteryDischargeBalanceVictronBiasTelemetryRequireCleanPhases=1
```

This policy is intentionally soft. It does not command foreign ESS devices or
force equal discharge. It only makes the Auto policy more cautious and exposes
the imbalance clearly through the local state surface. The Victron-bias path is
the first exception: it is still indirect, but it can move one local Victron GX
setpoint when enabled.

Per-source naming/device-instance rules:

- battery-like source services use `CompanionSourceBatteryServicePrefix`
  plus a sanitized `source_id`
- PV-like source services use `CompanionSourcePvInverterServicePrefix`
  plus a sanitized `source_id`
- grid-like source services use `CompanionSourceGridServicePrefix`
  plus a sanitized `source_id`
- source services allocate `DeviceInstance` values starting at the configured
  `...Base` value in snapshot order

Example: one Victron battery plus one external hybrid inverter:

```ini
AutoUseCombinedBatterySoc=1
AutoEnergySources=victron,hybrid

AutoEnergySource.victron.Profile=dbus-battery
AutoEnergySource.victron.Service=com.victronenergy.battery.lynxparallel
AutoEnergySource.victron.UsableCapacityWh=10240

AutoEnergySource.hybrid.Profile=dbus-hybrid
AutoEnergySource.hybrid.Service=com.victronenergy.multi.rs.hybrid
AutoEnergySource.hybrid.UsableCapacityWh=14000

CompanionDbusBridgeEnabled=1
CompanionBatteryServiceEnabled=1
CompanionPvInverterServiceEnabled=1
CompanionGridServiceEnabled=0
CompanionGridAuthoritativeSource=
CompanionSourceServicesEnabled=1
CompanionSourceGridServicesEnabled=0
CompanionGridHoldSeconds=5
CompanionGridSmoothingAlpha=1.0
CompanionGridSmoothingMaxJumpWatts=0
CompanionSourceGridHoldSeconds=5
CompanionSourceGridSmoothingAlpha=1.0
CompanionSourceGridSmoothingMaxJumpWatts=0
CompanionBatteryServiceName=com.victronenergy.battery.external_100
CompanionPvInverterServiceName=com.victronenergy.pvinverter.external_101
CompanionGridServiceName=com.victronenergy.grid.external_102
CompanionSourceBatteryServicePrefix=com.victronenergy.battery.external
CompanionSourcePvInverterServicePrefix=com.victronenergy.pvinverter.external
CompanionSourceGridServicePrefix=com.victronenergy.grid.external
CompanionSourceBatteryDeviceInstanceBase=200
CompanionSourcePvInverterDeviceInstanceBase=300
CompanionSourceGridDeviceInstanceBase=400
```

Example: prefix-based discovery for a second source:

```ini
AutoUseCombinedBatterySoc=1
AutoEnergySources=victron,external

AutoEnergySource.victron.Profile=dbus-battery
AutoEnergySource.victron.UsableCapacityWh=5120

AutoEnergySource.external.Profile=dbus-hybrid
AutoEnergySource.external.ServicePrefix=com.victronenergy.hybrid
AutoEnergySource.external.UsableCapacityWh=10000
```

Example: external source through the first non-DBus connector:

```ini
AutoUseCombinedBatterySoc=1
AutoEnergySources=victron,external

AutoEnergySource.victron.Profile=dbus-battery
AutoEnergySource.victron.UsableCapacityWh=5120

AutoEnergySource.external.Profile=template-http-hybrid
AutoEnergySource.external.ConfigPath=/data/etc/external-energy.ini
AutoEnergySource.external.UsableCapacityWh=10000
```

Example: Huawei MA platform over the inverter access-point path:

```ini
AutoUseCombinedBatterySoc=1
AutoEnergySources=victron,huawei

AutoEnergySource.victron.Profile=dbus-battery
AutoEnergySource.victron.UsableCapacityWh=5120

AutoEnergySource.huawei.Profile=huawei_ma_native_ap
AutoEnergySource.huawei.ConfigPath=/data/etc/huawei-ma-modbus.ini
AutoEnergySource.huawei.Service=SUN2000-MA
AutoEnergySource.huawei.UsableCapacityWh=14000
```

Huawei preset notes:

- `huawei_*_native_ap` defaults to AP-style probing with host `192.168.200.1`
  and port candidates `6607,502`
- `huawei_*_native_lan` probes ports `502,6607`
- `huawei_*_sdongle` keeps the same Modbus-TCP preset family but marks the
  access mode separately for operational tooling
- `huawei_smartlogger_modbus_tcp` defaults to port `502`
- all Huawei presets currently mark write support as `experimental`

Example `template_http` energy-source file:

```ini
[Adapter]
BaseUrl=http://hybrid.local
RequestTimeoutSeconds=2.0

[EnergyRequest]
Method=GET
Url=/api/energy

[EnergyResponse]
SocPath=data.soc
UsableCapacityWhPath=data.capacity_wh
BatteryPowerPath=data.battery_power_w
AcPowerPath=data.ac_power_w
OnlinePath=data.online
ConfidencePath=data.confidence
```

A ready-to-copy starter file also lives in
[`deploy/venus/template-energy-source.ini`](deploy/venus/template-energy-source.ini).

Example `modbus` energy-source file:

```ini
[Adapter]
Transport=tcp

[Transport]
Host=192.0.2.90
Port=502
UnitId=7
RequestTimeoutSeconds=2.0

[SocRead]
RegisterType=holding
Address=10
DataType=uint16
Scale=0.1

[UsableCapacityRead]
RegisterType=holding
Address=20
DataType=uint16

[BatteryPowerRead]
RegisterType=holding
Address=30
DataType=int16

[AcPowerRead]
RegisterType=holding
Address=40
DataType=uint16
```

Example `command_json` energy-source file:

```ini
[Command]
Args=python3 /data/bin/external-energy-helper.py --once
TimeoutSeconds=2.0

[Response]
SocPath=data.soc
UsableCapacityWhPath=data.capacity_wh
BatteryPowerPath=data.battery_power_w
AcPowerPath=data.ac_power_w
OnlinePath=data.online
ConfidencePath=data.confidence
```

This `command_json` pattern is also the recommended MQTT bridge seam: let one
small local helper subscribe to MQTT, normalize the vendor payload, and return
one compact JSON object to the wallbox service.

Starter files:

- [`deploy/venus/template-energy-source.ini`](deploy/venus/template-energy-source.ini)
- [`deploy/venus/template-energy-source-modbus.ini`](deploy/venus/template-energy-source-modbus.ini)
- [`deploy/venus/template-energy-source-command.ini`](deploy/venus/template-energy-source-command.ini)

## Scheduled / Plan Policy

The mode values are part of the service contract:

- `Mode=0` (`Manual`) follows direct user control and can charge immediately.
- `Mode=1` (`Auto`) is PV-surplus/Auto-policy charging only. It does not start
  the night fallback by itself.
- `Mode=2` (`Scheduled`/`Plan`) runs the same Auto policy during the configured
  daytime window and adds target-day night/fallback charging after that window.

The night/fallback window starts after the configured daytime window plus
`AutoScheduledNightStartDelaySeconds`, and it is capped by
`AutoScheduledLatestEndTime`.

Main settings:

- `AutoScheduledEnabledDays`
- `AutoScheduledNightStartDelaySeconds`
- `AutoScheduledLatestEndTime`
- `AutoScheduledNightCurrentAmps`

These are also available through DBus and MQTT as runtime overrides.

## Runtime Overrides

Selected runtime values can be changed through DBus and MQTT and are stored in
the runtime override file.

The default `RuntimeOverridesPath` uses `/run/...`, so the active values stay
available during runtime and service restarts, and the base config takes over
again after a GX reboot.

The runtime layer is a strong fit for:

- charging mode
- current and phase selection
- Auto thresholds and delays
- learned-power tuning
- phase policy
- Scheduled policy

Operational metadata:

- `/Auto/RuntimeOverridesActive`
- `/Auto/RuntimeOverridesPath`

## Local HTTP Control API

An optional process-local HTTP control surface can be enabled when another
local service or script should drive the charger without going through DBus
directly.

Main config values:

- `ControlApiEnabled`
- `ControlApiHost`
- `ControlApiPort`
- `ControlApiAuthToken`
- `ControlApiReadToken`
- `ControlApiControlToken`
- `ControlApiAdminToken`
- `ControlApiUpdateToken`
- `ControlApiLocalhostOnly`
- `ControlApiUnixSocketPath`
- `ControlApiAuditPath`
- `ControlApiIdempotencyPath`
- `ControlApiRateLimitMaxRequests`
- `ControlApiRateLimitWindowSeconds`
- `ControlApiCriticalCooldownSeconds`

Recommended shape:

- keep `ControlApiLocalhostOnly=1`
- keep `ControlApiHost=127.0.0.1`
- prefer `ControlApiUnixSocketPath` for process-local automation when feasible
- prefer split `ControlApiReadToken` and `ControlApiControlToken` for local clients with different scopes
- add `ControlApiAdminToken` and `ControlApiUpdateToken` only when you want stricter local separation for advanced local control
- use `ControlApiAuthToken` only when one shared token is enough
- keep `ControlApiAuditPath` and `ControlApiIdempotencyPath` on `/run/...` or `/tmp/...`
- keep the API rate-limit window small and local; this is meant as abuse protection, not remote multi-tenant throttling
- do not point API audit or idempotency files at flash-backed storage
- treat this as a command surface, not a general telemetry interface

The formal JSON contract, the OpenAPI `3.1` document, HTTP status mapping, and
`curl` examples live in [CONTROL_API.md](CONTROL_API.md).

The same listener also exposes `GET /v1/capabilities` for stable topology and
command discovery, `GET /v1/events` for NDJSON event streaming, and the
state endpoints:

- `GET /v1/state/healthz`
- `GET /v1/state/version`
- `GET /v1/state/build`
- `GET /v1/state/contracts`
- `GET /v1/state/summary`
- `GET /v1/state/runtime`
- `GET /v1/state/operational`
- `GET /v1/state/dbus-diagnostics`
- `GET /v1/state/topology`
- `GET /v1/state/update`
- `GET /v1/state/config-effective`
- `GET /v1/state/health`

For read-only local inspection on the same listener, see [STATE_API.md](STATE_API.md).
For stability rules inside `v1`, see [API_VERSIONING.md](API_VERSIONING.md).

## Validation Before Start

Validate the full wallbox config:

```bash
python3 -m venus_evcharger.backend.probe validate-wallbox deploy/venus/config.venus_evcharger.ini
```

Validate adapter files individually:

```bash
python3 -m venus_evcharger.backend.probe validate /data/etc/wallbox-meter.ini
python3 -m venus_evcharger.backend.probe validate /data/etc/wallbox-switch.ini
python3 -m venus_evcharger.backend.probe validate /data/etc/wallbox-charger.ini
```

## Live Inspection

For runtime diagnostics, see:

- [DIAGNOSTICS.md](DIAGNOSTICS.md)
- [TROUBLESHOOTING.md](TROUBLESHOOTING.md)
- [STATE_MODEL.md](STATE_MODEL.md)

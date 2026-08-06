# Shelly Profiles

This file is the quick reference for native Shelly backends that support
`[Adapter] ShellyProfile=...`.

Use it when you want to avoid writing a custom `template_meter` or
`template_switch` config just to select the correct Shelly RPC namespace.

## How To Read This Table

- `Backend Type`: choose this in the child/backend config as `Type=...`
- `ShellyProfile`: optional preset that fills the most likely `Component=` and
  default `Id=0`
- `Use For`: what role this preset is meant for in this project
- `RPC Namespace`: the Shelly component family the native backend will talk to
- `Notes`: practical limits or things to watch out for

You can still override `Component=` and `Id=` manually if your device/profile
needs a small variation.

## Switch Backends

| Typical Shelly family | Backend Type | ShellyProfile | RPC Namespace | Use For | Notes |
| --- | --- | --- | --- | --- | --- |
| Shelly 1 / Plus 1 / Pro 1 style single-channel relays | `shelly_switch` or `shelly_contactor_switch` | `switch_1ch` | `Switch.GetStatus` / `Switch.Set` | Simple relay or contactor control | Best fit for one dry-contact or one switched output |
| Shelly 1PM / Plus 1PM / Pro 1PM style single-channel relays with power in switch status | `shelly_switch` or `shelly_contactor_switch` | `switch_1ch_with_pm` | `Switch.GetStatus` / `Switch.Set` | Relay or contactor control where the device family also exposes power through `Switch` | Good native default for the classic wallbox relay path |
| Multi-channel switch devices and switch-based plugs | `shelly_switch` or `shelly_contactor_switch` | `switch_multi_or_plug` | `Switch.GetStatus` / `Switch.Set` | One selected switch channel | Use `Id=` to pick the concrete channel |
| 2PM-family devices when they are operated in switch profile | `shelly_switch` or `shelly_contactor_switch` | `switch_or_cover_profile` | `Switch.GetStatus` / `Switch.Set` | Switch-mode 2PM path | This preset assumes the device is in `switch` profile, not `cover` |

## Meter Backends

| Typical Shelly family | Backend Type | ShellyProfile | RPC Namespace | Use For | Notes |
| --- | --- | --- | --- | --- | --- |
| PM1-family pure power meter devices | `shelly_meter` | `pm1_meter_only` | `PM1.GetStatus` | Single-channel meter | Native meter normalization without a template adapter |
| EM Mini / EM / Pro EM style single- or dual-channel meter devices | `shelly_meter` | `em1_meter_single_or_dual` | `EM1.GetStatus` | One selected metering channel | Use `Id=` to choose channel `0` or `1` where applicable |
| 3EM / Pro 3EM style devices in aggregated three-phase mode | `shelly_meter` | `em_3phase_profiled` | `EM.GetStatus` | Aggregated three-phase meter | Best fit when the device is in `triphase` profile |

## Examples

### Single-channel relay

```ini
[Adapter]
Type=shelly_switch
Host=192.0.2.20
ShellyProfile=switch_1ch_with_pm
```

### Single-channel EM1 meter

```ini
[Adapter]
Type=shelly_meter
Host=192.0.2.30
ShellyProfile=em1_meter_single_or_dual
Id=1
```

### Three-phase EM meter

```ini
[Adapter]
Type=shelly_meter
Host=192.0.2.40
ShellyProfile=em_3phase_profiled

[Phase]
MeasuredPhaseSelection=P1_P2_P3
```

## Practical Boundaries

These presets are intentionally wallbox-focused. They currently cover the
Shelly RPC families that are directly useful here:

- `Switch`
- `PM1`
- `EM1`
- `EM`

They do not try to cover the full Shelly lighting/sensor space such as
`Light`, `RGB`, `RGBW`, `CCT`, `Flood`, or `Presence`.

For devices that do not match these native families cleanly yet, keep using:

- `template_switch`
- `template_meter`
- `switch_group`

That gives us a safe fallback without blocking support for additional Shelly
families later.

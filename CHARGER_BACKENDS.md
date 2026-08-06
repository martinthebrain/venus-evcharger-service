# Charger Backends

This file collects the charger-focused backend options, transport shapes, and
example configurations for Venus OS wallbox deployments.

## Backend Overview

| Backend | Transport | Delivered functions | Good fit |
| --- | --- | --- | --- |
| `goe_charger` | HTTP | enable, disable, current, status, power, energy | go-e charger with native current control |
| `simpleevse_charger` | Modbus | enable, disable, current, status, fault | SimpleEVSE WB/DIN and similar boards |
| `smartevse_charger` | Modbus | enable, disable, current, status, fault | SmartEVSE-family boards |
| `modbus_charger` | Modbus | generic profile-driven charger mapping | documented Modbus EVSEs and custom mappings |
| `template_charger` | HTTP | path-driven adapter surface | chargers reachable through HTTP/JSON |

## Native Charger Backends

### go-e

Use `goe_charger` when the charger exposes the local go-e API and should handle
enable and current setpoints directly.

Highlights:

- native enable / disable
- native current setpoint
- status, power, and energy readback
- direct fit for meterless charger-native setups

### SimpleEVSE

Use `simpleevse_charger` for SimpleEVSE boards that expose the documented
register map over Modbus.

Highlights:

- native enable / disable
- native current setpoint
- charger status and fault mapping
- strong fit for fixed one-, two-, or three-phase boards

Example:

```ini
[Adapter]
Type=simpleevse_charger
Transport=serial_rtu

[Capabilities]
SupportedPhaseSelections=P1_P2_P3

[Transport]
Device=/dev/ttyUSB0
Baudrate=9600
Parity=N
StopBits=1
UnitId=1
```

### SmartEVSE

Use `smartevse_charger` for SmartEVSE-family boards with Modbus access.

Highlights:

- native enable / disable
- native current setpoint
- charger status and fault mapping
- clean fit for fixed charger boards with external phase switching

Example:

```ini
[Adapter]
Type=smartevse_charger
Transport=serial_rtu

[Capabilities]
SupportedPhaseSelections=P1_P2_P3

[Transport]
Device=/dev/ttyUSB0
Baudrate=9600
Parity=N
StopBits=1
UnitId=1
```

## Generic Modbus Charger

`modbus_charger` maps a documented EVSE register schema into the normalized
charger surface.

Typical transport choices:

- `serial_rtu`
- `tcp`
- `udp`

Minimal example:

```ini
[Adapter]
Type=modbus_charger
Profile=generic
Transport=tcp

[Transport]
Host=192.0.2.40
Port=502
UnitId=7

[EnableWrite]
RegisterType=coil
Address=20
TrueValue=1
FalseValue=0

[CurrentWrite]
RegisterType=holding
Address=30
DataType=uint16
Scale=10
```

## Meterless Charger-Native Setups

A charger backend can carry a full split setup with:

- `MeterType=none`
- `SwitchType=none`
- `ChargerType=<native or template charger>`

In these setups the service derives the visible charger state from charger
readback and runtime policy. When the charger provides current but no measured
power, the service can publish estimated power and energy and marks that state
through `/Auto/ChargerEstimate*`.

## Charger Plus External Phase Switching

A charger backend can be combined with:

- `SwitchType=switch_group`
- `SwitchType=template_switch`
- `SwitchType=shelly_switch`
- `SwitchType=shelly_contactor_switch`

This layout is a strong fit for:

- contactor banks
- fixed charger boards
- installations where current control and phase switching live in different
  devices

## Validation

Validate a full wallbox configuration:

```bash
python3 -m venus_evcharger.backend.probe validate-wallbox deploy/venus/config.venus_evcharger.ini
```

Validate a charger adapter file directly:

```bash
python3 -m venus_evcharger.backend.probe validate /data/etc/wallbox-charger.ini
```

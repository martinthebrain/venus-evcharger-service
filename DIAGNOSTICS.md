# Diagnostics

This file collects the operational gateway values, log locations, and quick
checks for live troubleshooting on Venus OS.

## Service Commands

```bash
svstat /service/dbus-venus-evcharger
svc -t /service/dbus-venus-evcharger
tail -f /var/volatile/log/dbus-venus-evcharger/current
tail -f /var/volatile/log/dbus-venus-evcharger/auto-reasons.log
```

## First Five Checks

When the charger did something unexpected, start with these paths:

```bash
python3 scripts/ops/gateway_cache_read.py \
  /Mode /StartStop /Status /Auto/DecisionReason /Auto/DecisionState
```

Read them as:

- `Mode=0`: Manual; direct user control.
- `Mode=1`: Auto; PV-surplus/Auto-policy only.
- `Mode=2`: Scheduled; Auto during the day window plus night/fallback charging.
- `/Status`: outward EV charger status shown to Venus.
- `/Auto/DecisionReason`: latest policy or safety reason behind the Auto/Scheduled decision.

## Why Did It Start Or Stop?

The service publishes a compact "last decision" surface so you do not have to
reconstruct the answer from logs:

| Path | Meaning |
| --- | --- |
| `/Auto/DecisionReason` | Latest Auto/Scheduled health or policy reason |
| `/Auto/DecisionState` | Normalized state such as `idle`, `waiting`, `charging`, or `recovery` |
| `/Auto/DecisionRelayIntent` | Last intended relay/charger enable state: `1`, `0`, or `-1` when unknown |
| `/Auto/DecisionSurplusWatts` | Surplus used by the decision after smoothing/adjustments |
| `/Auto/DecisionGridWatts` | Grid import/export value used by the decision |
| `/Auto/DecisionSocPercent` | Battery SOC used by the decision, or `-1` when unavailable |
| `/Auto/DecisionStartThresholdWatts` | Active start threshold |
| `/Auto/DecisionStopThresholdWatts` | Active stop threshold |
| `/Auto/DecisionProfile` | Active threshold profile, for example `normal` or `high-soc` |
| `/Auto/DecisionThresholdMode` | `static` or adaptive/learned threshold mode |

The same information is also available as structured JSON for a future GUI or
for local scripts:

```bash
curl -s http://127.0.0.1:8765/v1/state/operational
```

Look at `state.auto_decision.reason`, `state.auto_decision.relay_intent`,
`state.auto_decision.surplus_watts`, `state.auto_decision.soc_percent`, and the
active start/stop thresholds first.

Related gates and lockouts:

- `/Auto/MinSoc`, `/Auto/ResumeSoc`
- `/Auto/StartSurplusWatts`, `/Auto/StopSurplusWatts`
- `/Auto/GridRecoveryStartSeconds`, `/Auto/StopSurplusDelaySeconds`
- `/Auto/ScheduledState`, `/Auto/ScheduledReason`, `/Auto/ScheduledNightBoostActive`
- `/Auto/PhaseLockoutActive`, `/Auto/PhaseLockoutReason`
- `/Auto/ContactorLockoutActive`, `/Auto/ContactorLockoutReason`
- `/Auto/SwitchFeedbackMismatch`, `/Auto/SwitchInterlockOk`

The rolling reason log is still useful for history:

```bash
tail -n 200 /var/volatile/log/dbus-venus-evcharger/auto-reasons.log
```

## Backend Diagnostics

### Backend composition

- `/Auto/BackendMode`
- `/Auto/MeterBackend`
- `/Auto/SwitchBackend`
- `/Auto/ChargerBackend`

### Charger command health

- `/Auto/ChargerWriteErrors`
- `/Auto/ChargerCurrentTarget`
- `/Auto/ChargerCurrentTargetAge`
- `/Auto/ChargerEstimateActive`
- `/Auto/ChargerEstimateSource`
- `/Auto/LastChargerEstimateAge`

### Outward EVSE state

- `/Auto/StatusSource`
- `/Auto/FaultActive`
- `/Auto/FaultReason`
- `/Auto/RecoveryActive`

### Last Auto/Scheduled decision

- `/Auto/DecisionReason`
- `/Auto/DecisionState`
- `/Auto/DecisionStateCode`
- `/Auto/DecisionRelayIntent`
- `/Auto/DecisionSurplusWatts`
- `/Auto/DecisionGridWatts`
- `/Auto/DecisionSocPercent`
- `/Auto/DecisionStartThresholdWatts`
- `/Auto/DecisionStopThresholdWatts`
- `/Auto/DecisionProfile`
- `/Auto/DecisionThresholdMode`

### Transport and retry

- `/Auto/ChargerTransportActive`
- `/Auto/ChargerTransportReason`
- `/Auto/ChargerTransportSource`
- `/Auto/ChargerTransportDetail`
- `/Auto/LastChargerTransportAge`
- `/Auto/ChargerRetryActive`
- `/Auto/ChargerRetryReason`
- `/Auto/ChargerRetrySource`
- `/Auto/ChargerRetryRemaining`

## Scheduled Diagnostics

- `/Auto/ScheduledState`
- `/Auto/ScheduledStateCode`
- `/Auto/ScheduledReason`
- `/Auto/ScheduledReasonCode`
- `/Auto/ScheduledNightBoostActive`
- `/Auto/ScheduledTargetDayEnabled`
- `/Auto/ScheduledTargetDay`
- `/Auto/ScheduledTargetDate`
- `/Auto/ScheduledFallbackStart`
- `/Auto/ScheduledBoostUntil`

## Scheduled Mode Recovery

In `Mode=2` (`Scheduled/Plan`), `/StartStop=1` does not force the relay on.
It only allows the scheduled/Auto policy to charge. For immediate manual
charging, switch to manual mode first and then start:

Use the Venus GUI controls for `Mode=0` and `StartStop=1`, or the authenticated
local Control API when it is enabled. Operational tools must not bypass the
gateway with direct DBus writes.

If the service UI stops updating, restart the service before rebooting the GX:

```bash
svc -t /service/dbus-venus-evcharger
tail -n 200 /var/volatile/log/dbus-venus-evcharger/current
```

## Phase And Contactor Diagnostics

- `/Auto/PhaseObserved`
- `/Auto/PhaseMismatchActive`
- `/Auto/PhaseLockoutActive`
- `/Auto/PhaseLockoutTarget`
- `/Auto/PhaseLockoutReason`
- `/Auto/PhaseSupportedConfigured`
- `/Auto/PhaseSupportedEffective`
- `/Auto/PhaseDegradedActive`
- `/Auto/ContactorFaultCount`
- `/Auto/ContactorLockoutActive`
- `/Auto/ContactorLockoutReason`
- `/Auto/ContactorLockoutSource`
- `/Auto/ContactorLockoutReset`
- `/Auto/PhaseLockoutReset`

## Switch Feedback And Interlock

- `/Auto/SwitchFeedbackClosed`
- `/Auto/SwitchInterlockOk`
- `/Auto/SwitchFeedbackMismatch`
- `/Auto/LastSwitchFeedbackAge`

## Runtime Override Diagnostics

- `/Auto/RuntimeOverridesActive`
- `/Auto/RuntimeOverridesPath`

## Software Update Diagnostics

- `/Auto/SoftwareUpdateAvailable`
- `/Auto/SoftwareUpdateState`
- `/Auto/SoftwareUpdateStateCode`
- `/Auto/SoftwareUpdateDetail`
- `/Auto/SoftwareUpdateCurrentVersion`
- `/Auto/SoftwareUpdateAvailableVersion`
- `/Auto/SoftwareUpdateNoUpdateActive`
- `/Auto/SoftwareUpdateRun`
- `/Auto/SoftwareUpdateLastCheckAge`
- `/Auto/SoftwareUpdateLastRunAge`

The outward update-state vocabulary is fixed and compact:

- `idle`
- `checking`
- `up-to-date`
- `available`
- `available-blocked`
- `running`
- `installed`
- `check-failed`
- `install-failed`
- `update-unavailable`

## Probe Commands

Validate a full wallbox configuration:

```bash
python3 -m venus_evcharger.backend.probe validate-wallbox deploy/venus/config.venus_evcharger.ini
```

Validate individual adapters:

```bash
python3 -m venus_evcharger.backend.probe validate /data/etc/wallbox-meter.ini
python3 -m venus_evcharger.backend.probe validate /data/etc/wallbox-switch.ini
python3 -m venus_evcharger.backend.probe validate /data/etc/wallbox-charger.ini
```

## Venus/Cerbo Testbed

For repeatable diagnostics without touching real relays, use the deterministic
testbed scenarios:

```bash
python3 ./scripts/dev/venus_cerbo_testbed.py simulate pv-surplus
python3 ./scripts/dev/venus_cerbo_testbed.py simulate night-fallback
python3 ./scripts/dev/venus_cerbo_testbed.py simulate unplug-replug
```

On a real Venus OS/Cerbo device, the same helper can run read-only relay-path
probes. It does not switch relays:

```bash
python3 ./scripts/dev/venus_cerbo_testbed.py probe-real
```

The probe checks the two Cerbo relay state paths and the relay function paths
that must stay in manual mode before any relay is used for charger switching.

## Shelly Raw Values

For Shelly-style setups, the service has no vehicle communication. It infers
"charging" from relay state, measured power, and energy deltas. If the Venus UI
looks wrong, compare the published state with the raw Shelly RPC values:

```bash
SHELLY_HOST=192.0.2.76
curl -s "http://${SHELLY_HOST}/rpc/PM1.GetStatus?id=0"
curl -s "http://${SHELLY_HOST}/rpc/Switch.GetStatus?id=0"
curl -s "http://${SHELLY_HOST}/rpc/Shelly.GetDeviceInfo"
```

Important fields:

- `apower`: current measured power; below the configured charging threshold the service treats the session as not actively charging.
- `aenergy.total`: Shelly lifetime energy counter; the service converts this to Venus session energy before publishing.
- `output`: switch/relay output state.

If an unplug/replug sequence is suspected, check:

- `/Session/Energy` should return to `0` when charging stops.
- `/Ac/Energy/Forward` should show session energy, not Shelly lifetime energy.
- `/Auto/LastShellyReadAge` should stay low while the Shelly is reachable.

## Local Shelly RPC Emulator

For Venus OS smoke tests without a physical Shelly relay, a small RPC emulator
can run on another machine in the same LAN:

```bash
python3 ./scripts/dev/mock_shelly_rpc.py --bind 0.0.0.0 --port 8080
```

Point the Venus config to that host including the port:

```ini
Host=192.0.2.25:8080
```

Supported RPC endpoints:

- `GET /rpc/Shelly.GetDeviceInfo`
- `GET /rpc/Switch.GetStatus?id=0`
- `GET /rpc/Switch.Set?id=0&on=true|false`

Fault injection and state changes:

- `GET /__admin/state?relay=1&apower=2300&current=10&voltage=230&total_energy_wh=12500`
- `GET /__admin/fault?mode=http500`
- `GET /__admin/fault?mode=timeout&seconds=5`
- `GET /__admin/fault?mode=badjson`
- `GET /__admin/reset`

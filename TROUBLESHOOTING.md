# Troubleshooting

This guide collects the fastest checks for common field issues on Venus OS.

For the full DBus path list and live diagnostics, see
[DIAGNOSTICS.md](DIAGNOSTICS.md).

## Service Does Not Start

Check the runit service first:

```bash
svstat /service/dbus-venus-evcharger
```

Then reinstall the service wiring:

```bash
cd /data/shellyWB
./deploy/venus/install_venus_evcharger_service.sh
```

If you want the direct traceback, start the service once in the foreground:

```bash
python3 ./venus_evcharger_service.py
```

If the issue started directly after a refresh, check the bootstrap and release
behavior in [UPDATE_FLOW.md](UPDATE_FLOW.md).

## EV Charger Tile Does Not Appear

Check whether the gateway reports the EV charger service as connected:

```bash
python3 scripts/ops/gateway_cache_read.py /Connected
```

Replace `http_60` with the configured `DeviceInstance` when needed.

If the cached service is connected and the tile still does not refresh, restart the GUI:

```bash
svc -t /service/gui
```

## Auto Mode Does Not Start Charging

Check these points:

- current `/Mode` and `/AutoStart`
- PV surplus and grid values
- battery SOC versus `AutoMinSoc` and `AutoResumeSoc`
- start-delay, off-time, and recovery timers
- current scheduled state when `/Mode = 2`

Useful logs:

```bash
tail -f /var/volatile/log/dbus-venus-evcharger/current
tail -f /var/volatile/log/dbus-venus-evcharger/auto-reasons.log
```

Useful DBus paths:

- `/Auto/StatusSource`
- `/Auto/FaultReason`
- `/Auto/RecoveryActive`
- `/Auto/ScheduledState`
- `/Auto/ScheduledReason`

## Charger Commands Do Not Reach The Backend

Check:

- `/Auto/ChargerBackend`
- `/Auto/ChargerWriteErrors`
- `/Auto/ChargerCurrentTarget`
- `/Auto/ChargerCurrentTargetAge`
- `/Auto/ChargerTransportReason`
- `/Auto/ChargerRetryReason`

If the setup uses a charger adapter file, validate it directly:

```bash
python3 -m venus_evcharger.backend.probe validate /data/etc/wallbox-charger.ini
```

## Meter, Charger, And Switch Disagree

Look at the authoritative outward paths first:

- `/Auto/StatusSource`
- `/Auto/FaultActive`
- `/Auto/FaultReason`
- `/Auto/BackendMode`
- `/Auto/MeterBackend`
- `/Auto/SwitchBackend`
- `/Auto/ChargerBackend`

Then inspect the live phase and contactor picture:

- `/Auto/PhaseObserved`
- `/Auto/PhaseMismatchActive`
- `/Auto/PhaseLockoutActive`
- `/Auto/ContactorLockoutActive`
- `/Auto/SwitchFeedbackClosed`
- `/Auto/SwitchInterlockOk`

## Scheduled / Plan Mode Does Not Boost Overnight

Check:

- `/Mode = 2`
- `/Auto/ScheduledEnabledDays`
- `/Auto/ScheduledFallbackDelaySeconds`
- `/Auto/ScheduledLatestEndTime`
- `/Auto/ScheduledNightCurrent`

Useful DBus paths:

- `/Auto/ScheduledState`
- `/Auto/ScheduledReason`
- `/Auto/ScheduledTargetDay`
- `/Auto/ScheduledTargetDate`
- `/Auto/ScheduledFallbackStart`
- `/Auto/ScheduledBoostUntil`

## Phase Switching Does Not Behave As Expected

Check the phase policy values:

- `/Auto/PhaseSwitching`
- `/Auto/PhaseUpshiftDelaySeconds`
- `/Auto/PhaseDownshiftDelaySeconds`
- `/Auto/PhaseUpshiftHeadroomWatts`
- `/Auto/PhaseDownshiftMarginWatts`

Then inspect:

- `/Auto/PhaseObserved`
- `/Auto/PhaseMismatchActive`
- `/Auto/PhaseLockoutActive`
- `/Auto/PhaseLockoutTarget`
- `/Auto/PhaseLockoutReason`
- `/Auto/PhaseSupportedConfigured`
- `/Auto/PhaseSupportedEffective`

## Runtime Overrides Feel Stale Or Unexpected

Check:

- `/Auto/RuntimeOverridesActive`
- `/Auto/RuntimeOverridesPath`

Restart the service after a larger tuning session:

```bash
svc -t /service/dbus-venus-evcharger
```

If the override path points to `/run/...`, a full GX reboot clears the runtime
layer and the service comes back on the base config.

## Fast Validation Commands

Validate the full wallbox configuration:

```bash
python3 -m venus_evcharger.backend.probe validate-wallbox deploy/venus/config.venus_evcharger.ini
```

Validate adapter files:

```bash
python3 -m venus_evcharger.backend.probe validate /data/etc/wallbox-meter.ini
python3 -m venus_evcharger.backend.probe validate /data/etc/wallbox-switch.ini
python3 -m venus_evcharger.backend.probe validate /data/etc/wallbox-charger.ini
```

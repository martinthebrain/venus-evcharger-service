# DBus Introspection Worker

The DBus introspection worker is an advisory background process for the Venus EV charger service. It is intentionally separate from the main service and from the Auto input helper so slow or broken DBus objects cannot block charging decisions or input snapshots.

## Purpose

Normal runtime reads use known Victron DBus paths and call `GetValue(timeout=...)` directly. They do not need DBus introspection. The worker exists for slow capability discovery:

- identify which DBus objects expose which interfaces and child nodes
- mark paths as `fresh`, `known-missing`, or `unresponsive-backoff`
- provide a fresh RAM mapping that other components can consult as optional ground truth
- avoid repeated probing of objects that are visible on DBus but do not answer

The mapping is advisory. A missing or stale mapping must never stop the charger service from running.

## Process Model

The main service supervises `venus_evcharger_dbus_introspection_worker.py` as a small separate process. It writes a volatile JSON snapshot, by default:

```text
/run/dbus-venus-evcharger-dbus-map-<DeviceInstance>.json
```

Other components can submit priority requests through:

```text
/run/dbus-venus-evcharger-dbus-map-requests-<DeviceInstance>.json
```

Both files live in RAM. After a reboot the worker rebuilds the mapping from scratch.

The worker is started from the normal update cycle after the charger topology is configured. It is deliberately not a runit service of its own: the EV charger service owns the worker lifetime, restart cooldown, command line, and parent-pid guard. If the parent exits, the worker exits as well. If the worker crashes, the next update cycles start it again after `DbusIntrospectionRestartSeconds`.

The worker process has three inputs:

- the EV charger config file
- the snapshot output path
- the priority-request path

It has one output: the advisory snapshot. No state is persisted to disk.

## Hotpath Rule

The worker must not be used synchronously from charging decisions. Runtime code may:

- read an already written snapshot
- skip a path that is freshly marked `known-missing` or `unresponsive-backoff`
- queue a priority request for later discovery

Runtime code must not:

- call `Introspect` directly in the Auto input hotpath
- wait for the worker to answer
- treat stale mapping data as authoritative

## Queue And Backoff

Discovery is trickled:

- one due job is processed per worker tick
- `DbusIntrospectionMinJobIntervalSeconds` spaces individual requests
- failed objects get exponential backoff between `DbusIntrospectionRetryBaseSeconds` and `DbusIntrospectionRetryMaxSeconds`
- priority requests run before routine background jobs

This prevents bursty scans over all DBus objects.

The worker keeps a small in-memory queue. Background discovery jobs are created from configured services and prefixes, for example:

- `AutoGridService`
- `AutoBatteryService`
- `AutoBatteryServicePrefix`
- `AutoPvService`
- `AutoPvServicePrefix`
- DC PV paths on `com.victronenergy.system`

Priority requests are appended by other components to the request file. They are consumed on the next tick, de-duplicated by service and path, and queued ahead of background work. A priority request still uses the same explicit timeout and still only updates the snapshot; the caller must continue without waiting.

## Resource And Physical Gates

The worker can delay discovery under system pressure:

- `DbusIntrospectionLoadAvgMax`
- `DbusIntrospectionMinMemAvailableKb`

It also has a UTC PV quiet window:

- `DbusIntrospectionPvQuietHours=22:00-05:00`

Routine AC PV introspection is postponed during that window. Explicit priority requests still run. This is useful for systems where inverter-like DBus services may be present but physically unavailable or not meaningful at night.

The quiet window is interpreted in UTC because the GX runtime and default config are kept UTC-oriented. The worker does not try to infer sunrise or inverter vendor state. It only avoids routine AC PV probing during the configured quiet hours. If a component has an immediate need, it can enqueue an explicit request and that request is allowed through.

## Snapshot Shape

The snapshot contains:

```json
{
  "schema_version": 1,
  "captured_at": 123.0,
  "heartbeat_at": 123.0,
  "worker_state": "running",
  "queue_depth": 0,
  "services": {
    "com.victronenergy.system": {
      "last_updated_at": 123.0,
      "paths": {
        "/Dc/Pv/Power": {
          "status": "fresh",
          "confidence": 1.0,
          "interfaces": ["com.victronenergy.BusItem"],
          "children": [],
          "last_success_at": 123.0,
          "retry_after": 21723.0
        }
      }
    }
  }
}
```

Status meanings:

- `fresh`: explicit Introspect succeeded recently
- `known-missing`: DBus reported a missing service/object/interface
- `unresponsive-backoff`: the object did not answer or failed in a retryable way

Consumers should also inspect `retry_after` and `confidence`:

- `retry_after` tells when the worker intends to probe the path again.
- `confidence=1.0` means a recent explicit result.
- lower confidence means the finding is still useful for throttling but should not be treated as a permanent capability statement.

The helper-side skip logic only skips paths that are freshly marked `known-missing` or `unresponsive-backoff`. A stale, missing, malformed, or absent snapshot is ignored.

## Priority Requests

Use `venus_evcharger.dbus_introspection.request_introspection()` to enqueue a request:

```python
request_introspection(
    "/run/dbus-venus-evcharger-dbus-map-requests-60.json",
    "com.victronenergy.system",
    "/Dc/Pv/Power",
    priority=150,
    reason="new configured path",
    source="auto-input-helper",
)
```

The worker consumes and clears accepted requests on the next tick. The caller should continue with the best currently available information.

Request priorities are numeric. Lower values run earlier. Suggested bands:

- `0..49`: operator/debug request
- `50..99`: configured service/path needed soon
- `100..199`: helper/runtime discovered uncertainty
- `200+`: routine background refresh

Requests are best-effort. A request file write failure should be logged by the caller, but must not fail the caller's normal work.

## Consumer Pattern

Use the helpers in `venus_evcharger.dbus_introspection` rather than parsing the JSON ad hoc:

```python
snapshot = load_introspection_snapshot(
    "/run/dbus-venus-evcharger-dbus-map-60.json",
    max_age_seconds=900,
)
skip, reason = path_unusable_until(
    snapshot,
    "com.victronenergy.pvinverter.http_48",
    "/Ac/Power",
)
if skip:
    # Avoid a known-bad read attempt for this cycle.
    ...
```

The normal pattern is:

1. Read the current snapshot without blocking.
2. If it contains a fresh negative finding, skip that path for this cycle.
3. If the component needs a capability answer, enqueue a priority request.
4. Continue with fallback behavior.

The EV charger service must remain correct if the worker is disabled.

## Configuration

The default configuration lives in `deploy/venus/config.venus_evcharger.default.ini`. Important keys:

```ini
DbusIntrospectionEnabled=1
DbusIntrospectionSnapshotPath=/run/dbus-venus-evcharger-dbus-map-60.json
DbusIntrospectionRequestPath=/run/dbus-venus-evcharger-dbus-map-requests-60.json
DbusIntrospectionFullScanIntervalSeconds=21600
DbusIntrospectionTickSeconds=5
DbusIntrospectionMinJobIntervalSeconds=2
DbusIntrospectionTimeoutSeconds=1
DbusIntrospectionRetryBaseSeconds=900
DbusIntrospectionRetryMaxSeconds=10800
DbusIntrospectionLoadAvgMax=3.0
DbusIntrospectionMinMemAvailableKb=32768
DbusIntrospectionPvQuietHours=22:00-05:00
```

`DbusIntrospectionMaxAgeSeconds` belongs to snapshot consumers. It controls how long they trust a heartbeat before ignoring the snapshot. The worker itself keeps writing heartbeats whenever it runs.

## Operational Checks

On a GX device, useful read-only checks are:

```sh
svc -s /service/dbus-venus-evcharger
ps | grep '[v]enus_evcharger_dbus_introspection_worker.py'
ls -l /run/dbus-venus-evcharger-dbus-map-*.json
cat /run/dbus-venus-evcharger-dbus-map-60.json
```

Expected behavior:

- the worker process exists while the main service runs
- the snapshot `heartbeat_at` changes over time
- `queue_depth` is usually small
- known broken services appear with `unresponsive-backoff`, not as repeated hotpath stalls

If the process is absent but the main service is healthy, check:

- `DbusIntrospectionEnabled`
- config parse errors
- restart cooldown warnings in the service log
- whether topology is still unconfigured

## Design Notes

All DBus proxies are created with `introspect=False`. The only introspection performed by this worker is an explicit call to:

```python
org.freedesktop.DBus.Introspectable.Introspect(timeout=...)
```

That keeps timeouts and error handling under our control.

This design intentionally favors isolation over completeness. DBus discovery is useful, but charging control depends on known configured paths and bounded `GetValue` calls. Introspection is allowed to be slow, stale, absent, or wrong without taking the charger service down.

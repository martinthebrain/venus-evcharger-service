# DBus Gateway Architecture

The Venus EV charger service treats the Victron DBus as a scarce and fragile
resource. Only one process may talk to it directly:

`venus_evcharger_dbus_adapter.py`

All other service components, helper processes, forensic tools, companion
publishers, and operational scripts must use the gateway interfaces. They must
not import `dbus`, create `SystemBus` or `SessionBus`, call `GetValue`,
`SetValue`, `Introspect`, or instantiate `VeDbusService`.

## Responsibilities

The adapter owns:

- Victron DBus read access
- Victron DBus write access
- DBus service registration for the EV charger service
- optional companion DBus service publication
- introspection and service discovery
- rate limiting
- circuit breaker state
- read cache ownership
- write command coalescing

The rest of the project reads gateway snapshots or submits gateway commands.

## Runtime Files

By default the gateway uses `/run/venus-evcharger`:

- `gateway.sock`: Unix socket for direct request/response IPC
- `dbus-cache.json`: atomic read cache snapshot
- `dbus-cache.seq`: monotonic cache sequence
- `dbus-health.json`: DBus health summary
- `dbus-health-history.jsonl`: compact rolling health timeline for field diagnosis
- `dbus-command-lifecycle.jsonl`: command lifecycle events such as queued,
  coalesced, applied, deferred, dropped, and expired
- `dbus-commands/`: command inbox for DBus writes, refreshes, and introspection
- `core-commands/`: command inbox from GUI DBus writes back to the core service

`DbusGatewayRunDir`, `DbusGatewayCachePath`, `DbusGatewayHealthPath`,
`DbusGatewaySocketPath`, `DbusGatewayCommandDir`, and
`DbusGatewayCoreCommandDir` can override these paths.

`DbusGatewayCachePublishIntervalSeconds=0` preserves the default behavior:
the adapter writes the RAM-backed cache and health files every tick. A positive
value throttles unchanged snapshot writes, while cache sequence changes still
flush immediately.

`DbusGatewayHealthLogPath`, `DbusGatewayHealthLogIntervalSeconds`, and
`DbusGatewayCommandLifecyclePath` control the JSONL diagnostics. These files are
operational evidence, not control surfaces: readers may tail or archive them,
but must not derive runtime decisions from them.

## Read Model

Consumers read `dbus-cache.json`. Every value is more than a scalar:

- `value`: last value seen by the adapter
- `source`: DBus service/path source
- `updated_at`: capture timestamp
- `age_s`: age at snapshot creation
- `status`: `fresh`, `stale`, or `error`
- `last_error`: last read error, if any
- `confidence`: advisory confidence

If a value is missing or too old, consumers request a refresh from the gateway.
They still do not read DBus directly.

## Write Model

Consumers submit commands, usually through `GatewayClient`.

Writes use `kind=set_value`, include the target service and path, and should
provide a `coalesce_key`. Latest commands for the same key win unless a higher
priority safety command is present.

The adapter may delay, drop optional, or coalesce commands depending on DBus
health. Safety/user commands keep priority, but still go through the scheduler.

Commands with a `coalesce_key` use a deterministic command filename derived
from that key. A newer command therefore atomically replaces the older desired
state on disk; stale values cannot reappear on the next adapter tick.

Temporary DBus failures do not delete command files. The adapter returns an
internal outcome for each command:

- `applied`: command completed and can be removed
- `dropped`: command was invalid or intentionally ignored
- `deferred`: command remains on disk for a later retry

## Discovery And Introspection

Discovery and introspection are gateway-owned. The legacy request/snapshot
contract is kept only as a compatibility surface; requesters enqueue gateway
commands and do not touch DBus.

Forensic code reads the gateway cache and logs the cache state. It must never
open DBus to investigate a DBus problem.

## Health Modes

The circuit breaker classifies DBus health:

- `ok`: all scheduled work is allowed
- `degraded`: optional discovery and diagnostics are reduced
- `protective`: only required reads and safety/user writes continue

The adapter starts conservatively. Reads, writes, and introspection each have a
separate global rate limiter. This prevents many internal modules from creating
accidental DBus bursts.

The adapter must never sleep inside its GLib tick. Rate limiting is implemented
as "due/not due", not as blocking waits. Each tick processes at most one real
DBus operation. Socket handling and snapshot writing may still happen every
tick because they do not touch Victron DBus.

Fast read groups must not perform service discovery. For example, PV prefix
polling may only consume the cached service list. `ListNames()` and
introspection belong to slow discovery work or explicit requests.

## Release Gate

Gateway changes should be exercised on the Raspberry Pi test target before they
are considered ready for a real GX device:

```sh
python3 scripts/dev/pi_gateway_release_gate.py \
  --pi root@192.168.142.129 \
  --deploy \
  --configure-host \
  --start-host-shelly \
  --restart
```

The gate deploys the current workspace to the Pi test directory, starts a
host-side Shelly simulator, configures the Pi service to use that simulated
endpoint, runs offline gateway chaos scenarios on the Pi, restarts the Pi
services, and then checks:

- remote syntax and DBus isolation
- bounded behavior during simulated DBus hangs, GUI publish bursts, queue
  overproduction, adapter restart during queued work, and tick pressure
- exactly one service, adapter, and observer instance
- gateway health state, queue age, cache freshness, and event-loop gap
- recent `dbus-health-history.jsonl`
- GUI-visible EVCS values such as power, current, session energy, and time
- GUI `/Mode` writes flowing through the gateway back to the core
- recent `dbus-command-lifecycle.jsonl`

The Pi gate is a pre-release integration check. It does not replace final
observation on the target GX, but it should catch gateway, queue, GUI-write, and
Shelly-integration regressions before customer hardware is touched.

Use `--skip-chaos` only when the offline chaos scenarios were already run in the
same workspace and the current task is strictly about live Pi wiring.

## Development Rule

When adding new code, search before merging:

```sh
rg -n "^import dbus|from dbus|from vedbus|SystemBus\\(|SessionBus\\(|GetValue\\(|SetValue\\(|Introspect\\(|VeDbusService" venus_evcharger venus_evcharger_*.py scripts -g '*.py'
```

Only the gateway entrypoint and its adapter component modules may contain real
DBus access. The allow-list is enforced by
`scripts/dev/check_dbus_isolation.py`.

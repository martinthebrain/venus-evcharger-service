# DBus Gateway Architecture

The Venus EV charger service treats the Victron DBus as a scarce and fragile
resource. Only one process may talk to it directly:

`venus_evcharger_dbus_adapter.py`, assembled from the exclusive
`venus_evcharger/dbus_adapter/` package.

All other service components, helper processes, forensic tools, companion
publishers, and operational scripts must use the gateway interfaces. They must
not import `dbus`, create `SystemBus` or `SessionBus`, call `GetValue`,
`SetValue`, `ListNames`, `Introspect`, instantiate `VeDbusService`, or execute
DBus command-line clients. Developer testbeds and release gates follow the same
rule; integration tests request reads and writes through gateway IPC.

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

## Implementation Layout

The adapter package is grouped by responsibility:

- `process/`: process assembly, GLib lifecycle, socket IPC, identity,
  introspection, and the process-only protocols
- `read/`: typed read specifications, targets, PV source selection,
  aggregation, and execution
- `write/`: command scheduling, local publication, remote writes, lifecycle,
  and write-side protocols
- `health/`: freshness, queue, backpressure, history, GUI, and SLO metrics
- `rate.py`: DBus connection ownership, rate limiting, and circuit breaking
- `resources.py`: CPU, memory, and event-loop timing observations
- `scheduling.py`: read and discovery due-time scheduling
- `contracts.py`: shared adapter boundary contracts
- `jsonl.py`: bounded RAM-backed operational history

There are deliberately no top-level compatibility modules for the former
fragmented layout. Imports must use these canonical package paths so that
architecture checks can detect renewed top-level fragmentation.

The gateway owns DBus transport; it does not own the semantic Venus EV charger
surface. GUI/VRM-visible path requirements live in
[VENUS_DBUS_SURFACE.md](VENUS_DBUS_SURFACE.md) and
`venus_evcharger/dbus_gateway_surface.py`, exported through the gateway facade.

## Runtime Files

By default the gateway uses `/run/venus-evcharger`:

- `gateway.sock`: Unix socket for direct request/response IPC
- `dbus-cache.json`: atomic read cache snapshot
- `dbus-cache.seq`: monotonic cache sequence
- `dbus-health.json`: DBus health summary
- `gateway-diagnostics.json`: strict semantic discovery, pending-work, and
  source-health snapshot for operational consumers
- `dbus-health-history.jsonl`: compact rolling health timeline for field diagnosis
- `dbus-command-lifecycle.jsonl`: command lifecycle events such as queued,
  coalesced, applied, deferred, dropped, and expired
- `dbus-commands/`: adapter-owned scheduler inbox for DBus writes, refreshes,
  and introspection
- `core-commands/`: transport-neutral control mailbox from control-surface
  adapters to the core service

The two directories intentionally use the same atomic JSON-file transport but
have different semantic policies. The reusable transport, command types, and
core-control envelope live under `venus_evcharger/ipc/`. The DBus adapter is a
producer for `core-commands/`; the core runtime is its consumer. Neither side
owns a second implementation of queueing or coalescing, and core modules must
not import the DBus command scheduler policy.

`gateway-diagnostics.json` is the only public diagnostics projection of
adapter-owned discovery and inspection state. Consumers read it through
`GatewayDiagnosticsFileReader` and the `GatewayDiagnosticsSnapshot` DTO. They
must not read the adapter's raw introspection map or depend on DBus service
names, paths, inspection XML, service counts, or path counts.

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
- `changed_at`: timestamp of the last semantic value change
- `confirmed_at`: timestamp of the last successful read or ownership confirmation
- `updated_at`: compatibility alias for `confirmed_at`
- `age_s`: confirmation age at snapshot creation
- `change_age_s`: age of the semantic value itself
- `freshness_kind`: `external_read`, `local_owned`, `static`, or `diagnostic`
- `stale_after_s`: per-value read TTL where applicable
- `status`: `fresh`, `stale`, `error`, or `unavailable`
- `source_state`: `active`, `unavailable`, or `error`
- `last_error`: last read error, if any
- `confidence`: advisory confidence
- `next_probe_at`: next scheduled probe after a non-fatal source outage

If a value is missing or too old, consumers request a refresh from the gateway.
They still do not read DBus directly.

Freshness is deliberately class-specific. External measurements become stale
after the TTL derived from their read specification. Adapter-owned values stay
fresh while the EV charger DBus service is registered, static identity values
do not age merely because they remain unchanged, and introspection diagnostics
are not treated as measurements. An owned value becomes `unavailable` when its
service registration is absent.

`dbus-health.json` separates `external_read_status_counts`,
`local_publish_status_counts`, `static_status_counts`, and
`diagnostic_status_counts`. The compatibility field `status_counts` describes
only the three critical semantic reads. `critical_stale_count` therefore cannot
be inflated by unchanged GUI/configuration paths, while
`optional_source_unavailable_count` reports temporarily unavailable AC/DC PV
candidates separately from `optional_source_error_count`. A sleeping inverter
therefore remains visible with its last `NoReply`, `error_at`, and
`next_probe_at`, but it is not reported as an active DBus fault and does not
make a successfully aggregated `pv_power_w` value stale. The first successful
probe returns the source to `active` immediately.

Standard Auto inputs do not use raw DBus service/path keys. PV power, grid
power, and battery SOC are gateway-owned semantic read keys:

- `pv_power_w`
- `grid_power_w`
- `battery_soc`

Core and helper code must request these through `GatewayClient.request_read_key`
or the strict `gateway_read_value` helpers. This mirrors the publish-side
semantic field contract: backend policy consumes domain values and does not
know whether the gateway obtained them from AC PV services, DC PV paths,
three grid phases, or another future transport.

Explicit service/path reads are still possible for deliberately raw detail
surfaces such as relay readback or configured energy-source diagnostics, but
they must use the visibly named `GatewayClient.request_raw_value`. Raw reads are
not a standard Auto input path, and architecture checks reject the old
`request_read(service, path)` shape.

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

- remote syntax (using built-in `compile`, because Venus OS may omit
  `py_compile` and `compileall`) and DBus isolation
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

Only the gateway entrypoint and modules below `venus_evcharger/dbus_adapter/`
may contain real DBus access. This package boundary is enforced by
`scripts/dev/check_dbus_isolation.py`.

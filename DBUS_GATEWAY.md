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
All gateway participants derive these values through the single typed
`venus_evcharger.ipc.gateway_path_config.configured_gateway_paths` boundary.
The adapter, core, auto-input helper, and generic-Shelly helper therefore use
the same complete `GatewayPaths` contract; no process reconstructs filenames
or applies only a subset of the overrides. Configured paths must be absolute.
Without a runtime override, configured individual paths are authoritative. An
explicit adapter `--run-dir` argument has higher precedence and relocates the
entire derived `GatewayPaths` set together; configured individual paths are
then intentionally ignored so an isolated runtime cannot cross into another
gateway's socket, cache, health, or command queues.

## Publication IPC Arbitration

Live, diagnostic semantic publications may use the low-latency gateway socket.
Registration, safety, user-critical, and other durable commands remain in the
atomic file inbox. Both lanes are governed by one arbitration contract:

- Every coalesced publication carries a process-wide monotonic
  `transport_order` and one `transport_field_orders` entry per field. Separate
  `GatewayClient` instances share the issuer sequence. Tests may inject an
  isolated sequence without resetting production-global state. A retry
  preserves its original order.
- The gateway retains a bounded high-water history for accepted key/field
  pairs. It writes this small restart guard atomically below the configured
  runtime directory, normally `/run`, so it does not add flash wear.
  Inactive marks expire after 60 seconds. A transient file fallback expires
  after 30 seconds, so its protective high-water mark always outlives the
  fallback it guards.
- If the bounded history is full, a new socket key is rejected and follows the
  durable fallback path. Safety and other durable commands are never rejected
  because of this RAM guard.
- Duplicate or older fields are idempotently superseded regardless of which
  lane delivered them. Independent fields from Fast and Durable commands are
  merged without losing either update.
- A newer socket publication prevents an older file fallback from being
  revived after an acknowledgement is lost. A newer durable command likewise
  removes an older queued socket publication.
- Ready safety and user-critical durable commands reserve an execution
  opportunity and may overtake continuous socket traffic. A delayed retry does
  not block unrelated ready socket work.
- Fast fields have individual TTLs. A deferred key is requeued behind ready
  work with a bounded retry delay, preventing an unavailable companion from
  monopolizing a tick.
- Transient `live` and `diagnostic` publications have at most 30 seconds of
  lifetime. The fast lane rejects invalid or implausibly future creation
  timestamps, and the durable scheduler checks an explicitly supplied command
  deadline again before dispatch.
- Coalesce keys, field names, field counts, payload bytes, and in-memory queue
  depth are bounded before admission.

This ordering makes socket acknowledgement loss harmless while preserving the
durability and priority guarantees of the file inbox.

Publication is split by consumer cost:

- `DbusGatewayEnergyPublishIntervalSeconds=1` keeps the compact binary energy
  snapshot fresh for control loops.
- `DbusGatewayHealthPublishIntervalSeconds=1` keeps backpressure and heartbeat
  observations fresh.
- `DbusGatewayFullCachePublishIntervalSeconds=10` is the heartbeat for the
  substantially larger raw diagnostic cache.
- `DbusGatewayFullCacheDirtyIntervalSeconds=2` permits an earlier full-cache
  update after a sequence change without turning every tick into JSON work.

The dedicated topology file is written initially and when its discovery
generation changes. Its reader uses the small, current health snapshot as the
freshness heartbeat, so an unchanged inventory does not need to be rewritten.
The full-cache heartbeat also carries the latest topology for operational
diagnostics, but domain consumers do not use that raw cache as their topology
API. Positive publication intervals have a 0.2 second minimum.

The adapter creates one coherent control/health snapshot per work tick. The
command mailbox is locked, decoded, and coalesced once into an immutable
tick-local view shared by scheduling, health, and pruning; it is never cached
across ticks. Queue directories, cache freshness, SLO observations, event-loop
metrics, and backpressure are evaluated once and shared by adaptive regulation
and file publication. `DbusGatewayResourceSampleIntervalSeconds=2` separately
limits procfs reads. Resource pressure escalates immediately, while distinct
recovery thresholds must remain satisfied for
`DbusGatewayResourceRecoveryHoldSeconds=10` before the state is lowered. This
prevents tick-rate oscillation near the 64 MiB available-memory boundary.

`DbusGatewayHealthLogPath`, `DbusGatewayHealthLogIntervalSeconds`, and
`DbusGatewayCommandLifecyclePath` control the JSONL diagnostics. These files are
operational evidence, not control surfaces: readers may tail or archive them,
but must not derive runtime decisions from them.

## Read Model

The frequent semantic energy consumer reads `energy-inputs.v1.bin` through the
typed `EnergyInputsSnapshot` contract. The small dependency-free binary format
avoids parsing the complete raw cache and may fall back to the typed
`energy_inputs` member in `dbus-cache.json` during an atomic rolling update.
`energy-topology.json` carries the much less frequently consumed topology; its
freshness is validated against `dbus-health.json`.

The binary reader accepts at most 65,536 bytes. It reads one guard byte beyond
that limit and rejects an oversized file before decoding it, so a malformed
runtime file cannot cause an unbounded allocation. Every measurement carries
its own `observed_at`; the snapshot contract rejects a measurement timestamp
later than `captured_at + 1 second`. Atomic replacement prevents consumers from
seeing a partially written snapshot.

Generic and diagnostic consumers read `dbus-cache.json`. Every value is more
than a scalar:

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

Standard Auto inputs do not use raw DBus service/path keys. The gateway
publishes one typed `EnergyInputsSnapshot` with these semantic fields:

- `pv_power_w`
- `grid_power_w`
- `battery_soc`

Core and helper code load this DTO through `GatewayClient.load_energy_inputs`.
Missing, stale, or unavailable values cause a typed `EnergyRefreshRequest`
through `GatewayClient.request_energy_refresh`; the request names a semantic
scope such as `pv`, `grid`, `battery`, `topology`, or `all`, never a DBus
service or path. Backend policy therefore does not know whether the gateway
obtained PV from AC inverter services, a DC charger path, or a future
transport.

There is no public raw service/path read API. GX relay readback is exposed as a
semantic `GatewayOperationsPort` operation, and discovery health is exposed as
the validated `GatewayDiagnosticsSnapshot`. The full cache remains an
operational diagnostic artifact, not a domain-model dependency.

## Write Model

Domain code publishes EVCS and companion values through
`GatewayPublicationPort`. It submits GX relay and ESS setpoint intents through
`GatewayOperationsPort`. These ports carry semantic fields and validated DTOs;
only adapter-owned executors translate them to DBus services and paths.

The adapter may delay, drop optional, or coalesce commands depending on DBus
health. Safety/user commands keep priority, but still go through the scheduler.
Registration and safety/user-critical work uses the durable file lane. Only
validated `live` and `diagnostic` field publications may use the bounded fast
lane.

Commands with a `coalesce_key` use a deterministic command filename derived
from that key. A newer command therefore atomically replaces the older desired
state on disk; stale values cannot reappear on the next adapter tick. A bounded
advisory `flock` serializes read/compare/replace across producer processes.
Kernel process teardown releases abandoned locks automatically, and every
replacement gets a new mailbox revision so a consumer holding an old snapshot
cannot delete newer work.

The scheduler does not let continuous fast publications starve durable
safety/user work: a ready urgent durable command reserves the next execution
opportunity. Fast work whose retry time has not arrived is skipped, and a
deferred fast item receives a bounded retry delay so unrelated ready items can
run. Field TTLs are independent, so expiry of one diagnostic field does not
discard another field in the same coalesced publication.

Durable commands are non-expiring unless their semantic producer supplies a
deadline. An explicit deadline is checked immediately before dispatch.
Transient publication deadlines are normalized to a finite positive maximum of
30 seconds and are converted to monotonic per-field leases on admission.

Temporary DBus failures do not delete command files. The adapter returns an
internal outcome for each command:

- `applied`: command completed and can be removed
- `dropped`: command was invalid or intentionally ignored
- `deferred`: command remains on disk for a later retry

## Discovery And Introspection

Discovery and introspection are gateway-owned. Energy consumers request only a
semantic topology refresh. The adapter schedules concrete discovery and
introspection targets internally and publishes validated discovery health
through `gateway-diagnostics.json`.

An adapter-local raw introspection snapshot may be retained as operational
evidence, but it is not a public request/response contract. Domain, forensic,
and control code must not parse it or depend on DBus service names, paths, or
XML.

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

The automated boundary consists of two complementary checks:

```sh
python3 scripts/dev/check_dbus_isolation.py
python3 scripts/dev/check_architecture_contracts.py
```

Only the gateway entrypoint and modules below `venus_evcharger/dbus_adapter/`
may contain real DBus access. `check_dbus_isolation.py` rejects imports, API
calls, symbols, and command-line DBus clients elsewhere.

`check_architecture_contracts.py` enforces the next layer: backend/update/read
consumers may use only semantic gateway ports and DTOs, concrete Victron
service/path details remain gateway-owned, the Venus surface is imported only
through the gateway facade, and the bounded IPC, timestamp, deadline,
mailbox-revision, fairness, and field-ordering contracts must remain present.

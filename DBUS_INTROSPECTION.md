# DBus Introspection via Gateway

The EV charger no longer starts a separate DBus introspection worker process.
All Victron DBus access, including `Introspect`, is owned by the
`venus_evcharger_dbus_adapter.py` process. Its implementation lives under
`venus_evcharger/dbus_adapter/process/`; snapshot generation and process
contracts are in `process/introspection_snapshot.py` and
`process/protocols/introspection.py`.

The gateway may keep an adapter-private advisory map:

- `DbusIntrospectionSnapshotPath`
- `DbusIntrospectionMaxAgeSeconds`

No backend, core, helper, forensic, or publishing consumer reads this map. The
former `venus_evcharger.dbus_introspection` API is retired. Raw services, paths,
inspection XML, worker state, and service/path counts remain private adapter
implementation details.

Refresh requests use the gateway command mailbox and the typed semantic energy
refresh contract. The adapter alone selects raw introspection targets,
rate-limits the DBus operation, and writes diagnostic findings into
`DbusIntrospectionSnapshotPath`.

Operational consumers use `GatewayDiagnosticsFileReader` and the strict
`GatewayDiagnosticsSnapshot` DTO instead. Its discovery summary exposes only
semantic state, pending work, discovered source count, and unusable source
count. Value-source health is represented by typed diagnostic sample status,
timestamps, confidence, and reason codes. The public document contains no DBus
service identity, path, or introspection payload.

This keeps diagnostics available while removing both the old process and its
second command route:

- no extra Python worker RSS
- no second lifecycle to supervise
- no second component capable of DBus discovery
- no direct DBus access outside the gateway
- no raw introspection contract outside the adapter

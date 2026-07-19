# DBus Introspection via Gateway

The EV charger no longer starts a separate DBus introspection worker process.
All Victron DBus access, including `Introspect`, is owned by the
`venus_evcharger_dbus_adapter.py` process. Its implementation lives under
`venus_evcharger/dbus_adapter/process/`; snapshot generation and process
contracts are in `process/introspection_snapshot.py` and
`process/protocols/introspection.py`.

The gateway keeps the legacy advisory map file compatible for consumers:

- `DbusIntrospectionSnapshotPath`
- `DbusIntrospectionRequestPath`
- `DbusIntrospectionMaxAgeSeconds`

Consumers may still read the snapshot with `venus_evcharger.dbus_introspection`
helpers. They must treat it as optional advice and must never block waiting for
fresh introspection data.

Requests are written to `DbusIntrospectionRequestPath` in the historical
`{"requests": [...]}` shape. The gateway reads and clears that file, converts
each request into a coalesced gateway `introspect` command, rate-limits the real
DBus operation, and writes findings back into `DbusIntrospectionSnapshotPath`.

This preserves the old data contract while removing the old process:

- no extra Python worker RSS
- no second lifecycle to supervise
- no second component capable of DBus discovery
- no direct DBus access outside the gateway

The snapshot `worker_state` is now `gateway`.

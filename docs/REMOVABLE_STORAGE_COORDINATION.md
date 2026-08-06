# Removable Storage Coordination

Filesystem maintenance is outside the EV charger service boundary. An
independent system service owns device health checks, unmounting, filesystem
checks, repairs, and remounting.

## Lock Contract

Cooperating processes use this advisory lock:

```text
/run/lock/removable-storage-maintenance.lock
```

- A process writing to removable storage holds a shared `flock` lease for the
  complete write operation.
- The maintenance service holds an exclusive `flock` lease before unmounting
  or modifying a filesystem and releases it only after storage is stable
  again.
- The maintenance service must still verify that no non-cooperating process
  uses the mount before unmounting it.
- The lock is the authoritative synchronization signal. Kernel lock release on
  process exit prevents stale maintenance ownership.

The path can be overridden consistently for tests or platform integration with
`VENUS_REMOVABLE_STORAGE_MAINTENANCE_LOCK_PATH`.

## EV Charger Behavior

- The updater waits for a shared lease for at most 300 seconds by default. It
  then avoids the SD workspace and uses its normal fallback storage. The wait
  can be changed with
  `VENUS_EVCHARGER_UPDATER_STORAGE_MAINTENANCE_WAIT_SECONDS`.
- The forensic observer acquires a non-blocking shared lease only when an
  incident is ready to be written. If maintenance is active, it defers the
  incident and retries on a later observer cycle.
- The EV charger never invokes `fsck`, unmounts storage, or repairs a
  filesystem.


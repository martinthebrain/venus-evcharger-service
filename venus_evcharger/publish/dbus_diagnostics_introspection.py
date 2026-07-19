# SPDX-License-Identifier: GPL-3.0-or-later
"""Gateway-introspection diagnostic values."""

from __future__ import annotations

from venus_evcharger.core.contracts import non_negative_int
from venus_evcharger.dbus_introspection import IntrospectionPayload, load_owner_introspection_snapshot
from venus_evcharger.publish.dbus_diagnostics_contracts import DiagnosticValue
from venus_evcharger.publish.dbus_shared import DbusPublishContext, PublishServicePort, is_object_mapping


_INTROSPECTION_MISSING_STATE = "missing"
_INTROSPECTION_SERVICES_FIELD = "services"
_INTROSPECTION_QUEUE_DEPTH_FIELD = "queue_depth"
_INTROSPECTION_WORKER_STATE_FIELD = "worker_state"
_INTROSPECTION_PATHS_FIELD = "paths"
_UNUSABLE_INTROSPECTION_STATUSES = frozenset({"known-missing", "unresponsive-backoff"})


class DbusDiagnosticsIntrospection:
    """Build diagnostics from the gateway-owned introspection snapshot."""

    def __init__(self, context: DbusPublishContext) -> None:
        self.context = context
        self.service: PublishServicePort = context.service

    def snapshot(self, now: float) -> IntrospectionPayload:
        """Return the owner-introspection snapshot when it has mapping shape."""
        return load_owner_introspection_snapshot(self.service, now=now)

    def counter_values(self, now: float) -> dict[str, DiagnosticValue]:
        """Return compact diagnostics for the advisory DBus introspection map."""
        snapshot = self.snapshot(now)
        services = snapshot.get(_INTROSPECTION_SERVICES_FIELD)
        return {
            "auto_dbus_introspection_state": self._dbus_introspection_state(snapshot),
            "auto_dbus_introspection_queue_depth": self._dbus_introspection_queue_depth(snapshot),
            "auto_dbus_introspection_service_count": self._dbus_introspection_service_count(services),
            "auto_dbus_introspection_unusable_path_count": self._dbus_introspection_unusable_count(services),
        }

    @staticmethod
    def _dbus_introspection_state(snapshot: object) -> str:
        """Return the gateway introspection state for diagnostics."""
        if not is_object_mapping(snapshot):
            return _INTROSPECTION_MISSING_STATE
        return str(snapshot.get(_INTROSPECTION_WORKER_STATE_FIELD) or _INTROSPECTION_MISSING_STATE)

    @staticmethod
    def _dbus_introspection_queue_depth(snapshot: object) -> int:
        """Return the pending introspection request count."""
        queue_depth = snapshot.get(_INTROSPECTION_QUEUE_DEPTH_FIELD) if is_object_mapping(snapshot) else None
        return int(non_negative_int(queue_depth))

    @staticmethod
    def _dbus_introspection_service_count(services: object) -> int:
        """Return the number of services in an introspection snapshot."""
        return len(services) if is_object_mapping(services) else 0

    def _dbus_introspection_unusable_count(self, services: object) -> int:
        """Count introspection findings that say a path should not be queried now."""
        if not is_object_mapping(services):
            return 0
        return sum(self._dbus_introspection_unusable_paths(service_payload) for service_payload in services.values())

    def _dbus_introspection_unusable_paths(self, service_payload: object) -> int:
        """Count unusable path findings in one introspection service payload."""
        paths = service_payload.get(_INTROSPECTION_PATHS_FIELD) if is_object_mapping(service_payload) else None
        if not is_object_mapping(paths):
            return 0
        return sum(1 for finding in paths.values() if _introspection_finding_unusable(finding))

    def snapshot_age(self, now: float) -> float:
        """Return age of the latest introspection snapshot heartbeat."""
        snapshot = self.snapshot(now)
        return self.context.age(snapshot.get("heartbeat_at"), now)


def _introspection_finding_unusable(finding: object) -> bool:
    """Return whether one introspection path finding is currently unusable."""
    if not is_object_mapping(finding):
        return False
    status = finding.get("status")
    if status is None:
        return False
    return str(status) in _UNUSABLE_INTROSPECTION_STATUSES

# SPDX-License-Identifier: GPL-3.0-or-later
"""Gateway-introspection diagnostic values."""

from __future__ import annotations

from collections.abc import Mapping

from venus_evcharger.dbus_introspection import IntrospectionPayload, load_owner_introspection_snapshot
from venus_evcharger.publish.dbus_diagnostics_contracts import DiagnosticValue
from venus_evcharger.publish.dbus_diagnostics_phase import _DbusDiagnosticsPhase


class _DbusDiagnosticsIntrospection(_DbusDiagnosticsPhase):
    def _dbus_introspection_snapshot(self, now: float) -> IntrospectionPayload:
        """Return the owner-introspection snapshot when it has mapping shape."""
        return load_owner_introspection_snapshot(self.service, now=now)

    def _dbus_introspection_counter_values(self, now: float) -> dict[str, DiagnosticValue]:
        """Return compact diagnostics for the advisory DBus introspection map."""
        snapshot = self._dbus_introspection_snapshot(now)
        services = snapshot.get("services", {})
        return {
            "auto_dbus_introspection_state": self._dbus_introspection_state(snapshot),
            "auto_dbus_introspection_queue_depth": self._dbus_introspection_queue_depth(snapshot),
            "auto_dbus_introspection_service_count": self._dbus_introspection_service_count(services),
            "auto_dbus_introspection_unusable_path_count": self._dbus_introspection_unusable_count(services),
        }

    @staticmethod
    def _dbus_introspection_state(snapshot: object) -> str:
        """Return the gateway introspection state for diagnostics."""
        return str(snapshot.get("worker_state", "missing")) if isinstance(snapshot, Mapping) else "missing"

    @staticmethod
    def _dbus_introspection_queue_depth(snapshot: object) -> int:
        """Return the pending introspection request count."""
        return int(snapshot.get("queue_depth", 0) or 0) if isinstance(snapshot, Mapping) else 0

    @staticmethod
    def _dbus_introspection_service_count(services: object) -> int:
        """Return the number of services in an introspection snapshot."""
        return len(services) if isinstance(services, Mapping) else 0

    def _dbus_introspection_unusable_count(self, services: object) -> int:
        """Count introspection findings that say a path should not be queried now."""
        if not isinstance(services, Mapping):
            return 0
        return sum(self._dbus_introspection_unusable_paths(service_payload) for service_payload in services.values())

    def _dbus_introspection_unusable_paths(self, service_payload: object) -> int:
        """Count unusable path findings in one introspection service payload."""
        paths = service_payload.get("paths", {}) if isinstance(service_payload, Mapping) else {}
        if not isinstance(paths, Mapping):
            return 0
        return sum(1 for finding in paths.values() if _introspection_finding_unusable(finding))

    def _dbus_introspection_snapshot_age(self, now: float) -> float:
        """Return age of the latest introspection snapshot heartbeat."""
        return self._age_seconds(self._dbus_introspection_snapshot(now).get("heartbeat_at"), now)


def _introspection_finding_unusable(finding: object) -> bool:
    """Return whether one introspection path finding is currently unusable."""
    if not isinstance(finding, Mapping):
        return False
    return str(finding.get("status", "") or "") in ("known-missing", "unresponsive-backoff")

# SPDX-License-Identifier: GPL-3.0-or-later
"""Gateway-introspection diagnostic values."""

from __future__ import annotations

from typing import Any

from venus_evcharger.dbus_introspection import load_owner_introspection_snapshot
from venus_evcharger.publish.dbus_diagnostics_contracts import DiagnosticValue, _DbusDiagnosticsContractsMixin


class _DbusDiagnosticsIntrospectionMixin(_DbusDiagnosticsContractsMixin):
    def _dbus_introspection_snapshot(self, now: float) -> dict[str, Any]:
        """Return the owner-introspection snapshot when it has mapping shape."""
        snapshot = load_owner_introspection_snapshot(self.service, now=now)
        return snapshot if isinstance(snapshot, dict) else {}

    def _dbus_introspection_counter_values(self, now: float) -> dict[str, DiagnosticValue]:
        """Return compact diagnostics for the advisory DBus introspection map."""
        snapshot = self._dbus_introspection_snapshot(now)
        services = snapshot.get("services", {})
        return {
            "/Auto/DbusIntrospectionState": self._dbus_introspection_state(snapshot),
            "/Auto/DbusIntrospectionQueueDepth": self._dbus_introspection_queue_depth(snapshot),
            "/Auto/DbusIntrospectionServiceCount": self._dbus_introspection_service_count(services),
            "/Auto/DbusIntrospectionUnusablePathCount": self._dbus_introspection_unusable_count(services),
        }

    @staticmethod
    def _dbus_introspection_state(snapshot: object) -> str:
        """Return the gateway introspection state for diagnostics."""
        return str(snapshot.get("worker_state", "missing")) if isinstance(snapshot, dict) else "missing"

    @staticmethod
    def _dbus_introspection_queue_depth(snapshot: object) -> int:
        """Return the pending introspection request count."""
        return int(snapshot.get("queue_depth", 0) or 0) if isinstance(snapshot, dict) else 0

    @staticmethod
    def _dbus_introspection_service_count(services: object) -> int:
        """Return the number of services in an introspection snapshot."""
        return len(services) if isinstance(services, dict) else 0

    def _dbus_introspection_unusable_count(self, services: object) -> int:
        """Count introspection findings that say a path should not be queried now."""
        if not isinstance(services, dict):
            return 0
        return sum(self._dbus_introspection_unusable_paths(service_payload) for service_payload in services.values())

    def _dbus_introspection_unusable_paths(self, service_payload: object) -> int:
        """Count unusable path findings in one introspection service payload."""
        paths = service_payload.get("paths", {}) if isinstance(service_payload, dict) else {}
        if not isinstance(paths, dict):
            return 0
        return sum(1 for finding in paths.values() if self._dbus_introspection_finding_unusable(finding))

    @staticmethod
    def _dbus_introspection_finding_unusable(finding: object) -> bool:
        """Return whether one introspection path finding is currently unusable."""
        if not isinstance(finding, dict):
            return False
        return str(finding.get("status", "") or "") in ("known-missing", "unresponsive-backoff")

    def _dbus_introspection_snapshot_age(self, now: float) -> float:
        """Return age of the latest introspection snapshot heartbeat."""
        return self._age_seconds(self._dbus_introspection_snapshot(now).get("heartbeat_at"), now)

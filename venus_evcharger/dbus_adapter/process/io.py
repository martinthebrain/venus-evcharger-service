#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Exclusive low-level DBus IO for the adapter process.

This module is part of the dedicated DBus gateway. Direct Victron DBus access
is intentionally isolated to the gateway adapter modules only.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable
from typing import TypeGuard, TypeVar

from venus_evcharger.dbus_adapter.async_broker import DbusMethodCall, dbus_call_operation
from venus_evcharger.dbus_adapter.contracts import CommandOutcome
from venus_evcharger.dbus_adapter.process.health import GatewayControlSnapshot
from venus_evcharger.dbus_adapter.process.protocols.io import DbusAdapterIoContext
from venus_evcharger.dbus_adapter.rate import DBUS_GATEWAY_OPERATION_ERRORS, DbusOperationDeferred
from venus_evcharger.dbus_adapter.read.semantic import energy_inputs_snapshot
from venus_evcharger.ipc.energy import EnergyTopologySnapshot

_T = TypeVar("_T")
_DBUS_DISCOVERY_TIMEOUT_SECONDS = 1.0


class DbusAdapterIo:
    def __init__(self, context: DbusAdapterIoContext) -> None:
        self._context = context

    def poll_one_due_read_once(self) -> bool:
        context = self._context
        monotonic_at = time.monotonic()
        due = context.read_scheduler.next_due(
            monotonic_at=monotonic_at,
            circuit_state=context.circuit.state(),
            priority_allowed=context.circuit.allows_priority,
        )
        if due is None:
            return False
        key, spec, interval = due
        def _complete(outcome: CommandOutcome) -> None:
            completed_at = time.monotonic()
            if outcome == "applied":
                context.read_scheduler.record_success(
                    key,
                    monotonic_at=completed_at,
                    interval=interval,
                    interval_factor=context.read_executor.consume_interval_factor(
                        key
                    ),
                )
            elif outcome == "dropped":
                context.read_scheduler.record_error(
                    key,
                    monotonic_at=completed_at,
                    interval=interval,
                )

        outcome = context.read_executor.poll_read_spec(
            key,
            spec,
            completion=_complete,
        )
        return outcome != "deferred" or bool(context.read_executor.last_operation_performed)

    def maybe_refresh_services(self) -> None:
        self.refresh_services_if_due_once()

    def refresh_services_if_due_once(self) -> bool:
        context = self._context
        monotonic_at = time.monotonic()
        if not context.discovery.due(
            monotonic_at=monotonic_at,
            priority_allowed=context.circuit.allows_priority,
        ):
            return False
        try:
            context.operation_broker.submit(
                dbus_call_operation(
                    context.connection,
                    DbusMethodCall(
                        service="org.freedesktop.DBus",
                        path="/org/freedesktop/DBus",
                        interface="org.freedesktop.DBus",
                        method_name="ListNames",
                        signature="",
                        rate_kind="read",
                        metric_kind="discovery",
                        source="org.freedesktop.DBus/ListNames",
                        priority="discovery",
                        timeout_seconds=_DBUS_DISCOVERY_TIMEOUT_SECONDS,
                    ),
                    on_success=self._complete_service_discovery,
                    on_error=self._fail_service_discovery,
                )
            )
            return True
        except DbusOperationDeferred:
            return False
        except DBUS_GATEWAY_OPERATION_ERRORS as error:
            self._fail_service_discovery(error)
            return True

    def _complete_service_discovery(self, value: object) -> None:
        context = self._context
        captured_at = time.time()
        monotonic_at = time.monotonic()
        try:
            services = _service_names(value)
        except (TypeError, ValueError) as error:
            context.discovery.record_error(
                error,
                monotonic_at=monotonic_at,
                captured_at=captured_at,
            )
            return
        context.cache.update_services(services, now=captured_at)
        context.energy_discovery.update_services(
            services,
            captured_at=captured_at,
        )
        context.discovery.record_success(
            monotonic_at=monotonic_at,
            captured_at=captured_at,
            needs_early_rescan=context.energy_discovery.needs_early_pv_rescan(),
        )

    def _fail_service_discovery(self, error: BaseException) -> None:
        self._context.discovery.record_error(
            error,
            monotonic_at=time.monotonic(),
            captured_at=time.time(),
        )

    def timed_local_publish(self, operation: Callable[[], _T]) -> _T:
        context = self._context
        started = time.monotonic()
        try:
            result = operation()
            context.circuit.record_success(
                (time.monotonic() - started) * 1000.0,
                kind="local_publish",
            )
            return result
        except DBUS_GATEWAY_OPERATION_ERRORS as error:
            context.circuit.record_error(error, kind="local_publish")
            raise

    def publish_cache(
        self,
        control_snapshot: GatewayControlSnapshot | None = None,
    ) -> None:
        context = self._context
        control = (
            context.health_role.control_snapshot()
            if control_snapshot is None
            else control_snapshot
        )
        captured_at = control.captured_at
        context.cache.health.update(control.health)
        now = time.monotonic()
        topology = self._publish_energy_if_due(now, captured_at)
        topology = self._publish_topology_if_changed(topology, captured_at)
        topology = self._publish_health_if_due(
            topology,
            control=control,
            now=now,
        )
        self._publish_full_cache_if_due(topology, captured_at=captured_at, now=now)

    def _publish_energy_if_due(
        self,
        now: float,
        captured_at: float,
    ) -> EnergyTopologySnapshot | None:
        context = self._context
        if _publish_due(
            now,
            context._last_energy_publish_monotonic,
            context.energy_publish_interval_seconds,
        ):
            topology = context.energy_discovery.topology_snapshot(
                captured_at=captured_at
            )
            inputs = energy_inputs_snapshot(
                context.cache.values,
                context.energy_discovery,
                sequence=context.cache.sequence,
                captured_at=captured_at,
            )
            context.cache.set_semantic_energy_snapshots(inputs, topology)
            context.cache.write_energy_inputs_snapshot()
            context._last_energy_publish_monotonic = now
            return topology
        return None

    def _publish_topology_if_changed(
        self,
        topology: EnergyTopologySnapshot | None,
        captured_at: float,
    ) -> EnergyTopologySnapshot | None:
        context = self._context
        if context.energy_discovery.generation != context._last_topology_generation:
            topology = topology or context.energy_discovery.topology_snapshot(
                captured_at=captured_at
            )
            context.cache.set_energy_topology_snapshot(topology)
            context.cache.write_energy_topology_snapshot()
            context._last_topology_generation = topology.generation
        return topology

    def _publish_health_if_due(
        self,
        topology: EnergyTopologySnapshot | None,
        *,
        control: GatewayControlSnapshot,
        now: float,
    ) -> EnergyTopologySnapshot | None:
        context = self._context
        if _publish_due(
            now,
            context._last_health_publish_monotonic,
            context.health_publish_interval_seconds,
        ):
            topology = topology or context.energy_discovery.topology_snapshot(
                captured_at=control.captured_at
            )
            context.cache.write_health_snapshot(now=control.captured_at)
            context.diagnostics_role.write_gateway_diagnostics(
                health=control.health,
                topology=topology,
                captured_at=control.captured_at,
                captured_monotonic=control.monotonic_at,
            )
            context.health_role.append_health_log(control.health)
            context._last_health_publish_monotonic = now
        return topology

    def _publish_full_cache_if_due(
        self,
        topology: EnergyTopologySnapshot | None,
        *,
        captured_at: float,
        now: float,
    ) -> None:
        context = self._context
        if self._full_cache_publish_due(now):
            topology = topology or context.energy_discovery.topology_snapshot(
                captured_at=captured_at
            )
            context.cache.set_energy_topology_snapshot(topology)
            context.cache.write_cache_snapshot(now=captured_at)
            context._last_cache_publish_monotonic = now
            context._last_cache_publish_sequence = context.cache.sequence
            context.introspection_snapshot_role.write_introspection_snapshot()

    def _full_cache_publish_due(self, now: float) -> bool:
        context = self._context
        elapsed = now - context._last_cache_publish_monotonic
        heartbeat_due = (
            context._last_cache_publish_monotonic <= 0.0
            or elapsed >= context.cache_publish_interval_seconds
        )
        dirty = context.cache.sequence != context._last_cache_publish_sequence
        dirty_due = dirty and elapsed >= context.cache_dirty_publish_interval_seconds
        return heartbeat_due or dirty_due


def _service_names(value: object) -> list[str]:
    if not _is_object_iterable(value):
        raise TypeError("DBus ListNames returned a non-iterable service list")
    return [str(name) for name in value]


def _is_object_iterable(value: object) -> TypeGuard[Iterable[object]]:
    return not isinstance(value, (str, bytes)) and isinstance(value, Iterable)


def _publish_due(now: float, last_published_at: float, interval_seconds: float) -> bool:
    return last_published_at <= 0.0 or now - last_published_at >= interval_seconds

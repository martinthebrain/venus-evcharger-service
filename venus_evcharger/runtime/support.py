# SPDX-License-Identifier: GPL-3.0-or-later
"""Composition root for runtime state, scheduling, and observability."""

from __future__ import annotations

from typing import Any

from venus_evcharger.control import ControlCommand
from venus_evcharger.runtime.async_mainloop_control import ControlCommandQueue
from venus_evcharger.runtime.async_mainloop_executor import RuntimeExecutor
from venus_evcharger.runtime.async_mainloop_publish import DbusPublishQueue
from venus_evcharger.runtime.async_mainloop_state import AsyncRuntimeState
from venus_evcharger.runtime.async_mainloop_watchdog import MainloopWatchdog
from venus_evcharger.runtime.audit import RuntimeAuditLogger
from venus_evcharger.runtime.audit_fields import RuntimeAuditFields
from venus_evcharger.runtime.contracts import AgeSeconds, DefaultFactory, HealthCode, WorkerSnapshot
from venus_evcharger.runtime.health import RuntimeHealthMonitor
from venus_evcharger.readback_store import InMemoryReadbackStore
from venus_evcharger.runtime.setup import RuntimeSetup
from venus_evcharger.runtime.state_store import RuntimeStateStore


class RuntimeSupportController:
    """Expose the productive runtime API through explicitly composed components."""

    def __init__(
        self,
        service: Any,
        age_seconds_func: AgeSeconds,
        health_code_func: HealthCode,
    ) -> None:
        self.service = service
        service._readback_store = InMemoryReadbackStore()
        self.state = RuntimeStateStore(service)
        self.async_state = AsyncRuntimeState(service)
        self.control_commands = ControlCommandQueue(service)
        self.executor = RuntimeExecutor(service, self.control_commands)
        self.mainloop_watchdog = MainloopWatchdog(service, self.async_state)
        self.publish_queue = DbusPublishQueue(service, self.async_state, self.mainloop_watchdog)
        self.setup = RuntimeSetup(service, health_code_func, self.state, self.async_state)
        self.audit_fields = RuntimeAuditFields()
        self.audit = RuntimeAuditLogger(service, self.audit_fields, self.state)
        self.health = RuntimeHealthMonitor(service, age_seconds_func, self.state)

    def initialize_runtime_support(self) -> None:
        self.setup.initialize_runtime_support()

    def reset_system_bus(self) -> None:
        self.setup.reset_system_bus()

    def ensure_system_bus_state(self) -> None:
        self.setup.ensure_system_bus_state()

    @staticmethod
    def create_system_bus() -> Any:
        return RuntimeSetup.create_system_bus()

    def init_worker_state(self) -> None:
        self.state.initialize_worker_state()

    def worker_state_defaults(self) -> dict[str, DefaultFactory]:
        return self.state.worker_defaults()

    @staticmethod
    def ensure_missing_attributes(service: Any, defaults: dict[str, DefaultFactory]) -> None:
        RuntimeStateStore.ensure_missing_attributes(service, defaults)

    def ensure_worker_state(self) -> None:
        self.state.ensure_worker_state()

    @staticmethod
    def empty_worker_snapshot() -> WorkerSnapshot:
        return RuntimeStateStore.empty_snapshot()

    @staticmethod
    def clone_worker_snapshot(snapshot: WorkerSnapshot) -> WorkerSnapshot:
        return RuntimeStateStore.clone_snapshot(snapshot)

    def set_worker_snapshot(self, snapshot: WorkerSnapshot) -> None:
        self.state.set_worker_snapshot(snapshot)

    def update_worker_snapshot(self, **fields: Any) -> None:
        self.state.update_worker_snapshot(**fields)

    def get_worker_snapshot(self) -> WorkerSnapshot:
        return self.state.get_worker_snapshot()

    @staticmethod
    def observability_state_defaults() -> dict[str, DefaultFactory]:
        return RuntimeStateStore.observability_defaults()

    def ensure_observability_state(self) -> None:
        self.state.ensure_observability_state()

    def mark_mainloop_thread(self) -> None:
        self.async_state.mark_mainloop_thread()

    def dbus_publish_direct_allowed(self) -> bool:
        return self.async_state.direct_publish_allowed()

    def assert_dbus_mainloop_thread(self, operation: str = "dbus access") -> None:
        self.async_state.assert_mainloop_thread(operation)

    def enqueue_dbus_publish_values(self, values: list[tuple[str, Any]], current: float) -> bool:
        return self.publish_queue.enqueue_values(values, current)

    def enqueue_dbus_publish_fields(self, fields: list[tuple[str, Any]], current: float) -> bool:
        return self.publish_queue.enqueue_fields(fields, current)

    def enqueue_dbus_update_index_bump(self, current: float) -> None:
        self.publish_queue.enqueue_update_index_bump(current)

    def enqueue_companion_dbus_publish(self, now: float | None = None) -> bool:
        return self.publish_queue.enqueue_companion_publish(now)

    def flush_dbus_publish_queue(self) -> bool:
        return self.publish_queue.flush()

    def start_update_worker(self) -> None:
        self.executor.start_update_worker()

    def schedule_update_cycle(self) -> bool:
        return self.executor.schedule_update_cycle()

    def start_control_command_worker(self) -> None:
        self.executor.start_control_command_worker()

    def enqueue_control_command(self, command: ControlCommand) -> bool:
        return self.control_commands.enqueue(command)

    def mainloop_heartbeat_tick(self) -> bool:
        return self.mainloop_watchdog.heartbeat_tick()

    def start_mainloop_watchdog(self) -> None:
        self.mainloop_watchdog.start()

    def is_update_stale(self, now: float | None = None) -> bool:
        return self.health.is_update_stale(now)

    def watchdog_recover(self, now: float) -> None:
        self.health.watchdog_recover(now)

    def warning_throttled(
        self,
        key: str,
        interval_seconds: float,
        message: str,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        self.health.warning_throttled(key, interval_seconds, message, *args, **kwargs)

    def mark_failure(self, key: str) -> None:
        self.health.mark_failure(key)

    def mark_recovery(self, key: str, message: str, *args: Any) -> None:
        self.health.mark_recovery(key, message, *args)

    def source_retry_ready(self, key: str, now: float | None = None) -> bool:
        return self.health.source_retry_ready(key, now)

    def source_retry_remaining(self, key: str, now: float | None = None) -> int:
        return self.health.source_retry_remaining(key, now)

    def delay_source_retry(
        self,
        key: str,
        now: float | None = None,
        delay_seconds: float | None = None,
    ) -> None:
        self.health.delay_source_retry(key, now, delay_seconds)

    def write_auto_audit_event(self, reason: str, cached: bool = False) -> None:
        self.audit.write_auto_audit_event(reason, cached)


__all__ = ["RuntimeSupportController"]

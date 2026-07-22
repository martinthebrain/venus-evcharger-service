# SPDX-License-Identifier: GPL-3.0-or-later
"""Explicit runtime boundary owned by the flat wallbox service."""

from __future__ import annotations

from venus_evcharger.backend.shelly_io_types import (
    JsonObject,
    PendingRelayCommand,
    ShellyHttpSession,
    ShellyPmStatus,
    ShellyRpcScalar,
)
from venus_evcharger.control import ControlCommand
from venus_evcharger.runtime.contracts import WorkerSnapshot

from .composition_contracts import ControllerOwnerPort


class ServiceRuntimeFacade:
    """Coordinate runtime, helper, and Shelly I/O components without inheritance."""

    def __init__(self, controllers: ControllerOwnerPort) -> None:
        self._controllers = controllers

    def initialize_worker_state(self) -> None:
        self._controllers.runtime.runtime.init_worker_state()

    def ensure_worker_state(self) -> None:
        self._controllers.runtime.runtime.ensure_worker_state()

    def start_update_worker(self) -> None:
        self._controllers.runtime.runtime.start_update_worker()

    def schedule_update_cycle(self) -> bool:
        return self._controllers.runtime.runtime.schedule_update_cycle()

    def start_control_command_worker(self) -> None:
        self._controllers.runtime.runtime.start_control_command_worker()

    def enqueue_control_command(self, command: ControlCommand) -> bool:
        return self._controllers.runtime.runtime.enqueue_control_command(command)

    def mainloop_heartbeat_tick(self) -> bool:
        return self._controllers.runtime.runtime.mainloop_heartbeat_tick()

    def start_mainloop_watchdog(self) -> None:
        self._controllers.runtime.runtime.start_mainloop_watchdog()

    def update_worker_snapshot(self, **fields: object) -> None:
        self._controllers.runtime.runtime.update_worker_snapshot(**fields)

    def worker_snapshot(self) -> WorkerSnapshot:
        return self._controllers.runtime.runtime.get_worker_snapshot()

    def ensure_observability_state(self) -> None:
        self._controllers.runtime.runtime.ensure_observability_state()

    def update_is_stale(self, now: float | None = None) -> bool:
        return self._controllers.runtime.runtime.is_update_stale(now)

    def recover_watchdog(self, now: float) -> None:
        self._controllers.runtime.runtime.watchdog_recover(now)

    def warning_throttled(
        self,
        key: str,
        interval_seconds: float,
        message: str,
        *args: object,
        **kwargs: object,
    ) -> None:
        self._controllers.runtime.runtime.warning_throttled(key, interval_seconds, message, *args, **kwargs)

    def write_auto_audit_event(self, reason: str, cached: bool = False) -> None:
        self._controllers.runtime.runtime.write_auto_audit_event(reason, cached)

    def mark_failure(self, source_key: str) -> None:
        self._controllers.runtime.runtime.mark_failure(source_key)

    def mark_recovery(self, source_key: str, message: str, *args: object) -> None:
        self._controllers.runtime.runtime.mark_recovery(source_key, message, *args)

    def source_retry_ready(self, source_key: str, now: float) -> bool:
        return self._controllers.runtime.runtime.source_retry_ready(source_key, now)

    def source_retry_remaining(self, source_key: str, now: float | None = None) -> int:
        return self._controllers.runtime.runtime.source_retry_remaining(source_key, now)

    def delay_source_retry(self, source_key: str, now: float, delay_seconds: float | None = None) -> None:
        runtime = self._controllers.runtime.runtime
        if delay_seconds is None:
            runtime.delay_source_retry(source_key, now)
        else:
            runtime.delay_source_retry(source_key, now, delay_seconds)

    def stop_auto_input_helper(self, force: bool = False) -> None:
        self._controllers.runtime.auto_input.stop_helper(force)

    def spawn_auto_input_helper(self, now: float | None = None) -> None:
        self._controllers.runtime.auto_input.spawn_helper(now)

    def ensure_auto_input_helper(self, now: float | None = None) -> None:
        self._controllers.runtime.auto_input.ensure_helper_process(now)

    def refresh_auto_input_snapshot(self, now: float | None = None) -> None:
        self._controllers.runtime.auto_input.refresh_snapshot(now)

    def request(self, url: str) -> JsonObject:
        return self._controllers.runtime.shelly.request(url)

    def request_with_session(self, session: ShellyHttpSession, url: str) -> JsonObject:
        return self._controllers.runtime.shelly.request_with_session(session, url)

    def rpc_call(self, method: str, **params: ShellyRpcScalar) -> JsonObject:
        return self._controllers.runtime.shelly.rpc_call(method, **params)

    def rpc_call_with_session(
        self,
        session: ShellyHttpSession,
        method: str,
        **params: ShellyRpcScalar,
    ) -> JsonObject:
        return self._controllers.runtime.shelly.rpc_call_with_session(session, method, **params)

    def worker_fetch_pm_status(self) -> JsonObject:
        return self._controllers.runtime.shelly.worker_fetch_pm_status()

    def build_local_pm_status(self, relay_on: bool) -> ShellyPmStatus:
        return self._controllers.runtime.shelly.build_local_pm_status(relay_on)

    def publish_local_pm_status(self, relay_on: bool, now: float | None = None) -> ShellyPmStatus:
        return self._controllers.runtime.shelly.publish_local_pm_status(relay_on, now)

    def queue_relay_command(self, relay_on: bool, now: float | None = None) -> None:
        self._controllers.runtime.shelly.queue_relay_command(relay_on, now)

    def pending_relay_command(self) -> PendingRelayCommand:
        return self._controllers.runtime.shelly.peek_pending_relay_command()

    def clear_pending_relay_command(self, relay_on: bool) -> None:
        self._controllers.runtime.shelly.clear_pending_relay_command(relay_on)

    def apply_pending_relay_command(self) -> None:
        self._controllers.runtime.shelly.worker_apply_pending_relay_command()

    def start_io_worker(self) -> None:
        self._controllers.runtime.shelly.start_io_worker()

    def fetch_pm_status(self) -> JsonObject:
        return self._controllers.runtime.shelly.fetch_pm_status()

    def set_relay(self, on: bool) -> JsonObject:
        return self._controllers.runtime.shelly.set_relay(on)

    def phase_selection_requires_pause(self) -> bool:
        return self._controllers.runtime.shelly.phase_selection_requires_pause()

    def apply_phase_selection(self, selection: object) -> str:
        return self._controllers.runtime.shelly.set_phase_selection(selection)

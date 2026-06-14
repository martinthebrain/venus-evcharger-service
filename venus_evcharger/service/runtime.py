# SPDX-License-Identifier: GPL-3.0-or-later
"""Runtime, helper, and Shelly-I/O mixins for the Venus EV charger service."""

from __future__ import annotations

from typing import Any, cast

from venus_evcharger.control import ControlCommand

from .factory import ServiceControllerFactoryMixin


class RuntimeHelperMixin(ServiceControllerFactoryMixin):
    """Static runtime, helper, and Shelly-I/O delegations."""

    def _reset_system_bus(self) -> None:
        self._ensure_runtime_support_controller()
        self._runtime_support_controller.reset_system_bus()

    def _ensure_system_bus_state(self) -> None:
        self._ensure_runtime_support_controller()
        self._runtime_support_controller.ensure_system_bus_state()

    def _create_system_bus(self) -> Any:
        self._ensure_runtime_support_controller()
        return self._runtime_support_controller.create_system_bus()

    def _get_system_bus(self) -> Any:
        raise RuntimeError("Direct DBus access is disabled; use the DBus gateway adapter")

    def _init_worker_state(self) -> None:
        self._ensure_runtime_support_controller()
        self._runtime_support_controller.init_worker_state()

    def _worker_state_defaults(self) -> dict[str, Any]:
        self._ensure_runtime_support_controller()
        return cast(dict[str, Any], self._runtime_support_controller.worker_state_defaults())

    def _ensure_missing_attributes(self, defaults: dict[str, Any]) -> None:
        self._ensure_runtime_support_controller()
        self._runtime_support_controller.ensure_missing_attributes(self, defaults)

    def _ensure_worker_state(self) -> None:
        self._ensure_runtime_support_controller()
        self._runtime_support_controller.ensure_worker_state()

    def _mark_mainloop_thread(self) -> None:
        self._ensure_runtime_support_controller()
        self._runtime_support_controller.mark_mainloop_thread()

    def _dbus_publish_direct_allowed(self) -> bool:
        self._ensure_runtime_support_controller()
        return cast(bool, self._runtime_support_controller.dbus_publish_direct_allowed())

    def _assert_dbus_mainloop_thread(self, operation: str = "dbus access") -> None:
        self._ensure_runtime_support_controller()
        self._runtime_support_controller.assert_dbus_mainloop_thread(operation)

    def _enqueue_dbus_publish_values(self, values: list[tuple[str, Any]], current: float) -> bool:
        self._ensure_runtime_support_controller()
        return cast(bool, self._runtime_support_controller.enqueue_dbus_publish_values(values, current))

    def _enqueue_dbus_update_index_bump(self, current: float) -> None:
        self._ensure_runtime_support_controller()
        self._runtime_support_controller.enqueue_dbus_update_index_bump(current)

    def _enqueue_companion_dbus_publish(self, now: float | None = None) -> bool:
        self._ensure_runtime_support_controller()
        return cast(bool, self._runtime_support_controller.enqueue_companion_dbus_publish(now))

    def _flush_dbus_publish_queue(self) -> bool:
        self._ensure_runtime_support_controller()
        return cast(bool, self._runtime_support_controller.flush_dbus_publish_queue())

    def _start_update_worker(self) -> None:
        self._ensure_runtime_support_controller()
        self._runtime_support_controller.start_update_worker()

    def _schedule_update_cycle(self) -> bool:
        self._ensure_runtime_support_controller()
        return cast(bool, self._runtime_support_controller.schedule_update_cycle())

    def _start_control_command_worker(self) -> None:
        self._ensure_runtime_support_controller()
        self._runtime_support_controller.start_control_command_worker()

    def _enqueue_control_command(self, command: ControlCommand) -> bool:
        self._ensure_runtime_support_controller()
        return cast(bool, self._runtime_support_controller.enqueue_control_command(command))

    def _mainloop_heartbeat_tick(self) -> bool:
        self._ensure_runtime_support_controller()
        return cast(bool, self._runtime_support_controller.mainloop_heartbeat_tick())

    def _start_mainloop_watchdog(self) -> None:
        self._ensure_runtime_support_controller()
        self._runtime_support_controller.start_mainloop_watchdog()

    def _set_worker_snapshot(self, snapshot: dict[str, Any]) -> None:
        self._ensure_runtime_support_controller()
        self._runtime_support_controller.set_worker_snapshot(snapshot)

    def _update_worker_snapshot(self, **fields: Any) -> None:
        self._ensure_runtime_support_controller()
        self._runtime_support_controller.update_worker_snapshot(**fields)

    def _get_worker_snapshot(self) -> dict[str, Any]:
        self._ensure_runtime_support_controller()
        return cast(dict[str, Any], self._runtime_support_controller.get_worker_snapshot())

    def _ensure_observability_state(self) -> None:
        self._ensure_runtime_support_controller()
        self._runtime_support_controller.ensure_observability_state()

    def _is_update_stale(self, now: float | None = None) -> bool:
        self._ensure_runtime_support_controller()
        return cast(bool, self._runtime_support_controller.is_update_stale(now))

    def _watchdog_recover(self, now: float) -> None:
        self._ensure_runtime_support_controller()
        self._runtime_support_controller.watchdog_recover(now)

    def _warning_throttled(
        self,
        key: str,
        interval_seconds: float,
        message: str,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        self._ensure_runtime_support_controller()
        self._runtime_support_controller.warning_throttled(key, interval_seconds, message, *args, **kwargs)

    def _write_auto_audit_event(self, reason: str, cached: bool = False) -> None:
        self._ensure_runtime_support_controller()
        self._runtime_support_controller.write_auto_audit_event(reason, cached)

    def _mark_failure(self, source_key: str) -> None:
        self._ensure_runtime_support_controller()
        self._runtime_support_controller.mark_failure(source_key)

    def _mark_recovery(self, source_key: str, message: str, *args: Any) -> None:
        self._ensure_runtime_support_controller()
        self._runtime_support_controller.mark_recovery(source_key, message, *args)

    def _source_retry_ready(self, source_key: str, now: float) -> bool:
        self._ensure_runtime_support_controller()
        return cast(bool, self._runtime_support_controller.source_retry_ready(source_key, now))

    def _source_retry_remaining(self, source_key: str, now: float | None = None) -> int:
        self._ensure_runtime_support_controller()
        return cast(int, self._runtime_support_controller.source_retry_remaining(source_key, now))

    def _delay_source_retry(self, source_key: str, now: float, delay_seconds: float | None = None) -> None:
        self._ensure_runtime_support_controller()
        if delay_seconds is None:
            self._runtime_support_controller.delay_source_retry(source_key, now)
            return
        self._runtime_support_controller.delay_source_retry(source_key, now, delay_seconds)

    def _stop_auto_input_helper(self, force: bool = False) -> None:
        self._ensure_auto_input_supervisor()
        self._auto_input_supervisor.stop_helper(force)

    def _spawn_auto_input_helper(self, now: float | None = None) -> None:
        self._ensure_auto_input_supervisor()
        self._auto_input_supervisor.spawn_helper(now)

    def _ensure_auto_input_helper_process(self, now: float | None = None) -> None:
        self._ensure_auto_input_supervisor()
        self._auto_input_supervisor.ensure_helper_process(now)

    def _stop_dbus_introspection_worker(self, force: bool = False) -> None:
        self._ensure_dbus_introspection_supervisor()
        self._dbus_introspection_supervisor.stop_worker(force)

    def _ensure_dbus_introspection_worker_process(self, now: float | None = None) -> None:
        self._ensure_dbus_introspection_supervisor()
        self._dbus_introspection_supervisor.ensure_worker_process(now)

    def _refresh_auto_input_snapshot(self, now: float | None = None) -> Any:
        self._ensure_auto_input_supervisor()
        return self._auto_input_supervisor.refresh_snapshot(now)

    def _request_with_session(self, session: Any, url: str) -> Any:
        self._ensure_shelly_io_controller()
        return self._shelly_io_controller.request_with_session(session, url)

    def _rpc_call_with_session(self, session: Any, method: str, **params: Any) -> Any:
        self._ensure_shelly_io_controller()
        return self._shelly_io_controller.rpc_call_with_session(session, method, **params)

    def _worker_fetch_pm_status(self) -> Any:
        self._ensure_shelly_io_controller()
        return self._shelly_io_controller.worker_fetch_pm_status()

    def _build_local_pm_status(self, relay_on: bool) -> Any:
        self._ensure_shelly_io_controller()
        return self._shelly_io_controller.build_local_pm_status(relay_on)

    def _publish_local_pm_status(self, relay_on: bool, now: float | None = None) -> Any:
        self._ensure_shelly_io_controller()
        return self._shelly_io_controller.publish_local_pm_status(relay_on, now)

    def _queue_relay_command(self, relay_on: bool, now: float | None = None) -> None:
        self._ensure_shelly_io_controller()
        self._shelly_io_controller.queue_relay_command(relay_on, now)

    def _peek_pending_relay_command(self) -> tuple[bool | None, float | None]:
        self._ensure_shelly_io_controller()
        return cast(tuple[bool | None, float | None], self._shelly_io_controller.peek_pending_relay_command())

    def _clear_pending_relay_command(self, relay_on: bool) -> None:
        self._ensure_shelly_io_controller()
        self._shelly_io_controller.clear_pending_relay_command(relay_on)

    def _worker_apply_pending_relay_command(self) -> None:
        self._ensure_shelly_io_controller()
        self._shelly_io_controller.worker_apply_pending_relay_command()

    def _io_worker_once(self) -> None:
        self._ensure_shelly_io_controller()
        self._shelly_io_controller.io_worker_once()

    def _io_worker_loop(self) -> None:
        self._ensure_shelly_io_controller()
        self._shelly_io_controller.io_worker_loop()

    def _start_io_worker(self) -> None:
        self._ensure_shelly_io_controller()
        self._shelly_io_controller.start_io_worker()

    def _request(self, url: str) -> Any:
        self._ensure_shelly_io_controller()
        return self._shelly_io_controller.request(url)

    def rpc_call(self, method: str, **params: Any) -> Any:
        self._ensure_shelly_io_controller()
        return self._shelly_io_controller.rpc_call(method, **params)

    def fetch_rpc(self, method: str) -> Any:
        return self.rpc_call(method)

    def fetch_pm_status(self) -> Any:
        self._ensure_shelly_io_controller()
        return self._shelly_io_controller.fetch_pm_status()

    def set_relay(self, on: bool) -> Any:
        self._ensure_shelly_io_controller()
        return self._shelly_io_controller.set_relay(on)

    def _phase_selection_requires_pause(self) -> bool:
        self._ensure_shelly_io_controller()
        return cast(bool, self._shelly_io_controller.phase_selection_requires_pause())

    def _apply_phase_selection(self, selection: Any) -> str:
        self._ensure_shelly_io_controller()
        return cast(str, self._shelly_io_controller.set_phase_selection(selection))

# SPDX-License-Identifier: GPL-3.0-or-later
"""Runtime, worker-state, and watchdog helpers for the Venus EV charger service.

This controller owns the "glue" state that keeps the service robust in the
field: cached worker snapshots, throttled warnings, watchdog recovery,
auto-audit logging, and safe persistence of runtime-only state.
"""

from __future__ import annotations

import os
import threading
import time
import uuid
from collections.abc import Callable
from typing import Any, TYPE_CHECKING

import requests
from venus_evcharger.dbus_gateway import DbusCommandInbox, gateway_paths
from venus_evcharger.runtime.async_mainloop import _RuntimeAsyncMainloop
from venus_evcharger.runtime.setup_support import (
    _first_existing_version_line,
    clone_worker_battery_sources_payload as _clone_worker_battery_sources_payload,
    clone_worker_learning_profiles_payload as _clone_worker_learning_profiles_payload,
    clone_worker_status_payload as _clone_worker_status_payload,
    default_auto_metrics,
    empty_worker_snapshot as _empty_worker_snapshot,
    initialize_runtime_override_state,
    initialize_victron_balance_runtime_state,
)
from venus_evcharger.runtime.software_update_setup import initialize_software_update_runtime_state

WorkerSnapshot = dict[str, Any]



class _RuntimeSetup(_RuntimeAsyncMainloop):
    if TYPE_CHECKING:  # pragma: no cover
        service: Any
        _health_code: Callable[[str], int]

        def new_error_state(self) -> dict[str, int]: ...

        def new_failure_state(self) -> dict[str, bool]: ...

    @staticmethod
    def _service_repo_root(service: Any) -> str:
        """Return the repository root inferred from the main entrypoint path."""
        script_path = getattr(service, "_script_path_value", None)
        if not script_path:
            return ""
        return os.path.dirname(os.path.realpath(str(script_path)))

    @staticmethod
    def _system_uptime_seconds() -> float | None:
        """Return the current Linux uptime from ``/proc/uptime`` when available."""
        try:
            with open("/proc/uptime", "r", encoding="utf-8") as handle:
                first_field = handle.readline().split()[0]
        except (IndexError, OSError):
            return None
        try:
            return max(0.0, float(first_field))
        except ValueError:
            return None

    @classmethod
    def _boot_delayed_update_due_at(cls, current_time: float, delay_seconds: float) -> float | None:
        """Return the due timestamp for the post-boot auto-update when applicable."""
        uptime_seconds = cls._system_uptime_seconds()
        if uptime_seconds is None or uptime_seconds >= delay_seconds:
            return None
        return float(current_time) + max(0.0, float(delay_seconds) - uptime_seconds)

    @staticmethod
    def _read_local_version(repo_root: str) -> str:
        """Return the locally installed wallbox version or an empty string."""
        candidates = (
            os.path.join(repo_root, ".bootstrap-state", "installed_version"),
            os.path.join(repo_root, "version.txt"),
        )
        return _first_existing_version_line(candidates)

    def initialize_runtime_support(self) -> None:
        """Initialize runtime caches and watchdog state kept in RAM only."""
        svc = self.service
        repo_root = self._service_repo_root(svc)
        started_at = time.time()
        svc.last_update = 0
        svc.session = requests.Session()
        svc._system_bus = None
        svc._system_bus_state = threading.local()
        svc._system_bus_generation = 0
        svc._system_bus_generation_lock = threading.Lock()
        svc._gateway_paths = gateway_paths(getattr(svc, "dbus_gateway_run_dir", ""))
        svc._gateway_core_commands = DbusCommandInbox(svc._gateway_paths.core_command_dir)
        svc._resolved_auto_pv_services = []
        svc._auto_pv_last_scan = 0.0
        svc._last_pv_missing_warning = None
        svc._resolved_auto_battery_service = None
        svc._auto_battery_last_scan = 0.0
        svc._resolved_auto_energy_services = {}
        svc._auto_energy_last_scan = {}
        svc._last_battery_missing_warning = None
        svc._last_battery_allow_warning = None
        svc._last_grid_missing_warning = None
        svc._dbus_list_backoff_until = 0.0
        svc._dbus_list_failures = 0
        svc._warning_state = {}
        svc._error_state = self.new_error_state()
        svc._failure_active = self.new_failure_state()
        svc._last_health_reason = "init"
        svc._last_health_code = self._health_code(svc._last_health_reason)
        svc._last_auto_state = "idle"
        svc._last_auto_state_code = 0
        svc._auto_cached_inputs_used = False
        svc._last_pv_value = None
        svc._last_pv_at = None
        svc._last_grid_value = None
        svc._last_grid_at = None
        svc._last_battery_soc_value = None
        svc._last_battery_soc_at = None
        svc._last_combined_battery_soc_value = None
        svc._last_combined_battery_soc_at = None
        svc._last_combined_battery_charge_power_w = None
        svc._last_combined_battery_charge_power_at = None
        svc._last_combined_battery_discharge_power_w = None
        svc._last_combined_battery_discharge_power_at = None
        svc._last_combined_battery_net_power_w = None
        svc._last_combined_battery_net_power_at = None
        svc._last_combined_battery_ac_power_w = None
        svc._last_combined_battery_ac_power_at = None
        svc._last_energy_cluster = {}
        svc._last_energy_learning_profiles = {}
        svc._last_pm_status = None
        svc._last_pm_status_at = None
        svc._last_pm_status_confirmed = False
        svc._last_shelly_warning = None
        svc._shelly_state = "unknown"
        svc._shelly_last_error_reason = ""
        svc._shelly_last_error_detail = ""
        svc._shelly_last_error_at = None
        svc._shelly_consecutive_errors = 0
        svc._shelly_last_ok_at = None
        svc._shelly_retry_after = 0.0
        svc._shelly_session_reset_count = 0
        svc._shelly_offline_since = None
        svc._last_auto_metrics = default_auto_metrics()
        initialize_victron_balance_runtime_state(svc)
        svc._last_voltage = None
        svc._last_dbus_ok_at = None
        svc._last_successful_update_at = None
        svc._last_recovery_attempt_at = None
        svc._recovery_attempts = 0
        initialize_runtime_override_state(svc)
        initialize_software_update_runtime_state(
            svc,
            repo_root=repo_root,
            started_at=started_at,
            current_version=self._read_local_version(repo_root),
            boot_auto_due_at=self._boot_delayed_update_due_at(started_at, 3600.0),
        )
        self.initialize_async_runtime_state()

    def reset_system_bus(self) -> None:
        """Invalidate cached DBus connections so each thread reconnects cleanly."""
        svc = self.service
        svc._ensure_system_bus_state()
        with svc._system_bus_generation_lock:
            svc._system_bus_generation += 1
        svc._system_bus = None
        svc._system_bus_state.bus = None
        svc._system_bus_state.generation = -1

    def ensure_system_bus_state(self) -> None:
        """Initialize per-thread DBus connection helpers for partial test instances."""
        svc = self.service
        if not hasattr(svc, "_system_bus_state"):
            svc._system_bus_state = threading.local()
        if not hasattr(svc, "_system_bus_generation"):
            svc._system_bus_generation = 0
        if not hasattr(svc, "_system_bus_generation_lock"):
            svc._system_bus_generation_lock = threading.Lock()

    @staticmethod
    def create_system_bus() -> Any:
        """Reject direct DBus access from the core service."""
        raise RuntimeError("Direct DBus access is disabled; use the DBus gateway adapter")

    def get_system_bus(self) -> Any:
        """Return the current thread-local DBus connection, reconnecting after resets."""
        svc = self.service
        svc._ensure_system_bus_state()
        state = svc._system_bus_state
        generation = int(getattr(svc, "_system_bus_generation", 0))
        bus = getattr(state, "bus", None)
        bus_generation = int(getattr(state, "generation", -1))
        if bus is None or bus_generation != generation:
            bus = self.create_system_bus()
            state.bus = bus
            state.generation = generation
        return bus

    @staticmethod
    def empty_worker_snapshot() -> WorkerSnapshot:
        """Return a default RAM snapshot for the background I/O worker."""
        return _empty_worker_snapshot()

    @staticmethod
    def clone_worker_snapshot(snapshot: WorkerSnapshot) -> WorkerSnapshot:
        """Copy the worker snapshot so the main loop sees a stable view."""
        cloned = dict(snapshot)
        _clone_worker_status_payload(cloned)
        _clone_worker_battery_sources_payload(cloned)
        _clone_worker_learning_profiles_payload(cloned)
        return cloned

    def init_worker_state(self) -> None:
        """Initialize the background I/O worker state kept in RAM."""
        svc = self.service
        svc._worker_poll_interval_seconds = max(0.2, svc.poll_interval_ms / 1000.0)
        svc._worker_snapshot_lock = threading.Lock()
        svc._relay_command_lock = threading.Lock()
        svc._worker_snapshot = self.empty_worker_snapshot()
        svc._worker_stop_event = threading.Event()
        svc._worker_session = requests.Session()
        svc._worker_thread = None
        svc._pending_relay_state = None
        svc._pending_relay_requested_at = None
        svc.relay_sync_timeout_seconds = max(2.0, svc._worker_poll_interval_seconds * 3.0)
        svc._relay_sync_expected_state = None
        svc._relay_sync_requested_at = None
        svc._relay_sync_deadline_at = None
        svc._relay_sync_failure_reported = False
        svc._auto_input_helper_process = None
        svc._auto_input_helper_generation = 0
        svc._auto_input_runtime_instance_id = uuid.uuid4().hex
        svc._auto_input_helper_last_start_at = 0.0
        svc._auto_input_helper_restart_requested_at = None
        svc._auto_input_snapshot_last_seen = None
        svc._auto_input_snapshot_seen_for_current_helper = False
        svc._auto_input_snapshot_mtime_ns = None
        svc._auto_input_snapshot_last_captured_at = None
        svc._auto_input_snapshot_version = None
        svc._auto_input_snapshot_writer_pid = None
        svc._auto_input_snapshot_generation = None
        svc._auto_input_snapshot_runtime_instance_id = None
__all__ = ["_RuntimeSetup"]

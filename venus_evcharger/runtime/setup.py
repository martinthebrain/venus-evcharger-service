# SPDX-License-Identifier: GPL-3.0-or-later
"""Initialization of mutable, RAM-only service runtime state."""

from __future__ import annotations

import os
import time
from typing import Any

import requests

from venus_evcharger.ipc.core_commands import DEFAULT_CORE_COMMAND_DIR, CoreCommandMailbox
from venus_evcharger.runtime.contracts import AsyncRuntimeStatePort, HealthCode, RuntimeStateStorePort
from venus_evcharger.runtime.setup_support import (
    _first_existing_version_line,
    default_auto_metrics,
    initialize_runtime_override_state,
    initialize_victron_balance_runtime_state,
)
from venus_evcharger.runtime.software_update_setup import initialize_software_update_runtime_state


class RuntimeSetup:
    """Initialize the mutable runtime state for one service."""

    def __init__(
        self,
        service: Any,
        health_code: HealthCode,
        state_store: RuntimeStateStorePort,
        async_state: AsyncRuntimeStatePort,
    ) -> None:
        self.service = service
        self._health_code = health_code
        self.state_store = state_store
        self.async_state = async_state

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
        return str(_first_existing_version_line(candidates))

    def initialize_runtime_support(self) -> None:
        """Initialize runtime caches and watchdog state kept in RAM only."""
        svc = self.service
        repo_root = self._service_repo_root(svc)
        started_at = time.time()
        svc.last_update = 0
        svc.session = requests.Session()
        mailbox_dir = str(getattr(svc, "core_command_mailbox_dir", "") or DEFAULT_CORE_COMMAND_DIR)
        svc._core_command_mailbox = CoreCommandMailbox(mailbox_dir)
        svc._last_pv_missing_warning = None
        svc._last_battery_missing_warning = None
        svc._last_battery_allow_warning = None
        svc._last_grid_missing_warning = None
        svc._warning_state = {}
        defaults = self.state_store.observability_defaults()
        svc._error_state = defaults["_error_state"]()
        svc._failure_active = defaults["_failure_active"]()
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
        self.async_state.initialize()

__all__ = ["RuntimeSetup"]

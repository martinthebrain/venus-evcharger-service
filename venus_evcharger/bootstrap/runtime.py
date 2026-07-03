# SPDX-License-Identifier: GPL-3.0-or-later
"""Bootstrap and service-registration helpers for the Venus EV charger service.

This module is the place to look first when you want to understand how the
service comes up:
- read config
- normalize and validate wallbox state
- build controller objects
- register DBus paths
- start the helper/worker processes
- hand control over to the GLib main loop
"""

from __future__ import annotations

import logging
import os
import time
from collections import deque
from collections.abc import Mapping
from typing import Any

from venus_evcharger.bootstrap.config import _ServiceBootstrapConfig
from venus_evcharger.backend.errors import BACKEND_OPTIONAL_CAPABILITY_ERRORS
from venus_evcharger.backend.shelly_io import ShellyIoController
from venus_evcharger.inputs.supervisor import AutoInputSupervisor
from venus_evcharger.publish.dbus import DbusPublishController
from venus_evcharger.update.controller import UpdateCycleController
from venus_evcharger.backend.factory import build_service_backends
from venus_evcharger.backend.models import PhaseSelection, normalize_phase_selection, normalize_phase_selection_tuple
from venus_evcharger.bootstrap.errors import BOOTSTRAP_DEVICE_INFO_ERRORS
from venus_evcharger.controllers.auto import AutoDecisionController
from venus_evcharger.controllers.state import ServiceStateController
from venus_evcharger.controllers.write import DbusWriteController
from venus_evcharger.ports import AutoDecisionPort, UpdateCyclePort, WriteControllerPort
from venus_evcharger.runtime import RuntimeSupportController
from venus_evcharger.dbus_gateway import GatewayDbusServiceProxy


def _device_info_payload(payload: object) -> dict[str, Any]:
    """Return one plain device-info dict from an RPC payload."""
    return dict(payload) if isinstance(payload, Mapping) else {}


class _ServiceBootstrapRuntime(_ServiceBootstrapConfig):
    @staticmethod
    def _topology_configured(svc: Any) -> bool:
        """Return whether one service has a configured runtime topology."""
        return bool(getattr(svc, "topology_configured", getattr(svc, "host_configured", False)))

    @staticmethod
    def _primary_rpc_configured(svc: Any) -> bool:
        """Return whether one service still has a direct legacy RPC endpoint."""
        return bool(getattr(svc, "primary_rpc_configured", getattr(svc, "host_configured", False)))

    @staticmethod
    def _switch_backend_supported_phase_selections(svc: Any) -> tuple[PhaseSelection, ...]:
        """Return normalized supported phase selections declared by the current switch backend."""
        backend = getattr(svc, "_switch_backend", None)
        capabilities_method = getattr(backend, "capabilities", None)
        if not callable(capabilities_method):
            return ("P1",)
        try:
            capabilities = capabilities_method()
        except BACKEND_OPTIONAL_CAPABILITY_ERRORS:
            return ("P1",)
        return normalize_phase_selection_tuple(
            getattr(capabilities, "supported_phase_selections", ("P1",)),
            ("P1",),
        )

    @staticmethod
    def _charger_backend_supported_phase_selections(svc: Any) -> tuple[PhaseSelection, ...]:
        """Return normalized supported phase selections declared by the current charger backend."""
        backend = getattr(svc, "_charger_backend", None)
        settings = getattr(backend, "settings", None)
        return normalize_phase_selection_tuple(
            getattr(settings, "supported_phase_selections", ("P1",)),
            ("P1",),
        )

    def initialize_controllers(self) -> None:
        """Create the controller objects used by the service runtime."""
        svc = self.service
        svc._runtime_support_controller = RuntimeSupportController(svc, self._age_seconds, self._health_code)
        svc._runtime_support_controller.initialize_runtime_support()
        svc._auto_controller = AutoDecisionController(
            AutoDecisionPort(svc),
            self._health_code,
            self._mode_uses_auto_logic,
        )
        svc._dbus_publisher = DbusPublishController(svc, self._age_seconds)
        svc._shelly_io_controller = ShellyIoController(svc)
        resolved_backends = build_service_backends(svc)
        svc._backend_bundle = resolved_backends
        svc._meter_backend = resolved_backends.meter
        svc._switch_backend = resolved_backends.switch
        svc._charger_backend = resolved_backends.charger
        runtime_backends = resolved_backends.runtime
        svc.topology_configured = runtime_backends.topology_configured
        svc.primary_rpc_configured = runtime_backends.primary_rpc_configured
        if not hasattr(svc, "_state_controller") or svc._state_controller is None:
            svc._state_controller = ServiceStateController(svc, self._normalize_mode)
        svc._write_controller = DbusWriteController(WriteControllerPort(svc))
        svc._auto_input_supervisor = AutoInputSupervisor(svc)
        svc._update_controller = UpdateCycleController(
            UpdateCyclePort(svc),
            self._phase_values,
            self._health_code,
        )

    def initialize_virtual_state(self) -> None:
        """Initialize the writable EV charger state exposed on DBus."""
        svc = self.service
        defaults = svc.config["DEFAULT"]
        supported_phase_selections = self._switch_backend_supported_phase_selections(svc)
        if getattr(svc, "_switch_backend", None) is None and getattr(svc, "_charger_backend", None) is not None:
            supported_phase_selections = self._charger_backend_supported_phase_selections(svc)
        svc.manual_override_until = 0.0
        svc.virtual_mode = self._normalize_mode(defaults.get("Mode", "0"))
        svc.virtual_autostart = int(defaults.get("AutoStart", "1"))
        svc.virtual_startstop = int(defaults.get("StartStop", "1"))
        svc.virtual_enable = int(defaults.get("Enable", defaults.get("StartStop", "1")))
        svc.virtual_set_current = float(defaults.get("SetCurrent", svc.max_current))
        svc.charging_started_at = None
        svc.energy_at_start = 0.0
        svc.last_status = 0
        svc.auto_start_condition_since = None
        svc.auto_stop_condition_since = None
        svc.auto_stop_condition_reason = None
        svc.auto_samples = deque()
        svc._auto_high_soc_profile_active = None
        svc._stop_smoothed_surplus_power = None
        svc._stop_smoothed_grid_power = None
        svc.learned_charge_power_watts = None
        svc.learned_charge_power_updated_at = None
        svc.learned_charge_power_state = "unknown"
        svc.learned_charge_power_learning_since = None
        svc.learned_charge_power_sample_count = 0
        svc.learned_charge_power_phase = None
        svc.learned_charge_power_voltage = None
        svc.learned_charge_power_signature_mismatch_sessions = 0
        svc.learned_charge_power_signature_checked_session_started_at = None
        svc.relay_last_changed_at = None
        svc.relay_last_off_at = None
        svc.supported_phase_selections = supported_phase_selections
        configured_phase_selection = normalize_phase_selection(
            defaults.get("PhaseSelection", supported_phase_selections[0]),
            supported_phase_selections[0],
        )
        if configured_phase_selection not in supported_phase_selections:
            configured_phase_selection = supported_phase_selections[0]
        svc.requested_phase_selection = configured_phase_selection
        svc.active_phase_selection = configured_phase_selection
        svc._grid_recovery_required = False
        svc._grid_recovery_since = None
        svc._auto_mode_cutover_pending = False
        svc._ignore_min_offtime_once = False

    def restore_runtime_state(self) -> None:
        """Restore RAM-backed state and initialize worker bookkeeping."""
        svc = self.service
        svc._load_runtime_state()
        svc._startup_manual_target = (
            bool(svc.virtual_enable or svc.virtual_startstop)
            if not self._mode_uses_auto_logic(svc.virtual_mode)
            else None
        )
        svc._init_worker_state()

    def initialize_dbus_service(self) -> None:  # pragma: no cover
        """Create the Venus EV charger gateway service proxy."""
        svc = self.service
        svc._dbusservice = GatewayDbusServiceProxy(f"{svc.service_name}.http_{svc.deviceinstance}")

    def apply_device_metadata(self) -> None:
        """Fetch Shelly metadata and apply UI-facing identity fields."""
        svc = self.service
        defaults = svc.config["DEFAULT"]
        svc.product_name = defaults.get("ProductName", "Venus EV Charger Service").strip()
        if not self._topology_configured(svc):
            self._apply_unconfigured_device_metadata(svc)
            return
        if not self._primary_rpc_configured(svc):
            self._apply_adapter_topology_device_metadata(svc)
            return
        self._apply_rpc_device_metadata(svc)

    def _apply_unconfigured_device_metadata(self, svc: Any) -> None:
        """Apply device metadata for an unconfigured topology."""
        svc.custom_name = svc.custom_name_override or "Venus EV Charger Service"
        svc.serial = f"unconfigured-{svc.deviceinstance}"
        svc.firmware_version = self._read_version("version.txt")
        svc.hardware_version = "Not configured"
        logging.info("No load topology is configured yet; starting without device metadata")

    def _apply_adapter_topology_device_metadata(self, svc: Any) -> None:
        """Apply generic metadata for adapter-only topologies without direct RPC."""
        svc.custom_name = svc.custom_name_override or "Venus EV Charger Service"
        svc.serial = f"topology-{svc.deviceinstance}"
        svc.firmware_version = self._read_version("version.txt")
        svc.hardware_version = "External adapter topology"
        logging.info("No direct legacy RPC endpoint is configured; starting with generic device metadata")

    def _apply_rpc_device_metadata(self, svc: Any) -> None:
        """Apply metadata fetched from the direct device RPC endpoint."""
        device_info = self.fetch_device_info_with_fallback()
        svc.custom_name = svc.custom_name_override or device_info.get("name") or "Venus EV Charger Service"
        svc.serial = device_info.get("mac", svc.host.replace(".", ""))
        svc.firmware_version = device_info.get("fw_id", self._read_version("version.txt"))
        svc.hardware_version = device_info.get("model", "Shelly 1PM Gen4")

    def start_runtime_loops(self) -> None:
        """Register DBus paths, start background workers, and arm timers."""
        svc = self.service
        self._call_runtime_hook("_mark_mainloop_thread")
        if self._topology_configured(svc):
            svc._start_io_worker()
        else:
            logging.info("No load topology is configured yet; skipping runtime I/O worker startup")
        svc._start_control_api_server()
        self._start_runtime_optional_hooks()
        logging.info(
            "Initialized Venus EV charger service pid=%s runtime_state=%s %s",
            os.getpid(),
            svc.runtime_state_path,
            svc._state_summary(),
        )
        self._register_runtime_timers()
        self._gobject.timeout_add(svc.sign_of_life_minutes * 60 * 1000, svc._sign_of_life)

    def _call_runtime_hook(self, name: str) -> bool:
        """Call one optional runtime hook when present."""
        hook = getattr(self.service, name, None)
        if not callable(hook):
            return False
        hook()
        return True

    def _start_runtime_optional_hooks(self) -> None:
        """Start optional runtime workers and companions."""
        for name in (
            "_start_update_worker",
            "_start_control_command_worker",
            "_start_mainloop_watchdog",
            "_start_companion_dbus_bridge",
        ):
            self._call_runtime_hook(name)

    def _register_runtime_timers(self) -> None:
        """Register runtime GLib timers."""
        svc = self.service
        schedule_update_cycle = getattr(svc, "_schedule_update_cycle", None)
        self._gobject.timeout_add(
            svc.poll_interval_ms,
            schedule_update_cycle if callable(schedule_update_cycle) else svc._update,
        )
        flush_dbus_publish_queue = getattr(svc, "_flush_dbus_publish_queue", None)
        if callable(flush_dbus_publish_queue):
            self._gobject.timeout_add(int(getattr(svc, "_dbus_publish_flush_interval_ms", 200)), flush_dbus_publish_queue)
        mainloop_heartbeat_tick = getattr(svc, "_mainloop_heartbeat_tick", None)
        if callable(mainloop_heartbeat_tick):
            self._gobject.timeout_add(1000, mainloop_heartbeat_tick)

    def fetch_device_info_with_fallback(self) -> dict[str, Any]:
        """Try to fetch Shelly device info, but start with generic metadata if that fails."""
        svc = self.service
        last_error = None
        attempts = svc.startup_device_info_retries + 1
        for attempt in range(attempts):
            try:
                return _device_info_payload(svc.fetch_rpc("Shelly.GetDeviceInfo"))
            except BOOTSTRAP_DEVICE_INFO_ERRORS as error:
                last_error = error
                if attempt < (attempts - 1) and svc.startup_device_info_retry_seconds > 0:
                    logging.warning(
                        "Shelly.GetDeviceInfo failed during startup (attempt %s/%s): %s",
                        attempt + 1,
                        attempts,
                        error,
                    )
                    time.sleep(svc.startup_device_info_retry_seconds)
        logging.warning(
            "Shelly.GetDeviceInfo unavailable during startup, continuing with generic metadata: %s",
            last_error,
        )
        return {}

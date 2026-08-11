# SPDX-License-Identifier: GPL-3.0-or-later
"""Unit tests for the Venus EV charger service helper logic."""

import configparser
import json
import sys
import tempfile
import threading
import unittest
from collections import deque
from collections.abc import Mapping
from datetime import datetime, timezone
from unittest.mock import MagicMock

from tests.support.auto_input_supervisor import valid_snapshot

sys.modules["vedbus"] = MagicMock()
sys.modules["dbus"] = MagicMock()
sys.modules["dbus.mainloop.glib"] = MagicMock()
sys.modules["gi"] = MagicMock()
sys.modules["gi.repository"] = MagicMock()
sys.modules["gi.repository.GLib"] = MagicMock()

import venus_evcharger_service  # noqa: E402
import venus_evcharger.runtime.support as runtime_support_module  # noqa: E402
from venus_evcharger.auto.policy import AutoPolicy, AutoThresholdProfile  # noqa: E402
from venus_evcharger.controllers.auto import AutoDecisionController  # noqa: E402
from venus_evcharger.control.models import ControlCommandName  # noqa: E402
from venus_evcharger.core.common import _age_seconds, _health_code, read_version  # noqa: E402
from venus_evcharger.dbus_adapter.publication.schema import EVCS_PUBLICATION_SPECS, validate_fields  # noqa: E402
from venus_evcharger.ports.gateway_publication import (  # noqa: E402
    CompanionServiceIdentity,
    EvcsServiceIdentity,
    PublicationPriority,
    PublicationReceipt,
)
from venus_evcharger.service.auto_facade import ServiceAutoFacade  # noqa: E402
from venus_evcharger.service.control import ServiceControlFacade  # noqa: E402
from venus_evcharger.service.controller_owner import ServiceControllerOwner, ServiceFunctionBundle  # noqa: E402
from venus_evcharger.service.runtime_facade import ServiceRuntimeFacade  # noqa: E402
from venus_evcharger.service.state_facade import ServiceStateFacade  # noqa: E402
from venus_evcharger.service.update_facade import ServiceUpdateFacade  # noqa: E402
from venus_evcharger_service import ShellyWallboxService, mode_uses_auto_logic, month_in_ranges, month_window, normalize_mode, normalize_phase, parse_hhmm, phase_values  # noqa: E402


def utc_timestamp(year: int, month: int, day: int, hour: int, minute: int = 0) -> float:
    return datetime(year, month, day, hour, minute, tzinfo=timezone.utc).timestamp()


def configure_auto_policy(
    service: object,
    *,
    start_surplus_watts: float = 2000.0,
    stop_surplus_watts: float = 1600.0,
    min_soc: float = 30.0,
    resume_soc: float = 33.0,
    start_max_grid_import_watts: float = 50.0,
    stop_grid_import_watts: float = 300.0,
    grid_recovery_start_seconds: float = 10.0,
) -> AutoPolicy:
    policy = AutoPolicy(
        normal_profile=AutoThresholdProfile(start_surplus_watts, stop_surplus_watts),
        min_soc=min_soc,
        resume_soc=resume_soc,
        start_max_grid_import_watts=start_max_grid_import_watts,
        stop_grid_import_watts=stop_grid_import_watts,
        grid_recovery_start_seconds=grid_recovery_start_seconds,
    )
    setattr(service, "auto_policy", policy)
    return policy


class _TestTimers:
    @staticmethod
    def timeout_add(_interval: int, _callback: object) -> object:
        return object()


class _HelperGatewayPublication:
    """Apply semantic publications to the fixture's adapter-facing value view."""

    def __init__(self, service: ShellyWallboxService) -> None:
        self._service = service

    def register_evcs(
        self,
        identity: EvcsServiceIdentity,
        initial_fields: Mapping[str, object],
    ) -> PublicationReceipt:
        del identity
        self._apply_evcs_fields(initial_fields)
        return PublicationReceipt(True, "helper-evcs-registration")

    def publish_evcs_fields(
        self,
        fields: Mapping[str, object],
        *,
        priority: PublicationPriority,
    ) -> PublicationReceipt:
        del priority
        self._apply_evcs_fields(fields)
        return PublicationReceipt(True, "helper-evcs-publication")

    def register_companion(
        self,
        identity: CompanionServiceIdentity,
        initial_fields: Mapping[str, object],
    ) -> PublicationReceipt:
        del initial_fields
        return PublicationReceipt(True, identity.service_id)

    def publish_companion_fields(
        self,
        service_id: str,
        fields: Mapping[str, object],
        *,
        priority: PublicationPriority,
    ) -> PublicationReceipt:
        del fields, priority
        return PublicationReceipt(True, service_id)

    def _apply_evcs_fields(self, fields: Mapping[str, object]) -> None:
        semantic_fields = validate_fields(fields, EVCS_PUBLICATION_SPECS, surface="helper-evcs")
        for field, value in semantic_fields.items():
            self._service._dbusservice[EVCS_PUBLICATION_SPECS[field].path] = value


def handle_control_surface_write(
    service: ShellyWallboxService,
    name: ControlCommandName,
    target: str,
    value: object,
) -> bool:
    """Exercise one GUI-equivalent write through the canonical control contract."""
    command = service.auto.command(name, target, value, source="control-surface")
    return bool(service.auto.handle_command(command).accepted)


def _compose_helper_service(service: ShellyWallboxService) -> ShellyWallboxService:
    """Build the production controller graph around a lightweight test service."""
    functions = ServiceFunctionBundle(
        normalize_phase=normalize_phase,
        normalize_mode=normalize_mode,
        mode_uses_auto_logic=mode_uses_auto_logic,
        month_window=month_window,
        age_seconds=_age_seconds,
        health_code=_health_code,
        phase_values=phase_values,
        read_version=read_version,
        gobject=_TestTimers(),
        script_path=str(venus_evcharger_service.__file__),
        config_path="/tmp/venus-evcharger-helper-test.ini",
        auto_input_helper_path="/tmp/venus_evcharger_auto_input_helper.py",
    )
    service.controllers = ServiceControllerOwner(service, functions)
    service.runtime = ServiceRuntimeFacade(service.controllers)
    service.state = ServiceStateFacade(service.controllers, service.runtime)
    service.update = ServiceUpdateFacade(service.controllers)
    service.control = ServiceControlFacade(service)
    service.auto = ServiceAutoFacade(
        service.controllers,
        service.control.publish_command_event,
    )
    service.controllers.initialize_runtime()
    service.gateway_publication = _HelperGatewayPublication(service)
    return service


def make_helper_service() -> ShellyWallboxService:
    """Return a fully composed service fixture with neutral runtime defaults."""
    return ShellyWallboxHelpersTestBase._make_update_service(background_runtime=True)


class ShellyWallboxHelpersTestBase(unittest.TestCase):
    @staticmethod
    def _make_update_service(*, background_runtime: bool = False) -> ShellyWallboxService:
        service = ShellyWallboxService.__new__(ShellyWallboxService)
        service.config = configparser.ConfigParser()
        service.config.read_string(
            """
[DEFAULT]
Host=192.0.2.20

[Backends]
Mode=combined
MeterType=shelly_combined
SwitchType=shelly_combined
ChargerType=
"""
        )
        service.poll_interval_ms = 1000
        service.host = "192.0.2.20"
        service.use_digest_auth = False
        service.username = ""
        service.password = ""
        service.pm_component = "switch:0"
        service.pm_id = 0
        service.shelly_request_timeout_seconds = 2.0
        service.phase = "L1"
        service.voltage_mode = "phase"
        service.charging_threshold_watts = 100
        service.idle_status = 6
        service.virtual_mode = 1
        service.virtual_autostart = 1
        service.virtual_startstop = 0
        service.virtual_enable = 1
        service.virtual_set_current = 16.0
        service.min_current = 6.0
        service.max_current = 16.0
        service.supported_phase_selections = ("P1",)
        service.requested_phase_selection = "P1"
        service.active_phase_selection = "P1"
        service.relay_sync_timeout_seconds = 5.0
        service._worker_poll_interval_seconds = 1.0
        service._worker_session = MagicMock()
        service._worker_stop_event = threading.Event()
        service._worker_thread = None
        service._relay_command_lock = threading.Lock()
        service._pending_relay_state = None
        service._pending_relay_requested_at = None
        service.charging_started_at = None
        service.energy_at_start = 0.0
        service.last_status = 0
        service._last_health_reason = "init"
        service._last_health_code = 0
        service._error_state = {
            "dbus": 0,
            "shelly": 0,
            "charger": 0,
            "pv": 0,
            "battery": 0,
            "grid": 0,
            "cache_hits": 0,
        }
        service._failure_active = {
            "dbus": False,
            "shelly": False,
            "charger": False,
            "pv": False,
            "battery": False,
            "grid": False,
        }
        service._warning_state = {}
        service._dbusservice = {"/UpdateIndex": 0}
        service._dbus_publish_state = {}
        service._dbus_live_publish_interval_seconds = 0.0
        service._dbus_slow_publish_interval_seconds = 0.0
        service.last_update = 0
        service.service_name = "com.victronenergy.evcharger.test"
        service._control_command_async_enabled = False
        service._script_path_value = str(venus_evcharger_service.__file__)
        service.auto_input_cache_seconds = 120
        service.auto_pv_poll_interval_seconds = 2.0
        service.auto_grid_poll_interval_seconds = 2.0
        service.auto_battery_poll_interval_seconds = 10.0
        service.auto_input_validation_poll_seconds = 30.0
        service.auto_input_snapshot_path = "/tmp/auto-helper.json"
        service.auto_input_helper_restart_seconds = 5
        service.auto_input_helper_stale_seconds = 15
        service._auto_input_helper_generation = 0
        service._auto_input_runtime_instance_id = "helper-test"
        service.auto_shelly_soft_fail_seconds = 10
        service.auto_watchdog_stale_seconds = 180
        service.auto_watchdog_recovery_seconds = 60
        service.auto_grid_missing_stop_seconds = 60
        service.auto_audit_log = False
        service.auto_manual_override_seconds = 300
        service.auto_start_condition_since = None
        service.auto_stop_condition_since = None
        service.auto_start_delay_seconds = 0.0
        service.auto_stop_delay_seconds = 0.0
        service.auto_scheduled_enabled_days = "0,1,2,3,4,5,6"
        service.auto_scheduled_night_start_delay_seconds = 0.0
        service.auto_scheduled_latest_end_time = "07:00"
        service.auto_scheduled_night_current_amps = 6.0
        service.manual_override_until = 0.0
        service._auto_mode_cutover_pending = False
        service._ignore_min_offtime_once = False
        service._phase_switch_lockout_selection = None
        service._phase_switch_lockout_until = 0.0
        service.started_at = 0.0
        service.auto_startup_warmup_seconds = 0.0
        service._last_pv_value = None
        service._last_pv_at = None
        service._last_grid_value = None
        service._last_grid_at = None
        service._last_battery_soc_value = None
        service._last_battery_soc_at = None
        service._last_pm_status = None
        service._last_pm_status_at = None
        service._last_shelly_warning = None
        service._last_battery_allow_warning = None
        service._last_dbus_ok_at = None
        service._last_voltage = None
        service._charger_target_current_amps = None
        service._charger_target_current_applied_at = None
        service._last_auto_metrics = {"surplus": None, "grid": None, "soc": None}
        service.dbus_gateway_run_dir = "/tmp/venus-evcharger-helper-gateway"
        service.dbus_gateway_cache_path = "/tmp/venus-evcharger-helper-gateway/dbus-cache.json"
        service.dbus_gateway_max_age_seconds = 10.0
        service.auto_dbus_backoff_base_seconds = 1.0
        service.auto_dbus_backoff_max_seconds = 60.0
        service.auto_pv_scan_interval_seconds = 60.0
        service.auto_pv_service = ""
        service.auto_pv_service_prefix = "com.victronenergy.pvinverter"
        service.auto_pv_max_services = 8
        service.auto_pv_path = "/Ac/Power"
        service.auto_use_dc_pv = True
        service.auto_battery_scan_interval_seconds = 60.0
        service.auto_battery_service = ""
        service.auto_battery_service_prefix = "com.victronenergy.battery"
        service.auto_battery_soc_path = "/Soc"
        service.auto_battery_capacity_wh = None
        service.auto_battery_power_path = "/Dc/0/Power"
        service.auto_battery_ac_power_path = "/Ac/Power"
        service.auto_battery_pv_power_path = "/Dc/Pv/Power"
        service.auto_battery_grid_interaction_path = "/Ac/ActiveIn/L1/P"
        service.auto_battery_operating_mode_path = "/Settings/CGwacs/BatteryLife/State"
        service.auto_energy_sources = ()
        service.auto_use_combined_battery_soc = False
        service.auto_grid_service = "com.victronenergy.system"
        service._last_energy_learning_profiles = {}
        service._last_energy_cluster = {}
        service.auto_samples = deque()
        service.relay_last_changed_at = None
        service.relay_last_off_at = None
        service._auto_cached_inputs_used = False
        service._worker_snapshot_lock = threading.Lock()
        service._worker_snapshot = runtime_support_module.RuntimeSupportController.empty_worker_snapshot()
        service._ensure_auto_input_helper_process = MagicMock()
        service._refresh_auto_input_snapshot = MagicMock()
        configure_auto_policy(service)
        composed = _compose_helper_service(service)
        composed.runtime.initialize_worker_state()
        if not background_runtime:
            composed.controllers.runtime.shelly.start_io_worker = MagicMock()
            composed.controllers.runtime.auto_input.ensure_helper_process = MagicMock()
            composed.controllers.runtime.auto_input.refresh_snapshot = MagicMock()
        return composed

    @staticmethod
    def _set_worker_snapshot(service: object, **overrides: object) -> None:
        snapshot = runtime_support_module.RuntimeSupportController.empty_worker_snapshot()
        snapshot.update(overrides)
        setattr(service, "_worker_snapshot", snapshot)

__all__ = [name for name in globals() if not name.startswith("__")]

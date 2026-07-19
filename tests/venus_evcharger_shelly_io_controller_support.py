# SPDX-License-Identifier: GPL-3.0-or-later
import tempfile
import threading
import unittest
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import requests

from venus_evcharger.backend.modbus_transport import ModbusSlaveOfflineError
from venus_evcharger.backend.shelly_io import (
    ShellyIoController as _RuntimeShellyIoController,
    _phase_currents_for_selection,
    _phase_powers_for_selection,
    _single_phase_vector,
)
from venus_evcharger.backend.shelly_io_capabilities import ShellyCapabilities
from venus_evcharger.backend.shelly_io_requests import ShellyRequestClient
from venus_evcharger.backend.shelly_io_runtime import ShellyChargerRuntime
from venus_evcharger.backend.shelly_io_runtime_cache import ShellyRuntimeCache
from venus_evcharger.backend.shelly_io_split import ShellyBackendReadback
from venus_evcharger.backend.shelly_io_worker import ShellyWorker
from venus_evcharger.backend.shelly_io_worker_lifecycle import ShellyWorkerLifecycle
from venus_evcharger.backend.shelly_io_worker_transport import ShellyWorkerTransport
from venus_evcharger.backend.smartevse_charger import SmartEvseChargerBackend
from venus_evcharger.backend.models import (
    BackendMode,
    BackendRuntimeSummary,
    ChargerState,
    MeterReading,
    SwitchState,
)
from venus_evcharger.readback_store import InMemoryReadbackStore
from venus_evcharger.ports.readback import TimedChargerState


def ShellyIoController(service: object) -> _RuntimeShellyIoController:
    """Build the production composition root around a complete test service port."""
    if not callable(getattr(service, "time_now", None)):
        setattr(service, "time_now", lambda: 0.0)
    defaults: dict[str, object] = {
        "_readback_store": InMemoryReadbackStore(),
        "session": MagicMock(),
        "use_digest_auth": False,
        "username": "",
        "password": "",
        "host": "127.0.0.1",
        "shelly_request_timeout_seconds": 2.0,
        "pm_component": "switch",
        "pm_id": 0,
        "_worker_poll_interval_seconds": 1.0,
        "_worker_stop_event": threading.Event(),
        "_worker_thread": None,
        "_worker_session": MagicMock(),
        "auto_shelly_soft_fail_seconds": 10.0,
        "relay_sync_timeout_seconds": 2.0,
        "supported_phase_selections": ("P1",),
        "requested_phase_selection": "P1",
        "active_phase_selection": "P1",
        "_last_pm_status": None,
        "_last_pm_status_at": None,
        "_last_pm_status_confirmed": False,
        "_last_voltage": None,
        "_last_switch_feedback_closed": None,
        "_last_switch_interlock_ok": None,
        "_last_switch_feedback_at": None,
        "_relay_command_lock": threading.Lock(),
        "_pending_relay_state": None,
        "_pending_relay_requested_at": None,
        "_relay_sync_expected_state": None,
        "_relay_sync_requested_at": None,
        "_relay_sync_deadline_at": None,
        "_relay_sync_failure_reported": False,
        "_shelly_state": "unknown",
        "_shelly_last_error_reason": None,
        "_shelly_last_error_detail": None,
        "_shelly_last_error_at": None,
        "_shelly_consecutive_errors": 0,
        "_shelly_retry_after": 0.0,
        "_shelly_offline_since": None,
        "_shelly_last_ok_at": None,
        "_shelly_session_reset_count": 0,
        "virtual_mode": 0,
        "virtual_startstop": 0,
        "virtual_enable": 0,
        "virtual_set_current": 0.0,
        "_last_charger_state_enabled": None,
        "_last_charger_state_current_amps": None,
        "_last_charger_state_phase_selection": None,
        "_last_charger_state_actual_current_amps": None,
        "_last_charger_state_power_w": None,
        "_last_charger_state_energy_kwh": None,
        "_last_charger_state_status": None,
        "_last_charger_state_fault": None,
        "_last_charger_state_at": None,
        "_last_charger_estimate_source": None,
        "_last_charger_estimate_at": None,
        "_charger_estimated_energy_kwh": None,
        "_charger_estimated_energy_at": None,
        "_charger_estimated_power_w": None,
        "_last_charger_transport_reason": None,
        "_last_charger_transport_source": None,
        "_last_charger_transport_detail": None,
        "_last_charger_transport_at": None,
        "_charger_retry_reason": None,
        "_charger_retry_source": None,
        "_charger_retry_until": None,
        "_charger_target_current_amps": None,
        "_charger_target_current_applied_at": None,
        "_source_retry_after": {},
    }
    for name, value in defaults.items():
        if not hasattr(service, name):
            setattr(service, name, value)
    if not hasattr(service, "runtime"):
        setattr(service, "runtime", ShellyRuntimeOperationsHarness(service))
    if not hasattr(service, "auto"):
        setattr(service, "auto", ShellyAutoOperationsHarness())
    return _RuntimeShellyIoController(service)


class ShellyRuntimeOperationsHarness:
    """Observable implementation of the runtime role used by Shelly tests."""

    def __init__(self, service: object) -> None:
        self.service = service
        self.ensure_worker_state = MagicMock()
        self.update_worker_snapshot = MagicMock()
        self.mark_failure = MagicMock()
        self.warning_throttled = MagicMock()
        self.mark_recovery = MagicMock()
        self.worker_snapshot = MagicMock(return_value={})
        self.ensure_auto_input_helper = MagicMock()
        self.source_retry_ready = MagicMock(side_effect=self.retry_ready)
        self.source_retry_remaining = MagicMock(side_effect=self.retry_remaining)
        self.delay_source_retry = MagicMock(side_effect=self.delay_retry)

    def retry_ready(self, source_key: str, now: float) -> bool:
        retry_after = getattr(self.service, "_source_retry_after")
        return float(retry_after.get(source_key, 0.0)) <= float(now)

    def retry_remaining(self, source_key: str, now: float | None = None) -> int:
        current = float(getattr(self.service, "time_now")() if now is None else now)
        retry_after = getattr(self.service, "_source_retry_after")
        return max(0, int(float(retry_after.get(source_key, 0.0)) - current))

    def delay_retry(self, source_key: str, now: float, delay_seconds: float | None = None) -> None:
        delay = 1.0 if delay_seconds is None else float(delay_seconds)
        retry_after = getattr(self.service, "_source_retry_after")
        retry_after[source_key] = float(now) + delay


class ShellyAutoOperationsHarness:
    """Observable implementation of the auto-control role used by Shelly tests."""

    def __init__(self) -> None:
        self.mark_relay_changed = MagicMock()
        self.mode_uses_auto_logic = MagicMock(return_value=False)

__all__ = [
    "ChargerState",
    "MagicMock",
    "json",
    "MeterReading",
    "ModbusSlaveOfflineError",
    "Path",
    "ShellyIoController",
    "ShellyBackendReadback",
    "ShellyCapabilities",
    "ShellyChargerRuntime",
    "ShellyRequestClient",
    "ShellyRuntimeCache",
    "ShellyRuntimeOperationsHarness",
    "ShellyAutoOperationsHarness",
    "ShellyWorker",
    "ShellyWorkerLifecycle",
    "ShellyWorkerTransport",
    "ShellyIoControllerTestBase",
    "SimpleNamespace",
    "SmartEvseChargerBackend",
    "SwitchState",
    "TimedChargerState",
    "requests",
    "_runtime_bundle",
    "_phase_currents_for_selection",
    "_phase_powers_for_selection",
    "_single_phase_vector",
    "patch",
    "tempfile",
    "threading",
]


def _runtime_bundle(
    mode: BackendMode,
    *,
    meter_type: str | None = None,
    switch_type: str | None = None,
    charger_type: str | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        runtime=BackendRuntimeSummary(
            backend_mode=mode,
            meter_type=meter_type,
            meter_config_path=None,
            switch_type=switch_type,
            switch_config_path=None,
            charger_type=charger_type,
            charger_config_path=None,
            topology_configured=mode == "split",
            primary_rpc_configured=False,
        )
    )


class ShellyIoControllerTestBase(unittest.TestCase):
    @staticmethod
    def _write_config(directory: str, content: str) -> str:
        path = Path(directory) / "smartevse-charger.ini"
        path.write_text(content, encoding="utf-8")
        return str(path)

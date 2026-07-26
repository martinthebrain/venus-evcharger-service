# SPDX-License-Identifier: GPL-3.0-or-later
import math
import tempfile
import unittest
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

from tests.support.gateway_pressure import FreshOkGatewayPressurePolicy
from tests.support.update_cycle_roles import install_update_cycle_roles
from tests.venus_evcharger_shelly_io_controller_support import ShellyIoController

from venus_evcharger.backend.factory import build_service_backends
from venus_evcharger.backend.config_normalization import normalize_backend_mode
from venus_evcharger.backend.modbus_transport import ModbusRequest, ModbusSlaveOfflineError
from venus_evcharger.backend.models import (
    BackendRuntimeSummary,
    ChargerState,
    SwitchState,
    normalize_phase_selection_or_none,
)
from venus_evcharger.auto.policy import AutoLearnChargePowerPolicy, AutoPhasePolicy, AutoPolicy
from venus_evcharger.runtime.setup_support import initialize_victron_balance_runtime_state
from venus_evcharger.readback_store import InMemoryReadbackStore
from venus_evcharger.service.control_state_config import _VICTRON_BIAS_FIELDS
from venus_evcharger.update.controller import UpdateCycleController as _RuntimeUpdateCycleController
from venus_evcharger.update.input_cache import InputCacheResolver
from venus_evcharger.update.learning import LearningController
from venus_evcharger.update.offline_publish import OfflinePublisher
from venus_evcharger.update.pm_snapshot import PmSnapshotResolver
from venus_evcharger.update.readback_resolver import ReadbackResolver
from venus_evcharger.update.relay import (
    PHASE_SWITCH_STABILIZING_STATE,
    PHASE_SWITCH_WAITING_STATE,
    RelayComponents,
    build_relay_foundation,
    complete_relay_components,
)
from venus_evcharger.update.relay_charger_current import ChargerTargetController
from venus_evcharger.update.relay_charger_current_targets import ChargerCurrentTargetPolicy
from venus_evcharger.update.relay_charger_health import ChargerHealthMonitor
from venus_evcharger.update.relay_charger_transport import ChargerTransportTracker
from venus_evcharger.update.relay_phase_decision import AutoPhaseTargetSelector
from venus_evcharger.update.relay_phase_publish import RelayTelemetry
from venus_evcharger.update.relay_phase_switch_mismatch import PhaseSwitchMismatchMonitor
from venus_evcharger.update.relay_phase_switch_policy import AutoPhaseSwitchController
from venus_evcharger.update.relay_phase_switch_runtime import PhaseSwitchCoordinator
from venus_evcharger.update.relay_phase_switch_runtime_recovery import PhaseSwitchRecovery
from venus_evcharger.update.relay_status_publish import RelayStatusPublisher
from venus_evcharger.update.runtime_cycle import RuntimeCycleCoordinator
from venus_evcharger.update.runtime_cycle_warnings import (
    blocking_charger_health_warning_spec,
    switch_feedback_warning_spec,
)
from venus_evcharger.update.software_update_controller import SoftwareUpdateController
from venus_evcharger.update.state import UpdateStateController
from venus_evcharger.ports.readback import TimedChargerState, TimedSwitchState


def _phase_values(total_power, voltage, _phase, _voltage_mode):
    current = (total_power / voltage) if voltage else 0.0
    return {
        "L1": {"power": total_power, "voltage": voltage, "current": current},
        "L2": {"power": 0.0, "voltage": voltage, "current": 0.0},
        "L3": {"power": 0.0, "voltage": voltage, "current": 0.0},
    }


def utc_timestamp(year: int, month: int, day: int, hour: int, minute: int = 0) -> float:
    return datetime(year, month, day, hour, minute, tzinfo=timezone.utc).timestamp()


def _learning_policy(
    *,
    enabled: bool = True,
    reference_power_watts: float = 1900.0,
    min_watts: float = 500.0,
    alpha: float = 0.2,
    start_delay_seconds: float = 30.0,
    window_seconds: float = 180.0,
    max_age_seconds: float = 21600.0,
) -> AutoPolicy:
    return AutoPolicy(
        learn_charge_power=AutoLearnChargePowerPolicy(
            enabled=enabled,
            reference_power_watts=reference_power_watts,
            min_watts=min_watts,
            alpha=alpha,
            start_delay_seconds=start_delay_seconds,
            window_seconds=window_seconds,
            max_age_seconds=max_age_seconds,
        )
    )


def _phase_policy(
    *,
    enabled: bool = True,
    upshift_delay_seconds: float = 120.0,
    downshift_delay_seconds: float = 30.0,
    upshift_headroom_watts: float = 250.0,
    downshift_margin_watts: float = 150.0,
    mismatch_retry_seconds: float = 300.0,
    mismatch_lockout_count: int = 3,
    mismatch_lockout_seconds: float = 1800.0,
    prefer_lowest_phase_when_idle: bool = True,
) -> AutoPolicy:
    return AutoPolicy(
        phase=AutoPhasePolicy(
            enabled=enabled,
            upshift_delay_seconds=upshift_delay_seconds,
            downshift_delay_seconds=downshift_delay_seconds,
            upshift_headroom_watts=upshift_headroom_watts,
            downshift_margin_watts=downshift_margin_watts,
            mismatch_retry_seconds=mismatch_retry_seconds,
            mismatch_lockout_count=mismatch_lockout_count,
            mismatch_lockout_seconds=mismatch_lockout_seconds,
            prefer_lowest_phase_when_idle=prefer_lowest_phase_when_idle,
        )
    )


class UpdateCycleController(_RuntimeUpdateCycleController):
    """Production controller with the runtime initialization contract applied to test doubles."""

    def __init__(self, service: Any, phase_values_func: Any, health_code_func: Any) -> None:
        if not hasattr(service, "auto_policy"):
            service.auto_policy = AutoPolicy()
        install_update_cycle_roles(service)
        initialize_victron_test_service(service)
        sync_readback_test_service(service)
        super().__init__(service, phase_values_func, health_code_func)


def sync_readback_test_service(service: Any) -> None:
    """Install explicit immutable readbacks for one update-controller scenario."""
    if not hasattr(service, "time_now"):
        service.time_now = lambda: 0.0
    if not hasattr(service, "_readback_store"):
        service._readback_store = InMemoryReadbackStore()
    if not hasattr(service, "_worker_poll_interval_seconds"):
        service._worker_poll_interval_seconds = 1.0
    if not hasattr(service, "auto_shelly_soft_fail_seconds"):
        service.auto_shelly_soft_fail_seconds = 10.0
    service._readback_resolver = ReadbackResolver(service._readback_store, service, service.time_now)
    service._readback_store.replace_charger(_test_charger_snapshot(service))
    service._readback_store.replace_switch(_test_switch_snapshot(service))


def relay_components_for_test(service: Any) -> RelayComponents:
    """Build the real relay component graph around one canonical test fixture."""
    install_update_cycle_roles(service)
    if not hasattr(service, "auto_policy"):
        service.auto_policy = AutoPolicy()
    sync_readback_test_service(service)
    foundation = build_relay_foundation(_phase_values)
    return complete_relay_components(foundation, MagicMock())


def sync_backend_runtime_test_service(service: Any) -> None:
    """Attach the normalized backend-selection contract used by the factory."""
    def configured_path(name: str) -> Path | None:
        value = str(getattr(service, name, "") or "").strip()
        return Path(value) if value else None

    def backend_type(name: str) -> str | None:
        value = str(getattr(service, name, "") or "").strip()
        return None if value in {"", "none"} else value

    service._backend_runtime_summary = BackendRuntimeSummary(
        backend_mode=normalize_backend_mode(getattr(service, "backend_mode", "split")),
        meter_type=backend_type("meter_backend_type"),
        meter_config_path=configured_path("meter_backend_config_path"),
        switch_type=backend_type("switch_backend_type"),
        switch_config_path=configured_path("switch_backend_config_path"),
        charger_type=backend_type("charger_backend_type"),
        charger_config_path=configured_path("charger_backend_config_path"),
        topology_configured=True,
        primary_rpc_configured=False,
    )


def _test_charger_snapshot(service: Any) -> TimedChargerState | None:
    captured_at = getattr(service, "_last_charger_state_at", None)
    if getattr(service, "_charger_backend", None) is None or not isinstance(captured_at, (int, float)):
        return None
    phase = normalize_phase_selection_or_none(getattr(service, "_last_charger_state_phase_selection", None))
    state = ChargerState(
        enabled=_optional_test_bool(getattr(service, "_last_charger_state_enabled", None)),
        current_amps=_optional_test_float(getattr(service, "_last_charger_state_current_amps", None)),
        phase_selection=phase,
        actual_current_amps=_optional_test_float(
            getattr(service, "_last_charger_state_actual_current_amps", None)
        ),
        power_w=_optional_test_float(getattr(service, "_last_charger_state_power_w", None)),
        energy_kwh=_optional_test_float(getattr(service, "_last_charger_state_energy_kwh", None)),
        status_text=_optional_test_text(getattr(service, "_last_charger_state_status", None)),
        fault_text=_optional_test_text(getattr(service, "_last_charger_state_fault", None)),
    )
    return TimedChargerState(state=state, captured_at=float(captured_at))


def _test_switch_snapshot(service: Any) -> TimedSwitchState | None:
    captured_at = getattr(service, "_last_switch_feedback_at", None)
    if not isinstance(captured_at, (int, float)):
        return None
    feedback = _optional_test_bool(getattr(service, "_last_switch_feedback_closed", None))
    interlock = _optional_test_bool(getattr(service, "_last_switch_interlock_ok", None))
    if feedback is None and interlock is None:
        return None
    state = SwitchState(
        enabled=bool(feedback),
        phase_selection=normalize_phase_selection_or_none(
            getattr(service, "active_phase_selection", None)
        )
        or "P1",
        feedback_closed=feedback,
        interlock_ok=interlock,
    )
    return TimedSwitchState(state=state, captured_at=float(captured_at))


def _optional_test_bool(value: object) -> bool | None:
    return None if value is None else bool(value)


def _optional_test_float(value: object) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _optional_test_text(value: object) -> str | None:
    return None if value is None else str(value)


def initialize_victron_test_service(service: Any) -> None:
    """Apply production config/runtime defaults while preserving explicit scenario state."""
    for field in _VICTRON_BIAS_FIELDS:
        attr_name = field.attr or field.name
        if not hasattr(service, attr_name):
            setattr(service, attr_name, field.default)
    explicit_state = vars(service).copy()
    initialize_victron_balance_runtime_state(service)
    vars(service).update(explicit_state)


class _FakeTemplateResponse:
    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, object]:
        return {}


class _FakeSmartEvseTransport:
    def __init__(self) -> None:
        self.requests: list[ModbusRequest] = []
        self.holding_registers: dict[int, int] = {
            0x0000: 2,
            0x0001: 0,
            0x0002: 16,
            0x0005: 1,
            0x0007: 32,
        }

    def exchange(self, request: ModbusRequest, *, timeout_seconds: float) -> bytes:
        self.requests.append(request)
        if request.function_code == 0x03:
            address = int.from_bytes(request.payload[0:2], "big")
            count = int.from_bytes(request.payload[2:4], "big")
            payload = b"".join(
                int(self.holding_registers.get(address + index, 0)).to_bytes(2, "big")
                for index in range(count)
            )
            return bytes((0x03, len(payload))) + payload
        if request.function_code == 0x06:
            address = int.from_bytes(request.payload[0:2], "big")
            value = int.from_bytes(request.payload[2:4], "big")
            self.holding_registers[address] = value
            return bytes((0x06,)) + request.payload
        raise AssertionError(f"Unexpected Modbus function code {request.function_code}")


class AutoPhaseServiceStub:
    auto_policy: AutoPolicy
    supported_phase_selections: tuple[str, ...]
    requested_phase_selection: str
    active_phase_selection: str
    _last_auto_metrics: dict[str, float]
    min_current: float
    voltage_mode: str
    _phase_selection_requires_pause: Callable[[], bool]
    _peek_pending_relay_command: Callable[[], tuple[bool | None, float | None]]
    auto_shelly_soft_fail_seconds: float
    _worker_poll_interval_seconds: float
    relay_sync_timeout_seconds: float
    _last_confirmed_pm_status: dict[str, object] | None
    _last_confirmed_pm_status_at: float | None
    _phase_switch_pending_selection: str | None
    _phase_switch_state: str | None
    _phase_switch_requested_at: float | None
    _phase_switch_stable_until: float | None
    _phase_switch_resume_relay: bool
    _phase_switch_mismatch_active: bool
    _phase_switch_last_mismatch_selection: str | None
    _phase_switch_last_mismatch_at: float | None
    _auto_phase_target_candidate: str | None
    _auto_phase_target_since: float | None
    time_now: Callable[[], float]

    def __init__(self, **overrides: object) -> None:
        auto_policy = AutoPolicy()
        auto_policy.phase.upshift_delay_seconds = 10.0
        auto_policy.phase.downshift_delay_seconds = 5.0
        auto_policy.phase.upshift_headroom_watts = 250.0
        auto_policy.phase.downshift_margin_watts = 150.0
        auto_policy.phase.mismatch_retry_seconds = 60.0
        self.auto_policy = auto_policy
        self.supported_phase_selections = ("P1", "P1_P2")
        self.requested_phase_selection = "P1"
        self.active_phase_selection = "P1"
        self._last_auto_metrics = {"surplus": 3200.0}
        self.min_current = 6.0
        self.voltage_mode = "phase"
        self._phase_selection_requires_pause = MagicMock(return_value=True)
        self._peek_pending_relay_command = MagicMock(return_value=(None, None))
        self._apply_phase_selection = MagicMock(return_value="P1")
        self._save_runtime_state = MagicMock()
        self._publish_local_pm_status = MagicMock()
        self._warning_throttled = MagicMock()
        self._mark_failure = MagicMock()
        self.auto_shelly_soft_fail_seconds = 10.0
        self._worker_poll_interval_seconds = 1.0
        self.relay_sync_timeout_seconds = 3.0
        self._last_confirmed_pm_status = {"output": False}
        self._last_confirmed_pm_status_at = 99.0
        self._phase_switch_pending_selection = None
        self._phase_switch_state = None
        self._phase_switch_requested_at = None
        self._phase_switch_stable_until = None
        self._phase_switch_resume_relay = False
        self._phase_switch_mismatch_active = False
        self._phase_switch_last_mismatch_selection = None
        self._phase_switch_last_mismatch_at = None
        self._auto_phase_target_candidate = None
        self._auto_phase_target_since = None
        self.time_now = lambda: 100.0
        for name, value in overrides.items():
            setattr(self, name, value)


def _auto_phase_service(**overrides: object) -> AutoPhaseServiceStub:
    return install_update_cycle_roles(AutoPhaseServiceStub(**overrides))


class LearningServiceStub:
    auto_policy: AutoPolicy
    charging_started_at: float | None
    learned_charge_power_watts: float | None
    learned_charge_power_updated_at: float | None
    learned_charge_power_state: str
    learned_charge_power_learning_since: float | None
    learned_charge_power_sample_count: int
    learned_charge_power_phase: str | None
    learned_charge_power_voltage: float | None
    learned_charge_power_signature_mismatch_sessions: int
    learned_charge_power_signature_checked_session_started_at: float | None
    learned_charge_power_confidence: float
    learned_charge_power_stability_score: float
    learned_charge_power_reason: str
    learned_charge_power_detail: str
    phase: str
    max_current: float
    _last_voltage: float
    time_now: Callable[[], float]

    def __init__(self, **overrides: object) -> None:
        self.auto_policy = AutoPolicy()
        self.charging_started_at = 50.0
        self.learned_charge_power_watts = 1900.0
        self.learned_charge_power_updated_at = 90.0
        self.learned_charge_power_state = "stable"
        self.learned_charge_power_learning_since = None
        self.learned_charge_power_sample_count = 3
        self.learned_charge_power_phase = "L1"
        self.learned_charge_power_voltage = 230.0
        self.learned_charge_power_signature_mismatch_sessions = 0
        self.learned_charge_power_signature_checked_session_started_at = None
        self.learned_charge_power_confidence = 1.0
        self.learned_charge_power_stability_score = 1.0
        self.learned_charge_power_reason = "stable"
        self.learned_charge_power_detail = ""
        self.phase = "L1"
        self.max_current = 16.0
        self._last_voltage = 230.0
        self.time_now = lambda: 100.0
        for name, value in overrides.items():
            setattr(self, name, value)


def _learning_service(**overrides: object) -> LearningServiceStub:
    return install_update_cycle_roles(LearningServiceStub(**overrides))


class PhaseSwitchMismatchServiceStub:
    auto_policy: AutoPolicy
    active_phase_selection: str
    requested_phase_selection: str
    _phase_switch_mismatch_active: bool
    _phase_switch_mismatch_counts: dict[str, int]
    _phase_switch_last_mismatch_selection: str | None
    _phase_switch_last_mismatch_at: float | None
    _phase_switch_lockout_selection: str | None
    _phase_switch_lockout_reason: str
    _phase_switch_lockout_at: float | None
    _phase_switch_lockout_until: float | None
    time_now: Callable[[], float]

    def __init__(self, **overrides: object) -> None:
        self.auto_policy = AutoPolicy()
        self.active_phase_selection = "P1"
        self.requested_phase_selection = "P1"
        self._phase_switch_mismatch_active = False
        self._phase_switch_mismatch_counts = {}
        self._phase_switch_last_mismatch_selection = None
        self._phase_switch_last_mismatch_at = None
        self._phase_switch_lockout_selection = None
        self._phase_switch_lockout_reason = ""
        self._phase_switch_lockout_at = None
        self._phase_switch_lockout_until = None
        self.time_now = lambda: 100.0
        for name, value in overrides.items():
            setattr(self, name, value)


def _phase_switch_mismatch_service(**overrides: object) -> PhaseSwitchMismatchServiceStub:
    return install_update_cycle_roles(PhaseSwitchMismatchServiceStub(**overrides))



class UpdateCycleControllerTestBase(unittest.TestCase):
    @staticmethod
    def _write_config(directory: str, filename: str, content: str) -> str:
        path = Path(directory) / filename
        path.write_text(content, encoding="utf-8")
        return str(path)

    @staticmethod
    def _software_update_service(repo_root: str, **overrides: object) -> SimpleNamespace:
        data: dict[str, object] = {
            "software_update_repo_root": repo_root,
            "software_update_install_script": str(Path(repo_root) / "install.sh"),
            "software_update_restart_script": str(Path(repo_root) / "deploy/venus/restart_venus_evcharger_service.sh"),
            "software_update_no_update_file": str(Path(repo_root) / "noUpdate"),
            "software_update_log_path": str(Path(repo_root) / "software-update.log"),
            "software_update_manifest_source": "https://example.invalid/bootstrap_manifest.json",
            "software_update_version_source": "https://example.invalid/version.txt",
            "_software_update_current_version": "",
            "_software_update_available_version": "",
            "_software_update_available": False,
            "_software_update_state": "idle",
            "_software_update_detail": "",
            "_software_update_last_check_at": None,
            "_software_update_last_run_at": None,
            "_software_update_last_result": "",
            "_software_update_process": None,
            "_software_update_process_log_handle": None,
            "_software_update_run_requested_at": None,
            "_software_update_no_update_active": 0,
            "_software_update_next_check_at": None,
            "_software_update_boot_auto_due_at": None,
            "gateway_pressure_policy": FreshOkGatewayPressurePolicy(),
        }
        data.update(overrides)
        return SimpleNamespace(**data)

__all__ = [name for name in globals() if not name.startswith("__")]

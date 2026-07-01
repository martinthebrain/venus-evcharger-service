# SPDX-License-Identifier: GPL-3.0-or-later
import math
import tempfile
import unittest
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from venus_evcharger.backend.factory import build_service_backends
from venus_evcharger.backend.modbus_transport import ModbusRequest, ModbusSlaveOfflineError
from venus_evcharger.backend.shelly_io import ShellyIoController
from venus_evcharger.auto.policy import AutoPolicy
from venus_evcharger.update.controller import UpdateCycleController
from venus_evcharger.update.relay import _UpdateCycleRelay


def _phase_values(total_power, voltage, _phase, _voltage_mode):
    current = (total_power / voltage) if voltage else 0.0
    return {
        "L1": {"power": total_power, "voltage": voltage, "current": current},
        "L2": {"power": 0.0, "voltage": voltage, "current": 0.0},
        "L3": {"power": 0.0, "voltage": voltage, "current": 0.0},
    }


def utc_timestamp(year: int, month: int, day: int, hour: int, minute: int = 0) -> float:
    return datetime(year, month, day, hour, minute, tzinfo=timezone.utc).timestamp()


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
    auto_policy: AutoPolicy | None
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
        for name, value in overrides.items():
            setattr(self, name, value)


def _auto_phase_service(**overrides: object) -> AutoPhaseServiceStub:
    return AutoPhaseServiceStub(**overrides)


class LearningServiceStub:
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
    auto_learn_charge_power_enabled: bool
    auto_learn_charge_power_start_delay_seconds: float
    auto_learn_charge_power_window_seconds: float
    auto_learn_charge_power_max_age_seconds: float
    auto_learn_charge_power_min_watts: float
    auto_learn_charge_power_alpha: float
    phase: str
    max_current: float
    _last_voltage: float

    def __init__(self, **overrides: object) -> None:
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
        self.auto_learn_charge_power_enabled = True
        self.auto_learn_charge_power_start_delay_seconds = 30.0
        self.auto_learn_charge_power_window_seconds = 180.0
        self.auto_learn_charge_power_max_age_seconds = 21600.0
        self.auto_learn_charge_power_min_watts = 500.0
        self.auto_learn_charge_power_alpha = 0.2
        self.phase = "L1"
        self.max_current = 16.0
        self._last_voltage = 230.0
        for name, value in overrides.items():
            setattr(self, name, value)


def _learning_service(**overrides: object) -> LearningServiceStub:
    return LearningServiceStub(**overrides)


class PhaseSwitchMismatchServiceStub:
    auto_policy: object | None
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

    def __init__(self, **overrides: object) -> None:
        self.auto_policy = None
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
        for name, value in overrides.items():
            setattr(self, name, value)


def _phase_switch_mismatch_service(**overrides: object) -> PhaseSwitchMismatchServiceStub:
    return PhaseSwitchMismatchServiceStub(**overrides)



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
        }
        data.update(overrides)
        return SimpleNamespace(**data)

__all__ = [name for name in globals() if not name.startswith("__")]

# SPDX-License-Identifier: GPL-3.0-or-later
import math
import tempfile
import unittest
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


def _auto_phase_service(**overrides):
    auto_policy = AutoPolicy()
    auto_policy.phase.upshift_delay_seconds = 10.0
    auto_policy.phase.downshift_delay_seconds = 5.0
    auto_policy.phase.upshift_headroom_watts = 250.0
    auto_policy.phase.downshift_margin_watts = 150.0
    auto_policy.phase.mismatch_retry_seconds = 60.0
    data = {
        "auto_policy": auto_policy,
        "supported_phase_selections": ("P1", "P1_P2"),
        "requested_phase_selection": "P1",
        "active_phase_selection": "P1",
        "_last_auto_metrics": {"surplus": 3200.0},
        "min_current": 6.0,
        "voltage_mode": "phase",
        "_phase_selection_requires_pause": MagicMock(return_value=True),
        "_peek_pending_relay_command": MagicMock(return_value=(None, None)),
        "_apply_phase_selection": MagicMock(return_value="P1"),
        "_save_runtime_state": MagicMock(),
        "_publish_local_pm_status": MagicMock(),
        "_warning_throttled": MagicMock(),
        "_mark_failure": MagicMock(),
        "auto_shelly_soft_fail_seconds": 10.0,
        "_worker_poll_interval_seconds": 1.0,
        "relay_sync_timeout_seconds": 3.0,
        "_last_confirmed_pm_status": {"output": False},
        "_last_confirmed_pm_status_at": 99.0,
        "_phase_switch_pending_selection": None,
        "_phase_switch_state": None,
        "_phase_switch_requested_at": None,
        "_phase_switch_stable_until": None,
        "_phase_switch_resume_relay": False,
        "_phase_switch_mismatch_active": False,
        "_phase_switch_last_mismatch_selection": None,
        "_phase_switch_last_mismatch_at": None,
        "_auto_phase_target_candidate": None,
        "_auto_phase_target_since": None,
    }
    data.update(overrides)
    return SimpleNamespace(**data)



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

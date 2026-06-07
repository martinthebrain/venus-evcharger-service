# SPDX-License-Identifier: GPL-3.0-or-later
"""Unit tests for the Venus EV charger service helper logic."""

import configparser
import json
import sys
import tempfile
import threading
import unittest
from collections import deque
from datetime import datetime, timezone
from unittest.mock import MagicMock

sys.modules["vedbus"] = MagicMock()
sys.modules["dbus"] = MagicMock()
sys.modules["dbus.mainloop.glib"] = MagicMock()
sys.modules["gi"] = MagicMock()
sys.modules["gi.repository"] = MagicMock()
sys.modules["gi.repository.GLib"] = MagicMock()

import venus_evcharger_service  # noqa: E402
import venus_evcharger.runtime.support as runtime_support_module  # noqa: E402
from venus_evcharger_service import ShellyWallboxService, mode_uses_auto_logic, month_in_ranges, month_window, normalize_mode, normalize_phase, parse_hhmm, phase_values  # noqa: E402


def utc_timestamp(year: int, month: int, day: int, hour: int, minute: int = 0) -> float:
    return datetime(year, month, day, hour, minute, tzinfo=timezone.utc).timestamp()



class ShellyWallboxHelpersTestBase(unittest.TestCase):
    @staticmethod
    def _make_update_service():
        service = ShellyWallboxService.__new__(ShellyWallboxService)
        service.config = configparser.ConfigParser()
        service.config.read_string(
            """
[DEFAULT]
Host=192.168.1.20

[Backends]
Mode=combined
MeterType=shelly_combined
SwitchType=shelly_combined
ChargerType=
"""
        )
        service.poll_interval_ms = 1000
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
        service.last_update = 0
        service.auto_input_cache_seconds = 120
        service.auto_input_snapshot_path = "/tmp/auto-helper.json"
        service.auto_input_helper_restart_seconds = 5
        service.auto_input_helper_stale_seconds = 15
        service.auto_shelly_soft_fail_seconds = 10
        service.auto_watchdog_stale_seconds = 180
        service.auto_watchdog_recovery_seconds = 60
        service.auto_grid_missing_stop_seconds = 60
        service.auto_audit_log = False
        service.auto_manual_override_seconds = 300
        service.manual_override_until = 0.0
        service._auto_mode_cutover_pending = False
        service._ignore_min_offtime_once = False
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
        service.auto_samples = deque()
        service.relay_last_changed_at = None
        service.relay_last_off_at = None
        service._auto_cached_inputs_used = False
        service._worker_snapshot_lock = threading.Lock()
        service._worker_snapshot = ShellyWallboxService._empty_worker_snapshot()
        service._ensure_auto_input_helper_process = MagicMock()
        service._refresh_auto_input_snapshot = MagicMock()
        return service

    @staticmethod
    def _set_worker_snapshot(service, **overrides):
        snapshot = ShellyWallboxService._empty_worker_snapshot()
        snapshot.update(overrides)
        service._worker_snapshot = snapshot

__all__ = [name for name in globals() if not name.startswith("__")]

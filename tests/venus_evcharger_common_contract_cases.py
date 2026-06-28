# SPDX-License-Identifier: GPL-3.0-or-later
import configparser
from datetime import datetime
from pathlib import Path
import tempfile
import unittest
from unittest.mock import mock_open, patch

from venus_evcharger.core.common import (
    _age_seconds,
    _auto_state_code,
    _base_auto_reason,
    _charger_retry_remaining_seconds,
    _charger_transport_health_reason,
    _charger_transport_max_age_seconds,
    _charger_transport_now,
    _charger_transport_retry_delay_seconds,
    _fresh_charger_retry_reason,
    _fresh_charger_retry_source,
    _fresh_charger_retry_until,
    _fresh_charger_transport_detail,
    _fresh_charger_transport_source,
    _derive_auto_state,
    _fresh_charger_transport_timestamp,
    _health_code,
    _normalize_auto_state,
    _normalized_charger_transport_reason,
    _positive_service_float,
    _reason_auto_state,
    _status_label,
    _confirmed_relay_state_max_age_seconds,
    _fresh_confirmed_relay_output,
    normalize_scheduled_enabled_days,
    read_version,
    scheduled_mode_snapshot,
    scheduled_night_window_active,
)
from venus_evcharger.core.contracts import (
    _resolved_snapshot_captured_at,
    cutover_confirmed_off,
    displayable_confirmed_read_timestamp,
    finite_float_or_none,
    non_negative_float_or_none,
    non_negative_int,
    normalized_auto_decision_trace,
    normalized_auto_state_pair,
    normalized_fault_state,
    normalized_scheduled_state_fields,
    normalized_software_update_state_fields,
    normalized_status_source,
    normalized_worker_snapshot,
    normalize_learning_phase,
    normalize_learning_state,
    paired_optional_values,
    sanitized_auto_metrics,
    thresholds_ordered,
    timestamp_age_within,
    timestamp_not_future,
    valid_battery_soc,
    write_failure_is_reversible,
)
from venus_evcharger.core.contracts_snapshot import _confirmed_after_requested


class TestShellyWallboxCommonContracts(unittest.TestCase):
    def test_contract_helpers_cover_numeric_state_pairing_and_time_rules(self):
        self.assertEqual(finite_float_or_none("7.5"), 7.5)
        self.assertEqual(non_negative_float_or_none("8.5"), 8.5)
        self.assertEqual(non_negative_int("-3", 2), 0)
        self.assertEqual(non_negative_int(True, 7), 7)
        self.assertEqual(normalize_learning_state(" stable "), "stable")
        self.assertEqual(normalize_learning_phase("3p"), "3P")
        self.assertTrue(paired_optional_values(None, None))
        self.assertTrue(valid_battery_soc(50.0))
        self.assertTrue(timestamp_not_future(100.5, 100.0, 1.0))
        self.assertTrue(timestamp_age_within(99.0, 100.0, 5.0))
        self.assertFalse(timestamp_age_within(None, 100.0, 5.0))
        self.assertTrue(thresholds_ordered(1850.0, 1350.0))
        self.assertTrue(write_failure_is_reversible(False))
        self.assertEqual(normalized_auto_state_pair(" charging ", 99), ("charging", 3))
        self.assertEqual(normalized_status_source(" charger-status-ready "), "charger-status-ready")
        self.assertEqual(normalized_fault_state("contactor-lockout-open"), ("contactor-lockout-open", 1))
        self.assertEqual(normalized_scheduled_state_fields(True, "night-boost", 99, "night-boost-window", -1, 1), ("night-boost", 4, "night-boost-window", 4, 1))
        self.assertEqual(normalized_software_update_state_fields("available", 1, 1), ("available-blocked", 4, 1, 1))
        self.assertEqual(_resolved_snapshot_captured_at(None, 91.0, None), 91.0)
        worker_snapshot = normalized_worker_snapshot({"captured_at": 100.0, "pm_status": {"output": 1}, "pm_confirmed": True}, now=100.0)
        self.assertEqual(worker_snapshot["pm_captured_at"], 100.0)
        self.assertTrue(worker_snapshot["pm_confirmed"])
        self.assertFalse(cutover_confirmed_off(relay_on=False, pending_state=True, confirmed_output=False, confirmed_at=100.0, requested_at=99.0, now=100.5, max_age_seconds=2.0))

    def test_scheduled_helpers_normalize_days_and_derive_states(self):
        waiting = scheduled_mode_snapshot(datetime(2026, 4, 19, 20, 0), {4: ((7, 30), (19, 30))}, "Mon,Tue,Wed,Thu,Fri", delay_seconds=3600.0, latest_end_time="06:30")
        self.assertEqual(waiting.state, "waiting-fallback")
        night_boost = scheduled_mode_snapshot(datetime(2026, 4, 19, 21, 0), {4: ((7, 30), (19, 30))}, "Mon,Tue,Wed,Thu,Fri", delay_seconds=3600.0, latest_end_time="06:30")
        self.assertEqual(night_boost.state, "night-boost")
        after_end = scheduled_mode_snapshot(datetime(2026, 4, 20, 6, 45), {4: ((7, 30), (19, 30))}, "Mon,Tue,Wed,Thu,Fri", delay_seconds=3600.0, latest_end_time="06:30")
        self.assertEqual(after_end.state, "after-latest-end")
        self.assertTrue(cutover_confirmed_off(relay_on=False, pending_state=None, confirmed_output=False, confirmed_at=100.0, requested_at=99.0, now=100.5, max_age_seconds=2.0))

    def test_formatters_health_code_and_age_seconds(self):
        self.assertEqual(_status_label(None, 2), "Laden")
        self.assertEqual(_health_code("contactor-lockout-welded"), 33)
        self.assertEqual(_auto_state_code("charging"), 3)
        self.assertEqual(_age_seconds(100.0, 105.9), 5)

    def test_charger_transport_and_scheduled_helpers_cover_remaining_policy_paths(self):
        transport_service = type("TransportService", (), {"_worker_poll_interval_seconds": 3.0, "_dbus_live_publish_interval_seconds": 2.0, "auto_shelly_soft_fail_seconds": 12.0, "_last_charger_transport_at": "bad", "_time_now": staticmethod(lambda: "bad"), "auto_dbus_backoff_base_seconds": 4.0})()
        self.assertEqual(_charger_transport_max_age_seconds(transport_service), 2.0)
        with patch("venus_evcharger.core.common.time.time", return_value=123.0):
            self.assertEqual(_charger_transport_now(transport_service), 123.0)
        self.assertIsNone(_fresh_charger_transport_timestamp(transport_service, 100.0))
        self.assertEqual(_charger_transport_retry_delay_seconds(transport_service, "offline"), 16.0)
        self.assertEqual(normalize_scheduled_enabled_days("all"), (0, 1, 2, 3, 4, 5, 6))
        self.assertTrue(scheduled_night_window_active(datetime(2026, 4, 19, 21, 0), {4: ((7, 30), (19, 30))}, delay_seconds=3600.0))

    def test_read_version_success_and_missing_file(self):
        with patch("builtins.open", mock_open(read_data="version: 1.2.3\n")):
            self.assertEqual(read_version("version.txt"), "1.2.3")
        with patch("builtins.open", side_effect=FileNotFoundError):
            self.assertEqual(read_version("missing.txt"), "0.1")

    def test_read_version_prefers_service_root_version_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            module_dir = root / "venus_evcharger" / "core"
            module_dir.mkdir(parents=True)
            (root / "version.txt").write_text("version: 2.3.4\n", encoding="utf-8")
            fake_module_file = module_dir / "common_values.py"
            with patch("venus_evcharger.core.common_values.__file__", str(fake_module_file)):
                self.assertEqual(read_version("version.txt"), "2.3.4")

    def test_common_auto_helpers_cover_transport_retry_and_confirmed_relay_edges(self):
        service = type(
            "TransportService",
            (),
            {
                "_worker_poll_interval_seconds": 3.0,
                "_dbus_live_publish_interval_seconds": 2.0,
                "auto_shelly_soft_fail_seconds": 12.0,
                "_last_charger_transport_at": "99.5",
                "_last_charger_transport_reason": "response",
                "_last_charger_transport_source": "poll",
                "_last_charger_transport_detail": "retry later",
                "_time_now": staticmethod(lambda: 100.0),
                "auto_dbus_backoff_base_seconds": 4.0,
                "_charger_retry_until": 101.2,
                "_charger_retry_reason": "busy",
                "_charger_retry_source": "native",
                "_last_confirmed_pm_status": None,
                "_last_confirmed_pm_status_at": None,
                "_last_pm_status_confirmed": True,
                "_last_pm_status": {"output": True},
                "_last_pm_status_at": 99.0,
                "relay_sync_timeout_seconds": 2.5,
            },
        )()

        self.assertEqual(_normalize_auto_state(" strange "), "idle")
        self.assertEqual(_base_auto_reason("grid-missing-cached"), "grid-missing")
        self.assertEqual(_reason_auto_state("unknown"), None)
        self.assertEqual(_normalized_charger_transport_reason(" BUSY "), "busy")
        self.assertEqual(_charger_transport_health_reason(" timeout "), "charger-transport-timeout")
        self.assertEqual(_positive_service_float(service, "auto_dbus_backoff_base_seconds"), 4.0)
        self.assertIsNone(_positive_service_float(service, "missing_attr"))
        self.assertEqual(_fresh_charger_transport_source(service, 100.0), "poll")
        self.assertEqual(_fresh_charger_transport_detail(service, 100.0), "retry later")
        self.assertEqual(_fresh_charger_retry_until(service, 100.0), 101.2)
        self.assertEqual(_fresh_charger_retry_reason(service, 100.0), "busy")
        self.assertEqual(_fresh_charger_retry_source(service, 100.0), "native")
        self.assertEqual(_charger_retry_remaining_seconds(service, 100.0), 2)
        self.assertEqual(_charger_transport_retry_delay_seconds(service, "unknown"), 8.0)
        self.assertTrue(_fresh_confirmed_relay_output(service, 100.0))

    def test_outward_and_snapshot_contract_helpers_cover_remaining_normalization_paths(self):
        self.assertEqual(normalized_software_update_state_fields("available-blocked", 0, 0), ("up-to-date", 2, 0, 0))
        self.assertEqual(normalized_software_update_state_fields("running", 1, 1), ("running", 5, 1, 1))
        self.assertEqual(sanitized_auto_metrics(None), {})
        metrics = sanitized_auto_metrics(
            {
                "surplus": 1000.0,
                "grid": -200.0,
                "soc": 55.0,
                "profile": "high-soc",
                "start_threshold": 1200.0,
                "stop_threshold": 1400.0,
                "learned_charge_power": 2400.0,
                "learned_charge_power_state": "stable",
            }
        )
        self.assertIsNone(metrics["start_threshold"])
        self.assertIsNone(metrics["stop_threshold"])
        self.assertEqual(_resolved_snapshot_captured_at(None, None, None), 0.0)
        worker_snapshot = normalized_worker_snapshot(
            {
                "captured_at": 110.0,
                "pm_status": {"output": True},
                "pm_captured_at": 115.0,
                "pm_confirmed": True,
            },
            now=100.0,
            clamp_future_timestamps=True,
        )
        self.assertIsNone(worker_snapshot["pm_status"])
        self.assertFalse(worker_snapshot["pm_confirmed"])
        worker_snapshot = normalized_worker_snapshot(
            {"pm_status": {"output": True}, "pm_confirmed": True},
            now=None,
        )
        self.assertEqual(worker_snapshot["captured_at"], 0.0)
        worker_snapshot = normalized_worker_snapshot(
            configparser.ConfigParser({"captured_at": "5.0"})["DEFAULT"],
            now=6.0,
        )
        self.assertEqual(worker_snapshot["captured_at"], 5.0)
        worker_snapshot = normalized_worker_snapshot(
            {"captured_at": 5.0, "pm_status": "bad", "pm_confirmed": True},
            now=6.0,
        )
        self.assertIsNone(worker_snapshot["pm_status"])
        self.assertFalse(worker_snapshot["pm_confirmed"])
        self.assertFalse(cutover_confirmed_off(
            relay_on=False,
            pending_state=None,
            confirmed_output=False,
            confirmed_at=90.0,
            requested_at=91.0,
            now=100.0,
            max_age_seconds=2.0,
        ))
        self.assertFalse(_confirmed_after_requested("bad", 91.0))

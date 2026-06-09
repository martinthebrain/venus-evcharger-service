# SPDX-License-Identifier: GPL-3.0-or-later
import configparser
import unittest
from unittest.mock import patch

from venus_evcharger.core.common import (
    _charger_transport_now,
    _derive_auto_state,
    mode_uses_auto_logic,
    month_in_ranges,
    month_window,
    normalize_mode,
    normalize_phase,
    parse_hhmm,
    phase_values,
)
from venus_evcharger.core.common_types import _a, _kwh, _status_label, _v, _w
from venus_evcharger.core.common_schedule import (
    _normalized_weekday_candidates,
    _weekday_indices_for_token,
    normalize_hhmm_text,
    normalize_scheduled_enabled_days,
    scheduled_enabled_days_text,
)


class TestShellyWallboxCommonRuntime(unittest.TestCase):
    def test_auto_state_helpers_cover_normalization_and_reason_mapping(self):
        self.assertEqual(_derive_auto_state("init"), "idle")
        self.assertEqual(_derive_auto_state("waiting-surplus"), "waiting")
        self.assertEqual(_derive_auto_state("manual-override"), "blocked")
        self.assertEqual(_derive_auto_state("grid-missing"), "recovery")
        self.assertEqual(_derive_auto_state("running", relay_on=True, learned_charge_power_state="stable"), "charging")
        self.assertEqual(_derive_auto_state("mystery"), "idle")

    def test_confirmed_relay_helpers_cover_invalid_age_and_freshness(self):
        fresh_service = type("FreshService", (), {"auto_shelly_soft_fail_seconds": 10.0, "_last_confirmed_pm_status": {"output": True}, "_last_confirmed_pm_status_at": 95.0})()
        invalid_service = type("InvalidService", (), {"auto_shelly_soft_fail_seconds": "bad", "_worker_poll_interval_seconds": "bad", "relay_sync_timeout_seconds": "bad"})()
        self.assertEqual(__import__("venus_evcharger.core.common", fromlist=["_confirmed_relay_state_max_age_seconds"])._confirmed_relay_state_max_age_seconds(invalid_service), 5.0)
        self.assertTrue(__import__("venus_evcharger.core.common", fromlist=["_fresh_confirmed_relay_output"])._fresh_confirmed_relay_output(fresh_service, 100.0))

    def test_phase_values_covers_single_phase_and_invalid_three_phase_voltage(self):
        values = phase_values(2300, 230, "L2", "phase")
        self.assertEqual(values["L2"]["power"], 2300.0)
        values = phase_values(3000, 400, "3P", "line")
        self.assertAlmostEqual(values["L2"]["power"], 1000.0)
        with self.assertRaises(ValueError):
            phase_values(3000, 0, "3P", "phase")
        with self.assertRaises(ValueError):
            phase_values(2300, 0, "L1", "phase")

    def test_normalizers_and_time_helpers_cover_fallbacks(self):
        self.assertEqual(normalize_phase("1P"), "L1")
        self.assertEqual(normalize_phase("L2"), "L2")
        with self.assertRaises(ValueError):
            normalize_phase("invalid")
        self.assertEqual(normalize_mode("2"), 2)
        self.assertTrue(mode_uses_auto_logic(1))
        self.assertEqual(parse_hhmm("07:30", (8, 0)), (7, 30))
        self.assertTrue(month_in_ranges(3, [(1, 4)]))
        parser = configparser.ConfigParser()
        parser.read_dict({"DEFAULT": {"AutoJanStart": "06:15", "AutoJanEnd": "17:45"}})
        self.assertEqual(month_window(parser, 1, "08:00", "18:00"), ((6, 15), (17, 45)))

    def test_common_schedule_helpers_cover_empty_ranges_wraps_and_fallbacks(self):
        self.assertEqual(_normalized_weekday_candidates(None), [])
        self.assertEqual(_normalized_weekday_candidates(" Mon-Fri / Sat "), ["mon-fri", "sat"])
        self.assertEqual(_weekday_indices_for_token("fri-mon"), [4, 5, 6, 0])
        self.assertEqual(_weekday_indices_for_token("bogus"), [])
        self.assertEqual(_weekday_indices_for_token("mon-bogus"), [])
        self.assertEqual(normalize_scheduled_enabled_days("", (1, 2)), (1, 2))
        self.assertEqual(normalize_scheduled_enabled_days("weekend"), (5, 6))
        self.assertEqual(normalize_scheduled_enabled_days(["mon", "wed", "mon"]), (0, 2))
        self.assertEqual(scheduled_enabled_days_text("mon-fri"), "Mon,Tue,Wed,Thu,Fri")
        self.assertEqual(normalize_hhmm_text("25:99", "06:30"), "06:30")

    def test_common_type_formatters_cover_value_helpers(self):
        self.assertEqual(_kwh(None, 1.234), "1.23")
        self.assertEqual(_a(None, 12.34), "12.3A")
        self.assertEqual(_w(None, 456.78), "456.8W")
        self.assertEqual(_v(None, 229.94), "229.9V")
        self.assertEqual(_status_label(None, 99), "Unbekannt")

    def test_common_auto_now_helper_ignores_boolean_time_callback_values(self):
        service = type("BoolTimeService", (), {"_time_now": staticmethod(lambda: True)})()
        with patch("venus_evcharger.core.common_auto.time.time", return_value=123.0):
            self.assertEqual(_charger_transport_now(service), 123.0)

    def test_common_auto_now_helper_falls_back_when_service_has_no_time_callback(self):
        service = type("NoTimeService", (), {})()
        with patch("venus_evcharger.core.common_auto.time.time", return_value=321.0):
            self.assertEqual(_charger_transport_now(service), 321.0)

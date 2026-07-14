# SPDX-License-Identifier: GPL-3.0-or-later
"""Boundary contracts for Auto-state, transport, and relay freshness helpers."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from venus_evcharger.core.common_auto import (
    _age_seconds,
    _auto_state_code,
    _base_auto_reason,
    _charger_retry_remaining_seconds,
    _charger_transport_health_reason,
    _charger_transport_max_age_seconds,
    _charger_transport_now,
    _charger_transport_retry_delay_seconds,
    _confirmed_relay_output_value,
    _confirmed_relay_sample,
    _confirmed_relay_sample_fresh,
    _confirmed_relay_sample_valid,
    _confirmed_relay_state_max_age_seconds,
    _derive_auto_state,
    _evse_fault_reason,
    _fresh_charger_retry_reason,
    _fresh_charger_retry_source,
    _fresh_charger_retry_until,
    _fresh_charger_transport_detail,
    _fresh_charger_transport_reason,
    _fresh_charger_transport_source,
    _fresh_charger_transport_timestamp,
    _fresh_confirmed_relay_output,
    _fresh_confirmed_relay_sample,
    _health_code,
    _normalize_auto_state,
    _normalized_charger_transport_reason,
    _normalized_learning_hint,
    _normalized_text_attr,
    _positive_service_float,
    _reason_auto_state,
    _reason_resolved_auto_state,
    _relay_on_auto_state,
)


class TestCoreCommonAutoContracts(unittest.TestCase):
    def test_health_and_auto_state_normalization_contract(self) -> None:
        self.assertEqual(_health_code("init"), 0)
        self.assertEqual(_health_code("not-configured"), 41)
        self.assertEqual(_health_code("unknown"), 99)
        self.assertEqual(_normalize_auto_state(None), "idle")
        self.assertEqual(_normalize_auto_state(" CHARGING "), "charging")
        self.assertEqual(_normalize_auto_state("invalid"), "idle")
        self.assertEqual(_auto_state_code("waiting"), 1)
        self.assertEqual(_auto_state_code("invalid"), 0)

    def test_reason_and_learning_normalization_contract(self) -> None:
        self.assertEqual(_base_auto_reason(None), "init")
        self.assertEqual(_base_auto_reason("inputs-cached"), "inputs")
        self.assertEqual(_base_auto_reason("cached-cached"), "cached")
        self.assertEqual(_evse_fault_reason("charger-fault-cached"), "charger-fault")
        self.assertIsNone(_evse_fault_reason("grid-missing"))
        self.assertEqual(_normalized_learning_hint(None), "unknown")
        self.assertEqual(_normalized_learning_hint(" LEARNING "), "learning")
        self.assertEqual(_relay_on_auto_state("learning"), "learning")
        self.assertEqual(_relay_on_auto_state("stable"), "charging")

    def test_reason_state_priority_contract(self) -> None:
        expected = {
            "init": "idle",
            "grid-missing": "recovery",
            "manual-override": "blocked",
            "running": "charging",
            "waiting-surplus": "waiting",
        }
        for reason, state in expected.items():
            with self.subTest(reason=reason):
                self.assertEqual(_reason_auto_state(reason), state)
        self.assertIsNone(_reason_auto_state("unmapped"))
        self.assertEqual(_reason_resolved_auto_state("charging", True, "learning"), "learning")
        self.assertEqual(_reason_resolved_auto_state("charging", False, "learning"), "charging")
        self.assertEqual(_reason_resolved_auto_state("waiting", True, "learning"), "waiting")

    def test_derive_auto_state_contract(self) -> None:
        cases = (
            ("running", False, None, "charging"),
            ("running", True, "learning", "learning"),
            ("waiting", True, "learning", "waiting"),
            ("unmapped", True, "learning", "learning"),
            ("unmapped", True, "stable", "charging"),
            ("unmapped", False, "learning", "idle"),
        )
        for reason, relay_on, learned, expected in cases:
            with self.subTest(reason=reason, relay_on=relay_on, learned=learned):
                self.assertEqual(
                    _derive_auto_state(reason, relay_on=relay_on, learned_charge_power_state=learned),
                    expected,
                )

    def test_transport_reason_contract(self) -> None:
        for reason in ("busy", "ownership", "timeout", "offline", "response", "error"):
            with self.subTest(reason=reason):
                self.assertEqual(_normalized_charger_transport_reason(f" {reason.upper()} "), reason)
                self.assertEqual(_charger_transport_health_reason(reason), f"charger-transport-{reason}")
        for invalid in (None, "", "other"):
            with self.subTest(invalid=invalid):
                self.assertIsNone(_normalized_charger_transport_reason(invalid))
                self.assertIsNone(_charger_transport_health_reason(invalid))

    def test_positive_service_float_contract(self) -> None:
        service = SimpleNamespace(value="2.5", zero=0, negative=-1, invalid="x", boolean=True)
        self.assertEqual(_positive_service_float(service, "value"), 2.5)
        self.assertEqual(_positive_service_float(service, "boolean"), 1.0)
        self.assertIsNone(_positive_service_float(service, "zero"))
        self.assertIsNone(_positive_service_float(service, "negative"))
        self.assertIsNone(_positive_service_float(service, "invalid"))
        self.assertIsNone(_positive_service_float(service, "missing"))

    def test_normalized_text_attribute_contract(self) -> None:
        service = SimpleNamespace(text=" value ", numeric=12, empty=" ", null=None)
        self.assertEqual(_normalized_text_attr(service, "text"), "value")
        self.assertEqual(_normalized_text_attr(service, "numeric"), "12")
        self.assertIsNone(_normalized_text_attr(service, "empty"))
        self.assertIsNone(_normalized_text_attr(service, "null"))
        self.assertIsNone(_normalized_text_attr(service, "missing"))

    def test_transport_max_age_uses_smallest_positive_candidate(self) -> None:
        self.assertEqual(_charger_transport_max_age_seconds(SimpleNamespace()), 2.0)
        self.assertEqual(
            _charger_transport_max_age_seconds(
                SimpleNamespace(
                    _worker_poll_interval_seconds=0.25,
                    _dbus_live_publish_interval_seconds=0.75,
                    auto_shelly_soft_fail_seconds=0.2,
                )
            ),
            1.0,
        )
        self.assertEqual(
            _charger_transport_max_age_seconds(
                SimpleNamespace(
                    _worker_poll_interval_seconds=0.75,
                    _dbus_live_publish_interval_seconds=4.0,
                    auto_shelly_soft_fail_seconds=9.0,
                )
            ),
            1.5,
        )
        self.assertEqual(
            _charger_transport_max_age_seconds(SimpleNamespace(_dbus_live_publish_interval_seconds=0.75)),
            1.5,
        )
        self.assertEqual(
            _charger_transport_max_age_seconds(SimpleNamespace(auto_shelly_soft_fail_seconds=1.25)),
            1.25,
        )

    def test_transport_now_contract(self) -> None:
        self.assertEqual(_charger_transport_now(SimpleNamespace(), 0), 0.0)
        self.assertEqual(_charger_transport_now(SimpleNamespace(_time_now=lambda: 12), None), 12.0)
        with patch("venus_evcharger.core.common_auto.time.time", return_value=14.5):
            self.assertEqual(_charger_transport_now(SimpleNamespace(_time_now=lambda: True)), 14.5)
            self.assertEqual(_charger_transport_now(SimpleNamespace(_time_now=lambda: "bad")), 14.5)
            self.assertEqual(_charger_transport_now(SimpleNamespace()), 14.5)

    def test_transport_timestamp_freshness_boundaries(self) -> None:
        base = {
            "_worker_poll_interval_seconds": 2.0,
            "_last_charger_transport_reason": "timeout",
            "_last_charger_transport_source": " poll ",
            "_last_charger_transport_detail": " delayed ",
        }
        for timestamp in (98.0, 102.0):
            service = SimpleNamespace(**base, _last_charger_transport_at=timestamp)
            with self.subTest(timestamp=timestamp):
                self.assertEqual(_fresh_charger_transport_timestamp(service, 100.0), timestamp)
        for timestamp in (97.999, 102.001, "bad", None):
            service = SimpleNamespace(**base, _last_charger_transport_at=timestamp)
            with self.subTest(timestamp=timestamp):
                self.assertIsNone(_fresh_charger_transport_timestamp(service, 100.0))
                self.assertIsNone(_fresh_charger_transport_reason(service, 100.0))
                self.assertIsNone(_fresh_charger_transport_source(service, 100.0))
                self.assertIsNone(_fresh_charger_transport_detail(service, 100.0))
        for timestamp in (True, float("nan"), float("inf")):
            service = SimpleNamespace(**base, _last_charger_transport_at=timestamp)
            with self.subTest(timestamp=timestamp):
                self.assertIsNone(_fresh_charger_transport_timestamp(service, 100.0))
        self.assertIsNone(_fresh_charger_transport_timestamp(SimpleNamespace(), 100.0))
        service_clock = SimpleNamespace(
            _last_charger_transport_at=99.0,
            _time_now=lambda: 100.0,
            _worker_poll_interval_seconds=0.75,
        )
        self.assertEqual(_fresh_charger_transport_timestamp(service_clock), 99.0)
        short_window = SimpleNamespace(
            _last_charger_transport_at=98.4,
            _worker_poll_interval_seconds=0.75,
        )
        self.assertIsNone(_fresh_charger_transport_timestamp(short_window, 100.0))
        service = SimpleNamespace(**base, _last_charger_transport_at="99.5")
        self.assertEqual(_fresh_charger_transport_reason(service, 100.0), "timeout")
        self.assertEqual(_fresh_charger_transport_source(service, 100.0), "poll")
        self.assertEqual(_fresh_charger_transport_detail(service, 100.0), "delayed")
        service._last_charger_transport_reason = "invalid"
        service._last_charger_transport_source = " "
        service._last_charger_transport_detail = None
        self.assertIsNone(_fresh_charger_transport_reason(service, 100.0))
        self.assertIsNone(_fresh_charger_transport_source(service, 100.0))
        self.assertIsNone(_fresh_charger_transport_detail(service, 100.0))
        missing_metadata = SimpleNamespace(_last_charger_transport_at=100.0)
        self.assertIsNone(_fresh_charger_transport_reason(missing_metadata, 100.0))

    def test_transport_retry_delay_policy_contract(self) -> None:
        service = SimpleNamespace(auto_dbus_backoff_base_seconds=4.0, auto_shelly_soft_fail_seconds=10.0)
        expected = {
            "busy": 4.0,
            "ownership": 8.0,
            "timeout": 6.0,
            "offline": 16.0,
            "response": 8.0,
            "error": 8.0,
            "unknown": 8.0,
        }
        for reason, delay in expected.items():
            with self.subTest(reason=reason):
                self.assertEqual(_charger_transport_retry_delay_seconds(service, reason), delay)
        low = SimpleNamespace(auto_dbus_backoff_base_seconds=0.25, auto_shelly_soft_fail_seconds=0.5)
        self.assertEqual(_charger_transport_retry_delay_seconds(low, "busy"), 1.0)
        self.assertEqual(_charger_transport_retry_delay_seconds(low, "ownership"), 3.0)
        self.assertEqual(_charger_transport_retry_delay_seconds(low, "timeout"), 2.0)
        self.assertEqual(_charger_transport_retry_delay_seconds(low, "offline"), 10.0)
        self.assertEqual(_charger_transport_retry_delay_seconds(low, "response"), 3.0)
        self.assertEqual(_charger_transport_retry_delay_seconds(low, None), 2.0)
        high_base = SimpleNamespace(auto_dbus_backoff_base_seconds=10.0, auto_shelly_soft_fail_seconds=3.0)
        self.assertEqual(_charger_transport_retry_delay_seconds(high_base, "busy"), 5.0)
        self.assertEqual(_charger_transport_retry_delay_seconds(high_base, "ownership"), 20.0)
        self.assertEqual(_charger_transport_retry_delay_seconds(high_base, "timeout"), 3.0)
        self.assertEqual(_charger_transport_retry_delay_seconds(high_base, "offline"), 40.0)
        self.assertEqual(_charger_transport_retry_delay_seconds(high_base, "response"), 20.0)
        high_soft_fail = SimpleNamespace(auto_dbus_backoff_base_seconds=2.0, auto_shelly_soft_fail_seconds=20.0)
        self.assertEqual(_charger_transport_retry_delay_seconds(high_soft_fail, "timeout"), 3.0)
        self.assertEqual(_charger_transport_retry_delay_seconds(high_soft_fail, "offline"), 20.0)
        threshold = SimpleNamespace(auto_dbus_backoff_base_seconds=0.25, auto_shelly_soft_fail_seconds=2.5)
        self.assertEqual(_charger_transport_retry_delay_seconds(threshold, "timeout"), 2.0)
        defaults = SimpleNamespace()
        self.assertEqual(_charger_transport_retry_delay_seconds(defaults, "busy"), 5.0)
        self.assertEqual(_charger_transport_retry_delay_seconds(defaults, "timeout"), 7.5)
        self.assertEqual(_charger_transport_retry_delay_seconds(defaults, "offline"), 20.0)
        default_soft_fail = SimpleNamespace(auto_dbus_backoff_base_seconds=2.0)
        self.assertEqual(_charger_transport_retry_delay_seconds(default_soft_fail, "offline"), 10.0)
        self.assertEqual(_charger_transport_retry_delay_seconds(low, "error"), 2.0)

    def test_retry_freshness_and_remaining_seconds_contract(self) -> None:
        service = SimpleNamespace(
            _charger_retry_until=101.01,
            _charger_retry_reason="busy",
            _charger_retry_source=" native ",
        )
        self.assertEqual(_fresh_charger_retry_until(service, 100.0), 101.01)
        self.assertEqual(_fresh_charger_retry_reason(service, 100.0), "busy")
        self.assertEqual(_fresh_charger_retry_source(service, 100.0), "native")
        self.assertEqual(_charger_retry_remaining_seconds(service, 100.0), 2)
        service._charger_retry_until = 100.01
        self.assertEqual(_charger_retry_remaining_seconds(service, 100.0), 1)
        service._charger_retry_until = 101.01
        self.assertIsNone(_fresh_charger_retry_until(service, 101.01))
        self.assertIsNone(_fresh_charger_retry_reason(service, 101.01))
        self.assertIsNone(_fresh_charger_retry_source(service, 101.01))
        self.assertEqual(_charger_retry_remaining_seconds(service, 101.01), -1)
        for invalid in (None, "bad", float("nan"), float("inf")):
            service._charger_retry_until = invalid
            with self.subTest(invalid=invalid):
                self.assertIsNone(_fresh_charger_retry_until(service, 100.0))
        self.assertIsNone(_fresh_charger_retry_until(SimpleNamespace(), 100.0))
        service_clock = SimpleNamespace(
            _charger_retry_until=102.0,
            _charger_retry_reason=None,
            _time_now=lambda: 100.0,
        )
        self.assertEqual(_fresh_charger_retry_until(service_clock), 102.0)
        self.assertIsNone(_fresh_charger_retry_reason(service_clock))
        self.assertEqual(_charger_retry_remaining_seconds(service_clock), 2)
        missing_reason = SimpleNamespace(_charger_retry_until=102.0)
        self.assertIsNone(_fresh_charger_retry_reason(missing_reason, 100.0))

    def test_age_seconds_contract(self) -> None:
        self.assertEqual(_age_seconds(None, 100.0), -1)
        self.assertEqual(_age_seconds(99.1, 100.0), 0)
        self.assertEqual(_age_seconds(98.9, 100.0), 1)
        self.assertEqual(_age_seconds(101.0, 100.0), 0)
        with patch("venus_evcharger.core.common_auto.time.time", return_value=105.0):
            self.assertEqual(_age_seconds(100.0), 5)

    def test_confirmed_relay_max_age_contract(self) -> None:
        self.assertEqual(_confirmed_relay_state_max_age_seconds(SimpleNamespace()), 5.0)
        self.assertEqual(
            _confirmed_relay_state_max_age_seconds(
                SimpleNamespace(_worker_poll_interval_seconds=0.25, relay_sync_timeout_seconds=0.2)
            ),
            1.0,
        )
        self.assertEqual(
            _confirmed_relay_state_max_age_seconds(
                SimpleNamespace(_worker_poll_interval_seconds=1.5, relay_sync_timeout_seconds=4.0)
            ),
            3.0,
        )
        self.assertEqual(
            _confirmed_relay_state_max_age_seconds(SimpleNamespace(relay_sync_timeout_seconds=2.5)),
            2.5,
        )

    def test_confirmed_relay_sample_precedence_and_copy_contract(self) -> None:
        confirmed = {"output": True, 1: "value"}
        service = SimpleNamespace(
            _last_confirmed_pm_status=confirmed,
            _last_confirmed_pm_status_at=9.0,
            _last_pm_status_confirmed=True,
            _last_pm_status={"output": False},
            _last_pm_status_at=8.0,
        )
        sample, captured_at = _confirmed_relay_sample(service)
        self.assertEqual(sample, {"output": True, "1": "value"})
        self.assertEqual(captured_at, 9.0)
        self.assertIsNot(sample, confirmed)
        fallback = SimpleNamespace(
            _last_confirmed_pm_status=None,
            _last_confirmed_pm_status_at=None,
            _last_pm_status_confirmed=True,
            _last_pm_status={"output": False},
            _last_pm_status_at=8.0,
        )
        self.assertEqual(_confirmed_relay_sample(fallback), ({"output": False}, 8.0))
        fallback._last_pm_status_confirmed = False
        self.assertEqual(_confirmed_relay_sample(fallback), (None, None))
        fallback._last_confirmed_pm_status = []
        self.assertEqual(_confirmed_relay_sample(fallback), (None, None))
        self.assertEqual(_confirmed_relay_sample(SimpleNamespace()), (None, None))
        missing_legacy_status = SimpleNamespace(
            _last_confirmed_pm_status=None,
            _last_confirmed_pm_status_at=None,
            _last_pm_status_confirmed=True,
            _last_pm_status_at=8.0,
        )
        self.assertEqual(_confirmed_relay_sample(missing_legacy_status), (None, None))
        missing_legacy_time = SimpleNamespace(
            _last_confirmed_pm_status=None,
            _last_confirmed_pm_status_at=None,
            _last_pm_status_confirmed=True,
            _last_pm_status={"output": True},
        )
        self.assertEqual(_confirmed_relay_sample(missing_legacy_time), ({"output": True}, None))

    def test_confirmed_relay_validation_and_freshness_contract(self) -> None:
        self.assertTrue(_confirmed_relay_sample_valid({"output": False}, 0.0))
        self.assertFalse(_confirmed_relay_sample_valid({}, 0.0))
        self.assertFalse(_confirmed_relay_sample_valid({"output": True}, None))
        self.assertFalse(_confirmed_relay_sample_valid(None, 1.0))
        self.assertTrue(_confirmed_relay_sample_fresh(99.0, 100.0, 1.0))
        self.assertTrue(_confirmed_relay_sample_fresh(101.0, 100.0, 1.0))
        self.assertFalse(_confirmed_relay_sample_fresh(98.999, 100.0, 1.0))
        self.assertFalse(_confirmed_relay_sample_fresh(101.001, 100.0, 1.0))
        self.assertTrue(_confirmed_relay_output_value({"output": 1}))
        self.assertFalse(_confirmed_relay_output_value({"output": 0}))
        self.assertFalse(_confirmed_relay_output_value({}))

    def test_fresh_confirmed_relay_sample_contract(self) -> None:
        service = SimpleNamespace(
            _worker_poll_interval_seconds=1.0,
            _last_confirmed_pm_status={"output": True},
            _last_confirmed_pm_status_at=98.0,
        )
        self.assertEqual(_fresh_confirmed_relay_sample(service, 100.0), ({"output": True}, 98.0))
        self.assertTrue(_fresh_confirmed_relay_output(service, 100.0))
        service._last_confirmed_pm_status_at = 97.999
        self.assertIsNone(_fresh_confirmed_relay_sample(service, 100.0))
        self.assertIsNone(_fresh_confirmed_relay_output(service, 100.0))
        service._last_confirmed_pm_status = {"missing": True}
        service._last_confirmed_pm_status_at = 100.0
        self.assertIsNone(_fresh_confirmed_relay_sample(service, 100.0))


if __name__ == "__main__":
    unittest.main()

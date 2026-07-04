# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import math
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from venus_evcharger.publish.dbus_learned import _DbusPublishLearned, _optional_binary_flag_int


class _DbusLearnedHarness(_DbusPublishLearned):
    def __init__(self, service: object) -> None:
        self.service = service


def _service(**overrides: object) -> SimpleNamespace:
    values: dict[str, object] = {
        "display_learned_set_current": 1,
        "virtual_set_current": 16.0,
        "min_current": 6.0,
        "max_current": 16.0,
        "phase": "L1",
        "voltage_mode": "phase",
        "_dbus_live_publish_interval_seconds": 1.0,
        "auto_shelly_soft_fail_seconds": 10.0,
        "auto_learn_charge_power_max_age_seconds": 21600.0,
        "learned_charge_power_state": "stable",
        "learned_charge_power_watts": 2300.0,
        "learned_charge_power_voltage": 230.0,
        "learned_charge_power_phase": "L1",
        "learned_charge_power_updated_at": 100.0,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class DbusPublishLearnedContractTests(unittest.TestCase):
    def test_optional_binary_flag_int_parses_supported_shapes_only(self) -> None:
        self.assertEqual(_optional_binary_flag_int(2), 2)
        self.assertEqual(_optional_binary_flag_int(2.9), 2)
        self.assertEqual(_optional_binary_flag_int("3"), 3)
        self.assertEqual(_optional_binary_flag_int(b"4"), 4)
        self.assertEqual(_optional_binary_flag_int(bytearray(b"5")), 5)
        self.assertIsNone(_optional_binary_flag_int("bad"))
        self.assertIsNone(_optional_binary_flag_int(object()))

    def test_display_uses_learned_set_current_requires_no_native_set_current_backend_and_enabled_flag(self) -> None:
        self.assertTrue(_DbusLearnedHarness(_service())._display_uses_learned_set_current())
        self.assertTrue(
            _DbusLearnedHarness(_service(_charger_backend=SimpleNamespace(read_only=True)))._display_uses_learned_set_current()
        )
        self.assertFalse(
            _DbusLearnedHarness(_service(_charger_backend=SimpleNamespace(set_current=MagicMock())))._display_uses_learned_set_current()
        )
        self.assertFalse(_DbusLearnedHarness(_service(display_learned_set_current=0))._display_uses_learned_set_current())
        self.assertFalse(_DbusLearnedHarness(_service(display_learned_set_current=False))._display_uses_learned_set_current())

        default_service = _service()
        delattr(default_service, "display_learned_set_current")
        self.assertTrue(_DbusLearnedHarness(default_service)._display_uses_learned_set_current())
        self.assertTrue(_DbusLearnedHarness(_service(display_learned_set_current="bad"))._display_uses_learned_set_current())
        self.assertTrue(_DbusLearnedHarness(_service(display_learned_set_current=2))._display_uses_learned_set_current())

    def test_charger_state_freshness_and_readbacks_require_backend_timestamp_and_positive_current(self) -> None:
        harness = _DbusLearnedHarness(
            _service(
                _charger_backend=object(),
                _last_charger_state_at=99.0,
                _last_charger_state_enabled=1,
                _last_charger_state_current_amps=12.5,
                _dbus_live_publish_interval_seconds=3.0,
                auto_shelly_soft_fail_seconds=4.0,
            )
        )

        self.assertEqual(harness._charger_state_max_age_seconds(), 2.0)
        self.assertEqual(
            _DbusLearnedHarness(
                _service(_dbus_live_publish_interval_seconds=0.4, auto_shelly_soft_fail_seconds=10.0)
            )._charger_state_max_age_seconds(),
            1.0,
        )
        self.assertEqual(
            _DbusLearnedHarness(
                _service(_dbus_live_publish_interval_seconds=5.0, auto_shelly_soft_fail_seconds=0.5)
            )._charger_state_max_age_seconds(),
            1.0,
        )
        self.assertEqual(
            _DbusLearnedHarness(_service(_dbus_live_publish_interval_seconds=0.0, auto_shelly_soft_fail_seconds=0.0))
            ._charger_state_max_age_seconds(),
            2.0,
        )
        self.assertEqual(
            _DbusLearnedHarness(_service(_dbus_live_publish_interval_seconds=None, auto_shelly_soft_fail_seconds=None))
            ._charger_state_max_age_seconds(),
            2.0,
        )
        missing_interval = _service(auto_shelly_soft_fail_seconds=10.0)
        delattr(missing_interval, "_dbus_live_publish_interval_seconds")
        self.assertEqual(_DbusLearnedHarness(missing_interval)._charger_state_max_age_seconds(), 2.0)
        missing_soft_fail = _service(_dbus_live_publish_interval_seconds=6.0)
        delattr(missing_soft_fail, "auto_shelly_soft_fail_seconds")
        self.assertEqual(_DbusLearnedHarness(missing_soft_fail)._charger_state_max_age_seconds(), 2.0)

        self.assertTrue(harness._charger_state_fresh(100.5))
        self.assertTrue(harness._charger_state_fresh(101.0))
        self.assertFalse(harness._charger_state_fresh(102.1))
        self.assertTrue(harness._charger_enabled_readback(100.0))
        self.assertEqual(harness._charger_current_readback(100.0), 12.5)

        stale = _DbusLearnedHarness(_service(_charger_backend=None, _last_charger_state_at=100.0))
        self.assertFalse(stale._charger_state_fresh(100.0))
        self.assertIsNone(stale._charger_enabled_readback(100.0))
        missing_state_at = _DbusLearnedHarness(_service(_charger_backend=object()))
        self.assertFalse(missing_state_at._charger_state_fresh(100.0))

        missing_enabled = _service(_charger_backend=object(), _last_charger_state_at=100.0)
        self.assertIsNone(_DbusLearnedHarness(missing_enabled)._charger_enabled_readback(100.0))
        self.assertIsNone(_DbusLearnedHarness(_service(_charger_backend=object(), _last_charger_state_at=100.0))._charger_current_readback(100.0))
        self.assertIsNone(
            _DbusLearnedHarness(
                _service(_charger_backend=object(), _last_charger_state_at=100.0, _last_charger_state_current_amps=0.0)
            )._charger_current_readback(100.0)
        )
        self.assertEqual(
            _DbusLearnedHarness(
                _service(_charger_backend=object(), _last_charger_state_at=100.0, _last_charger_state_current_amps=0.5)
            )._charger_current_readback(100.0),
            0.5,
        )

    def test_charger_text_estimate_transport_and_retry_diagnostics_are_normalized_and_fresh(self) -> None:
        service = _service(
            _last_charger_text="  ready ",
            _last_charger_estimate_source="  meterless ",
            _last_charger_transport_at=99.0,
            _last_charger_transport_reason="timeout",
            _last_charger_transport_source="  charger ",
            _last_charger_transport_detail="  slow reply ",
            _charger_retry_until=120.0,
            _charger_retry_reason="offline",
            _charger_retry_source="  relay ",
            auto_dbus_backoff_max_seconds=20.0,
        )
        harness = _DbusLearnedHarness(service)

        self.assertEqual(harness._charger_text_observed("_last_charger_text"), "ready")
        self.assertEqual(harness._charger_text_observed("_missing_text"), "")
        self.assertEqual(harness._charger_estimate_active(), 1)
        self.assertEqual(harness._charger_estimate_source(), "meterless")
        self.assertEqual(harness._charger_transport_active(100.0), 1)
        self.assertEqual(harness._charger_transport_reason(100.0), "timeout")
        self.assertEqual(harness._charger_transport_source(100.0), "charger")
        self.assertEqual(harness._charger_transport_detail(100.0), "slow reply")
        self.assertEqual(harness._charger_retry_active(100.0), 1)
        self.assertEqual(harness._charger_retry_reason(100.0), "offline")
        self.assertEqual(harness._charger_retry_source(100.0), "relay")

        self.assertEqual(harness._charger_transport_active(200.0), 0)
        self.assertEqual(harness._charger_retry_active(200.0), 0)

    def test_learned_power_expiry_and_allowed_display_state_are_strict(self) -> None:
        self.assertFalse(
            _DbusLearnedHarness(_service(auto_learn_charge_power_max_age_seconds=0.0))._learned_charge_power_expired_for_display(200.0)
        )
        self.assertTrue(
            _DbusLearnedHarness(_service(auto_learn_charge_power_max_age_seconds=1.0))._learned_charge_power_expired_for_display(200.0)
        )
        self.assertTrue(
            _DbusLearnedHarness(_service(learned_charge_power_updated_at=None))._learned_charge_power_expired_for_display(200.0)
        )
        missing_max_age = _service(learned_charge_power_updated_at=100.0)
        delattr(missing_max_age, "auto_learn_charge_power_max_age_seconds")
        self.assertTrue(_DbusLearnedHarness(missing_max_age)._learned_charge_power_expired_for_display(21700.5))

        missing_updated_at = _service()
        delattr(missing_updated_at, "learned_charge_power_updated_at")
        self.assertTrue(_DbusLearnedHarness(missing_updated_at)._learned_charge_power_expired_for_display(200.0))
        self.assertTrue(
            _DbusLearnedHarness(
                _service(learned_charge_power_updated_at=100.0, auto_learn_charge_power_max_age_seconds=50.0)
            )._learned_charge_power_expired_for_display(151.0)
        )
        self.assertFalse(
            _DbusLearnedHarness(
                _service(learned_charge_power_updated_at=100.0, auto_learn_charge_power_max_age_seconds=50.0)
            )._learned_charge_power_expired_for_display(150.0)
        )

        self.assertTrue(_DbusLearnedHarness(_service())._learned_display_current_allowed(101.0))
        self.assertFalse(_DbusLearnedHarness(_service(learned_charge_power_state="learning"))._learned_display_current_allowed(101.0))
        self.assertFalse(_DbusLearnedHarness(_service(display_learned_set_current=0))._learned_display_current_allowed(101.0))
        missing_state = _service()
        delattr(missing_state, "learned_charge_power_state")
        self.assertFalse(_DbusLearnedHarness(missing_state)._learned_display_current_allowed(101.0))

    def test_learned_display_scalar_phase_voltage_rounding_and_clamping_contract(self) -> None:
        harness = _DbusLearnedHarness(_service())

        self.assertEqual(harness._validated_learned_display_scalars(2300.0, 230.0), (2300.0, 230.0))
        self.assertIsNone(harness._validated_learned_display_scalars(0.0, 230.0))
        self.assertIsNone(harness._validated_learned_display_scalars(2300.0, 0.0))
        self.assertEqual(harness._validated_learned_display_scalars(0.5, 0.5), (0.5, 0.5))
        self.assertEqual(harness._learned_display_phase(), "L1")
        fallback_phase_service = _service(phase="3P")
        delattr(fallback_phase_service, "learned_charge_power_phase")
        self.assertEqual(_DbusLearnedHarness(fallback_phase_service)._learned_display_phase(), "3P")
        default_phase_service = _service()
        delattr(default_phase_service, "learned_charge_power_phase")
        delattr(default_phase_service, "phase")
        self.assertEqual(_DbusLearnedHarness(default_phase_service)._learned_display_phase(), "L1")
        invalid_phase_service = _service()
        delattr(invalid_phase_service, "learned_charge_power_phase")
        invalid_phase_service.phase = "bad"
        self.assertIsNone(_DbusLearnedHarness(invalid_phase_service)._learned_display_phase())
        self.assertEqual(harness._raw_learned_display_values(), (2300.0, 230.0, "L1"))
        missing_power = _service()
        delattr(missing_power, "learned_charge_power_watts")
        self.assertIsNone(_DbusLearnedHarness(missing_power)._raw_learned_display_values())
        missing_voltage = _service()
        delattr(missing_voltage, "learned_charge_power_voltage")
        self.assertIsNone(_DbusLearnedHarness(missing_voltage)._raw_learned_display_values())
        self.assertEqual(harness._phase_voltage_for_display_current(400.0, "3P"), 400.0)
        default_voltage_mode = _service()
        delattr(default_voltage_mode, "voltage_mode")
        self.assertEqual(_DbusLearnedHarness(default_voltage_mode)._phase_voltage_for_display_current(400.0, "3P"), 400.0)
        self.assertEqual(_DbusLearnedHarness(_service(voltage_mode=" PHASE "))._phase_voltage_for_display_current(400.0, "3P"), 400.0)
        line_voltage = _DbusLearnedHarness(_service(voltage_mode="line"))
        self.assertAlmostEqual(line_voltage._phase_voltage_for_display_current(400.0, "3P") or 0.0, 400.0 / math.sqrt(3.0))
        self.assertIsNone(harness._phase_voltage_for_display_current(0.0, "L1"))
        self.assertEqual(harness._phase_voltage_for_display_current(0.5, "L1"), 0.5)
        self.assertEqual(harness._rounded_display_current(10.4), 10.0)
        self.assertIsNone(harness._rounded_display_current(0.1))
        self.assertEqual(harness._rounded_display_current(1.0), 1.0)
        self.assertEqual(_DbusLearnedHarness(_service(min_current=6.0, max_current=12.0))._clamped_display_current(20.0), 12.0)
        self.assertEqual(_DbusLearnedHarness(_service(min_current=6.0, max_current=12.0))._clamped_display_current(3.0), 6.0)
        self.assertEqual(_DbusLearnedHarness(_service(min_current=None, max_current=None))._clamped_display_current(3.0), 3.0)
        missing_limits = _service()
        delattr(missing_limits, "min_current")
        delattr(missing_limits, "max_current")
        self.assertEqual(_DbusLearnedHarness(missing_limits)._clamped_display_current(3.0), 3.0)
        self.assertEqual(_DbusLearnedHarness(_service(min_current=None, max_current=0.0))._clamped_display_current(3.0), 3.0)
        self.assertEqual(_DbusLearnedHarness(_service(min_current=None, max_current=1.0))._clamped_display_current(3.0), 1.0)
        self.assertEqual(_DbusLearnedHarness(_service(min_current=None, max_current=2.0))._clamped_display_current(3.0), 2.0)

    def test_display_set_current_prefers_fresh_charger_readback_then_learned_then_virtual(self) -> None:
        native = _DbusLearnedHarness(
            _service(
                _charger_backend=object(),
                _last_charger_state_at=100.0,
                _last_charger_state_current_amps=11.0,
                learned_charge_power_watts=2990.0,
            )
        )
        self.assertEqual(native._display_set_current(100.0), 11.0)

        learned = _DbusLearnedHarness(_service(learned_charge_power_watts=2990.0, learned_charge_power_voltage=230.0))
        self.assertEqual(learned._display_set_current(101.0), 13.0)
        three_phase = _DbusLearnedHarness(
            _service(
                phase="3P",
                learned_charge_power_phase="3P",
                voltage_mode="phase",
                learned_charge_power_watts=20700.0,
                learned_charge_power_voltage=230.0,
                min_current=6.0,
                max_current=32.0,
            )
        )
        self.assertEqual(three_phase._display_set_current(101.0), 30.0)

        fallback = _DbusLearnedHarness(_service(learned_charge_power_state="unknown", virtual_set_current=9.0))
        self.assertEqual(fallback._display_set_current(101.0), 9.0)

    def test_stable_learned_display_inputs_reject_invalid_raw_or_phase_voltage_values(self) -> None:
        self.assertIsNone(_DbusLearnedHarness(_service(learned_charge_power_watts=None))._stable_learned_display_inputs(101.0))
        self.assertIsNone(_DbusLearnedHarness(_service(learned_charge_power_phase="bad"))._stable_learned_display_inputs(101.0))
        self.assertIsNone(_DbusLearnedHarness(_service(learned_charge_power_voltage=0.0))._stable_learned_display_inputs(101.0))
        inputs = _DbusLearnedHarness(_service(learned_charge_power_phase="3P"))._stable_learned_display_inputs(101.0)
        self.assertIsNotNone(inputs)
        assert inputs is not None
        self.assertEqual(inputs.phase_count, 3.0)
        self.assertEqual(inputs.phase_voltage_v, 230.0)
        line_inputs = _DbusLearnedHarness(
            _service(learned_charge_power_phase="3P", learned_charge_power_voltage=400.0, voltage_mode="line")
        )._stable_learned_display_inputs(101.0)
        self.assertIsNotNone(line_inputs)
        assert line_inputs is not None
        self.assertAlmostEqual(line_inputs.phase_voltage_v, 400.0 / math.sqrt(3.0))


if __name__ == "__main__":
    unittest.main()

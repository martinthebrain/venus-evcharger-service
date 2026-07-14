# SPDX-License-Identifier: GPL-3.0-or-later
"""Contracts for the pure Victron ESS learning-profile helpers."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, call

from venus_evcharger.update.victron_ess_balance_learning_profiles_support import (
    _clear_victron_ess_balance_active_profile_state,
    _clear_victron_ess_balance_tracking_episode_state,
    _record_victron_ess_balance_tracking_command,
    _reset_victron_ess_balance_pid_integral_state,
    _reset_victron_ess_balance_pid_state,
    _victron_ess_balance_action_direction_site_regime,
    _victron_ess_balance_active_profile_fields,
    _victron_ess_balance_adaptive_scalar_specs,
    _victron_ess_balance_adaptive_scalar_value,
    _victron_ess_balance_energy_ids,
    _victron_ess_balance_float_attr,
    _victron_ess_balance_forecast_site_regime,
    _victron_ess_balance_grid_site_regime,
    _victron_ess_balance_learning_profile_key,
    _victron_ess_balance_near_charge_limit,
    _victron_ess_balance_near_discharge_limit,
    _victron_ess_balance_prefixed_scalar_metrics,
    _victron_ess_balance_profile_counter,
    _victron_ess_balance_profile_identity,
    _victron_ess_balance_profile_scalar_snapshot,
    _victron_ess_balance_pv_phase,
    _victron_ess_balance_update_profile_sample,
    _victron_ess_balance_update_service_sample,
)


def _optional_float(value: object) -> float | None:
    return float(value) if isinstance(value, (int, float)) else None


class VictronEssBalanceLearningProfilesSupportContracts(unittest.TestCase):
    def test_site_regime_boundaries(self) -> None:
        self.assertEqual(_victron_ess_balance_grid_site_regime(-25.0), "export")
        self.assertEqual(_victron_ess_balance_grid_site_regime(-24.9), "")
        self.assertEqual(_victron_ess_balance_grid_site_regime(24.9), "")
        self.assertEqual(_victron_ess_balance_grid_site_regime(25.0), "import")
        self.assertEqual(_victron_ess_balance_grid_site_regime(None), "")

        self.assertEqual(_victron_ess_balance_forecast_site_regime(26.0, 25.0), "export")
        self.assertEqual(_victron_ess_balance_forecast_site_regime(25.0, 0.0), "")
        self.assertEqual(_victron_ess_balance_forecast_site_regime(25.0, 26.0), "import")
        self.assertEqual(_victron_ess_balance_forecast_site_regime(30.0, 30.0), "")
        self.assertEqual(_victron_ess_balance_action_direction_site_regime("more_export"), "export")
        self.assertEqual(_victron_ess_balance_action_direction_site_regime("less_export"), "import")

    def test_limit_and_pv_phase_boundaries(self) -> None:
        self.assertTrue(_victron_ess_balance_near_discharge_limit("export", 300.0))
        self.assertFalse(_victron_ess_balance_near_discharge_limit("export", 300.1))
        self.assertFalse(_victron_ess_balance_near_discharge_limit("import", 0.0))
        self.assertFalse(_victron_ess_balance_near_discharge_limit("export", None))
        self.assertTrue(_victron_ess_balance_near_charge_limit("import", 300.0))
        self.assertFalse(_victron_ess_balance_near_charge_limit("import", 300.1))
        self.assertFalse(_victron_ess_balance_near_charge_limit("export", 0.0))
        self.assertFalse(_victron_ess_balance_near_charge_limit("import", None))
        self.assertEqual(_victron_ess_balance_pv_phase(1499.9, 1499.9), "pv_weak")
        self.assertEqual(_victron_ess_balance_pv_phase(1500.0, 0.0), "pv_strong")
        self.assertEqual(_victron_ess_balance_pv_phase(0.0, 1500.0), "pv_strong")

    def test_adaptive_scalar_casts_are_explicit(self) -> None:
        optional = Mock(side_effect=_optional_float)
        self.assertEqual(_victron_ess_balance_adaptive_scalar_value(-3, "int", optional), 0)
        self.assertEqual(_victron_ess_balance_adaptive_scalar_value(4, "int", optional), 4)
        self.assertEqual(_victron_ess_balance_adaptive_scalar_value(None, "int", optional), 0)
        self.assertEqual(_victron_ess_balance_adaptive_scalar_value(None, "str", optional), "")
        self.assertEqual(_victron_ess_balance_adaptive_scalar_value("x", "str", optional), "x")
        self.assertFalse(_victron_ess_balance_adaptive_scalar_value(0, "bool", optional))
        self.assertTrue(_victron_ess_balance_adaptive_scalar_value(1, "bool", optional))
        self.assertEqual(_victron_ess_balance_adaptive_scalar_value(2, "optional_float", optional), 2.0)
        optional.assert_called_once_with(2)
        with self.assertRaises(KeyError):
            _victron_ess_balance_adaptive_scalar_value(1, "unknown", optional)

    def test_profile_key_and_identity_versions(self) -> None:
        key = _victron_ess_balance_learning_profile_key(
            "more_export", "export", "day", "above", "ev_active", "pv_strong", "near_discharge_limit"
        )
        self.assertEqual(key, "more_export:export:day:above:ev_active:pv_strong:near_discharge_limit")
        self.assertEqual(
            _victron_ess_balance_profile_identity(key),
            {
                "key": key,
                "action_direction": "more_export",
                "site_regime": "export",
                "direction": "export",
                "day_phase": "day",
                "reserve_phase": "above",
                "ev_phase": "ev_active",
                "pv_phase": "pv_strong",
                "battery_limit_phase": "near_discharge_limit",
            },
        )
        legacy = _victron_ess_balance_profile_identity("export:day:above")
        self.assertEqual(
            legacy,
            {
                "key": "export:day:above",
                "action_direction": "",
                "site_regime": "export",
                "direction": "export",
                "day_phase": "day",
                "reserve_phase": "above",
                "ev_phase": "ev_idle",
                "pv_phase": "pv_weak",
                "battery_limit_phase": "mid_band",
            },
        )
        four_part = _victron_ess_balance_profile_identity("more_export:export:day:above")
        self.assertEqual(four_part["action_direction"], "more_export")
        self.assertEqual(four_part["site_regime"], "export")
        self.assertEqual(four_part["day_phase"], "day")
        self.assertEqual(four_part["reserve_phase"], "above")
        minimal = _victron_ess_balance_profile_identity("import")
        self.assertEqual(
            minimal,
            {
                "key": "import",
                "action_direction": "",
                "site_regime": "import",
                "direction": "import",
                "day_phase": "",
                "reserve_phase": "",
                "ev_phase": "ev_idle",
                "pv_phase": "pv_weak",
                "battery_limit_phase": "mid_band",
            },
        )
        empty = _victron_ess_balance_profile_identity("")
        self.assertEqual(empty["site_regime"], "")
        extended = _victron_ess_balance_profile_identity(f"{key}:ignored")
        self.assertEqual(extended["battery_limit_phase"], "near_discharge_limit")

    def test_profile_counter_normalizes_missing_and_negative_values(self) -> None:
        self.assertEqual(_victron_ess_balance_profile_counter({}, "samples"), 0)
        self.assertEqual(_victron_ess_balance_profile_counter({"samples": -2}, "samples"), 0)
        self.assertEqual(_victron_ess_balance_profile_counter({"samples": 3}, "samples"), 3)

    def test_profile_sample_first_and_subsequent_updates(self) -> None:
        profile: dict[str, object] = {}
        ewma = Mock(side_effect=lambda current, sample, count: sample + count)
        optional = Mock(side_effect=_optional_float)
        _victron_ess_balance_update_profile_sample(
            profile,
            12.0,
            samples_field="samples",
            value_field="value",
            deviation_field="deviation",
            optional_float=optional,
            ewma=ewma,
        )
        self.assertEqual(profile, {"value": 12.0, "samples": 1})
        self.assertEqual(ewma.call_args_list, [call(None, 12.0, 0)])

        ewma.reset_mock()
        optional.reset_mock()
        profile.update({"value": 10.0, "deviation": 1.0, "samples": 2})
        _victron_ess_balance_update_profile_sample(
            profile,
            14.0,
            samples_field="samples",
            value_field="value",
            deviation_field="deviation",
            optional_float=optional,
            ewma=ewma,
        )
        self.assertEqual(profile, {"value": 16.0, "samples": 3, "deviation": 6.0})
        self.assertEqual(ewma.call_args_list, [call(1.0, 4.0, 2), call(10.0, 14.0, 2)])

    def test_service_sample_updates_value_then_counter(self) -> None:
        svc = SimpleNamespace(samples=2, value=10.0)
        ewma = Mock(return_value=11.0)
        _victron_ess_balance_update_service_sample(
            svc,
            12.0,
            samples_attr="samples",
            value_attr="value",
            optional_float=_optional_float,
            ewma=ewma,
        )
        ewma.assert_called_once_with(10.0, 12.0, 2)
        self.assertEqual((svc.value, svc.samples), (11.0, 3))

        empty = SimpleNamespace()
        ewma.reset_mock()
        _victron_ess_balance_update_service_sample(
            empty,
            7.0,
            samples_attr="count",
            value_attr="average",
            optional_float=_optional_float,
            ewma=ewma,
        )
        ewma.assert_called_once_with(None, 7.0, 0)
        self.assertEqual((empty.average, empty.count), (11.0, 1))

        zero = SimpleNamespace(count=0, average=None)
        ewma.reset_mock()
        _victron_ess_balance_update_service_sample(
            zero,
            8.0,
            samples_attr="count",
            value_attr="average",
            optional_float=_optional_float,
            ewma=ewma,
        )
        ewma.assert_called_once_with(None, 8.0, 0)
        self.assertEqual(zero.count, 1)

    def test_scalar_snapshots_have_stable_names(self) -> None:
        fields = ("key", "site_regime", "day_phase")
        snapshot = _victron_ess_balance_profile_scalar_snapshot(
            {"key": "ignored", "site_regime": "export", "day_phase": None}, fields
        )
        self.assertEqual(snapshot, {"site_regime": "export", "day_phase": ""})
        self.assertEqual(
            _victron_ess_balance_prefixed_scalar_metrics(snapshot, fields),
            {
                "battery_discharge_balance_victron_bias_learning_profile_key": "",
                "battery_discharge_balance_victron_bias_learning_profile_site_regime": "export",
                "battery_discharge_balance_victron_bias_learning_profile_day_phase": "",
            },
        )

    def test_active_profile_schema_and_clear_are_complete(self) -> None:
        fields = _victron_ess_balance_active_profile_fields()
        self.assertEqual(
            fields,
            (
                ("_victron_ess_balance_active_learning_profile_key", "key"),
                ("_victron_ess_balance_active_learning_profile_action_direction", "action_direction"),
                ("_victron_ess_balance_active_learning_profile_site_regime", "site_regime"),
                ("_victron_ess_balance_active_learning_profile_direction", "direction"),
                ("_victron_ess_balance_active_learning_profile_day_phase", "day_phase"),
                ("_victron_ess_balance_active_learning_profile_reserve_phase", "reserve_phase"),
                ("_victron_ess_balance_active_learning_profile_ev_phase", "ev_phase"),
                ("_victron_ess_balance_active_learning_profile_pv_phase", "pv_phase"),
                ("_victron_ess_balance_active_learning_profile_battery_limit_phase", "battery_limit_phase"),
            ),
        )
        svc = SimpleNamespace(**{attr: "set" for attr, _field in fields})
        _clear_victron_ess_balance_active_profile_state(svc)
        self.assertTrue(all(getattr(svc, attr) == "" for attr, _field in fields))

    def test_pid_reset_contracts(self) -> None:
        svc = SimpleNamespace()
        _reset_victron_ess_balance_pid_state(svc)
        self.assertEqual(
            (svc._victron_ess_balance_pid_last_error_w, svc._victron_ess_balance_pid_last_at,
             svc._victron_ess_balance_pid_integral_output_w, svc._victron_ess_balance_pid_last_output_w),
            (0.0, None, 0.0, 0.0),
        )
        svc._victron_ess_balance_pid_last_error_w = 3.0
        svc._victron_ess_balance_pid_last_output_w = 4.0
        _reset_victron_ess_balance_pid_integral_state(svc)
        self.assertEqual((svc._victron_ess_balance_pid_last_error_w, svc._victron_ess_balance_pid_last_output_w), (3.0, 4.0))
        _reset_victron_ess_balance_pid_integral_state(svc, aggressive=True)
        self.assertEqual((svc._victron_ess_balance_pid_last_error_w, svc._victron_ess_balance_pid_last_output_w), (0.0, 0.0))

    def test_tracking_episode_record_and_clear_are_symmetric(self) -> None:
        svc = SimpleNamespace()
        _record_victron_ess_balance_tracking_command(svc, 2, 3, 4, " profile ")
        self.assertEqual(svc._victron_ess_balance_telemetry_last_command_at, 2.0)
        self.assertEqual(svc._victron_ess_balance_telemetry_last_command_setpoint_w, 3.0)
        self.assertEqual(svc._victron_ess_balance_telemetry_last_command_error_w, 4.0)
        self.assertEqual(svc._victron_ess_balance_telemetry_last_command_profile_key, "profile")
        self.assertTrue(svc._victron_ess_balance_telemetry_settling_active)
        for attr in (
            "_victron_ess_balance_telemetry_command_response_recorded",
            "_victron_ess_balance_telemetry_command_overshoot_recorded",
            "_victron_ess_balance_telemetry_command_settled_recorded",
            "_victron_ess_balance_telemetry_overshoot_active",
        ):
            self.assertIs(getattr(svc, attr), False, attr)
        _clear_victron_ess_balance_tracking_episode_state(svc)
        self.assertIsNone(svc._victron_ess_balance_telemetry_last_command_at)
        self.assertIsNone(svc._victron_ess_balance_telemetry_last_command_setpoint_w)
        self.assertIsNone(svc._victron_ess_balance_telemetry_last_command_error_w)
        self.assertEqual(svc._victron_ess_balance_telemetry_last_command_profile_key, "")
        for attr in (
            "_victron_ess_balance_telemetry_command_response_recorded",
            "_victron_ess_balance_telemetry_command_overshoot_recorded",
            "_victron_ess_balance_telemetry_command_settled_recorded",
            "_victron_ess_balance_telemetry_overshoot_active",
            "_victron_ess_balance_telemetry_settling_active",
        ):
            self.assertIs(getattr(svc, attr), False, attr)

    def test_energy_ids_specs_and_float_attr(self) -> None:
        svc = SimpleNamespace(
            auto_energy_sources=(object(), SimpleNamespace(source_id=" a "), SimpleNamespace(source_id="")),
            value=3.5,
            none_value=None,
        )
        self.assertEqual(_victron_ess_balance_energy_ids(svc), ["a"])
        specs = _victron_ess_balance_adaptive_scalar_specs()
        self.assertEqual(
            specs,
            (
                ("auto_apply_generation", "_victron_ess_balance_auto_apply_generation", "int"),
                ("auto_apply_observe_until", "_victron_ess_balance_auto_apply_observe_until", "optional_float"),
                ("auto_apply_last_applied_param", "_victron_ess_balance_auto_apply_last_applied_param", "str"),
                ("auto_apply_last_applied_at", "_victron_ess_balance_auto_apply_last_applied_at", "optional_float"),
                ("oscillation_lockout_until", "_victron_ess_balance_oscillation_lockout_until", "optional_float"),
                ("oscillation_lockout_reason", "_victron_ess_balance_oscillation_lockout_reason", "str"),
                ("last_stable_at", "_victron_ess_balance_last_stable_at", "optional_float"),
                ("last_stable_profile_key", "_victron_ess_balance_last_stable_profile_key", "str"),
                ("auto_apply_suspend_until", "_victron_ess_balance_auto_apply_suspend_until", "optional_float"),
                ("auto_apply_suspend_reason", "_victron_ess_balance_auto_apply_suspend_reason", "str"),
                ("overshoot_cooldown_until", "_victron_ess_balance_overshoot_cooldown_until", "optional_float"),
                ("overshoot_cooldown_reason", "_victron_ess_balance_overshoot_cooldown_reason", "str"),
                ("safe_state_active", "_victron_ess_balance_safe_state_active", "bool"),
                ("safe_state_reason", "_victron_ess_balance_safe_state_reason", "str"),
            ),
        )
        self.assertEqual(_victron_ess_balance_float_attr(svc, "value"), 3.5)
        self.assertEqual(_victron_ess_balance_float_attr(svc, "missing"), 0.0)
        svc.zero = 0.0
        self.assertEqual(_victron_ess_balance_float_attr(svc, "zero"), 0.0)
        self.assertEqual(_victron_ess_balance_float_attr(svc, "none_value"), 0.0)
        self.assertEqual(_victron_ess_balance_energy_ids(SimpleNamespace()), [])


if __name__ == "__main__":
    unittest.main()

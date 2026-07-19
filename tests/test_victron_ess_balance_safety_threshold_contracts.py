# SPDX-License-Identifier: GPL-3.0-or-later
"""Boundary and delegation contracts for Victron ESS safety."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from venus_evcharger.update.victron_ess_balance_safety import VictronEssSafetyController
from tests.support.victron_ess_balance import build_victron_ess_components


class VictronEssBalanceSafetyThresholdContracts(unittest.TestCase):
    def setUp(self) -> None:
        components = build_victron_ess_components()
        self.safety = components.safety
        self.recovery = components.recovery
        self.sources = components.sources

    def test_missing_runtime_metric_fields_have_explicit_defaults(self) -> None:
        svc = SimpleNamespace()
        with (
            patch.object(self.safety, "_victron_ess_balance_recent_direction_change_count", return_value=0),
            patch.object(self.recovery, "_victron_ess_balance_overshoot_cooldown_active", return_value=False),
            patch.object(self.recovery, "_victron_ess_balance_auto_apply_suspended", return_value=False),
        ):
            self.assertEqual(
                self.safety._victron_ess_balance_lockout_metrics(svc, 10.0, None),
                {
                    "battery_discharge_balance_victron_bias_oscillation_lockout_enabled": 1,
                    "battery_discharge_balance_victron_bias_oscillation_lockout_active": 0,
                    "battery_discharge_balance_victron_bias_oscillation_lockout_reason": "",
                    "battery_discharge_balance_victron_bias_oscillation_lockout_until": None,
                    "battery_discharge_balance_victron_bias_oscillation_direction_change_count": 0,
                },
            )

            self.assertEqual(
                self.safety._victron_ess_balance_cooldown_metrics(svc, 10.0),
                {
                    "battery_discharge_balance_victron_bias_overshoot_cooldown_active": 0,
                    "battery_discharge_balance_victron_bias_overshoot_cooldown_reason": "",
                    "battery_discharge_balance_victron_bias_overshoot_cooldown_until": None,
                },
            )
            self.assertEqual(
                self.safety._victron_ess_balance_auto_apply_suspend_metrics(svc, 10.0),
                {
                    "battery_discharge_balance_victron_bias_auto_apply_suspend_active": 0,
                    "battery_discharge_balance_victron_bias_auto_apply_suspend_reason": "",
                    "battery_discharge_balance_victron_bias_auto_apply_suspend_until": None,
                },
            )

        disabled = SimpleNamespace(auto_battery_discharge_balance_victron_bias_oscillation_lockout_enabled=False)
        with patch.object(self.safety, "_victron_ess_balance_recent_direction_change_count", return_value=0):
            result = self.safety._victron_ess_balance_lockout_metrics(disabled, 10.0, 10.0)
        self.assertEqual(result["battery_discharge_balance_victron_bias_oscillation_lockout_enabled"], 0)
        self.assertEqual(result["battery_discharge_balance_victron_bias_oscillation_lockout_active"], 0)

    def test_clean_decision_forwards_exact_arguments(self) -> None:
        svc = SimpleNamespace()
        cluster = {"grid": 1}
        with (
            patch.object(self.safety, "_victron_ess_balance_telemetry_precheck_reason", return_value=None) as precheck,
            patch.object(self.safety, "_victron_ess_balance_telemetry_window_reason", return_value=None) as window,
            patch.object(self.safety, "_victron_ess_balance_error_inside_deadband", return_value=False) as deadband,
        ):
            self.assertEqual(self.safety._victron_ess_balance_telemetry_is_clean(svc, cluster, 12.0), (True, "clean"))
        precheck.assert_called_once_with(svc)
        window.assert_called_once_with(svc, cluster)
        deadband.assert_called_once_with(svc, 12.0)

    def test_default_and_configured_thresholds_are_exact(self) -> None:
        missing = SimpleNamespace(auto_battery_discharge_balance_victron_bias_deadband_watts=0.0)
        self.assertTrue(self.safety._victron_ess_balance_requires_clean_phases(missing))
        self.assertEqual(self.safety._victron_ess_balance_direction_change_window_seconds(missing), 120.0)
        self.assertEqual(self.safety._victron_ess_balance_oscillation_lockout_duration_seconds(missing), 180.0)
        self.assertTrue(self.safety._victron_ess_balance_should_enter_oscillation_lockout(missing, 3))
        self.assertFalse(self.safety._victron_ess_balance_should_enter_oscillation_lockout(missing, 2))
        self.assertFalse(self.safety._victron_ess_balance_error_inside_deadband(missing, 10.0))
        self.assertTrue(self.safety._victron_ess_balance_error_inside_deadband(missing, 9.9))

        configured = SimpleNamespace(
            auto_battery_discharge_balance_victron_bias_oscillation_lockout_enabled=False,
            auto_battery_discharge_balance_victron_bias_oscillation_lockout_min_direction_changes=1,
            auto_battery_discharge_balance_victron_bias_oscillation_lockout_window_seconds=-5.0,
            auto_battery_discharge_balance_victron_bias_oscillation_lockout_duration_seconds=-6.0,
        )
        self.assertFalse(self.safety._victron_ess_balance_should_enter_oscillation_lockout(configured, 99))
        self.assertEqual(self.safety._victron_ess_balance_direction_change_window_seconds(configured), 0.0)
        self.assertEqual(self.safety._victron_ess_balance_oscillation_lockout_duration_seconds(configured), 0.0)

    def test_action_state_helpers_normalize_and_preserve_identity(self) -> None:
        entries: list[object] = [{"at": 1.0}]
        svc = SimpleNamespace(
            _victron_ess_balance_recent_action_changes=entries,
            _victron_ess_balance_last_action_direction=" more_export ",
        )
        self.assertIs(self.safety._victron_ess_balance_action_change_entries(svc), entries)
        self.assertEqual(self.safety._victron_ess_balance_last_action_direction(svc), "more_export")
        self.assertEqual(self.safety._victron_ess_balance_last_action_direction(SimpleNamespace()), "")
        self.assertEqual(self.safety._victron_ess_balance_action_change_entries(SimpleNamespace()), [])
        self.assertTrue(self.safety._victron_ess_balance_should_record_action_direction([], "more_export", "more_export"))
        self.assertFalse(
            self.safety._victron_ess_balance_should_record_action_direction(entries, "more_export", "more_export")
        )
        self.assertTrue(
            self.safety._victron_ess_balance_should_record_action_direction(entries, "more_export", "less_export")
        )

    def test_direction_note_forwards_state_and_records_without_lockout(self) -> None:
        svc = SimpleNamespace()
        entries: list[object] = []
        with (
            patch.object(self.safety, "_victron_ess_balance_action_change_entries", return_value=entries) as action_entries,
            patch.object(self.safety, "_victron_ess_balance_last_action_direction", return_value="") as last,
            patch.object(self.safety, "_victron_ess_balance_should_record_action_direction", return_value=True) as should_record,
            patch.object(self.safety, "_victron_ess_balance_recent_direction_change_count", return_value=0) as count,
            patch.object(self.safety, "_victron_ess_balance_should_enter_oscillation_lockout", return_value=False) as lockout,
        ):
            self.assertEqual(self.safety._victron_ess_balance_note_action_direction(svc, " more_export ", 12.0), 0)
        action_entries.assert_called_once_with(svc)
        last.assert_called_once_with(svc)
        should_record.assert_called_once_with(entries, "", "more_export")
        count.assert_called_once_with(svc, 12.0)
        lockout.assert_called_once_with(svc, 0)
        self.assertEqual(entries, [{"at": 12.0, "action_direction": "more_export"}])
        self.assertEqual(svc._victron_ess_balance_last_action_direction, "more_export")
        self.assertIs(svc._victron_ess_balance_recent_action_changes, entries)

    def test_recent_count_forwards_exact_cutoff_and_normalizes_count(self) -> None:
        svc = SimpleNamespace(_victron_ess_balance_recent_action_changes=[{"at": 1.0}])
        kept = [{"at": 5.0}, {"at": 6.0}, {"at": 7.0}]
        with (
            patch.object(VictronEssSafetyController, "_victron_ess_balance_direction_change_window_seconds", return_value=4.0) as window,
            patch.object(VictronEssSafetyController, "_victron_ess_balance_kept_action_changes", return_value=kept) as filter_entries,
        ):
            self.assertEqual(self.safety._victron_ess_balance_recent_direction_change_count(svc, 10.0), 2)
        window.assert_called_once_with(svc)
        filter_entries.assert_called_once_with([{"at": 1.0}], 6.0)
        self.assertIs(svc._victron_ess_balance_recent_action_changes, kept)

        with patch.object(
            VictronEssSafetyController,
            "_victron_ess_balance_kept_action_changes",
            return_value=[],
        ):
            self.assertEqual(self.safety._victron_ess_balance_recent_direction_change_count(svc, 10.0), 0)

    def test_jump_detectors_use_delta_instead_of_sum(self) -> None:
        cases = (
            (self.safety._victron_ess_balance_grid_interaction_unstable, 500.0, 400.0),
            (self.safety._victron_ess_balance_foreign_power_event, 800.0, 700.0),
            (self.safety._victron_ess_balance_ev_load_jump, 400.0, 300.0),
        )
        for detector, current, previous in cases:
            with self.subTest(detector=detector.__name__):
                self.assertFalse(detector(current, previous))

    def test_window_helpers_forward_exact_runtime_values(self) -> None:
        svc = SimpleNamespace(
            _victron_ess_balance_telemetry_last_grid_interaction_w=1.0,
            _victron_ess_balance_telemetry_last_ac_power_w=2.0,
            _victron_ess_balance_telemetry_last_ev_power_w=3.0,
        )
        cluster = {
            "battery_combined_grid_interaction_w": 4.0,
            "battery_combined_ac_power_w": 5.0,
        }
        with patch.object(self.safety, "_victron_ess_balance_grid_interaction_unstable", return_value=False) as unstable:
            self.assertIsNone(self.safety._victron_ess_balance_grid_window_reason(svc, cluster))
        unstable.assert_called_once_with(4.0, 1.0)
        with patch.object(self.safety, "_victron_ess_balance_foreign_power_event", return_value=False) as foreign:
            self.assertIsNone(self.safety._victron_ess_balance_power_window_reason(svc, cluster))
        foreign.assert_called_once_with(5.0, 2.0)
        with (
            patch.object(self.sources, "_victron_ess_balance_ev_power_w", return_value=6.0) as power,
            patch.object(self.safety, "_victron_ess_balance_ev_load_jump", return_value=False) as jump,
        ):
            self.assertIsNone(self.safety._victron_ess_balance_ev_window_reason(svc))
        power.assert_called_once_with(svc)
        jump.assert_called_once_with(6.0, 3.0)


if __name__ == "__main__":
    unittest.main()

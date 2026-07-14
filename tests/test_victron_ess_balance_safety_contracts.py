# SPDX-License-Identifier: GPL-3.0-or-later
"""State-transition contracts for Victron ESS balance safety handling."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from venus_evcharger.update.victron_ess_balance_safety import _UpdateCycleVictronEssBalanceSafety


class _SafetyHarness(_UpdateCycleVictronEssBalanceSafety):
    @staticmethod
    def _optional_float(value: object) -> float | None:
        return float(value) if isinstance(value, (int, float)) else None


class VictronEssBalanceSafetySupportContracts(unittest.TestCase):
    def setUp(self) -> None:
        self.safety = _SafetyHarness()

    def test_stable_tuning_threshold_contract_and_refresh(self) -> None:
        can_refresh = self.safety._victron_ess_balance_can_refresh_stable_tuning
        self.assertTrue(can_refresh(0.8, 0.8, 2, 0))
        for args in ((None, 0.8, 2, 0), (0.79, 0.8, 2, 0), (0.8, None, 2, 0), (0.8, 0.79, 2, 0), (0.8, 0.8, 1, 0), (0.8, 0.8, 2, 1)):
            self.assertFalse(can_refresh(*args))

        svc = SimpleNamespace()
        metrics = {
            "battery_discharge_balance_victron_bias_recommendation_confidence": 0.8,
            "battery_discharge_balance_victron_bias_learning_profile_stability_score": 0.8,
            "battery_discharge_balance_victron_bias_learning_profile_sample_count": 2,
            "battery_discharge_balance_victron_bias_learning_profile_overshoot_count": 0,
            "battery_discharge_balance_victron_bias_learning_profile_key": " profile ",
        }
        snapshot = {"kp": 0.2}
        with (
            patch.object(self.safety, "_victron_ess_balance_ensure_conservative_tuning") as conservative,
            patch.object(self.safety, "_victron_ess_balance_current_tuning_snapshot", return_value=snapshot) as current,
        ):
            self.safety._victron_ess_balance_refresh_stable_tuning(svc, metrics, 12.5)
        conservative.assert_called_once_with(svc)
        current.assert_called_once_with(svc)
        self.assertIs(svc._victron_ess_balance_last_stable_tuning, snapshot)
        self.assertEqual(svc._victron_ess_balance_last_stable_at, 12.5)
        self.assertEqual(svc._victron_ess_balance_last_stable_profile_key, "profile")

        with patch.object(self.safety, "_victron_ess_balance_ensure_conservative_tuning") as conservative:
            self.safety._victron_ess_balance_refresh_stable_tuning(svc, {}, 13.0)
        conservative.assert_not_called()

    def test_conservative_snapshot_is_initialized_once(self) -> None:
        svc = SimpleNamespace(_victron_ess_balance_conservative_tuning=None)
        with patch.object(self.safety, "_victron_ess_balance_current_tuning_snapshot", return_value={"kp": 1.0}) as current:
            self.safety._victron_ess_balance_ensure_conservative_tuning(svc)
            self.safety._victron_ess_balance_ensure_conservative_tuning(svc)
        current.assert_called_once_with(svc)
        self.assertEqual(svc._victron_ess_balance_conservative_tuning, {"kp": 1.0})

    def test_rollback_gate_covers_enable_window_signal_and_stability(self) -> None:
        metrics = {"battery_discharge_balance_victron_bias_stability_score": 0.44}
        svc = SimpleNamespace(
            auto_battery_discharge_balance_victron_bias_rollback_enabled=True,
            auto_battery_discharge_balance_victron_bias_rollback_min_stability_score=0.45,
            _victron_ess_balance_auto_apply_observe_until=20.0,
        )
        self.assertTrue(self.safety._victron_ess_balance_should_rollback_stable_tuning(svc, metrics, 10.0))
        metrics["battery_discharge_balance_victron_bias_stability_score"] = 0.45
        self.assertFalse(self.safety._victron_ess_balance_should_rollback_stable_tuning(svc, metrics, 10.0))
        metrics["battery_discharge_balance_victron_bias_overshoot_active"] = 1
        self.assertTrue(self.safety._victron_ess_balance_should_rollback_stable_tuning(svc, metrics, 10.0))
        svc._victron_ess_balance_auto_apply_observe_until = 10.0
        self.assertFalse(self.safety._victron_ess_balance_should_rollback_stable_tuning(svc, metrics, 10.0))
        svc.auto_battery_discharge_balance_victron_bias_rollback_enabled = False
        svc._victron_ess_balance_auto_apply_observe_until = 20.0
        self.assertFalse(self.safety._victron_ess_balance_should_rollback_stable_tuning(svc, metrics, 10.0))

    def test_immediate_rollback_signal_checks_each_safety_flag(self) -> None:
        keys = (
            "battery_discharge_balance_victron_bias_oscillation_lockout_active",
            "battery_discharge_balance_victron_bias_overshoot_active",
            "battery_discharge_balance_victron_bias_overshoot_cooldown_active",
        )
        self.assertFalse(self.safety._victron_ess_balance_has_immediate_rollback_signal({}))
        for key in keys:
            self.assertTrue(self.safety._victron_ess_balance_has_immediate_rollback_signal({key: 1}))

    def test_restore_target_prefers_stable_then_conservative(self) -> None:
        stable = {"kp": 1.0}
        conservative = {"kp": 0.5}
        svc = SimpleNamespace(
            _victron_ess_balance_last_stable_tuning=stable,
            _victron_ess_balance_conservative_tuning=conservative,
        )
        self.assertEqual(self.safety._victron_ess_balance_restore_target(svc, "risk"), (stable, "risk"))
        svc._victron_ess_balance_last_stable_tuning = {}
        self.assertEqual(
            self.safety._victron_ess_balance_restore_target(svc, "risk"),
            (conservative, "conservative_fallback"),
        )
        svc._victron_ess_balance_conservative_tuning = []
        self.assertEqual(self.safety._victron_ess_balance_restore_target(svc, "risk"), (None, ""))
        with patch.object(self.safety, "_victron_ess_balance_activation_mode", return_value="export_only") as mode:
            self.assertEqual(self.safety._victron_ess_balance_restored_activation_mode(svc, {}), "export_only")
        mode.assert_called_once_with(svc)
        with patch.object(self.safety, "_victron_ess_balance_activation_mode") as mode:
            self.assertEqual(self.safety._victron_ess_balance_restored_activation_mode(svc, {"activation_mode": ""}), "always")
        mode.assert_not_called()

    def test_restore_applies_all_fields_and_records_safe_state(self) -> None:
        stable = {
            "kp": 1,
            "ki": 2,
            "kd": 3,
            "deadband_watts": 4,
            "max_abs_watts": 5,
            "ramp_rate_watts_per_second": 6,
            "activation_mode": " export_only ",
        }
        svc = SimpleNamespace(
            auto_battery_discharge_balance_victron_bias_rollback_enabled=True,
            _victron_ess_balance_last_stable_tuning=stable,
            _victron_ess_balance_last_stable_profile_key="p",
            _victron_ess_balance_auto_apply_last_applied_at=9.0,
        )
        metrics: dict[str, object] = {}
        with patch.object(self.safety, "_victron_ess_balance_suspend_auto_apply") as suspend:
            self.assertTrue(self.safety._maybe_restore_victron_ess_balance_stable_tuning(svc, metrics, "risk"))
        suspend.assert_called_once_with(svc, "risk", 9.0)
        self.assertEqual(
            (
                svc.auto_battery_discharge_balance_victron_bias_kp,
                svc.auto_battery_discharge_balance_victron_bias_ki,
                svc.auto_battery_discharge_balance_victron_bias_kd,
                svc.auto_battery_discharge_balance_victron_bias_deadband_watts,
                svc.auto_battery_discharge_balance_victron_bias_max_abs_watts,
                svc.auto_battery_discharge_balance_victron_bias_ramp_rate_watts_per_second,
                svc.auto_battery_discharge_balance_victron_bias_activation_mode,
            ),
            (1.0, 2.0, 3.0, 4.0, 5.0, 6.0, "export_only"),
        )
        self.assertEqual(metrics["battery_discharge_balance_victron_bias_rollback_active"], 1)
        self.assertEqual(metrics["battery_discharge_balance_victron_bias_rollback_reason"], "risk")
        self.assertEqual(metrics["battery_discharge_balance_victron_bias_safe_state_reason"], "risk")

        svc._victron_ess_balance_last_stable_tuning = None
        svc._victron_ess_balance_conservative_tuning = None
        metrics = {}
        self.assertFalse(self.safety._maybe_restore_victron_ess_balance_stable_tuning(svc, metrics, "risk"))
        self.assertEqual(metrics["battery_discharge_balance_victron_bias_rollback_reason"], "no_stable_tuning")

    def test_ev_power_sources_and_active_thresholds(self) -> None:
        for attr in ("_last_charger_state_power_w", "_charger_estimated_power_w", "_last_power", "ac_power"):
            svc = SimpleNamespace(**{attr: 123.0})
            self.assertEqual(self.safety._victron_ess_balance_direct_ev_power_w(svc), 123.0)
        self.assertIsNone(self.safety._victron_ess_balance_direct_ev_power_w(SimpleNamespace(ac_power="bad")))
        self.assertEqual(
            self.safety._victron_ess_balance_ev_power_w(
                SimpleNamespace(learned_charge_power_watts=1800, virtual_startstop=1)
            ),
            1800.0,
        )
        self.assertIsNone(
            self.safety._victron_ess_balance_ev_power_w(
                SimpleNamespace(learned_charge_power_watts=1800, virtual_startstop=0)
            )
        )
        self.assertTrue(self.safety._victron_ess_balance_ev_active(SimpleNamespace(ac_power=200.0)))
        self.assertFalse(self.safety._victron_ess_balance_ev_active(SimpleNamespace(ac_power=199.9, virtual_startstop=0)))
        self.assertTrue(self.safety._victron_ess_balance_ev_active(SimpleNamespace(charging_started_at=0)))
        self.assertTrue(self.safety._victron_ess_balance_ev_active(SimpleNamespace(virtual_startstop=1)))

    def test_cooldown_and_suspend_time_windows_are_exact(self) -> None:
        svc = SimpleNamespace(
            _victron_ess_balance_telemetry_response_delay_seconds=5.0,
            auto_battery_discharge_balance_victron_bias_observation_window_seconds=40.0,
        )
        with patch.object(self.safety, "_victron_ess_balance_suspend_auto_apply") as suspend:
            self.safety._enter_victron_ess_balance_overshoot_cooldown(svc, 100.0, "risk")
        self.assertEqual(svc._victron_ess_balance_overshoot_cooldown_until, 120.0)
        self.assertEqual(svc._victron_ess_balance_overshoot_cooldown_reason, "risk")
        suspend.assert_called_once_with(svc, "overshoot_cooldown", 100.0)
        self.assertTrue(self.safety._victron_ess_balance_overshoot_cooldown_active(svc, 119.9))
        self.assertFalse(self.safety._victron_ess_balance_overshoot_cooldown_active(svc, 120.0))

        self.safety._victron_ess_balance_suspend_auto_apply(svc, "hold", 10.0)
        self.assertEqual(svc._victron_ess_balance_auto_apply_suspend_until, 90.0)
        self.assertEqual(svc._victron_ess_balance_auto_apply_suspend_reason, "hold")
        self.assertTrue(self.safety._victron_ess_balance_auto_apply_suspended(svc, 89.9))
        self.assertFalse(self.safety._victron_ess_balance_auto_apply_suspended(svc, 90.0))

        fallback = SimpleNamespace(
            _victron_ess_balance_telemetry_last_observed_at=25.0,
            _victron_ess_balance_auto_apply_last_applied_at=20.0,
        )
        self.safety._victron_ess_balance_suspend_auto_apply(fallback, "fallback")
        self.assertEqual(fallback._victron_ess_balance_auto_apply_suspend_until, 85.0)


class VictronEssBalanceSafetyContracts(unittest.TestCase):
    def setUp(self) -> None:
        self.safety = _SafetyHarness()

    def test_runtime_metric_groups_are_merged_exactly(self) -> None:
        svc = SimpleNamespace(_victron_ess_balance_oscillation_lockout_until=20.0)
        metrics: dict[str, object] = {}
        with (
            patch.object(self.safety, "_victron_ess_balance_lockout_metrics", return_value={"lockout": 1}) as lockout,
            patch.object(self.safety, "_victron_ess_balance_cooldown_metrics", return_value={"cooldown": 2}) as cooldown,
            patch.object(self.safety, "_victron_ess_balance_auto_apply_suspend_metrics", return_value={"suspend": 3}) as suspend,
            patch.object(self.safety, "_victron_ess_balance_safe_state_metrics", return_value={"safe": 4}) as safe,
        ):
            self.safety._populate_victron_ess_balance_runtime_safety_metrics(svc, 10.0, metrics)
        self.assertEqual(metrics, {"lockout": 1, "cooldown": 2, "suspend": 3, "safe": 4})
        lockout.assert_called_once_with(svc, 10.0, 20.0)
        cooldown.assert_called_once_with(svc, 10.0)
        suspend.assert_called_once_with(svc, 10.0)
        safe.assert_called_once_with(svc)

    def test_individual_runtime_metric_payloads_are_exact(self) -> None:
        svc = SimpleNamespace(
            auto_battery_discharge_balance_victron_bias_oscillation_lockout_enabled=True,
            _victron_ess_balance_oscillation_lockout_reason=" lockout ",
            _victron_ess_balance_overshoot_cooldown_reason=" cooldown ",
            _victron_ess_balance_overshoot_cooldown_until=30.0,
            _victron_ess_balance_auto_apply_suspend_reason=" suspend ",
            _victron_ess_balance_auto_apply_suspend_until=40.0,
            _victron_ess_balance_safe_state_active=True,
            _victron_ess_balance_safe_state_reason=" safe ",
        )
        with (
            patch.object(self.safety, "_victron_ess_balance_recent_direction_change_count", return_value=3) as count,
            patch.object(self.safety, "_victron_ess_balance_overshoot_cooldown_active", return_value=True) as cooldown,
            patch.object(self.safety, "_victron_ess_balance_auto_apply_suspended", return_value=True) as suspended,
        ):
            self.assertEqual(
                self.safety._victron_ess_balance_lockout_metrics(svc, 10.0, 20.0),
                {
                    "battery_discharge_balance_victron_bias_oscillation_lockout_enabled": 1,
                    "battery_discharge_balance_victron_bias_oscillation_lockout_active": 1,
                    "battery_discharge_balance_victron_bias_oscillation_lockout_reason": " lockout ",
                    "battery_discharge_balance_victron_bias_oscillation_lockout_until": 20.0,
                    "battery_discharge_balance_victron_bias_oscillation_direction_change_count": 3,
                },
            )
            self.assertEqual(
                self.safety._victron_ess_balance_cooldown_metrics(svc, 10.0),
                {
                    "battery_discharge_balance_victron_bias_overshoot_cooldown_active": 1,
                    "battery_discharge_balance_victron_bias_overshoot_cooldown_reason": " cooldown ",
                    "battery_discharge_balance_victron_bias_overshoot_cooldown_until": 30.0,
                },
            )
            self.assertEqual(
                self.safety._victron_ess_balance_auto_apply_suspend_metrics(svc, 10.0),
                {
                    "battery_discharge_balance_victron_bias_auto_apply_suspend_active": 1,
                    "battery_discharge_balance_victron_bias_auto_apply_suspend_reason": " suspend ",
                    "battery_discharge_balance_victron_bias_auto_apply_suspend_until": 40.0,
                },
            )
        count.assert_called_once_with(svc, 10.0)
        cooldown.assert_called_once_with(svc, 10.0)
        suspended.assert_called_once_with(svc, 10.0)
        self.assertEqual(
            self.safety._victron_ess_balance_safe_state_metrics(svc),
            {
                "battery_discharge_balance_victron_bias_safe_state_active": 1,
                "battery_discharge_balance_victron_bias_safe_state_reason": " safe ",
            },
        )
        self.assertEqual(
            self.safety._victron_ess_balance_safe_state_metrics(SimpleNamespace()),
            {
                "battery_discharge_balance_victron_bias_safe_state_active": 0,
                "battery_discharge_balance_victron_bias_safe_state_reason": "",
            },
        )

    def test_window_reason_delegation_and_raw_windows_are_exact(self) -> None:
        svc = SimpleNamespace(
            _victron_ess_balance_telemetry_last_grid_interaction_w=0.0,
            _victron_ess_balance_telemetry_last_ac_power_w=0.0,
            _victron_ess_balance_telemetry_last_ev_power_w=0.0,
        )
        with (
            patch.object(self.safety, "_victron_ess_balance_grid_window_reason", return_value="grid") as grid,
            patch.object(self.safety, "_victron_ess_balance_power_window_reason") as power,
        ):
            self.assertEqual(self.safety._victron_ess_balance_telemetry_window_reason(svc, {}), "grid")
        grid.assert_called_once_with(svc, {})
        power.assert_not_called()
        with (
            patch.object(self.safety, "_victron_ess_balance_grid_window_reason", return_value=None),
            patch.object(self.safety, "_victron_ess_balance_power_window_reason", return_value="power") as power,
            patch.object(self.safety, "_victron_ess_balance_ev_window_reason") as ev,
        ):
            self.assertEqual(self.safety._victron_ess_balance_telemetry_window_reason(svc, {}), "power")
        power.assert_called_once_with(svc, {})
        ev.assert_not_called()
        with (
            patch.object(self.safety, "_victron_ess_balance_grid_window_reason", return_value=None),
            patch.object(self.safety, "_victron_ess_balance_power_window_reason", return_value=None),
            patch.object(self.safety, "_victron_ess_balance_ev_window_reason", return_value="ev") as ev,
        ):
            self.assertEqual(self.safety._victron_ess_balance_telemetry_window_reason(svc, {}), "ev")
        ev.assert_called_once_with(svc)

        self.assertEqual(self.safety._victron_ess_balance_grid_window_reason(svc, {}), "grid_interaction_missing")
        self.assertEqual(
            self.safety._victron_ess_balance_grid_window_reason(
                svc, {"battery_combined_grid_interaction_w": 600.1}
            ),
            "grid_unstable",
        )
        self.assertIsNone(
            self.safety._victron_ess_balance_grid_window_reason(
                svc, {"battery_combined_grid_interaction_w": 600.0}
            )
        )
        self.assertEqual(
            self.safety._victron_ess_balance_power_window_reason(
                svc, {"battery_combined_ac_power_w": 900.1}
            ),
            "foreign_power_event",
        )
        self.assertIsNone(
            self.safety._victron_ess_balance_power_window_reason(
                svc, {"battery_combined_ac_power_w": 900.0}
            )
        )
        with patch.object(self.safety, "_victron_ess_balance_ev_power_w", return_value=500.1):
            self.assertEqual(self.safety._victron_ess_balance_ev_window_reason(svc), "ev_load_jump")
        with patch.object(self.safety, "_victron_ess_balance_ev_power_w", return_value=500.0):
            self.assertIsNone(self.safety._victron_ess_balance_ev_window_reason(svc))

    def test_telemetry_clean_reason_has_strict_priority(self) -> None:
        svc = SimpleNamespace()
        cluster: dict[str, object] = {}
        with patch.object(self.safety, "_victron_ess_balance_telemetry_precheck_reason", return_value=(False, "precheck")):
            self.assertEqual(self.safety._victron_ess_balance_telemetry_is_clean(svc, cluster, 20.0), (False, "precheck"))
        with (
            patch.object(self.safety, "_victron_ess_balance_telemetry_precheck_reason", return_value=None),
            patch.object(self.safety, "_victron_ess_balance_telemetry_window_reason", return_value="window"),
        ):
            self.assertEqual(self.safety._victron_ess_balance_telemetry_is_clean(svc, cluster, 20.0), (False, "window"))
        with (
            patch.object(self.safety, "_victron_ess_balance_telemetry_precheck_reason", return_value=None),
            patch.object(self.safety, "_victron_ess_balance_telemetry_window_reason", return_value=None),
            patch.object(self.safety, "_victron_ess_balance_error_inside_deadband", return_value=True),
        ):
            self.assertEqual(self.safety._victron_ess_balance_telemetry_is_clean(svc, cluster, 20.0), (False, "error_inside_deadband"))
        with (
            patch.object(self.safety, "_victron_ess_balance_telemetry_precheck_reason", return_value=None),
            patch.object(self.safety, "_victron_ess_balance_telemetry_window_reason", return_value=None),
            patch.object(self.safety, "_victron_ess_balance_error_inside_deadband", return_value=False),
        ):
            self.assertEqual(self.safety._victron_ess_balance_telemetry_is_clean(svc, cluster, 20.0), (True, "clean"))

    def test_precheck_reason_covers_each_runtime_block(self) -> None:
        self.assertEqual(
            self.safety._victron_ess_balance_telemetry_precheck_reason(
                SimpleNamespace(auto_battery_discharge_balance_victron_bias_require_clean_phases=False)
            ),
            (True, "clean_not_required"),
        )
        cases = (
            ("_auto_cached_inputs_used", True, "cached_inputs"),
            ("_phase_switch_state", "switching", "phase_switch_active"),
            ("_contactor_fault_active_reason", "fault", "contactor_fault_active"),
            ("_contactor_lockout_reason", "lock", "contactor_lockout_active"),
        )
        for attr, value, expected in cases:
            svc = SimpleNamespace(auto_battery_discharge_balance_victron_bias_require_clean_phases=True, **{attr: value})
            self.assertEqual(self.safety._victron_ess_balance_telemetry_precheck_reason(svc), (False, expected))
        self.assertIsNone(
            self.safety._victron_ess_balance_telemetry_precheck_reason(
                SimpleNamespace(auto_battery_discharge_balance_victron_bias_require_clean_phases=True)
            )
        )

    def test_window_threshold_helpers_use_strict_boundaries(self) -> None:
        helpers = (
            self.safety._victron_ess_balance_grid_interaction_unstable,
            self.safety._victron_ess_balance_foreign_power_event,
            self.safety._victron_ess_balance_ev_load_jump,
        )
        thresholds = (600.0, 900.0, 500.0)
        for helper, threshold in zip(helpers, thresholds, strict=True):
            self.assertFalse(helper(None, 0.0))
            self.assertFalse(helper(0.0, None))
            self.assertFalse(helper(threshold, 0.0))
            self.assertTrue(helper(threshold + 0.1, 0.0))
        svc = SimpleNamespace(auto_battery_discharge_balance_victron_bias_deadband_watts=20.0)
        self.assertTrue(self.safety._victron_ess_balance_error_inside_deadband(svc, 19.9))
        self.assertFalse(self.safety._victron_ess_balance_error_inside_deadband(svc, 20.0))

    def test_action_history_filters_and_counts_direction_changes(self) -> None:
        entries: list[object] = [
            "bad",
            {"at": "bad"},
            {"at": 89.9, "action_direction": "less_export"},
            {"at": 90.0, "action_direction": "more_export"},
            {"at": 100.0, "action_direction": "less_export"},
        ]
        self.assertEqual(
            self.safety._victron_ess_balance_kept_action_changes(entries, 90.0),
            [
                {"at": 90.0, "action_direction": "more_export"},
                {"at": 100.0, "action_direction": "less_export"},
            ],
        )
        svc = SimpleNamespace(
            auto_battery_discharge_balance_victron_bias_oscillation_lockout_window_seconds=10.0,
            _victron_ess_balance_recent_action_changes=entries,
        )
        self.assertEqual(self.safety._victron_ess_balance_recent_direction_change_count(svc, 100.0), 1)

    def test_note_direction_enters_lockout_at_configured_threshold(self) -> None:
        svc = SimpleNamespace(
            auto_battery_discharge_balance_victron_bias_oscillation_lockout_enabled=True,
            auto_battery_discharge_balance_victron_bias_oscillation_lockout_min_direction_changes=1,
            auto_battery_discharge_balance_victron_bias_oscillation_lockout_duration_seconds=30.0,
            auto_battery_discharge_balance_victron_bias_oscillation_lockout_window_seconds=120.0,
            _victron_ess_balance_recent_action_changes=[{"at": 9.0, "action_direction": "more_export"}],
            _victron_ess_balance_last_action_direction="more_export",
        )
        with patch.object(self.safety, "_reset_victron_ess_balance_pid_integral") as reset:
            count = self.safety._victron_ess_balance_note_action_direction(svc, " less_export ", 10.0)
        self.assertEqual(count, 1)
        self.assertEqual(svc._victron_ess_balance_oscillation_lockout_until, 40.0)
        self.assertEqual(svc._victron_ess_balance_oscillation_lockout_reason, "direction_change_oscillation")
        reset.assert_called_once_with(svc, aggressive=True)
        self.assertTrue(self.safety._victron_ess_balance_oscillation_lockout_active(svc, 39.9))
        self.assertFalse(self.safety._victron_ess_balance_oscillation_lockout_active(svc, 40.0))

        with patch.object(self.safety, "_victron_ess_balance_recent_direction_change_count", return_value=7) as count_call:
            self.assertEqual(self.safety._victron_ess_balance_note_action_direction(svc, "invalid", 11.0), 7)
        count_call.assert_called_once_with(svc, 11.0)


if __name__ == "__main__":
    unittest.main()

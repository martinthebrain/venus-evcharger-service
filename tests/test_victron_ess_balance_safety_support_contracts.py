# SPDX-License-Identifier: GPL-3.0-or-later
"""Detailed rollback and timing contracts for Victron ESS safety support."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from venus_evcharger.update.victron_ess_balance_safety_support import (
    _UpdateCycleVictronEssBalanceSafetySupport,
)


class _SupportHarness(_UpdateCycleVictronEssBalanceSafetySupport):
    @staticmethod
    def _optional_float(value: object) -> float | None:
        return float(value) if isinstance(value, (int, float)) else None


class VictronEssBalanceSafetySupportContracts(unittest.TestCase):
    def setUp(self) -> None:
        self.support = _SupportHarness()

    def test_refresh_defaults_and_negative_overshoot_are_well_defined(self) -> None:
        metrics = {
            "battery_discharge_balance_victron_bias_recommendation_confidence": 0.8,
            "battery_discharge_balance_victron_bias_learning_profile_stability_score": 0.8,
            "battery_discharge_balance_victron_bias_learning_profile_sample_count": 2,
            "battery_discharge_balance_victron_bias_learning_profile_overshoot_count": -1,
        }
        svc = SimpleNamespace()
        with (
            patch.object(self.support, "_victron_ess_balance_ensure_conservative_tuning") as conservative,
            patch.object(self.support, "_victron_ess_balance_current_tuning_snapshot", return_value={"kp": 1.0}),
        ):
            self.support._victron_ess_balance_refresh_stable_tuning(svc, metrics, 5.0)
        conservative.assert_called_once_with(svc)
        self.assertEqual(svc._victron_ess_balance_last_stable_profile_key, "")
        self.assertEqual(svc._victron_ess_balance_last_stable_at, 5.0)

        metrics["battery_discharge_balance_victron_bias_learning_profile_overshoot_count"] = 1
        with patch.object(self.support, "_victron_ess_balance_ensure_conservative_tuning") as conservative:
            self.support._victron_ess_balance_refresh_stable_tuning(SimpleNamespace(), metrics, 6.0)
        conservative.assert_not_called()

    def test_rollback_minimum_stability_defaults_and_clamps(self) -> None:
        self.assertEqual(self.support._victron_ess_balance_rollback_min_stability_score(SimpleNamespace()), 0.45)
        self.assertEqual(
            self.support._victron_ess_balance_rollback_min_stability_score(
                SimpleNamespace(auto_battery_discharge_balance_victron_bias_rollback_min_stability_score=0.3)
            ),
            0.3,
        )
        self.assertEqual(
            self.support._victron_ess_balance_rollback_min_stability_score(
                SimpleNamespace(auto_battery_discharge_balance_victron_bias_rollback_min_stability_score=-1.0)
            ),
            0.0,
        )

    def test_rollback_gate_defaults_to_enabled_and_forwards_arguments(self) -> None:
        svc = SimpleNamespace(_victron_ess_balance_auto_apply_observe_until=20.0)
        metrics = {"battery_discharge_balance_victron_bias_stability_score": 0.1}
        with (
            patch.object(self.support, "_victron_ess_balance_observation_window_active", return_value=True) as window,
            patch.object(self.support, "_victron_ess_balance_has_immediate_rollback_signal", return_value=False) as immediate,
            patch.object(self.support, "_victron_ess_balance_rollback_min_stability_score", return_value=0.2) as minimum,
        ):
            self.assertTrue(self.support._victron_ess_balance_should_rollback_stable_tuning(svc, metrics, 10.0))
        window.assert_called_once_with(10.0, 20.0)
        immediate.assert_called_once_with(metrics)
        minimum.assert_called_once_with(svc)

    def test_full_restore_state_and_audit_payload_are_exact(self) -> None:
        stable = {"kp": 1.0}
        svc = SimpleNamespace(
            auto_battery_discharge_balance_victron_bias_rollback_enabled=False,
            _victron_ess_balance_last_stable_tuning=stable,
            _victron_ess_balance_last_stable_profile_key="profile",
            _victron_ess_balance_auto_apply_observe_until=99.0,
            _victron_ess_balance_auto_apply_last_applied_at=7.0,
        )
        metrics: dict[str, object] = {}
        with (
            patch.object(self.support, "_apply_victron_ess_balance_restored_tuning") as apply_tuning,
            patch.object(self.support, "_victron_ess_balance_suspend_auto_apply") as suspend,
        ):
            self.assertTrue(self.support._maybe_restore_victron_ess_balance_stable_tuning(svc, metrics, "overshoot"))
        apply_tuning.assert_called_once_with(svc, stable)
        suspend.assert_called_once_with(svc, "overshoot", 7.0)
        self.assertIsNone(svc._victron_ess_balance_auto_apply_observe_until)
        self.assertTrue(svc._victron_ess_balance_safe_state_active)
        self.assertEqual(svc._victron_ess_balance_safe_state_reason, "overshoot")
        self.assertEqual(
            metrics,
            {
                "battery_discharge_balance_victron_bias_rollback_enabled": 0,
                "battery_discharge_balance_victron_bias_rollback_active": 1,
                "battery_discharge_balance_victron_bias_rollback_reason": "overshoot",
                "battery_discharge_balance_victron_bias_rollback_stable_profile_key": "profile",
                "battery_discharge_balance_victron_bias_safe_state_active": 1,
                "battery_discharge_balance_victron_bias_safe_state_reason": "overshoot",
            },
        )

    def test_no_restore_target_still_records_enablement_and_reason(self) -> None:
        svc = SimpleNamespace(
            auto_battery_discharge_balance_victron_bias_rollback_enabled=True,
            _victron_ess_balance_last_stable_tuning=None,
            _victron_ess_balance_conservative_tuning=None,
        )
        metrics: dict[str, object] = {}
        self.assertFalse(self.support._maybe_restore_victron_ess_balance_stable_tuning(svc, metrics, "risk"))
        self.assertEqual(
            metrics,
            {
                "battery_discharge_balance_victron_bias_rollback_enabled": 1,
                "battery_discharge_balance_victron_bias_rollback_reason": "no_stable_tuning",
            },
        )

        default_svc = SimpleNamespace(_victron_ess_balance_last_stable_tuning={"kp": 1.0})
        default_metrics: dict[str, object] = {}
        with (
            patch.object(self.support, "_apply_victron_ess_balance_restored_tuning"),
            patch.object(self.support, "_victron_ess_balance_suspend_auto_apply"),
        ):
            self.assertTrue(
                self.support._maybe_restore_victron_ess_balance_stable_tuning(
                    default_svc, default_metrics, "default"
                )
            )
        self.assertEqual(default_metrics["battery_discharge_balance_victron_bias_rollback_enabled"], 1)
        self.assertEqual(default_metrics["battery_discharge_balance_victron_bias_rollback_stable_profile_key"], "")
        self.assertEqual(default_metrics["battery_discharge_balance_victron_bias_safe_state_reason"], "default")

    def test_restored_field_defaults_and_activation_fallback_are_exact(self) -> None:
        svc = SimpleNamespace()
        self.support._apply_victron_ess_balance_restored_pid_terms(svc, {})
        self.support._apply_victron_ess_balance_restored_limits(svc, {})
        self.assertEqual(
            (
                svc.auto_battery_discharge_balance_victron_bias_kp,
                svc.auto_battery_discharge_balance_victron_bias_ki,
                svc.auto_battery_discharge_balance_victron_bias_kd,
                svc.auto_battery_discharge_balance_victron_bias_deadband_watts,
                svc.auto_battery_discharge_balance_victron_bias_max_abs_watts,
                svc.auto_battery_discharge_balance_victron_bias_ramp_rate_watts_per_second,
            ),
            (0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
        )
        with patch.object(self.support, "_victron_ess_balance_activation_mode", return_value=""):
            self.assertEqual(self.support._victron_ess_balance_restored_activation_mode(svc, {}), "always")
        with (
            patch.object(self.support, "_apply_victron_ess_balance_restored_pid_terms") as pid,
            patch.object(self.support, "_apply_victron_ess_balance_restored_limits") as limits,
            patch.object(self.support, "_victron_ess_balance_restored_activation_mode", return_value="mode") as mode,
        ):
            self.support._apply_victron_ess_balance_restored_tuning(svc, {"kp": 1.0})
        pid.assert_called_once_with(svc, {"kp": 1.0})
        limits.assert_called_once_with(svc, {"kp": 1.0})
        mode.assert_called_once_with(svc, {"kp": 1.0})
        self.assertEqual(svc.auto_battery_discharge_balance_victron_bias_activation_mode, "mode")

    def test_cooldown_clamps_delay_and_suspend_uses_each_time_source(self) -> None:
        for delay, expected in ((1.0, 20.0), (20.0, 80.0), (100.0, 180.0), (None, 40.0)):
            svc = SimpleNamespace(_victron_ess_balance_telemetry_response_delay_seconds=delay)
            with patch.object(self.support, "_victron_ess_balance_suspend_auto_apply"):
                self.support._enter_victron_ess_balance_overshoot_cooldown(svc, 10.0, "reason")
            self.assertEqual(svc._victron_ess_balance_overshoot_cooldown_until, 10.0 + expected)

        cases = (
            (SimpleNamespace(_victron_ess_balance_telemetry_last_observed_at=5.0), 65.0),
            (SimpleNamespace(_victron_ess_balance_auto_apply_last_applied_at=4.0), 64.0),
            (SimpleNamespace(), 60.0),
        )
        for svc, expected in cases:
            self.support._victron_ess_balance_suspend_auto_apply(svc, "reason")
            self.assertEqual(svc._victron_ess_balance_auto_apply_suspend_until, expected)
            self.assertEqual(svc._victron_ess_balance_auto_apply_suspend_reason, "reason")

    def test_ev_direct_source_has_priority_over_learned_fallback(self) -> None:
        svc = SimpleNamespace(
            _last_charger_state_power_w=100.0,
            learned_charge_power_watts=200.0,
            virtual_startstop=1,
        )
        self.assertEqual(self.support._victron_ess_balance_ev_power_w(svc), 100.0)
        self.assertTrue(self.support._victron_ess_balance_ev_active(SimpleNamespace(charging_started_at=0)))
        self.assertIsNone(
            self.support._victron_ess_balance_ev_power_w(SimpleNamespace(learned_charge_power_watts=200.0))
        )
        self.assertFalse(self.support._victron_ess_balance_ev_active(SimpleNamespace()))
        self.assertFalse(self.support._victron_ess_balance_overshoot_cooldown_active(SimpleNamespace(), 1.0))
        self.assertFalse(self.support._victron_ess_balance_auto_apply_suspended(SimpleNamespace(), 1.0))

    def test_missing_restore_targets_and_cooldown_delay_are_safe(self) -> None:
        self.assertEqual(self.support._victron_ess_balance_restore_target(SimpleNamespace(), "reason"), (None, ""))
        self.assertEqual(
            self.support._victron_ess_balance_restore_target(
                SimpleNamespace(_victron_ess_balance_conservative_tuning=["invalid"]), "reason"
            ),
            (None, ""),
        )
        svc = SimpleNamespace()
        with patch.object(self.support, "_victron_ess_balance_suspend_auto_apply"):
            self.support._enter_victron_ess_balance_overshoot_cooldown(svc, 10.0, "reason")
        self.assertEqual(svc._victron_ess_balance_overshoot_cooldown_until, 50.0)


if __name__ == "__main__":
    unittest.main()

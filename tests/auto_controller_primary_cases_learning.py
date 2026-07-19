# SPDX-License-Identifier: GPL-3.0-or-later
from tests.auto_controller_primary_cases_common import *


class _AutoControllerPrimaryLearningCases:
    def test_battery_learning_behavior_prefers_night_support_bias_when_time_is_night(self):
        controller, service = self._make_controller()
        service.time_now = lambda: datetime(2026, 4, 22, 23, 0).timestamp()

        behavior = controller.battery_learning._battery_learning_behavior(
            {
                "support_bias": 0.0,
                "day_support_bias": 1.0,
                "night_support_bias": -0.5,
                "battery_first_export_bias": 0.25,
                "power_smoothing_ratio": 0.8,
                "reserve_band_floor_soc": 35.0,
                "reserve_band_ceiling_soc": 85.0,
                "reserve_band_width_soc": 50.0,
            }
        )

        self.assertEqual(behavior["support_bias"], -0.5)
        self.assertEqual(behavior["day_support_bias"], 1.0)
        self.assertEqual(behavior["night_support_bias"], -0.5)
        self.assertEqual(behavior["battery_first_export_bias"], 0.25)
        self.assertEqual(behavior["power_smoothing_ratio"], 0.8)
        self.assertEqual(behavior["reserve_band_floor_soc"], 35.0)
        self.assertEqual(behavior["reserve_band_ceiling_soc"], 85.0)
        self.assertEqual(behavior["reserve_band_width_soc"], 50.0)

    def test_learning_period_helpers_fall_back_to_default_bias_for_unknown_period(self):
        controller, service = self._make_controller()
        service.time_now = lambda: "bad"

        self.assertIsNone(controller.battery_learning._current_learning_period())
        self.assertEqual(
            controller.battery_learning._support_bias_for_current_period(
                default_bias=0.25,
                day_bias=1.0,
                night_bias=-0.5,
            ),
            0.25,
        )

    def test_adaptive_stop_alpha_uses_stable_medium_and_volatile_stages(self):
        controller, service = self._make_controller()
        service.auto_samples = deque(
            [
                (1.0, 1000.0, 0.0),
                (2.0, 1020.0, 0.0),
                (3.0, 980.0, 0.0),
            ]
        )
        alpha, stage, volatility = controller.samples._adaptive_stop_alpha()
        self.assertEqual(alpha, 0.55)
        self.assertEqual(stage, "stable")
        self.assertAlmostEqual(volatility, 16.33, places=2)

        service.auto_samples = deque(
            [
                (1.0, 1000.0, 0.0),
                (2.0, 1200.0, 0.0),
                (3.0, 800.0, 0.0),
            ]
        )
        alpha, stage, volatility = controller.samples._adaptive_stop_alpha()
        self.assertEqual(alpha, 0.25)
        self.assertEqual(stage, "medium")
        self.assertGreater(volatility, 150.0)
        self.assertLess(volatility, 400.0)

        service.auto_samples = deque(
            [
                (1.0, 1000.0, 0.0),
                (2.0, 1800.0, 0.0),
                (3.0, 200.0, 0.0),
            ]
        )
        alpha, stage, volatility = controller.samples._adaptive_stop_alpha()
        self.assertEqual(alpha, 0.15)
        self.assertEqual(stage, "volatile")
        self.assertGreaterEqual(volatility, 400.0)

    def test_surplus_stop_uses_own_delay_but_grid_stop_stays_hard(self):
        controller, service = self._make_controller()
        service.relay_last_changed_at = -1000.0
        service.auto_stop_condition_since = 100.0
        service.auto_stop_condition_reason = "auto-stop-surplus"

        self.assertTrue(controller.relay.handle_relay_on(1000.0, 0.0, 45.0, True, 150.0, False))
        self.assertEqual(service._last_health_reason, "running")

        self.assertFalse(controller.relay.handle_relay_on(1000.0, 0.0, 45.0, True, 191.0, False))
        self.assertEqual(service._last_health_reason, "auto-stop")

        service.auto_stop_condition_since = 100.0
        service.auto_stop_condition_reason = "auto-stop-grid"
        self.assertFalse(controller.relay.handle_relay_on(1700.0, 400.0, 60.0, True, 131.0, False))
        self.assertEqual(service._last_health_reason, "auto-stop")

    def test_high_soc_uses_more_aggressive_start_and_stop_surplus_thresholds(self):
        controller, service = self._make_controller()

        self.assertEqual(controller.learning._surplus_thresholds_for_soc(50.0), (2000.0, 1600.0, "normal"))
        self.assertEqual(controller.learning._surplus_thresholds_for_soc(55.0), (1650.0, 800.0, "high-soc"))

        self.assertTrue(
            controller.relay._relay_off_start_conditions_met(
                True,
                True,
                1700.0,
                0.0,
                55.0,
                1650.0,
                service.auto_policy,
            )
        )
        self.assertFalse(
            controller.relay._relay_off_start_conditions_met(
                True,
                True,
                1700.0,
                0.0,
                50.0,
                2000.0,
                service.auto_policy,
            )
        )
        self.assertIsNone(controller.relay._relay_on_stop_reason(1000.0, 0.0, 55.0, True, True))
        service._auto_high_soc_profile_active = False
        self.assertEqual(controller.relay._relay_on_stop_reason(1000.0, 0.0, 50.0, True, True), "auto-stop-surplus")

    def test_high_soc_profile_uses_release_hysteresis_before_falling_back_to_normal_thresholds(self):
        controller, service = self._make_controller()

        self.assertEqual(controller.learning._surplus_thresholds_for_soc(55.0), (1650.0, 800.0, "high-soc"))
        self.assertTrue(service._auto_high_soc_profile_active)
        self.assertEqual(controller.learning._surplus_thresholds_for_soc(49.0), (1650.0, 800.0, "high-soc"))
        self.assertTrue(service._auto_high_soc_profile_active)
        self.assertEqual(controller.learning._surplus_thresholds_for_soc(44.0), (2000.0, 1600.0, "normal"))
        self.assertFalse(service._auto_high_soc_profile_active)

    def test_non_auto_mode_sets_explicit_idle_state(self):
        controller, service = self._make_controller()
        service.virtual_mode = 0

        result = controller.gates.handle_non_auto_mode(True)

        self.assertTrue(result)
        self.assertEqual(service._last_auto_state, "idle")
        self.assertEqual(service._last_auto_state_code, 0)

    def test_learned_charge_power_scales_normal_and_high_soc_surplus_thresholds(self):
        controller, service = self._make_controller()
        service.learned_charge_power_watts = 2280.0
        service.learned_charge_power_updated_at = 995.0
        service.learned_charge_power_state = "stable"

        self.assertEqual(controller.learning._surplus_thresholds_for_soc(50.0), (2400.0, 1920.0, "normal"))
        self.assertEqual(controller.learning._surplus_thresholds_for_soc(55.0), (1980.0, 960.0, "high-soc"))

    def test_learned_charge_power_scale_falls_back_to_static_when_disabled_or_invalid(self):
        controller, service = self._make_controller()
        policy = controller.learning._auto_policy()

        policy.learn_charge_power.enabled = False
        service.learned_charge_power_watts = 2280.0
        service.learned_charge_power_updated_at = 995.0
        service.learned_charge_power_state = "stable"
        self.assertEqual(controller.learning._learned_charge_power_scale(), 1.0)

        policy.learn_charge_power.enabled = True
        service.learned_charge_power_watts = 0.0
        self.assertEqual(controller.learning._learned_charge_power_scale(), 1.0)

    def test_learned_charge_power_scale_ignores_stale_runtime_value(self):
        controller, service = self._make_controller()
        service.learned_charge_power_watts = 2280.0
        service.learned_charge_power_updated_at = 100.0
        service.learned_charge_power_state = "stable"
        service.auto_policy.learn_charge_power.max_age_seconds = 60.0

        self.assertEqual(controller.learning._learned_charge_power_scale(), 1.0)
        self.assertIsNone(controller.learning._active_learned_charge_power())

    def test_active_learned_charge_power_covers_unbounded_age_missing_timestamp_and_time_fallback(self):
        controller, service = self._make_controller()
        service.learned_charge_power_watts = 2280.0
        service.learned_charge_power_state = "stable"
        service.auto_policy.learn_charge_power.max_age_seconds = 0.0
        service.learned_charge_power_updated_at = None

        self.assertEqual(controller.learning._active_learned_charge_power(), 2280.0)

        controller.learning._auto_policy().learn_charge_power.max_age_seconds = 60.0
        self.assertIsNone(controller.learning._active_learned_charge_power())

        delattr(service, "time_now")
        service.learned_charge_power_updated_at = 995.0
        with patch("venus_evcharger.auto.logic_learning.time.time", return_value=1000.0):
            self.assertEqual(controller.learning._active_learned_charge_power(), 2280.0)

    def test_learned_charge_power_helpers_cover_invalid_state_and_defensive_none_paths(self):
        controller, service = self._make_controller()
        service.learned_charge_power_watts = 2280.0
        service.learned_charge_power_updated_at = 995.0
        service.learned_charge_power_state = "unsupported"
        self.assertEqual(controller.learning._current_learned_charge_power_state(1000.0), "unknown")

        with patch.object(controller.learning, "_current_learned_charge_power_state", return_value="stable"):
            service.learned_charge_power_watts = None
            self.assertIsNone(controller.learning._active_learned_charge_power())

            service.learned_charge_power_watts = 0.0
            self.assertIsNone(controller.learning._active_learned_charge_power())

            controller.learning._auto_policy().learn_charge_power.max_age_seconds = 60.0
            service.learned_charge_power_watts = 2280.0
            service.learned_charge_power_updated_at = None
            self.assertIsNone(controller.learning._active_learned_charge_power())

            service.learned_charge_power_updated_at = 900.0
            self.assertIsNone(controller.learning._active_learned_charge_power(1000.0))

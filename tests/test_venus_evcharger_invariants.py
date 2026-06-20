# SPDX-License-Identifier: GPL-3.0-or-later
import random
import unittest
from unittest.mock import patch

from venus_evcharger.controllers.auto import AutoDecisionController
from venus_evcharger.auto.policy import (
    AutoLearnChargePowerPolicy,
    AutoPolicy,
    AutoStopEwmaPolicy,
    AutoThresholdProfile,
    validate_auto_policy,
)
from tests.venus_evcharger_test_fixtures import make_auto_controller_service


def _health_code(reason: str) -> int:
    return {
        "grid-missing": 1,
        "inputs-missing": 2,
        "auto-start": 3,
        "battery-soc-missing": 4,
        "battery-soc-missing-allowed": 5,
        "waiting-grid": 6,
        "waiting": 7,
        "autostart-disabled": 8,
        "averaging": 9,
        "mode-transition": 10,
        "waiting-grid-recovery": 11,
        "waiting-surplus": 12,
        "waiting-soc": 13,
        "running": 14,
        "auto-stop": 15,
    }.get(reason, 99)


def _mode_uses_auto_logic(mode) -> bool:
    return int(mode) in (1, 2)


class TestShellyWallboxInvariants(unittest.TestCase):
    def _make_controller(self, **service_overrides):
        service = make_auto_controller_service(**service_overrides)
        controller = AutoDecisionController(service, _health_code, _mode_uses_auto_logic)
        service._clear_auto_samples = controller.clear_auto_samples
        service._set_health = controller.set_health
        service._get_available_surplus_watts = controller.get_available_surplus_watts
        service._add_auto_sample = controller.add_auto_sample
        service._average_auto_metric = controller.average_auto_metric
        service._is_within_auto_daytime_window = lambda: True
        return controller, service

    def test_auto_controller_uses_public_service_methods_when_not_bound_back_to_self(self):
        class PublicService:
            def get_available_surplus_watts(self, _pv_power, _grid_power):
                return 123.5

            def write_auto_audit_event(self, reason, cached=False):
                return f"{reason}:{int(cached)}"

        controller = AutoDecisionController(PublicService(), _health_code, _mode_uses_auto_logic)

        self.assertEqual(controller.available_surplus_watts(2000, -1000), 123.5)
        self.assertEqual(controller.write_auto_audit_event("running", cached=True), "running:1")

    @staticmethod
    def _random_policy(rng: random.Random) -> AutoPolicy:
        normal_start = rng.uniform(700.0, 4200.0)
        normal_stop = rng.uniform(0.0, normal_start)
        high_start = rng.uniform(500.0, normal_start)
        high_stop = rng.uniform(0.0, high_start)
        high_soc_threshold = rng.uniform(35.0, 90.0)
        high_soc_release_threshold = rng.uniform(20.0, high_soc_threshold)
        min_soc = rng.uniform(5.0, 80.0)
        resume_soc = rng.uniform(min_soc, min(100.0, min_soc + 20.0))
        start_import = rng.uniform(0.0, 250.0)
        stop_import = rng.uniform(start_import, start_import + 600.0)
        reference_power = rng.uniform(600.0, 4200.0)
        return validate_auto_policy(
            AutoPolicy(
                normal_profile=AutoThresholdProfile(normal_start, normal_stop),
                high_soc_profile=AutoThresholdProfile(high_start, high_stop),
                high_soc_threshold=high_soc_threshold,
                high_soc_release_threshold=high_soc_release_threshold,
                min_soc=min_soc,
                resume_soc=resume_soc,
                start_max_grid_import_watts=start_import,
                stop_grid_import_watts=stop_import,
                grid_recovery_start_seconds=rng.uniform(0.0, 60.0),
                stop_surplus_delay_seconds=rng.uniform(0.0, 180.0),
                ewma=AutoStopEwmaPolicy(
                    base_alpha=0.35,
                    stable_alpha=0.55,
                    volatile_alpha=0.15,
                    volatility_low_watts=150.0,
                    volatility_high_watts=400.0,
                ),
                learn_charge_power=AutoLearnChargePowerPolicy(
                    enabled=True,
                    reference_power_watts=reference_power,
                    min_watts=rng.uniform(0.0, reference_power * 0.6),
                    alpha=rng.uniform(0.05, 1.0),
                    start_delay_seconds=rng.uniform(0.0, 60.0),
                    window_seconds=rng.uniform(60.0, 600.0),
                    max_age_seconds=rng.uniform(600.0, 86400.0),
                ),
            )
        )

    def _run_auto_sequence(self, samples):
        controller, service = self._make_controller()
        service.auto_startup_warmup_seconds = 0.0
        service.started_at = 0.0
        service.auto_average_window_seconds = 5.0
        service.auto_start_delay_seconds = 10.0
        service.auto_stop_delay_seconds = 20.0
        service.auto_stop_surplus_delay_seconds = 20.0
        service.auto_min_runtime_seconds = 60.0
        service.auto_min_offtime_seconds = 60.0
        service.auto_start_max_grid_import_watts = 50.0
        relay_on = False
        transitions = 0
        now = 0.0

        for pv_power, battery_soc, grid_power, step_seconds in samples:
            now += float(step_seconds)
            service._last_grid_at = now
            service._time_now = lambda current=now: current
            with patch("venus_evcharger.auto.workflow.time.time", return_value=now):
                desired_relay = controller.auto_decide_relay(relay_on, pv_power, battery_soc, grid_power)
            if desired_relay != relay_on:
                relay_on = desired_relay
                transitions += 1
                controller.mark_relay_changed(relay_on, now)

        return transitions, relay_on

    def test_threshold_invariants_hold_across_generated_policies(self):
        rng = random.Random(42042)

        for case_index in range(100):
            controller, service = self._make_controller()
            service.auto_policy = self._random_policy(rng)
            service.learned_charge_power_watts = rng.uniform(1.0, 11000.0)
            service.learned_charge_power_updated_at = 995.0
            service.learned_charge_power_state = "stable"

            with self.subTest(case=case_index, profile="normal"):
                service._auto_high_soc_profile_active = None
                start_threshold, stop_threshold, _ = controller._surplus_thresholds_for_soc(
                    service.auto_policy.high_soc_release_threshold - 1.0
                )
                self.assertGreaterEqual(start_threshold, 0.0)
                self.assertGreaterEqual(stop_threshold, 0.0)
                self.assertLessEqual(stop_threshold, start_threshold)
                self.assertGreaterEqual(controller._learned_charge_power_scale(1000.0), 0.0)

            with self.subTest(case=case_index, profile="high-soc"):
                service._auto_high_soc_profile_active = None
                start_threshold, stop_threshold, _ = controller._surplus_thresholds_for_soc(
                    service.auto_policy.high_soc_threshold + 1.0
                )
                self.assertGreaterEqual(start_threshold, 0.0)
                self.assertGreaterEqual(stop_threshold, 0.0)
                self.assertLessEqual(stop_threshold, start_threshold)
                self.assertGreaterEqual(controller._learned_charge_power_scale(1000.0), 0.0)

    def test_stale_learned_values_never_change_thresholds(self):
        rng = random.Random(1701)

        for case_index in range(80):
            policy = self._random_policy(rng)
            stale_controller, stale_service = self._make_controller(auto_policy=policy)
            static_controller, static_service = self._make_controller(auto_policy=policy)

            stale_service.learned_charge_power_watts = rng.uniform(1000.0, 8000.0)
            stale_service.learned_charge_power_updated_at = 100.0
            stale_service.learned_charge_power_state = "stable"
            stale_service.auto_policy.learn_charge_power.max_age_seconds = rng.uniform(1.0, 120.0)

            static_service.learned_charge_power_watts = None
            static_service.learned_charge_power_updated_at = None
            static_service.learned_charge_power_state = "unknown"

            for profile_name, soc_value in (
                ("normal", policy.high_soc_release_threshold - 1.0),
                ("high-soc", policy.high_soc_threshold + 1.0),
            ):
                with self.subTest(case=case_index, profile=profile_name):
                    stale_service._auto_high_soc_profile_active = None
                    static_service._auto_high_soc_profile_active = None
                    stale_thresholds = stale_controller._surplus_thresholds_for_soc(soc_value)
                    static_thresholds = static_controller._surplus_thresholds_for_soc(soc_value)
                    self.assertEqual(stale_thresholds, static_thresholds)
                    self.assertEqual(stale_controller._learned_charge_power_scale(1000.0), 1.0)
                    self.assertIsNone(stale_controller._active_learned_charge_power(1000.0))

    def test_short_surplus_dips_do_not_cause_relay_chatter(self):
        for low_pulse_samples in (1, 2, 3):
            samples = []
            for _cycle in range(8):
                samples.extend([(2600.0, 60.0, -2200.0, 5.0)] * 4)
                samples.extend([(150.0, 60.0, 400.0, 5.0)] * low_pulse_samples)
            samples.extend([(150.0, 60.0, 400.0, 5.0)] * 14)

            with self.subTest(low_pulse_samples=low_pulse_samples):
                transitions, relay_on = self._run_auto_sequence(samples)
                self.assertEqual(transitions, 2)
                self.assertFalse(relay_on)

# SPDX-License-Identifier: GPL-3.0-or-later
import configparser
import unittest
from unittest.mock import patch

import venus_evcharger.auto.policy as auto_policy_module
from venus_evcharger.auto.policy import (
    AutoLearnChargePowerPolicy,
    AutoPhasePolicy,
    AutoPolicy,
    AutoStopEwmaPolicy,
    AutoThresholdProfile,
)


class TestAutoPolicy(unittest.TestCase):
    def test_non_negative_helper_preserves_zero_and_logs_negative_values(self) -> None:
        with patch("venus_evcharger.auto.policy.logging.warning") as warning_mock:
            self.assertEqual(auto_policy_module._clamp_non_negative_value(0.0, "ExactZero"), 0.0)
            self.assertEqual(auto_policy_module._clamp_non_negative_value(0.5, "SmallPositive"), 0.5)

        warning_mock.assert_not_called()

        with patch("venus_evcharger.auto.policy.logging.warning") as warning_mock:
            self.assertEqual(auto_policy_module._clamp_non_negative_value(-0.5, "NegativeLabel"), 0.0)

        warning_mock.assert_called_once_with(
            "%s %s invalid, clamping to 0",
            "NegativeLabel",
            -0.5,
        )

    def test_ewma_policy_clamps_negative_volatility_bounds(self) -> None:
        policy = AutoStopEwmaPolicy(
            base_alpha=0.35,
            stable_alpha=0.55,
            volatile_alpha=0.15,
            volatility_low_watts=-1.0,
            volatility_high_watts=-2.0,
        )

        policy.clamp()

        self.assertEqual(policy.volatility_low_watts, 0.0)
        self.assertEqual(policy.volatility_high_watts, 0.0)

    def test_percentage_helper_clamps_out_of_range_values(self) -> None:
        self.assertEqual(AutoPolicy._clamp_percentage(120.0, "AutoMinSoc"), 100.0)
        self.assertEqual(AutoPolicy._clamp_percentage(-5.0, "AutoResumeSoc"), 0.0)

    def test_learn_charge_policy_clamps_invalid_values(self) -> None:
        policy = AutoLearnChargePowerPolicy(
            enabled=True,
            reference_power_watts=-1.0,
            min_watts=-1.0,
            alpha=-1.0,
            start_delay_seconds=-1.0,
            window_seconds=-1.0,
            max_age_seconds=-1.0,
        )

        policy.clamp()

        self.assertEqual(policy.reference_power_watts, 1900.0)
        self.assertEqual(policy.min_watts, 0.0)
        self.assertEqual(policy.alpha, 0.2)
        self.assertEqual(policy.start_delay_seconds, 0.0)
        self.assertEqual(policy.window_seconds, 0.0)
        self.assertEqual(policy.max_age_seconds, 0.0)

    def test_auto_policy_from_config_reads_learning_settings(self) -> None:
        parser = configparser.ConfigParser()
        parser.read_dict(
            {
                "DEFAULT": {
                    "AutoLearnChargePower": "0",
                    "AutoReferenceChargePowerWatts": "2050",
                    "AutoLearnChargePowerMinWatts": "650",
                    "AutoLearnChargePowerAlpha": "0.3",
                    "AutoLearnChargePowerStartDelaySeconds": "40",
                    "AutoLearnChargePowerWindowSeconds": "120",
                    "AutoLearnChargePowerMaxAgeSeconds": "1800",
                }
            }
        )

        policy = AutoPolicy.from_config(parser["DEFAULT"])

        self.assertFalse(policy.learn_charge_power.enabled)
        self.assertEqual(policy.learn_charge_power.reference_power_watts, 2050.0)
        self.assertEqual(policy.learn_charge_power.min_watts, 650.0)
        self.assertEqual(policy.learn_charge_power.alpha, 0.3)
        self.assertEqual(policy.learn_charge_power.start_delay_seconds, 40.0)
        self.assertEqual(policy.learn_charge_power.window_seconds, 120.0)
        self.assertEqual(policy.learn_charge_power.max_age_seconds, 1800.0)

    def test_auto_policy_from_config_reads_phase_switch_settings(self) -> None:
        parser = configparser.ConfigParser()
        parser.read_dict(
            {
                "DEFAULT": {
                    "AutoPhaseSwitching": "0",
                    "AutoPhaseUpshiftDelaySeconds": "90",
                    "AutoPhaseDownshiftDelaySeconds": "45",
                    "AutoPhaseUpshiftHeadroomWatts": "400",
                    "AutoPhaseDownshiftMarginWatts": "200",
                    "AutoPhaseMismatchRetrySeconds": "600",
                    "AutoPhaseMismatchLockoutCount": "4",
                    "AutoPhaseMismatchLockoutSeconds": "2400",
                    "AutoPhasePreferLowestWhenIdle": "0",
                }
            }
        )

        policy = AutoPolicy.from_config(parser["DEFAULT"])

        self.assertFalse(policy.phase.enabled)
        self.assertEqual(policy.phase.upshift_delay_seconds, 90.0)
        self.assertEqual(policy.phase.downshift_delay_seconds, 45.0)
        self.assertEqual(policy.phase.upshift_headroom_watts, 400.0)
        self.assertEqual(policy.phase.downshift_margin_watts, 200.0)
        self.assertEqual(policy.phase.mismatch_retry_seconds, 600.0)
        self.assertEqual(policy.phase.mismatch_lockout_count, 4)
        self.assertEqual(policy.phase.mismatch_lockout_seconds, 2400.0)
        self.assertFalse(policy.phase.prefer_lowest_phase_when_idle)

    def test_auto_phase_policy_clamps_negative_values(self) -> None:
        policy = AutoPhasePolicy(
            enabled=True,
            upshift_delay_seconds=-1.0,
            downshift_delay_seconds=-2.0,
            upshift_headroom_watts=-3.0,
            downshift_margin_watts=-4.0,
            mismatch_retry_seconds=-5.0,
            mismatch_lockout_count=-6,
            mismatch_lockout_seconds=-7.0,
        )

        policy.clamp()

        self.assertEqual(policy.upshift_delay_seconds, 0.0)
        self.assertEqual(policy.downshift_delay_seconds, 0.0)
        self.assertEqual(policy.upshift_headroom_watts, 0.0)
        self.assertEqual(policy.downshift_margin_watts, 0.0)
        self.assertEqual(policy.mismatch_retry_seconds, 0.0)
        self.assertEqual(policy.mismatch_lockout_count, 0)
        self.assertEqual(policy.mismatch_lockout_seconds, 0.0)

    def test_learn_charge_policy_warns_about_semantically_odd_values(self) -> None:
        policy = AutoLearnChargePowerPolicy(
            enabled=True,
            reference_power_watts=1900.0,
            min_watts=1950.0,
            alpha=0.2,
            start_delay_seconds=60.0,
            window_seconds=30.0,
            max_age_seconds=20.0,
        )

        with patch("venus_evcharger.auto.policy.logging.warning") as warning_mock:
            policy.clamp()

        warning_messages = [call.args[0] for call in warning_mock.call_args_list]
        self.assertIn(
            "AutoLearnChargePowerWindowSeconds %s below AutoLearnChargePowerStartDelaySeconds %s, learning may never leave the initial delay window",
            warning_messages,
        )
        self.assertIn(
            "AutoLearnChargePowerMaxAgeSeconds %s below AutoLearnChargePowerWindowSeconds %s, learned power may expire before a full learning window completes",
            warning_messages,
        )
        self.assertIn(
            "AutoLearnChargePowerMinWatts %s at or above AutoReferenceChargePowerWatts %s, learning may reject normal charging sessions",
            warning_messages,
        )

    def test_learn_charge_policy_skips_semantic_warnings_when_learning_is_disabled(self) -> None:
        policy = AutoLearnChargePowerPolicy(
            enabled=False,
            reference_power_watts=1900.0,
            min_watts=1950.0,
            alpha=0.2,
            start_delay_seconds=60.0,
            window_seconds=30.0,
            max_age_seconds=20.0,
        )

        with patch("venus_evcharger.auto.policy.logging.warning") as warning_mock:
            policy.clamp()

        warning_mock.assert_not_called()

    def test_auto_policy_warns_about_small_hysteresis_relationships(self) -> None:
        policy = AutoPolicy(
            normal_profile=AutoThresholdProfile(1500.0, 1420.0),
            high_soc_profile=AutoThresholdProfile(1200.0, 1140.0),
            high_soc_threshold=50.0,
            high_soc_release_threshold=49.0,
            min_soc=30.0,
            resume_soc=30.5,
            start_max_grid_import_watts=100.0,
            stop_grid_import_watts=100.0,
        )

        with patch("venus_evcharger.auto.policy.logging.warning") as warning_mock:
            policy.clamp()

        warning_messages = [call.args[0] for call in warning_mock.call_args_list]
        self.assertIn(
            "%s surplus gap %.1f W between %s %.1f and %s %.1f is very small, relay chatter risk may increase",
            warning_messages,
        )
        self.assertIn(
            "AutoHighSocThreshold %s and AutoHighSocReleaseThreshold %s leave very little SOC hysteresis, profile switching may flap",
            warning_messages,
        )
        self.assertIn(
            "AutoResumeSoc %s is very close to AutoMinSoc %s, SOC-based start/stop hysteresis is very small",
            warning_messages,
        )
        self.assertIn(
            "AutoStopGridImportWatts %s at or below AutoStartMaxGridImportWatts %s, grid-import hysteresis is zero or negative",
            warning_messages,
        )

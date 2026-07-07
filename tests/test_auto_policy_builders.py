from __future__ import annotations

import configparser
import unittest
from types import SimpleNamespace
from typing import cast

from venus_evcharger.auto.policy import AutoPolicy
from venus_evcharger.auto.policy_builders import (
    build_auto_policy_from_config,
    build_auto_policy_from_service,
    load_auto_policy_from_config,
    validate_auto_policy,
)


def defaults_section(values: dict[str, str]) -> configparser.SectionProxy:
    parser = configparser.ConfigParser()
    parser.read_dict({"DEFAULT": values})
    return parser["DEFAULT"]


class CaseSensitiveDefaults:
    def __init__(self, values: dict[str, str]) -> None:
        self.values = dict(values)

    def get(self, key: str, fallback: object | None = None) -> object:
        return self.values.get(key, fallback)


def case_sensitive_defaults(values: dict[str, str]) -> configparser.SectionProxy:
    return cast(configparser.SectionProxy, CaseSensitiveDefaults(values))


class FakePolicy:
    def __init__(self) -> None:
        self.applied_to: object | None = None
        self.clamped = False

    def clamp(self) -> None:
        self.clamped = True

    def apply_to_service(self, svc: object) -> None:
        self.applied_to = svc


class TestAutoPolicyBuilders(unittest.TestCase):
    def assert_policy_values(self, policy: AutoPolicy, expected: dict[str, object]) -> None:
        self.assertEqual(policy.normal_profile.start_surplus_watts, expected["normal_start"])
        self.assertEqual(policy.normal_profile.stop_surplus_watts, expected["normal_stop"])
        self.assertEqual(policy.high_soc_profile.start_surplus_watts, expected["high_start"])
        self.assertEqual(policy.high_soc_profile.stop_surplus_watts, expected["high_stop"])
        self.assertEqual(policy.high_soc_threshold, expected["high_threshold"])
        self.assertEqual(policy.high_soc_release_threshold, expected["high_release"])
        self.assertEqual(policy.min_soc, expected["min_soc"])
        self.assertEqual(policy.resume_soc, expected["resume_soc"])
        self.assertEqual(policy.start_max_grid_import_watts, expected["start_grid"])
        self.assertEqual(policy.stop_grid_import_watts, expected["stop_grid"])
        self.assertEqual(policy.grid_recovery_start_seconds, expected["grid_recovery"])
        self.assertEqual(policy.stop_surplus_delay_seconds, expected["stop_surplus_delay"])
        self.assertEqual(policy.ewma.base_alpha, expected["ewma_base"])
        self.assertEqual(policy.ewma.stable_alpha, expected["ewma_stable"])
        self.assertEqual(policy.ewma.volatile_alpha, expected["ewma_volatile"])
        self.assertEqual(policy.ewma.volatility_low_watts, expected["volatility_low"])
        self.assertEqual(policy.ewma.volatility_high_watts, expected["volatility_high"])
        self.assertIs(policy.learn_charge_power.enabled, expected["learn_enabled"])
        self.assertEqual(policy.learn_charge_power.reference_power_watts, expected["learn_reference"])
        self.assertEqual(policy.learn_charge_power.min_watts, expected["learn_min"])
        self.assertEqual(policy.learn_charge_power.alpha, expected["learn_alpha"])
        self.assertEqual(policy.learn_charge_power.start_delay_seconds, expected["learn_start_delay"])
        self.assertEqual(policy.learn_charge_power.window_seconds, expected["learn_window"])
        self.assertEqual(policy.learn_charge_power.max_age_seconds, expected["learn_max_age"])
        self.assertIs(policy.phase.enabled, expected["phase_enabled"])
        self.assertEqual(policy.phase.upshift_delay_seconds, expected["phase_upshift_delay"])
        self.assertEqual(policy.phase.downshift_delay_seconds, expected["phase_downshift_delay"])
        self.assertEqual(policy.phase.upshift_headroom_watts, expected["phase_upshift_headroom"])
        self.assertEqual(policy.phase.downshift_margin_watts, expected["phase_downshift_margin"])
        self.assertEqual(policy.phase.mismatch_retry_seconds, expected["phase_retry"])
        self.assertEqual(policy.phase.mismatch_lockout_count, expected["phase_lockout_count"])
        self.assertEqual(policy.phase.mismatch_lockout_seconds, expected["phase_lockout_seconds"])
        self.assertIs(policy.phase.prefer_lowest_phase_when_idle, expected["phase_prefer_lowest"])

    def test_build_auto_policy_from_config_reads_every_config_key(self) -> None:
        policy = build_auto_policy_from_config(
            AutoPolicy,
            case_sensitive_defaults(
                {
                    "AutoStartSurplusWatts": "2101",
                    "AutoStopSurplusWatts": "1702",
                    "AutoHighSocStartSurplusWatts": "2403",
                    "AutoHighSocStopSurplusWatts": "1804",
                    "AutoHighSocThreshold": "71",
                    "AutoHighSocReleaseThreshold": "63",
                    "AutoMinSoc": "31",
                    "AutoResumeSoc": "36",
                    "AutoStartMaxGridImportWatts": "55",
                    "AutoStopGridImportWatts": "355",
                    "AutoGridRecoveryStartSeconds": "14",
                    "AutoStopSurplusDelaySeconds": "27",
                    "AutoStopEwmaAlpha": "0.31",
                    "AutoStopEwmaAlphaStable": "0.61",
                    "AutoStopEwmaAlphaVolatile": "0.11",
                    "AutoStopSurplusVolatilityLowWatts": "141",
                    "AutoStopSurplusVolatilityHighWatts": "461",
                    "AutoLearnChargePower": "no",
                    "AutoReferenceChargePowerWatts": "2011",
                    "AutoLearnChargePowerMinWatts": "611",
                    "AutoLearnChargePowerAlpha": "0.27",
                    "AutoLearnChargePowerStartDelaySeconds": "41",
                    "AutoLearnChargePowerWindowSeconds": "121",
                    "AutoLearnChargePowerMaxAgeSeconds": "3601",
                    "AutoPhaseSwitching": "off",
                    "AutoPhaseUpshiftDelaySeconds": "91",
                    "AutoPhaseDownshiftDelaySeconds": "46",
                    "AutoPhaseUpshiftHeadroomWatts": "401",
                    "AutoPhaseDownshiftMarginWatts": "201",
                    "AutoPhaseMismatchRetrySeconds": "601",
                    "AutoPhaseMismatchLockoutCount": "5",
                    "AutoPhaseMismatchLockoutSeconds": "2401",
                    "AutoPhasePreferLowestWhenIdle": "false",
                }
            ),
        )

        self.assert_policy_values(
            policy,
            {
                "normal_start": 2101.0,
                "normal_stop": 1702.0,
                "high_start": 2403.0,
                "high_stop": 1804.0,
                "high_threshold": 71.0,
                "high_release": 63.0,
                "min_soc": 31.0,
                "resume_soc": 36.0,
                "start_grid": 55.0,
                "stop_grid": 355.0,
                "grid_recovery": 14.0,
                "stop_surplus_delay": 27.0,
                "ewma_base": 0.31,
                "ewma_stable": 0.61,
                "ewma_volatile": 0.11,
                "volatility_low": 141.0,
                "volatility_high": 461.0,
                "learn_enabled": False,
                "learn_reference": 2011.0,
                "learn_min": 611.0,
                "learn_alpha": 0.27,
                "learn_start_delay": 41.0,
                "learn_window": 121.0,
                "learn_max_age": 3601.0,
                "phase_enabled": False,
                "phase_upshift_delay": 91.0,
                "phase_downshift_delay": 46.0,
                "phase_upshift_headroom": 401.0,
                "phase_downshift_margin": 201.0,
                "phase_retry": 601.0,
                "phase_lockout_count": 5,
                "phase_lockout_seconds": 2401.0,
                "phase_prefer_lowest": False,
            },
        )

    def test_build_auto_policy_from_config_uses_documented_fallbacks(self) -> None:
        policy = build_auto_policy_from_config(
            AutoPolicy,
            defaults_section(
                {
                    "AutoStartSurplusWatts": "2000",
                    "AutoMinSoc": "40",
                    "AutoHighSocThreshold": "60",
                    "AutoStartDelaySeconds": "17",
                    "AutoStopDelaySeconds": "23",
                }
            ),
        )

        self.assert_policy_values(
            policy,
            {
                "normal_start": 2000.0,
                "normal_stop": 1600.0,
                "high_start": 2000.0,
                "high_stop": 1600.0,
                "high_threshold": 60.0,
                "high_release": 60.0,
                "min_soc": 40.0,
                "resume_soc": 43.0,
                "start_grid": 50.0,
                "stop_grid": 300.0,
                "grid_recovery": 17.0,
                "stop_surplus_delay": 23.0,
                "ewma_base": 0.35,
                "ewma_stable": 0.55,
                "ewma_volatile": 0.15,
                "volatility_low": 150.0,
                "volatility_high": 400.0,
                "learn_enabled": True,
                "learn_reference": 1900.0,
                "learn_min": 500.0,
                "learn_alpha": 0.2,
                "learn_start_delay": 30.0,
                "learn_window": 180.0,
                "learn_max_age": 21600.0,
                "phase_enabled": True,
                "phase_upshift_delay": 120.0,
                "phase_downshift_delay": 30.0,
                "phase_upshift_headroom": 250.0,
                "phase_downshift_margin": 150.0,
                "phase_retry": 300.0,
                "phase_lockout_count": 3,
                "phase_lockout_seconds": 1800.0,
                "phase_prefer_lowest": True,
            },
        )

    def test_build_auto_policy_from_config_uses_case_sensitive_nested_fallback_keys(self) -> None:
        policy = build_auto_policy_from_config(
            AutoPolicy,
            case_sensitive_defaults(
                {
                    "AutoHighSocThreshold": "66",
                    "AutoStartDelaySeconds": "18",
                    "AutoStopDelaySeconds": "24",
                }
            ),
        )

        self.assertEqual(policy.high_soc_release_threshold, 66.0)
        self.assertEqual(policy.grid_recovery_start_seconds, 18.0)
        self.assertEqual(policy.stop_surplus_delay_seconds, 24.0)

    def test_build_auto_policy_from_empty_config_uses_complete_documented_defaults(self) -> None:
        policy = build_auto_policy_from_config(AutoPolicy, defaults_section({}))

        self.assert_policy_values(
            policy,
            {
                "normal_start": 1500.0,
                "normal_stop": 1100.0,
                "high_start": 1500.0,
                "high_stop": 1100.0,
                "high_threshold": 50.0,
                "high_release": 50.0,
                "min_soc": 30.0,
                "resume_soc": 33.0,
                "start_grid": 50.0,
                "stop_grid": 300.0,
                "grid_recovery": 10.0,
                "stop_surplus_delay": 10.0,
                "ewma_base": 0.35,
                "ewma_stable": 0.55,
                "ewma_volatile": 0.15,
                "volatility_low": 150.0,
                "volatility_high": 400.0,
                "learn_enabled": True,
                "learn_reference": 1900.0,
                "learn_min": 500.0,
                "learn_alpha": 0.2,
                "learn_start_delay": 30.0,
                "learn_window": 180.0,
                "learn_max_age": 21600.0,
                "phase_enabled": True,
                "phase_upshift_delay": 120.0,
                "phase_downshift_delay": 30.0,
                "phase_upshift_headroom": 250.0,
                "phase_downshift_margin": 150.0,
                "phase_retry": 300.0,
                "phase_lockout_count": 3,
                "phase_lockout_seconds": 1800.0,
                "phase_prefer_lowest": True,
            },
        )

    def test_build_auto_policy_from_config_accepts_all_documented_true_tokens(self) -> None:
        for token in ("1", "true", "yes", "on"):
            with self.subTest(token=token):
                policy = build_auto_policy_from_config(
                    AutoPolicy,
                    defaults_section(
                        {
                            "AutoLearnChargePower": token,
                            "AutoPhaseSwitching": token,
                            "AutoPhasePreferLowestWhenIdle": token,
                        }
                    ),
                )

                self.assertIs(policy.learn_charge_power.enabled, True)
                self.assertIs(policy.phase.enabled, True)
                self.assertIs(policy.phase.prefer_lowest_phase_when_idle, True)

    def test_build_auto_policy_from_config_treats_unknown_bool_tokens_as_false(self) -> None:
        policy = build_auto_policy_from_config(
            AutoPolicy,
            defaults_section(
                {
                    "AutoLearnChargePower": "enabled",
                    "AutoPhaseSwitching": "enabled",
                    "AutoPhasePreferLowestWhenIdle": "enabled",
                }
            ),
        )

        self.assertIs(policy.learn_charge_power.enabled, False)
        self.assertIs(policy.phase.enabled, False)
        self.assertIs(policy.phase.prefer_lowest_phase_when_idle, False)

    def test_build_auto_policy_from_service_reads_every_flat_service_attribute(self) -> None:
        service = SimpleNamespace(
            auto_start_surplus_watts=3101,
            auto_stop_surplus_watts=2702,
            auto_high_soc_start_surplus_watts=3403,
            auto_high_soc_stop_surplus_watts=2804,
            auto_high_soc_threshold=81,
            auto_high_soc_release_threshold=73,
            auto_min_soc=41,
            auto_resume_soc=46,
            auto_start_max_grid_import_watts=65,
            auto_stop_grid_import_watts=365,
            auto_grid_recovery_start_seconds=24,
            auto_stop_surplus_delay_seconds=37,
            auto_stop_ewma_alpha=0.32,
            auto_stop_ewma_alpha_stable=0.62,
            auto_stop_ewma_alpha_volatile=0.12,
            auto_stop_surplus_volatility_low_watts=142,
            auto_stop_surplus_volatility_high_watts=462,
            auto_learn_charge_power_enabled=False,
            auto_reference_charge_power_watts=3011,
            auto_learn_charge_power_min_watts=711,
            auto_learn_charge_power_alpha=0.28,
            auto_learn_charge_power_start_delay_seconds=42,
            auto_learn_charge_power_window_seconds=122,
            auto_learn_charge_power_max_age_seconds=4601,
            auto_phase_switching_enabled=False,
            auto_phase_upshift_delay_seconds=92,
            auto_phase_downshift_delay_seconds=47,
            auto_phase_upshift_headroom_watts=402,
            auto_phase_downshift_margin_watts=202,
            auto_phase_mismatch_retry_seconds=602,
            auto_phase_mismatch_lockout_count=6,
            auto_phase_mismatch_lockout_seconds=2402,
            auto_phase_prefer_lowest_when_idle=False,
        )

        policy = build_auto_policy_from_service(AutoPolicy, service)

        self.assert_policy_values(
            policy,
            {
                "normal_start": 3101.0,
                "normal_stop": 2702.0,
                "high_start": 3403.0,
                "high_stop": 2804.0,
                "high_threshold": 81.0,
                "high_release": 73.0,
                "min_soc": 41.0,
                "resume_soc": 46.0,
                "start_grid": 65.0,
                "stop_grid": 365.0,
                "grid_recovery": 24.0,
                "stop_surplus_delay": 37.0,
                "ewma_base": 0.32,
                "ewma_stable": 0.62,
                "ewma_volatile": 0.12,
                "volatility_low": 142.0,
                "volatility_high": 462.0,
                "learn_enabled": False,
                "learn_reference": 3011.0,
                "learn_min": 711.0,
                "learn_alpha": 0.28,
                "learn_start_delay": 42.0,
                "learn_window": 122.0,
                "learn_max_age": 4601.0,
                "phase_enabled": False,
                "phase_upshift_delay": 92.0,
                "phase_downshift_delay": 47.0,
                "phase_upshift_headroom": 402.0,
                "phase_downshift_margin": 202.0,
                "phase_retry": 602.0,
                "phase_lockout_count": 6,
                "phase_lockout_seconds": 2402.0,
                "phase_prefer_lowest": False,
            },
        )

    def test_build_auto_policy_from_empty_service_uses_complete_documented_defaults(self) -> None:
        policy = build_auto_policy_from_service(AutoPolicy, SimpleNamespace())

        self.assert_policy_values(
            policy,
            {
                "normal_start": 1500.0,
                "normal_stop": 1100.0,
                "high_start": 1500.0,
                "high_stop": 1100.0,
                "high_threshold": 50.0,
                "high_release": 50.0,
                "min_soc": 30.0,
                "resume_soc": 33.0,
                "start_grid": 50.0,
                "stop_grid": 300.0,
                "grid_recovery": 10.0,
                "stop_surplus_delay": 10.0,
                "ewma_base": 0.35,
                "ewma_stable": 0.55,
                "ewma_volatile": 0.15,
                "volatility_low": 150.0,
                "volatility_high": 400.0,
                "learn_enabled": True,
                "learn_reference": 1900.0,
                "learn_min": 500.0,
                "learn_alpha": 0.2,
                "learn_start_delay": 30.0,
                "learn_window": 180.0,
                "learn_max_age": 21600.0,
                "phase_enabled": True,
                "phase_upshift_delay": 120.0,
                "phase_downshift_delay": 30.0,
                "phase_upshift_headroom": 250.0,
                "phase_downshift_margin": 150.0,
                "phase_retry": 300.0,
                "phase_lockout_count": 3,
                "phase_lockout_seconds": 1800.0,
                "phase_prefer_lowest": True,
            },
        )

    def test_build_auto_policy_from_service_uses_documented_defaults_and_high_soc_fallbacks(self) -> None:
        service = SimpleNamespace(auto_start_surplus_watts=2200, auto_stop_surplus_watts=1750, auto_high_soc_threshold=64)

        policy = build_auto_policy_from_service(AutoPolicy, service)

        self.assert_policy_values(
            policy,
            {
                "normal_start": 2200.0,
                "normal_stop": 1750.0,
                "high_start": 2200.0,
                "high_stop": 1750.0,
                "high_threshold": 64.0,
                "high_release": 64.0,
                "min_soc": 30.0,
                "resume_soc": 33.0,
                "start_grid": 50.0,
                "stop_grid": 300.0,
                "grid_recovery": 10.0,
                "stop_surplus_delay": 10.0,
                "ewma_base": 0.35,
                "ewma_stable": 0.55,
                "ewma_volatile": 0.15,
                "volatility_low": 150.0,
                "volatility_high": 400.0,
                "learn_enabled": True,
                "learn_reference": 1900.0,
                "learn_min": 500.0,
                "learn_alpha": 0.2,
                "learn_start_delay": 30.0,
                "learn_window": 180.0,
                "learn_max_age": 21600.0,
                "phase_enabled": True,
                "phase_upshift_delay": 120.0,
                "phase_downshift_delay": 30.0,
                "phase_upshift_headroom": 250.0,
                "phase_downshift_margin": 150.0,
                "phase_retry": 300.0,
                "phase_lockout_count": 3,
                "phase_lockout_seconds": 1800.0,
                "phase_prefer_lowest": True,
            },
        )

    def test_validate_auto_policy_clamps_and_optionally_applies_to_service(self) -> None:
        policy = FakePolicy()

        self.assertIs(validate_auto_policy(policy), policy)
        self.assertTrue(policy.clamped)
        self.assertIsNone(policy.applied_to)

        policy = FakePolicy()
        service = SimpleNamespace()

        self.assertIs(validate_auto_policy(policy, service), policy)
        self.assertTrue(policy.clamped)
        self.assertIs(policy.applied_to, service)

    def test_load_auto_policy_from_config_validates_and_applies_to_service(self) -> None:
        service = SimpleNamespace()

        policy = load_auto_policy_from_config(
            defaults_section({"AutoStartSurplusWatts": "2300", "AutoStopSurplusWatts": "1800"}),
            service,
        )

        self.assertIs(service.auto_policy, policy)
        self.assertEqual(service.auto_start_surplus_watts, 2300.0)
        self.assertEqual(service.auto_stop_surplus_watts, 1800.0)


if __name__ == "__main__":
    unittest.main()

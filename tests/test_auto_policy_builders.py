from __future__ import annotations

import configparser
import unittest
from typing import cast

from venus_evcharger.auto.policy import AutoPolicy
from venus_evcharger.auto.policy_builders import (
    build_auto_policy_from_config,
    load_auto_policy_from_config,
    validate_auto_policy,
)
from venus_evcharger.auto.policy_settings import (
    AUTO_POLICY_SETTING_BY_CONFIG_KEY,
    AUTO_POLICY_SETTING_BY_TARGET,
    AUTO_POLICY_SETTINGS,
    auto_policy_control_values,
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

    def test_validate_auto_policy_clamps_the_canonical_object(self) -> None:
        policy = AutoPolicy(min_soc=-1.0)

        self.assertIs(validate_auto_policy(policy), policy)
        self.assertEqual(policy.min_soc, 0.0)

    def test_load_auto_policy_from_config_returns_the_canonical_policy(self) -> None:
        policy = load_auto_policy_from_config(
            defaults_section({"AutoStartSurplusWatts": "2300", "AutoStopSurplusWatts": "1800"}),
        )

        self.assertEqual(policy.normal_profile.start_surplus_watts, 2300.0)
        self.assertEqual(policy.normal_profile.stop_surplus_watts, 1800.0)

    def test_runtime_setting_registry_has_unique_complete_boundaries(self) -> None:
        self.assertEqual(len(AUTO_POLICY_SETTINGS), 24)
        self.assertEqual(len(AUTO_POLICY_SETTING_BY_TARGET), len(AUTO_POLICY_SETTINGS))
        self.assertEqual(len(AUTO_POLICY_SETTING_BY_CONFIG_KEY), len(AUTO_POLICY_SETTINGS))
        self.assertEqual(
            set(AUTO_POLICY_SETTING_BY_TARGET.values()),
            set(AUTO_POLICY_SETTINGS),
        )
        self.assertEqual(
            set(AUTO_POLICY_SETTING_BY_CONFIG_KEY.values()),
            set(AUTO_POLICY_SETTINGS),
        )

    def test_runtime_settings_read_and_update_only_the_canonical_policy(self) -> None:
        raw_values: dict[str, object] = {
            "auto_start_surplus_watts": 2500,
            "auto_stop_surplus_watts": 900,
            "auto_min_soc": 20,
            "auto_resume_soc": 40,
            "auto_stop_surplus_volatility_high_watts": 500,
            "auto_reference_charge_power_watts": 2100,
            "auto_learn_charge_power_min_watts": 600,
            "auto_learn_charge_power_alpha": 0.4,
            "auto_learn_charge_power_enabled": 0,
            "auto_phase_switching": 0,
            "auto_phase_prefer_lowest_when_idle": 0,
            "auto_phase_mismatch_lockout_count": "7",
        }
        policy = AutoPolicy()

        for setting in AUTO_POLICY_SETTINGS:
            with self.subTest(path=setting.target):
                raw_value = raw_values.get(setting.target, 12)
                stored_value = setting.update(policy, raw_value)
                self.assertEqual(setting.read(policy), stored_value)
                if setting.value_kind == "bool":
                    self.assertIsInstance(stored_value, int)
                elif setting.value_kind == "int":
                    self.assertIsInstance(stored_value, int)
                else:
                    self.assertIsInstance(stored_value, float)

        self.assertEqual(auto_policy_control_values(policy), {
            setting.target: setting.read(policy) for setting in AUTO_POLICY_SETTINGS
        })

    def test_runtime_setting_normalization_rejects_non_scalars(self) -> None:
        setting = AUTO_POLICY_SETTING_BY_TARGET["auto_start_surplus_watts"]

        with self.assertRaisesRegex(TypeError, "requires a scalar value"):
            setting.normalize(object())

    def test_boolean_runtime_setting_preserves_historical_positive_rule(self) -> None:
        setting = AUTO_POLICY_SETTING_BY_TARGET["auto_learn_charge_power_enabled"]

        self.assertIs(setting.normalize("1"), True)
        self.assertIs(setting.normalize(-1), False)


if __name__ == "__main__":
    unittest.main()

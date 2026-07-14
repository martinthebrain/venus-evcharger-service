# SPDX-License-Identifier: GPL-3.0-or-later
"""Contracts for Victron ESS balance learning profiles and payloads."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

from venus_evcharger.update import victron_ess_balance_learning_profiles as profiles_module
from venus_evcharger.update.victron_ess_balance_learning_profiles import (
    _UpdateCycleVictronEssBalanceLearningProfiles,
)


class _ProfilesHarness(_UpdateCycleVictronEssBalanceLearningProfiles):
    @staticmethod
    def _optional_float(value: object) -> float | None:
        return float(value) if isinstance(value, (int, float)) else None

    @staticmethod
    def _ewma_learned_value(current: float | None, sample: float, samples: int) -> float:
        return sample if current is None else ((current * samples) + sample) / (samples + 1)

    @staticmethod
    def _victron_ess_balance_ev_active(svc: object) -> bool:
        return bool(getattr(svc, "ev_active", False))

    @staticmethod
    def _victron_ess_balance_activation_mode(svc: object) -> str:
        return str(getattr(svc, "activation_mode", "always"))


class VictronEssBalanceLearningProfilesContracts(unittest.TestCase):
    def setUp(self) -> None:
        self.profiles = _ProfilesHarness()

    def test_field_schemas_are_exact(self) -> None:
        self.assertEqual(
            self.profiles._victron_ess_balance_profile_scalar_fields(),
            ("key", "action_direction", "site_regime", "direction", "day_phase", "reserve_phase",
             "ev_phase", "pv_phase", "battery_limit_phase"),
        )
        self.assertEqual(
            self.profiles._victron_ess_balance_profile_metric_fields(),
            ("response_delay_seconds", "estimated_gain", "overshoot_count", "settled_count", "stability_score",
             "regime_consistency_score", "response_variance_score", "reproducibility_score",
             "safe_ramp_rate_watts_per_second", "preferred_bias_limit_watts"),
        )

    def test_limit_settings_bands_and_presets(self) -> None:
        missing = SimpleNamespace()
        self.assertEqual(self.profiles._victron_ess_balance_current_limit_settings(missing), (0.0, 0.0))
        configured = SimpleNamespace(
            auto_battery_discharge_balance_victron_bias_ramp_rate_watts_per_second=-2.0,
            auto_battery_discharge_balance_victron_bias_max_abs_watts=1000.0,
        )
        self.assertEqual(self.profiles._victron_ess_balance_current_limit_settings(configured), (0.0, 1000.0))
        positive = SimpleNamespace(
            auto_battery_discharge_balance_victron_bias_ramp_rate_watts_per_second=100.0,
            auto_battery_discharge_balance_victron_bias_max_abs_watts=1000.0,
        )
        self.assertEqual(self.profiles._victron_ess_balance_current_limit_settings(positive), (100.0, 1000.0))
        self.assertEqual(self.profiles._victron_ess_balance_profile_limit_band(0.9, 1), "conservative")
        self.assertEqual(self.profiles._victron_ess_balance_profile_limit_band(0.549, 0), "conservative")
        self.assertEqual(self.profiles._victron_ess_balance_profile_limit_band(0.55, 0), "nominal")
        self.assertEqual(self.profiles._victron_ess_balance_profile_limit_band(0.799, 0), "nominal")
        self.assertEqual(self.profiles._victron_ess_balance_profile_limit_band(0.8, 0), "relaxed")
        self.assertEqual(
            self.profiles._victron_ess_balance_profile_limit_values(0.0, 0.0, "conservative"),
            {"safe_ramp_rate_watts_per_second": 25.0, "preferred_bias_limit_watts": 350.0},
        )
        self.assertEqual(
            self.profiles._victron_ess_balance_profile_limit_values(100.0, 1000.0, "conservative"),
            {"safe_ramp_rate_watts_per_second": 70.0, "preferred_bias_limit_watts": 800.0},
        )
        self.assertEqual(
            self.profiles._victron_ess_balance_profile_limit_values(100.0, 1000.0, "nominal"),
            {"safe_ramp_rate_watts_per_second": 100.0, "preferred_bias_limit_watts": 1000.0},
        )
        self.assertEqual(
            self.profiles._victron_ess_balance_profile_limit_values(0.0, 0.0, "nominal"),
            {"safe_ramp_rate_watts_per_second": 50.0, "preferred_bias_limit_watts": 500.0},
        )
        self.assertEqual(
            self.profiles._victron_ess_balance_profile_limit_values(0.5, 0.5, "nominal"),
            {"safe_ramp_rate_watts_per_second": 0.5, "preferred_bias_limit_watts": 0.5},
        )
        relaxed = self.profiles._victron_ess_balance_profile_limit_values(100.0, 1000.0, "relaxed")
        self.assertAlmostEqual(relaxed["safe_ramp_rate_watts_per_second"], 110.0)
        self.assertEqual(relaxed["preferred_bias_limit_watts"], 1100.0)
        self.assertEqual(
            self.profiles._victron_ess_balance_profile_limit_values(0.0, 0.0, "relaxed"),
            {"safe_ramp_rate_watts_per_second": 60.0, "preferred_bias_limit_watts": 550.0},
        )
        with self.assertRaises(KeyError):
            self.profiles._victron_ess_balance_profile_limit_values(1.0, 1.0, "unknown")

    def test_limit_recommendation_delegates_exact_values(self) -> None:
        svc = SimpleNamespace()
        with (
            patch.object(self.profiles, "_victron_ess_balance_current_limit_settings", return_value=(2.0, 3.0)) as settings,
            patch.object(self.profiles, "_victron_ess_balance_profile_limit_band", return_value="nominal") as band,
            patch.object(self.profiles, "_victron_ess_balance_profile_limit_values", return_value={"x": 1.0}) as values,
        ):
            self.assertEqual(self.profiles._victron_ess_balance_profile_limit_recommendations(svc, 0.7, 2), {"x": 1.0})
        settings.assert_called_once_with(svc)
        band.assert_called_once_with(0.7, 2)
        values.assert_called_once_with(2.0, 3.0, "nominal")

    def test_action_day_reserve_and_battery_phase_boundaries(self) -> None:
        action = self.profiles._victron_ess_balance_action_direction
        self.assertEqual(action(-0.1, 0.0, 0.0), "more_export")
        self.assertEqual(action(0.1, 0.0, 0.0), "less_export")
        self.assertEqual(action(0.5, 100.0, 0.0), "less_export")
        self.assertEqual(action(0.0, 26.0, 25.0), "more_export")
        self.assertEqual(action(0.0, 25.0, 0.0), "less_export")
        self.assertEqual(action(0.0, 30.0, 30.0), "less_export")
        self.assertEqual(self.profiles._victron_ess_balance_day_phase(49.9, 49.9), "night")
        self.assertEqual(self.profiles._victron_ess_balance_day_phase(50.0, 0.0), "day")
        self.assertEqual(self.profiles._victron_ess_balance_day_phase(0.0, 50.0), "day")
        self.assertEqual(self.profiles._victron_ess_balance_reserve_phase({"soc": 55, "discharge_balance_reserve_floor_soc": 50}), "reserve_band")
        self.assertEqual(self.profiles._victron_ess_balance_reserve_phase({"soc": 55.1, "discharge_balance_reserve_floor_soc": 50}), "above_reserve_band")
        self.assertEqual(self.profiles._victron_ess_balance_reserve_phase({}), "above_reserve_band")
        battery = self.profiles._victron_ess_balance_battery_limit_phase
        self.assertEqual(battery("export", 900.0, 300.0), "near_discharge_limit")
        self.assertEqual(battery("import", 300.0, 900.0), "near_charge_limit")
        self.assertEqual(battery("export", 300.0, 300.1), "mid_band")

    def test_site_regime_obeys_grid_forecast_action_precedence(self) -> None:
        with (
            patch.object(profiles_module, "_victron_ess_balance_grid_site_regime", side_effect=("export", "")) as grid,
            patch.object(profiles_module, "_victron_ess_balance_forecast_site_regime", return_value="import") as forecast,
            patch.object(profiles_module, "_victron_ess_balance_action_direction_site_regime", return_value="fallback") as action,
        ):
            self.assertEqual(self.profiles._victron_ess_balance_site_regime(1.0, 2.0, 3.0, "more_export"), "export")
            self.assertEqual(self.profiles._victron_ess_balance_site_regime(4.0, 5.0, 6.0, "less_export"), "import")
        self.assertEqual(grid.call_args_list, [call(1.0), call(4.0)])
        forecast.assert_called_once_with(5.0, 6.0)
        action.assert_not_called()

        with (
            patch.object(profiles_module, "_victron_ess_balance_grid_site_regime", return_value=""),
            patch.object(profiles_module, "_victron_ess_balance_forecast_site_regime", return_value=""),
            patch.object(profiles_module, "_victron_ess_balance_action_direction_site_regime", return_value="fallback") as action,
        ):
            self.assertEqual(self.profiles._victron_ess_balance_site_regime(None, 0.0, 0.0, "more_export"), "fallback")
        action.assert_called_once_with("more_export")

    def test_phase_inputs_normalize_numeric_values(self) -> None:
        cluster = {
            "battery_combined_grid_interaction_w": 1,
            "expected_near_term_export_w": 2,
            "expected_near_term_import_w": 3,
            "battery_combined_pv_input_power_w": 4,
            "battery_headroom_charge_w": 5,
            "battery_headroom_discharge_w": 6,
        }
        self.assertEqual(
            self.profiles._victron_ess_balance_learning_profile_phase_inputs(cluster),
            {"grid_interaction_w": 1.0, "expected_export_w": 2.0, "expected_import_w": 3.0,
             "pv_input_power_w": 4.0, "combined_charge_headroom_w": 5.0,
             "combined_discharge_headroom_w": 6.0},
        )
        self.assertEqual(
            self.profiles._victron_ess_balance_learning_profile_phase_inputs({}),
            {"grid_interaction_w": None, "expected_export_w": 0.0, "expected_import_w": 0.0,
             "pv_input_power_w": 0.0, "combined_charge_headroom_w": None,
             "combined_discharge_headroom_w": None},
        )

    def test_learning_profile_builds_complete_identity(self) -> None:
        svc = SimpleNamespace(ev_active=True)
        cluster = {
            "battery_combined_grid_interaction_w": -30.0,
            "expected_near_term_export_w": 100.0,
            "battery_combined_pv_input_power_w": 1600.0,
            "battery_headroom_discharge_w": 300.0,
        }
        source = {"soc": 50.0, "discharge_balance_reserve_floor_soc": 45.0}
        result = self.profiles._victron_ess_balance_learning_profile(svc, cluster, source, -1.0)
        self.assertEqual(
            result,
            {"key": "more_export:export:day:reserve_band:ev_active:pv_strong:near_discharge_limit",
             "action_direction": "more_export", "site_regime": "export", "direction": "export",
             "day_phase": "day", "reserve_phase": "reserve_band", "ev_phase": "ev_active",
             "pv_phase": "pv_strong", "battery_limit_phase": "near_discharge_limit"},
        )
        idle = self.profiles._victron_ess_balance_learning_profile(
            SimpleNamespace(ev_active=False),
            {
                "battery_combined_grid_interaction_w": 30.0,
                "expected_near_term_export_w": 10.0,
                "expected_near_term_import_w": 40.0,
                "battery_combined_pv_input_power_w": 10.0,
                "battery_headroom_charge_w": 300.0,
                "battery_headroom_discharge_w": 900.0,
            },
            {},
            1.0,
        )
        self.assertEqual(
            idle,
            {"key": "less_export:import:night:above_reserve_band:ev_idle:pv_weak:near_charge_limit",
             "action_direction": "less_export", "site_regime": "import", "direction": "import",
             "day_phase": "night", "reserve_phase": "above_reserve_band", "ev_phase": "ev_idle",
             "pv_phase": "pv_weak", "battery_limit_phase": "near_charge_limit"},
        )

    def test_learning_profile_forwards_phase_inputs_without_loss(self) -> None:
        svc = SimpleNamespace()
        cluster: dict[str, object] = {}
        source: dict[str, object] = {}
        phase_inputs = {
            "grid_interaction_w": 1.0,
            "expected_export_w": 2.0,
            "expected_import_w": 3.0,
            "pv_input_power_w": 4.0,
            "combined_charge_headroom_w": 5.0,
            "combined_discharge_headroom_w": 6.0,
        }
        with (
            patch.object(self.profiles, "_victron_ess_balance_learning_profile_phase_inputs", autospec=True, return_value=phase_inputs) as inputs,
            patch.object(self.profiles, "_victron_ess_balance_action_direction", autospec=True, return_value="action") as action,
            patch.object(self.profiles, "_victron_ess_balance_site_regime", autospec=True, return_value="site") as site,
            patch.object(self.profiles, "_victron_ess_balance_day_phase", autospec=True, return_value="day") as day,
            patch.object(self.profiles, "_victron_ess_balance_ev_active", autospec=True, return_value=False) as ev,
            patch.object(profiles_module, "_victron_ess_balance_pv_phase", autospec=True, return_value="pv") as pv,
            patch.object(self.profiles, "_victron_ess_balance_reserve_phase", autospec=True, return_value="reserve") as reserve,
            patch.object(self.profiles, "_victron_ess_balance_battery_limit_phase", autospec=True, return_value="limit") as limit_phase,
            patch.object(profiles_module, "_victron_ess_balance_learning_profile_key", autospec=True, return_value="key") as key,
        ):
            result = self.profiles._victron_ess_balance_learning_profile(svc, cluster, source, 7.0)
        inputs.assert_called_once_with(cluster)
        action.assert_called_once_with(7.0, 2.0, 3.0)
        site.assert_called_once_with(1.0, 2.0, 3.0, "action")
        day.assert_called_once_with(2.0, 4.0)
        ev.assert_called_once_with(svc)
        pv.assert_called_once_with(2.0, 4.0)
        reserve.assert_called_once_with(source)
        limit_phase.assert_called_once_with("site", 5.0, 6.0)
        key.assert_called_once_with("action", "site", "day", "reserve", "ev_idle", "pv", "limit")
        self.assertEqual(
            result,
            {"key": "key", "action_direction": "action", "site_regime": "site", "direction": "site",
             "day_phase": "day", "reserve_phase": "reserve", "ev_phase": "ev_idle",
             "pv_phase": "pv", "battery_limit_phase": "limit"},
        )

    def test_profile_state_initialization_and_identity(self) -> None:
        svc = SimpleNamespace()
        profiles = self.profiles._victron_ess_balance_learning_profiles(svc)
        self.assertEqual(profiles, {})
        self.assertIs(svc._victron_ess_balance_learning_profiles, profiles)
        self.assertIs(profiles, self.profiles._victron_ess_balance_learning_profiles(svc))
        self.assertEqual(self.profiles._victron_ess_balance_learning_profile_state(svc, ""), {})
        self.assertEqual(self.profiles._ensure_victron_ess_balance_learning_profile_state(svc, ""), {})
        state = self.profiles._ensure_victron_ess_balance_learning_profile_state(svc, "export:day:above")
        self.assertIs(state, profiles["export:day:above"])
        self.assertIs(state, self.profiles._ensure_victron_ess_balance_learning_profile_state(svc, "export:day:above"))
        self.assertIs(state, self.profiles._victron_ess_balance_learning_profile_state(svc, "export:day:above"))
        self.assertEqual(
            state,
            {
                "key": "export:day:above", "action_direction": "", "site_regime": "export",
                "direction": "export", "day_phase": "day", "reserve_phase": "above",
                "ev_phase": "ev_idle", "pv_phase": "pv_weak", "battery_limit_phase": "mid_band",
                "response_delay_seconds": None, "delay_samples": 0, "estimated_gain": None,
                "gain_samples": 0, "response_delay_mad_seconds": None, "gain_mad": None,
                "overshoot_count": 0, "settled_count": 0, "stability_score": None,
                "regime_consistency_score": None, "response_variance_score": None,
                "reproducibility_score": None, "safe_ramp_rate_watts_per_second": None,
                "preferred_bias_limit_watts": None,
            },
        )
        profiles["broken"] = 1
        self.assertEqual(self.profiles._victron_ess_balance_learning_profile_state(svc, "broken"), {})

    def test_sample_count_uses_largest_signal(self) -> None:
        self.assertEqual(self.profiles._victron_ess_balance_profile_sample_count({}), 0)
        self.assertEqual(self.profiles._victron_ess_balance_profile_sample_count({"delay_samples": 4}), 4)
        self.assertEqual(self.profiles._victron_ess_balance_profile_sample_count({"gain_samples": 5}), 5)
        self.assertEqual(
            self.profiles._victron_ess_balance_profile_sample_count({"settled_count": 3, "overshoot_count": 3}),
            6,
        )
        self.assertEqual(self.profiles._victron_ess_balance_profile_sample_count(
            {"delay_samples": 2, "gain_samples": 3, "settled_count": 3, "overshoot_count": 2}), 5)

    def test_metric_snapshot_aliases_and_normalizes(self) -> None:
        profile = {"response_delay_seconds": 2, "estimated_gain": 3, "overshoot_count": -1,
                   "settled_count": 4, "stability_score": 0.5, "safe_ramp_rate_watts_per_second": 20}
        snapshot = self.profiles._victron_ess_balance_profile_metric_snapshot(profile)
        self.assertEqual(
            snapshot,
            {"response_delay_seconds": 2.0, "estimated_gain": 3.0, "overshoot_count": 0,
             "settled_count": 4, "stability_score": 0.5, "regime_consistency_score": None,
             "response_variance_score": None, "reproducibility_score": None,
             "safe_ramp_rate_watts_per_second": 20.0, "preferred_bias_limit_watts": None,
             "typical_response_delay_seconds": 2.0, "effective_gain": 3.0},
        )

    def test_profile_snapshot_and_metric_merge(self) -> None:
        svc = SimpleNamespace()
        state = self.profiles._ensure_victron_ess_balance_learning_profile_state(svc, "export:day:above")
        state.update({"delay_samples": 2, "response_delay_seconds": 3.0, "settled_count": 1})
        snapshot = self.profiles._victron_ess_balance_profile_snapshot(svc, "export:day:above")
        self.assertEqual(snapshot["key"], "export:day:above")
        self.assertEqual(snapshot["sample_count"], 2)
        self.assertEqual(snapshot["site_regime"], "export")
        self.assertEqual(snapshot["response_delay_seconds"], 3.0)
        self.assertEqual(self.profiles._victron_ess_balance_profile_snapshot(svc, "missing"), {})
        aliased = dict(state)
        aliased["key"] = "canonical"
        svc._victron_ess_balance_learning_profiles["alias"] = aliased
        self.assertEqual(self.profiles._victron_ess_balance_profile_snapshot(svc, "alias")["key"], "canonical")
        metrics: dict[str, object] = {}
        self.profiles._merge_victron_ess_balance_learning_profile_metrics(svc, metrics, "export:day:above")
        self.assertEqual(metrics["battery_discharge_balance_victron_bias_learning_profile_sample_count"], 2)
        self.assertEqual(metrics["battery_discharge_balance_victron_bias_learning_profile_site_regime"], "export")
        self.assertEqual(metrics["battery_discharge_balance_victron_bias_learning_profile_settled_count"], 1)
        self.assertEqual(
            set(metrics),
            {
                f"battery_discharge_balance_victron_bias_learning_profile_{field}"
                for field in (*self.profiles._victron_ess_balance_profile_scalar_fields(), "sample_count",
                              *self.profiles._victron_ess_balance_profile_metric_fields())
            },
        )
        empty_metrics: dict[str, object] = {"preserved": True}
        self.profiles._merge_victron_ess_balance_learning_profile_metrics(svc, empty_metrics, "")
        self.assertEqual(empty_metrics, {"preserved": True})

    def test_profile_updates_delegate_exact_fields(self) -> None:
        svc = SimpleNamespace()
        self.profiles._victron_ess_balance_update_profile_delay(svc, "p", 2.0)
        self.profiles._victron_ess_balance_update_profile_gain(svc, "p", 3.0)
        self.profiles._victron_ess_balance_update_profile_delay(svc, "p", 4.0)
        self.profiles._victron_ess_balance_update_profile_gain(svc, "p", 5.0)
        self.profiles._victron_ess_balance_increment_profile_counter(svc, "p", "settled_count")
        state = svc._victron_ess_balance_learning_profiles["p"]
        self.assertEqual(state["response_delay_seconds"], 3.0)
        self.assertEqual(state["delay_samples"], 2)
        self.assertEqual(state["response_delay_mad_seconds"], 2.0)
        self.assertEqual(state["estimated_gain"], 4.0)
        self.assertEqual(state["gain_samples"], 2)
        self.assertEqual(state["gain_mad"], 2.0)
        self.assertEqual(state["settled_count"], 1)
        state["settled_count"] = 2
        with patch.object(self.profiles, "_ensure_victron_ess_balance_learning_profile_state", return_value=state):
            self.profiles._victron_ess_balance_increment_profile_counter(svc, "p", "settled_count")
        self.assertEqual(state["settled_count"], 3)

    def test_refresh_stability_writes_all_derived_metrics(self) -> None:
        svc = SimpleNamespace()
        state = {"settled_count": 4, "overshoot_count": 1, "estimated_gain": 2.0,
                 "response_delay_seconds": 3.0, "response_delay_mad_seconds": 0.5, "gain_mad": 0.25}
        with (
            patch.object(self.profiles, "_victron_ess_balance_learning_profile_state", autospec=True, return_value=state) as profile_state,
            patch.object(self.profiles, "_victron_ess_balance_stability_score_values", autospec=True, return_value=0.6) as stability,
            patch.object(self.profiles, "_victron_ess_balance_variance_score", autospec=True, return_value=0.7) as variance,
            patch.object(self.profiles, "_victron_ess_balance_regime_consistency_score", autospec=True, return_value=0.8) as consistency,
            patch.object(self.profiles, "_victron_ess_balance_reproducibility_score", autospec=True, return_value=0.9) as reproducibility,
            patch.object(self.profiles, "_victron_ess_balance_profile_limit_recommendations", autospec=True, return_value={"limit": 1.0}) as limits,
        ):
            self.profiles._victron_ess_balance_refresh_profile_stability(svc, "p")
        profile_state.assert_called_once_with(svc, "p")
        stability.assert_called_once_with(4, 1, 2.0, 3.0)
        variance.assert_called_once_with(3.0, 0.5, 2.0, 0.25)
        consistency.assert_called_once_with(state)
        reproducibility.assert_called_once_with(state)
        limits.assert_called_once_with(svc, 0.6, 1)
        self.assertEqual((state["stability_score"], state["response_variance_score"],
                          state["regime_consistency_score"], state["reproducibility_score"], state["limit"]),
                         (0.6, 0.7, 0.8, 0.9, 1.0))

        zero_state = dict(state)
        with (
            patch.object(self.profiles, "_victron_ess_balance_learning_profile_state", return_value=zero_state),
            patch.object(self.profiles, "_victron_ess_balance_stability_score_values", return_value=0.0),
            patch.object(self.profiles, "_victron_ess_balance_variance_score", return_value=0.0),
            patch.object(self.profiles, "_victron_ess_balance_regime_consistency_score", return_value=0.0),
            patch.object(self.profiles, "_victron_ess_balance_reproducibility_score", return_value=0.0),
            patch.object(self.profiles, "_victron_ess_balance_profile_limit_recommendations", return_value={}) as limits,
        ):
            self.profiles._victron_ess_balance_refresh_profile_stability(svc, "zero")
        limits.assert_called_once_with(svc, 0.0, 1)

    def test_active_profile_and_adaptive_scalars_delegate_schema(self) -> None:
        svc = SimpleNamespace()
        learning_profile = {
            "key": "k", "action_direction": "a", "site_regime": "s", "direction": "d",
            "day_phase": "day", "reserve_phase": "reserve", "ev_phase": "ev",
            "pv_phase": "pv", "battery_limit_phase": "limit",
        }
        self.profiles._set_victron_ess_balance_active_profile(svc, learning_profile)
        for attr_name, field_name in profiles_module._victron_ess_balance_active_profile_fields():
            self.assertEqual(getattr(svc, attr_name), learning_profile[field_name])
        self.profiles._clear_victron_ess_balance_active_profile(svc)
        for attr_name, _field_name in profiles_module._victron_ess_balance_active_profile_fields():
            self.assertEqual(getattr(svc, attr_name), "")
        with (
            patch.object(profiles_module, "_victron_ess_balance_adaptive_scalar_specs", return_value=(("target", "source", "int"),)) as specs,
            patch.object(self.profiles, "_victron_ess_balance_adaptive_scalar_value", return_value=4) as cast,
        ):
            svc.source = 3
            self.assertEqual(self.profiles._victron_ess_balance_adaptive_scalar_payload(svc), {"target": 4})
        specs.assert_called_once_with()
        cast.assert_called_once_with(3, "int")
        with patch.object(profiles_module, "_victron_ess_balance_adaptive_scalar_value", return_value="cast") as helper:
            self.assertEqual(self.profiles._victron_ess_balance_adaptive_scalar_value("raw", "str"), "cast")
        helper.assert_called_once_with("raw", "str", self.profiles._optional_float)
        with (
            patch.object(profiles_module, "_victron_ess_balance_adaptive_scalar_specs", return_value=(("target", "missing", "str"),)),
            patch.object(self.profiles, "_victron_ess_balance_adaptive_scalar_value", return_value="empty") as cast,
        ):
            self.assertEqual(self.profiles._victron_ess_balance_adaptive_scalar_payload(SimpleNamespace()), {"target": "empty"})
        cast.assert_called_once_with(None, "str")

    def test_topology_and_public_payloads_are_deterministic(self) -> None:
        svc = SimpleNamespace(
            auto_energy_sources=(SimpleNamespace(source_id="b"), SimpleNamespace(source_id="a")),
            auto_battery_discharge_balance_victron_bias_service=" service ",
            auto_battery_discharge_balance_victron_bias_path=" /path ",
            auto_battery_discharge_balance_victron_bias_source_id=" source ",
            _victron_ess_balance_learning_profiles={},
            _victron_ess_balance_last_stable_tuning={"kp": 1},
            _victron_ess_balance_conservative_tuning={"kp": 2},
            activation_mode="export_only",
        )
        self.assertEqual(
            self.profiles._victron_ess_balance_current_topology_key(svc, " source "),
            "victron-bias-learning/v2/source=source/service=service/path=/path/energy=a,b",
        )
        state = self.profiles.victron_ess_balance_learning_state_payload(svc)
        self.assertEqual(state, {"schema_version": 2, "topology_key": self.profiles._victron_ess_balance_current_topology_key(svc, "source"),
                                 "source_id": "source", "profiles": {}})
        svc._victron_ess_balance_learning_profiles = {"b": {}, "a": {}}
        with patch.object(self.profiles, "_victron_ess_balance_profile_snapshot", autospec=True, side_effect=lambda _svc, key: {"key": key}) as snapshot:
            populated = self.profiles.victron_ess_balance_learning_state_payload(svc)
        self.assertEqual(populated["profiles"], {"a": {"key": "a"}, "b": {"key": "b"}})
        self.assertEqual(snapshot.call_args_list, [call(svc, "a"), call(svc, "b")])
        with (
            patch.object(self.profiles, "_victron_ess_balance_current_tuning_snapshot", autospec=True, return_value={"kp": 3}) as tuning_snapshot,
            patch.object(self.profiles, "_victron_ess_balance_adaptive_scalar_payload", autospec=True, return_value={"generation": 4}) as scalar_payload,
        ):
            tuning = self.profiles.victron_ess_balance_adaptive_tuning_payload(svc)
        self.assertEqual(tuning["schema_version"], 2)
        self.assertEqual(tuning["source_id"], "source")
        self.assertEqual(tuning["kp"], 3)
        self.assertEqual(tuning["generation"], 4)
        self.assertEqual(tuning["last_stable_tuning"], {"kp": 1})
        self.assertEqual(tuning["conservative_tuning"], {"kp": 2})
        self.assertEqual(tuning["topology_key"], self.profiles._victron_ess_balance_current_topology_key(svc, "source"))
        tuning_snapshot.assert_called_once_with(svc)
        scalar_payload.assert_called_once_with(svc)

        minimal = SimpleNamespace(
            auto_energy_sources=(),
            auto_battery_discharge_balance_victron_bias_service="",
            auto_battery_discharge_balance_victron_bias_path="",
            auto_battery_discharge_balance_victron_bias_source_id="",
            _victron_ess_balance_learning_profiles={},
            _victron_ess_balance_last_stable_tuning={},
            _victron_ess_balance_conservative_tuning={},
        )
        with (
            patch.object(self.profiles, "_victron_ess_balance_current_tuning_snapshot", return_value={}),
            patch.object(self.profiles, "_victron_ess_balance_adaptive_scalar_payload", return_value={}),
        ):
            minimal_tuning = self.profiles.victron_ess_balance_adaptive_tuning_payload(minimal)
        self.assertEqual(minimal_tuning["last_stable_tuning"], {})
        self.assertEqual(minimal_tuning["conservative_tuning"], {})

    def test_current_tuning_snapshot_uses_all_config_fields(self) -> None:
        svc = SimpleNamespace(
            auto_battery_discharge_balance_victron_bias_kp=1,
            auto_battery_discharge_balance_victron_bias_ki=2,
            auto_battery_discharge_balance_victron_bias_kd=3,
            auto_battery_discharge_balance_victron_bias_deadband_watts=4,
            auto_battery_discharge_balance_victron_bias_max_abs_watts=5,
            auto_battery_discharge_balance_victron_bias_ramp_rate_watts_per_second=6,
            activation_mode="export_only",
        )
        self.assertEqual(
            self.profiles._victron_ess_balance_current_tuning_snapshot(svc),
            {"kp": 1.0, "ki": 2.0, "kd": 3.0, "deadband_watts": 4.0, "max_abs_watts": 5.0,
             "ramp_rate_watts_per_second": 6.0, "activation_mode": "export_only"},
        )


if __name__ == "__main__":
    unittest.main()

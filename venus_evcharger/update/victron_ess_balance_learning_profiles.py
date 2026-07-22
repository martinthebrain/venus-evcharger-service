# SPDX-License-Identifier: GPL-3.0-or-later
"""Victron ESS balance-bias learning-profile helpers.

The helpers in this module keep profile scoring, profile keys, and learned
telemetry fields close together so adaptive decisions remain traceable.
"""

from __future__ import annotations

from typing import Any, TypeGuard, TypedDict

from venus_evcharger.core.ess_topology import ess_learning_topology_key

from .victron_ess_balance_apply_sources import VictronEssSourceResolver
from .victron_ess_balance_learning_profiles_support import (
    _clear_victron_ess_balance_active_profile_state,
    _victron_ess_balance_action_direction_site_regime,
    _victron_ess_balance_active_profile_fields,
    _victron_ess_balance_adaptive_scalar_value,
    _victron_ess_balance_adaptive_scalar_specs,
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
)
from .victron_ess_balance_scoring import VictronEssTelemetryScorer


def _is_learning_profile_store(value: object) -> TypeGuard[dict[str, dict[str, Any]]]:
    return isinstance(value, dict)


class VictronEssLearningProfiles:
    def __init__(self, sources: VictronEssSourceResolver, scorer: VictronEssTelemetryScorer) -> None:
        self._sources = sources
        self._scorer = scorer

    @staticmethod
    def _victron_ess_balance_profile_scalar_fields() -> tuple[str, ...]:
        return (
            "key",
            "action_direction",
            "site_regime",
            "direction",
            "day_phase",
            "reserve_phase",
            "ev_phase",
            "pv_phase",
            "battery_limit_phase",
        )

    @staticmethod
    def _victron_ess_balance_profile_metric_fields() -> tuple[str, ...]:
        return (
            "response_delay_seconds",
            "estimated_gain",
            "overshoot_count",
            "settled_count",
            "stability_score",
            "regime_consistency_score",
            "response_variance_score",
            "reproducibility_score",
            "safe_ramp_rate_watts_per_second",
            "preferred_bias_limit_watts",
        )

    @staticmethod
    def _victron_ess_balance_current_limit_settings(svc: Any) -> tuple[float, float]:
        current_ramp = max(
            0.0,
            _victron_ess_balance_float_attr(
                svc,
                "auto_battery_discharge_balance_victron_bias_ramp_rate_watts_per_second",
            ),
        )
        current_max_abs = max(
            0.0,
            _victron_ess_balance_float_attr(svc, "auto_battery_discharge_balance_victron_bias_max_abs_watts"),
        )
        return current_ramp, current_max_abs

    @staticmethod
    def _victron_ess_balance_profile_limit_band(stability: float, overshoot_count: int) -> str:
        if overshoot_count > 0 or stability < 0.55:
            return "conservative"
        if stability >= 0.8:
            return "relaxed"
        return "nominal"

    @staticmethod
    def _victron_ess_balance_profile_limit_values(
        current_ramp: float,
        current_max_abs: float,
        band: str,
    ) -> dict[str, float]:
        presets: dict[str, tuple[float, float, float, float]] = {
            "conservative": (0.7, 25.0, 0.8, 350.0),
            "relaxed": (1.1, 60.0, 1.1, 550.0),
            "nominal": (1.0, 50.0, 1.0, 500.0),
        }
        ramp_factor, ramp_default, max_abs_factor, max_abs_default = presets[band]
        return {
            "safe_ramp_rate_watts_per_second": current_ramp * ramp_factor if current_ramp > 0.0 else ramp_default,
            "preferred_bias_limit_watts": current_max_abs * max_abs_factor if current_max_abs > 0.0 else max_abs_default,
        }

    def _victron_ess_balance_profile_limit_recommendations(self, svc: Any, stability: float, overshoot_count: int) -> dict[str, float]:
        current_ramp, current_max_abs = self._victron_ess_balance_current_limit_settings(svc)
        band = self._victron_ess_balance_profile_limit_band(stability, overshoot_count)
        return self._victron_ess_balance_profile_limit_values(current_ramp, current_max_abs, band)

    @staticmethod
    def _victron_ess_balance_action_direction(
        source_error_w: float,
        expected_export_w: float,
        expected_import_w: float,
    ) -> str:
        if float(source_error_w) < 0.0:
            return "more_export"
        if float(source_error_w) > 0.0:
            return "less_export"
        if expected_export_w > max(25.0, expected_import_w):
            return "more_export"
        return "less_export"

    @staticmethod
    def _victron_ess_balance_site_regime(
        grid_interaction_w: float | None,
        expected_export_w: float,
        expected_import_w: float,
        action_direction: str,
    ) -> str:
        grid_regime = _victron_ess_balance_grid_site_regime(grid_interaction_w)
        if grid_regime:
            return grid_regime
        forecast_regime = _victron_ess_balance_forecast_site_regime(expected_export_w, expected_import_w)
        if forecast_regime:
            return forecast_regime
        return _victron_ess_balance_action_direction_site_regime(action_direction)

    @staticmethod
    def _victron_ess_balance_day_phase(expected_export_w: float, pv_input_power_w: float) -> str:
        return "day" if max(expected_export_w, pv_input_power_w) >= 50.0 else "night"

    def _victron_ess_balance_reserve_phase(self, source: dict[str, Any]) -> str:
        source_soc = self._sources._optional_float(source.get("soc"))
        reserve_floor_soc = self._sources._optional_float(source.get("discharge_balance_reserve_floor_soc"))
        if source_soc is not None and reserve_floor_soc is not None and source_soc <= (reserve_floor_soc + 5.0):
            return "reserve_band"
        return "above_reserve_band"

    class _LearningProfilePhaseInputs(TypedDict):
        grid_interaction_w: float | None
        expected_export_w: float
        expected_import_w: float
        pv_input_power_w: float
        combined_charge_headroom_w: float | None
        combined_discharge_headroom_w: float | None

    @staticmethod
    def _victron_ess_balance_battery_limit_phase(
        site_regime: str,
        combined_charge_headroom_w: float | None,
        combined_discharge_headroom_w: float | None,
    ) -> str:
        if _victron_ess_balance_near_discharge_limit(site_regime, combined_discharge_headroom_w):
            return "near_discharge_limit"
        if _victron_ess_balance_near_charge_limit(site_regime, combined_charge_headroom_w):
            return "near_charge_limit"
        return "mid_band"

    def _victron_ess_balance_learning_profile(
        self,
        svc: Any,
        cluster: dict[str, Any],
        source: dict[str, Any],
        source_error_w: float,
    ) -> dict[str, str]:
        phase_inputs = self._victron_ess_balance_learning_profile_phase_inputs(cluster)
        action_direction = self._victron_ess_balance_action_direction(
            source_error_w,
            phase_inputs["expected_export_w"],
            phase_inputs["expected_import_w"],
        )
        site_regime = self._victron_ess_balance_site_regime(
            phase_inputs["grid_interaction_w"],
            phase_inputs["expected_export_w"],
            phase_inputs["expected_import_w"],
            action_direction,
        )
        day_phase = self._victron_ess_balance_day_phase(
            phase_inputs["expected_export_w"],
            phase_inputs["pv_input_power_w"],
        )
        ev_phase = "ev_active" if self._sources._victron_ess_balance_ev_active(svc) else "ev_idle"
        pv_phase = _victron_ess_balance_pv_phase(
            phase_inputs["expected_export_w"],
            phase_inputs["pv_input_power_w"],
        )
        reserve_phase = self._victron_ess_balance_reserve_phase(source)
        battery_limit_phase = self._victron_ess_balance_battery_limit_phase(
            site_regime,
            phase_inputs["combined_charge_headroom_w"],
            phase_inputs["combined_discharge_headroom_w"],
        )
        key = _victron_ess_balance_learning_profile_key(
            action_direction,
            site_regime,
            day_phase,
            reserve_phase,
            ev_phase,
            pv_phase,
            battery_limit_phase,
        )
        return {
            "key": key,
            "action_direction": action_direction,
            "site_regime": site_regime,
            "direction": site_regime,
            "day_phase": day_phase,
            "reserve_phase": reserve_phase,
            "ev_phase": ev_phase,
            "pv_phase": pv_phase,
            "battery_limit_phase": battery_limit_phase,
        }

    def _victron_ess_balance_learning_profile_phase_inputs(
        self,
        cluster: dict[str, Any],
    ) -> _LearningProfilePhaseInputs:
        return {
            "grid_interaction_w": self._sources._optional_float(cluster.get("battery_combined_grid_interaction_w")),
            "expected_export_w": self._sources._optional_float(cluster.get("expected_near_term_export_w")) or 0.0,
            "expected_import_w": self._sources._optional_float(cluster.get("expected_near_term_import_w")) or 0.0,
            "pv_input_power_w": self._sources._optional_float(cluster.get("battery_combined_pv_input_power_w")) or 0.0,
            "combined_charge_headroom_w": self._sources._optional_float(cluster.get("battery_headroom_charge_w")),
            "combined_discharge_headroom_w": self._sources._optional_float(cluster.get("battery_headroom_discharge_w")),
        }

    @staticmethod
    def _victron_ess_balance_learning_profiles(svc: Any) -> dict[str, dict[str, Any]]:
        current: object = getattr(svc, "_victron_ess_balance_learning_profiles", None)
        if _is_learning_profile_store(current):
            return current
        profiles: dict[str, dict[str, Any]] = {}
        setattr(svc, "_victron_ess_balance_learning_profiles", profiles)
        return profiles

    def _victron_ess_balance_learning_profile_state(self, svc: Any, profile_key: str) -> dict[str, Any]:
        if not profile_key:
            return {}
        profiles = self._victron_ess_balance_learning_profiles(svc)
        profile = profiles.get(profile_key)
        return profile if isinstance(profile, dict) else {}

    def _ensure_victron_ess_balance_learning_profile_state(
        self,
        svc: Any,
        profile_key: str,
    ) -> dict[str, Any]:
        if not profile_key:
            return {}
        profiles = self._victron_ess_balance_learning_profiles(svc)
        profile = profiles.get(profile_key)
        if isinstance(profile, dict):
            return profile
        profile = self._victron_ess_balance_initialized_profile_state(profile_key)
        profiles[profile_key] = profile
        return profile

    def _victron_ess_balance_initialized_profile_state(self, profile_key: str) -> dict[str, Any]:
        profile_identity = _victron_ess_balance_profile_identity(profile_key)
        return {
            **profile_identity,
            "response_delay_seconds": None,
            "delay_samples": 0,
            "estimated_gain": None,
            "gain_samples": 0,
            "response_delay_mad_seconds": None,
            "gain_mad": None,
            "overshoot_count": 0,
            "settled_count": 0,
            "stability_score": None,
            "regime_consistency_score": None,
            "response_variance_score": None,
            "reproducibility_score": None,
            "safe_ramp_rate_watts_per_second": None,
            "preferred_bias_limit_watts": None,
        }

    @staticmethod
    def _victron_ess_balance_profile_sample_count(profile: dict[str, Any]) -> int:
        if not profile:
            return 0
        delay_samples = _victron_ess_balance_profile_counter(profile, "delay_samples")
        gain_samples = _victron_ess_balance_profile_counter(profile, "gain_samples")
        outcome_samples = (
            _victron_ess_balance_profile_counter(profile, "settled_count")
            + _victron_ess_balance_profile_counter(profile, "overshoot_count")
        )
        return max(delay_samples, gain_samples, outcome_samples)

    def _victron_ess_balance_profile_snapshot(self, svc: Any, profile_key: str) -> dict[str, Any]:
        profile = self._victron_ess_balance_learning_profile_state(svc, profile_key)
        if not profile:
            return {}
        snapshot: dict[str, Any] = {
            "key": str(profile["key"]),
            "sample_count": self._victron_ess_balance_profile_sample_count(profile),
        }
        snapshot.update(_victron_ess_balance_profile_scalar_snapshot(profile, self._victron_ess_balance_profile_scalar_fields()))
        snapshot.update(self._victron_ess_balance_profile_metric_snapshot(profile))
        return snapshot

    def _victron_ess_balance_profile_metric_snapshot(self, profile: dict[str, Any]) -> dict[str, Any]:
        snapshot = {
            "response_delay_seconds": self._sources._optional_float(profile.get("response_delay_seconds")),
            "estimated_gain": self._sources._optional_float(profile.get("estimated_gain")),
        }
        for field in ("overshoot_count", "settled_count"):
            snapshot[field] = _victron_ess_balance_profile_counter(profile, field)
        for field in (
            "stability_score",
            "regime_consistency_score",
            "response_variance_score",
            "reproducibility_score",
            "safe_ramp_rate_watts_per_second",
            "preferred_bias_limit_watts",
        ):
            snapshot[field] = self._sources._optional_float(profile.get(field))
        snapshot["typical_response_delay_seconds"] = snapshot["response_delay_seconds"]
        snapshot["effective_gain"] = snapshot["estimated_gain"]
        return snapshot

    def _merge_victron_ess_balance_learning_profile_metrics(
        self,
        svc: Any,
        metrics: dict[str, Any],
        profile_key: str,
    ) -> None:
        snapshot = self._victron_ess_balance_profile_snapshot(svc, profile_key)
        if not snapshot:
            return
        metrics.update(
            _victron_ess_balance_prefixed_scalar_metrics(
                snapshot,
                self._victron_ess_balance_profile_scalar_fields(),
            )
        )
        metrics["battery_discharge_balance_victron_bias_learning_profile_sample_count"] = int(snapshot["sample_count"])
        for field in self._victron_ess_balance_profile_metric_fields():
            value = snapshot[field]
            metrics[f"battery_discharge_balance_victron_bias_learning_profile_{field}"] = value

    @staticmethod
    def _set_victron_ess_balance_active_profile(svc: Any, learning_profile: dict[str, str]) -> None:
        for attr_name, field_name in _victron_ess_balance_active_profile_fields():
            setattr(svc, attr_name, str(learning_profile[field_name]))

    @staticmethod
    def _clear_victron_ess_balance_active_profile(svc: Any) -> None:
        _clear_victron_ess_balance_active_profile_state(svc)

    def _victron_ess_balance_update_profile_delay(self, svc: Any, profile_key: str, sample: float) -> None:
        profile = self._ensure_victron_ess_balance_learning_profile_state(svc, profile_key)
        if not profile:
            return
        _victron_ess_balance_update_profile_sample(
            profile,
            sample,
            samples_field="delay_samples",
            value_field="response_delay_seconds",
            deviation_field="response_delay_mad_seconds",
            optional_float=self._sources._optional_float,
            ewma=self._scorer.ewma_learned_value,
        )

    def _victron_ess_balance_update_profile_gain(self, svc: Any, profile_key: str, sample: float) -> None:
        profile = self._ensure_victron_ess_balance_learning_profile_state(svc, profile_key)
        if not profile:
            return
        _victron_ess_balance_update_profile_sample(
            profile,
            sample,
            samples_field="gain_samples",
            value_field="estimated_gain",
            deviation_field="gain_mad",
            optional_float=self._sources._optional_float,
            ewma=self._scorer.ewma_learned_value,
        )

    def _victron_ess_balance_increment_profile_counter(self, svc: Any, profile_key: str, field: str) -> None:
        profile = self._ensure_victron_ess_balance_learning_profile_state(svc, profile_key)
        if not profile:
            return
        profile[field] = _victron_ess_balance_profile_counter(profile, field) + 1

    def _victron_ess_balance_refresh_profile_stability(self, svc: Any, profile_key: str) -> None:
        profile = self._victron_ess_balance_learning_profile_state(svc, profile_key)
        if not profile:
            return
        profile["stability_score"] = self._scorer.stability_score_values(
            _victron_ess_balance_profile_counter(profile, "settled_count"),
            _victron_ess_balance_profile_counter(profile, "overshoot_count"),
            self._sources._optional_float(profile.get("estimated_gain")),
            self._sources._optional_float(profile.get("response_delay_seconds")),
        )
        profile["response_variance_score"] = self._scorer.variance_score(
            self._sources._optional_float(profile.get("response_delay_seconds")),
            self._sources._optional_float(profile.get("response_delay_mad_seconds")),
            self._sources._optional_float(profile.get("estimated_gain")),
            self._sources._optional_float(profile.get("gain_mad")),
        )
        profile["regime_consistency_score"] = self._scorer.regime_consistency_score(profile)
        profile["reproducibility_score"] = self._scorer.reproducibility_score(profile)
        stability = self._sources._optional_float(profile.get("stability_score")) or 0.0
        overshoot_count = _victron_ess_balance_profile_counter(profile, "overshoot_count")
        profile.update(self._victron_ess_balance_profile_limit_recommendations(svc, stability, overshoot_count))

    def _victron_ess_balance_current_topology_key(self, svc: Any, source_id: str) -> str:
        return ess_learning_topology_key(source_id, _victron_ess_balance_energy_ids(svc))

    def victron_ess_balance_learning_state_payload(self, svc: Any) -> dict[str, Any]:
        source_id = str(svc.auto_battery_discharge_balance_victron_bias_source_id).strip()
        topology_key = self._victron_ess_balance_current_topology_key(svc, source_id)
        profiles: dict[str, Any] = {}
        for profile_key in sorted(self._victron_ess_balance_learning_profiles(svc)):
            profiles[profile_key] = self._victron_ess_balance_profile_snapshot(svc, profile_key)
        return {
            "schema_version": 2,
            "topology_key": topology_key,
            "source_id": source_id,
            "profiles": profiles,
        }

    def victron_ess_balance_adaptive_tuning_payload(self, svc: Any) -> dict[str, Any]:
        source_id = str(svc.auto_battery_discharge_balance_victron_bias_source_id).strip()
        payload = {
            "schema_version": 2,
            "topology_key": self._victron_ess_balance_current_topology_key(svc, source_id),
            "source_id": source_id,
            **self._victron_ess_balance_current_tuning_snapshot(svc),
        }
        payload.update(self._victron_ess_balance_adaptive_scalar_payload(svc))
        payload["last_stable_tuning"] = dict(svc._victron_ess_balance_last_stable_tuning or {})
        payload["conservative_tuning"] = dict(svc._victron_ess_balance_conservative_tuning or {})
        return payload

    def _victron_ess_balance_adaptive_scalar_payload(self, svc: Any) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        for target_key, attr_name, caster in _victron_ess_balance_adaptive_scalar_specs():
            raw_value = getattr(svc, attr_name, None)
            payload[target_key] = self._victron_ess_balance_adaptive_scalar_value(raw_value, caster)
        return payload

    def _victron_ess_balance_adaptive_scalar_value(self, raw_value: Any, caster: Any) -> Any:
        return _victron_ess_balance_adaptive_scalar_value(raw_value, caster, self._sources._optional_float)

    def _victron_ess_balance_current_tuning_snapshot(self, svc: Any) -> dict[str, Any]:
        return {
            "kp": _victron_ess_balance_float_attr(svc, "auto_battery_discharge_balance_victron_bias_kp"),
            "ki": _victron_ess_balance_float_attr(svc, "auto_battery_discharge_balance_victron_bias_ki"),
            "kd": _victron_ess_balance_float_attr(svc, "auto_battery_discharge_balance_victron_bias_kd"),
            "deadband_watts": _victron_ess_balance_float_attr(
                svc,
                "auto_battery_discharge_balance_victron_bias_deadband_watts",
            ),
            "max_abs_watts": _victron_ess_balance_float_attr(
                svc,
                "auto_battery_discharge_balance_victron_bias_max_abs_watts",
            ),
            "ramp_rate_watts_per_second": _victron_ess_balance_float_attr(
                svc,
                "auto_battery_discharge_balance_victron_bias_ramp_rate_watts_per_second",
            ),
            "activation_mode": self._sources._victron_ess_balance_activation_mode(svc),
        }

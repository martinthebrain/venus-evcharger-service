# SPDX-License-Identifier: GPL-3.0-or-later
"""Support helpers for Victron ESS runtime-state restore."""

from __future__ import annotations

from typing import Any

from venus_evcharger.core.contracts import non_negative_float_or_none, non_negative_int


class _StateRuntimeRestoreVictronEssMixin:
    @classmethod
    def _valid_victron_ess_balance_schema_version(cls, payload: dict[str, object]) -> bool:
        return non_negative_int(payload.get("schema_version"), 0) in {1, 2}

    @classmethod
    def _victron_ess_balance_payload_matches_topology(
        cls,
        svc: Any,
        payload: dict[str, object],
    ) -> bool:
        source_id = str(
            payload.get(
                "source_id",
                getattr(svc, "auto_battery_discharge_balance_victron_bias_source_id", ""),
            )
            or ""
        ).strip()
        expected_topology_key = cls._victron_ess_balance_runtime_topology_key(svc, source_id)
        payload_topology_key = str(payload.get("topology_key", "") or "").strip()
        return payload_topology_key in {expected_topology_key, expected_topology_key.replace("/v2", "/v1")}

    @staticmethod
    def _normalized_victron_ess_balance_learning_text(
        raw_profile: dict[str, object],
        key: str,
        fallback_key: str | None = None,
    ) -> str:
        if fallback_key is None:
            return str(raw_profile.get(key, "") or "")
        return str(raw_profile.get(key, raw_profile.get(fallback_key, "")) or "")

    @staticmethod
    def _normalized_victron_ess_balance_learning_metric(
        raw_profile: dict[str, object],
        key: str,
        fallback_key: str | None = None,
    ) -> float | None:
        if fallback_key is None:
            return non_negative_float_or_none(raw_profile.get(key))
        return non_negative_float_or_none(raw_profile.get(key, raw_profile.get(fallback_key)))

    @staticmethod
    def _normalized_victron_ess_balance_learning_phase(
        raw_profile: dict[str, object],
        key: str,
        fallback: str,
    ) -> str:
        return str(raw_profile.get(key, fallback) or fallback)

    @staticmethod
    def _normalized_victron_ess_balance_learning_profile(
        profile_key: str,
        raw_profile: dict[str, object],
    ) -> dict[str, object]:
        return {
            "key": profile_key,
            "action_direction": _StateRuntimeRestoreVictronEssMixin._normalized_victron_ess_balance_learning_text(
                raw_profile,
                "action_direction",
            ),
            "site_regime": _StateRuntimeRestoreVictronEssMixin._normalized_victron_ess_balance_learning_text(
                raw_profile,
                "site_regime",
                "direction",
            ),
            "direction": _StateRuntimeRestoreVictronEssMixin._normalized_victron_ess_balance_learning_text(
                raw_profile,
                "direction",
                "site_regime",
            ),
            "day_phase": _StateRuntimeRestoreVictronEssMixin._normalized_victron_ess_balance_learning_text(
                raw_profile,
                "day_phase",
            ),
            "reserve_phase": _StateRuntimeRestoreVictronEssMixin._normalized_victron_ess_balance_learning_text(
                raw_profile,
                "reserve_phase",
            ),
            "ev_phase": _StateRuntimeRestoreVictronEssMixin._normalized_victron_ess_balance_learning_phase(
                raw_profile,
                "ev_phase",
                "ev_idle",
            ),
            "pv_phase": _StateRuntimeRestoreVictronEssMixin._normalized_victron_ess_balance_learning_phase(
                raw_profile,
                "pv_phase",
                "pv_weak",
            ),
            "battery_limit_phase": _StateRuntimeRestoreVictronEssMixin._normalized_victron_ess_balance_learning_phase(
                raw_profile,
                "battery_limit_phase",
                "mid_band",
            ),
            "delay_samples": non_negative_int(raw_profile.get("delay_samples"), 0),
            "gain_samples": non_negative_int(raw_profile.get("gain_samples"), 0),
            "response_delay_seconds": _StateRuntimeRestoreVictronEssMixin._normalized_victron_ess_balance_learning_metric(
                raw_profile,
                "response_delay_seconds",
                "typical_response_delay_seconds",
            ),
            "estimated_gain": _StateRuntimeRestoreVictronEssMixin._normalized_victron_ess_balance_learning_metric(
                raw_profile,
                "estimated_gain",
                "effective_gain",
            ),
            "response_delay_mad_seconds": _StateRuntimeRestoreVictronEssMixin._normalized_victron_ess_balance_learning_metric(
                raw_profile,
                "response_delay_mad_seconds",
            ),
            "gain_mad": _StateRuntimeRestoreVictronEssMixin._normalized_victron_ess_balance_learning_metric(
                raw_profile,
                "gain_mad",
            ),
            "overshoot_count": non_negative_int(raw_profile.get("overshoot_count"), 0),
            "settled_count": non_negative_int(raw_profile.get("settled_count"), 0),
            "stability_score": _StateRuntimeRestoreVictronEssMixin._normalized_victron_ess_balance_learning_metric(
                raw_profile,
                "stability_score",
            ),
            "regime_consistency_score": _StateRuntimeRestoreVictronEssMixin._normalized_victron_ess_balance_learning_metric(
                raw_profile,
                "regime_consistency_score",
            ),
            "response_variance_score": _StateRuntimeRestoreVictronEssMixin._normalized_victron_ess_balance_learning_metric(
                raw_profile,
                "response_variance_score",
            ),
            "reproducibility_score": _StateRuntimeRestoreVictronEssMixin._normalized_victron_ess_balance_learning_metric(
                raw_profile,
                "reproducibility_score",
            ),
            "safe_ramp_rate_watts_per_second": _StateRuntimeRestoreVictronEssMixin._normalized_victron_ess_balance_learning_metric(
                raw_profile,
                "safe_ramp_rate_watts_per_second",
            ),
            "preferred_bias_limit_watts": _StateRuntimeRestoreVictronEssMixin._normalized_victron_ess_balance_learning_metric(
                raw_profile,
                "preferred_bias_limit_watts",
            ),
        }

    @staticmethod
    def _restore_victron_ess_balance_pid_value(
        svc: Any,
        payload: dict[str, object],
        payload_key: str,
        attr_name: str,
    ) -> None:
        setattr(
            svc,
            attr_name,
            float(non_negative_float_or_none(payload.get(payload_key)) or 0.0),
        )

    @staticmethod
    def _victron_ess_balance_activation_mode(payload: dict[str, object], svc: Any) -> str | None:
        activation_mode = str(
            payload.get(
                "activation_mode",
                getattr(svc, "auto_battery_discharge_balance_victron_bias_activation_mode", "always"),
            )
            or "always"
        ).strip().lower()
        if activation_mode in {"always", "export_only", "above_reserve_band", "export_and_above_reserve_band"}:
            return activation_mode
        return None

    @staticmethod
    def _victron_ess_balance_runtime_topology_key(svc: Any, source_id: str) -> str:
        energy_ids = _victron_ess_balance_energy_ids(svc)
        service_name = _victron_ess_balance_runtime_string(
            svc, "auto_battery_discharge_balance_victron_bias_service"
        )
        path = _victron_ess_balance_runtime_string(
            svc, "auto_battery_discharge_balance_victron_bias_path"
        )
        return (
            "victron-bias-learning/v2"
            f"/source={str(source_id or '').strip()}"
            f"/service={service_name}"
            f"/path={path}"
            f"/energy={','.join(sorted(energy_ids))}"
        )

    @classmethod
    def _restore_victron_ess_balance_runtime_state(cls, svc: Any, state: dict[str, object]) -> None:
        raw_learning_state = state.get("victron_ess_balance_learning_state")
        if isinstance(raw_learning_state, dict):
            cls._restore_victron_ess_balance_learning_state_payload(svc, raw_learning_state)
        raw_adaptive_state = state.get("victron_ess_balance_adaptive_tuning_state")
        if isinstance(raw_adaptive_state, dict):
            cls._restore_victron_ess_balance_adaptive_tuning_payload(svc, raw_adaptive_state)

    @classmethod
    def _normalized_victron_ess_balance_learning_profiles(
        cls,
        raw_profiles: dict[str, object],
    ) -> dict[str, dict[str, object]]:
        normalized_profiles: dict[str, dict[str, object]] = {}
        for raw_key, raw_profile in raw_profiles.items():
            profile_key = str(raw_key or "").strip()
            if not profile_key or not isinstance(raw_profile, dict):
                continue
            normalized_profiles[profile_key] = cls._normalized_victron_ess_balance_learning_profile(
                profile_key,
                raw_profile,
            )
        return normalized_profiles

    @classmethod
    def _restore_victron_ess_balance_learning_state_payload(cls, svc: Any, payload: dict[str, object]) -> None:
        if not cls._valid_victron_ess_balance_schema_version(payload):
            return
        if not cls._victron_ess_balance_payload_matches_topology(svc, payload):
            return
        raw_profiles = payload.get("profiles")
        if not isinstance(raw_profiles, dict):
            return
        svc._victron_ess_balance_learning_profiles = cls._normalized_victron_ess_balance_learning_profiles(
            raw_profiles
        )

    @staticmethod
    def _restore_victron_ess_balance_pid_tuning(svc: Any, payload: dict[str, object]) -> None:
        _StateRuntimeRestoreVictronEssMixin._restore_victron_ess_balance_pid_value(
            svc,
            payload,
            "kp",
            "auto_battery_discharge_balance_victron_bias_kp",
        )
        _StateRuntimeRestoreVictronEssMixin._restore_victron_ess_balance_pid_value(
            svc,
            payload,
            "ki",
            "auto_battery_discharge_balance_victron_bias_ki",
        )
        _StateRuntimeRestoreVictronEssMixin._restore_victron_ess_balance_pid_value(
            svc,
            payload,
            "kd",
            "auto_battery_discharge_balance_victron_bias_kd",
        )
        _StateRuntimeRestoreVictronEssMixin._restore_victron_ess_balance_pid_value(
            svc,
            payload,
            "deadband_watts",
            "auto_battery_discharge_balance_victron_bias_deadband_watts",
        )
        _StateRuntimeRestoreVictronEssMixin._restore_victron_ess_balance_pid_value(
            svc,
            payload,
            "max_abs_watts",
            "auto_battery_discharge_balance_victron_bias_max_abs_watts",
        )
        _StateRuntimeRestoreVictronEssMixin._restore_victron_ess_balance_pid_value(
            svc,
            payload,
            "ramp_rate_watts_per_second",
            "auto_battery_discharge_balance_victron_bias_ramp_rate_watts_per_second",
        )

    @staticmethod
    def _normalized_victron_ess_balance_tuning_mapping(value: object) -> dict[str, object]:
        return dict(value) if isinstance(value, dict) else {}

    @staticmethod
    def _restore_victron_ess_balance_suspend_state(svc: Any, payload: dict[str, object]) -> None:
        svc._victron_ess_balance_auto_apply_suspend_until = non_negative_float_or_none(
            payload.get("auto_apply_suspend_until")
        )
        svc._victron_ess_balance_auto_apply_suspend_reason = str(
            payload.get("auto_apply_suspend_reason", "") or ""
        )
        svc._victron_ess_balance_safe_state_active = bool(payload.get("safe_state_active"))
        svc._victron_ess_balance_safe_state_reason = str(payload.get("safe_state_reason", "") or "")

    @staticmethod
    def _restore_victron_ess_balance_auto_apply_state(svc: Any, payload: dict[str, object]) -> None:
        svc._victron_ess_balance_auto_apply_generation = non_negative_int(
            payload.get("auto_apply_generation"),
            getattr(svc, "_victron_ess_balance_auto_apply_generation", 0),
        )
        svc._victron_ess_balance_auto_apply_observe_until = non_negative_float_or_none(
            payload.get("auto_apply_observe_until")
        )
        svc._victron_ess_balance_auto_apply_last_applied_param = str(
            payload.get("auto_apply_last_applied_param", "") or ""
        )
        svc._victron_ess_balance_auto_apply_last_applied_at = non_negative_float_or_none(
            payload.get("auto_apply_last_applied_at")
        )
        svc._victron_ess_balance_oscillation_lockout_until = non_negative_float_or_none(
            payload.get("oscillation_lockout_until")
        )
        svc._victron_ess_balance_oscillation_lockout_reason = str(
            payload.get("oscillation_lockout_reason", "") or ""
        )
        svc._victron_ess_balance_overshoot_cooldown_until = non_negative_float_or_none(
            payload.get("overshoot_cooldown_until")
        )
        svc._victron_ess_balance_overshoot_cooldown_reason = str(
            payload.get("overshoot_cooldown_reason", "") or ""
        )

    @staticmethod
    def _restore_victron_ess_balance_stable_tuning_state(svc: Any, payload: dict[str, object]) -> None:
        svc._victron_ess_balance_last_stable_tuning = _StateRuntimeRestoreVictronEssMixin._normalized_victron_ess_balance_tuning_mapping(
            payload.get("last_stable_tuning")
        )
        svc._victron_ess_balance_last_stable_at = non_negative_float_or_none(payload.get("last_stable_at"))
        svc._victron_ess_balance_last_stable_profile_key = str(
            payload.get("last_stable_profile_key", "") or ""
        )
        svc._victron_ess_balance_conservative_tuning = _StateRuntimeRestoreVictronEssMixin._normalized_victron_ess_balance_tuning_mapping(
            payload.get("conservative_tuning")
        )
        _StateRuntimeRestoreVictronEssMixin._restore_victron_ess_balance_suspend_state(svc, payload)

    @classmethod
    def _restore_victron_ess_balance_adaptive_tuning_payload(cls, svc: Any, payload: dict[str, object]) -> None:
        if not cls._valid_victron_ess_balance_schema_version(payload):
            return
        if not cls._victron_ess_balance_payload_matches_topology(svc, payload):
            return
        cls._restore_victron_ess_balance_pid_tuning(svc, payload)
        activation_mode = cls._victron_ess_balance_activation_mode(payload, svc)
        if activation_mode is not None:
            svc.auto_battery_discharge_balance_victron_bias_activation_mode = activation_mode
        cls._restore_victron_ess_balance_auto_apply_state(svc, payload)
        cls._restore_victron_ess_balance_stable_tuning_state(svc, payload)


def _victron_ess_balance_energy_ids(svc: Any) -> list[str]:
    energy_ids: list[str] = []
    for definition in tuple(getattr(svc, "auto_energy_sources", ()) or ()):
        normalized_id = str(getattr(definition, "source_id", "") or "").strip()
        if normalized_id:
            energy_ids.append(normalized_id)
    return energy_ids


def _victron_ess_balance_runtime_string(svc: Any, attr_name: str) -> str:
    return str(getattr(svc, attr_name, "") or "").strip()

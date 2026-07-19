# SPDX-License-Identifier: GPL-3.0-or-later
"""Victron ESS runtime-state restoration."""

from __future__ import annotations

from venus_evcharger.controllers.state_contracts import (
    StateAttributes,
    is_object_dict,
    string_key_items,
)
from venus_evcharger.core.contracts import non_negative_float_or_none, non_negative_int


class VictronEssRuntimeRestorer:
    """Restore Victron ESS learning and adaptive tuning state."""

    @classmethod
    def _valid_victron_ess_balance_schema_version(cls, payload: dict[str, object]) -> bool:
        return non_negative_int(payload.get("schema_version")) in {1, 2}

    @classmethod
    def _victron_ess_balance_payload_matches_topology(
        cls,
        svc: object,
        payload: dict[str, object],
    ) -> bool:
        if "source_id" in payload:
            raw_source_id = payload["source_id"]
        else:
            raw_source_id = getattr(
                svc,
                "auto_battery_discharge_balance_victron_bias_source_id",
                None,
            )
        source_id = str(raw_source_id).strip() if raw_source_id else ""
        expected_topology_key = cls._victron_ess_balance_runtime_topology_key(svc, source_id)
        raw_topology_key = payload.get("topology_key")
        if raw_topology_key is None:
            return False
        payload_topology_key = str(raw_topology_key).strip()
        return payload_topology_key in {expected_topology_key, expected_topology_key.replace("/v2", "/v1")}

    @staticmethod
    def _normalized_victron_ess_balance_learning_text(
        raw_profile: dict[str, object],
        key: str,
        fallback_key: str | None = None,
    ) -> str:
        if key in raw_profile:
            return str(raw_profile[key] or "")
        if fallback_key is None:
            return ""
        return str(raw_profile.get(fallback_key) or "")

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
        return str(raw_profile.get(key) or fallback)

    @staticmethod
    def _normalized_victron_ess_balance_learning_profile(
        profile_key: str,
        raw_profile: dict[str, object],
    ) -> dict[str, object]:
        return {
            "key": profile_key,
            "action_direction": VictronEssRuntimeRestorer._normalized_victron_ess_balance_learning_text(
                raw_profile,
                "action_direction",
            ),
            "site_regime": VictronEssRuntimeRestorer._normalized_victron_ess_balance_learning_text(
                raw_profile,
                "site_regime",
                "direction",
            ),
            "direction": VictronEssRuntimeRestorer._normalized_victron_ess_balance_learning_text(
                raw_profile,
                "direction",
                "site_regime",
            ),
            "day_phase": VictronEssRuntimeRestorer._normalized_victron_ess_balance_learning_text(
                raw_profile,
                "day_phase",
            ),
            "reserve_phase": VictronEssRuntimeRestorer._normalized_victron_ess_balance_learning_text(
                raw_profile,
                "reserve_phase",
            ),
            "ev_phase": VictronEssRuntimeRestorer._normalized_victron_ess_balance_learning_phase(
                raw_profile,
                "ev_phase",
                "ev_idle",
            ),
            "pv_phase": VictronEssRuntimeRestorer._normalized_victron_ess_balance_learning_phase(
                raw_profile,
                "pv_phase",
                "pv_weak",
            ),
            "battery_limit_phase": VictronEssRuntimeRestorer._normalized_victron_ess_balance_learning_phase(
                raw_profile,
                "battery_limit_phase",
                "mid_band",
            ),
            "delay_samples": non_negative_int(raw_profile.get("delay_samples")),
            "gain_samples": non_negative_int(raw_profile.get("gain_samples")),
            "response_delay_seconds": VictronEssRuntimeRestorer._normalized_victron_ess_balance_learning_metric(
                raw_profile,
                "response_delay_seconds",
                "typical_response_delay_seconds",
            ),
            "estimated_gain": VictronEssRuntimeRestorer._normalized_victron_ess_balance_learning_metric(
                raw_profile,
                "estimated_gain",
                "effective_gain",
            ),
            "response_delay_mad_seconds": VictronEssRuntimeRestorer._normalized_victron_ess_balance_learning_metric(
                raw_profile,
                "response_delay_mad_seconds",
            ),
            "gain_mad": VictronEssRuntimeRestorer._normalized_victron_ess_balance_learning_metric(
                raw_profile,
                "gain_mad",
            ),
            "overshoot_count": non_negative_int(raw_profile.get("overshoot_count")),
            "settled_count": non_negative_int(raw_profile.get("settled_count")),
            "stability_score": VictronEssRuntimeRestorer._normalized_victron_ess_balance_learning_metric(
                raw_profile,
                "stability_score",
            ),
            "regime_consistency_score": VictronEssRuntimeRestorer._normalized_victron_ess_balance_learning_metric(
                raw_profile,
                "regime_consistency_score",
            ),
            "response_variance_score": VictronEssRuntimeRestorer._normalized_victron_ess_balance_learning_metric(
                raw_profile,
                "response_variance_score",
            ),
            "reproducibility_score": VictronEssRuntimeRestorer._normalized_victron_ess_balance_learning_metric(
                raw_profile,
                "reproducibility_score",
            ),
            "safe_ramp_rate_watts_per_second": VictronEssRuntimeRestorer._normalized_victron_ess_balance_learning_metric(
                raw_profile,
                "safe_ramp_rate_watts_per_second",
            ),
            "preferred_bias_limit_watts": VictronEssRuntimeRestorer._normalized_victron_ess_balance_learning_metric(
                raw_profile,
                "preferred_bias_limit_watts",
            ),
        }

    @staticmethod
    def _restore_victron_ess_balance_pid_value(
        svc: object,
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
    def _victron_ess_balance_activation_mode(
        payload: dict[str, object],
        svc: object,
    ) -> str | None:
        if "activation_mode" in payload:
            raw_mode = payload["activation_mode"]
        else:
            raw_mode = getattr(
                svc,
                "auto_battery_discharge_balance_victron_bias_activation_mode",
                None,
            )
        if raw_mode is None or not str(raw_mode).strip():
            return "always"
        activation_mode = str(raw_mode).strip().lower()
        if activation_mode in {"always", "export_only", "above_reserve_band", "export_and_above_reserve_band"}:
            return activation_mode
        return None

    @staticmethod
    def _victron_ess_balance_runtime_topology_key(svc: object, source_id: str) -> str:
        energy_ids = _victron_ess_balance_energy_ids(svc)
        service_name = _victron_ess_balance_runtime_string(
            svc, "auto_battery_discharge_balance_victron_bias_service"
        )
        path = _victron_ess_balance_runtime_string(
            svc, "auto_battery_discharge_balance_victron_bias_path"
        )
        normalized_source_id = str(source_id).strip() if source_id else ""
        return (
            "victron-bias-learning/v2"
            f"/source={normalized_source_id}"
            f"/service={service_name}"
            f"/path={path}"
            f"/energy={','.join(sorted(energy_ids))}"
        )

    @classmethod
    def restore_runtime_state(cls, svc: object, state: dict[str, object]) -> None:
        raw_learning_state = state.get("victron_ess_balance_learning_state")
        if is_object_dict(raw_learning_state):
            cls._restore_victron_ess_balance_learning_state_payload(
                svc,
                string_key_items(raw_learning_state),
            )
        raw_adaptive_state = state.get("victron_ess_balance_adaptive_tuning_state")
        if is_object_dict(raw_adaptive_state):
            cls._restore_victron_ess_balance_adaptive_tuning_payload(
                svc,
                string_key_items(raw_adaptive_state),
            )

    @classmethod
    def _normalized_victron_ess_balance_learning_profiles(
        cls,
        raw_profiles: object,
    ) -> dict[str, dict[str, object]]:
        normalized_profiles: dict[str, dict[str, object]] = {}
        if not is_object_dict(raw_profiles):
            return normalized_profiles
        for raw_key, raw_profile in raw_profiles.items():
            normalized_entry = cls._normalized_victron_ess_balance_learning_profile_entry(raw_key, raw_profile)
            if normalized_entry is None:
                continue
            profile_key, profile = normalized_entry
            normalized_profiles[profile_key] = profile
        return normalized_profiles

    @classmethod
    def _normalized_victron_ess_balance_learning_profile_entry(
        cls,
        raw_key: object,
        raw_profile: object,
    ) -> tuple[str, dict[str, object]] | None:
        profile_key = str(raw_key or "").strip()
        if not profile_key:
            return None
        if not is_object_dict(raw_profile):
            return None
        profile = string_key_items(raw_profile)
        return profile_key, cls._normalized_victron_ess_balance_learning_profile(profile_key, profile)

    @classmethod
    def _restore_victron_ess_balance_learning_state_payload(
        cls,
        svc: object,
        payload: dict[str, object],
    ) -> None:
        if not cls._valid_victron_ess_balance_schema_version(payload):
            return
        if not cls._victron_ess_balance_payload_matches_topology(svc, payload):
            return
        raw_profiles = payload.get("profiles")
        if not is_object_dict(raw_profiles):
            return
        StateAttributes(svc).set(
            "_victron_ess_balance_learning_profiles",
            cls._normalized_victron_ess_balance_learning_profiles(raw_profiles),
        )

    @staticmethod
    def _restore_victron_ess_balance_pid_tuning(svc: object, payload: dict[str, object]) -> None:
        VictronEssRuntimeRestorer._restore_victron_ess_balance_pid_value(
            svc,
            payload,
            "kp",
            "auto_battery_discharge_balance_victron_bias_kp",
        )
        VictronEssRuntimeRestorer._restore_victron_ess_balance_pid_value(
            svc,
            payload,
            "ki",
            "auto_battery_discharge_balance_victron_bias_ki",
        )
        VictronEssRuntimeRestorer._restore_victron_ess_balance_pid_value(
            svc,
            payload,
            "kd",
            "auto_battery_discharge_balance_victron_bias_kd",
        )
        VictronEssRuntimeRestorer._restore_victron_ess_balance_pid_value(
            svc,
            payload,
            "deadband_watts",
            "auto_battery_discharge_balance_victron_bias_deadband_watts",
        )
        VictronEssRuntimeRestorer._restore_victron_ess_balance_pid_value(
            svc,
            payload,
            "max_abs_watts",
            "auto_battery_discharge_balance_victron_bias_max_abs_watts",
        )
        VictronEssRuntimeRestorer._restore_victron_ess_balance_pid_value(
            svc,
            payload,
            "ramp_rate_watts_per_second",
            "auto_battery_discharge_balance_victron_bias_ramp_rate_watts_per_second",
        )

    @staticmethod
    def _normalized_victron_ess_balance_tuning_mapping(value: object) -> dict[str, object]:
        return string_key_items(value)

    @staticmethod
    def _restore_victron_ess_balance_suspend_state(
        svc: object,
        payload: dict[str, object],
    ) -> None:
        attributes = StateAttributes(svc)
        attributes.set(
            "_victron_ess_balance_auto_apply_suspend_until",
            non_negative_float_or_none(payload.get("auto_apply_suspend_until")),
        )
        attributes.set(
            "_victron_ess_balance_auto_apply_suspend_reason",
            str(payload.get("auto_apply_suspend_reason") or ""),
        )
        attributes.set(
            "_victron_ess_balance_safe_state_active",
            bool(payload.get("safe_state_active")),
        )
        attributes.set(
            "_victron_ess_balance_safe_state_reason",
            str(payload.get("safe_state_reason") or ""),
        )

    @staticmethod
    def _restore_victron_ess_balance_auto_apply_state(
        svc: object,
        payload: dict[str, object],
    ) -> None:
        attributes = StateAttributes(svc)
        attributes.set(
            "_victron_ess_balance_auto_apply_generation",
            non_negative_int(
                payload.get("auto_apply_generation"),
                non_negative_int(attributes.get("_victron_ess_balance_auto_apply_generation", None)),
            ),
        )
        attributes.set(
            "_victron_ess_balance_auto_apply_observe_until",
            non_negative_float_or_none(payload.get("auto_apply_observe_until")),
        )
        attributes.set(
            "_victron_ess_balance_auto_apply_last_applied_param",
            str(payload.get("auto_apply_last_applied_param") or ""),
        )
        attributes.set(
            "_victron_ess_balance_auto_apply_last_applied_at",
            non_negative_float_or_none(payload.get("auto_apply_last_applied_at")),
        )
        attributes.set(
            "_victron_ess_balance_oscillation_lockout_until",
            non_negative_float_or_none(payload.get("oscillation_lockout_until")),
        )
        attributes.set(
            "_victron_ess_balance_oscillation_lockout_reason",
            str(payload.get("oscillation_lockout_reason") or ""),
        )
        attributes.set(
            "_victron_ess_balance_overshoot_cooldown_until",
            non_negative_float_or_none(payload.get("overshoot_cooldown_until")),
        )
        attributes.set(
            "_victron_ess_balance_overshoot_cooldown_reason",
            str(payload.get("overshoot_cooldown_reason") or ""),
        )

    @staticmethod
    def _restore_victron_ess_balance_stable_tuning_state(
        svc: object,
        payload: dict[str, object],
    ) -> None:
        attributes = StateAttributes(svc)
        attributes.set(
            "_victron_ess_balance_last_stable_tuning",
            VictronEssRuntimeRestorer._normalized_victron_ess_balance_tuning_mapping(
                payload.get("last_stable_tuning")
            ),
        )
        attributes.set(
            "_victron_ess_balance_last_stable_at",
            non_negative_float_or_none(payload.get("last_stable_at")),
        )
        attributes.set(
            "_victron_ess_balance_last_stable_profile_key",
            str(payload.get("last_stable_profile_key") or ""),
        )
        attributes.set(
            "_victron_ess_balance_conservative_tuning",
            VictronEssRuntimeRestorer._normalized_victron_ess_balance_tuning_mapping(
                payload.get("conservative_tuning")
            ),
        )
        VictronEssRuntimeRestorer._restore_victron_ess_balance_suspend_state(svc, payload)

    @classmethod
    def _restore_victron_ess_balance_adaptive_tuning_payload(
        cls,
        svc: object,
        payload: dict[str, object],
    ) -> None:
        if not cls._valid_victron_ess_balance_schema_version(payload):
            return
        if not cls._victron_ess_balance_payload_matches_topology(svc, payload):
            return
        cls._restore_victron_ess_balance_pid_tuning(svc, payload)
        activation_mode = cls._victron_ess_balance_activation_mode(payload, svc)
        if activation_mode is not None:
            StateAttributes(svc).set(
                "auto_battery_discharge_balance_victron_bias_activation_mode",
                activation_mode,
            )
        cls._restore_victron_ess_balance_auto_apply_state(svc, payload)
        cls._restore_victron_ess_balance_stable_tuning_state(svc, payload)


def _victron_ess_balance_energy_ids(svc: object) -> list[str]:
    energy_ids: list[str] = []
    definitions = getattr(svc, "auto_energy_sources", None)
    for definition in tuple(definitions or ()):
        normalized_id = str(getattr(definition, "source_id", None) or "").strip()
        if normalized_id:
            energy_ids.append(normalized_id)
    return energy_ids


def _victron_ess_balance_runtime_string(svc: object, attr_name: str) -> str:
    return str(getattr(svc, attr_name, None) or "").strip()

# SPDX-License-Identifier: GPL-3.0-or-later
"""Runtime-state snapshot helpers for the state controller."""

from __future__ import annotations

from collections.abc import Mapping

from venus_evcharger.controllers.state_contracts import is_object_dict, is_object_list, string_key_items
from venus_evcharger.controllers.state_json import json_object_payload, read_json_object_file
from venus_evcharger.controllers.state_runtime_normalize import RuntimeStateNormalizer
from venus_evcharger.controllers.state_runtime_snapshot_victron import (
    _victron_ess_balance_energy_ids,
    _victron_ess_balance_runtime_attr_text,
    _victron_ess_balance_runtime_non_negative_int,
    _victron_ess_balance_runtime_profile_counts,
    _victron_ess_balance_runtime_profile_identity,
    _victron_ess_balance_runtime_profile_learning_metrics,
    _victron_ess_balance_runtime_profile_limit_metrics,
    _victron_ess_balance_runtime_profile_metric,
    _victron_ess_balance_runtime_profile_response_metrics,
    _victron_ess_balance_runtime_profile_sample_count,
    _victron_ess_balance_runtime_string,
)
from venus_evcharger.core.contracts import finite_float_or_none

_DEFAULT_VICTRON_BIAS_ACTIVATION_MODE = "always"


class RuntimeStateSnapshotBuilder:
    """Build a normalized, serializable view of volatile runtime state."""

    def __init__(self, service: object, normalizer: RuntimeStateNormalizer) -> None:
        self.service = service
        self.normalizer = normalizer

    @staticmethod
    def _victron_ess_balance_runtime_profile_sample_count(profile: Mapping[str, object]) -> int:
        return _victron_ess_balance_runtime_profile_sample_count(profile)

    @staticmethod
    def _victron_ess_balance_runtime_profile_metric(
        profile: Mapping[str, object],
        key: str,
        fallback_key: str | None = None,
    ) -> float | None:
        return _victron_ess_balance_runtime_profile_metric(profile, key, fallback_key)

    @classmethod
    def _victron_ess_balance_runtime_adaptive_scalar_payload(cls, svc: object) -> dict[str, object]:
        payload = cls._victron_ess_balance_runtime_tuning_payload(svc)
        payload.update(cls._victron_ess_balance_runtime_auto_apply_payload(svc))
        payload.update(cls._victron_ess_balance_runtime_safety_payload(svc))
        return payload

    @staticmethod
    def _victron_ess_balance_runtime_tuning_payload(svc: object) -> dict[str, object]:
        return {
            "kp": finite_float_or_none(getattr(svc, "auto_battery_discharge_balance_victron_bias_kp", None)),
            "ki": finite_float_or_none(getattr(svc, "auto_battery_discharge_balance_victron_bias_ki", None)),
            "kd": finite_float_or_none(getattr(svc, "auto_battery_discharge_balance_victron_bias_kd", None)),
            "deadband_watts": finite_float_or_none(
                getattr(svc, "auto_battery_discharge_balance_victron_bias_deadband_watts", None)
            ),
            "max_abs_watts": finite_float_or_none(
                getattr(svc, "auto_battery_discharge_balance_victron_bias_max_abs_watts", None)
            ),
            "ramp_rate_watts_per_second": finite_float_or_none(
                getattr(svc, "auto_battery_discharge_balance_victron_bias_ramp_rate_watts_per_second", None)
            ),
            "activation_mode": _victron_ess_balance_runtime_attr_text(
            svc,
            "auto_battery_discharge_balance_victron_bias_activation_mode",
            fallback=_DEFAULT_VICTRON_BIAS_ACTIVATION_MODE,
                normalize_lower=True,
            ),
        }

    @staticmethod
    def _victron_ess_balance_runtime_auto_apply_payload(svc: object) -> dict[str, object]:
        return {
            "auto_apply_generation": _victron_ess_balance_runtime_non_negative_int(
                getattr(svc, "_victron_ess_balance_auto_apply_generation", None)
            ),
            "auto_apply_observe_until": finite_float_or_none(
                getattr(svc, "_victron_ess_balance_auto_apply_observe_until", None)
            ),
            "auto_apply_last_applied_param": _victron_ess_balance_runtime_attr_text(
                svc,
                "_victron_ess_balance_auto_apply_last_applied_param",
            ),
            "auto_apply_last_applied_at": finite_float_or_none(
                getattr(svc, "_victron_ess_balance_auto_apply_last_applied_at", None)
            ),
            "auto_apply_suspend_until": finite_float_or_none(
                getattr(svc, "_victron_ess_balance_auto_apply_suspend_until", None)
            ),
            "auto_apply_suspend_reason": _victron_ess_balance_runtime_attr_text(
                svc,
                "_victron_ess_balance_auto_apply_suspend_reason",
            ),
        }

    @staticmethod
    def _victron_ess_balance_runtime_safety_payload(svc: object) -> dict[str, object]:
        return {
            "oscillation_lockout_until": finite_float_or_none(
                getattr(svc, "_victron_ess_balance_oscillation_lockout_until", None)
            ),
            "oscillation_lockout_reason": _victron_ess_balance_runtime_attr_text(
                svc,
                "_victron_ess_balance_oscillation_lockout_reason",
            ),
            "overshoot_cooldown_until": finite_float_or_none(
                getattr(svc, "_victron_ess_balance_overshoot_cooldown_until", None)
            ),
            "overshoot_cooldown_reason": _victron_ess_balance_runtime_attr_text(
                svc,
                "_victron_ess_balance_overshoot_cooldown_reason",
            ),
            "last_stable_tuning": dict(getattr(svc, "_victron_ess_balance_last_stable_tuning", None) or {}),
            "last_stable_at": finite_float_or_none(getattr(svc, "_victron_ess_balance_last_stable_at", None)),
            "last_stable_profile_key": _victron_ess_balance_runtime_attr_text(
                svc,
                "_victron_ess_balance_last_stable_profile_key",
            ),
            "conservative_tuning": dict(getattr(svc, "_victron_ess_balance_conservative_tuning", None) or {}),
            "safe_state_active": bool(getattr(svc, "_victron_ess_balance_safe_state_active", None)),
            "safe_state_reason": _victron_ess_balance_runtime_attr_text(
                svc,
                "_victron_ess_balance_safe_state_reason",
            ),
        }

    def _base_runtime_state(self, svc: object) -> dict[str, object]:
        return {
            "mode": int(getattr(svc, "virtual_mode")),
            "autostart": int(getattr(svc, "virtual_autostart")),
            "enable": int(getattr(svc, "virtual_enable")),
            "startstop": int(getattr(svc, "virtual_startstop")),
            "manual_override_until": float(getattr(svc, "manual_override_until")),
            "auto_mode_cutover_pending": 1
            if bool(getattr(svc, "_auto_mode_cutover_pending"))
            else 0,
            "relay_last_changed_at": getattr(svc, "relay_last_changed_at"),
            "relay_last_off_at": getattr(svc, "relay_last_off_at"),
        }

    @staticmethod
    def _learned_charge_power_runtime_state(svc: object) -> dict[str, object]:
        return {
            "learned_charge_power_watts": getattr(svc, "learned_charge_power_watts", None),
            "learned_charge_power_updated_at": getattr(svc, "learned_charge_power_updated_at", None),
            "learned_charge_power_state": getattr(svc, "learned_charge_power_state", "unknown"),
            "learned_charge_power_learning_since": getattr(svc, "learned_charge_power_learning_since", None),
            "learned_charge_power_sample_count": int(getattr(svc, "learned_charge_power_sample_count", 0)),
            "learned_charge_power_phase": getattr(svc, "learned_charge_power_phase", None),
            "learned_charge_power_voltage": getattr(svc, "learned_charge_power_voltage", None),
            "learned_charge_power_signature_mismatch_sessions": int(
                getattr(svc, "learned_charge_power_signature_mismatch_sessions", 0)
            ),
            "learned_charge_power_signature_checked_session_started_at": getattr(
                svc,
                "learned_charge_power_signature_checked_session_started_at",
                None,
            ),
        }

    def _phase_selection_runtime_state(self, svc: object) -> dict[str, object]:
        return {
            "active_phase_selection": self.normalizer.phase_selection(
                getattr(svc, "active_phase_selection", None)
            ),
            "requested_phase_selection": self.normalizer.phase_selection(
                getattr(svc, "requested_phase_selection", None)
            ),
            "supported_phase_selections": list(
                self.normalizer.supported_phase_selections(getattr(svc, "supported_phase_selections", None))
            ),
        }

    def _phase_switch_runtime_state(self, svc: object) -> dict[str, object]:
        default_phase = self.normalizer.phase_selection(getattr(svc, "requested_phase_selection", None))
        return {
            "phase_switch_pending_selection": self.normalizer.optional_phase_selection(
                getattr(svc, "_phase_switch_pending_selection", None),
                default_phase,
            ),
            "phase_switch_state": self.normalizer.phase_switch_state(getattr(svc, "_phase_switch_state", None)),
            "phase_switch_requested_at": self.normalizer.optional_past_time(
                getattr(svc, "_phase_switch_requested_at", None)
            ),
            "phase_switch_stable_until": self.normalizer.optional_float(
                getattr(svc, "_phase_switch_stable_until", None)
            ),
            "phase_switch_resume_relay": 1 if bool(getattr(svc, "_phase_switch_resume_relay", None)) else 0,
            "phase_switch_mismatch_counts": dict(getattr(svc, "_phase_switch_mismatch_counts", None) or {}),
            "phase_switch_last_mismatch_selection": self.normalizer.optional_phase_selection(
                getattr(svc, "_phase_switch_last_mismatch_selection", None),
                default_phase,
            ),
            "phase_switch_last_mismatch_at": self.normalizer.optional_past_time(
                getattr(svc, "_phase_switch_last_mismatch_at", None)
            ),
            "phase_switch_lockout_selection": self.normalizer.optional_phase_selection(
                getattr(svc, "_phase_switch_lockout_selection", None),
                default_phase,
            ),
            "phase_switch_lockout_reason": str(getattr(svc, "_phase_switch_lockout_reason", None) or ""),
            "phase_switch_lockout_at": self.normalizer.optional_past_time(
                getattr(svc, "_phase_switch_lockout_at", None)
            ),
            "phase_switch_lockout_until": self.normalizer.optional_float(
                getattr(svc, "_phase_switch_lockout_until", None)
            ),
        }

    def _contactor_runtime_state(self, svc: object) -> dict[str, object]:
        return {
            "contactor_fault_counts": dict(getattr(svc, "_contactor_fault_counts", None) or {}),
            "contactor_fault_active_reason": self.normalizer.optional_text(
                getattr(svc, "_contactor_fault_active_reason", None)
            ),
            "contactor_fault_active_since": self.normalizer.optional_past_time(
                getattr(svc, "_contactor_fault_active_since", None)
            ),
            "contactor_lockout_reason": str(getattr(svc, "_contactor_lockout_reason", None) or ""),
            "contactor_lockout_source": str(getattr(svc, "_contactor_lockout_source", None) or ""),
            "contactor_lockout_at": self.normalizer.optional_past_time(
                getattr(svc, "_contactor_lockout_at", None)
            ),
        }

    @staticmethod
    def _energy_runtime_state(svc: object) -> dict[str, object]:
        snapshot: dict[str, object] = {}
        get_snapshot = getattr(svc, "_get_worker_snapshot", None)
        if callable(get_snapshot):
            raw_snapshot: object = get_snapshot()
            snapshot = json_object_payload(raw_snapshot) or {}
        raw_sources = snapshot.get("battery_sources")
        battery_sources = list(raw_sources) if is_object_list(raw_sources) else []
        battery_learning_profiles = json_object_payload(
            snapshot.get("battery_learning_profiles")
        ) or {}
        return {
            "combined_battery_soc": snapshot.get("battery_combined_soc"),
            "combined_battery_usable_capacity_wh": snapshot.get("battery_combined_usable_capacity_wh"),
            "combined_battery_charge_power_w": snapshot.get("battery_combined_charge_power_w"),
            "combined_battery_discharge_power_w": snapshot.get("battery_combined_discharge_power_w"),
            "combined_battery_net_power_w": snapshot.get("battery_combined_net_power_w"),
            "combined_battery_ac_power_w": snapshot.get("battery_combined_ac_power_w"),
            "combined_battery_pv_input_power_w": snapshot.get("battery_combined_pv_input_power_w"),
            "combined_battery_grid_interaction_w": snapshot.get("battery_combined_grid_interaction_w"),
            "combined_battery_headroom_charge_w": snapshot.get("battery_headroom_charge_w"),
            "combined_battery_headroom_discharge_w": snapshot.get("battery_headroom_discharge_w"),
            "expected_near_term_export_w": snapshot.get("expected_near_term_export_w"),
            "expected_near_term_import_w": snapshot.get("expected_near_term_import_w"),
            "battery_discharge_balance_mode": snapshot.get("battery_discharge_balance_mode"),
            "battery_discharge_balance_target_distribution_mode": snapshot.get(
                "battery_discharge_balance_target_distribution_mode"
            ),
            "battery_discharge_balance_error_w": snapshot.get("battery_discharge_balance_error_w"),
            "battery_discharge_balance_max_abs_error_w": snapshot.get("battery_discharge_balance_max_abs_error_w"),
            "battery_discharge_balance_total_discharge_w": snapshot.get("battery_discharge_balance_total_discharge_w"),
            "battery_discharge_balance_eligible_source_count": snapshot.get(
                "battery_discharge_balance_eligible_source_count",
                0,
            ),
            "battery_discharge_balance_active_source_count": snapshot.get(
                "battery_discharge_balance_active_source_count",
                0,
            ),
            "battery_discharge_balance_control_candidate_count": snapshot.get(
                "battery_discharge_balance_control_candidate_count",
                0,
            ),
            "battery_discharge_balance_control_ready_count": snapshot.get(
                "battery_discharge_balance_control_ready_count",
                0,
            ),
            "battery_discharge_balance_supported_control_source_count": snapshot.get(
                "battery_discharge_balance_supported_control_source_count",
                0,
            ),
            "battery_discharge_balance_experimental_control_source_count": snapshot.get(
                "battery_discharge_balance_experimental_control_source_count",
                0,
            ),
            "combined_battery_average_confidence": snapshot.get("battery_average_confidence"),
            "combined_battery_source_count": snapshot.get("battery_source_count", 0),
            "combined_battery_online_source_count": snapshot.get("battery_online_source_count", 0),
            "combined_battery_valid_soc_source_count": snapshot.get("battery_valid_soc_source_count", 0),
            "combined_battery_battery_source_count": snapshot.get("battery_battery_source_count", 0),
            "combined_battery_hybrid_inverter_source_count": snapshot.get("battery_hybrid_inverter_source_count", 0),
            "combined_battery_inverter_source_count": snapshot.get("battery_inverter_source_count", 0),
            "combined_battery_sources": battery_sources,
            "combined_battery_learning_profiles": battery_learning_profiles,
        }

    @staticmethod
    def _victron_ess_balance_runtime_topology_key(svc: object, source_id: str) -> str:
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

    @staticmethod
    def _victron_ess_balance_runtime_profile_snapshot(profile_key: str, raw_profile: object) -> dict[str, object]:
        profile = string_key_items(raw_profile)
        payload = _victron_ess_balance_runtime_profile_identity(profile_key, profile)
        payload.update(_victron_ess_balance_runtime_profile_counts(profile))
        payload.update(_victron_ess_balance_runtime_profile_response_metrics(profile))
        payload.update(_victron_ess_balance_runtime_profile_learning_metrics(profile))
        payload.update(_victron_ess_balance_runtime_profile_limit_metrics(profile))
        return payload

    @classmethod
    def _victron_ess_balance_runtime_learning_state(cls, svc: object) -> dict[str, object]:
        raw_profiles = getattr(svc, "_victron_ess_balance_learning_profiles", None)
        source_id = str(getattr(svc, "auto_battery_discharge_balance_victron_bias_source_id", None) or "").strip()
        profiles: dict[str, object] = {}
        if is_object_dict(raw_profiles):
            for profile_key, raw_profile in raw_profiles.items():
                profiles[str(profile_key)] = cls._victron_ess_balance_runtime_profile_snapshot(
                    str(profile_key),
                    raw_profile,
                )
        return {
            "schema_version": 2,
            "topology_key": cls._victron_ess_balance_runtime_topology_key(svc, source_id),
            "source_id": source_id,
            "profiles": profiles,
        }

    @classmethod
    def _victron_ess_balance_runtime_adaptive_tuning_state(cls, svc: object) -> dict[str, object]:
        source_id = str(getattr(svc, "auto_battery_discharge_balance_victron_bias_source_id", None) or "").strip()
        return {
            "schema_version": 2,
            "topology_key": cls._victron_ess_balance_runtime_topology_key(svc, source_id),
            "source_id": source_id,
            **cls._victron_ess_balance_runtime_adaptive_scalar_payload(svc),
        }

    def build(self) -> dict[str, object]:
        svc = self.service
        runtime_state = self._base_runtime_state(svc)
        runtime_state.update(self._learned_charge_power_runtime_state(svc))
        runtime_state.update(self._phase_selection_runtime_state(svc))
        runtime_state.update(self._phase_switch_runtime_state(svc))
        runtime_state.update(self._contactor_runtime_state(svc))
        runtime_state.update(self._energy_runtime_state(svc))
        runtime_state["victron_ess_balance_learning_state"] = self._victron_ess_balance_runtime_learning_state(svc)
        runtime_state["victron_ess_balance_adaptive_tuning_state"] = self._victron_ess_balance_runtime_adaptive_tuning_state(
            svc
        )
        return runtime_state

    @staticmethod
    def _read_runtime_state_payload(path: str) -> dict[str, object] | None:
        return read_json_object_file(path)

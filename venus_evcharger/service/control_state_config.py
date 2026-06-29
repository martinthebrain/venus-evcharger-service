# SPDX-License-Identifier: GPL-3.0-or-later
"""Configuration-effective state payload helpers for the Control API role."""

from __future__ import annotations

from typing import Any

from venus_evcharger.backend.config import backend_mode_for_service, backend_type_for_service
from venus_evcharger.core.contracts import normalized_state_api_config_effective_fields
from venus_evcharger.energy import energy_source_profile_details
from venus_evcharger.service.control_state_victron import _ControlApiStateVictron


class _ControlApiStateConfig(_ControlApiStateVictron):
    @staticmethod
    def _config_effective_energy_source_ids(sources: tuple[Any, ...]) -> list[str]:
        return [getattr(source, "source_id", "") for source in sources]

    @staticmethod
    def _config_effective_energy_source_profiles(sources: tuple[Any, ...]) -> dict[str, str]:
        return {
            getattr(source, "source_id", ""): getattr(source, "profile_name", "")
            for source in sources
            if str(getattr(source, "source_id", "")).strip()
        }

    @staticmethod
    def _config_effective_energy_source_profile_details(sources: tuple[Any, ...]) -> dict[str, dict[str, Any]]:
        return {
            getattr(source, "source_id", ""): dict(
                energy_source_profile_details(getattr(source, "profile_name", ""))
            )
            for source in sources
            if str(getattr(source, "source_id", "")).strip()
        }

    def _config_effective_state_base(self) -> dict[str, Any]:
        return {
            "deviceinstance": getattr(self, "deviceinstance", 0),
            "host": getattr(self, "host", ""),
            "phase": getattr(self, "phase", "L1"),
            "service_name": getattr(self, "service_name", ""),
            "connection_name": getattr(self, "connection_name", ""),
            "runtime_state_path": getattr(self, "runtime_state_path", ""),
            "runtime_overrides_path": getattr(self, "runtime_overrides_path", ""),
            "backend_mode": backend_mode_for_service(self, "combined"),
            "meter_backend": backend_type_for_service(self, "meter", "na"),
            "switch_backend": backend_type_for_service(self, "switch", "na"),
            "charger_backend": backend_type_for_service(self, "charger", "na"),
            "max_current": getattr(self, "max_current", 0.0),
            "min_current": getattr(self, "min_current", 0.0),
            "auto_daytime_only": bool(getattr(self, "auto_daytime_only", False)),
            "auto_scheduled_enabled_days": getattr(self, "auto_scheduled_enabled_days", ""),
            "auto_scheduled_latest_end_time": getattr(self, "auto_scheduled_latest_end_time", ""),
            "auto_scheduled_night_current_amps": getattr(self, "auto_scheduled_night_current_amps", 0.0),
        }

    def _config_effective_control_api(self) -> dict[str, Any]:
        return {
            "control_api_enabled": bool(getattr(self, "control_api_enabled", False)),
            "control_api_host": getattr(self, "control_api_host", "127.0.0.1"),
            "control_api_port": int(getattr(self, "control_api_port", 0)),
            "control_api_localhost_only": bool(getattr(self, "control_api_localhost_only", True)),
            "control_api_unix_socket_path": getattr(self, "control_api_unix_socket_path", ""),
            "control_api_audit_path": getattr(self, "control_api_audit_path", ""),
            "control_api_idempotency_path": getattr(self, "control_api_idempotency_path", ""),
            "control_api_rate_limit_max_requests": int(getattr(self, "control_api_rate_limit_max_requests", 0)),
            "control_api_rate_limit_window_seconds": float(getattr(self, "control_api_rate_limit_window_seconds", 0.0)),
            "control_api_critical_cooldown_seconds": float(
                getattr(self, "control_api_critical_cooldown_seconds", 0.0)
            ),
            "control_api_read_token_configured": bool(str(getattr(self, "control_api_read_token", "")).strip()),
            "control_api_control_token_configured": bool(str(getattr(self, "control_api_control_token", "")).strip()),
            "control_api_admin_token_configured": bool(str(getattr(self, "control_api_admin_token", "")).strip()),
            "control_api_update_token_configured": bool(str(getattr(self, "control_api_update_token", "")).strip()),
        }

    def _config_effective_companion(self) -> dict[str, Any]:
        return {
            "companion_dbus_bridge_enabled": bool(getattr(self, "companion_dbus_bridge_enabled", False)),
            "companion_battery_service_enabled": bool(getattr(self, "companion_battery_service_enabled", False)),
            "companion_pvinverter_service_enabled": bool(getattr(self, "companion_pvinverter_service_enabled", False)),
            "companion_grid_service_enabled": bool(getattr(self, "companion_grid_service_enabled", False)),
            "companion_grid_authoritative_source": str(getattr(self, "companion_grid_authoritative_source", "")),
            "companion_grid_hold_seconds": float(getattr(self, "companion_grid_hold_seconds", 0.0)),
            "companion_grid_smoothing_alpha": float(getattr(self, "companion_grid_smoothing_alpha", 1.0)),
            "companion_grid_smoothing_max_jump_watts": float(
                getattr(self, "companion_grid_smoothing_max_jump_watts", 0.0)
            ),
            "companion_source_services_enabled": bool(getattr(self, "companion_source_services_enabled", False)),
            "companion_source_grid_services_enabled": bool(getattr(self, "companion_source_grid_services_enabled", False)),
            "companion_source_grid_hold_seconds": float(getattr(self, "companion_source_grid_hold_seconds", 0.0)),
            "companion_source_grid_smoothing_alpha": float(getattr(self, "companion_source_grid_smoothing_alpha", 1.0)),
            "companion_source_grid_smoothing_max_jump_watts": float(
                getattr(self, "companion_source_grid_smoothing_max_jump_watts", 0.0)
            ),
            "companion_battery_deviceinstance": int(getattr(self, "companion_battery_deviceinstance", 0)),
            "companion_pvinverter_deviceinstance": int(getattr(self, "companion_pvinverter_deviceinstance", 0)),
            "companion_grid_deviceinstance": int(getattr(self, "companion_grid_deviceinstance", 0)),
            "companion_source_battery_deviceinstance_base": int(
                getattr(self, "companion_source_battery_deviceinstance_base", 0)
            ),
            "companion_source_pvinverter_deviceinstance_base": int(
                getattr(self, "companion_source_pvinverter_deviceinstance_base", 0)
            ),
            "companion_source_grid_deviceinstance_base": int(
                getattr(self, "companion_source_grid_deviceinstance_base", 0)
            ),
            "companion_battery_service_name": getattr(self, "companion_battery_service_name", ""),
            "companion_pvinverter_service_name": getattr(self, "companion_pvinverter_service_name", ""),
            "companion_grid_service_name": getattr(self, "companion_grid_service_name", ""),
            "companion_source_battery_service_prefix": getattr(self, "companion_source_battery_service_prefix", ""),
            "companion_source_pvinverter_service_prefix": getattr(
                self,
                "companion_source_pvinverter_service_prefix",
                "",
            ),
            "companion_source_grid_service_prefix": getattr(self, "companion_source_grid_service_prefix", ""),
        }

    def _config_effective_energy_sources(self) -> dict[str, Any]:
        sources = tuple(getattr(self, "auto_energy_sources", ()) or ())
        return {
            "auto_use_combined_battery_soc": bool(getattr(self, "auto_use_combined_battery_soc", True)),
            "auto_energy_source_ids": self._config_effective_energy_source_ids(sources),
            "auto_energy_source_profiles": self._config_effective_energy_source_profiles(sources),
            "auto_energy_source_profile_details": self._config_effective_energy_source_profile_details(sources),
            "auto_energy_source_count": len(sources),
        }

    def _state_api_config_effective_payload(self) -> dict[str, Any]:
        state = {}
        state.update(self._config_effective_state_base())
        state.update(self._config_effective_control_api())
        state.update(self._config_effective_companion())
        state.update(
            {
                    "auto_use_combined_battery_soc": bool(getattr(self, "auto_use_combined_battery_soc", True)),
                    "auto_battery_discharge_balance_policy_enabled": bool(
                        getattr(self, "auto_battery_discharge_balance_policy_enabled", False)
                    ),
                    "auto_battery_discharge_balance_warn_error_watts": float(
                        getattr(self, "auto_battery_discharge_balance_warn_error_watts", 0.0)
                    ),
                    "auto_battery_discharge_balance_bias_start_error_watts": float(
                        getattr(self, "auto_battery_discharge_balance_bias_start_error_watts", 0.0)
                    ),
                    "auto_battery_discharge_balance_bias_max_penalty_watts": float(
                        getattr(self, "auto_battery_discharge_balance_bias_max_penalty_watts", 0.0)
                    ),
                    "auto_battery_discharge_balance_bias_mode": str(
                        getattr(self, "auto_battery_discharge_balance_bias_mode", "always")
                    ),
                    "auto_battery_discharge_balance_bias_reserve_margin_soc": float(
                        getattr(self, "auto_battery_discharge_balance_bias_reserve_margin_soc", 0.0)
                    ),
                    "auto_battery_discharge_balance_coordination_enabled": bool(
                        getattr(self, "auto_battery_discharge_balance_coordination_enabled", False)
                    ),
                    "auto_battery_discharge_balance_coordination_support_mode": str(
                        getattr(self, "auto_battery_discharge_balance_coordination_support_mode", "supported_only")
                    ),
                    "auto_battery_discharge_balance_coordination_start_error_watts": float(
                        getattr(self, "auto_battery_discharge_balance_coordination_start_error_watts", 0.0)
                    ),
                    "auto_battery_discharge_balance_coordination_max_penalty_watts": float(
                        getattr(self, "auto_battery_discharge_balance_coordination_max_penalty_watts", 0.0)
                    ),
                    "auto_battery_discharge_balance_victron_bias_enabled": bool(
                        getattr(self, "auto_battery_discharge_balance_victron_bias_enabled", False)
                    ),
                    "auto_battery_discharge_balance_victron_bias_source_id": str(
                        getattr(self, "auto_battery_discharge_balance_victron_bias_source_id", "")
                    ),
                    "auto_battery_discharge_balance_victron_bias_service": str(
                        getattr(self, "auto_battery_discharge_balance_victron_bias_service", "")
                    ),
                    "auto_battery_discharge_balance_victron_bias_path": str(
                        getattr(self, "auto_battery_discharge_balance_victron_bias_path", "")
                    ),
                    "auto_battery_discharge_balance_victron_bias_base_setpoint_watts": float(
                        getattr(self, "auto_battery_discharge_balance_victron_bias_base_setpoint_watts", 0.0)
                    ),
                    "auto_battery_discharge_balance_victron_bias_deadband_watts": float(
                        getattr(self, "auto_battery_discharge_balance_victron_bias_deadband_watts", 0.0)
                    ),
                    "auto_battery_discharge_balance_victron_bias_activation_mode": str(
                        getattr(self, "auto_battery_discharge_balance_victron_bias_activation_mode", "always")
                    ),
                    "auto_battery_discharge_balance_victron_bias_support_mode": str(
                        getattr(self, "auto_battery_discharge_balance_victron_bias_support_mode", "allow_experimental")
                    ),
                    "auto_battery_discharge_balance_victron_bias_kp": float(
                        getattr(self, "auto_battery_discharge_balance_victron_bias_kp", 0.0)
                    ),
                    "auto_battery_discharge_balance_victron_bias_ki": float(
                        getattr(self, "auto_battery_discharge_balance_victron_bias_ki", 0.0)
                    ),
                    "auto_battery_discharge_balance_victron_bias_kd": float(
                        getattr(self, "auto_battery_discharge_balance_victron_bias_kd", 0.0)
                    ),
                    "auto_battery_discharge_balance_victron_bias_integral_limit_watts": float(
                        getattr(self, "auto_battery_discharge_balance_victron_bias_integral_limit_watts", 0.0)
                    ),
                    "auto_battery_discharge_balance_victron_bias_max_abs_watts": float(
                        getattr(self, "auto_battery_discharge_balance_victron_bias_max_abs_watts", 0.0)
                    ),
                    "auto_battery_discharge_balance_victron_bias_ramp_rate_watts_per_second": float(
                        getattr(self, "auto_battery_discharge_balance_victron_bias_ramp_rate_watts_per_second", 0.0)
                    ),
                    "auto_battery_discharge_balance_victron_bias_min_update_seconds": float(
                        getattr(self, "auto_battery_discharge_balance_victron_bias_min_update_seconds", 0.0)
                    ),
                    "auto_battery_discharge_balance_victron_bias_auto_apply_enabled": bool(
                        getattr(self, "auto_battery_discharge_balance_victron_bias_auto_apply_enabled", False)
                    ),
                    "auto_battery_discharge_balance_victron_bias_auto_apply_min_confidence": float(
                        getattr(self, "auto_battery_discharge_balance_victron_bias_auto_apply_min_confidence", 0.0)
                    ),
                    "auto_battery_discharge_balance_victron_bias_auto_apply_min_profile_samples": int(
                        getattr(self, "auto_battery_discharge_balance_victron_bias_auto_apply_min_profile_samples", 0)
                    ),
                    "auto_battery_discharge_balance_victron_bias_auto_apply_min_stability_score": float(
                        getattr(self, "auto_battery_discharge_balance_victron_bias_auto_apply_min_stability_score", 0.0)
                    ),
                    "auto_battery_discharge_balance_victron_bias_auto_apply_blend": float(
                        getattr(self, "auto_battery_discharge_balance_victron_bias_auto_apply_blend", 0.0)
                    ),
                    "auto_battery_discharge_balance_victron_bias_observation_window_seconds": float(
                        getattr(self, "auto_battery_discharge_balance_victron_bias_observation_window_seconds", 0.0)
                    ),
                    "auto_battery_discharge_balance_victron_bias_oscillation_lockout_enabled": bool(
                        getattr(self, "auto_battery_discharge_balance_victron_bias_oscillation_lockout_enabled", False)
                    ),
                    "auto_battery_discharge_balance_victron_bias_oscillation_lockout_window_seconds": float(
                        getattr(self, "auto_battery_discharge_balance_victron_bias_oscillation_lockout_window_seconds", 0.0)
                    ),
                    "auto_battery_discharge_balance_victron_bias_oscillation_lockout_min_direction_changes": int(
                        getattr(
                            self,
                            "auto_battery_discharge_balance_victron_bias_oscillation_lockout_min_direction_changes",
                            0,
                        )
                    ),
                    "auto_battery_discharge_balance_victron_bias_oscillation_lockout_duration_seconds": float(
                        getattr(self, "auto_battery_discharge_balance_victron_bias_oscillation_lockout_duration_seconds", 0.0)
                    ),
                    "auto_battery_discharge_balance_victron_bias_rollback_enabled": bool(
                        getattr(self, "auto_battery_discharge_balance_victron_bias_rollback_enabled", False)
                    ),
                    "auto_battery_discharge_balance_victron_bias_rollback_min_stability_score": float(
                        getattr(self, "auto_battery_discharge_balance_victron_bias_rollback_min_stability_score", 0.0)
                    ),
                    "auto_battery_discharge_balance_victron_bias_require_clean_phases": bool(
                        getattr(self, "auto_battery_discharge_balance_victron_bias_require_clean_phases", False)
                    ),
                }
        )
        state.update(self._config_effective_energy_sources())
        return normalized_state_api_config_effective_fields(
            {
                "ok": True,
                "api_version": "v1",
                "kind": "config-effective",
                "state": state,
            }
        )

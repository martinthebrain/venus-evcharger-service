# SPDX-License-Identifier: GPL-3.0-or-later
"""Runtime-config validation helpers for the state controller."""

from __future__ import annotations

import logging

from venus_evcharger.auto.policy import AutoPolicy, validate_auto_policy
from venus_evcharger.core.common import DEFAULT_SCHEDULED_ENABLED_DAYS, normalize_hhmm_text, scheduled_enabled_days_text


_BALANCE_ACTIVATION_MODES = frozenset(
    {"always", "export_only", "above_reserve_band", "export_and_above_reserve_band"}
)
_BALANCE_SUPPORT_MODES = frozenset({"supported_only", "allow_experimental"})
_BALANCE_NON_NEGATIVE_FLOAT_ATTRS = (
    "auto_battery_discharge_balance_warn_error_watts",
    "auto_battery_discharge_balance_bias_start_error_watts",
    "auto_battery_discharge_balance_bias_max_penalty_watts",
    "auto_battery_discharge_balance_bias_reserve_margin_soc",
    "auto_battery_discharge_balance_coordination_start_error_watts",
    "auto_battery_discharge_balance_coordination_max_penalty_watts",
    "auto_battery_discharge_balance_victron_bias_base_setpoint_watts",
    "auto_battery_discharge_balance_victron_bias_deadband_watts",
    "auto_battery_discharge_balance_victron_bias_kp",
    "auto_battery_discharge_balance_victron_bias_ki",
    "auto_battery_discharge_balance_victron_bias_kd",
    "auto_battery_discharge_balance_victron_bias_integral_limit_watts",
    "auto_battery_discharge_balance_victron_bias_max_abs_watts",
    "auto_battery_discharge_balance_victron_bias_ramp_rate_watts_per_second",
    "auto_battery_discharge_balance_victron_bias_min_update_seconds",
    "auto_contactor_fault_latch_seconds",
)


class RuntimeConfigValidator:
    """Validate mutable runtime configuration at the service boundary."""

    def __init__(self, service: object) -> None:
        self.service = service

    NON_NEGATIVE_INTERVAL_ATTRS = (
        "auto_pv_scan_interval_seconds",
        "auto_battery_scan_interval_seconds",
        "auto_dbus_backoff_base_seconds",
        "auto_dbus_backoff_max_seconds",
        "auto_grid_missing_stop_seconds",
        "auto_average_window_seconds",
        "auto_min_runtime_seconds",
        "auto_min_offtime_seconds",
        "auto_start_delay_seconds",
        "auto_stop_delay_seconds",
        "auto_scheduled_night_start_delay_seconds",
        "auto_input_cache_seconds",
        "auto_input_helper_restart_seconds",
        "auto_input_helper_stale_seconds",
        "auto_shelly_soft_fail_seconds",
        "auto_watchdog_stale_seconds",
        "auto_watchdog_recovery_seconds",
        "auto_watchdog_restart_attempts",
        "auto_startup_warmup_seconds",
        "auto_manual_override_seconds",
        "auto_audit_log_max_age_hours",
        "auto_audit_log_repeat_seconds",
    )

    @staticmethod
    def _clamp_min_int(
        svc: object,
        attr_name: str,
        minimum: int,
        label: str,
        unit: str,
    ) -> None:
        value = getattr(svc, attr_name)
        if not isinstance(value, int):
            raise TypeError(f"{attr_name} must be an integer")
        if value >= minimum:
            return
        logging.warning("%s %s too small, clamping to %s%s", label, value, minimum, unit)
        setattr(svc, attr_name, minimum)

    @staticmethod
    def _clamp_non_negative_float(svc: object, attr_name: str) -> None:
        if not hasattr(svc, attr_name):
            return
        value = getattr(svc, attr_name)
        if not isinstance(value, (int, float)):
            raise TypeError(f"{attr_name} must be numeric")
        if value >= 0:
            return
        logging.warning("%s %s invalid, clamping to 0", attr_name, value)
        setattr(svc, attr_name, 0.0)

    @staticmethod
    def _clamp_positive_timeout(
        svc: object,
        attr_name: str,
        minimum: float,
        label: str,
    ) -> None:
        value = getattr(svc, attr_name)
        if not isinstance(value, (int, float)):
            raise TypeError(f"{attr_name} must be numeric")
        if value > 0:
            return
        logging.warning("%s %s invalid, clamping to %s", label, value, minimum)
        setattr(svc, attr_name, minimum)

    def _clamp_interval_settings(self) -> None:
        for attr_name in self.NON_NEGATIVE_INTERVAL_ATTRS:
            self._clamp_non_negative_float(self.service, attr_name)

    @staticmethod
    def _clamp_fraction(
        svc: object,
        attr_name: str,
        label: str,
        default: float,
    ) -> None:
        value = getattr(svc, attr_name)
        if not isinstance(value, (int, float)):
            raise TypeError(f"{attr_name} must be numeric")
        if 0 < value <= 1:
            return
        logging.warning("%s %s outside (0,1], clamping to %s", label, value, default)
        setattr(svc, attr_name, float(default))

    def validate(self) -> None:
        svc = self.service
        self._clamp_min_int(svc, "poll_interval_ms", 100, "PollIntervalMs", " ms")
        self._clamp_min_int(svc, "sign_of_life_minutes", 1, "SignOfLifeLog", " minute")
        self._clamp_min_int(svc, "auto_pv_max_services", 1, "AutoPvMaxServices", "")
        self._clamp_interval_settings()
        self._validate_scheduled_runtime_config(svc)
        self._validate_timeout_settings(svc)
        self._validate_balance_runtime_config(svc)
        policy = getattr(svc, "auto_policy")
        if not isinstance(policy, AutoPolicy):
            raise TypeError("state service must expose AutoPolicy as auto_policy")
        validate_auto_policy(policy)

    @staticmethod
    def _validate_scheduled_runtime_config(svc: object) -> None:
        if hasattr(svc, "auto_scheduled_enabled_days"):
            enabled_days = scheduled_enabled_days_text(
                getattr(svc, "auto_scheduled_enabled_days"),
                DEFAULT_SCHEDULED_ENABLED_DAYS,
            )
            setattr(svc, "auto_scheduled_enabled_days", enabled_days)
        if hasattr(svc, "auto_scheduled_latest_end_time"):
            latest_end = normalize_hhmm_text(
                getattr(svc, "auto_scheduled_latest_end_time"),
                "06:30",
            )
            setattr(svc, "auto_scheduled_latest_end_time", latest_end)
        if hasattr(svc, "auto_scheduled_night_current_amps"):
            RuntimeConfigValidator._clamp_non_negative_float(svc, "auto_scheduled_night_current_amps")

    @staticmethod
    def _validate_optional_non_negative_int(svc: object, attr_name: str, label: str) -> None:
        if not hasattr(svc, attr_name):
            return
        value = getattr(svc, attr_name)
        if not isinstance(value, int):
            raise TypeError(f"{attr_name} must be an integer")
        if value >= 0:
            return
        logging.warning("%s %s invalid, clamping to 0", label, value)
        setattr(svc, attr_name, 0)

    def _validate_timeout_settings(self, svc: object) -> None:
        for attr_name, default, label in (
            ("shelly_request_timeout_seconds", 2.0, "ShellyRequestTimeoutSeconds"),
            ("dbus_method_timeout_seconds", 1.0, "DbusMethodTimeoutSeconds"),
        ):
            self._clamp_positive_timeout(svc, attr_name, default, label)
        for attr_name, default, label in (
            ("auto_audit_log_max_age_hours", 168.0, "AutoAuditLogMaxAgeHours"),
            ("auto_audit_log_repeat_seconds", 30.0, "AutoAuditLogRepeatSeconds"),
        ):
            if hasattr(svc, attr_name):
                self._clamp_positive_timeout(svc, attr_name, default, label)

    @staticmethod
    def _normalize_discharge_balance_bias_mode(svc: object) -> None:
        if not hasattr(svc, "auto_battery_discharge_balance_bias_mode"):
            return
        RuntimeConfigValidator._normalize_choice(
            svc,
            "auto_battery_discharge_balance_bias_mode",
            _BALANCE_ACTIVATION_MODES,
            "always",
            "AutoBatteryDischargeBalanceBiasMode",
        )

    @staticmethod
    def _normalize_discharge_balance_coordination_support_mode(svc: object) -> None:
        if not hasattr(svc, "auto_battery_discharge_balance_coordination_support_mode"):
            return
        RuntimeConfigValidator._normalize_choice(
            svc,
            "auto_battery_discharge_balance_coordination_support_mode",
            _BALANCE_SUPPORT_MODES,
            "supported_only",
            "AutoBatteryDischargeBalanceCoordinationSupportMode",
        )

    @staticmethod
    def _normalize_victron_balance_support_mode(svc: object) -> None:
        if not hasattr(svc, "auto_battery_discharge_balance_victron_bias_support_mode"):
            return
        RuntimeConfigValidator._normalize_choice(
            svc,
            "auto_battery_discharge_balance_victron_bias_support_mode",
            _BALANCE_SUPPORT_MODES,
            "allow_experimental",
            "AutoBatteryDischargeBalanceVictronBiasSupportMode",
        )

    @staticmethod
    def _normalize_victron_balance_activation_mode(svc: object) -> None:
        if not hasattr(svc, "auto_battery_discharge_balance_victron_bias_activation_mode"):
            return
        RuntimeConfigValidator._normalize_choice(
            svc,
            "auto_battery_discharge_balance_victron_bias_activation_mode",
            _BALANCE_ACTIVATION_MODES,
            "always",
            "AutoBatteryDischargeBalanceVictronBiasActivationMode",
        )

    @staticmethod
    def _normalize_choice(
        svc: object,
        attr_name: str,
        allowed: frozenset[str],
        fallback: str,
        label: str,
    ) -> None:
        original = getattr(svc, attr_name)
        normalized = str(original).strip().lower()
        if normalized in allowed:
            setattr(svc, attr_name, normalized)
            return
        logging.warning("%s %s invalid, clamping to %s", label, original, fallback)
        setattr(svc, attr_name, fallback)

    def _normalize_victron_balance_auto_apply_settings(self, svc: object) -> None:
        if hasattr(svc, "auto_battery_discharge_balance_victron_bias_auto_apply_min_confidence"):
            self._clamp_fraction(
                svc,
                "auto_battery_discharge_balance_victron_bias_auto_apply_min_confidence",
                "AutoBatteryDischargeBalanceVictronBiasAutoApplyMinConfidence",
                0.85,
            )
        if hasattr(svc, "auto_battery_discharge_balance_victron_bias_auto_apply_min_stability_score"):
            self._clamp_fraction(
                svc,
                "auto_battery_discharge_balance_victron_bias_auto_apply_min_stability_score",
                "AutoBatteryDischargeBalanceVictronBiasAutoApplyMinStabilityScore",
                0.75,
            )
        if hasattr(svc, "auto_battery_discharge_balance_victron_bias_auto_apply_blend"):
            self._clamp_fraction(
                svc,
                "auto_battery_discharge_balance_victron_bias_auto_apply_blend",
                "AutoBatteryDischargeBalanceVictronBiasAutoApplyBlend",
                0.25,
            )
        self._validate_optional_non_negative_int(
            svc,
            "auto_battery_discharge_balance_victron_bias_auto_apply_min_profile_samples",
            "AutoBatteryDischargeBalanceVictronBiasAutoApplyMinProfileSamples",
        )
        self._clamp_non_negative_float(
            svc,
            "auto_battery_discharge_balance_victron_bias_observation_window_seconds",
        )
        self._clamp_non_negative_float(
            svc,
            "auto_battery_discharge_balance_victron_bias_oscillation_lockout_window_seconds",
        )
        self._validate_optional_non_negative_int(
            svc,
            "auto_battery_discharge_balance_victron_bias_oscillation_lockout_min_direction_changes",
            "AutoBatteryDischargeBalanceVictronBiasOscillationLockoutMinDirectionChanges",
        )
        self._clamp_non_negative_float(
            svc,
            "auto_battery_discharge_balance_victron_bias_oscillation_lockout_duration_seconds",
        )
        if hasattr(svc, "auto_battery_discharge_balance_victron_bias_rollback_min_stability_score"):
            self._clamp_fraction(
                svc,
                "auto_battery_discharge_balance_victron_bias_rollback_min_stability_score",
                "AutoBatteryDischargeBalanceVictronBiasRollbackMinStabilityScore",
                0.45,
            )

    def _validate_balance_runtime_config(self, svc: object) -> None:
        for attr_name in _BALANCE_NON_NEGATIVE_FLOAT_ATTRS:
            self._clamp_non_negative_float(svc, attr_name)
        self._normalize_discharge_balance_bias_mode(svc)
        self._normalize_discharge_balance_coordination_support_mode(svc)
        self._normalize_victron_balance_activation_mode(svc)
        self._normalize_victron_balance_support_mode(svc)
        self._normalize_victron_balance_auto_apply_settings(svc)
        self._validate_optional_non_negative_int(
            svc,
            "auto_contactor_fault_latch_count",
            "AutoContactorFaultLatchCount",
        )

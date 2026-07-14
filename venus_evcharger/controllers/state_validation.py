# SPDX-License-Identifier: GPL-3.0-or-later
"""Runtime-config validation helpers for the state controller."""

from __future__ import annotations

import logging
from typing import Any

from venus_evcharger.auto.policy import validate_auto_policy
from venus_evcharger.core.common import DEFAULT_SCHEDULED_ENABLED_DAYS, normalize_hhmm_text, scheduled_enabled_days_text
from venus_evcharger.controllers.state_summary import _StateSummary


_BALANCE_ACTIVATION_MODES = frozenset(
    {"always", "export_only", "above_reserve_band", "export_and_above_reserve_band"}
)
_BALANCE_SUPPORT_MODES = frozenset({"supported_only", "allow_experimental"})


class _StateValidation(_StateSummary):
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
        "auto_phase_upshift_delay_seconds",
        "auto_phase_downshift_delay_seconds",
        "auto_phase_mismatch_retry_seconds",
        "auto_phase_mismatch_lockout_seconds",
        "startup_device_info_retry_seconds",
        "auto_audit_log_max_age_hours",
        "auto_audit_log_repeat_seconds",
    )

    @staticmethod
    def _clamp_min_int(svc: Any, attr_name: str, minimum: int, label: str, unit: str) -> None:
        value = getattr(svc, attr_name)
        if value >= minimum:
            return
        logging.warning("%s %s too small, clamping to %s%s", label, value, minimum, unit)
        setattr(svc, attr_name, minimum)

    @staticmethod
    def _clamp_non_negative_float(svc: Any, attr_name: str) -> None:
        if not hasattr(svc, attr_name):
            return
        value = getattr(svc, attr_name)
        if value >= 0:
            return
        logging.warning("%s %s invalid, clamping to 0", attr_name, value)
        setattr(svc, attr_name, 0.0)

    @staticmethod
    def _clamp_positive_timeout(svc: Any, attr_name: str, minimum: float, label: str) -> None:
        value = getattr(svc, attr_name)
        if value > 0:
            return
        logging.warning("%s %s invalid, clamping to %s", label, value, minimum)
        setattr(svc, attr_name, minimum)

    @staticmethod
    def _clamp_percentage(svc: Any, attr_name: str, label: str) -> None:
        value = getattr(svc, attr_name)
        if 0 <= value <= 100:
            return
        logging.warning("%s %s outside 0..100, clamping", label, value)
        setattr(svc, attr_name, min(100.0, max(0.0, value)))

    def _clamp_interval_settings(self) -> None:
        for attr_name in self.NON_NEGATIVE_INTERVAL_ATTRS:
            self._clamp_non_negative_float(self.service, attr_name)

    def _clamp_soc_thresholds(self) -> None:
        svc = self.service
        self._clamp_percentage(svc, "auto_min_soc", "AutoMinSoc")
        self._clamp_percentage(svc, "auto_resume_soc", "AutoResumeSoc")
        if hasattr(svc, "auto_high_soc_threshold"):
            self._clamp_percentage(svc, "auto_high_soc_threshold", "AutoHighSocThreshold")
        if hasattr(svc, "auto_high_soc_release_threshold"):
            self._clamp_percentage(svc, "auto_high_soc_release_threshold", "AutoHighSocReleaseThreshold")
            if svc.auto_high_soc_release_threshold > svc.auto_high_soc_threshold:
                logging.warning(
                    "AutoHighSocReleaseThreshold %s above AutoHighSocThreshold %s, clamping",
                    svc.auto_high_soc_release_threshold,
                    svc.auto_high_soc_threshold,
                )
                svc.auto_high_soc_release_threshold = svc.auto_high_soc_threshold
        if svc.auto_resume_soc >= svc.auto_min_soc:
            return
        logging.warning("AutoResumeSoc %s below AutoMinSoc %s, clamping to AutoMinSoc", svc.auto_resume_soc, svc.auto_min_soc)
        svc.auto_resume_soc = svc.auto_min_soc

    @staticmethod
    def _clamp_surplus_pair(svc: Any, start_attr: str, stop_attr: str, start_label: str, stop_label: str) -> None:
        start_value = getattr(svc, start_attr)
        stop_value = getattr(svc, stop_attr)
        if stop_value <= start_value:
            return
        logging.warning("%s %s above %s %s, clamping", stop_label, stop_value, start_label, start_value)
        setattr(svc, stop_attr, start_value)

    @staticmethod
    def _clamp_surplus_thresholds(svc: Any) -> None:
        _StateValidation._clamp_surplus_pair(
            svc,
            "auto_start_surplus_watts",
            "auto_stop_surplus_watts",
            "AutoStartSurplusWatts",
            "AutoStopSurplusWatts",
        )
        if hasattr(svc, "auto_high_soc_start_surplus_watts") and hasattr(svc, "auto_high_soc_stop_surplus_watts"):
            _StateValidation._clamp_surplus_pair(
                svc,
                "auto_high_soc_start_surplus_watts",
                "auto_high_soc_stop_surplus_watts",
                "AutoHighSocStartSurplusWatts",
                "AutoHighSocStopSurplusWatts",
            )

    @staticmethod
    def _clamp_fraction(svc: Any, attr_name: str, label: str, default: float) -> None:
        value = getattr(svc, attr_name)
        if 0 < value <= 1:
            return
        logging.warning("%s %s outside (0,1], clamping to %s", label, value, default)
        setattr(svc, attr_name, float(default))

    def validate_runtime_config(self) -> None:
        svc = self.service
        self._clamp_min_int(svc, "poll_interval_ms", 100, "PollIntervalMs", " ms")
        self._clamp_min_int(svc, "sign_of_life_minutes", 1, "SignOfLifeLog", " minute")
        self._clamp_min_int(svc, "auto_pv_max_services", 1, "AutoPvMaxServices", "")
        self._clamp_interval_settings()
        self._validate_scheduled_runtime_config(svc)
        self._validate_startup_retry_config(svc)
        self._validate_timeout_settings(svc)
        if hasattr(svc, "auto_policy"):
            validate_auto_policy(svc.auto_policy, svc)
        else:
            self._validate_legacy_auto_config(svc)

    @staticmethod
    def _validate_scheduled_runtime_config(svc: Any) -> None:
        if hasattr(svc, "auto_scheduled_enabled_days"):
            svc.auto_scheduled_enabled_days = scheduled_enabled_days_text(
                svc.auto_scheduled_enabled_days,
                DEFAULT_SCHEDULED_ENABLED_DAYS,
            )
        if hasattr(svc, "auto_scheduled_latest_end_time"):
            svc.auto_scheduled_latest_end_time = normalize_hhmm_text(
                svc.auto_scheduled_latest_end_time,
                "06:30",
            )
        if hasattr(svc, "auto_scheduled_night_current_amps"):
            _StateValidation._clamp_non_negative_float(svc, "auto_scheduled_night_current_amps")

    @staticmethod
    def _validate_startup_retry_config(svc: Any) -> None:
        if svc.startup_device_info_retries >= 0:
            return
        logging.warning("StartupDeviceInfoRetries %s invalid, clamping to 0", svc.startup_device_info_retries)
        svc.startup_device_info_retries = 0

    @staticmethod
    def _validate_optional_non_negative_int(svc: Any, attr_name: str, label: str) -> None:
        if not hasattr(svc, attr_name):
            return
        value = getattr(svc, attr_name)
        if value >= 0:
            return
        logging.warning("%s %s invalid, clamping to 0", label, value)
        setattr(svc, attr_name, 0)

    def _validate_timeout_settings(self, svc: Any) -> None:
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

    def _validate_legacy_auto_config(self, svc: Any) -> None:
        self._clamp_legacy_non_negative_auto_values(svc)
        self._clamp_legacy_reference_power(svc)
        self._clamp_soc_thresholds()
        self._clamp_surplus_thresholds(svc)
        self._clamp_legacy_fractional_values(svc)
        self._clamp_legacy_volatility_band(svc)
        self._normalize_discharge_balance_bias_mode(svc)
        self._normalize_discharge_balance_coordination_support_mode(svc)
        self._normalize_victron_balance_activation_mode(svc)
        self._normalize_victron_balance_support_mode(svc)
        self._normalize_victron_balance_auto_apply_settings(svc)
        self._validate_optional_non_negative_int(svc, "auto_phase_mismatch_lockout_count", "AutoPhaseMismatchLockoutCount")
        self._validate_optional_non_negative_int(svc, "auto_contactor_fault_latch_count", "AutoContactorFaultLatchCount")

    def _clamp_legacy_non_negative_auto_values(self, svc: Any) -> None:
        for attr_name in (
            "auto_grid_recovery_start_seconds",
            "auto_stop_surplus_delay_seconds",
            "auto_scheduled_night_start_delay_seconds",
            "auto_stop_surplus_volatility_low_watts",
            "auto_stop_surplus_volatility_high_watts",
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
            "auto_battery_discharge_balance_victron_bias_auto_apply_min_confidence",
            "auto_battery_discharge_balance_victron_bias_auto_apply_min_stability_score",
            "auto_battery_discharge_balance_victron_bias_auto_apply_blend",
            "auto_phase_upshift_headroom_watts",
            "auto_phase_downshift_margin_watts",
            "auto_learn_charge_power_min_watts",
            "auto_learn_charge_power_start_delay_seconds",
            "auto_learn_charge_power_window_seconds",
            "auto_learn_charge_power_max_age_seconds",
            "auto_phase_mismatch_retry_seconds",
            "auto_phase_mismatch_lockout_seconds",
            "auto_contactor_fault_latch_seconds",
        ):
            if hasattr(svc, attr_name):
                self._clamp_non_negative_float(svc, attr_name)

    @staticmethod
    def _clamp_legacy_reference_power(svc: Any) -> None:
        if not hasattr(svc, "auto_reference_charge_power_watts") or svc.auto_reference_charge_power_watts > 0:
            return
        logging.warning(
            "AutoReferenceChargePowerWatts %s invalid, clamping to 1900.0",
            svc.auto_reference_charge_power_watts,
        )
        svc.auto_reference_charge_power_watts = 1900.0

    @staticmethod
    def _normalize_discharge_balance_bias_mode(svc: Any) -> None:
        if not hasattr(svc, "auto_battery_discharge_balance_bias_mode"):
            return
        _StateValidation._normalize_choice(
            svc,
            "auto_battery_discharge_balance_bias_mode",
            _BALANCE_ACTIVATION_MODES,
            "always",
            "AutoBatteryDischargeBalanceBiasMode",
        )

    @staticmethod
    def _normalize_discharge_balance_coordination_support_mode(svc: Any) -> None:
        if not hasattr(svc, "auto_battery_discharge_balance_coordination_support_mode"):
            return
        _StateValidation._normalize_choice(
            svc,
            "auto_battery_discharge_balance_coordination_support_mode",
            _BALANCE_SUPPORT_MODES,
            "supported_only",
            "AutoBatteryDischargeBalanceCoordinationSupportMode",
        )

    @staticmethod
    def _normalize_victron_balance_support_mode(svc: Any) -> None:
        if not hasattr(svc, "auto_battery_discharge_balance_victron_bias_support_mode"):
            return
        _StateValidation._normalize_choice(
            svc,
            "auto_battery_discharge_balance_victron_bias_support_mode",
            _BALANCE_SUPPORT_MODES,
            "allow_experimental",
            "AutoBatteryDischargeBalanceVictronBiasSupportMode",
        )

    @staticmethod
    def _normalize_victron_balance_activation_mode(svc: Any) -> None:
        if not hasattr(svc, "auto_battery_discharge_balance_victron_bias_activation_mode"):
            return
        _StateValidation._normalize_choice(
            svc,
            "auto_battery_discharge_balance_victron_bias_activation_mode",
            _BALANCE_ACTIVATION_MODES,
            "always",
            "AutoBatteryDischargeBalanceVictronBiasActivationMode",
        )

    @staticmethod
    def _normalize_choice(
        svc: Any,
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

    def _normalize_victron_balance_auto_apply_settings(self, svc: Any) -> None:
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

    def _clamp_legacy_fractional_values(self, svc: Any) -> None:
        for attr_name, label, default in (
            ("auto_stop_ewma_alpha", "AutoStopEwmaAlpha", 0.35),
            ("auto_stop_ewma_alpha_stable", "AutoStopEwmaAlphaStable", 0.55),
            ("auto_stop_ewma_alpha_volatile", "AutoStopEwmaAlphaVolatile", 0.15),
            ("auto_learn_charge_power_alpha", "AutoLearnChargePowerAlpha", 0.2),
        ):
            if hasattr(svc, attr_name):
                self._clamp_fraction(svc, attr_name, label, default)

    @staticmethod
    def _clamp_legacy_volatility_band(svc: Any) -> None:
        if not (
            hasattr(svc, "auto_stop_surplus_volatility_low_watts")
            and hasattr(svc, "auto_stop_surplus_volatility_high_watts")
            and svc.auto_stop_surplus_volatility_high_watts < svc.auto_stop_surplus_volatility_low_watts
        ):
            return
        logging.warning(
            "AutoStopSurplusVolatilityHighWatts %s below AutoStopSurplusVolatilityLowWatts %s, clamping",
            svc.auto_stop_surplus_volatility_high_watts,
            svc.auto_stop_surplus_volatility_low_watts,
        )
        svc.auto_stop_surplus_volatility_high_watts = svc.auto_stop_surplus_volatility_low_watts

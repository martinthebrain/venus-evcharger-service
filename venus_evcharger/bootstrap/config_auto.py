# SPDX-License-Identifier: GPL-3.0-or-later
# mypy: disable-error-code="attr-defined"
from __future__ import annotations

import configparser

from venus_evcharger.auto.policy import load_auto_policy_from_config
from venus_evcharger.bootstrap.config_shared import _config_value, _seasonal_month_windows
from venus_evcharger.core.common import (
    DEFAULT_SCHEDULED_ENABLED_DAYS,
    normalize_hhmm_text,
    scheduled_enabled_days_text,
)
from venus_evcharger.core.split_mixins import ComposableControllerMixin as _ComposableControllerMixin
from venus_evcharger.energy import load_energy_source_settings


class _ServiceBootstrapAutoConfigMixin(_ComposableControllerMixin):
    def _load_auto_source_config(self, defaults: configparser.SectionProxy) -> None:
        """Load PV, battery, and grid source configuration for Auto mode."""
        svc = self.service
        svc.auto_pv_service = defaults.get("AutoPvService", "").strip()
        svc.auto_pv_service_prefix = defaults.get(
            "AutoPvServicePrefix",
            "com.victronenergy.pvinverter",
        ).strip()
        svc.auto_pv_path = defaults.get("AutoPvPath", "/Ac/Power").strip()
        svc.auto_pv_max_services = int(_config_value(defaults, "AutoPvMaxServices", 10))
        svc.auto_pv_scan_interval_seconds = float(_config_value(defaults, "AutoPvScanIntervalSeconds", 60))
        svc.auto_use_dc_pv = defaults.get("AutoUseDcPv", "1").strip().lower() in ("1", "true", "yes", "on")
        svc.auto_dc_pv_service = defaults.get("AutoDcPvService", "com.victronenergy.system").strip()
        svc.auto_dc_pv_path = defaults.get("AutoDcPvPath", "/Dc/Pv/Power").strip()
        svc.auto_battery_service = defaults.get(
            "AutoBatteryService",
            "com.victronenergy.battery.socketcan_can1",
        ).strip()
        svc.auto_battery_soc_path = defaults.get("AutoBatterySocPath", "/Soc").strip()
        svc.auto_battery_service_prefix = defaults.get(
            "AutoBatteryServicePrefix",
            "com.victronenergy.battery",
        ).strip()
        svc.auto_battery_scan_interval_seconds = float(_config_value(defaults, "AutoBatteryScanIntervalSeconds", 60))
        svc.auto_battery_capacity_wh = float(_config_value(defaults, "AutoBatteryCapacityWh", 0))
        svc.auto_battery_chemistry = str(_config_value(defaults, "AutoBatteryChemistry", "lfp")).strip().lower()
        svc.auto_battery_capacity_auto_estimate = defaults.get(
            "AutoBatteryCapacityAutoEstimate",
            "1",
        ).strip().lower() in ("1", "true", "yes", "on")
        svc.auto_battery_capacity_wh_path = defaults.get("AutoBatteryCapacityWhPath", "").strip()
        svc.auto_battery_capacity_ah_path = defaults.get("AutoBatteryCapacityAhPath", "/InstalledCapacity").strip()
        svc.auto_battery_voltage_path = defaults.get("AutoBatteryVoltagePath", "/Dc/0/Voltage").strip()
        svc.auto_battery_capacity_estimate_min_soc = float(_config_value(defaults, "AutoBatteryCapacityEstimateMinSoc", 95))
        svc.auto_battery_capacity_startup_recheck_seconds = float(
            _config_value(defaults, "AutoBatteryCapacityStartupRecheckSeconds", 300)
        )
        svc.auto_battery_power_path = defaults.get("AutoBatteryPowerPath", "").strip()
        svc.auto_battery_ac_power_path = defaults.get("AutoBatteryAcPowerPath", "").strip()
        svc.auto_battery_pv_power_path = defaults.get("AutoBatteryPvPowerPath", "").strip()
        svc.auto_battery_grid_interaction_path = defaults.get("AutoBatteryGridInteractionPath", "").strip()
        svc.auto_battery_operating_mode_path = defaults.get("AutoBatteryOperatingModePath", "").strip()
        svc.auto_allow_without_battery_soc = defaults.get(
            "AutoAllowWithoutBatterySoc",
            "1",
        ).strip().lower() in ("1", "true", "yes", "on")
        svc.auto_energy_sources, svc.auto_use_combined_battery_soc = load_energy_source_settings(defaults)
        svc.auto_energy_source_ids = tuple(source.source_id for source in svc.auto_energy_sources)
        svc.auto_dbus_backoff_base_seconds = float(_config_value(defaults, "AutoDbusBackoffBaseSeconds", 5))
        svc.auto_dbus_backoff_max_seconds = float(_config_value(defaults, "AutoDbusBackoffMaxSeconds", 60))
        svc.auto_grid_service = defaults.get("AutoGridService", "com.victronenergy.system").strip()
        svc.auto_grid_l1_path = defaults.get("AutoGridL1Path", "/Ac/Grid/L1/Power").strip()
        svc.auto_grid_l2_path = defaults.get("AutoGridL2Path", "/Ac/Grid/L2/Power").strip()
        svc.auto_grid_l3_path = defaults.get("AutoGridL3Path", "/Ac/Grid/L3/Power").strip()
        svc.auto_grid_require_all_phases = defaults.get(
            "AutoGridRequireAllPhases",
            "1",
        ).strip().lower() in ("1", "true", "yes", "on")
        svc.auto_grid_missing_stop_seconds = float(_config_value(defaults, "AutoGridMissingStopSeconds", 60))
        svc.auto_grid_recovery_start_seconds = float(
            _config_value(defaults, "AutoGridRecoveryStartSeconds", _config_value(defaults, "AutoStartDelaySeconds", 10))
        )

    def _load_auto_policy_config(self, defaults: configparser.SectionProxy) -> None:
        """Load Auto thresholds, seasonal windows, and timing policy."""
        self._load_auto_surplus_thresholds(defaults)
        self._load_auto_timing_policy(defaults)
        self._load_auto_daytime_policy(defaults)

    def _load_auto_surplus_thresholds(self, defaults: configparser.SectionProxy) -> None:
        """Load Auto thresholds around surplus, SOC, and grid import."""
        svc = self.service
        load_auto_policy_from_config(defaults, svc)

    def _load_auto_timing_policy(self, defaults: configparser.SectionProxy) -> None:
        """Load averaging, runtime, and delay settings for Auto mode."""
        svc = self.service
        svc.auto_average_window_seconds = float(_config_value(defaults, "AutoAverageWindowSeconds", 30))
        svc.auto_min_runtime_seconds = float(_config_value(defaults, "AutoMinRuntimeSeconds", 300))
        svc.auto_min_offtime_seconds = float(_config_value(defaults, "AutoMinOfftimeSeconds", 120))
        svc.auto_start_delay_seconds = float(_config_value(defaults, "AutoStartDelaySeconds", 10))
        svc.auto_stop_delay_seconds = float(_config_value(defaults, "AutoStopDelaySeconds", 10))
        svc.auto_input_cache_seconds = float(_config_value(defaults, "AutoInputCacheSeconds", 120))
        svc.auto_audit_log = defaults.get("AutoAuditLog", "1").strip().lower() in ("1", "true", "yes", "on")
        svc.auto_audit_log_path = defaults.get(
            "AutoAuditLogPath",
            "/var/volatile/log/dbus-venus-evcharger/auto-reasons.log",
        ).strip()
        svc.auto_audit_log_max_age_hours = float(_config_value(defaults, "AutoAuditLogMaxAgeHours", 168))
        svc.auto_audit_log_repeat_seconds = float(_config_value(defaults, "AutoAuditLogRepeatSeconds", 30))
        svc.auto_battery_discharge_balance_policy_enabled = defaults.get(
            "AutoBatteryDischargeBalancePolicyEnabled",
            "0",
        ).strip().lower() in ("1", "true", "yes", "on")
        svc.auto_battery_discharge_balance_warn_error_watts = float(
            _config_value(defaults, "AutoBatteryDischargeBalanceWarnErrorWatts", 500.0)
        )
        svc.auto_battery_discharge_balance_bias_start_error_watts = float(
            _config_value(defaults, "AutoBatteryDischargeBalanceBiasStartErrorWatts", 750.0)
        )
        svc.auto_battery_discharge_balance_bias_max_penalty_watts = float(
            _config_value(defaults, "AutoBatteryDischargeBalanceBiasMaxPenaltyWatts", 300.0)
        )
        svc.auto_battery_discharge_balance_bias_mode = str(
            _config_value(defaults, "AutoBatteryDischargeBalanceBiasMode", "always")
        ).strip().lower()
        svc.auto_battery_discharge_balance_bias_reserve_margin_soc = float(
            _config_value(defaults, "AutoBatteryDischargeBalanceBiasReserveMarginSoc", 5.0)
        )
        svc.auto_battery_discharge_balance_coordination_enabled = defaults.get(
            "AutoBatteryDischargeBalanceCoordinationEnabled",
            "0",
        ).strip().lower() in ("1", "true", "yes", "on")
        svc.auto_battery_discharge_balance_coordination_support_mode = str(
            _config_value(defaults, "AutoBatteryDischargeBalanceCoordinationSupportMode", "supported_only")
        ).strip().lower()
        svc.auto_battery_discharge_balance_coordination_start_error_watts = float(
            _config_value(defaults, "AutoBatteryDischargeBalanceCoordinationStartErrorWatts", 1000.0)
        )
        svc.auto_battery_discharge_balance_coordination_max_penalty_watts = float(
            _config_value(defaults, "AutoBatteryDischargeBalanceCoordinationMaxPenaltyWatts", 200.0)
        )
        svc.auto_battery_discharge_balance_victron_bias_enabled = defaults.get(
            "AutoBatteryDischargeBalanceVictronBiasEnabled",
            "0",
        ).strip().lower() in ("1", "true", "yes", "on")
        svc.auto_battery_discharge_balance_victron_bias_source_id = str(
            _config_value(defaults, "AutoBatteryDischargeBalanceVictronBiasSourceId", "")
        ).strip()
        svc.auto_battery_discharge_balance_victron_bias_service = str(
            _config_value(defaults, "AutoBatteryDischargeBalanceVictronBiasService", "com.victronenergy.settings")
        ).strip()
        svc.auto_battery_discharge_balance_victron_bias_path = str(
            _config_value(defaults, "AutoBatteryDischargeBalanceVictronBiasPath", "/Settings/CGwacs/AcPowerSetPoint")
        ).strip()
        svc.auto_battery_discharge_balance_victron_bias_base_setpoint_watts = float(
            _config_value(defaults, "AutoBatteryDischargeBalanceVictronBiasBaseSetpointWatts", 50.0)
        )
        svc.auto_battery_discharge_balance_victron_bias_deadband_watts = float(
            _config_value(defaults, "AutoBatteryDischargeBalanceVictronBiasDeadbandWatts", 100.0)
        )
        svc.auto_battery_discharge_balance_victron_bias_activation_mode = str(
            _config_value(defaults, "AutoBatteryDischargeBalanceVictronBiasActivationMode", "always")
        ).strip().lower()
        svc.auto_battery_discharge_balance_victron_bias_support_mode = str(
            _config_value(defaults, "AutoBatteryDischargeBalanceVictronBiasSupportMode", "allow_experimental")
        ).strip().lower()
        svc.auto_battery_discharge_balance_victron_bias_kp = float(
            _config_value(defaults, "AutoBatteryDischargeBalanceVictronBiasKp", 0.2)
        )
        svc.auto_battery_discharge_balance_victron_bias_ki = float(
            _config_value(defaults, "AutoBatteryDischargeBalanceVictronBiasKi", 0.02)
        )
        svc.auto_battery_discharge_balance_victron_bias_kd = float(
            _config_value(defaults, "AutoBatteryDischargeBalanceVictronBiasKd", 0.0)
        )
        svc.auto_battery_discharge_balance_victron_bias_integral_limit_watts = float(
            _config_value(defaults, "AutoBatteryDischargeBalanceVictronBiasIntegralLimitWatts", 250.0)
        )
        svc.auto_battery_discharge_balance_victron_bias_max_abs_watts = float(
            _config_value(defaults, "AutoBatteryDischargeBalanceVictronBiasMaxAbsWatts", 500.0)
        )
        svc.auto_battery_discharge_balance_victron_bias_ramp_rate_watts_per_second = float(
            _config_value(defaults, "AutoBatteryDischargeBalanceVictronBiasRampRateWattsPerSecond", 50.0)
        )
        svc.auto_battery_discharge_balance_victron_bias_min_update_seconds = float(
            _config_value(defaults, "AutoBatteryDischargeBalanceVictronBiasMinUpdateSeconds", 2.0)
        )
        svc.auto_battery_discharge_balance_victron_bias_auto_apply_enabled = defaults.get(
            "AutoBatteryDischargeBalanceVictronBiasAutoApplyEnabled",
            "0",
        ).strip().lower() in ("1", "true", "yes", "on")
        svc.auto_battery_discharge_balance_victron_bias_auto_apply_min_confidence = float(
            _config_value(defaults, "AutoBatteryDischargeBalanceVictronBiasAutoApplyMinConfidence", 0.85)
        )
        svc.auto_battery_discharge_balance_victron_bias_auto_apply_min_profile_samples = int(
            _config_value(defaults, "AutoBatteryDischargeBalanceVictronBiasAutoApplyMinProfileSamples", 3)
        )
        svc.auto_battery_discharge_balance_victron_bias_auto_apply_min_stability_score = float(
            _config_value(defaults, "AutoBatteryDischargeBalanceVictronBiasAutoApplyMinStabilityScore", 0.75)
        )
        svc.auto_battery_discharge_balance_victron_bias_auto_apply_blend = float(
            _config_value(defaults, "AutoBatteryDischargeBalanceVictronBiasAutoApplyBlend", 0.25)
        )
        svc.auto_battery_discharge_balance_victron_bias_observation_window_seconds = float(
            _config_value(defaults, "AutoBatteryDischargeBalanceVictronBiasObservationWindowSeconds", 30.0)
        )
        svc.auto_battery_discharge_balance_victron_bias_oscillation_lockout_enabled = defaults.get(
            "AutoBatteryDischargeBalanceVictronBiasOscillationLockoutEnabled",
            "1",
        ).strip().lower() in ("1", "true", "yes", "on")
        svc.auto_battery_discharge_balance_victron_bias_oscillation_lockout_window_seconds = float(
            _config_value(defaults, "AutoBatteryDischargeBalanceVictronBiasOscillationLockoutWindowSeconds", 120.0)
        )
        svc.auto_battery_discharge_balance_victron_bias_oscillation_lockout_min_direction_changes = int(
            _config_value(defaults, "AutoBatteryDischargeBalanceVictronBiasOscillationLockoutMinDirectionChanges", 3)
        )
        svc.auto_battery_discharge_balance_victron_bias_oscillation_lockout_duration_seconds = float(
            _config_value(defaults, "AutoBatteryDischargeBalanceVictronBiasOscillationLockoutDurationSeconds", 180.0)
        )
        svc.auto_battery_discharge_balance_victron_bias_rollback_enabled = defaults.get(
            "AutoBatteryDischargeBalanceVictronBiasRollbackEnabled",
            "1",
        ).strip().lower() in ("1", "true", "yes", "on")
        svc.auto_battery_discharge_balance_victron_bias_rollback_min_stability_score = float(
            _config_value(defaults, "AutoBatteryDischargeBalanceVictronBiasRollbackMinStabilityScore", 0.45)
        )
        svc.auto_battery_discharge_balance_victron_bias_require_clean_phases = defaults.get(
            "AutoBatteryDischargeBalanceVictronBiasTelemetryRequireCleanPhases",
            "1",
        ).strip().lower() in ("1", "true", "yes", "on")

    def _load_auto_daytime_policy(self, defaults: configparser.SectionProxy) -> None:
        """Load seasonal day-window behavior for Auto mode.

        The same section also carries Scheduled-mode inputs because Scheduled is
        modeled as "Auto in the daytime window plus a target-day night boost".
        Keeping both in one loader makes that relationship easier to spot.
        """
        svc = self.service
        svc.auto_daytime_only = defaults.get("AutoDaytimeOnly", "1").strip().lower() in (
            "1",
            "true",
            "yes",
            "on",
        )
        svc.auto_month_windows = _seasonal_month_windows(svc.config, self._month_window)
        svc.auto_scheduled_night_start_delay_seconds = float(
            _config_value(defaults, "AutoScheduledNightStartDelaySeconds", 3600)
        )
        svc.auto_scheduled_enabled_days = scheduled_enabled_days_text(
            defaults.get("AutoScheduledEnabledDays", "Mon,Tue,Wed,Thu,Fri"),
            DEFAULT_SCHEDULED_ENABLED_DAYS,
        )
        svc.auto_scheduled_latest_end_time = normalize_hhmm_text(
            defaults.get("AutoScheduledLatestEndTime", "06:30"),
            "06:30",
        )
        svc.auto_scheduled_night_current_amps = float(
            _config_value(defaults, "AutoScheduledNightCurrentAmps", 0)
        )
        svc.auto_night_lock_stop = defaults.get("AutoNightLockStop", "0").strip().lower() in (
            "1",
            "true",
            "yes",
            "on",
        )

    def _load_helper_and_timeout_config(self, defaults: configparser.SectionProxy) -> None:
        """Load helper-process, watchdog, and request timeout settings."""
        svc = self.service
        auto_input_poll_interval_ms = float(
            _config_value(
                defaults,
                "AutoInputPollIntervalMs",
                _config_value(defaults, "PollIntervalMs", 1000),
            )
        )
        svc.auto_pv_poll_interval_seconds = max(
            0.2,
            float(_config_value(defaults, "AutoPvPollIntervalMs", auto_input_poll_interval_ms)) / 1000.0,
        )
        svc.auto_grid_poll_interval_seconds = max(
            0.2,
            float(_config_value(defaults, "AutoGridPollIntervalMs", auto_input_poll_interval_ms)) / 1000.0,
        )
        svc.auto_battery_poll_interval_seconds = max(
            0.2,
            float(_config_value(defaults, "AutoBatteryPollIntervalMs", auto_input_poll_interval_ms)) / 1000.0,
        )
        svc.auto_input_validation_poll_seconds = max(
            5.0,
            float(_config_value(defaults, "AutoInputValidationPollSeconds", 30)),
        )
        svc.auto_input_snapshot_path = defaults.get(
            "AutoInputSnapshotPath",
            f"/run/dbus-venus-evcharger-auto-{svc.deviceinstance}.json",
        ).strip()
        svc.auto_input_helper_restart_seconds = float(_config_value(defaults, "AutoInputHelperRestartSeconds", 5))
        svc.auto_input_helper_stale_seconds = float(_config_value(defaults, "AutoInputHelperStaleSeconds", 15))
        svc.auto_shelly_soft_fail_seconds = float(_config_value(defaults, "AutoShellySoftFailSeconds", 10))
        svc.auto_contactor_fault_latch_count = int(_config_value(defaults, "AutoContactorFaultLatchCount", 3))
        svc.auto_contactor_fault_latch_seconds = float(_config_value(defaults, "AutoContactorFaultLatchSeconds", 60))
        svc.auto_watchdog_stale_seconds = float(_config_value(defaults, "AutoWatchdogStaleSeconds", 180))
        svc.auto_watchdog_recovery_seconds = float(_config_value(defaults, "AutoWatchdogRecoverySeconds", 60))
        svc.auto_watchdog_restart_attempts = int(_config_value(defaults, "AutoWatchdogRestartAttempts", 5))
        svc.auto_startup_warmup_seconds = float(_config_value(defaults, "AutoStartupWarmupSeconds", 15))
        svc.auto_manual_override_seconds = float(_config_value(defaults, "AutoManualOverrideSeconds", 300))
        svc.startup_device_info_retries = int(_config_value(defaults, "StartupDeviceInfoRetries", 3))
        svc.startup_device_info_retry_seconds = float(_config_value(defaults, "StartupDeviceInfoRetrySeconds", 2))
        svc.shelly_request_timeout_seconds = float(_config_value(defaults, "ShellyRequestTimeoutSeconds", 2.0))
        svc.dbus_method_timeout_seconds = float(_config_value(defaults, "DbusMethodTimeoutSeconds", 1.0))

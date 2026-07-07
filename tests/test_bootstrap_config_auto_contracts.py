# SPDX-License-Identifier: GPL-3.0-or-later
import configparser
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

from tests.venus_evcharger_bootstrap_controller_support import ServiceBootstrapControllerTestCase
from venus_evcharger.bootstrap.config_auto_daytime import load_auto_daytime_policy
from venus_evcharger.bootstrap.config_auto_helper_gateway import load_gateway_and_introspection_config
from venus_evcharger.bootstrap.config_auto_helper_polling import load_helper_polling_config
from venus_evcharger.bootstrap.config_auto_helper_resilience import load_helper_resilience_config
from venus_evcharger.bootstrap.config_auto_sources_battery import load_auto_battery_source_config
from venus_evcharger.bootstrap.config_auto_sources_energy import load_auto_energy_source_config
from venus_evcharger.bootstrap.config_auto_sources_grid import load_auto_grid_source_config
from venus_evcharger.bootstrap.config_auto_sources_pv import load_auto_pv_source_config
from venus_evcharger.bootstrap.config_auto_timing_audit import load_auto_audit_config
from venus_evcharger.bootstrap.config_auto_timing_balance import load_discharge_balance_policy
from venus_evcharger.bootstrap.config_auto_timing_core import load_auto_timing_core_config
from venus_evcharger.bootstrap.config_auto_timing_victron_bias_apply import load_victron_bias_auto_apply_config
from venus_evcharger.bootstrap.config_auto_timing_victron_bias_base import load_victron_bias_base_config
from venus_evcharger.bootstrap.config_auto_timing_victron_bias_pid import load_victron_bias_pid_config
from venus_evcharger.bootstrap.config_auto_timing_victron_bias_safety import load_victron_bias_safety_config
from venus_evcharger.energy.models import EnergySourceDefinition


def _defaults(values: dict[str, str]) -> configparser.SectionProxy:
    parser = configparser.ConfigParser()
    parser.read_dict({"DEFAULT": values})
    return parser["DEFAULT"]


def _battery_bool_attr(config_key: str) -> str:
    return {
        "AutoBatteryCapacityAutoEstimate": "auto_battery_capacity_auto_estimate",
        "AutoAllowWithoutBatterySoc": "auto_allow_without_battery_soc",
    }[config_key]


def _month_window(
    config: configparser.ConfigParser,
    month: int,
    start: str,
    end: str,
) -> tuple[tuple[int, int], tuple[int, int]]:
    del config, month
    return ((int(start[:2]), int(start[3:])), (int(end[:2]), int(end[3:])))


class BootstrapConfigAutoContracts(ServiceBootstrapControllerTestCase):
    def test_auto_policy_loader_delegates_to_surplus_timing_and_daytime_steps(self) -> None:
        controller = self._controller(SimpleNamespace())
        defaults = _defaults({})
        with patch.object(controller, "_load_auto_surplus_thresholds") as surplus, patch.object(
            controller, "_load_auto_timing_policy"
        ) as timing, patch.object(controller, "_load_auto_daytime_policy") as daytime:
            controller._load_auto_policy_config(defaults)

        self.assertEqual(
            [surplus.call_args, timing.call_args, daytime.call_args],
            [call(defaults), call(defaults), call(defaults)],
        )

    def test_auto_surplus_thresholds_delegate_to_policy_loader(self) -> None:
        service = SimpleNamespace()
        controller = self._controller(service)
        defaults = _defaults({})

        with patch("venus_evcharger.bootstrap.config_auto.load_auto_policy_from_config") as load_policy:
            controller._load_auto_surplus_thresholds(defaults)

        load_policy.assert_called_once_with(defaults, service)

    def test_auto_pv_source_config_uses_victron_defaults(self) -> None:
        service = SimpleNamespace()

        load_auto_pv_source_config(service, _defaults({}))

        self.assertEqual(service.auto_pv_service, "")
        self.assertEqual(service.auto_pv_service_prefix, "com.victronenergy.pvinverter")
        self.assertEqual(service.auto_pv_path, "/Ac/Power")
        self.assertEqual(service.auto_pv_max_services, 10)
        self.assertEqual(service.auto_pv_scan_interval_seconds, 60.0)
        self.assertTrue(service.auto_use_dc_pv)
        self.assertEqual(service.auto_dc_pv_service, "com.victronenergy.system")
        self.assertEqual(service.auto_dc_pv_path, "/Dc/Pv/Power")

    def test_auto_pv_source_config_accepts_common_true_values_for_dc_pv(self) -> None:
        for raw_value in ("1", "true", "yes", "on", " TRUE "):
            with self.subTest(raw_value=raw_value):
                service = SimpleNamespace()

                load_auto_pv_source_config(service, _defaults({"AutoUseDcPv": raw_value}))

                self.assertTrue(service.auto_use_dc_pv)

    def test_auto_battery_source_config_uses_victron_defaults(self) -> None:
        service = SimpleNamespace()

        load_auto_battery_source_config(service, _defaults({}))

        self.assertEqual(service.auto_battery_service, "com.victronenergy.battery.socketcan_can1")
        self.assertEqual(service.auto_battery_soc_path, "/Soc")
        self.assertEqual(service.auto_battery_service_prefix, "com.victronenergy.battery")
        self.assertEqual(service.auto_battery_scan_interval_seconds, 60.0)
        self.assertEqual(service.auto_battery_capacity_wh, 0.0)
        self.assertEqual(service.auto_battery_chemistry, "lfp")
        self.assertTrue(service.auto_battery_capacity_auto_estimate)
        self.assertEqual(service.auto_battery_capacity_wh_path, "")
        self.assertEqual(service.auto_battery_capacity_ah_path, "/InstalledCapacity")
        self.assertEqual(service.auto_battery_voltage_path, "/Dc/0/Voltage")
        self.assertEqual(service.auto_battery_capacity_estimate_min_soc, 95.0)
        self.assertEqual(service.auto_battery_capacity_startup_recheck_seconds, 300.0)
        self.assertEqual(service.auto_battery_power_path, "")
        self.assertEqual(service.auto_battery_ac_power_path, "")
        self.assertEqual(service.auto_battery_pv_power_path, "")
        self.assertEqual(service.auto_battery_grid_interaction_path, "")
        self.assertEqual(service.auto_battery_operating_mode_path, "")
        self.assertTrue(service.auto_allow_without_battery_soc)

    def test_auto_battery_source_config_accepts_common_true_values(self) -> None:
        for key in ("AutoBatteryCapacityAutoEstimate", "AutoAllowWithoutBatterySoc"):
            for raw_value in ("1", "true", "yes", "on", " TRUE "):
                with self.subTest(key=key, raw_value=raw_value):
                    service = SimpleNamespace()

                    load_auto_battery_source_config(service, _defaults({key: raw_value}))

                    self.assertTrue(getattr(service, _battery_bool_attr(key)))

    def test_auto_energy_source_config_uses_backoff_defaults_and_passes_defaults_to_loader(self) -> None:
        source = EnergySourceDefinition(source_id="battery-a", service_name="battery-service")
        service = SimpleNamespace()
        defaults = _defaults({})

        with patch(
            "venus_evcharger.bootstrap.config_auto_sources_energy.load_energy_source_settings",
            return_value=((source,), False),
        ) as load_sources:
            load_auto_energy_source_config(service, defaults)

        load_sources.assert_called_once_with(defaults)
        self.assertEqual(service.auto_energy_sources, (source,))
        self.assertFalse(service.auto_use_combined_battery_soc)
        self.assertEqual(service.auto_energy_source_ids, ("battery-a",))
        self.assertEqual(service.auto_dbus_backoff_base_seconds, 5.0)
        self.assertEqual(service.auto_dbus_backoff_max_seconds, 60.0)

    def test_auto_grid_source_config_uses_victron_defaults(self) -> None:
        service = SimpleNamespace()

        load_auto_grid_source_config(service, _defaults({}))

        self.assertEqual(service.auto_grid_service, "com.victronenergy.system")
        self.assertEqual(service.auto_grid_l1_path, "/Ac/Grid/L1/Power")
        self.assertEqual(service.auto_grid_l2_path, "/Ac/Grid/L2/Power")
        self.assertEqual(service.auto_grid_l3_path, "/Ac/Grid/L3/Power")
        self.assertTrue(service.auto_grid_require_all_phases)
        self.assertEqual(service.auto_grid_missing_stop_seconds, 60.0)
        self.assertEqual(service.auto_grid_recovery_start_seconds, 10.0)

    def test_auto_grid_source_config_accepts_common_true_values(self) -> None:
        for raw_value in ("1", "true", "yes", "on", " TRUE "):
            with self.subTest(raw_value=raw_value):
                service = SimpleNamespace()

                load_auto_grid_source_config(service, _defaults({"AutoGridRequireAllPhases": raw_value}))

                self.assertTrue(service.auto_grid_require_all_phases)

    def test_auto_timing_core_config_uses_defaults(self) -> None:
        service = SimpleNamespace()

        load_auto_timing_core_config(service, _defaults({}))

        self.assertEqual(service.auto_average_window_seconds, 30.0)
        self.assertEqual(service.auto_min_runtime_seconds, 300.0)
        self.assertEqual(service.auto_min_offtime_seconds, 120.0)
        self.assertEqual(service.auto_start_delay_seconds, 10.0)
        self.assertEqual(service.auto_stop_delay_seconds, 10.0)
        self.assertEqual(service.auto_input_cache_seconds, 120.0)

    def test_auto_audit_config_uses_defaults(self) -> None:
        service = SimpleNamespace()

        load_auto_audit_config(service, _defaults({}))

        self.assertTrue(service.auto_audit_log)
        self.assertEqual(service.auto_audit_log_path, "/var/volatile/log/dbus-venus-evcharger/auto-reasons.log")
        self.assertEqual(service.auto_audit_log_max_age_hours, 168.0)
        self.assertEqual(service.auto_audit_log_repeat_seconds, 30.0)

    def test_auto_audit_config_accepts_common_true_values(self) -> None:
        for raw_value in ("1", "true", "yes", "on", " TRUE "):
            with self.subTest(raw_value=raw_value):
                service = SimpleNamespace()

                load_auto_audit_config(service, _defaults({"AutoAuditLog": raw_value}))

                self.assertTrue(service.auto_audit_log)

    def test_auto_discharge_balance_policy_uses_defaults(self) -> None:
        service = SimpleNamespace()

        load_discharge_balance_policy(service, _defaults({}))

        self.assertFalse(service.auto_battery_discharge_balance_policy_enabled)
        self.assertEqual(service.auto_battery_discharge_balance_warn_error_watts, 500.0)
        self.assertEqual(service.auto_battery_discharge_balance_bias_start_error_watts, 750.0)
        self.assertEqual(service.auto_battery_discharge_balance_bias_max_penalty_watts, 300.0)
        self.assertEqual(service.auto_battery_discharge_balance_bias_mode, "always")
        self.assertEqual(service.auto_battery_discharge_balance_bias_reserve_margin_soc, 5.0)
        self.assertFalse(service.auto_battery_discharge_balance_coordination_enabled)
        self.assertEqual(service.auto_battery_discharge_balance_coordination_support_mode, "supported_only")
        self.assertEqual(service.auto_battery_discharge_balance_coordination_start_error_watts, 1000.0)
        self.assertEqual(service.auto_battery_discharge_balance_coordination_max_penalty_watts, 200.0)

    def test_auto_discharge_balance_policy_accepts_common_true_values(self) -> None:
        bool_keys = {
            "AutoBatteryDischargeBalancePolicyEnabled": "auto_battery_discharge_balance_policy_enabled",
            "AutoBatteryDischargeBalanceCoordinationEnabled": "auto_battery_discharge_balance_coordination_enabled",
        }
        for key, attr in bool_keys.items():
            for raw_value in ("1", "true", "yes", "on", " TRUE "):
                with self.subTest(key=key, raw_value=raw_value):
                    service = SimpleNamespace()

                    load_discharge_balance_policy(service, _defaults({key: raw_value}))

                    self.assertTrue(getattr(service, attr))

    def test_victron_bias_base_config_uses_defaults(self) -> None:
        service = SimpleNamespace()

        load_victron_bias_base_config(service, _defaults({}))

        self.assertFalse(service.auto_battery_discharge_balance_victron_bias_enabled)
        self.assertEqual(service.auto_battery_discharge_balance_victron_bias_source_id, "")
        self.assertEqual(service.auto_battery_discharge_balance_victron_bias_service, "com.victronenergy.settings")
        self.assertEqual(
            service.auto_battery_discharge_balance_victron_bias_path,
            "/Settings/CGwacs/AcPowerSetPoint",
        )
        self.assertEqual(service.auto_battery_discharge_balance_victron_bias_base_setpoint_watts, 50.0)
        self.assertEqual(service.auto_battery_discharge_balance_victron_bias_deadband_watts, 100.0)
        self.assertEqual(service.auto_battery_discharge_balance_victron_bias_activation_mode, "always")
        self.assertEqual(service.auto_battery_discharge_balance_victron_bias_support_mode, "allow_experimental")

    def test_victron_bias_base_config_accepts_common_true_values(self) -> None:
        for raw_value in ("1", "true", "yes", "on", " TRUE "):
            with self.subTest(raw_value=raw_value):
                service = SimpleNamespace()

                load_victron_bias_base_config(
                    service,
                    _defaults({"AutoBatteryDischargeBalanceVictronBiasEnabled": raw_value}),
                )

                self.assertTrue(service.auto_battery_discharge_balance_victron_bias_enabled)

    def test_victron_bias_pid_config_uses_defaults(self) -> None:
        service = SimpleNamespace()

        load_victron_bias_pid_config(service, _defaults({}))

        self.assertEqual(service.auto_battery_discharge_balance_victron_bias_kp, 0.2)
        self.assertEqual(service.auto_battery_discharge_balance_victron_bias_ki, 0.02)
        self.assertEqual(service.auto_battery_discharge_balance_victron_bias_kd, 0.0)
        self.assertEqual(service.auto_battery_discharge_balance_victron_bias_integral_limit_watts, 250.0)
        self.assertEqual(service.auto_battery_discharge_balance_victron_bias_max_abs_watts, 500.0)
        self.assertEqual(service.auto_battery_discharge_balance_victron_bias_ramp_rate_watts_per_second, 50.0)
        self.assertEqual(service.auto_battery_discharge_balance_victron_bias_min_update_seconds, 2.0)

    def test_victron_bias_auto_apply_config_uses_defaults(self) -> None:
        service = SimpleNamespace()

        load_victron_bias_auto_apply_config(service, _defaults({}))

        self.assertFalse(service.auto_battery_discharge_balance_victron_bias_auto_apply_enabled)
        self.assertEqual(service.auto_battery_discharge_balance_victron_bias_auto_apply_min_confidence, 0.85)
        self.assertEqual(service.auto_battery_discharge_balance_victron_bias_auto_apply_min_profile_samples, 3)
        self.assertEqual(service.auto_battery_discharge_balance_victron_bias_auto_apply_min_stability_score, 0.75)
        self.assertEqual(service.auto_battery_discharge_balance_victron_bias_auto_apply_blend, 0.25)
        self.assertEqual(service.auto_battery_discharge_balance_victron_bias_observation_window_seconds, 30.0)

    def test_victron_bias_auto_apply_config_accepts_common_true_values(self) -> None:
        for raw_value in ("1", "true", "yes", "on", " TRUE "):
            with self.subTest(raw_value=raw_value):
                service = SimpleNamespace()

                load_victron_bias_auto_apply_config(
                    service,
                    _defaults({"AutoBatteryDischargeBalanceVictronBiasAutoApplyEnabled": raw_value}),
                )

                self.assertTrue(service.auto_battery_discharge_balance_victron_bias_auto_apply_enabled)

    def test_victron_bias_safety_config_uses_defaults(self) -> None:
        service = SimpleNamespace()

        load_victron_bias_safety_config(service, _defaults({}))

        self.assertTrue(service.auto_battery_discharge_balance_victron_bias_oscillation_lockout_enabled)
        self.assertEqual(service.auto_battery_discharge_balance_victron_bias_oscillation_lockout_window_seconds, 120.0)
        self.assertEqual(service.auto_battery_discharge_balance_victron_bias_oscillation_lockout_min_direction_changes, 3)
        self.assertEqual(service.auto_battery_discharge_balance_victron_bias_oscillation_lockout_duration_seconds, 180.0)
        self.assertTrue(service.auto_battery_discharge_balance_victron_bias_rollback_enabled)
        self.assertEqual(service.auto_battery_discharge_balance_victron_bias_rollback_min_stability_score, 0.45)
        self.assertTrue(service.auto_battery_discharge_balance_victron_bias_require_clean_phases)

    def test_victron_bias_safety_config_accepts_common_true_values(self) -> None:
        bool_keys = {
            "AutoBatteryDischargeBalanceVictronBiasOscillationLockoutEnabled": (
                "auto_battery_discharge_balance_victron_bias_oscillation_lockout_enabled"
            ),
            "AutoBatteryDischargeBalanceVictronBiasRollbackEnabled": (
                "auto_battery_discharge_balance_victron_bias_rollback_enabled"
            ),
            "AutoBatteryDischargeBalanceVictronBiasTelemetryRequireCleanPhases": (
                "auto_battery_discharge_balance_victron_bias_require_clean_phases"
            ),
        }
        for key, attr in bool_keys.items():
            for raw_value in ("1", "true", "yes", "on", " TRUE "):
                with self.subTest(key=key, raw_value=raw_value):
                    service = SimpleNamespace()

                    load_victron_bias_safety_config(service, _defaults({key: raw_value}))

                    self.assertTrue(getattr(service, attr))

    def test_auto_daytime_policy_uses_defaults(self) -> None:
        parser = configparser.ConfigParser()
        parser.read_dict({"DEFAULT": {}})
        service = SimpleNamespace(config=parser)

        load_auto_daytime_policy(service, parser["DEFAULT"], _month_window)

        self.assertTrue(service.auto_daytime_only)
        self.assertEqual(len(service.auto_month_windows), 12)
        self.assertEqual(service.auto_month_windows[1], ((9, 0), (16, 30)))
        self.assertEqual(service.auto_month_windows[7], ((7, 0), (21, 0)))
        self.assertEqual(service.auto_schedule_timezone, "UTC")
        self.assertEqual(service.auto_scheduled_night_start_delay_seconds, 3600.0)
        self.assertEqual(service.auto_scheduled_enabled_days, "Mon,Tue,Wed,Thu,Fri")
        self.assertEqual(service.auto_scheduled_latest_end_time, "04:30")
        self.assertEqual(service.auto_scheduled_night_current_amps, 0.0)
        self.assertFalse(service.auto_night_lock_stop)

    def test_auto_daytime_policy_accepts_common_true_values(self) -> None:
        bool_keys = {
            "AutoDaytimeOnly": "auto_daytime_only",
            "AutoNightLockStop": "auto_night_lock_stop",
        }
        for key, attr in bool_keys.items():
            for raw_value in ("1", "true", "yes", "on", " TRUE "):
                with self.subTest(key=key, raw_value=raw_value):
                    parser = configparser.ConfigParser()
                    parser.read_dict({"DEFAULT": {key: raw_value}})
                    service = SimpleNamespace(config=parser)

                    load_auto_daytime_policy(service, parser["DEFAULT"], _month_window)

                    self.assertTrue(getattr(service, attr))

    def test_auto_daytime_policy_passes_parser_to_month_window_loader(self) -> None:
        parser = configparser.ConfigParser()
        parser.read_dict({"DEFAULT": {}})
        service = SimpleNamespace(config=parser)

        def month_window(
            config: configparser.ConfigParser,
            month: int,
            start: str,
            end: str,
        ) -> tuple[tuple[int, int], tuple[int, int]]:
            del month, start, end
            self.assertIs(config, parser)
            return ((1, 2), (3, 4))

        load_auto_daytime_policy(service, parser["DEFAULT"], month_window)

        self.assertEqual(service.auto_month_windows[1], ((1, 2), (3, 4)))

    def test_auto_daytime_policy_normalizes_invalid_schedule_values_to_defaults(self) -> None:
        parser = configparser.ConfigParser()
        parser.read_dict(
            {
                "DEFAULT": {
                    "AutoScheduledEnabledDays": "noday",
                    "AutoScheduledLatestEndTime": "99:99",
                }
            }
        )
        service = SimpleNamespace(config=parser)

        load_auto_daytime_policy(service, parser["DEFAULT"], _month_window)

        self.assertEqual(service.auto_scheduled_enabled_days, "Mon,Tue,Wed,Thu,Fri")
        self.assertEqual(service.auto_scheduled_latest_end_time, "04:30")

    def test_auto_source_config_maps_configured_sources_and_dbus_paths(self) -> None:
        source = EnergySourceDefinition(source_id="hybrid", service_name="configured-hybrid")
        defaults = _defaults(
            {
                "AutoPvService": " com.example.pv ",
                "AutoPvServicePrefix": " com.example.pvprefix ",
                "AutoPvPath": " /Pv/Power ",
                "AutoPvMaxServices": "2",
                "AutoPvScanIntervalSeconds": "12",
                "AutoUseDcPv": "off",
                "AutoDcPvService": " com.example.system ",
                "AutoDcPvPath": " /Dc/Pv ",
                "AutoBatteryService": " com.example.battery ",
                "AutoBatterySocPath": " /Battery/Soc ",
                "AutoBatteryServicePrefix": " com.example.battprefix ",
                "AutoBatteryScanIntervalSeconds": "25",
                "AutoBatteryCapacityWh": "12345",
                "AutoBatteryChemistry": " NMC ",
                "AutoBatteryCapacityAutoEstimate": "0",
                "AutoBatteryCapacityWhPath": " /Capacity/Wh ",
                "AutoBatteryCapacityAhPath": " /Capacity/Ah ",
                "AutoBatteryVoltagePath": " /Dc/Voltage ",
                "AutoBatteryCapacityEstimateMinSoc": "88",
                "AutoBatteryCapacityStartupRecheckSeconds": "44",
                "AutoBatteryPowerPath": " /Battery/Power ",
                "AutoBatteryAcPowerPath": " /Battery/AcPower ",
                "AutoBatteryPvPowerPath": " /Battery/PvPower ",
                "AutoBatteryGridInteractionPath": " /Battery/Grid ",
                "AutoBatteryOperatingModePath": " /Battery/Mode ",
                "AutoAllowWithoutBatterySoc": "false",
                "AutoDbusBackoffBaseSeconds": "3",
                "AutoDbusBackoffMaxSeconds": "9",
                "AutoGridService": " com.example.grid ",
                "AutoGridL1Path": " /Grid/L1 ",
                "AutoGridL2Path": " /Grid/L2 ",
                "AutoGridL3Path": " /Grid/L3 ",
                "AutoGridRequireAllPhases": "0",
                "AutoGridMissingStopSeconds": "33",
                "AutoGridRecoveryStartSeconds": "14",
            }
        )
        service = SimpleNamespace()
        controller = self._controller(service)

        with patch(
            "venus_evcharger.bootstrap.config_auto_sources_energy.load_energy_source_settings",
            return_value=((source,), True),
        ) as load_sources:
            controller._load_auto_source_config(defaults)

        load_sources.assert_called_once_with(defaults)
        self.assertEqual(service.auto_pv_service, "com.example.pv")
        self.assertEqual(service.auto_pv_service_prefix, "com.example.pvprefix")
        self.assertEqual(service.auto_pv_path, "/Pv/Power")
        self.assertEqual(service.auto_pv_max_services, 2)
        self.assertEqual(service.auto_pv_scan_interval_seconds, 12.0)
        self.assertFalse(service.auto_use_dc_pv)
        self.assertEqual(service.auto_dc_pv_service, "com.example.system")
        self.assertEqual(service.auto_dc_pv_path, "/Dc/Pv")
        self.assertEqual(service.auto_battery_service, "com.example.battery")
        self.assertEqual(service.auto_battery_soc_path, "/Battery/Soc")
        self.assertEqual(service.auto_battery_service_prefix, "com.example.battprefix")
        self.assertEqual(service.auto_battery_scan_interval_seconds, 25.0)
        self.assertEqual(service.auto_battery_capacity_wh, 12345.0)
        self.assertEqual(service.auto_battery_chemistry, "nmc")
        self.assertFalse(service.auto_battery_capacity_auto_estimate)
        self.assertEqual(service.auto_battery_capacity_wh_path, "/Capacity/Wh")
        self.assertEqual(service.auto_battery_capacity_ah_path, "/Capacity/Ah")
        self.assertEqual(service.auto_battery_voltage_path, "/Dc/Voltage")
        self.assertEqual(service.auto_battery_capacity_estimate_min_soc, 88.0)
        self.assertEqual(service.auto_battery_capacity_startup_recheck_seconds, 44.0)
        self.assertEqual(service.auto_battery_power_path, "/Battery/Power")
        self.assertEqual(service.auto_battery_ac_power_path, "/Battery/AcPower")
        self.assertEqual(service.auto_battery_pv_power_path, "/Battery/PvPower")
        self.assertEqual(service.auto_battery_grid_interaction_path, "/Battery/Grid")
        self.assertEqual(service.auto_battery_operating_mode_path, "/Battery/Mode")
        self.assertFalse(service.auto_allow_without_battery_soc)
        self.assertEqual(service.auto_energy_sources, (source,))
        self.assertTrue(service.auto_use_combined_battery_soc)
        self.assertEqual(service.auto_energy_source_ids, ("hybrid",))
        self.assertEqual(service.auto_dbus_backoff_base_seconds, 3.0)
        self.assertEqual(service.auto_dbus_backoff_max_seconds, 9.0)
        self.assertEqual(service.auto_grid_service, "com.example.grid")
        self.assertEqual(service.auto_grid_l1_path, "/Grid/L1")
        self.assertEqual(service.auto_grid_l2_path, "/Grid/L2")
        self.assertEqual(service.auto_grid_l3_path, "/Grid/L3")
        self.assertFalse(service.auto_grid_require_all_phases)
        self.assertEqual(service.auto_grid_missing_stop_seconds, 33.0)
        self.assertEqual(service.auto_grid_recovery_start_seconds, 14.0)

    def test_auto_source_config_uses_grid_recovery_start_delay_fallback(self) -> None:
        defaults = _defaults({"AutoStartDelaySeconds": "42"})
        service = SimpleNamespace()
        controller = self._controller(service)

        with patch(
            "venus_evcharger.bootstrap.config_auto_sources_energy.load_energy_source_settings",
            return_value=((), False),
        ) as load_sources:
            controller._load_auto_source_config(defaults)

        load_sources.assert_called_once_with(defaults)
        self.assertTrue(service.auto_use_dc_pv)
        self.assertEqual(service.auto_grid_recovery_start_seconds, 42.0)

    def test_auto_timing_policy_maps_balance_and_audit_settings(self) -> None:
        defaults = _defaults(
            {
                "AutoAverageWindowSeconds": "45",
                "AutoMinRuntimeSeconds": "360",
                "AutoMinOfftimeSeconds": "90",
                "AutoStartDelaySeconds": "12",
                "AutoStopDelaySeconds": "18",
                "AutoInputCacheSeconds": "150",
                "AutoAuditLog": "1",
                "AutoAuditLogPath": " /tmp/auto.log ",
                "AutoAuditLogMaxAgeHours": "24",
                "AutoAuditLogRepeatSeconds": "60",
                "AutoBatteryDischargeBalancePolicyEnabled": "1",
                "AutoBatteryDischargeBalanceWarnErrorWatts": "501",
                "AutoBatteryDischargeBalanceBiasStartErrorWatts": "751",
                "AutoBatteryDischargeBalanceBiasMaxPenaltyWatts": "301",
                "AutoBatteryDischargeBalanceBiasMode": " Reserve ",
                "AutoBatteryDischargeBalanceBiasReserveMarginSoc": "6",
                "AutoBatteryDischargeBalanceCoordinationEnabled": "1",
                "AutoBatteryDischargeBalanceCoordinationSupportMode": " always ",
                "AutoBatteryDischargeBalanceCoordinationStartErrorWatts": "1001",
                "AutoBatteryDischargeBalanceCoordinationMaxPenaltyWatts": "201",
                "AutoBatteryDischargeBalanceVictronBiasEnabled": "1",
                "AutoBatteryDischargeBalanceVictronBiasSourceId": " source-a ",
                "AutoBatteryDischargeBalanceVictronBiasService": " com.example.settings ",
                "AutoBatteryDischargeBalanceVictronBiasPath": " /Settings/SetPoint ",
                "AutoBatteryDischargeBalanceVictronBiasBaseSetpointWatts": "51",
                "AutoBatteryDischargeBalanceVictronBiasDeadbandWatts": "101",
                "AutoBatteryDischargeBalanceVictronBiasActivationMode": " Auto ",
                "AutoBatteryDischargeBalanceVictronBiasSupportMode": " strict ",
                "AutoBatteryDischargeBalanceVictronBiasKp": "0.21",
                "AutoBatteryDischargeBalanceVictronBiasKi": "0.03",
                "AutoBatteryDischargeBalanceVictronBiasKd": "0.01",
                "AutoBatteryDischargeBalanceVictronBiasIntegralLimitWatts": "251",
                "AutoBatteryDischargeBalanceVictronBiasMaxAbsWatts": "501",
                "AutoBatteryDischargeBalanceVictronBiasRampRateWattsPerSecond": "51",
                "AutoBatteryDischargeBalanceVictronBiasMinUpdateSeconds": "3",
                "AutoBatteryDischargeBalanceVictronBiasAutoApplyEnabled": "1",
                "AutoBatteryDischargeBalanceVictronBiasAutoApplyMinConfidence": "0.86",
                "AutoBatteryDischargeBalanceVictronBiasAutoApplyMinProfileSamples": "4",
                "AutoBatteryDischargeBalanceVictronBiasAutoApplyMinStabilityScore": "0.76",
                "AutoBatteryDischargeBalanceVictronBiasAutoApplyBlend": "0.26",
                "AutoBatteryDischargeBalanceVictronBiasObservationWindowSeconds": "31",
                "AutoBatteryDischargeBalanceVictronBiasOscillationLockoutEnabled": "0",
                "AutoBatteryDischargeBalanceVictronBiasOscillationLockoutWindowSeconds": "121",
                "AutoBatteryDischargeBalanceVictronBiasOscillationLockoutMinDirectionChanges": "4",
                "AutoBatteryDischargeBalanceVictronBiasOscillationLockoutDurationSeconds": "181",
                "AutoBatteryDischargeBalanceVictronBiasRollbackEnabled": "0",
                "AutoBatteryDischargeBalanceVictronBiasRollbackMinStabilityScore": "0.46",
                "AutoBatteryDischargeBalanceVictronBiasTelemetryRequireCleanPhases": "0",
            }
        )
        service = SimpleNamespace()
        controller = self._controller(service)

        controller._load_auto_timing_policy(defaults)

        self.assertEqual(service.auto_average_window_seconds, 45.0)
        self.assertEqual(service.auto_min_runtime_seconds, 360.0)
        self.assertEqual(service.auto_min_offtime_seconds, 90.0)
        self.assertEqual(service.auto_start_delay_seconds, 12.0)
        self.assertEqual(service.auto_stop_delay_seconds, 18.0)
        self.assertEqual(service.auto_input_cache_seconds, 150.0)
        self.assertTrue(service.auto_audit_log)
        self.assertEqual(service.auto_audit_log_path, "/tmp/auto.log")
        self.assertEqual(service.auto_audit_log_max_age_hours, 24.0)
        self.assertEqual(service.auto_audit_log_repeat_seconds, 60.0)
        self.assertTrue(service.auto_battery_discharge_balance_policy_enabled)
        self.assertEqual(service.auto_battery_discharge_balance_warn_error_watts, 501.0)
        self.assertEqual(service.auto_battery_discharge_balance_bias_start_error_watts, 751.0)
        self.assertEqual(service.auto_battery_discharge_balance_bias_max_penalty_watts, 301.0)
        self.assertEqual(service.auto_battery_discharge_balance_bias_mode, "reserve")
        self.assertEqual(service.auto_battery_discharge_balance_bias_reserve_margin_soc, 6.0)
        self.assertTrue(service.auto_battery_discharge_balance_coordination_enabled)
        self.assertEqual(service.auto_battery_discharge_balance_coordination_support_mode, "always")
        self.assertEqual(service.auto_battery_discharge_balance_coordination_start_error_watts, 1001.0)
        self.assertEqual(service.auto_battery_discharge_balance_coordination_max_penalty_watts, 201.0)
        self.assertTrue(service.auto_battery_discharge_balance_victron_bias_enabled)
        self.assertEqual(service.auto_battery_discharge_balance_victron_bias_source_id, "source-a")
        self.assertEqual(service.auto_battery_discharge_balance_victron_bias_service, "com.example.settings")
        self.assertEqual(service.auto_battery_discharge_balance_victron_bias_path, "/Settings/SetPoint")
        self.assertEqual(service.auto_battery_discharge_balance_victron_bias_base_setpoint_watts, 51.0)
        self.assertEqual(service.auto_battery_discharge_balance_victron_bias_deadband_watts, 101.0)
        self.assertEqual(service.auto_battery_discharge_balance_victron_bias_activation_mode, "auto")
        self.assertEqual(service.auto_battery_discharge_balance_victron_bias_support_mode, "strict")
        self.assertEqual(service.auto_battery_discharge_balance_victron_bias_kp, 0.21)
        self.assertEqual(service.auto_battery_discharge_balance_victron_bias_ki, 0.03)
        self.assertEqual(service.auto_battery_discharge_balance_victron_bias_kd, 0.01)
        self.assertEqual(service.auto_battery_discharge_balance_victron_bias_integral_limit_watts, 251.0)
        self.assertEqual(service.auto_battery_discharge_balance_victron_bias_max_abs_watts, 501.0)
        self.assertEqual(service.auto_battery_discharge_balance_victron_bias_ramp_rate_watts_per_second, 51.0)
        self.assertEqual(service.auto_battery_discharge_balance_victron_bias_min_update_seconds, 3.0)
        self.assertTrue(service.auto_battery_discharge_balance_victron_bias_auto_apply_enabled)
        self.assertEqual(service.auto_battery_discharge_balance_victron_bias_auto_apply_min_confidence, 0.86)
        self.assertEqual(service.auto_battery_discharge_balance_victron_bias_auto_apply_min_profile_samples, 4)
        self.assertEqual(service.auto_battery_discharge_balance_victron_bias_auto_apply_min_stability_score, 0.76)
        self.assertEqual(service.auto_battery_discharge_balance_victron_bias_auto_apply_blend, 0.26)
        self.assertEqual(service.auto_battery_discharge_balance_victron_bias_observation_window_seconds, 31.0)
        self.assertFalse(service.auto_battery_discharge_balance_victron_bias_oscillation_lockout_enabled)
        self.assertEqual(service.auto_battery_discharge_balance_victron_bias_oscillation_lockout_window_seconds, 121.0)
        self.assertEqual(service.auto_battery_discharge_balance_victron_bias_oscillation_lockout_min_direction_changes, 4)
        self.assertEqual(service.auto_battery_discharge_balance_victron_bias_oscillation_lockout_duration_seconds, 181.0)
        self.assertFalse(service.auto_battery_discharge_balance_victron_bias_rollback_enabled)
        self.assertEqual(service.auto_battery_discharge_balance_victron_bias_rollback_min_stability_score, 0.46)
        self.assertFalse(service.auto_battery_discharge_balance_victron_bias_require_clean_phases)

    def test_auto_daytime_policy_maps_schedule_fields_and_normalizes_defaults(self) -> None:
        parser = configparser.ConfigParser()
        parser.read_dict(
            {
                "DEFAULT": {
                    "AutoDaytimeOnly": "0",
                    "AutoScheduleTimezone": "   ",
                    "AutoScheduledNightStartDelaySeconds": "7200",
                    "AutoScheduledEnabledDays": "Sat,Sun",
                    "AutoScheduledLatestEndTime": "5:45",
                    "AutoScheduledNightCurrentAmps": "13",
                    "AutoNightLockStop": "1",
                }
            }
        )
        service = SimpleNamespace(config=parser)
        controller = self._controller(service)

        controller._load_auto_daytime_policy(parser["DEFAULT"])

        self.assertFalse(service.auto_daytime_only)
        self.assertEqual(len(service.auto_month_windows), 12)
        self.assertTrue(all(window == ((8, 0), (18, 0)) for window in service.auto_month_windows.values()))
        self.assertEqual(service.auto_schedule_timezone, "UTC")
        self.assertEqual(service.auto_scheduled_night_start_delay_seconds, 7200.0)
        self.assertEqual(service.auto_scheduled_enabled_days, "Sat,Sun")
        self.assertEqual(service.auto_scheduled_latest_end_time, "05:45")
        self.assertEqual(service.auto_scheduled_night_current_amps, 13.0)
        self.assertTrue(service.auto_night_lock_stop)

    def test_helper_and_gateway_config_applies_minimums_and_device_paths(self) -> None:
        defaults = _defaults(
            {
                "PollIntervalMs": "100",
                "AutoPvPollIntervalMs": "100",
                "AutoGridPollIntervalMs": "150",
                "AutoBatteryPollIntervalMs": "199",
                "AutoInputValidationPollSeconds": "1",
                "DbusGatewayRunDir": " /tmp/gateway ",
                "DbusIntrospectionEnabled": "0",
                "DbusIntrospectionMaxAgeSeconds": "123",
                "AutoInputHelperRestartSeconds": "8",
                "AutoInputHelperStaleSeconds": "19",
                "AutoShellySoftFailSeconds": "17",
                "AutoContactorFaultLatchCount": "4",
                "AutoContactorFaultLatchSeconds": "61",
                "AutoWatchdogStaleSeconds": "111",
                "AutoWatchdogRecoverySeconds": "22",
                "AutoWatchdogRestartAttempts": "6",
                "AutoStartupWarmupSeconds": "18",
                "AutoManualOverrideSeconds": "333",
                "StartupDeviceInfoRetries": "4",
                "StartupDeviceInfoRetrySeconds": "1.5",
                "ShellyRequestTimeoutSeconds": "4.5",
                "DbusMethodTimeoutSeconds": "2.5",
            }
        )
        service = SimpleNamespace(deviceinstance=77)
        controller = self._controller(service)

        controller._load_helper_and_timeout_config(defaults)

        self.assertEqual(service.auto_pv_poll_interval_seconds, 0.2)
        self.assertEqual(service.auto_grid_poll_interval_seconds, 0.2)
        self.assertEqual(service.auto_battery_poll_interval_seconds, 0.2)
        self.assertEqual(service.auto_input_validation_poll_seconds, 5.0)
        self.assertEqual(service.auto_input_snapshot_path, "/run/dbus-venus-evcharger-auto-77.json")
        self.assertEqual(service.dbus_gateway_run_dir, "/tmp/gateway")
        self.assertEqual(service.dbus_gateway_cache_path, "/tmp/gateway/dbus-cache.json")
        self.assertEqual(service.dbus_gateway_health_path, "/tmp/gateway/dbus-health.json")
        self.assertEqual(service.dbus_gateway_socket_path, "/tmp/gateway/gateway.sock")
        self.assertEqual(service.dbus_gateway_command_dir, "/tmp/gateway/dbus-commands")
        self.assertEqual(service.dbus_gateway_core_command_dir, "/tmp/gateway/core-commands")
        self.assertFalse(service.dbus_introspection_enabled)
        self.assertEqual(service.dbus_introspection_snapshot_path, "/run/dbus-venus-evcharger-dbus-map-77.json")
        self.assertEqual(service.dbus_introspection_request_path, "/run/dbus-venus-evcharger-dbus-map-requests-77.json")
        self.assertEqual(service.dbus_introspection_max_age_seconds, 123.0)
        self.assertEqual(service.auto_input_helper_restart_seconds, 8.0)
        self.assertEqual(service.auto_input_helper_stale_seconds, 19.0)
        self.assertEqual(service.auto_shelly_soft_fail_seconds, 17.0)
        self.assertEqual(service.auto_contactor_fault_latch_count, 4)
        self.assertEqual(service.auto_contactor_fault_latch_seconds, 61.0)
        self.assertEqual(service.auto_watchdog_stale_seconds, 111.0)
        self.assertEqual(service.auto_watchdog_recovery_seconds, 22.0)
        self.assertEqual(service.auto_watchdog_restart_attempts, 6)
        self.assertEqual(service.auto_startup_warmup_seconds, 18.0)
        self.assertEqual(service.auto_manual_override_seconds, 333.0)
        self.assertEqual(service.startup_device_info_retries, 4)
        self.assertEqual(service.startup_device_info_retry_seconds, 1.5)
        self.assertEqual(service.shelly_request_timeout_seconds, 4.5)
        self.assertEqual(service.dbus_method_timeout_seconds, 2.5)

    def test_helper_and_timeout_loader_delegates_to_focused_loaders(self) -> None:
        service = SimpleNamespace()
        controller = self._controller(service)
        defaults = _defaults({})

        with patch(
            "venus_evcharger.bootstrap.config_auto_helper.load_helper_polling_config"
        ) as polling, patch(
            "venus_evcharger.bootstrap.config_auto_helper.load_gateway_and_introspection_config"
        ) as gateway, patch(
            "venus_evcharger.bootstrap.config_auto_helper.load_helper_resilience_config"
        ) as resilience:
            controller._load_helper_and_timeout_config(defaults)

        polling.assert_called_once_with(service, defaults)
        gateway.assert_called_once_with(service, defaults)
        resilience.assert_called_once_with(service, defaults)

    def test_helper_polling_config_uses_defaults(self) -> None:
        service = SimpleNamespace(deviceinstance=17)

        load_helper_polling_config(service, _defaults({}))

        self.assertEqual(service.auto_pv_poll_interval_seconds, 1.0)
        self.assertEqual(service.auto_grid_poll_interval_seconds, 1.0)
        self.assertEqual(service.auto_battery_poll_interval_seconds, 1.0)
        self.assertEqual(service.auto_input_validation_poll_seconds, 30.0)
        self.assertEqual(service.auto_input_snapshot_path, "/run/dbus-venus-evcharger-auto-17.json")

    def test_helper_polling_config_uses_auto_input_poll_fallback_and_custom_snapshot(self) -> None:
        service = SimpleNamespace(deviceinstance=18)

        load_helper_polling_config(
            service,
            _defaults(
                {
                    "AutoInputPollIntervalMs": "2500",
                    "AutoInputSnapshotPath": " /tmp/snapshot.json ",
                }
            ),
        )

        self.assertEqual(service.auto_pv_poll_interval_seconds, 2.5)
        self.assertEqual(service.auto_grid_poll_interval_seconds, 2.5)
        self.assertEqual(service.auto_battery_poll_interval_seconds, 2.5)
        self.assertEqual(service.auto_input_snapshot_path, "/tmp/snapshot.json")

    def test_helper_polling_config_uses_legacy_poll_interval_fallback(self) -> None:
        service = SimpleNamespace(deviceinstance=19)

        load_helper_polling_config(service, _defaults({"PollIntervalMs": "4000"}))

        self.assertEqual(service.auto_pv_poll_interval_seconds, 4.0)
        self.assertEqual(service.auto_grid_poll_interval_seconds, 4.0)
        self.assertEqual(service.auto_battery_poll_interval_seconds, 4.0)

    def test_gateway_and_introspection_config_uses_defaults(self) -> None:
        service = SimpleNamespace(deviceinstance=23)

        load_gateway_and_introspection_config(service, _defaults({}))

        self.assertEqual(service.dbus_gateway_run_dir, "/run/venus-evcharger")
        self.assertEqual(service.dbus_gateway_cache_path, "/run/venus-evcharger/dbus-cache.json")
        self.assertEqual(service.dbus_gateway_health_path, "/run/venus-evcharger/dbus-health.json")
        self.assertEqual(service.dbus_gateway_socket_path, "/run/venus-evcharger/gateway.sock")
        self.assertEqual(service.dbus_gateway_command_dir, "/run/venus-evcharger/dbus-commands")
        self.assertEqual(service.dbus_gateway_core_command_dir, "/run/venus-evcharger/core-commands")
        self.assertTrue(service.dbus_introspection_enabled)
        self.assertEqual(service.dbus_introspection_snapshot_path, "/run/dbus-venus-evcharger-dbus-map-23.json")
        self.assertEqual(
            service.dbus_introspection_request_path,
            "/run/dbus-venus-evcharger-dbus-map-requests-23.json",
        )
        self.assertEqual(service.dbus_introspection_max_age_seconds, 900.0)

    def test_gateway_and_introspection_config_accepts_custom_paths_and_true_values(self) -> None:
        for raw_value in ("1", "true", "yes", "on", " TRUE "):
            with self.subTest(raw_value=raw_value):
                service = SimpleNamespace(deviceinstance=24)

                load_gateway_and_introspection_config(
                    service,
                    _defaults(
                        {
                            "DbusGatewayRunDir": " /tmp/run ",
                            "DbusGatewayCachePath": " /tmp/cache.json ",
                            "DbusGatewayHealthPath": " /tmp/health.json ",
                            "DbusGatewaySocketPath": " /tmp/gateway.sock ",
                            "DbusGatewayCommandDir": " /tmp/commands ",
                            "DbusGatewayCoreCommandDir": " /tmp/core-commands ",
                            "DbusIntrospectionEnabled": raw_value,
                            "DbusIntrospectionSnapshotPath": " /tmp/map.json ",
                            "DbusIntrospectionRequestPath": " /tmp/map-requests.json ",
                            "DbusIntrospectionMaxAgeSeconds": "321",
                        }
                    ),
                )

                self.assertEqual(service.dbus_gateway_run_dir, "/tmp/run")
                self.assertEqual(service.dbus_gateway_cache_path, "/tmp/cache.json")
                self.assertEqual(service.dbus_gateway_health_path, "/tmp/health.json")
                self.assertEqual(service.dbus_gateway_socket_path, "/tmp/gateway.sock")
                self.assertEqual(service.dbus_gateway_command_dir, "/tmp/commands")
                self.assertEqual(service.dbus_gateway_core_command_dir, "/tmp/core-commands")
                self.assertTrue(service.dbus_introspection_enabled)
                self.assertEqual(service.dbus_introspection_snapshot_path, "/tmp/map.json")
                self.assertEqual(service.dbus_introspection_request_path, "/tmp/map-requests.json")
                self.assertEqual(service.dbus_introspection_max_age_seconds, 321.0)

    def test_helper_resilience_config_uses_defaults(self) -> None:
        service = SimpleNamespace()

        load_helper_resilience_config(service, _defaults({}))

        self.assertEqual(service.auto_input_helper_restart_seconds, 5.0)
        self.assertEqual(service.auto_input_helper_stale_seconds, 15.0)
        self.assertEqual(service.auto_shelly_soft_fail_seconds, 10.0)
        self.assertEqual(service.auto_contactor_fault_latch_count, 3)
        self.assertEqual(service.auto_contactor_fault_latch_seconds, 60.0)
        self.assertEqual(service.auto_watchdog_stale_seconds, 180.0)
        self.assertEqual(service.auto_watchdog_recovery_seconds, 60.0)
        self.assertEqual(service.auto_watchdog_restart_attempts, 5)
        self.assertEqual(service.auto_startup_warmup_seconds, 15.0)
        self.assertEqual(service.auto_manual_override_seconds, 300.0)
        self.assertEqual(service.startup_device_info_retries, 3)
        self.assertEqual(service.startup_device_info_retry_seconds, 2.0)
        self.assertEqual(service.shelly_request_timeout_seconds, 2.0)
        self.assertEqual(service.dbus_method_timeout_seconds, 1.0)


if __name__ == "__main__":
    unittest.main()

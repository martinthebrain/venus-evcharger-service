# SPDX-License-Identifier: GPL-3.0-or-later
from tests.venus_evcharger_bootstrap_controller_support import (
    MONTH_WINDOW_DEFAULTS,
    MagicMock,
    ServiceBootstrapControllerTestCase,
    SimpleNamespace,
    _logging_level_from_config,
    _seasonal_month_windows,
    configparser,
)
from venus_evcharger.bootstrap.config_identity import (
    _config_bool,
    _config_float,
    _config_int,
    _config_lower_text,
    _config_text,
    _host_is_configured,
)


class TestServiceBootstrapControllerConfig(ServiceBootstrapControllerTestCase):
    def test_identity_config_helpers_trim_coerce_and_detect_configured_hosts(self):
        parser = configparser.ConfigParser()
        parser.optionxform = str
        parser.read_dict(
            {
                "DEFAULT": {
                    "Text": "  value  ",
                    "TruthYes": "YES",
                    "TruthOne": "1",
                    "TruthOn": "on",
                    "FalseText": "off",
                    "IntValue": "42",
                    "FloatValue": "3.5",
                }
            }
        )
        defaults = parser["DEFAULT"]

        self.assertEqual(_config_text(defaults, "Text"), "value")
        self.assertEqual(_config_text(defaults, "MissingText", " fallback "), "fallback")
        self.assertTrue(_config_bool(defaults, "TruthYes"))
        self.assertTrue(_config_bool(defaults, "TruthOne"))
        self.assertTrue(_config_bool(defaults, "TruthOn"))
        self.assertFalse(_config_bool(defaults, "FalseText", True))
        self.assertTrue(_config_bool(defaults, "MissingBool", True))
        self.assertFalse(_config_bool(defaults, "MissingBool"))
        self.assertEqual(_config_lower_text(defaults, "MissingLowerText", "fallback"), "fallback")
        self.assertEqual(_config_lower_text(defaults, "Text", "fallback"), "value")
        self.assertEqual(_config_int(defaults, "IntValue", 7), 42)
        self.assertEqual(_config_int(defaults, "MissingInt", 7), 7)
        self.assertEqual(_config_float(defaults, "FloatValue", 1.25), 3.5)
        self.assertEqual(_config_float(defaults, "MissingFloat", 1.25), 1.25)
        self.assertTrue(_host_is_configured("  192.168.1.2  "))
        self.assertTrue(_host_is_configured(123))
        self.assertFalse(_host_is_configured(""))
        self.assertFalse(_host_is_configured("   "))
        self.assertFalse(_host_is_configured(None))

    def test_load_auto_policy_config_reads_thresholds_timing_and_daytime(self):
        parser = configparser.ConfigParser()
        parser.read_dict(
            {
                "DEFAULT": {
                    "AutoStartSurplusWatts": "1700",
                    "AutoStopSurplusWatts": "1200",
                    "AutoMinSoc": "40",
                    "AutoResumeSoc": "44",
                    "AutoStartMaxGridImportWatts": "70",
                    "AutoStopGridImportWatts": "350",
                    "AutoHighSocThreshold": "50",
                    "AutoHighSocReleaseThreshold": "45",
                    "AutoHighSocStartSurplusWatts": "1650",
                    "AutoHighSocStopSurplusWatts": "800",
                    "AutoAverageWindowSeconds": "45",
                    "AutoMinRuntimeSeconds": "360",
                    "AutoMinOfftimeSeconds": "90",
                    "AutoStartDelaySeconds": "12",
                    "AutoStopDelaySeconds": "18",
                    "AutoStopSurplusDelaySeconds": "54",
                    "AutoStopEwmaAlpha": "0.4",
                    "AutoStopEwmaAlphaStable": "0.6",
                    "AutoStopEwmaAlphaVolatile": "0.2",
                    "AutoStopSurplusVolatilityLowWatts": "120",
                    "AutoStopSurplusVolatilityHighWatts": "380",
                    "AutoLearnChargePower": "1",
                    "AutoReferenceChargePowerWatts": "2050",
                    "AutoLearnChargePowerMinWatts": "650",
                    "AutoLearnChargePowerAlpha": "0.3",
                    "AutoLearnChargePowerStartDelaySeconds": "40",
                    "AutoLearnChargePowerWindowSeconds": "120",
                    "AutoLearnChargePowerMaxAgeSeconds": "1800",
                    "AutoInputCacheSeconds": "150",
                    "AutoAuditLog": "1",
                    "AutoAuditLogPath": "/tmp/auto.log",
                    "AutoAuditLogMaxAgeHours": "24",
                    "AutoAuditLogRepeatSeconds": "60",
                    "AutoDaytimeOnly": "0",
                    "AutoNightLockStop": "1",
                }
            }
        )
        service = SimpleNamespace(config=parser, deviceinstance=60)
        controller = self._controller(service)

        controller.components.auto.load(parser["DEFAULT"])

        self.assertEqual(service.auto_policy.normal_profile.start_surplus_watts, 1700.0)
        self.assertEqual(service.auto_policy.normal_profile.stop_surplus_watts, 1200.0)
        self.assertEqual(service.auto_policy.high_soc_threshold, 50.0)
        self.assertEqual(service.auto_policy.high_soc_release_threshold, 45.0)
        self.assertEqual(service.auto_policy.high_soc_profile.start_surplus_watts, 1650.0)
        self.assertEqual(service.auto_policy.high_soc_profile.stop_surplus_watts, 800.0)
        self.assertEqual(service.auto_policy.min_soc, 40.0)
        self.assertEqual(service.auto_policy.resume_soc, 44.0)
        self.assertEqual(service.auto_policy.start_max_grid_import_watts, 70.0)
        self.assertEqual(service.auto_policy.stop_grid_import_watts, 350.0)
        self.assertEqual(service.auto_average_window_seconds, 45.0)
        self.assertEqual(service.auto_min_runtime_seconds, 360.0)
        self.assertEqual(service.auto_min_offtime_seconds, 90.0)
        self.assertEqual(service.auto_start_delay_seconds, 12.0)
        self.assertEqual(service.auto_stop_delay_seconds, 18.0)
        self.assertEqual(service.auto_policy.stop_surplus_delay_seconds, 54.0)
        self.assertEqual(service.auto_policy.ewma.base_alpha, 0.4)
        self.assertEqual(service.auto_policy.ewma.stable_alpha, 0.6)
        self.assertEqual(service.auto_policy.ewma.volatile_alpha, 0.2)
        self.assertEqual(service.auto_policy.ewma.volatility_low_watts, 120.0)
        self.assertEqual(service.auto_policy.ewma.volatility_high_watts, 380.0)
        self.assertTrue(service.auto_policy.learn_charge_power.enabled)
        self.assertEqual(service.auto_policy.learn_charge_power.reference_power_watts, 2050.0)
        self.assertEqual(service.auto_policy.learn_charge_power.min_watts, 650.0)
        self.assertEqual(service.auto_policy.learn_charge_power.alpha, 0.3)
        self.assertEqual(service.auto_policy.learn_charge_power.start_delay_seconds, 40.0)
        self.assertEqual(service.auto_policy.learn_charge_power.window_seconds, 120.0)
        self.assertEqual(service.auto_policy.learn_charge_power.max_age_seconds, 1800.0)
        self.assertFalse(hasattr(service, "auto_start_surplus_watts"))
        self.assertFalse(hasattr(service, "auto_learn_charge_power_enabled"))
        self.assertEqual(service.auto_input_cache_seconds, 150.0)
        self.assertTrue(service.auto_audit_log)
        self.assertEqual(service.auto_audit_log_path, "/tmp/auto.log")
        self.assertEqual(service.auto_audit_log_max_age_hours, 24.0)
        self.assertEqual(service.auto_audit_log_repeat_seconds, 60.0)
        self.assertFalse(service.auto_daytime_only)
        self.assertTrue(service.auto_night_lock_stop)

    def test_load_auto_policy_config_validates_policy_while_loading(self):
        parser = configparser.ConfigParser()
        parser.read_dict(
            {
                "DEFAULT": {
                    "AutoStartSurplusWatts": "1850",
                    "AutoStopSurplusWatts": "2400",
                    "AutoMinSoc": "35",
                    "AutoResumeSoc": "30",
                    "AutoHighSocThreshold": "55",
                    "AutoHighSocReleaseThreshold": "60",
                    "AutoHighSocStartSurplusWatts": "1650",
                    "AutoHighSocStopSurplusWatts": "1800",
                    "AutoStopSurplusDelaySeconds": "-1",
                    "AutoStopEwmaAlpha": "-1",
                    "AutoStopEwmaAlphaStable": "-1",
                    "AutoStopEwmaAlphaVolatile": "-1",
                    "AutoStopSurplusVolatilityLowWatts": "200",
                    "AutoStopSurplusVolatilityHighWatts": "100",
                    "AutoReferenceChargePowerWatts": "-1",
                    "AutoLearnChargePowerMinWatts": "-1",
                    "AutoLearnChargePowerAlpha": "-1",
                    "AutoLearnChargePowerStartDelaySeconds": "-1",
                    "AutoLearnChargePowerWindowSeconds": "-1",
                    "AutoLearnChargePowerMaxAgeSeconds": "-1",
                }
            }
        )
        service = SimpleNamespace(config=parser, deviceinstance=60)
        controller = self._controller(service)

        controller.components.auto.load(parser["DEFAULT"])

        self.assertEqual(service.auto_policy.normal_profile.stop_surplus_watts, 1850.0)
        self.assertEqual(service.auto_policy.high_soc_profile.stop_surplus_watts, 1650.0)
        self.assertEqual(service.auto_policy.high_soc_release_threshold, 55.0)
        self.assertEqual(service.auto_policy.resume_soc, 35.0)
        self.assertEqual(service.auto_policy.stop_surplus_delay_seconds, 0.0)
        self.assertEqual(service.auto_policy.ewma.base_alpha, 0.35)
        self.assertEqual(service.auto_policy.ewma.stable_alpha, 0.55)
        self.assertEqual(service.auto_policy.ewma.volatile_alpha, 0.15)
        self.assertEqual(service.auto_policy.ewma.volatility_high_watts, 200.0)
        self.assertEqual(service.auto_policy.learn_charge_power.reference_power_watts, 1900.0)
        self.assertEqual(service.auto_policy.learn_charge_power.min_watts, 0.0)
        self.assertEqual(service.auto_policy.learn_charge_power.alpha, 0.2)
        self.assertEqual(service.auto_policy.learn_charge_power.start_delay_seconds, 0.0)
        self.assertEqual(service.auto_policy.learn_charge_power.window_seconds, 0.0)
        self.assertEqual(service.auto_policy.learn_charge_power.max_age_seconds, 0.0)
        self.assertEqual(len(service.auto_month_windows), len(MONTH_WINDOW_DEFAULTS))

    def test_load_runtime_configuration_populates_identity_sources_and_helper_settings(self):
        parser = configparser.ConfigParser()
        parser.optionxform = str
        parser.read_dict(
            {
                "DEFAULT": {
                    "DeviceInstance": "77",
                    "Host": "192.168.1.44",
                    "Phase": "L2",
                    "Position": "3",
                    "PollIntervalMs": "2500",
                    "SignOfLifeLog": "7",
                    "MaxCurrent": "20",
                    "MinCurrent": "5",
                    "ChargingThresholdWatts": "250",
                    "IdleStatus": "9",
                    "ThreePhaseVoltageMode": "line",
                    "Username": "user",
                    "Password": "secret",
                    "DigestAuth": "yes",
                    "ShellyComponent": "Relay",
                    "ShellyId": "4",
                    "Name": "Garage Wallbox",
                    "ServiceName": "com.example.ev",
                    "Connection": "Custom RPC",
                    "RuntimeStatePath": "/tmp/runtime.json",
                    "RuntimeOverridesPath": "/tmp/overrides.ini",
                    "DisplayLearnedSetCurrent": "off",
                    "ControlApiEnabled": "true",
                    "ControlApiHost": "0.0.0.0",
                    "ControlApiPort": "9876",
                    "ControlApiAuthToken": "token-123",
                    "ControlApiReadToken": "read-123",
                    "ControlApiControlToken": "control-123",
                    "ControlApiAdminToken": "admin-123",
                    "ControlApiUpdateToken": "update-123",
                    "ControlApiAuditPath": "/tmp/control-audit.jsonl",
                    "ControlApiAuditMaxEntries": "11",
                    "ControlApiIdempotencyPath": "/tmp/idempotency.json",
                    "ControlApiIdempotencyMaxEntries": "12",
                    "ControlApiRateLimitMaxRequests": "13",
                    "ControlApiRateLimitWindowSeconds": "14.5",
                    "ControlApiCriticalCooldownSeconds": "1.25",
                    "ControlApiLocalhostOnly": "0",
                    "ControlApiUnixSocketPath": "/tmp/control.sock",
                    "CompanionDbusBridgeEnabled": "1",
                    "CompanionBatteryServiceEnabled": "0",
                    "CompanionPvInverterServiceEnabled": "0",
                    "CompanionGridServiceEnabled": "1",
                    "CompanionGridAuthoritativeSource": "grid-meter",
                    "CompanionGridHoldSeconds": "6.5",
                    "CompanionGridSmoothingAlpha": "0.4",
                    "CompanionGridSmoothingMaxJumpWatts": "1200",
                    "CompanionSourceServicesEnabled": "0",
                    "CompanionSourceGridServicesEnabled": "1",
                    "CompanionSourceGridHoldSeconds": "7.5",
                    "CompanionSourceGridSmoothingAlpha": "0.3",
                    "CompanionSourceGridSmoothingMaxJumpWatts": "900",
                    "CompanionBatteryDeviceInstance": "201",
                    "CompanionPvInverterDeviceInstance": "202",
                    "CompanionGridDeviceInstance": "203",
                    "CompanionSourceBatteryDeviceInstanceBase": "301",
                    "CompanionSourcePvInverterDeviceInstanceBase": "401",
                    "CompanionSourceGridDeviceInstanceBase": "501",
                    "CompanionBatteryServiceName": "com.example.battery.external",
                    "CompanionPvInverterServiceName": "com.example.pvinverter.external",
                    "CompanionGridServiceName": "com.example.grid.external",
                    "CompanionSourceBatteryServicePrefix": "com.example.battery.source",
                    "CompanionSourcePvInverterServicePrefix": "com.example.pvinverter.source",
                    "CompanionSourceGridServicePrefix": "com.example.grid.source",
                    "AutoPvService": "com.example.pv",
                    "AutoPvServicePrefix": "com.example.pvprefix",
                    "AutoPvPath": "/Pv/Power",
                    "AutoPvMaxServices": "2",
                    "AutoPvScanIntervalSeconds": "12",
                    "AutoUseDcPv": "0",
                    "AutoDcPvService": "com.example.system",
                    "AutoDcPvPath": "/Dc/Pv",
                    "AutoBatteryService": "com.example.battery",
                    "AutoBatterySocPath": "/Battery/Soc",
                    "AutoBatteryServicePrefix": "com.example.battprefix",
                    "AutoBatteryScanIntervalSeconds": "25",
                    "AutoAllowWithoutBatterySoc": "false",
                    "AutoDbusBackoffBaseSeconds": "3",
                    "AutoDbusBackoffMaxSeconds": "9",
                    "AutoGridService": "com.example.grid",
                    "AutoGridL1Path": "/Grid/L1",
                    "AutoGridL2Path": "/Grid/L2",
                    "AutoGridL3Path": "/Grid/L3",
                    "AutoGridRequireAllPhases": "0",
                    "AutoGridMissingStopSeconds": "33",
                    "AutoGridRecoveryStartSeconds": "14",
                    "AutoInputSnapshotPath": "/tmp/auto.json",
                    "AutoPvPollIntervalMs": "2200",
                    "AutoGridPollIntervalMs": "3300",
                    "AutoBatteryPollIntervalMs": "4400",
                    "AutoInputValidationPollSeconds": "45",
                    "AutoInputHelperRestartSeconds": "8",
                    "AutoInputHelperStaleSeconds": "19",
                    "AutoShellySoftFailSeconds": "17",
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
            }
        )
        state = SimpleNamespace(load_config=MagicMock(return_value=parser), validate_runtime_config=MagicMock())
        service = SimpleNamespace(state=state)
        controller = self._controller(service)

        controller.load_runtime_configuration()

        self.assertEqual(service.deviceinstance, 77)
        self.assertEqual(service.host, "192.168.1.44")
        self.assertTrue(service.host_configured)
        self.assertEqual(service.phase, "L2")
        self.assertEqual(service.position, 3)
        self.assertEqual(service.poll_interval_ms, 2500)
        self.assertEqual(service.sign_of_life_minutes, 7)
        self.assertEqual(service.max_current, 20.0)
        self.assertEqual(service.min_current, 5.0)
        self.assertEqual(service.charging_threshold_watts, 250.0)
        self.assertEqual(service.idle_status, 9)
        self.assertEqual(service.voltage_mode, "line")
        self.assertEqual(service.username, "user")
        self.assertEqual(service.password, "secret")
        self.assertTrue(service.use_digest_auth)
        self.assertEqual(service.pm_component, "Relay")
        self.assertEqual(service.pm_id, 4)
        self.assertEqual(service.custom_name_override, "Garage Wallbox")
        self.assertEqual(service.service_name, "com.example.ev")
        self.assertEqual(service.connection_name, "Custom RPC")
        self.assertEqual(service.runtime_state_path, "/tmp/runtime.json")
        self.assertEqual(service.runtime_overrides_path, "/tmp/overrides.ini")
        self.assertFalse(service.display_learned_set_current)
        self.assertTrue(service.control_api_enabled)
        self.assertEqual(service.control_api_host, "0.0.0.0")
        self.assertEqual(service.control_api_port, 9876)
        self.assertEqual(service.control_api_auth_token, "token-123")
        self.assertEqual(service.control_api_read_token, "read-123")
        self.assertEqual(service.control_api_control_token, "control-123")
        self.assertEqual(service.control_api_admin_token, "admin-123")
        self.assertEqual(service.control_api_update_token, "update-123")
        self.assertEqual(service.control_api_audit_path, "/tmp/control-audit.jsonl")
        self.assertEqual(service.control_api_audit_max_entries, 11)
        self.assertEqual(service.control_api_idempotency_path, "/tmp/idempotency.json")
        self.assertEqual(service.control_api_idempotency_max_entries, 12)
        self.assertEqual(service.control_api_rate_limit_max_requests, 13)
        self.assertEqual(service.control_api_rate_limit_window_seconds, 14.5)
        self.assertEqual(service.control_api_critical_cooldown_seconds, 1.25)
        self.assertFalse(service.control_api_localhost_only)
        self.assertEqual(service.control_api_unix_socket_path, "/tmp/control.sock")
        self.assertEqual(service.control_api_listen_host, "")
        self.assertEqual(service.control_api_listen_port, 0)
        self.assertEqual(service.control_api_bound_unix_socket_path, "")
        self.assertTrue(service.companion_dbus_bridge_enabled)
        self.assertFalse(service.companion_battery_service_enabled)
        self.assertFalse(service.companion_pvinverter_service_enabled)
        self.assertTrue(service.companion_grid_service_enabled)
        self.assertEqual(service.companion_grid_authoritative_source, "grid-meter")
        self.assertEqual(service.companion_grid_hold_seconds, 6.5)
        self.assertEqual(service.companion_grid_smoothing_alpha, 0.4)
        self.assertEqual(service.companion_grid_smoothing_max_jump_watts, 1200.0)
        self.assertFalse(service.companion_source_services_enabled)
        self.assertTrue(service.companion_source_grid_services_enabled)
        self.assertEqual(service.companion_source_grid_hold_seconds, 7.5)
        self.assertEqual(service.companion_source_grid_smoothing_alpha, 0.3)
        self.assertEqual(service.companion_source_grid_smoothing_max_jump_watts, 900.0)
        self.assertEqual(service.companion_battery_deviceinstance, 201)
        self.assertEqual(service.companion_pvinverter_deviceinstance, 202)
        self.assertEqual(service.companion_grid_deviceinstance, 203)
        self.assertEqual(service.companion_source_battery_deviceinstance_base, 301)
        self.assertEqual(service.companion_source_pvinverter_deviceinstance_base, 401)
        self.assertEqual(service.companion_source_grid_deviceinstance_base, 501)
        self.assertEqual(service.companion_battery_service_name, "com.example.battery.external")
        self.assertEqual(service.companion_pvinverter_service_name, "com.example.pvinverter.external")
        self.assertEqual(service.companion_grid_service_name, "com.example.grid.external")
        self.assertEqual(service.companion_source_battery_service_prefix, "com.example.battery.source")
        self.assertEqual(service.companion_source_pvinverter_service_prefix, "com.example.pvinverter.source")
        self.assertEqual(service.companion_source_grid_service_prefix, "com.example.grid.source")
        self.assertFalse(hasattr(service, "_backend_selection"))
        self.assertFalse(hasattr(service, "backend_mode"))
        self.assertFalse(hasattr(service, "meter_backend_type"))
        self.assertFalse(hasattr(service, "switch_backend_type"))
        self.assertFalse(hasattr(service, "charger_backend_type"))
        self.assertFalse(hasattr(service, "meter_backend_config_path"))
        self.assertFalse(hasattr(service, "switch_backend_config_path"))
        self.assertFalse(hasattr(service, "charger_backend_config_path"))
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
        self.assertFalse(service.auto_allow_without_battery_soc)
        self.assertEqual(service.auto_dbus_backoff_base_seconds, 3.0)
        self.assertEqual(service.auto_dbus_backoff_max_seconds, 9.0)
        self.assertEqual(service.auto_grid_service, "com.example.grid")
        self.assertEqual(service.auto_grid_l1_path, "/Grid/L1")
        self.assertEqual(service.auto_grid_l2_path, "/Grid/L2")
        self.assertEqual(service.auto_grid_l3_path, "/Grid/L3")
        self.assertFalse(service.auto_grid_require_all_phases)
        self.assertEqual(service.auto_grid_missing_stop_seconds, 33.0)
        self.assertEqual(service.auto_policy.grid_recovery_start_seconds, 14.0)
        self.assertEqual(service.auto_input_snapshot_path, "/tmp/auto.json")
        self.assertEqual(service.auto_pv_poll_interval_seconds, 2.2)
        self.assertEqual(service.auto_grid_poll_interval_seconds, 3.3)
        self.assertEqual(service.auto_battery_poll_interval_seconds, 4.4)
        self.assertEqual(service.auto_input_validation_poll_seconds, 45.0)
        self.assertEqual(service.auto_input_helper_restart_seconds, 8.0)
        self.assertEqual(service.auto_input_helper_stale_seconds, 19.0)
        self.assertEqual(service.auto_shelly_soft_fail_seconds, 17.0)
        self.assertEqual(service.auto_watchdog_stale_seconds, 111.0)
        self.assertEqual(service.auto_watchdog_recovery_seconds, 22.0)
        self.assertEqual(service.auto_watchdog_restart_attempts, 6)
        self.assertEqual(service.auto_startup_warmup_seconds, 18.0)
        self.assertEqual(service.auto_manual_override_seconds, 333.0)
        self.assertEqual(service.startup_device_info_retries, 4)
        self.assertEqual(service.startup_device_info_retry_seconds, 1.5)
        self.assertEqual(service.shelly_request_timeout_seconds, 4.5)
        self.assertEqual(service.dbus_method_timeout_seconds, 2.5)
        state.validate_runtime_config.assert_called_once_with()

    def test_load_runtime_configuration_applies_identity_and_companion_defaults(self):
        parser = configparser.ConfigParser()
        parser.read_dict({"DEFAULT": {}})
        state = SimpleNamespace(load_config=MagicMock(return_value=parser), validate_runtime_config=MagicMock())
        service = SimpleNamespace(state=state)
        controller = self._controller(service)

        controller.load_runtime_configuration()

        self.assertEqual(service.deviceinstance, 60)
        self.assertEqual(service.host, "")
        self.assertFalse(service.host_configured)
        self.assertEqual(service.phase, "L1")
        self.assertEqual(service.position, 1)
        self.assertEqual(service.poll_interval_ms, 1000)
        self.assertEqual(service.sign_of_life_minutes, 10)
        self.assertEqual(service.max_current, 16.0)
        self.assertEqual(service.min_current, 6.0)
        self.assertTrue(service.display_learned_set_current)
        self.assertEqual(service.charging_threshold_watts, 100.0)
        self.assertEqual(service.idle_status, 6)
        self.assertEqual(service.voltage_mode, "phase")
        self.assertEqual(service.username, "")
        self.assertEqual(service.password, "")
        self.assertFalse(service.use_digest_auth)
        self.assertEqual(service.pm_component, "Switch")
        self.assertEqual(service.pm_id, 0)
        self.assertEqual(service.custom_name_override, "")
        self.assertEqual(service.service_name, "com.victronenergy.evcharger")
        self.assertEqual(service.connection_name, "Shelly 1PM Gen4 RPC")
        self.assertEqual(service.runtime_state_path, "/run/dbus-venus-evcharger-60.json")
        self.assertEqual(service.runtime_overrides_path, "/run/dbus-venus-evcharger-overrides-60.ini")
        self.assertFalse(service.control_api_enabled)
        self.assertEqual(service.control_api_host, "127.0.0.1")
        self.assertEqual(service.control_api_port, 8765)
        self.assertEqual(service.control_api_auth_token, "")
        self.assertEqual(service.control_api_read_token, "")
        self.assertEqual(service.control_api_control_token, "")
        self.assertEqual(service.control_api_admin_token, "")
        self.assertEqual(service.control_api_update_token, "")
        self.assertEqual(service.control_api_audit_path, "/run/dbus-venus-evcharger-control-audit-60.jsonl")
        self.assertEqual(service.control_api_audit_max_entries, 200)
        self.assertEqual(service.control_api_idempotency_path, "/run/dbus-venus-evcharger-idempotency-60.json")
        self.assertEqual(service.control_api_idempotency_max_entries, 200)
        self.assertEqual(service.control_api_rate_limit_max_requests, 30)
        self.assertEqual(service.control_api_rate_limit_window_seconds, 5.0)
        self.assertEqual(service.control_api_critical_cooldown_seconds, 2.0)
        self.assertTrue(service.control_api_localhost_only)
        self.assertEqual(service.control_api_unix_socket_path, "")
        self.assertEqual(service.control_api_listen_host, "")
        self.assertEqual(service.control_api_listen_port, 0)
        self.assertEqual(service.control_api_bound_unix_socket_path, "")
        self.assertFalse(service.companion_dbus_bridge_enabled)
        self.assertTrue(service.companion_battery_service_enabled)
        self.assertTrue(service.companion_pvinverter_service_enabled)
        self.assertFalse(service.companion_grid_service_enabled)
        self.assertEqual(service.companion_grid_authoritative_source, "")
        self.assertEqual(service.companion_grid_hold_seconds, 5.0)
        self.assertEqual(service.companion_grid_smoothing_alpha, 1.0)
        self.assertEqual(service.companion_grid_smoothing_max_jump_watts, 0.0)
        self.assertTrue(service.companion_source_services_enabled)
        self.assertFalse(service.companion_source_grid_services_enabled)
        self.assertEqual(service.companion_source_grid_hold_seconds, 5.0)
        self.assertEqual(service.companion_source_grid_smoothing_alpha, 1.0)
        self.assertEqual(service.companion_source_grid_smoothing_max_jump_watts, 0.0)
        self.assertEqual(service.companion_battery_deviceinstance, 100)
        self.assertEqual(service.companion_pvinverter_deviceinstance, 101)
        self.assertEqual(service.companion_grid_deviceinstance, 102)
        self.assertEqual(service.companion_source_battery_deviceinstance_base, 200)
        self.assertEqual(service.companion_source_pvinverter_deviceinstance_base, 300)
        self.assertEqual(service.companion_source_grid_deviceinstance_base, 400)
        self.assertEqual(service.companion_battery_service_name, "com.victronenergy.battery.external_100")
        self.assertEqual(service.companion_pvinverter_service_name, "com.victronenergy.pvinverter.external_101")
        self.assertEqual(service.companion_grid_service_name, "com.victronenergy.grid.external_102")
        self.assertEqual(service.companion_source_battery_service_prefix, "com.victronenergy.battery.external")
        self.assertEqual(service.companion_source_pvinverter_service_prefix, "com.victronenergy.pvinverter.external")
        self.assertEqual(service.companion_source_grid_service_prefix, "com.victronenergy.grid.external")
        state.validate_runtime_config.assert_called_once_with()

    def test_identity_config_keeps_runtime_override_fallback_and_loopback_for_blank_host(self):
        parser = configparser.ConfigParser()
        parser.read_dict({"DEFAULT": {"ControlApiHost": "   "}})
        state = SimpleNamespace(load_config=MagicMock(return_value=parser), validate_runtime_config=MagicMock())
        service = SimpleNamespace(
            state=state,
            runtime_overrides_path="/tmp/existing-overrides.ini",
        )
        controller = self._controller(service)

        controller.load_runtime_configuration()

        self.assertEqual(service.control_api_host, "127.0.0.1")
        self.assertEqual(service.runtime_overrides_path, "/tmp/existing-overrides.ini")

    def test_load_runtime_configuration_reads_backend_section_when_present(self):
        parser = configparser.ConfigParser()
        parser.read_dict(
            {
                "DEFAULT": {"Host": "192.168.1.20"},
                "Backends": {
                    "Mode": "split",
                    "MeterType": "shelly_combined",
                    "SwitchType": "shelly_combined",
                    "ChargerType": "",
                    "MeterConfigPath": "/data/meter.ini",
                    "SwitchConfigPath": "/data/switch.ini",
                    "ChargerConfigPath": "",
                },
            }
        )
        state = SimpleNamespace(load_config=MagicMock(return_value=parser), validate_runtime_config=MagicMock())
        service = SimpleNamespace(state=state)
        controller = self._controller(service)

        controller.load_runtime_configuration()

        self.assertEqual(service._backend_runtime_summary.backend_mode, "split")
        self.assertEqual(service._backend_runtime_summary.meter_type, "shelly_meter")
        self.assertEqual(service._backend_runtime_summary.switch_type, "shelly_contactor_switch")
        self.assertFalse(hasattr(service, "_backend_selection"))
        self.assertFalse(hasattr(service, "backend_mode"))
        self.assertFalse(hasattr(service, "meter_backend_type"))
        self.assertFalse(hasattr(service, "switch_backend_type"))
        self.assertFalse(hasattr(service, "charger_backend_type"))
        self.assertFalse(hasattr(service, "meter_backend_config_path"))
        self.assertFalse(hasattr(service, "switch_backend_config_path"))
        self.assertFalse(hasattr(service, "charger_backend_config_path"))

    def test_load_runtime_configuration_stores_topology_without_forcing_non_bridge_backend_selection(self):
        parser = configparser.ConfigParser()
        parser.read_dict(
            {
                "DEFAULT": {
                    "Host": "",
                    "Phase": "L1",
                },
                "Topology": {
                    "Type": "simple_relay",
                },
                "Actuator": {
                    "Type": "template_switch",
                    "ConfigPath": "/data/etc/wallbox-actuator.ini",
                },
                "Measurement": {
                    "Type": "fixed_reference",
                    "ReferenceWatts": "2300",
                },
            }
        )
        state = SimpleNamespace(load_config=MagicMock(return_value=parser), validate_runtime_config=MagicMock())
        service = SimpleNamespace(state=state)
        controller = self._controller(service)

        controller.load_runtime_configuration()

        self.assertEqual(service._topology_config.topology.type, "simple_relay")
        self.assertEqual(service._topology_config.actuator.type, "template_switch")
        self.assertEqual(service._topology_config.measurement.type, "fixed_reference")
        self.assertEqual(service._backend_runtime_summary.backend_mode, "split")
        self.assertTrue(service._backend_runtime_summary.topology_configured)
        self.assertFalse(hasattr(service, "_backend_selection"))
        self.assertFalse(hasattr(service, "backend_mode"))
        self.assertFalse(hasattr(service, "meter_backend_type"))
        self.assertFalse(hasattr(service, "switch_backend_type"))
        self.assertFalse(hasattr(service, "charger_backend_type"))

    def test_load_runtime_configuration_rejects_invalid_meterless_backend_combo_early(self):
        parser = configparser.ConfigParser()
        parser.read_dict(
            {
                "DEFAULT": {"Host": "192.168.1.20"},
                "Backends": {
                    "Mode": "split",
                    "MeterType": "none",
                    "SwitchType": "shelly_combined",
                    "ChargerType": "",
                },
            }
        )
        state = SimpleNamespace(load_config=MagicMock(return_value=parser), validate_runtime_config=MagicMock())
        service = SimpleNamespace(state=state)
        controller = self._controller(service)

        with self.assertRaisesRegex(ValueError, "MeterType=none requires a configured charger backend"):
            controller.load_runtime_configuration()

        state.validate_runtime_config.assert_not_called()

    def test_logging_level_and_seasonal_windows_helpers(self):
        parser = configparser.ConfigParser()
        parser.read_dict({"DEFAULT": {"Logging": "debug"}})

        windows = _seasonal_month_windows(parser, lambda *_args, **_kwargs: ((7, 0), (19, 0)))

        self.assertEqual(_logging_level_from_config(parser), "DEBUG")
        self.assertEqual(len(windows), len(MONTH_WINDOW_DEFAULTS))
        self.assertEqual(windows[1], ((7, 0), (19, 0)))

    def test_seasonal_windows_forward_exact_month_defaults_to_loader(self):
        parser = configparser.ConfigParser()
        calls = []

        def month_window_func(config, month, start, end):
            calls.append((config, month, start, end))
            return month, start, end

        windows = _seasonal_month_windows(parser, month_window_func)

        self.assertEqual(windows, {month: (month, start, end) for month, (start, end) in MONTH_WINDOW_DEFAULTS.items()})
        self.assertEqual(
            calls,
            [
                (parser, month, start, end)
                for month, (start, end) in MONTH_WINDOW_DEFAULTS.items()
            ],
        )

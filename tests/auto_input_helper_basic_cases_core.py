# SPDX-License-Identifier: GPL-3.0-or-later
from tests.auto_input_helper_basic_cases_common import (
    AutoInputHelper,
    ModuleType,
    _as_bool,
    os,
    patch,
    runpy,
    sys,
    tempfile,
    venus_evcharger_auto_input_helper,
)


class _AutoInputHelperBasicCoreCases:
    def _write_helper_config(self, body):
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as handle:
            handle.write("[DEFAULT]\n")
            handle.write(body)
            config_path = handle.name
        self.addCleanup(lambda: os.path.exists(config_path) and os.unlink(config_path))
        return config_path

    def test_as_bool_parses_truthy_and_defaults(self):
        self.assertTrue(_as_bool("yes"))
        self.assertFalse(_as_bool("0"))
        self.assertTrue(_as_bool(None, default=True))

    def test_init_raises_for_missing_or_invalid_config(self):
        temp_dir = tempfile.mkdtemp()
        self.addCleanup(lambda: os.path.isdir(temp_dir) and os.rmdir(temp_dir))
        config_path = os.path.join(temp_dir, "missing.ini")
        with self.assertRaises(ValueError):
            AutoInputHelper(config_path)

    def test_init_uses_dedicated_auto_input_poll_interval_when_configured(self):
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as handle:
            handle.write(
                "[DEFAULT]\n"
                "PollIntervalMs=1000\n"
                "AutoInputPollIntervalMs=2000\n"
                "AutoPvPollIntervalMs=2000\n"
                "AutoGridPollIntervalMs=2000\n"
                "AutoBatteryPollIntervalMs=10000\n"
                "AutoInputSnapshotPath=/tmp/helper.json\n"
            )
            config_path = handle.name

        self.addCleanup(lambda: os.path.exists(config_path) and os.unlink(config_path))
        helper = AutoInputHelper(config_path)
        self.assertEqual(helper.poll_interval_seconds, 2.0)
        self.assertEqual(helper.auto_pv_poll_interval_seconds, 2.0)
        self.assertEqual(helper.auto_grid_poll_interval_seconds, 2.0)
        self.assertEqual(helper.auto_battery_poll_interval_seconds, 10.0)

    def test_init_helper_config_applies_explicit_contract_values_and_clamps(self):
        config_path = self._write_helper_config(
            "AutoInputSnapshotPath=/tmp/from-config.json\n"
            "DbusIntrospectionSnapshotPath= /tmp/intro.json \n"
            "DbusIntrospectionRequestPath=/tmp/request.json\n"
            "DbusIntrospectionMaxAgeSeconds=-5\n"
            "DbusGatewayRunDir=/tmp/gateway\n"
            "DbusGatewayCachePath=/tmp/cache.json\n"
            "DbusGatewayMaxAgeSeconds=-2\n"
            "DbusGatewayErrorRetrySeconds=0\n"
            "DbusMethodTimeoutSeconds=2.5\n"
            "PollIntervalMs=1500\n"
            "AutoInputPollIntervalMs=100\n"
            "AutoPvPollIntervalMs=50\n"
            "AutoGridPollIntervalMs=800\n"
            "AutoBatteryPollIntervalMs=4000\n"
            "AutoPvService=custom.pv\n"
            "AutoPvServicePrefix=custom.pv.prefix\n"
            "AutoPvPath=/Pv/Custom\n"
            "AutoPvMaxServices=0\n"
            "AutoPvScanIntervalSeconds=-1\n"
            "AutoUseDcPv=0\n"
            "AutoDcPvService=custom.dc\n"
            "AutoDcPvPath=/Dc/Custom\n"
            "AutoBatteryService=custom.battery\n"
            "AutoBatterySocPath=/SocCustom\n"
            "AutoBatteryCapacityWh=12000\n"
            "AutoBatteryChemistry=NMC\n"
            "AutoBatteryCapacityAutoEstimate=0\n"
            "AutoBatteryCapacityWhPath=/CapacityWh\n"
            "AutoBatteryCapacityAhPath=/CapacityAh\n"
            "AutoBatteryVoltagePath=/Voltage\n"
            "AutoBatteryCapacityEstimateMinSoc=-4\n"
            "AutoBatteryCapacityStartupRecheckSeconds=-7\n"
            "AutoBatteryCapacityEstimatedWh=111\n"
            "AutoBatteryCapacityEstimatedAh=222\n"
            "AutoBatteryCapacityEstimatedNominalVoltage=48.5\n"
            "AutoBatteryCapacityEstimatedCellCount=16\n"
            "AutoBatteryPowerPath=/Power\n"
            "AutoBatteryAcPowerPath=/AcPower\n"
            "AutoBatteryPvPowerPath=/PvPower\n"
            "AutoBatteryGridInteractionPath=/GridInteraction\n"
            "AutoBatteryOperatingModePath=/OperatingMode\n"
            "AutoBatteryServicePrefix=custom.battery.prefix\n"
            "AutoBatteryScanIntervalSeconds=-8\n"
            "AutoGridService=custom.system\n"
            "AutoGridL1Path=/L1\n"
            "AutoGridL2Path=/L2\n"
            "AutoGridL3Path=/L3\n"
            "AutoGridRequireAllPhases=0\n"
            "AutoDbusBackoffBaseSeconds=-1\n"
            "AutoDbusBackoffMaxSeconds=-2\n"
            "AutoInputValidationPollSeconds=1\n"
        )

        helper = AutoInputHelper(
            config_path,
            snapshot_path="/tmp/override.json",
            parent_pid="1234",
            helper_generation="7",
            runtime_instance_id=" runtime-1 ",
        )

        self.assertEqual(helper.config_path, config_path)
        self.assertEqual(helper.parent_pid, 1234)
        self.assertEqual(helper.helper_generation, 7)
        self.assertEqual(helper.runtime_instance_id, "runtime-1")
        self.assertEqual(helper.snapshot_path, "/tmp/override.json")
        self.assertEqual(helper.dbus_introspection_snapshot_path, "/tmp/intro.json")
        self.assertEqual(helper.dbus_introspection_request_path, "/tmp/request.json")
        self.assertEqual(helper.dbus_introspection_max_age_seconds, 0.0)
        self.assertEqual(helper.dbus_gateway_run_dir, "/tmp/gateway")
        self.assertEqual(helper.dbus_gateway_cache_path, "/tmp/cache.json")
        self.assertEqual(helper.dbus_gateway_max_age_seconds, 0.0)
        self.assertEqual(helper.dbus_gateway_error_retry_seconds, 1.0)
        self.assertEqual(helper.dbus_method_timeout_seconds, 2.5)
        self.assertEqual(helper.auto_pv_poll_interval_seconds, 0.2)
        self.assertEqual(helper.auto_grid_poll_interval_seconds, 0.8)
        self.assertEqual(helper.auto_battery_poll_interval_seconds, 4.0)
        self.assertEqual(helper.poll_interval_seconds, 0.2)
        self.assertEqual(helper.auto_pv_service, "custom.pv")
        self.assertEqual(helper.auto_pv_service_prefix, "custom.pv.prefix")
        self.assertEqual(helper.auto_pv_path, "/Pv/Custom")
        self.assertEqual(helper.auto_pv_max_services, 1)
        self.assertEqual(helper.auto_pv_scan_interval_seconds, 0.0)
        self.assertFalse(helper.auto_use_dc_pv)
        self.assertEqual(helper.auto_dc_pv_service, "custom.dc")
        self.assertEqual(helper.auto_dc_pv_path, "/Dc/Custom")
        self.assertEqual(helper.auto_battery_service, "custom.battery")
        self.assertEqual(helper.auto_battery_soc_path, "/SocCustom")
        self.assertEqual(helper.auto_battery_capacity_wh, 12000.0)
        self.assertEqual(helper.auto_battery_chemistry, "nmc")
        self.assertFalse(helper.auto_battery_capacity_auto_estimate)
        self.assertEqual(helper.auto_battery_capacity_wh_path, "/CapacityWh")
        self.assertEqual(helper.auto_battery_capacity_ah_path, "/CapacityAh")
        self.assertEqual(helper.auto_battery_voltage_path, "/Voltage")
        self.assertEqual(helper.auto_battery_capacity_estimate_min_soc, 0.0)
        self.assertEqual(helper.auto_battery_capacity_startup_recheck_seconds, 0.0)
        self.assertEqual(helper.auto_battery_capacity_estimated_wh, 111.0)
        self.assertEqual(helper.auto_battery_capacity_estimated_ah, 222.0)
        self.assertEqual(helper.auto_battery_capacity_estimated_nominal_voltage, 48.5)
        self.assertEqual(helper.auto_battery_capacity_estimated_cell_count, 16)
        self.assertEqual(helper.auto_battery_power_path, "/Power")
        self.assertEqual(helper.auto_battery_ac_power_path, "/AcPower")
        self.assertEqual(helper.auto_battery_pv_power_path, "/PvPower")
        self.assertEqual(helper.auto_battery_grid_interaction_path, "/GridInteraction")
        self.assertEqual(helper.auto_battery_operating_mode_path, "/OperatingMode")
        self.assertEqual(helper.auto_battery_service_prefix, "custom.battery.prefix")
        self.assertEqual(helper.auto_battery_scan_interval_seconds, 0.0)
        self.assertEqual(helper.auto_grid_service, "custom.system")
        self.assertEqual(helper.auto_grid_l1_path, "/L1")
        self.assertEqual(helper.auto_grid_l2_path, "/L2")
        self.assertEqual(helper.auto_grid_l3_path, "/L3")
        self.assertFalse(helper.auto_grid_require_all_phases)
        self.assertEqual(helper.auto_dbus_backoff_base_seconds, 0.0)
        self.assertEqual(helper.auto_dbus_backoff_max_seconds, 0.0)
        self.assertEqual(helper.validation_poll_seconds, 5.0)
        self.assertEqual(helper.subscription_refresh_seconds, 60.0)
        self.assertEqual(helper.auto_energy_source_ids, ("primary_battery",))
        self.assertTrue(helper.auto_use_combined_battery_soc)
        self.assertEqual(helper._next_source_poll_at, {"pv": 0.0, "battery": 0.0, "grid": 0.0})

    def test_config_initializers_use_exact_contract_keys_with_case_sensitive_mapping(self):
        section = {
            "AutoInputSnapshotPath": "/tmp/direct-snapshot.json",
            "DbusIntrospectionSnapshotPath": " /tmp/direct-intro.json ",
            "DbusIntrospectionRequestPath": "/tmp/direct-request.json",
            "DbusIntrospectionMaxAgeSeconds": "-5",
            "DbusGatewayRunDir": "/tmp/direct-gateway",
            "DbusGatewayCachePath": "/tmp/direct-cache.json",
            "DbusGatewayMaxAgeSeconds": "-2",
            "DbusGatewayErrorRetrySeconds": "0",
            "DbusMethodTimeoutSeconds": "2.5",
            "PollIntervalMs": "1500",
            "AutoInputPollIntervalMs": "100",
            "AutoPvPollIntervalMs": "50",
            "AutoGridPollIntervalMs": "800",
            "AutoBatteryPollIntervalMs": "4000",
            "AutoPvService": "direct.pv",
            "AutoPvServicePrefix": "direct.pv.prefix",
            "AutoPvPath": "/DirectPv/Power",
            "AutoPvMaxServices": "0",
            "AutoPvScanIntervalSeconds": "-1",
            "AutoUseDcPv": "0",
            "AutoDcPvService": "direct.dc",
            "AutoDcPvPath": "/DirectDc/Pv",
            "AutoBatteryService": "direct.battery",
            "AutoBatterySocPath": "/DirectSoc",
            "AutoBatteryCapacityWh": "12000",
            "AutoBatteryChemistry": "NMC",
            "AutoBatteryCapacityAutoEstimate": "0",
            "AutoBatteryCapacityWhPath": "/DirectCapacityWh",
            "AutoBatteryCapacityAhPath": "/DirectCapacityAh",
            "AutoBatteryVoltagePath": "/DirectVoltage",
            "AutoBatteryCapacityEstimateMinSoc": "-4",
            "AutoBatteryCapacityStartupRecheckSeconds": "-7",
            "AutoBatteryCapacityEstimatedWh": "111",
            "AutoBatteryCapacityEstimatedAh": "222",
            "AutoBatteryCapacityEstimatedNominalVoltage": "48.5",
            "AutoBatteryCapacityEstimatedCellCount": "16",
            "AutoBatteryPowerPath": "/DirectPower",
            "AutoBatteryAcPowerPath": "/DirectAcPower",
            "AutoBatteryPvPowerPath": "/DirectPvPower",
            "AutoBatteryGridInteractionPath": "/DirectGridInteraction",
            "AutoBatteryOperatingModePath": "/DirectOperatingMode",
            "AutoBatteryServicePrefix": "direct.battery.prefix",
            "AutoBatteryScanIntervalSeconds": "-8",
            "AutoGridService": "direct.system",
            "AutoGridL1Path": "/DirectL1",
            "AutoGridL2Path": "/DirectL2",
            "AutoGridL3Path": "/DirectL3",
            "AutoGridRequireAllPhases": "0",
            "AutoGridFusionEnabled": "1",
            "AutoGridFusionPrimarySource": "huawei",
            "AutoGridFusionBackupSource": "victron-grid",
            "AutoGridFusionPrimaryMaxAgeSeconds": "4",
            "AutoGridFusionBackupMaxAgeSeconds": "5",
            "AutoGridFusionMinimumConfidence": "0.7",
            "AutoGridFusionFailoverSamples": "2",
            "AutoGridFusionRecoverySamples": "8",
            "AutoGridFusionFailoverHoldSeconds": "3",
            "AutoGridFusionMismatchAbsoluteWatts": "250",
            "AutoGridFusionMismatchRelative": "0.2",
            "AutoGridFusionMismatchSamples": "4",
            "AutoGridFusionFutureToleranceSeconds": "0.5",
            "AutoDbusBackoffBaseSeconds": "-1",
            "AutoDbusBackoffMaxSeconds": "-2",
            "AutoInputValidationPollSeconds": "1",
        }

        class Parser:
            def __getitem__(self, key):
                if key != "DEFAULT":
                    raise KeyError(key)
                return section

        helper = AutoInputHelper.__new__(AutoInputHelper)
        helper._init_helper_base_config(
            "direct.ini",
            Parser(),
            None,
            parent_pid="4321",
            helper_generation="9",
            runtime_instance_id=" direct-runtime ",
        )
        helper._init_helper_polling()
        helper._init_helper_pv_config()
        helper._init_helper_battery_config()
        helper._init_helper_grid_config()
        helper._init_helper_runtime_config()

        expected = {
            "config_path": "direct.ini",
            "parent_pid": 4321,
            "helper_generation": 9,
            "runtime_instance_id": "direct-runtime",
            "snapshot_path": "/tmp/direct-snapshot.json",
            "dbus_introspection_snapshot_path": "/tmp/direct-intro.json",
            "dbus_introspection_request_path": "/tmp/direct-request.json",
            "dbus_introspection_max_age_seconds": 0.0,
            "dbus_gateway_run_dir": "/tmp/direct-gateway",
            "dbus_gateway_cache_path": "/tmp/direct-cache.json",
            "dbus_gateway_max_age_seconds": 0.0,
            "dbus_gateway_error_retry_seconds": 1.0,
            "dbus_method_timeout_seconds": 2.5,
            "auto_pv_poll_interval_seconds": 0.2,
            "auto_grid_poll_interval_seconds": 0.8,
            "auto_battery_poll_interval_seconds": 4.0,
            "poll_interval_seconds": 0.2,
            "auto_pv_service": "direct.pv",
            "auto_pv_service_prefix": "direct.pv.prefix",
            "auto_pv_path": "/DirectPv/Power",
            "auto_pv_max_services": 1,
            "auto_pv_scan_interval_seconds": 0.0,
            "auto_dc_pv_service": "direct.dc",
            "auto_dc_pv_path": "/DirectDc/Pv",
            "auto_battery_service": "direct.battery",
            "auto_battery_soc_path": "/DirectSoc",
            "auto_battery_capacity_wh": 12000.0,
            "auto_battery_chemistry": "nmc",
            "auto_battery_capacity_wh_path": "/DirectCapacityWh",
            "auto_battery_capacity_ah_path": "/DirectCapacityAh",
            "auto_battery_voltage_path": "/DirectVoltage",
            "auto_battery_capacity_estimate_min_soc": 0.0,
            "auto_battery_capacity_startup_recheck_seconds": 0.0,
            "auto_battery_capacity_estimated_wh": 111.0,
            "auto_battery_capacity_estimated_ah": 222.0,
            "auto_battery_capacity_estimated_nominal_voltage": 48.5,
            "auto_battery_capacity_estimated_cell_count": 16,
            "auto_battery_power_path": "/DirectPower",
            "auto_battery_ac_power_path": "/DirectAcPower",
            "auto_battery_pv_power_path": "/DirectPvPower",
            "auto_battery_grid_interaction_path": "/DirectGridInteraction",
            "auto_battery_operating_mode_path": "/DirectOperatingMode",
            "auto_battery_service_prefix": "direct.battery.prefix",
            "auto_battery_scan_interval_seconds": 0.0,
            "auto_grid_service": "direct.system",
            "auto_grid_l1_path": "/DirectL1",
            "auto_grid_l2_path": "/DirectL2",
            "auto_grid_l3_path": "/DirectL3",
            "auto_dbus_backoff_base_seconds": 0.0,
            "auto_dbus_backoff_max_seconds": 0.0,
            "validation_poll_seconds": 5.0,
            "subscription_refresh_seconds": 60.0,
            "auto_energy_source_ids": ("primary_battery",),
        }
        for name, value in expected.items():
            self.assertEqual(getattr(helper, name), value)
        self.assertFalse(helper.auto_use_dc_pv)
        self.assertFalse(helper.auto_battery_capacity_auto_estimate)
        self.assertFalse(helper.auto_grid_require_all_phases)
        self.assertTrue(helper.auto_use_combined_battery_soc)
        self.assertTrue(helper._grid_measurement_fusion.config.enabled)
        self.assertEqual(helper._grid_measurement_fusion.config.primary_source_id, "huawei")
        self.assertEqual(helper._grid_measurement_fusion.config.backup_source_id, "victron-grid")
        self.assertEqual(helper._grid_measurement_fusion.config.primary_max_age_seconds, 4.0)
        self.assertEqual(helper._grid_measurement_fusion.config.backup_max_age_seconds, 5.0)
        self.assertEqual(helper._grid_measurement_fusion.config.minimum_confidence, 0.7)
        self.assertEqual(helper._grid_measurement_fusion.config.failover_samples, 2)
        self.assertEqual(helper._grid_measurement_fusion.config.recovery_samples, 8)
        self.assertEqual(helper._grid_measurement_fusion.config.failover_hold_seconds, 3.0)
        self.assertEqual(helper._grid_measurement_fusion.config.mismatch_absolute_watts, 250.0)
        self.assertEqual(helper._grid_measurement_fusion.config.mismatch_relative, 0.2)
        self.assertEqual(helper._grid_measurement_fusion.config.mismatch_samples, 4)
        self.assertEqual(helper._grid_measurement_fusion.config.future_tolerance_seconds, 0.5)

    def test_init_helper_config_uses_documented_defaults(self):
        config_path = self._write_helper_config("")

        helper = AutoInputHelper(config_path)

        self.assertEqual(helper.snapshot_path, "/run/dbus-venus-evcharger-auto.json")
        self.assertEqual(helper.dbus_introspection_snapshot_path, "")
        self.assertEqual(helper.dbus_introspection_request_path, "")
        self.assertEqual(helper.dbus_introspection_max_age_seconds, 900.0)
        self.assertEqual(helper.dbus_gateway_run_dir, "/run/venus-evcharger")
        self.assertEqual(helper.dbus_gateway_cache_path, "/run/venus-evcharger/dbus-cache.json")
        self.assertEqual(helper.dbus_gateway_max_age_seconds, 10.0)
        self.assertEqual(helper.dbus_gateway_error_retry_seconds, 30.0)
        self.assertEqual(helper.dbus_method_timeout_seconds, 1.0)
        self.assertEqual(helper.auto_pv_service, "")
        self.assertEqual(helper.auto_pv_service_prefix, "com.victronenergy.pvinverter")
        self.assertEqual(helper.auto_pv_path, "/Ac/Power")
        self.assertEqual(helper.auto_pv_max_services, 10)
        self.assertEqual(helper.auto_pv_scan_interval_seconds, 60.0)
        self.assertTrue(helper.auto_use_dc_pv)
        self.assertEqual(helper.auto_dc_pv_service, "com.victronenergy.system")
        self.assertEqual(helper.auto_dc_pv_path, "/Dc/Pv/Power")
        self.assertEqual(helper.auto_battery_service, "com.victronenergy.battery.socketcan_can1")
        self.assertEqual(helper.auto_battery_soc_path, "/Soc")
        self.assertEqual(helper.auto_battery_capacity_wh, 0.0)
        self.assertEqual(helper.auto_battery_chemistry, "lfp")
        self.assertTrue(helper.auto_battery_capacity_auto_estimate)
        self.assertEqual(helper.auto_battery_capacity_wh_path, "")
        self.assertEqual(helper.auto_battery_capacity_ah_path, "/InstalledCapacity")
        self.assertEqual(helper.auto_battery_voltage_path, "/Dc/0/Voltage")
        self.assertEqual(helper.auto_battery_capacity_estimate_min_soc, 95.0)
        self.assertEqual(helper.auto_battery_capacity_startup_recheck_seconds, 300.0)
        self.assertEqual(helper.auto_battery_capacity_estimated_wh, 0.0)
        self.assertEqual(helper.auto_battery_capacity_estimated_ah, 0.0)
        self.assertEqual(helper.auto_battery_capacity_estimated_nominal_voltage, 0.0)
        self.assertEqual(helper.auto_battery_capacity_estimated_cell_count, 0)
        self.assertEqual(helper.auto_battery_power_path, "")
        self.assertEqual(helper.auto_battery_ac_power_path, "")
        self.assertEqual(helper.auto_battery_pv_power_path, "")
        self.assertEqual(helper.auto_battery_grid_interaction_path, "")
        self.assertEqual(helper.auto_battery_operating_mode_path, "")
        self.assertEqual(helper.auto_battery_service_prefix, "com.victronenergy.battery")
        self.assertEqual(helper.auto_battery_scan_interval_seconds, 60.0)
        self.assertEqual(helper.auto_grid_service, "com.victronenergy.system")
        self.assertEqual(helper.auto_grid_l1_path, "/Ac/Grid/L1/Power")
        self.assertEqual(helper.auto_grid_l2_path, "/Ac/Grid/L2/Power")
        self.assertEqual(helper.auto_grid_l3_path, "/Ac/Grid/L3/Power")
        self.assertTrue(helper.auto_grid_require_all_phases)
        self.assertFalse(helper._grid_measurement_fusion.config.enabled)
        self.assertEqual(helper._grid_measurement_fusion.config.backup_source_id, "victron")
        self.assertEqual(helper._grid_measurement_fusion.config.primary_max_age_seconds, 15.0)
        self.assertEqual(helper.auto_dbus_backoff_base_seconds, 5.0)
        self.assertEqual(helper.auto_dbus_backoff_max_seconds, 60.0)
        self.assertEqual(helper.validation_poll_seconds, 30.0)
        self.assertEqual(helper.subscription_refresh_seconds, 60.0)

    def test_grid_fusion_rejects_freshness_shorter_than_primary_poll_cycle(self):
        helper = AutoInputHelper.__new__(AutoInputHelper)
        helper.config = {
            "AutoGridFusionEnabled": "1",
            "AutoGridFusionPrimarySource": "huawei",
            "AutoGridFusionPrimaryMaxAgeSeconds": "9",
        }
        helper.auto_battery_poll_interval_seconds = 10.0

        with self.assertRaisesRegex(
            ValueError,
            "^AutoGridFusionPrimaryMaxAgeSeconds must cover AutoBatteryPollIntervalMs$",
        ):
            helper._init_helper_grid_config()

    def test_base_config_clamps_gateway_retry_ceiling(self):
        config_path = self._write_helper_config("DbusGatewayErrorRetrySeconds=999\n")

        helper = AutoInputHelper(config_path)

        self.assertEqual(helper.dbus_gateway_error_retry_seconds, 300.0)

    def test_poll_interval_helpers_cover_fallback_and_each_minimum_source(self):
        helper = AutoInputHelper.__new__(AutoInputHelper)

        helper.config = {"PollIntervalMs": "1750"}
        self.assertEqual(helper._auto_input_poll_interval_ms(), 1750.0)
        helper.config = {}
        self.assertEqual(helper._auto_input_poll_interval_ms(), 1000.0)

        cases = [
            (
                {
                    "AutoInputPollIntervalMs": "50",
                    "AutoPvPollIntervalMs": "600",
                    "AutoGridPollIntervalMs": "700",
                    "AutoBatteryPollIntervalMs": "800",
                },
                0.2,
            ),
            (
                {
                    "AutoInputPollIntervalMs": "1000",
                    "AutoPvPollIntervalMs": "300",
                    "AutoGridPollIntervalMs": "700",
                    "AutoBatteryPollIntervalMs": "800",
                },
                0.3,
            ),
            (
                {
                    "AutoInputPollIntervalMs": "1000",
                    "AutoPvPollIntervalMs": "700",
                    "AutoGridPollIntervalMs": "300",
                    "AutoBatteryPollIntervalMs": "800",
                },
                0.3,
            ),
            (
                {
                    "AutoInputPollIntervalMs": "1000",
                    "AutoPvPollIntervalMs": "700",
                    "AutoGridPollIntervalMs": "800",
                    "AutoBatteryPollIntervalMs": "300",
                },
                0.3,
            ),
        ]
        for config, expected in cases:
            with self.subTest(config=config):
                helper.config = config
                helper._init_helper_polling()
                self.assertEqual(helper.poll_interval_seconds, expected)

    def test_derive_subscription_refresh_seconds_uses_smallest_positive_scan_interval(self):
        helper = AutoInputHelper.__new__(AutoInputHelper)
        helper.config = {"AutoPvScanIntervalSeconds": "45", "AutoBatteryScanIntervalSeconds": "15"}
        self.assertEqual(helper._derive_subscription_refresh_seconds(), 15.0)

    def test_derive_subscription_refresh_seconds_ignores_non_positive_candidates(self):
        helper = AutoInputHelper.__new__(AutoInputHelper)
        helper.config = {"AutoPvScanIntervalSeconds": "0", "AutoBatteryScanIntervalSeconds": "-5"}
        self.assertEqual(helper._derive_subscription_refresh_seconds(), 60.0)

    def test_handle_signal_sets_stop_flag_and_requests_idle_quit(self):
        helper = self._make_helper()
        helper._main_loop = __import__("unittest.mock").mock.MagicMock()

        with patch("venus_evcharger_auto_input_helper.GLib.idle_add") as idle_add:
            helper._handle_signal(15, None)

        self.assertTrue(helper._stop_requested)
        idle_add.assert_called_once_with(helper._main_loop.quit)

    def test_handle_signal_sets_stop_flag_without_idle_quit_when_main_loop_is_missing(self):
        helper = self._make_helper()
        helper._main_loop = None

        with patch("venus_evcharger_auto_input_helper.GLib.idle_add") as idle_add:
            helper._handle_signal(15, None)

        self.assertTrue(helper._stop_requested)
        idle_add.assert_not_called()

    def test_warning_throttled_logs_once_per_interval(self):
        helper = self._make_helper()

        with patch("venus_evcharger_auto_input_helper.time.time", side_effect=[100.0, 105.0, 131.0]):
            with patch("venus_evcharger_auto_input_helper.logging.warning") as warning_mock:
                helper._warning_throttled("dbus", 30.0, "failed %s", "x")
                helper._warning_throttled("dbus", 30.0, "failed %s", "x")
                helper._warning_throttled("dbus", 30.0, "failed %s", "x")

        self.assertEqual(warning_mock.call_count, 2)

    def test_parent_alive_uses_parent_pid_and_handles_errors(self):
        helper = self._make_helper()
        helper.parent_pid = 1234

        helper.parent_pid = None
        self.assertTrue(helper._parent_alive())
        helper.parent_pid = 1234

        with __import__("unittest.mock").mock.patch("venus_evcharger_auto_input_helper.os.getppid", return_value=1234):
            self.assertTrue(helper._parent_alive())
        with __import__("unittest.mock").mock.patch("venus_evcharger_auto_input_helper.os.getppid", return_value=9999):
            self.assertFalse(helper._parent_alive())
        with __import__("unittest.mock").mock.patch("venus_evcharger_auto_input_helper.os.getppid", side_effect=RuntimeError("boom")):
            self.assertFalse(helper._parent_alive())

    def test_helper_module_import_fallback_sets_dbus_glib_mainloop_to_none(self):
        helper_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "venus_evcharger_auto_input_helper.py")
        fake_dbus = ModuleType("dbus")
        fake_dbus_mainloop = ModuleType("dbus.mainloop")
        fake_dbus_glib_mainloop = ModuleType("dbus.mainloop.glib")
        fake_glib = __import__("unittest.mock").mock.MagicMock()
        fake_gi = ModuleType("gi")
        fake_repository = ModuleType("gi.repository")
        fake_repository.GLib = fake_glib
        fake_gi.repository = fake_repository

        with patch.dict(
            sys.modules,
            {
                "dbus": fake_dbus,
                "dbus.mainloop": fake_dbus_mainloop,
                "dbus.mainloop.glib": fake_dbus_glib_mainloop,
                "gi": fake_gi,
                "gi.repository": fake_repository,
                "gi.repository.GLib": fake_glib,
            },
            clear=False,
        ):
            module_globals = runpy.run_path(helper_path, run_name="venus_evcharger_auto_input_helper_import_test")

        self.assertNotIn("dbus_glib_mainloop", module_globals)

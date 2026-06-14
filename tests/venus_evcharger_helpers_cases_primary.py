# SPDX-License-Identifier: GPL-3.0-or-later
from tests.venus_evcharger_helpers_support import *
from venus_evcharger.dbus_gateway import DbusCacheStore, gateway_paths


class TestShellyWallboxHelpersPrimary(ShellyWallboxHelpersTestBase):
    def _seed_gateway_services(self, service, names):
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        paths = gateway_paths(f"{temp_dir.name}/run")
        store = DbusCacheStore(paths)
        store.update_services(names)
        store.write_snapshot_files()
        service.dbus_gateway_run_dir = paths.run_dir
        service.dbus_gateway_cache_path = paths.cache_path
        service._mark_recovery = MagicMock()
        service._mark_failure = MagicMock()
        service._dbus_input_controller = None

    def test_normalize_phase_accepts_1p_alias(self):
        self.assertEqual(normalize_phase("1P"), "L1")

    def test_normalize_mode_preserves_supported_values(self):
        self.assertEqual(normalize_mode(2), 2)
        self.assertEqual(normalize_mode("2"), 2)
        self.assertEqual(normalize_mode(1), 1)
        self.assertEqual(normalize_mode(0), 0)
        self.assertEqual(normalize_mode("bad"), 0)

    def test_mode_uses_auto_logic_accepts_auto_and_scheduled(self):
        self.assertTrue(mode_uses_auto_logic(1))
        self.assertTrue(mode_uses_auto_logic(2))
        self.assertFalse(mode_uses_auto_logic(0))

    def test_parse_hhmm(self):
        self.assertEqual(parse_hhmm("07:30", (8, 0)), (7, 30))
        self.assertEqual(parse_hhmm("bad", (8, 0)), (8, 0))

    def test_month_in_ranges(self):
        self.assertTrue(month_in_ranges(1, ((12, 2),)))
        self.assertTrue(month_in_ranges(7, ((6, 8),)))
        self.assertFalse(month_in_ranges(11, ((3, 5),)))

    def test_month_window(self):
        config = {"DEFAULT": {"AutoAprStart": "07:45", "AutoAprEnd": "19:15"}}
        self.assertEqual(month_window(config, 4, "07:30", "19:30"), ((7, 45), (19, 15)))

    def test_validate_runtime_config_clamps_invalid_values(self):
        service = ShellyWallboxService.__new__(ShellyWallboxService)
        service.poll_interval_ms = 0
        service.sign_of_life_minutes = 0
        service.auto_pv_max_services = 0
        service.auto_pv_scan_interval_seconds = -1
        service.auto_battery_scan_interval_seconds = -1
        service.auto_dbus_backoff_base_seconds = -1
        service.auto_dbus_backoff_max_seconds = -1
        service.auto_average_window_seconds = -1
        service.auto_min_runtime_seconds = -1
        service.auto_min_offtime_seconds = -1
        service.auto_start_delay_seconds = -1
        service.auto_stop_delay_seconds = -1
        service.auto_input_cache_seconds = -1
        service.auto_input_helper_restart_seconds = -1
        service.auto_input_helper_stale_seconds = -1
        service.auto_shelly_soft_fail_seconds = -1
        service.auto_watchdog_stale_seconds = -1
        service.auto_watchdog_recovery_seconds = -1
        service.auto_startup_warmup_seconds = -1
        service.auto_scheduled_night_start_delay_seconds = -1
        service.auto_manual_override_seconds = -1
        service.startup_device_info_retry_seconds = -1
        service.startup_device_info_retries = -1
        service.shelly_request_timeout_seconds = -1
        service.dbus_method_timeout_seconds = -1
        service.auto_min_soc = 120
        service.auto_resume_soc = -5
        service.auto_start_surplus_watts = 1500
        service.auto_stop_surplus_watts = 2400

        service._validate_runtime_config()

        self.assertEqual(service.poll_interval_ms, 100)
        self.assertEqual(service.sign_of_life_minutes, 1)
        self.assertEqual(service.auto_pv_max_services, 1)
        self.assertEqual(service.auto_pv_scan_interval_seconds, 0.0)
        self.assertEqual(service.auto_input_cache_seconds, 0.0)
        self.assertEqual(service.auto_input_helper_restart_seconds, 0.0)
        self.assertEqual(service.auto_input_helper_stale_seconds, 0.0)
        self.assertEqual(service.auto_shelly_soft_fail_seconds, 0.0)
        self.assertEqual(service.auto_watchdog_stale_seconds, 0.0)
        self.assertEqual(service.auto_watchdog_recovery_seconds, 0.0)
        self.assertEqual(service.auto_startup_warmup_seconds, 0.0)
        self.assertEqual(service.auto_scheduled_night_start_delay_seconds, 0.0)
        self.assertEqual(service.auto_manual_override_seconds, 0.0)
        self.assertEqual(service.startup_device_info_retry_seconds, 0.0)
        self.assertEqual(service.startup_device_info_retries, 0)
        self.assertEqual(service.shelly_request_timeout_seconds, 2.0)
        self.assertEqual(service.dbus_method_timeout_seconds, 1.0)
        self.assertEqual(service.auto_min_soc, 100.0)
        self.assertEqual(service.auto_resume_soc, 100.0)
        self.assertEqual(service.auto_stop_surplus_watts, 1500)

    def test_available_surplus_uses_only_pv_backed_export(self):
        self.assertEqual(ShellyWallboxService._get_available_surplus_watts(2500, -1800), 1800)
        self.assertEqual(ShellyWallboxService._get_available_surplus_watts(0, -1800), 0)

    def test_get_pv_power_skips_failed_services_and_dc(self):
        service = ShellyWallboxService.__new__(ShellyWallboxService)
        service.auto_pv_path = "/Ac/Power"
        service.auto_use_dc_pv = True
        service.auto_dc_pv_service = "com.victronenergy.system"
        service.auto_dc_pv_path = "/Dc/Pv/Power"
        service._resolve_auto_pv_services = MagicMock(return_value=["pv1", "pv2"])

        def fake_get_value(service_name, path):
            if service_name == "pv1":
                return 1000
            if service_name == "pv2":
                raise ValueError("gone")
            if service_name == "com.victronenergy.system":
                raise ValueError("no dc pv")
            return None

        service._get_dbus_value = MagicMock(side_effect=fake_get_value)

        self.assertEqual(service._get_pv_power(), 1000)

    def test_get_pv_power_uses_dc_only_when_ac_missing(self):
        service = ShellyWallboxService.__new__(ShellyWallboxService)
        service.auto_pv_path = "/Ac/Power"
        service.auto_pv_service = ""
        service.auto_use_dc_pv = True
        service.auto_dc_pv_service = "com.victronenergy.system"
        service.auto_dc_pv_path = "/Dc/Pv/Power"
        service._resolve_auto_pv_services = MagicMock(side_effect=ValueError("no ac pv"))
        service.auto_pv_scan_interval_seconds = 60
        service._last_pv_missing_warning = None

        def fake_get_value(service_name, path):
            if service_name == "com.victronenergy.system":
                return 750
            return None

        service._get_dbus_value = MagicMock(side_effect=fake_get_value)

        self.assertEqual(service._get_pv_power(), 750)

    def test_get_pv_power_uses_summed_dc_sequence_when_available(self):
        service = ShellyWallboxService.__new__(ShellyWallboxService)
        service.auto_pv_path = "/Ac/Power"
        service.auto_pv_service = ""
        service.auto_use_dc_pv = True
        service.auto_dc_pv_service = "com.victronenergy.system"
        service.auto_dc_pv_path = "/Dc/Pv/Power"
        service._resolve_auto_pv_services = MagicMock(side_effect=ValueError("no ac pv"))
        service.auto_pv_scan_interval_seconds = 60
        service._last_pv_missing_warning = None

        def fake_get_value(service_name, path):
            if service_name == "com.victronenergy.system":
                return [500, 250]
            return None

        service._get_dbus_value = MagicMock(side_effect=fake_get_value)

        self.assertEqual(service._get_pv_power(), 750)

    def test_get_pv_power_assumes_zero_when_no_ac_or_dc_pv_exists(self):
        service = ShellyWallboxService.__new__(ShellyWallboxService)
        service.auto_pv_path = "/Ac/Power"
        service.auto_pv_service = ""
        service.auto_use_dc_pv = True
        service.auto_dc_pv_service = "com.victronenergy.system"
        service.auto_dc_pv_path = "/Dc/Pv/Power"
        service._resolve_auto_pv_services = MagicMock(side_effect=ValueError("no ac pv"))
        service.auto_pv_scan_interval_seconds = 60
        service._last_pv_missing_warning = None

        def fake_get_value(service_name, path):
            if service_name == "com.victronenergy.system":
                raise ValueError("no dc pv")
            return None

        service._get_dbus_value = MagicMock(side_effect=fake_get_value)

        self.assertEqual(service._get_pv_power(), 0.0)

    def test_get_pv_power_assumes_zero_when_discovered_services_have_no_readable_values(self):
        service = ShellyWallboxService.__new__(ShellyWallboxService)
        service.auto_pv_path = "/Ac/Power"
        service.auto_pv_service = ""
        service.auto_use_dc_pv = True
        service.auto_dc_pv_service = "com.victronenergy.system"
        service.auto_dc_pv_path = "/Dc/Pv/Power"
        service._resolve_auto_pv_services = MagicMock(return_value=["pv1", "pv2"])
        service.auto_pv_scan_interval_seconds = 60
        service._last_pv_missing_warning = None

        def fake_get_value(service_name, path):
            if service_name == "com.victronenergy.system":
                raise ValueError("no dc pv")
            raise ValueError("night mode")

        service._get_dbus_value = MagicMock(side_effect=fake_get_value)

        self.assertEqual(service._get_pv_power(), 0.0)

    def test_get_pv_power_does_not_assume_zero_for_explicit_ac_service_failure(self):
        service = ShellyWallboxService.__new__(ShellyWallboxService)
        service.auto_pv_path = "/Ac/Power"
        service.auto_pv_service = "com.victronenergy.pvinverter.http_48"
        service.auto_use_dc_pv = False
        service._resolve_auto_pv_services = MagicMock(side_effect=ValueError("explicit service missing"))
        service.auto_pv_scan_interval_seconds = 60
        service._last_pv_missing_warning = None

        self.assertIsNone(service._get_pv_power())

    def test_get_pv_power_rescans_when_cached_services_fail(self):
        service = ShellyWallboxService.__new__(ShellyWallboxService)
        service.auto_pv_path = "/Ac/Power"
        service.auto_use_dc_pv = False
        service.auto_pv_service = ""
        service._resolved_auto_pv_services = ["pv-old"]
        service._auto_pv_last_scan = 1.0
        service.auto_pv_scan_interval_seconds = 60
        service._last_pv_missing_warning = None
        service._resolve_auto_pv_services = MagicMock(side_effect=[["pv-old"], ["pv-new"]])

        def fake_get_value(service_name, path):
            if service_name == "pv-old":
                raise ValueError("stale service")
            if service_name == "pv-new":
                return 900
            return None

        service._get_dbus_value = MagicMock(side_effect=fake_get_value)

        self.assertEqual(service._get_pv_power(), 900)

    def test_get_pv_power_ignores_missing_and_nonnumeric_service_values_before_zero_fallback(self):
        service = ShellyWallboxService.__new__(ShellyWallboxService)
        service.auto_pv_path = "/Ac/Power"
        service.auto_pv_service = ""
        service.auto_use_dc_pv = False
        service._resolve_auto_pv_services = MagicMock(return_value=["pv1", "pv2"])
        service.auto_pv_scan_interval_seconds = 60
        service._last_pv_missing_warning = None

        def fake_get_value(service_name, _path):
            if service_name == "pv1":
                return None
            return ["bad"]

        service._get_dbus_value = MagicMock(side_effect=fake_get_value)

        self.assertEqual(service._get_pv_power(), 0.0)

    def test_get_pv_power_skips_reads_during_retry_cooldown(self):
        service = ShellyWallboxService.__new__(ShellyWallboxService)
        service._source_retry_after = {"pv": 200.0}

        with unittest.mock.patch("venus_evcharger_service.time.time", return_value=100.0):
            self.assertIsNone(service._get_pv_power())

    def test_resolve_auto_pv_services_limits_results(self):
        service = ShellyWallboxService.__new__(ShellyWallboxService)
        service.auto_pv_service = ""
        service.auto_pv_service_prefix = "com.victronenergy.pvinverter"
        service.auto_pv_max_services = 1
        service.auto_pv_scan_interval_seconds = 60
        service._resolved_auto_pv_services = []
        service._auto_pv_last_scan = 0.0
        self._seed_gateway_services(service, [
            "com.victronenergy.pvinverter.http_1",
            "com.victronenergy.pvinverter.http_2",
        ])

        services = service._resolve_auto_pv_services()
        self.assertEqual(services, ["com.victronenergy.pvinverter.http_1"])

    def test_auto_battery_service_auto_detect(self):
        service = ShellyWallboxService.__new__(ShellyWallboxService)
        service.auto_battery_service = ""
        service.auto_battery_service_prefix = "com.victronenergy.battery"
        service.auto_battery_scan_interval_seconds = 60
        service.auto_battery_soc_path = "/Soc"
        service._resolved_auto_battery_service = None
        service._auto_battery_last_scan = 0.0
        self._seed_gateway_services(service, [
            "com.victronenergy.system",
            "com.victronenergy.battery.test",
        ])
        service._get_dbus_value = MagicMock(return_value=55.0)

        self.assertEqual(service._resolve_auto_battery_service(), "com.victronenergy.battery.test")

    def test_get_grid_power_skips_reads_during_retry_cooldown(self):
        service = ShellyWallboxService.__new__(ShellyWallboxService)
        service._source_retry_after = {"grid": 200.0}

        with unittest.mock.patch("venus_evcharger_service.time.time", return_value=100.0):
            self.assertIsNone(service._get_grid_power())

    def test_get_grid_power_requires_all_phases_by_default(self):
        service = ShellyWallboxService.__new__(ShellyWallboxService)
        service.auto_grid_service = "com.victronenergy.system"
        service.auto_grid_l1_path = "/Ac/Grid/L1/Power"
        service.auto_grid_l2_path = "/Ac/Grid/L2/Power"
        service.auto_grid_l3_path = "/Ac/Grid/L3/Power"
        service.auto_grid_require_all_phases = True
        service.auto_pv_scan_interval_seconds = 60
        service._source_retry_after = {}
        service._warning_state = {}
        service._error_state = {"dbus": 0, "shelly": 0, "pv": 0, "battery": 0, "grid": 0, "cache_hits": 0}
        service._failure_active = {"dbus": False, "shelly": False, "pv": False, "battery": False, "grid": False}

        def fake_get_value(service_name, path):
            values = {
                "/Ac/Grid/L1/Power": -1000.0,
                "/Ac/Grid/L2/Power": 500.0,
            }
            if path not in values:
                raise ValueError("missing phase")
            return values[path]

        service._get_dbus_value = MagicMock(side_effect=fake_get_value)

        with unittest.mock.patch("venus_evcharger_service.time.time", return_value=100.0):
            self.assertIsNone(service._get_grid_power())

    def test_get_grid_power_can_allow_partial_phases_when_configured(self):
        service = ShellyWallboxService.__new__(ShellyWallboxService)
        service.auto_grid_service = "com.victronenergy.system"
        service.auto_grid_l1_path = "/Ac/Grid/L1/Power"
        service.auto_grid_l2_path = "/Ac/Grid/L2/Power"
        service.auto_grid_l3_path = "/Ac/Grid/L3/Power"
        service.auto_grid_require_all_phases = False
        service.auto_pv_scan_interval_seconds = 60
        service._source_retry_after = {}
        service._warning_state = {}
        service._error_state = {"dbus": 0, "shelly": 0, "pv": 0, "battery": 0, "grid": 0, "cache_hits": 0}
        service._failure_active = {"dbus": False, "shelly": False, "pv": False, "battery": False, "grid": False}

        def fake_get_value(service_name, path):
            values = {
                "/Ac/Grid/L1/Power": -1000.0,
                "/Ac/Grid/L2/Power": 500.0,
            }
            if path not in values:
                raise ValueError("missing phase")
            return values[path]

        service._get_dbus_value = MagicMock(side_effect=fake_get_value)

        with unittest.mock.patch("venus_evcharger_service.time.time", return_value=100.0):
            self.assertEqual(service._get_grid_power(), -500.0)

    def test_auto_battery_service_fallback_when_override_missing(self):
        service = ShellyWallboxService.__new__(ShellyWallboxService)
        service.auto_battery_service = "com.victronenergy.battery.explicit"
        service.auto_battery_service_prefix = "com.victronenergy.battery"
        service.auto_battery_scan_interval_seconds = 60
        service.auto_battery_soc_path = "/Soc"
        service._resolved_auto_battery_service = None
        service._auto_battery_last_scan = 0.0
        self._seed_gateway_services(service, [
            "com.victronenergy.system",
            "com.victronenergy.battery.test",
        ])

        def fake_get_value(service_name, path):
            if service_name == "com.victronenergy.battery.explicit":
                return None
            return 55.0

        service._get_dbus_value = MagicMock(side_effect=fake_get_value)

        self.assertEqual(service._resolve_auto_battery_service(), "com.victronenergy.battery.test")

    def test_get_battery_soc_retries_after_cached_service_failure(self):
        service = ShellyWallboxService.__new__(ShellyWallboxService)
        service.auto_battery_service = ""
        service.auto_battery_soc_path = "/Soc"
        service.auto_battery_scan_interval_seconds = 60
        service._last_battery_missing_warning = None
        service._resolved_auto_battery_service = "battery-old"
        service._auto_battery_last_scan = 1.0
        service._resolve_auto_battery_service = MagicMock(side_effect=["battery-old", "battery-new"])

        def fake_get_value(service_name, path):
            if service_name == "battery-old":
                raise ValueError("stale battery service")
            if service_name == "battery-new":
                return 56.0
            return None

        service._get_dbus_value = MagicMock(side_effect=fake_get_value)

        self.assertEqual(service._get_battery_soc(), 56.0)

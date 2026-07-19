# SPDX-License-Identifier: GPL-3.0-or-later
from tests.venus_evcharger_helpers_support import *
from venus_evcharger.dbus_gateway import (
    BATTERY_SOC_READ_KEY,
    GRID_POWER_READ_KEY,
    PV_POWER_READ_KEY,
    DbusCacheStore,
    gateway_paths,
)


class TestShellyWallboxHelpersPrimary(ShellyWallboxHelpersTestBase):
    def _seed_gateway_services(self, service, names=(), *, key_values=None):
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        paths = gateway_paths(f"{temp_dir.name}/run")
        store = DbusCacheStore(paths)
        store.update_services(list(names))
        for key, value in (key_values or {}).items():
            store.update_value(str(key), value, source=f"read-key:{key}")
        store.write_snapshot_files()
        service.dbus_gateway_run_dir = paths.run_dir
        service.dbus_gateway_cache_path = paths.cache_path
        service.controllers.runtime.runtime.mark_recovery = MagicMock()
        service.controllers.runtime.runtime.mark_failure = MagicMock()
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
        service = make_helper_service()
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
        configure_auto_policy(
            service,
            min_soc=120.0,
            resume_soc=-5.0,
            start_surplus_watts=1500.0,
            stop_surplus_watts=2400.0,
        )

        service.state.validate_runtime_config()

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
        self.assertEqual(service.auto_policy.min_soc, 100.0)
        self.assertEqual(service.auto_policy.resume_soc, 100.0)
        self.assertEqual(service.auto_policy.normal_profile.stop_surplus_watts, 1500.0)

    def test_available_surplus_uses_only_pv_backed_export(self):
        self.assertEqual(AutoDecisionController.get_available_surplus_watts(2500, -1800), 1800)
        self.assertEqual(AutoDecisionController.get_available_surplus_watts(0, -1800), 0)

    def test_get_pv_power_skips_failed_services_and_dc(self):
        service = make_helper_service()
        service.controllers.runtime.dbus_input.gateway.get_dbus_value = MagicMock(return_value=None)
        self._seed_gateway_services(service, key_values={PV_POWER_READ_KEY: 1000.0})

        self.assertEqual(service.controllers.runtime.dbus_input.get_pv_power(), 1000)
        service.controllers.runtime.dbus_input.gateway.get_dbus_value.assert_not_called()

    def test_get_pv_power_uses_dc_only_when_ac_missing(self):
        service = make_helper_service()
        service.controllers.runtime.dbus_input.gateway.get_dbus_value = MagicMock(return_value=None)
        self._seed_gateway_services(service, key_values={PV_POWER_READ_KEY: 750.0})

        self.assertEqual(service.controllers.runtime.dbus_input.get_pv_power(), 750)
        service.controllers.runtime.dbus_input.gateway.get_dbus_value.assert_not_called()

    def test_get_pv_power_uses_summed_dc_sequence_when_available(self):
        service = make_helper_service()
        service.controllers.runtime.dbus_input.gateway.get_dbus_value = MagicMock(return_value=None)
        self._seed_gateway_services(service, key_values={PV_POWER_READ_KEY: 750.0})

        self.assertEqual(service.controllers.runtime.dbus_input.get_pv_power(), 750)
        service.controllers.runtime.dbus_input.gateway.get_dbus_value.assert_not_called()

    def test_get_pv_power_assumes_zero_when_no_ac_or_dc_pv_exists(self):
        service = make_helper_service()
        self._seed_gateway_services(service, key_values={PV_POWER_READ_KEY: 0.0})
        service.controllers.runtime.dbus_input.gateway.get_dbus_value = MagicMock(return_value=None)

        self.assertEqual(service.controllers.runtime.dbus_input.get_pv_power(), 0.0)
        service.controllers.runtime.dbus_input.gateway.get_dbus_value.assert_not_called()

    def test_get_pv_power_assumes_zero_when_discovered_services_have_no_readable_values(self):
        service = make_helper_service()
        self._seed_gateway_services(service, key_values={PV_POWER_READ_KEY: 0.0})
        service.controllers.runtime.dbus_input.gateway.get_dbus_value = MagicMock(return_value=None)

        self.assertEqual(service.controllers.runtime.dbus_input.get_pv_power(), 0.0)
        service.controllers.runtime.dbus_input.gateway.get_dbus_value.assert_not_called()

    def test_get_pv_power_does_not_assume_zero_for_explicit_ac_service_failure(self):
        service = make_helper_service()
        service.auto_pv_path = "/Ac/Power"
        service.auto_pv_service = "com.victronenergy.pvinverter.http_48"
        service.auto_use_dc_pv = False
        service.controllers.runtime.dbus_input.pv.resolve_auto_pv_services = MagicMock(side_effect=ValueError("explicit service missing"))
        service.auto_pv_scan_interval_seconds = 60
        service._last_pv_missing_warning = None

        self.assertIsNone(service.controllers.runtime.dbus_input.get_pv_power())

    def test_get_pv_power_rescans_when_cached_services_fail(self):
        service = make_helper_service()
        self._seed_gateway_services(service, key_values={PV_POWER_READ_KEY: 900.0})
        service.controllers.runtime.dbus_input.gateway.get_dbus_value = MagicMock(return_value=None)

        self.assertEqual(service.controllers.runtime.dbus_input.get_pv_power(), 900)
        service.controllers.runtime.dbus_input.gateway.get_dbus_value.assert_not_called()

    def test_get_pv_power_ignores_missing_and_nonnumeric_service_values_before_zero_fallback(self):
        service = make_helper_service()
        self._seed_gateway_services(service, key_values={PV_POWER_READ_KEY: 0.0})
        service.controllers.runtime.dbus_input.gateway.get_dbus_value = MagicMock(return_value=None)

        self.assertEqual(service.controllers.runtime.dbus_input.get_pv_power(), 0.0)
        service.controllers.runtime.dbus_input.gateway.get_dbus_value.assert_not_called()

    def test_get_pv_power_skips_reads_during_retry_cooldown(self):
        service = make_helper_service()
        service._source_retry_after = {"pv": 200.0}

        with unittest.mock.patch("venus_evcharger_service.time.time", return_value=100.0):
            self.assertIsNone(service.controllers.runtime.dbus_input.get_pv_power())

    def test_resolve_auto_pv_services_limits_results(self):
        service = make_helper_service()
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

        services = service.controllers.runtime.dbus_input.resolve_auto_pv_services()
        self.assertEqual(services, ["com.victronenergy.pvinverter.http_1"])

    def test_auto_battery_service_auto_detect(self):
        service = make_helper_service()
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
        service.controllers.runtime.dbus_input.gateway.get_dbus_value = MagicMock(return_value=55.0)

        self.assertEqual(service.controllers.runtime.dbus_input.resolve_auto_battery_service(), "com.victronenergy.battery.test")

    def test_get_grid_power_skips_reads_during_retry_cooldown(self):
        service = make_helper_service()
        service._source_retry_after = {"grid": 200.0}

        with unittest.mock.patch("venus_evcharger_service.time.time", return_value=100.0):
            self.assertIsNone(service.controllers.runtime.dbus_input.get_grid_power())

    def test_get_grid_power_returns_none_when_semantic_gateway_value_is_missing(self):
        service = make_helper_service()
        service.auto_grid_service = "com.victronenergy.system"
        service.auto_pv_scan_interval_seconds = 60
        service.controllers.runtime.dbus_input.gateway.get_dbus_value = MagicMock(return_value=None)
        self._seed_gateway_services(service)

        self.assertIsNone(service.controllers.runtime.dbus_input.get_grid_power())
        service.controllers.runtime.dbus_input.gateway.get_dbus_value.assert_not_called()

    def test_get_grid_power_uses_fresh_semantic_gateway_value(self):
        service = make_helper_service()
        service.auto_pv_scan_interval_seconds = 60
        service.controllers.runtime.dbus_input.gateway.get_dbus_value = MagicMock(return_value=None)
        self._seed_gateway_services(service, key_values={GRID_POWER_READ_KEY: -500.0})

        self.assertEqual(service.controllers.runtime.dbus_input.get_grid_power(), -500.0)
        service.controllers.runtime.dbus_input.gateway.get_dbus_value.assert_not_called()

    def test_auto_battery_service_fallback_when_override_missing(self):
        service = make_helper_service()
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

        service.controllers.runtime.dbus_input.gateway.get_dbus_value = MagicMock(side_effect=fake_get_value)

        self.assertEqual(service.controllers.runtime.dbus_input.resolve_auto_battery_service(), "com.victronenergy.battery.test")

    def test_get_battery_soc_retries_after_cached_service_failure(self):
        service = make_helper_service()
        service.auto_battery_scan_interval_seconds = 60
        service.controllers.runtime.dbus_input.gateway.get_dbus_value = MagicMock(return_value=None)
        self._seed_gateway_services(service, key_values={BATTERY_SOC_READ_KEY: 56.0})

        self.assertEqual(service.controllers.runtime.dbus_input.get_battery_soc(), 56.0)
        service.controllers.runtime.dbus_input.gateway.get_dbus_value.assert_not_called()

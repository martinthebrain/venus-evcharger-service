# SPDX-License-Identifier: GPL-3.0-or-later
from tests.venus_evcharger_helpers_support import *


class TestShellyWallboxHelpersPrimary(ShellyWallboxHelpersTestBase):
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
        self.assertEqual(service.shelly_request_timeout_seconds, 2.0)
        self.assertEqual(service.dbus_method_timeout_seconds, 1.0)
        self.assertEqual(service.auto_policy.min_soc, 100.0)
        self.assertEqual(service.auto_policy.resume_soc, 100.0)
        self.assertEqual(service.auto_policy.normal_profile.stop_surplus_watts, 1500.0)

    def test_available_surplus_uses_only_pv_backed_export(self):
        self.assertEqual(AutoDecisionController.get_available_surplus_watts(2500, -1800), 1800)
        self.assertEqual(AutoDecisionController.get_available_surplus_watts(0, -1800), 0)

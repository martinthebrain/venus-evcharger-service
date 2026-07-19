# SPDX-License-Identifier: GPL-3.0-or-later
from tests.venus_evcharger_helpers_support import *


class TestShellyWallboxHelpersQuinary(ShellyWallboxHelpersTestBase):
    def test_auto_mode_does_not_restart_until_resume_soc(self):
        service = make_helper_service()
        service.virtual_mode = 1
        service.virtual_autostart = 1
        configure_auto_policy(service)
        service.auto_average_window_seconds = 30
        service.auto_min_runtime_seconds = 300
        service.auto_min_offtime_seconds = 120
        service.auto_start_delay_seconds = 10
        service.auto_stop_delay_seconds = 30
        service.auto_start_condition_since = 100.0
        service.auto_stop_condition_since = None
        service.auto_samples = deque([(105.0, 2200.0, -2200.0)])
        service.relay_last_changed_at = None
        service.relay_last_off_at = None
        service.started_at = 0.0
        service.auto_startup_warmup_seconds = 0.0
        service.manual_override_until = 0.0

        with unittest.mock.patch("venus_evcharger_service.time.time", return_value=111.0):
            self.assertFalse(service.auto.decide_relay(False, 2200, 31, -2200))

    def test_auto_mode_keeps_running_inside_hysteresis_band(self):
        service = make_helper_service()
        service.virtual_mode = 1
        service.virtual_autostart = 1
        configure_auto_policy(service)
        service.auto_average_window_seconds = 30
        service.auto_min_runtime_seconds = 300
        service.auto_min_offtime_seconds = 120
        service.auto_start_delay_seconds = 10
        service.auto_stop_delay_seconds = 30
        service.auto_start_condition_since = None
        service.auto_stop_condition_since = None
        service.auto_samples = deque([(205.0, 1800.0, -1800.0)])
        service.relay_last_changed_at = 0.0
        service.relay_last_off_at = None
        service.started_at = 0.0
        service.auto_startup_warmup_seconds = 0.0
        service.manual_override_until = 0.0

        with unittest.mock.patch("venus_evcharger_service.time.time", return_value=211.0):
            self.assertTrue(service.auto.decide_relay(True, 1800, 31, -1800))

    def test_auto_mode_does_not_stop_before_min_runtime(self):
        service = make_helper_service()
        service.virtual_mode = 1
        service.virtual_autostart = 1
        configure_auto_policy(service)
        service.auto_average_window_seconds = 30
        service.auto_min_runtime_seconds = 300
        service.auto_min_offtime_seconds = 120
        service.auto_start_delay_seconds = 10
        service.auto_stop_delay_seconds = 30
        service.auto_start_condition_since = None
        service.auto_stop_condition_since = None
        service.auto_samples = deque([(110.0, 1200.0, 500.0)])
        service.relay_last_changed_at = 100.0
        service.relay_last_off_at = None
        service.started_at = 0.0
        service.auto_startup_warmup_seconds = 0.0
        service.manual_override_until = 0.0

        with unittest.mock.patch("venus_evcharger_service.time.time", return_value=200.0):
            self.assertTrue(service.auto.decide_relay(True, 1200, 45, 500))

    def test_auto_mode_does_not_start_when_average_grid_import_is_too_high(self):
        service = make_helper_service()
        service.virtual_mode = 1
        service.virtual_autostart = 1
        configure_auto_policy(service)
        service.auto_average_window_seconds = 30
        service.auto_min_runtime_seconds = 300
        service.auto_min_offtime_seconds = 120
        service.auto_start_delay_seconds = 10
        service.auto_stop_delay_seconds = 30
        service.auto_start_condition_since = 100.0
        service.auto_stop_condition_since = None
        service.auto_samples = deque([(105.0, 2200.0, 150.0)])
        service.relay_last_changed_at = None
        service.relay_last_off_at = None
        service.started_at = 0.0
        service.auto_startup_warmup_seconds = 0.0
        service.manual_override_until = 0.0

        with unittest.mock.patch("venus_evcharger_service.time.time", return_value=111.0):
            self.assertFalse(service.auto.decide_relay(False, 2200, 45, 150))

    def test_auto_mode_does_not_restart_before_min_offtime(self):
        service = make_helper_service()
        service.virtual_mode = 1
        service.virtual_autostart = 1
        configure_auto_policy(service)
        service.auto_average_window_seconds = 30
        service.auto_min_runtime_seconds = 300
        service.auto_min_offtime_seconds = 120
        service.auto_start_delay_seconds = 10
        service.auto_stop_delay_seconds = 30
        service.auto_start_condition_since = 100.0
        service.auto_stop_condition_since = None
        service.auto_samples = deque([(105.0, 2200.0, -2200.0)])
        service.relay_last_changed_at = 100.0
        service.relay_last_off_at = 100.0
        service.started_at = 0.0
        service.auto_startup_warmup_seconds = 0.0
        service.manual_override_until = 0.0

        with unittest.mock.patch("venus_evcharger_service.time.time", return_value=111.0):
            self.assertFalse(service.auto.decide_relay(False, 2200, 45, -2200))

    def test_auto_mode_waits_during_startup_warmup(self):
        service = make_helper_service()
        service.virtual_mode = 1
        service.virtual_autostart = 1
        service.auto_allow_without_battery_soc = False
        configure_auto_policy(service)
        service.auto_average_window_seconds = 30
        service.auto_min_runtime_seconds = 300
        service.auto_min_offtime_seconds = 120
        service.auto_start_delay_seconds = 10
        service.auto_stop_delay_seconds = 30
        service.auto_start_condition_since = None
        service.auto_stop_condition_since = None
        service.auto_samples = deque()
        service.relay_last_changed_at = None
        service.relay_last_off_at = None
        service.started_at = 100.0
        service.auto_startup_warmup_seconds = 30.0
        service.manual_override_until = 0.0

        with unittest.mock.patch("venus_evcharger_service.time.time", return_value=110.0):
            self.assertFalse(service.auto.decide_relay(False, 2200, 45, -2200))
        self.assertEqual(service._last_health_reason, "warmup")

    def test_auto_mode_respects_manual_override_holdoff(self):
        service = make_helper_service()
        service.virtual_mode = 1
        service.virtual_autostart = 1
        service.auto_allow_without_battery_soc = False
        configure_auto_policy(service)
        service.auto_average_window_seconds = 30
        service.auto_min_runtime_seconds = 300
        service.auto_min_offtime_seconds = 120
        service.auto_start_delay_seconds = 10
        service.auto_stop_delay_seconds = 30
        service.auto_start_condition_since = None
        service.auto_stop_condition_since = None
        service.auto_samples = deque()
        service.relay_last_changed_at = None
        service.relay_last_off_at = None
        service.started_at = 0.0
        service.auto_startup_warmup_seconds = 0.0
        service.manual_override_until = 200.0

        with unittest.mock.patch("venus_evcharger_service.time.time", return_value=150.0):
            self.assertFalse(service.auto.decide_relay(False, 2200, 45, -2200))
        self.assertEqual(service._last_health_reason, "manual-override")

    def test_auto_mode_scenario_cloud_passages_hold_charge_then_stop_after_persistent_import(self):
        service = make_helper_service()
        service.virtual_mode = 1
        service.virtual_autostart = 1
        service.virtual_enable = 1
        service.auto_allow_without_battery_soc = False
        configure_auto_policy(service)
        service.auto_average_window_seconds = 30
        service.auto_min_runtime_seconds = 0
        service.auto_min_offtime_seconds = 120
        service.auto_start_delay_seconds = 10
        service.auto_stop_delay_seconds = 30
        service.auto_start_condition_since = None
        service.auto_stop_condition_since = None
        service.auto_samples = deque()
        service.relay_last_changed_at = 0.0
        service.relay_last_off_at = None
        service.started_at = 0.0
        service.auto_startup_warmup_seconds = 0.0
        service.manual_override_until = 0.0

        with unittest.mock.patch("venus_evcharger_service.time.time", return_value=100.0):
            self.assertTrue(service.auto.decide_relay(True, 2200.0, 45.0, -1800.0))
        self.assertIsNone(service.auto_stop_condition_since)

        with unittest.mock.patch("venus_evcharger_service.time.time", return_value=110.0):
            self.assertTrue(service.auto.decide_relay(True, 900.0, 45.0, 420.0))

        with unittest.mock.patch("venus_evcharger_service.time.time", return_value=130.0):
            self.assertTrue(service.auto.decide_relay(True, 950.0, 45.0, 390.0))

        with unittest.mock.patch("venus_evcharger_service.time.time", return_value=141.0):
            self.assertFalse(service.auto.decide_relay(True, 900.0, 45.0, 410.0))
        self.assertEqual(service._last_health_reason, "auto-stop")

    def test_auto_mode_scenario_grid_missing_for_45_seconds_then_recovering_keeps_session_running(self):
        service = make_helper_service()
        service.virtual_mode = 1
        service.virtual_autostart = 1
        service.virtual_enable = 1
        service.auto_allow_without_battery_soc = False
        configure_auto_policy(service)
        service.auto_average_window_seconds = 30
        service.auto_min_runtime_seconds = 0
        service.auto_min_offtime_seconds = 120
        service.auto_grid_missing_stop_seconds = 60
        service.auto_start_delay_seconds = 10
        service.auto_stop_delay_seconds = 30
        service.auto_start_condition_since = None
        service.auto_stop_condition_since = None
        service.auto_samples = deque()
        service.relay_last_changed_at = 0.0
        service.relay_last_off_at = None
        service.started_at = 0.0
        service.auto_startup_warmup_seconds = 0.0
        service.manual_override_until = 0.0
        service._last_grid_at = 100.0

        with unittest.mock.patch("venus_evcharger_service.time.time", return_value=145.0):
            self.assertTrue(service.auto.decide_relay(True, 0.0, 45.0, None))

        with unittest.mock.patch("venus_evcharger_service.time.time", return_value=146.0):
            self.assertTrue(service.auto.decide_relay(True, 0.0, 45.0, -250.0))

        self.assertNotEqual(service._last_health_reason, "grid-missing")

    def test_auto_mode_scenario_resume_soc_crossing_arms_then_allows_start(self):
        service = make_helper_service()
        service.virtual_mode = 1
        service.virtual_autostart = 1
        service.virtual_enable = 1
        service.auto_allow_without_battery_soc = False
        configure_auto_policy(service, min_soc=40.0, resume_soc=50.0)
        service.auto_average_window_seconds = 30
        service.auto_min_runtime_seconds = 300
        service.auto_min_offtime_seconds = 120
        service.auto_start_delay_seconds = 10
        service.auto_stop_delay_seconds = 30
        service.auto_start_condition_since = None
        service.auto_stop_condition_since = None
        service.auto_samples = deque()
        service.relay_last_changed_at = None
        service.relay_last_off_at = None
        service.started_at = 0.0
        service.auto_startup_warmup_seconds = 0.0
        service.manual_override_until = 0.0

        with unittest.mock.patch("venus_evcharger_service.time.time", return_value=100.0):
            self.assertFalse(service.auto.decide_relay(False, 2400.0, 49.8, -2200.0))
        self.assertIsNone(service.auto_start_condition_since)

        with unittest.mock.patch("venus_evcharger_service.time.time", return_value=101.0):
            self.assertFalse(service.auto.decide_relay(False, 2400.0, 50.2, -2200.0))
        self.assertEqual(service.auto_start_condition_since, 101.0)

        with unittest.mock.patch("venus_evcharger_service.time.time", return_value=112.0):
            self.assertTrue(service.auto.decide_relay(False, 2400.0, 50.2, -2200.0))

    def test_manual_override_scenario_after_auto_stop_keeps_manual_reenable_in_control(self):
        service = make_helper_service()
        service.virtual_mode = 0
        service.virtual_autostart = 1
        service.virtual_startstop = 0
        service.virtual_enable = 0
        service.virtual_set_current = 16.0
        service.max_current = 16.0
        service.min_current = 6.0
        service.auto_manual_override_seconds = 300
        service.manual_override_until = 0.0
        service._dbusservice = {"/Mode": 0, "/StartStop": 0, "/Enable": 0}
        service.controllers.runtime.shelly.queue_relay_command = MagicMock()
        service.controllers.runtime.shelly.publish_local_pm_status = MagicMock()

        service.virtual_mode = 1
        service.virtual_enable = 1
        service.virtual_autostart = 1
        service.auto_allow_without_battery_soc = False
        configure_auto_policy(service)
        service.auto_average_window_seconds = 30
        service.auto_min_runtime_seconds = 0
        service.auto_min_offtime_seconds = 120
        service.auto_start_delay_seconds = 10
        service.auto_stop_delay_seconds = 30
        service.auto_start_condition_since = None
        service.auto_stop_condition_since = 100.0
        service.auto_samples = deque([(105.0, 900.0, 400.0)])
        service.relay_last_changed_at = 0.0
        service.relay_last_off_at = None
        service.started_at = 0.0
        service.auto_startup_warmup_seconds = 0.0

        with unittest.mock.patch("venus_evcharger_service.time.time", return_value=131.0):
            self.assertFalse(service.auto.decide_relay(True, 900.0, 45.0, 400.0))

        service.virtual_mode = 0
        with unittest.mock.patch("venus_evcharger_service.time.time", return_value=132.0):
            self.assertTrue(service.auto.handle_dbus_write("/StartStop", 1))

        service.virtual_mode = 1
        with unittest.mock.patch("venus_evcharger_service.time.time", return_value=150.0):
            self.assertTrue(service.auto.decide_relay(True, 900.0, 45.0, 400.0))
        self.assertEqual(service._last_health_reason, "manual-override")

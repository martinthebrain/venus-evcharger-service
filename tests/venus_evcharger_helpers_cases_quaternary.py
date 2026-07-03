# SPDX-License-Identifier: GPL-3.0-or-later
from tests.venus_evcharger_helpers_support import *
from venus_evcharger.dbus_gateway import EVCS_FIELD_TO_PATH


class TestShellyWallboxHelpersQuaternary(ShellyWallboxHelpersTestBase):
    def test_auto_mode_can_stop_running_charge_when_pv_missing_but_soc_is_too_low(self):
        service = ShellyWallboxService.__new__(ShellyWallboxService)
        service.virtual_mode = 1
        service.virtual_autostart = 1
        service.virtual_startstop = 1
        service.virtual_enable = 1
        service.auto_allow_without_battery_soc = False
        service.auto_min_soc = 30
        service.auto_resume_soc = 33
        service.auto_start_surplus_watts = 2300
        service.auto_stop_surplus_watts = 1600
        service.auto_average_window_seconds = 30
        service.auto_min_runtime_seconds = 0
        service.auto_min_offtime_seconds = 120
        service.auto_start_max_grid_import_watts = 50
        service.auto_stop_grid_import_watts = 300
        service.auto_start_delay_seconds = 10
        service.auto_stop_delay_seconds = 30
        service.started_at = 0.0
        service.auto_startup_warmup_seconds = 0.0
        service.auto_manual_override_seconds = 300
        service.manual_override_until = 0.0
        service.auto_start_condition_since = None
        service.auto_stop_condition_since = None
        service.auto_samples = deque()
        service.relay_last_changed_at = None
        service.relay_last_off_at = None
        service._last_health_reason = "init"
        service._last_health_code = 0
        service._auto_cached_inputs_used = False

        with unittest.mock.patch("venus_evcharger_service.time.time", return_value=200.0):
            self.assertTrue(service._auto_decide_relay(True, None, 10.0, 500.0))
        with unittest.mock.patch("venus_evcharger_service.time.time", return_value=231.0):
            self.assertFalse(service._auto_decide_relay(True, None, 10.0, 500.0))

    def test_auto_mode_can_stop_running_charge_when_pv_missing_but_grid_import_is_high(self):
        service = ShellyWallboxService.__new__(ShellyWallboxService)
        service.virtual_mode = 1
        service.virtual_autostart = 1
        service.virtual_startstop = 1
        service.virtual_enable = 1
        service.auto_allow_without_battery_soc = True
        service.auto_min_soc = 30
        service.auto_resume_soc = 33
        service.auto_start_surplus_watts = 2300
        service.auto_stop_surplus_watts = 1600
        service.auto_average_window_seconds = 30
        service.auto_min_runtime_seconds = 0
        service.auto_min_offtime_seconds = 120
        service.auto_start_max_grid_import_watts = 50
        service.auto_stop_grid_import_watts = 300
        service.auto_start_delay_seconds = 10
        service.auto_stop_delay_seconds = 30
        service.started_at = 0.0
        service.auto_startup_warmup_seconds = 0.0
        service.auto_manual_override_seconds = 300
        service.manual_override_until = 0.0
        service.auto_start_condition_since = None
        service.auto_stop_condition_since = None
        service.auto_samples = deque()
        service.relay_last_changed_at = None
        service.relay_last_off_at = None
        service._last_health_reason = "init"
        service._last_health_code = 0
        service._auto_cached_inputs_used = False
        service._last_battery_allow_warning = None
        service.auto_battery_scan_interval_seconds = 60

        with unittest.mock.patch("venus_evcharger_service.time.time", return_value=200.0):
            self.assertTrue(service._auto_decide_relay(True, None, 40.0, 500.0))
        with unittest.mock.patch("venus_evcharger_service.time.time", return_value=231.0):
            self.assertFalse(service._auto_decide_relay(True, None, 40.0, 500.0))

    def test_update_virtual_state_keeps_enable_separate_from_relay_state(self):
        service = ShellyWallboxService.__new__(ShellyWallboxService)
        service.charging_started_at = None
        service.energy_at_start = 0.0
        service.last_status = 0
        service.virtual_mode = 0
        service.virtual_autostart = 1
        service.virtual_startstop = 1
        service.virtual_enable = 1
        service.virtual_set_current = 16.0
        service.min_current = 6.0
        service.max_current = 16.0
        service._dbusservice = {}

        service._update_virtual_state(6, 1.23, False)

        self.assertEqual(service._dbusservice["/Status"], 6)
        self.assertEqual(service._dbusservice["/StartStop"], 0)
        self.assertEqual(service._dbusservice["/Enable"], 1)

    def test_update_virtual_state_resets_session_when_relay_stays_on_but_charging_stops(self):
        service = ShellyWallboxService.__new__(ShellyWallboxService)
        service.charging_started_at = 100.0
        service.energy_at_start = 5.0
        service.last_status = 2
        service.virtual_mode = 1
        service.virtual_autostart = 1
        service.virtual_startstop = 1
        service.virtual_enable = 1
        service.virtual_set_current = 16.0
        service.min_current = 6.0
        service.max_current = 16.0
        service._dbusservice = {}

        with unittest.mock.patch("venus_evcharger_service.time.time", return_value=130.0):
            service._update_virtual_state(6, 6.25, True)

        self.assertEqual(service._dbusservice["/Status"], 6)
        self.assertEqual(service._dbusservice["/ChargingTime"], 0)
        self.assertEqual(service._dbusservice["/Session/Time"], 0)
        self.assertEqual(service._dbusservice["/Session/Energy"], 0.0)
        self.assertEqual(service._dbusservice["/Ac/Energy/Forward"], 0.0)
        self.assertEqual(service._dbusservice["/Ac/L1/Energy/Forward"], 0.0)
        self.assertIsNone(service.charging_started_at)
        self.assertEqual(service.energy_at_start, 6.25)

    def test_update_virtual_state_starts_new_session_after_manual_unplug_and_replug(self):
        service = self._make_update_service()
        service.virtual_mode = 2
        service.virtual_startstop = 1
        service._publish_dbus_field = MagicMock(
            side_effect=lambda field, value, *_args, **_kwargs: service._dbusservice.__setitem__(
                EVCS_FIELD_TO_PATH[field],
                value,
            )
            or True,
        )
        service._queue_relay_command = MagicMock()
        service._publish_local_pm_status = MagicMock()
        service._save_runtime_state = MagicMock()
        service._save_runtime_overrides = MagicMock()
        service._state_summary = MagicMock(return_value="state")
        service.auto_start_condition_since = 95.0
        service.auto_stop_condition_since = 120.0
        service.auto_stop_condition_reason = "auto-stop-surplus"

        service._time_now = MagicMock(return_value=100.0)
        service._update_virtual_state(2, 10.0, True)
        self.assertEqual(service._dbusservice["/Session/Energy"], 0.0)
        self.assertEqual(service.charging_started_at, 100.0)
        self.assertEqual(service.energy_at_start, 10.0)
        self.assertEqual(service.virtual_mode, 2)

        service._time_now.return_value = 130.0
        service._update_virtual_state(6, 10.5, True)
        self.assertEqual(service._dbusservice["/Session/Energy"], 0.0)
        self.assertIsNone(service.charging_started_at)
        self.assertEqual(service.energy_at_start, 10.5)
        self.assertEqual(service.virtual_mode, 2)
        self.assertIsNone(service.auto_start_condition_since)
        self.assertIsNone(service.auto_stop_condition_since)
        self.assertIsNone(service.auto_stop_condition_reason)
        service._queue_relay_command.assert_not_called()
        service._publish_local_pm_status.assert_not_called()
        self.assertFalse(service._ignore_min_offtime_once)

        service._time_now.return_value = 160.0
        service._update_virtual_state(2, 11.0, True)
        self.assertEqual(service._dbusservice["/Session/Energy"], 0.0)
        self.assertEqual(service.charging_started_at, 160.0)
        self.assertEqual(service.energy_at_start, 11.0)
        self.assertEqual(service.virtual_mode, 2)

        service._time_now.return_value = 190.0
        service._update_virtual_state(2, 11.4, True)
        self.assertEqual(service._dbusservice["/Session/Time"], 30)
        self.assertEqual(service._dbusservice["/Session/Energy"], 0.4)
        self.assertEqual(service._dbusservice["/Ac/Energy/Forward"], 0.4)
        self.assertEqual(service._dbusservice["/Ac/L1/Energy/Forward"], 0.4)
        self.assertEqual(service.virtual_mode, 2)

    def test_update_virtual_state_keeps_startstop_enabled_in_auto_while_waiting(self):
        service = ShellyWallboxService.__new__(ShellyWallboxService)
        service.charging_started_at = None
        service.energy_at_start = 0.0
        service.last_status = 0
        service.virtual_mode = 1
        service.virtual_autostart = 1
        service.virtual_startstop = 0
        service.virtual_enable = 1
        service.virtual_set_current = 16.0
        service.min_current = 6.0
        service.max_current = 16.0
        service._dbusservice = {}

        service._update_virtual_state(4, 1.23, False)

        self.assertEqual(service._dbusservice["/Status"], 4)
        self.assertEqual(service._dbusservice["/StartStop"], 1)
        self.assertEqual(service._dbusservice["/Enable"], 1)

    def test_update_virtual_state_keeps_startstop_visible_in_auto_when_relay_is_on(self):
        service = ShellyWallboxService.__new__(ShellyWallboxService)
        service.charging_started_at = None
        service.energy_at_start = 0.0
        service.last_status = 0
        service.virtual_mode = 1
        service.virtual_autostart = 1
        service.virtual_startstop = 0
        service.virtual_enable = 0
        service.virtual_set_current = 16.0
        service.min_current = 6.0
        service.max_current = 16.0
        service._dbusservice = {}

        service._update_virtual_state(2, 1.23, True)

        self.assertEqual(service._dbusservice["/Status"], 2)
        self.assertEqual(service._dbusservice["/StartStop"], 1)
        self.assertEqual(service._dbusservice["/Enable"], 0)

    def test_auto_mode_starts_after_surplus_delay(self):
        service = ShellyWallboxService.__new__(ShellyWallboxService)
        service.virtual_mode = 1
        service.virtual_autostart = 1
        service.virtual_enable = 1
        service.auto_min_soc = 30
        service.auto_resume_soc = 33
        service.auto_start_surplus_watts = 2000
        service.auto_stop_surplus_watts = 1600
        service.auto_average_window_seconds = 30
        service.auto_min_runtime_seconds = 300
        service.auto_min_offtime_seconds = 120
        service.auto_start_max_grid_import_watts = 50
        service.auto_stop_grid_import_watts = 300
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
            self.assertTrue(service._auto_decide_relay(False, 2200, 45, -2200))

    def test_auto_mode_stops_after_import_delay(self):
        service = ShellyWallboxService.__new__(ShellyWallboxService)
        service.virtual_mode = 1
        service.virtual_autostart = 1
        service.virtual_enable = 1
        service.auto_min_soc = 30
        service.auto_resume_soc = 33
        service.auto_start_surplus_watts = 2000
        service.auto_stop_surplus_watts = 1600
        service.auto_average_window_seconds = 30
        service.auto_min_runtime_seconds = 300
        service.auto_min_offtime_seconds = 120
        service.auto_start_max_grid_import_watts = 50
        service.auto_stop_grid_import_watts = 300
        service.auto_start_delay_seconds = 10
        service.auto_stop_delay_seconds = 30
        service.auto_start_condition_since = None
        service.auto_stop_condition_since = 200.0
        service.auto_samples = deque([(205.0, 1500.0, 400.0)])
        service.relay_last_changed_at = -100.0
        service.relay_last_off_at = None
        service.started_at = 0.0
        service.auto_startup_warmup_seconds = 0.0
        service.manual_override_until = 0.0

        with unittest.mock.patch("venus_evcharger_service.time.time", return_value=231.0):
            self.assertFalse(service._auto_decide_relay(True, 1500, 45, 400))

    def test_auto_mode_stops_running_charge_when_grid_value_missing_for_too_long(self):
        service = ShellyWallboxService.__new__(ShellyWallboxService)
        service.virtual_mode = 1
        service.virtual_autostart = 1
        service.virtual_enable = 1
        service.auto_allow_without_battery_soc = False
        service.auto_min_soc = 30
        service.auto_resume_soc = 33
        service.auto_start_surplus_watts = 2000
        service.auto_stop_surplus_watts = 1600
        service.auto_average_window_seconds = 30
        service.auto_min_runtime_seconds = 0
        service.auto_min_offtime_seconds = 120
        service.auto_start_max_grid_import_watts = 50
        service.auto_stop_grid_import_watts = 300
        service.auto_start_delay_seconds = 10
        service.auto_stop_delay_seconds = 30
        service.auto_grid_missing_stop_seconds = 60
        service.auto_start_condition_since = None
        service.auto_stop_condition_since = None
        service.auto_samples = deque()
        service.relay_last_changed_at = None
        service.relay_last_off_at = None
        service.started_at = 0.0
        service.auto_startup_warmup_seconds = 0.0
        service.manual_override_until = 0.0
        service._last_grid_at = 100.0

        with unittest.mock.patch("venus_evcharger_service.time.time", return_value=161.0):
            self.assertTrue(service._auto_decide_relay(True, 0.0, 45.0, None))
        with unittest.mock.patch("venus_evcharger_service.time.time", return_value=192.0):
            self.assertFalse(service._auto_decide_relay(True, 0.0, 45.0, None))
        self.assertEqual(service._last_health_reason, "grid-missing")

    def test_auto_mode_resumes_normal_start_logic_when_grid_value_is_fresh_again(self):
        service = ShellyWallboxService.__new__(ShellyWallboxService)
        service.virtual_mode = 1
        service.virtual_autostart = 1
        service.virtual_enable = 1
        service.auto_allow_without_battery_soc = False
        service.auto_min_soc = 30
        service.auto_resume_soc = 33
        service.auto_start_surplus_watts = 2000
        service.auto_stop_surplus_watts = 1600
        service.auto_average_window_seconds = 30
        service.auto_min_runtime_seconds = 300
        service.auto_min_offtime_seconds = 120
        service.auto_start_max_grid_import_watts = 50
        service.auto_stop_grid_import_watts = 300
        service.auto_start_delay_seconds = 10
        service.auto_stop_delay_seconds = 30
        service.auto_grid_missing_stop_seconds = 60
        service.auto_start_condition_since = 100.0
        service.auto_stop_condition_since = None
        service.auto_samples = deque([(105.0, 2200.0, -2200.0)])
        service.relay_last_changed_at = None
        service.relay_last_off_at = None
        service.started_at = 0.0
        service.auto_startup_warmup_seconds = 0.0
        service.manual_override_until = 0.0
        service._last_grid_at = 110.0

        with unittest.mock.patch("venus_evcharger_service.time.time", return_value=111.0):
            self.assertTrue(service._auto_decide_relay(False, 2200, 45, -2200))

    def test_auto_mode_forces_relay_off_when_disabled(self):
        service = ShellyWallboxService.__new__(ShellyWallboxService)
        service.virtual_mode = 1
        service.virtual_autostart = 1
        service.virtual_enable = 0
        service.auto_start_condition_since = 100.0
        service.auto_stop_condition_since = 200.0
        service.auto_samples = deque([(205.0, 2200.0, -2200.0)])

        with unittest.mock.patch("venus_evcharger_service.time.time", return_value=231.0):
            self.assertFalse(service._auto_decide_relay(True, 2200, 45, -2200))

        self.assertEqual(service._last_health_reason, "disabled")
        self.assertIsNone(service.auto_start_condition_since)
        self.assertIsNone(service.auto_stop_condition_since)
        self.assertEqual(len(service.auto_samples), 0)

    def test_manual_mode_ignores_auto_enable_disable_state(self):
        service = ShellyWallboxService.__new__(ShellyWallboxService)
        service.virtual_mode = 0
        service.virtual_autostart = 1
        service.virtual_enable = 0
        service.auto_start_condition_since = 100.0
        service.auto_stop_condition_since = 200.0
        service.auto_samples = deque([(205.0, 2200.0, -2200.0)])

        with unittest.mock.patch("venus_evcharger_service.time.time", return_value=231.0):
            self.assertTrue(service._auto_decide_relay(True, 2200, 45, -2200))

        self.assertIsNone(service.auto_start_condition_since)
        self.assertIsNone(service.auto_stop_condition_since)
        self.assertEqual(len(service.auto_samples), 0)

    def test_auto_mode_does_not_start_below_min_soc(self):
        service = ShellyWallboxService.__new__(ShellyWallboxService)
        service.virtual_mode = 1
        service.virtual_autostart = 1
        service.auto_allow_without_battery_soc = False
        service.auto_min_soc = 30
        service.auto_resume_soc = 33
        service.auto_start_surplus_watts = 2000
        service.auto_stop_surplus_watts = 1600
        service.auto_average_window_seconds = 30
        service.auto_min_runtime_seconds = 300
        service.auto_min_offtime_seconds = 120
        service.auto_start_max_grid_import_watts = 50
        service.auto_stop_grid_import_watts = 300
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
            self.assertFalse(service._auto_decide_relay(False, 2200, 25, -2200))

    def test_auto_mode_allows_missing_battery_soc_when_enabled(self):
        service = ShellyWallboxService.__new__(ShellyWallboxService)
        service.virtual_mode = 1
        service.virtual_autostart = 1
        service.auto_allow_without_battery_soc = True
        service.auto_min_soc = 30
        service.auto_resume_soc = 33
        service.auto_start_surplus_watts = 2000
        service.auto_stop_surplus_watts = 1600
        service.auto_average_window_seconds = 30
        service.auto_min_runtime_seconds = 300
        service.auto_min_offtime_seconds = 120
        service.auto_start_max_grid_import_watts = 50
        service.auto_stop_grid_import_watts = 300
        service.auto_start_delay_seconds = 10
        service.auto_stop_delay_seconds = 30
        service.auto_start_condition_since = 100.0
        service.auto_stop_condition_since = None
        service.auto_samples = deque([(105.0, 2200.0, -2200.0)])
        service.relay_last_changed_at = None
        service.relay_last_off_at = None
        service._last_battery_allow_warning = None
        service.auto_battery_scan_interval_seconds = 60
        service.started_at = 0.0
        service.auto_startup_warmup_seconds = 0.0
        service.manual_override_until = 0.0

        with unittest.mock.patch("venus_evcharger_service.time.time", return_value=111.0):
            self.assertTrue(service._auto_decide_relay(False, 2200, None, -2200))

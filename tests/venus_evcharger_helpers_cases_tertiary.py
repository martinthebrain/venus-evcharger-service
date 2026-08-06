# SPDX-License-Identifier: GPL-3.0-or-later
from tests.venus_evcharger_helpers_support import *


class TestShellyWallboxHelpersTertiary(ShellyWallboxHelpersTestBase):
    def test_worker_apply_pending_relay_command_runs_in_worker_thread(self):
        service = make_helper_service()
        service.poll_interval_ms = 1000
        service.pm_id = 0
        service.auto_shelly_soft_fail_seconds = 10
        service._warning_state = {}
        service._error_state = {
            "dbus": 0,
            "shelly": 0,
            "pv": 0,
            "battery": 0,
            "grid": 0,
            "cache_hits": 0,
        }
        service._failure_active = {
            "dbus": False,
            "shelly": False,
            "pv": False,
            "battery": False,
            "grid": False,
        }
        service._source_retry_after = {}
        service._worker_session = MagicMock()
        service.controllers.runtime.shelly.requests.rpc_call_with_session = MagicMock(return_value={"was_on": False})
        service.controllers.runtime.shelly.worker.publish_local_pm_status = MagicMock()
        service.controllers.runtime.auto.mark_relay_changed = MagicMock()

        with unittest.mock.patch("venus_evcharger_service.time.time", return_value=100.0):
            service.runtime.queue_relay_command(True, 100.0)
            service.runtime.apply_pending_relay_command()

        service.controllers.runtime.shelly.requests.rpc_call_with_session.assert_called_once_with(
            service._worker_session,
            "Switch.Set",
            id=0,
            on=True,
        )
        service.controllers.runtime.shelly.worker.publish_local_pm_status.assert_called_once_with(True, 100.0)
        service.controllers.runtime.auto.mark_relay_changed.assert_called_once_with(True, 100.0)
        self.assertEqual(service.runtime.pending_relay_command(), (None, None))

    def test_phase_values_single_phase(self):
        values = phase_values(3680, 230, "L1", "phase")
        self.assertAlmostEqual(values["L1"]["power"], 3680)
        self.assertAlmostEqual(values["L1"]["current"], 16)
        self.assertEqual(values["L2"]["power"], 0.0)
        self.assertEqual(values["L3"]["power"], 0.0)

    def test_phase_values_three_phase_with_line_voltage(self):
        values = phase_values(6900, 400, "3P", "line")
        self.assertAlmostEqual(values["L1"]["voltage"], 400 / (3 ** 0.5))
        self.assertAlmostEqual(values["L2"]["voltage"], 400 / (3 ** 0.5))
        self.assertAlmostEqual(values["L3"]["voltage"], 400 / (3 ** 0.5))

    def test_phase_values_three_phase_with_phase_voltage(self):
        values = phase_values(6900, 230, "3P", "phase")
        self.assertAlmostEqual(values["L1"]["power"], 2300)
        self.assertAlmostEqual(values["L2"]["power"], 2300)
        self.assertAlmostEqual(values["L3"]["power"], 2300)
        self.assertAlmostEqual(values["L1"]["current"], 10)
        self.assertAlmostEqual(values["L2"]["current"], 10)
        self.assertAlmostEqual(values["L3"]["current"], 10)

    def test_rpc_call_bool_is_lowercase_query_param(self):
        service = make_helper_service()
        service.host = "192.0.2.76"
        service.controllers.runtime.shelly.requests.request = MagicMock(return_value={"was_on": True})

        service.runtime.rpc_call("Switch.Set", id=0, on=False)

        service.controllers.runtime.shelly.requests.request.assert_called_once_with(
            "http://192.0.2.76/rpc/Switch.Set?id=0&on=false"
        )

    def test_handle_write_startstop_switches_relay_and_updates_dbus(self):
        service = make_helper_service()
        service.virtual_mode = 0
        service.virtual_autostart = 1
        service.virtual_startstop = 0
        service.virtual_enable = 0
        service.virtual_set_current = 16.0
        service.max_current = 16.0
        service.min_current = 6.0
        service.auto_manual_override_seconds = 300
        service.manual_override_until = 0
        service._dbusservice = {"/StartStop": 0, "/Enable": 0}
        service.controllers.runtime.shelly.queue_relay_command = MagicMock()
        service.controllers.runtime.shelly.publish_local_pm_status = MagicMock()

        with unittest.mock.patch("venus_evcharger_service.time.time", return_value=100.0):
            result = handle_control_surface_write(service, "set_start_stop", "start_stop", 1)

        self.assertTrue(result)
        service.controllers.runtime.shelly.queue_relay_command.assert_called_once_with(True, 100.0)
        service.controllers.runtime.shelly.publish_local_pm_status.assert_called_once_with(True, 100.0)
        self.assertEqual(service.virtual_startstop, 1)
        self.assertEqual(service.virtual_enable, 1)
        self.assertEqual(service._dbusservice["/StartStop"], 1)
        self.assertEqual(service._dbusservice["/Enable"], 1)
        self.assertEqual(service.manual_override_until, 400.0)

    def test_handle_write_enable_in_manual_mode_switches_relay_and_updates_dbus(self):
        service = make_helper_service()
        service.virtual_mode = 0
        service.virtual_autostart = 1
        service.virtual_startstop = 0
        service.virtual_enable = 0
        service.virtual_set_current = 16.0
        service.max_current = 16.0
        service.min_current = 6.0
        service.auto_manual_override_seconds = 300
        service.manual_override_until = 0
        service._dbusservice = {"/StartStop": 0, "/Enable": 0}
        service.controllers.runtime.shelly.queue_relay_command = MagicMock()
        service.controllers.runtime.shelly.publish_local_pm_status = MagicMock()

        with unittest.mock.patch("venus_evcharger_service.time.time", return_value=100.0):
            result = handle_control_surface_write(service, "set_enable", "enable", 1)

        self.assertTrue(result)
        service.controllers.runtime.shelly.queue_relay_command.assert_called_once_with(True, 100.0)
        service.controllers.runtime.shelly.publish_local_pm_status.assert_called_once_with(True, 100.0)
        self.assertEqual(service.virtual_startstop, 1)
        self.assertEqual(service.virtual_enable, 1)
        self.assertEqual(service._dbusservice["/StartStop"], 1)
        self.assertEqual(service._dbusservice["/Enable"], 1)
        self.assertEqual(service.manual_override_until, 400.0)

    def test_handle_write_enable_in_auto_mode_does_not_force_relay_on(self):
        service = make_helper_service()
        service.virtual_mode = 1
        service.virtual_autostart = 1
        service.virtual_startstop = 0
        service.virtual_enable = 0
        service.virtual_set_current = 16.0
        service.max_current = 16.0
        service.min_current = 6.0
        service.auto_manual_override_seconds = 300
        service.manual_override_until = 0
        service._dbusservice = {"/StartStop": 0, "/Enable": 0}
        service.controllers.runtime.shelly.queue_relay_command = MagicMock()

        with unittest.mock.patch("venus_evcharger_service.time.time", return_value=100.0):
            result = handle_control_surface_write(service, "set_enable", "enable", 1)

        self.assertTrue(result)
        service.controllers.runtime.shelly.queue_relay_command.assert_not_called()
        self.assertEqual(service.virtual_startstop, 0)
        self.assertEqual(service.virtual_enable, 1)
        self.assertEqual(service._dbusservice["/StartStop"], 1)
        self.assertEqual(service._dbusservice["/Enable"], 1)
        self.assertEqual(service.manual_override_until, 0)

    def test_handle_write_startstop_in_auto_mode_does_not_force_relay_on(self):
        service = make_helper_service()
        service.virtual_mode = 1
        service.virtual_autostart = 1
        service.virtual_startstop = 0
        service.virtual_enable = 0
        service.virtual_set_current = 16.0
        service.max_current = 16.0
        service.min_current = 6.0
        service.auto_manual_override_seconds = 300
        service.manual_override_until = 0
        service._dbusservice = {"/StartStop": 0, "/Enable": 0}
        service.controllers.runtime.shelly.queue_relay_command = MagicMock()

        with unittest.mock.patch("venus_evcharger_service.time.time", return_value=100.0):
            result = handle_control_surface_write(service, "set_start_stop", "start_stop", 1)

        self.assertTrue(result)
        service.controllers.runtime.shelly.queue_relay_command.assert_not_called()
        self.assertEqual(service.virtual_startstop, 0)
        self.assertEqual(service.virtual_enable, 1)
        self.assertEqual(service._dbusservice["/StartStop"], 1)
        self.assertEqual(service._dbusservice["/Enable"], 1)
        self.assertEqual(service.manual_override_until, 0)

    def test_handle_write_mode_updates_dbus_immediately(self):
        service = make_helper_service()
        service.virtual_mode = 1
        service.virtual_autostart = 1
        service.virtual_startstop = 0
        service.virtual_enable = 1
        service.auto_start_condition_since = 100.0
        service.auto_stop_condition_since = 200.0
        service.auto_samples = deque([(205.0, 2200.0, -2200.0)])
        service._dbusservice = {"/Mode": 1, "/StartStop": 1}

        result = handle_control_surface_write(service, "set_mode", "mode", 0)

        self.assertTrue(result)
        self.assertEqual(service.virtual_mode, 0)
        self.assertEqual(service._dbusservice["/Mode"], 0)
        self.assertEqual(service.virtual_startstop, 1)
        self.assertEqual(service._dbusservice["/StartStop"], 1)
        self.assertIsNone(service.auto_start_condition_since)
        self.assertIsNone(service.auto_stop_condition_since)
        self.assertEqual(len(service.auto_samples), 0)

    def test_handle_write_mode_updates_startstop_display_when_switching_to_auto(self):
        service = make_helper_service()
        service.virtual_mode = 0
        service.virtual_autostart = 1
        service.virtual_startstop = 0
        service.virtual_enable = 1
        service.auto_start_condition_since = None
        service.auto_stop_condition_since = None
        service.auto_samples = deque()
        service._dbusservice = {"/Mode": 0, "/StartStop": 0, "/Enable": 1}

        result = handle_control_surface_write(service, "set_mode", "mode", 1)

        self.assertTrue(result)
        self.assertEqual(service.virtual_mode, 1)
        self.assertEqual(service._dbusservice["/Mode"], 1)
        self.assertEqual(service._dbusservice["/StartStop"], 1)
        self.assertEqual(service._dbusservice["/Enable"], 1)

    def test_handle_write_mode_preserves_scheduled_value_and_uses_auto_display(self):
        service = make_helper_service()
        service.virtual_mode = 0
        service.virtual_autostart = 1
        service.virtual_startstop = 0
        service.virtual_enable = 1
        service.auto_start_condition_since = None
        service.auto_stop_condition_since = None
        service.auto_samples = deque()
        service._dbusservice = {"/Mode": 0, "/StartStop": 0, "/Enable": 1}

        result = handle_control_surface_write(service, "set_mode", "mode", 2)

        self.assertTrue(result)
        self.assertEqual(service.virtual_mode, 2)
        self.assertEqual(service._dbusservice["/Mode"], 2)
        self.assertEqual(service._dbusservice["/StartStop"], 1)
        self.assertEqual(service._dbusservice["/Enable"], 1)

    def test_handle_write_mode_switching_from_manual_to_auto_queues_clean_cutover(self):
        service = make_helper_service()
        service.virtual_mode = 0
        service.virtual_autostart = 1
        service.virtual_startstop = 1
        service.virtual_enable = 0
        service.auto_manual_override_seconds = 300
        service.manual_override_until = 500.0
        service.auto_start_condition_since = None
        service.auto_stop_condition_since = None
        service.auto_samples = deque()
        service.relay_last_changed_at = 100.0
        service._dbusservice = {"/Mode": 0, "/StartStop": 1, "/Enable": 0}
        service.controllers.runtime.shelly.queue_relay_command = MagicMock()
        service.controllers.runtime.shelly.publish_local_pm_status = MagicMock()

        with unittest.mock.patch("venus_evcharger_service.time.time", return_value=200.0):
            result = handle_control_surface_write(service, "set_mode", "mode", 1)

        self.assertTrue(result)
        self.assertEqual(service.virtual_mode, 1)
        self.assertEqual(service.virtual_enable, 1)
        self.assertEqual(service.virtual_startstop, 0)
        self.assertEqual(service._dbusservice["/Mode"], 1)
        self.assertEqual(service._dbusservice["/StartStop"], 1)
        self.assertEqual(service._dbusservice["/Enable"], 1)
        self.assertEqual(service.manual_override_until, 0.0)
        self.assertTrue(service._auto_mode_cutover_pending)
        self.assertFalse(service._ignore_min_offtime_once)
        service.controllers.runtime.shelly.queue_relay_command.assert_called_once_with(False, 200.0)
        service.controllers.runtime.shelly.publish_local_pm_status.assert_called_once_with(False, 200.0)

    def test_auto_mode_waits_for_cutover_off_before_restarting(self):
        service = make_helper_service()
        service.virtual_mode = 1
        service.virtual_autostart = 1
        service.virtual_startstop = 0
        service.virtual_enable = 1
        service.auto_allow_without_battery_soc = False
        configure_auto_policy(service, start_surplus_watts=2300.0)
        service.auto_average_window_seconds = 30
        service.auto_min_runtime_seconds = 300
        service.auto_min_offtime_seconds = 120
        service.auto_start_delay_seconds = 10
        service.auto_stop_delay_seconds = 30
        service.auto_grid_missing_stop_seconds = 60
        service.started_at = 0.0
        service.auto_startup_warmup_seconds = 0.0
        service.manual_override_until = 0.0
        service.auto_start_condition_since = None
        service.auto_stop_condition_since = None
        service.auto_samples = deque([(105.0, 2400.0, -2400.0)])
        service.relay_last_changed_at = None
        service.relay_last_off_at = 200.0
        service._auto_mode_cutover_pending = True
        service._ignore_min_offtime_once = False
        service._last_health_reason = "init"
        service._last_health_code = 0
        service._last_grid_at = 110.0
        service.controllers.runtime.shelly.peek_pending_relay_command = MagicMock(return_value=(False, 200.0))

        with unittest.mock.patch("venus_evcharger_service.time.time", return_value=231.0):
            self.assertFalse(service.auto.decide_relay(False, 2400.0, 45.0, -2400.0))

        self.assertEqual(service._last_health_reason, "mode-transition")
        self.assertTrue(service._auto_mode_cutover_pending)

    def test_auto_mode_can_restart_after_cutover_without_waiting_min_offtime(self):
        service = make_helper_service()
        service.virtual_mode = 1
        service.virtual_autostart = 1
        service.virtual_startstop = 0
        service.virtual_enable = 1
        service.auto_allow_without_battery_soc = False
        configure_auto_policy(service, start_surplus_watts=2300.0)
        service.auto_average_window_seconds = 30
        service.auto_min_runtime_seconds = 300
        service.auto_min_offtime_seconds = 120
        service.auto_start_delay_seconds = 10
        service.auto_stop_delay_seconds = 30
        service.auto_grid_missing_stop_seconds = 60
        service.started_at = 0.0
        service.auto_startup_warmup_seconds = 0.0
        service.manual_override_until = 0.0
        service.auto_start_condition_since = 100.0
        service.auto_stop_condition_since = None
        service.auto_samples = deque([(105.0, 2400.0, -2400.0)])
        service.relay_last_changed_at = None
        service.relay_last_off_at = 200.0
        service._auto_mode_cutover_pending = True
        service._ignore_min_offtime_once = False
        service._last_health_reason = "init"
        service._last_health_code = 0
        service._last_grid_at = 110.0
        service._last_pm_status_confirmed = True
        service._last_confirmed_pm_status = {"output": False}
        service._last_confirmed_pm_status_at = 110.5
        service._relay_sync_requested_at = 110.0
        service.controllers.runtime.shelly.peek_pending_relay_command = MagicMock(return_value=(None, None))

        with unittest.mock.patch("venus_evcharger_service.time.time", return_value=111.0):
            self.assertFalse(service.auto.decide_relay(False, 2400.0, 45.0, -2400.0))
        self.assertFalse(service._auto_mode_cutover_pending)
        self.assertTrue(service._ignore_min_offtime_once)

        with unittest.mock.patch("venus_evcharger_service.time.time", return_value=122.0):
            self.assertTrue(service.auto.decide_relay(False, 2400.0, 45.0, -2400.0))

        self.assertFalse(service._ignore_min_offtime_once)

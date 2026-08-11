# SPDX-License-Identifier: GPL-3.0-or-later
from tests.venus_evcharger_helpers_support import *


class TestShellyWallboxHelpersSeptenary(ShellyWallboxHelpersTestBase):
    def test_stale_helper_snapshot_prevents_auto_start_and_marks_grid_missing(self):
        service = self._make_update_service()
        service.auto_allow_without_battery_soc = True
        configure_auto_policy(
            service,
            start_surplus_watts=1500.0,
            stop_surplus_watts=1100.0,
        )
        service.auto_average_window_seconds = 30.0
        service.auto_min_runtime_seconds = 300.0
        service.auto_min_offtime_seconds = 120.0
        service.auto_start_delay_seconds = 10.0
        service.auto_stop_delay_seconds = 30.0
        service.auto_allow_without_battery_soc = True
        service.controllers.runtime.shelly.queue_relay_command = MagicMock()
        self._set_worker_snapshot(
            service,
            captured_at=125.0,
            pm_captured_at=125.0,
            pm_confirmed=True,
            pm_status={
                "output": False,
                "apower": 0.0,
                "voltage": 230.0,
                "current": 0.0,
                "aenergy": {"total": 1000.0},
            },
        )
        helper_snapshot = valid_snapshot(
            pv_power=2600.0,
            battery_soc=58.0,
            grid_power=-2200.0,
            helper_generation=0,
        )
        stat_result = MagicMock()
        stat_result.st_mtime_ns = 7

        with unittest.mock.patch("venus_evcharger_service.time.time", return_value=130.0):
            with unittest.mock.patch(
                "venus_evcharger.inputs.supervisor_snapshot_runtime.os.stat",
                return_value=stat_result,
            ):
                with unittest.mock.patch(
                    "venus_evcharger.inputs.supervisor_snapshot_runtime.Path.read_text",
                    return_value=json.dumps(helper_snapshot),
                ):
                    self.assertTrue(service.update.update())

        service.controllers.runtime.shelly.queue_relay_command.assert_not_called()
        self.assertEqual(service._last_health_reason, "grid-missing")
        self.assertEqual(service._dbusservice["/Auto/Health"], "grid-missing")
        self.assertEqual(service._dbusservice["/Status"], 4)

    def test_update_shelly_offline_does_not_double_count_worker_errors(self):
        service = self._make_update_service()
        service.virtual_mode = 0
        service.virtual_startstop = 1
        service._last_pm_status = None
        service._last_pm_status_at = None
        service._error_state["shelly"] = 0

        with unittest.mock.patch("venus_evcharger_service.time.time", return_value=100.0):
            self.assertTrue(service.update.update())

        self.assertEqual(service._error_state["shelly"], 0)
        self.assertEqual(service._dbusservice["/Auto/ShellyReadErrors"], 0)
        self.assertEqual(service._dbusservice["/Auto/ErrorCount"], 0)
        self.assertEqual(service._dbusservice["/Auto/LastShellyReadAge"], -1)
        self.assertEqual(service._dbusservice["/Auto/Health"], "shelly-offline")

    def test_update_keeps_live_measurements_when_auto_switch_command_fails(self):
        service = self._make_update_service()
        service.virtual_mode = 1
        service.virtual_startstop = 1
        service.virtual_enable = 1
        self._set_worker_snapshot(service, captured_at=100.0, pm_confirmed=True, pm_status={
            "output": True,
            "apower": 1980.0,
            "voltage": 230.0,
            "current": 8.6,
            "aenergy": {"total": 1000.0},
        }, pv_power=0.0, battery_soc=50.0, grid_power=500.0, auto_mode_active=True)
        service.controllers.runtime.auto.auto_decide_relay = MagicMock(return_value=False)
        service.controllers.runtime.shelly.queue_relay_command = MagicMock(side_effect=RuntimeError("switch failed"))

        with unittest.mock.patch("venus_evcharger_service.time.time", return_value=100.0):
            self.assertTrue(service.update.update())

        service.controllers.runtime.shelly.queue_relay_command.assert_called_once_with(False, 100.0)
        self.assertEqual(service._dbusservice["/Ac/Power"], 1980.0)
        self.assertEqual(service._dbusservice["/Ac/Voltage"], 230.0)
        self.assertEqual(service._dbusservice["/Status"], 2)
        self.assertEqual(service._dbusservice["/Auto/ShellyReadErrors"], 1)

    def test_update_applies_startup_manual_target_from_config(self):
        service = self._make_update_service()
        service.virtual_mode = 0
        service.virtual_startstop = 1
        service.virtual_enable = 1
        service._startup_manual_target = True
        self._set_worker_snapshot(service, captured_at=100.0, pm_confirmed=True, pm_status={
            "output": False,
            "apower": 0.0,
            "voltage": 230.0,
            "current": 0.0,
            "aenergy": {"total": 1000.0},
        })
        service.controllers.runtime.shelly.queue_relay_command = MagicMock()

        with unittest.mock.patch("venus_evcharger_service.time.time", return_value=100.0):
            self.assertTrue(service.update.update())

        service.controllers.runtime.shelly.queue_relay_command.assert_called_once_with(True, 100.0)
        self.assertEqual(service._dbusservice["/Mode"], 0)
        self.assertEqual(service._dbusservice["/StartStop"], 1)
        self.assertEqual(service._dbusservice["/Enable"], 1)
        self.assertIsNone(service._startup_manual_target)

    def test_update_uses_worker_snapshot_instead_of_direct_blocking_reads(self):
        service = self._make_update_service()
        self._set_worker_snapshot(service, captured_at=100.0, pm_confirmed=True, pm_status={
            "output": True,
            "apower": 1800.0,
            "voltage": 230.0,
            "current": 7.8,
            "aenergy": {"total": 1000.0},
        }, pv_power=2200.0, battery_soc=55.0, grid_power=-2000.0, auto_mode_active=True)
        service.fetch_pm_status = MagicMock(side_effect=AssertionError("main loop must not poll Shelly directly"))
        service.controllers.runtime.auto.auto_decide_relay = MagicMock(return_value=True)

        with unittest.mock.patch("venus_evcharger_service.time.time", return_value=100.0):
            self.assertTrue(service.update.update())

        service.controllers.runtime.auto.auto_decide_relay.assert_called_once_with(True, 2200.0, 55.0, -2000.0)

    def test_watchdog_marks_update_as_stale(self):
        service = self._make_update_service()
        service._last_successful_update_at = 100.0
        service.started_at = 0.0
        service.auto_watchdog_stale_seconds = 30.0
        service._dbusservice = {}

        with unittest.mock.patch("venus_evcharger_service.time.time", return_value=150.0):
            service.controllers.runtime.update.components.state.update_virtual_state(6, 0.0, False)

        self.assertEqual(service._dbusservice["/Auto/Stale"], 1)
        self.assertEqual(service._dbusservice["/Auto/StaleSeconds"], 50)
        self.assertEqual(service._dbusservice["/Auto/LastSuccessfulUpdateAge"], 50)

    def test_watchdog_can_be_disabled_with_zero_stale_seconds(self):
        service = self._make_update_service()
        service._last_successful_update_at = None
        service.started_at = 0.0
        service.auto_watchdog_stale_seconds = 0.0
        service.controllers.runtime.auto_input.refresh_snapshot = MagicMock()

        self.assertFalse(service.runtime.update_is_stale(500.0))
        service.runtime.recover_watchdog(500.0)

        self.assertEqual(service._recovery_attempts, 0)
        service.controllers.runtime.auto_input.refresh_snapshot.assert_not_called()

    def test_watchdog_age_uses_zero_timestamp_as_valid_success_time(self):
        service = self._make_update_service()
        service._last_successful_update_at = 0.0
        service.started_at = 10.0
        service.auto_watchdog_stale_seconds = 30.0
        service._dbusservice = {}

        with unittest.mock.patch("venus_evcharger_service.time.time", return_value=50.0):
            service.controllers.runtime.update.components.state.update_virtual_state(6, 0.0, False)

        self.assertEqual(service._dbusservice["/Auto/LastSuccessfulUpdateAge"], 50)
        self.assertEqual(service._dbusservice["/Auto/StaleSeconds"], 50)

    def test_watchdog_recovery_reloads_semantic_input_snapshot(self):
        service = self._make_update_service()
        service._last_successful_update_at = 100.0
        service.started_at = 0.0
        service.auto_watchdog_stale_seconds = 30.0
        service.auto_watchdog_recovery_seconds = 60.0
        service.controllers.runtime.auto_input.refresh_snapshot = MagicMock()

        service.runtime.recover_watchdog(200.0)

        self.assertEqual(service._recovery_attempts, 1)
        self.assertEqual(service._last_recovery_attempt_at, 200.0)
        service.controllers.runtime.auto_input.refresh_snapshot.assert_called_once_with(None)

    def test_auto_daytime_window(self):
        service = make_helper_service()
        service.auto_daytime_only = True
        service.auto_month_windows = {
            1: ((9, 0), (16, 30)),
            7: ((7, 0), (21, 0)),
        }

        self.assertFalse(service.controllers.runtime.auto.is_within_auto_daytime_window(datetime(2026, 1, 3, 8, 30)))
        self.assertTrue(service.controllers.runtime.auto.is_within_auto_daytime_window(datetime(2026, 1, 3, 10, 0)))
        self.assertFalse(service.controllers.runtime.auto.is_within_auto_daytime_window(datetime(2026, 7, 3, 6, 30)))
        self.assertTrue(service.controllers.runtime.auto.is_within_auto_daytime_window(datetime(2026, 7, 3, 20, 30)))

if __name__ == "__main__":
    unittest.main()

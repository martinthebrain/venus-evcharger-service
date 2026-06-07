# SPDX-License-Identifier: GPL-3.0-or-later
from tests.venus_evcharger_helpers_support import *


class TestShellyWallboxHelpersSenary(ShellyWallboxHelpersTestBase):
    def test_auto_mode_scenario_grid_recovery_gate_holds_restart_until_fresh_grid_window_elapsed(self):
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
        service.auto_min_offtime_seconds = 0
        service.auto_start_max_grid_import_watts = 50
        service.auto_stop_grid_import_watts = 300
        service.auto_grid_recovery_start_seconds = 10
        service.auto_start_delay_seconds = 0
        service.auto_stop_delay_seconds = 30
        service.auto_start_condition_since = None
        service.auto_stop_condition_since = None
        service.auto_stop_condition_reason = None
        service.auto_samples = deque()
        service.relay_last_changed_at = None
        service.relay_last_off_at = 89.0
        service.started_at = 0.0
        service.auto_startup_warmup_seconds = 0.0
        service.manual_override_until = 0.0
        service._grid_recovery_required = True
        service._grid_recovery_since = None

        with unittest.mock.patch("venus_evcharger_service.time.time", return_value=100.0):
            self.assertFalse(service._auto_decide_relay(False, 2400.0, 45.0, -2200.0))
        self.assertEqual(service._last_health_reason, "waiting-grid-recovery")
        self.assertEqual(service._grid_recovery_since, 100.0)

        with unittest.mock.patch("venus_evcharger_service.time.time", return_value=108.0):
            self.assertFalse(service._auto_decide_relay(False, 2400.0, 45.0, -2200.0))
        self.assertEqual(service._last_health_reason, "waiting-grid-recovery")
        self.assertTrue(service._grid_recovery_required)

        with unittest.mock.patch("venus_evcharger_service.time.time", return_value=111.0):
            self.assertFalse(service._auto_decide_relay(False, 2400.0, 45.0, -2200.0))
        self.assertEqual(service.auto_start_condition_since, 111.0)

        with unittest.mock.patch("venus_evcharger_service.time.time", return_value=112.0):
            self.assertTrue(service._auto_decide_relay(False, 2400.0, 45.0, -2200.0))
        self.assertEqual(service._last_health_reason, "auto-start")
        self.assertFalse(service._grid_recovery_required)

    def test_scheduled_mode_scenario_weekday_target_enters_night_boost_but_disabled_day_does_not(self):
        service = ShellyWallboxService.__new__(ShellyWallboxService)
        service.virtual_mode = 2
        service.virtual_autostart = 1
        service.virtual_enable = 1
        service.auto_allow_without_battery_soc = False
        service.auto_min_soc = 30
        service.auto_resume_soc = 33
        service.auto_start_surplus_watts = 2000
        service.auto_stop_surplus_watts = 1600
        service.auto_average_window_seconds = 30
        service.auto_min_runtime_seconds = 0
        service.auto_min_offtime_seconds = 0
        service.auto_start_max_grid_import_watts = 50
        service.auto_stop_grid_import_watts = 300
        service.auto_start_delay_seconds = 10
        service.auto_stop_delay_seconds = 30
        service.auto_start_condition_since = None
        service.auto_stop_condition_since = None
        service.auto_stop_condition_reason = None
        service.auto_samples = deque()
        service.relay_last_changed_at = None
        service.relay_last_off_at = None
        service.started_at = 0.0
        service.auto_startup_warmup_seconds = 0.0
        service.manual_override_until = 0.0
        service.auto_month_windows = {4: ((7, 30), (19, 30))}
        service.auto_scheduled_enabled_days = "Mon,Tue,Wed,Thu,Fri"
        service.auto_scheduled_night_start_delay_seconds = 3600.0
        service.auto_scheduled_latest_end_time = "06:30"

        with unittest.mock.patch("venus_evcharger_service.time.time", return_value=utc_timestamp(2026, 4, 19, 21, 0)):
            self.assertTrue(service._auto_decide_relay(False, None, None, None))
        self.assertEqual(service._last_health_reason, "scheduled-night-charge")

        with unittest.mock.patch("venus_evcharger_service.time.time", return_value=utc_timestamp(2026, 4, 17, 21, 0)):
            self.assertFalse(service._auto_decide_relay(False, 0.0, 45.0, 0.0))
        self.assertEqual(service._last_health_reason, "waiting-surplus")

    def test_scheduled_mode_scenario_latest_end_stops_night_boost_before_daytime_window_reopens(self):
        service = ShellyWallboxService.__new__(ShellyWallboxService)
        service.virtual_mode = 2
        service.virtual_autostart = 1
        service.virtual_enable = 1
        service.auto_allow_without_battery_soc = False
        service.auto_min_soc = 30
        service.auto_resume_soc = 33
        service.auto_start_surplus_watts = 2000
        service.auto_stop_surplus_watts = 1600
        service.auto_average_window_seconds = 30
        service.auto_min_runtime_seconds = 0
        service.auto_min_offtime_seconds = 0
        service.auto_start_max_grid_import_watts = 50
        service.auto_stop_grid_import_watts = 300
        service.auto_start_delay_seconds = 10
        service.auto_stop_delay_seconds = 30
        service.auto_start_condition_since = None
        service.auto_stop_condition_since = None
        service.auto_stop_condition_reason = None
        service.auto_samples = deque()
        service.relay_last_changed_at = None
        service.relay_last_off_at = None
        service.started_at = 0.0
        service.auto_startup_warmup_seconds = 0.0
        service.manual_override_until = 0.0
        service.auto_month_windows = {4: ((7, 30), (19, 30))}
        service.auto_scheduled_enabled_days = "Mon,Tue,Wed,Thu,Fri"
        service.auto_scheduled_night_start_delay_seconds = 3600.0
        service.auto_scheduled_latest_end_time = "06:30"

        with unittest.mock.patch("venus_evcharger_service.time.time", return_value=utc_timestamp(2026, 4, 20, 6, 15)):
            self.assertTrue(service._auto_decide_relay(False, None, None, None))
        self.assertEqual(service._last_health_reason, "scheduled-night-charge")

        with unittest.mock.patch("venus_evcharger_service.time.time", return_value=utc_timestamp(2026, 4, 20, 6, 45)):
            self.assertFalse(service._auto_decide_relay(False, 0.0, 45.0, 0.0))
        self.assertEqual(service._last_health_reason, "waiting-surplus")

    def test_auto_mode_sets_specific_waiting_reason_for_daytime(self):
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
        service.auto_start_condition_since = None
        service.auto_stop_condition_since = None
        service.auto_samples = deque()
        service.relay_last_changed_at = None
        service.relay_last_off_at = None
        service.started_at = 0.0
        service.auto_startup_warmup_seconds = 0.0
        service.manual_override_until = 0.0
        service._is_within_auto_daytime_window = MagicMock(return_value=False)

        with unittest.mock.patch("venus_evcharger_service.time.time", return_value=150.0):
            self.assertFalse(service._auto_decide_relay(False, 2200, 45, -2200))
        self.assertEqual(service._last_health_reason, "waiting-daytime")

    def test_update_uses_cached_inputs_and_updates_observability_paths(self):
        service = self._make_update_service()
        self._set_worker_snapshot(service, captured_at=100.0, pm_captured_at=100.0, pm_confirmed=True, pm_status={
            "output": False,
            "apower": 0.0,
            "voltage": 230.0,
            "current": 0.0,
            "aenergy": {"total": 1000.0},
        })
        service._auto_decide_relay = MagicMock(return_value=False)
        service._last_pv_value = 2400.0
        service._last_pv_at = 90.0
        service._last_grid_value = -2100.0
        service._last_grid_at = 91.0
        service._last_battery_soc_value = 56.0
        service._last_battery_soc_at = 92.0
        service._last_dbus_ok_at = 95.0

        with unittest.mock.patch("venus_evcharger_service.time.time", return_value=100.0):
            self.assertTrue(service._update())

        service._auto_decide_relay.assert_called_once_with(False, 2400.0, 56.0, -2100.0)
        self.assertEqual(service._error_state["cache_hits"], 1)
        self.assertEqual(service._dbusservice["/Auto/InputCacheHits"], 1)
        self.assertEqual(service._dbusservice["/Auto/LastPvReadAge"], 10)
        self.assertEqual(service._dbusservice["/Auto/LastBatteryReadAge"], 8)
        self.assertEqual(service._dbusservice["/Auto/LastGridReadAge"], 9)
        self.assertEqual(service._dbusservice["/Auto/LastDbusReadAge"], 5)

    def test_update_uses_source_specific_snapshot_timestamps_for_ages(self):
        service = self._make_update_service()
        self._set_worker_snapshot(
            service,
            captured_at=100.0,
            pm_captured_at=95.0,
            pm_confirmed=True,
            pm_status={
                "output": False,
                "apower": 0.0,
                "voltage": 230.0,
                "current": 0.0,
                "aenergy": {"total": 1000.0},
            },
            pv_power=2400.0,
            pv_captured_at=98.0,
            battery_soc=56.0,
            battery_captured_at=85.0,
            grid_power=-2100.0,
            grid_captured_at=96.0,
            auto_mode_active=True,
        )
        service._auto_decide_relay = MagicMock(return_value=False)

        with unittest.mock.patch("venus_evcharger_service.time.time", return_value=100.0):
            self.assertTrue(service._update())

        self.assertEqual(service._dbusservice["/Auto/LastShellyReadAge"], 5)
        self.assertEqual(service._dbusservice["/Auto/LastPvReadAge"], 2)
        self.assertEqual(service._dbusservice["/Auto/LastBatteryReadAge"], 15)
        self.assertEqual(service._dbusservice["/Auto/LastGridReadAge"], 4)

    def test_diagnostics_keep_last_confirmed_shelly_age_after_local_placeholder_publish(self):
        service = self._make_update_service()
        service._last_confirmed_pm_status = {"output": False}
        service._last_confirmed_pm_status_at = 95.0
        service._last_pm_status = {"output": False}
        service._last_pm_status_at = 95.0
        service._last_pm_status_confirmed = True

        service._publish_local_pm_status(True, 100.0)
        service._publish_diagnostic_paths(100.0)

        self.assertFalse(service._last_pm_status_confirmed)
        self.assertEqual(service._dbusservice["/Auto/LastShellyReadAge"], 5)

    def test_diagnostics_fall_back_to_last_confirmed_flagged_pm_age_when_no_confirmed_snapshot_is_stored(self):
        service = self._make_update_service()
        service._last_confirmed_pm_status = None
        service._last_confirmed_pm_status_at = None
        service._last_pm_status = {"output": False}
        service._last_pm_status_at = 95.0
        service._last_pm_status_confirmed = True

        service._publish_diagnostic_paths(100.0)

        self.assertEqual(service._dbusservice["/Auto/LastShellyReadAge"], 5)

    def test_diagnostics_normalize_invalid_auto_state_pair_before_publish(self):
        service = self._make_update_service()
        service._last_auto_state = "mystery"
        service._last_auto_state_code = 99

        service._publish_diagnostic_paths(100.0)

        self.assertEqual(service._dbusservice["/Auto/State"], "idle")
        self.assertEqual(service._dbusservice["/Auto/StateCode"], 0)

    def test_diagnostics_ignore_future_confirmed_shelly_timestamps(self):
        service = self._make_update_service()
        service._last_confirmed_pm_status = {"output": False}
        service._last_confirmed_pm_status_at = 105.0
        service._last_pm_status = {"output": False}
        service._last_pm_status_at = 106.0
        service._last_pm_status_confirmed = True

        service._publish_diagnostic_paths(100.0)

        self.assertEqual(service._dbusservice["/Auto/LastShellyReadAge"], -1)

    def test_diagnostics_publish_backend_mode_and_charger_target_visibility(self):
        service = self._make_update_service()
        service.config["Backends"]["Mode"] = "split"
        service.config["Backends"]["MeterType"] = "template_meter"
        service.config["Backends"]["SwitchType"] = "template_switch"
        service.config["Backends"]["ChargerType"] = "template_charger"
        service._error_state["charger"] = 2
        service._charger_target_current_amps = 13.0
        service._charger_target_current_applied_at = 96.0
        service._last_confirmed_pm_status = {"output": False, "_phase_selection": "P1"}
        service._phase_switch_mismatch_active = True
        service._phase_switch_lockout_selection = "P1_P2"
        service._phase_switch_lockout_reason = "mismatch-threshold"
        service._phase_switch_lockout_at = 91.0
        service._phase_switch_lockout_until = 150.0

        service._publish_diagnostic_paths(100.0)

        self.assertEqual(service._dbusservice["/Auto/BackendMode"], "split")
        self.assertEqual(service._dbusservice["/Auto/MeterBackend"], "template_meter")
        self.assertEqual(service._dbusservice["/Auto/SwitchBackend"], "template_switch")
        self.assertEqual(service._dbusservice["/Auto/ChargerBackend"], "template_charger")
        self.assertEqual(service._dbusservice["/Auto/RecoveryActive"], 0)
        self.assertEqual(service._dbusservice["/Auto/StatusSource"], "unknown")
        self.assertEqual(service._dbusservice["/Auto/FaultActive"], 0)
        self.assertEqual(service._dbusservice["/Auto/FaultReason"], "")
        self.assertEqual(service._dbusservice["/Auto/ChargerFaultActive"], 0)
        self.assertEqual(service._dbusservice["/Auto/ChargerWriteErrors"], 2)
        self.assertEqual(service._dbusservice["/Auto/ChargerCurrentTarget"], 13.0)
        self.assertEqual(service._dbusservice["/Auto/PhaseObserved"], "P1")
        self.assertEqual(service._dbusservice["/Auto/PhaseMismatchActive"], 1)
        self.assertEqual(service._dbusservice["/Auto/PhaseLockoutActive"], 1)
        self.assertEqual(service._dbusservice["/Auto/PhaseLockoutTarget"], "P1_P2")
        self.assertEqual(service._dbusservice["/Auto/PhaseLockoutReason"], "mismatch-threshold")
        self.assertEqual(service._dbusservice["/Auto/PhaseSupportedConfigured"], "P1")
        self.assertEqual(service._dbusservice["/Auto/PhaseSupportedEffective"], "P1")
        self.assertEqual(service._dbusservice["/Auto/PhaseDegradedActive"], 0)
        self.assertEqual(service._dbusservice["/Auto/SwitchFeedbackClosed"], -1)
        self.assertEqual(service._dbusservice["/Auto/SwitchInterlockOk"], -1)
        self.assertEqual(service._dbusservice["/Auto/SwitchFeedbackMismatch"], 0)
        self.assertEqual(service._dbusservice["/Auto/ContactorFaultCount"], 0)
        self.assertEqual(service._dbusservice["/Auto/ContactorLockoutActive"], 0)
        self.assertEqual(service._dbusservice["/Auto/ContactorLockoutReason"], "")
        self.assertEqual(service._dbusservice["/Auto/ContactorLockoutSource"], "")
        self.assertEqual(service._dbusservice["/Auto/PhaseLockoutAge"], 9)
        self.assertEqual(service._dbusservice["/Auto/ContactorLockoutAge"], -1)
        self.assertEqual(service._dbusservice["/Auto/LastSwitchFeedbackAge"], -1)
        self.assertEqual(service._dbusservice["/Auto/ChargerCurrentTargetAge"], 4)
        self.assertEqual(service._dbusservice["/Auto/ErrorCount"], 2)

        service._last_health_reason = "contactor-lockout-open"
        service._last_auto_state = "recovery"
        service._last_auto_state_code = 5
        service._publish_diagnostic_paths(100.0)

        self.assertEqual(service._dbusservice["/Auto/RecoveryActive"], 1)
        self.assertEqual(service._dbusservice["/Auto/FaultActive"], 1)
        self.assertEqual(service._dbusservice["/Auto/FaultReason"], "contactor-lockout-open")

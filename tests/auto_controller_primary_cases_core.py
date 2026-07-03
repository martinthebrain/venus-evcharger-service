# SPDX-License-Identifier: GPL-3.0-or-later
from tests.auto_controller_primary_cases_common import *


class _AutoControllerPrimaryCoreCases:
    def test_metric_helpers_and_relay_change_tracking_cover_empty_and_time_default(self):
        controller, service = self._make_controller()

        self.assertIsNone(controller.average_auto_metric(1))

        with patch("venus_evcharger.auto.workflow.time.time", return_value=123.0):
            controller.mark_relay_changed(False)

        self.assertEqual(service.relay_last_changed_at, 123.0)
        self.assertEqual(service.relay_last_off_at, 123.0)

    def test_auto_decide_relay_uses_service_time_source(self):
        controller, service = self._make_controller()
        service._time_now = lambda: 150.0
        service.relay_last_off_at = 140.0
        service.auto_min_offtime_seconds = 20.0
        service.auto_samples = deque([(149.0, 2500.0, -2500.0)])

        with patch("venus_evcharger.auto.workflow.time.time", return_value=999.0):
            self.assertFalse(controller.auto_decide_relay(False, 2500.0, 60.0, -2500.0))

        self.assertEqual(service._last_health_reason, "waiting-offtime")

    def test_daytime_window_handles_equal_and_wraparound_ranges(self):
        controller, service = self._make_controller()
        service.auto_daytime_only = True
        service.auto_month_windows = {
            4: ((8, 0), (8, 0)),
            5: ((20, 0), (6, 0)),
        }

        self.assertTrue(controller.is_within_auto_daytime_window(datetime(2026, 4, 4, 8, 0)))
        self.assertTrue(controller.is_within_auto_daytime_window(datetime(2026, 5, 4, 2, 0)))

    def test_auto_mode_does_not_start_scheduled_night_fallback_after_daytime_window_ends(self):
        controller, service = self._make_controller()
        service.virtual_mode = 1
        service.virtual_autostart = 1
        service.auto_month_windows = {4: ((7, 30), (19, 30))}
        service.auto_scheduled_enabled_days = "Mon,Tue,Wed,Thu,Fri"
        service.auto_scheduled_night_start_delay_seconds = 3600.0
        service.auto_scheduled_latest_end_time = "06:30"
        service._time_now = lambda: utc_timestamp(2026, 4, 20, 20, 45)

        self.assertFalse(controller.auto_decide_relay(False, None, None, None))
        self.assertNotEqual(service._last_health_reason, "scheduled-night-charge")
        self.assertNotEqual(service._last_auto_state, "charging")

    def test_scheduled_mode_starts_charging_one_hour_after_daytime_window_ends(self):
        controller, service = self._make_controller()
        service.virtual_mode = 2
        service.virtual_autostart = 1
        service.auto_month_windows = {4: ((7, 30), (19, 30))}
        service.auto_scheduled_enabled_days = "Mon,Tue,Wed,Thu,Fri"
        service.auto_scheduled_night_start_delay_seconds = 3600.0
        service.auto_scheduled_latest_end_time = "06:30"
        service._time_now = lambda: utc_timestamp(2026, 4, 20, 20, 45)

        self.assertTrue(controller.auto_decide_relay(False, None, None, None))
        self.assertEqual(service._last_health_reason, "scheduled-night-charge")
        self.assertEqual(service._last_auto_state, "charging")

    def test_scheduled_mode_keeps_running_overnight_despite_missing_inputs(self):
        controller, service = self._make_controller()
        service.virtual_mode = 2
        service.virtual_autostart = 1
        service.auto_month_windows = {4: ((7, 30), (19, 30))}
        service.auto_scheduled_enabled_days = "Mon,Tue,Wed,Thu,Fri"
        service.auto_scheduled_night_start_delay_seconds = 3600.0
        service.auto_scheduled_latest_end_time = "06:30"
        service._time_now = lambda: utc_timestamp(2026, 4, 21, 3, 0)

        self.assertTrue(controller.auto_decide_relay(True, None, None, None))
        self.assertEqual(service._last_health_reason, "scheduled-night-charge")
        self.assertEqual(service._last_auto_state, "charging")

    def test_scheduled_mode_respects_enabled_target_days(self):
        controller, service = self._make_controller()
        service.virtual_mode = 2
        service.virtual_autostart = 1
        service.auto_month_windows = {4: ((7, 30), (19, 30))}
        service.auto_scheduled_enabled_days = "Mon,Tue,Wed,Thu,Fri"
        service.auto_scheduled_night_start_delay_seconds = 3600.0
        service.auto_scheduled_latest_end_time = "06:30"
        service._time_now = lambda: utc_timestamp(2026, 4, 17, 21, 0)

        self.assertFalse(controller._scheduled_night_charge_active())
        self.assertFalse(controller.auto_decide_relay(False, None, None, None))
        self.assertNotEqual(service._last_health_reason, "scheduled-night-charge")

    def test_scheduled_mode_stops_night_boost_after_latest_end_time(self):
        controller, service = self._make_controller()
        service.virtual_mode = 2
        service.virtual_autostart = 1
        service.auto_month_windows = {4: ((7, 30), (19, 30))}
        service.auto_scheduled_enabled_days = "Mon,Tue,Wed,Thu,Fri"
        service.auto_scheduled_night_start_delay_seconds = 3600.0
        service.auto_scheduled_latest_end_time = "06:30"
        service._time_now = lambda: utc_timestamp(2026, 4, 21, 6, 45)

        self.assertFalse(controller._scheduled_night_charge_active())

    def test_scheduled_night_decision_covers_gate_autostart_and_waiting_offtime_edges(self):
        controller, service = self._make_controller()

        with patch.object(controller, "_handle_common_runtime_gates", return_value=True):
            self.assertTrue(controller._scheduled_night_decision(False, 100.0, False))

        service.virtual_autostart = 0
        with patch.object(controller, "_handle_common_runtime_gates", return_value=controller._NO_DECISION):
            self.assertFalse(controller._scheduled_night_decision(False, 100.0, False))
        self.assertEqual(service._last_health_reason, "autostart-disabled")

        service.virtual_autostart = 1
        with (
            patch.object(controller, "_handle_common_runtime_gates", return_value=controller._NO_DECISION),
            patch.object(controller, "_minimum_offtime_elapsed", return_value=False),
        ):
            self.assertFalse(controller._scheduled_night_decision(False, 100.0, False))
        self.assertEqual(service._last_health_reason, "waiting-offtime")

    def test_relay_on_helpers_cover_auto_stop_grid_reporting_and_nonnumeric_time_source(self):
        controller, service = self._make_controller()
        service.auto_stop_delay_seconds = 30.0

        with (
            patch.object(controller, "_minimum_runtime_elapsed", return_value=True),
            patch.object(controller, "_relay_on_stop_reason", return_value="auto-stop-grid"),
            patch.object(controller, "_pending_stop_or_running", return_value=False) as pending_stop,
        ):
            self.assertFalse(controller._handle_relay_on(1000.0, 200.0, 60.0, True, 100.0, False))

        self.assertEqual(pending_stop.call_args.kwargs["stop_key"], "auto-stop-grid")
        self.assertEqual(pending_stop.call_args.args[1], "auto-stop")

        service._time_now = lambda: "bad"
        with patch("venus_evcharger.auto.logic_learning.time.time", return_value=123.0):
            self.assertEqual(controller._learning_policy_now(), 123.0)

    def test_relay_on_helpers_keep_custom_stop_reason_reporting_outside_grid_and_soc(self):
        controller, service = self._make_controller()
        service.auto_stop_delay_seconds = 45.0

        with (
            patch.object(controller, "_minimum_runtime_elapsed", return_value=True),
            patch.object(controller, "_relay_on_stop_reason", return_value="custom-stop"),
            patch.object(controller, "_pending_stop_or_running", return_value=False) as pending_stop,
        ):
            self.assertFalse(controller._handle_relay_on(1000.0, 200.0, 60.0, True, 100.0, False))

        self.assertEqual(pending_stop.call_args.args[1], "custom-stop")
        self.assertEqual(pending_stop.call_args.kwargs["stop_key"], "custom-stop")

    def test_set_health_cached_updates_code_and_audit_log(self):
        controller, service = self._make_controller()
        service.auto_audit_log = True

        controller.set_health("grid-missing", cached=True)

        self.assertEqual(service._last_health_reason, "grid-missing-cached")
        self.assertEqual(service._last_health_code, 101)
        self.assertEqual(service._last_auto_state, "recovery")
        self.assertEqual(service._last_auto_state_code, 5)
        service._write_auto_audit_event.assert_called_once_with("grid-missing", True)

    def test_set_health_uses_explicit_learning_state_while_running(self):
        controller, service = self._make_controller()
        service._last_confirmed_pm_status = {"output": True}
        service._last_confirmed_pm_status_at = 995.0
        service.learned_charge_power_state = "learning"

        controller.set_health("running", cached=False)

        self.assertEqual(service._last_auto_state, "learning")
        self.assertEqual(service._last_auto_state_code, 2)
        self.assertEqual(service._last_auto_metrics["state"], "learning")
        self.assertEqual(service._last_auto_metrics["relay_intent"], 1)

    def test_set_health_postconditions_sanitize_metrics_and_follow_explicit_relay_intent(self):
        controller, service = self._make_controller()
        service._last_auto_metrics = {
            "start_threshold": 1000.0,
            "stop_threshold": 1200.0,
            "threshold_mode": 7,
            "learned_charge_power_state": "odd",
        }

        controller.set_health("running", cached=False, relay_intent=True)

        self.assertEqual(service._last_health_reason, "running")
        self.assertEqual(service._last_health_code, 99)
        self.assertEqual(service._last_auto_state, "charging")
        self.assertEqual(service._last_auto_state_code, 3)
        self.assertEqual(service._last_auto_metrics["relay_intent"], 1)
        self.assertEqual(service._last_auto_metrics["state"], "charging")
        self.assertIsNone(service._last_auto_metrics["start_threshold"])
        self.assertIsNone(service._last_auto_metrics["stop_threshold"])
        self.assertEqual(service._last_auto_metrics["threshold_mode"], "7")

    def test_observed_relay_state_ignores_stale_confirmed_or_virtual_only_state(self):
        controller, service = self._make_controller()
        service._last_confirmed_pm_status = {"output": True}
        service._last_confirmed_pm_status_at = 980.0
        service.virtual_startstop = 1

        self.assertFalse(controller._observed_relay_state())

    def test_derive_auto_state_uses_observed_relay_hint_and_learning_state(self):
        controller, service = self._make_controller()
        service._last_confirmed_pm_status = {"output": True}
        service._last_confirmed_pm_status_at = 999.0
        service.learned_charge_power_state = "learning"

        self.assertEqual(controller._derive_auto_state("custom-reason"), "learning")

    def test_battery_soc_missing_without_override_returns_terminal_decision(self):
        controller, service = self._make_controller()

        battery_soc, decision = controller._resolve_battery_soc(None, True, 100.0, False)

        self.assertIsNone(battery_soc)
        self.assertTrue(decision)
        self.assertEqual(service._last_health_reason, "battery-soc-missing")

    def test_out_of_range_battery_soc_is_treated_as_missing(self):
        controller, service = self._make_controller()
        service._warning_throttled = MagicMock()

        battery_soc, decision = controller._resolve_battery_soc(150.0, True, 100.0, False)

        self.assertIsNone(battery_soc)
        self.assertTrue(decision)
        self.assertEqual(service._last_health_reason, "battery-soc-missing")
        service._warning_throttled.assert_called_once()

    def test_stop_and_missing_input_helpers_cover_running_idle_and_none_reason(self):
        controller, service = self._make_controller()
        service.auto_stop_condition_since = 50.0
        self.assertIs(controller._arm_or_fire_stop(60.0, "grid-missing", False), controller._NO_DECISION)

        service.relay_last_changed_at = 90.0
        self.assertTrue(controller._handle_grid_missing(True, 100.0, False))
        self.assertEqual(service._last_health_reason, "grid-missing")

        service.auto_night_lock_stop = True
        self.assertEqual(controller._known_missing_input_stop_reason(60.0, None, False), "night-lock")
        service.auto_night_lock_stop = False
        self.assertIsNone(controller._known_missing_input_stop_reason(60.0, None, True))

        self.assertFalse(controller._handle_missing_inputs(False, 60.0, None, 100.0, False))
        self.assertEqual(service._last_health_reason, "inputs-missing")
        self.assertTrue(controller._handle_missing_inputs(True, 60.0, None, 100.0, False))
        self.assertEqual(service._last_health_reason, "inputs-missing")

    def test_average_metrics_and_relay_on_helpers_cover_none_night_lock_and_delayed_start(self):
        controller, service = self._make_controller()
        service._add_auto_sample = MagicMock()
        service._average_auto_metric = MagicMock(side_effect=[None, -100.0])

        self.assertEqual(controller._update_average_metrics(100.0, 2500.0, -2000.0, 60.0, False), (None, None))
        service.auto_night_lock_stop = True
        self.assertEqual(controller._relay_on_stop_reason(1500.0, -2000.0, 60.0, False, True), "night-lock")
        service.auto_night_lock_stop = False
        self.assertEqual(controller._relay_on_stop_reason(1500.0, -2000.0, 44.0, True, True), "auto-stop-surplus")
        self.assertEqual(controller._relay_on_stop_reason(1700.0, 400.0, 60.0, True, True), "auto-stop-grid")
        self.assertEqual(controller._relay_on_stop_reason(1700.0, 0.0, 20.0, True, True), "auto-stop-soc")
        service.auto_start_condition_since = 100.0
        self.assertFalse(controller._arm_or_fire_start(105.0, False))

    def test_adaptive_stop_smoothing_applies_only_while_running(self):
        controller, service = self._make_controller()
        service._add_auto_sample = MagicMock()
        service._average_auto_metric = MagicMock(side_effect=[1200.0, 400.0, 1200.0, 400.0])
        service._stop_smoothed_surplus_power = 2000.0
        service._stop_smoothed_grid_power = -200.0

        smoothed_surplus, smoothed_grid = controller._update_average_metrics(100.0, 2200.0, 400.0, 60.0, True)
        self.assertEqual(smoothed_surplus, 1800.0)
        self.assertEqual(smoothed_grid, -50.0)
        self.assertEqual(service._last_auto_metrics["raw_surplus"], 1200.0)
        self.assertEqual(service._last_auto_metrics["raw_grid"], 400.0)

        raw_surplus, raw_grid = controller._update_average_metrics(101.0, 2200.0, 400.0, 60.0, False)
        self.assertEqual(raw_surplus, 1200.0)
        self.assertEqual(raw_grid, 400.0)
        self.assertIsNone(service._stop_smoothed_surplus_power)
        self.assertIsNone(service._stop_smoothed_grid_power)

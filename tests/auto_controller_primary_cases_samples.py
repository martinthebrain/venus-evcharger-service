# SPDX-License-Identifier: GPL-3.0-or-later
import math

from venus_evcharger.auto.logic_samples import AutoSampleTracker
from tests.auto_controller_primary_cases_common import *


class _AutoControllerPrimarySampleCases:
    def test_available_surplus_contract_covers_import_export_and_clamps(self):
        controller, _ = self._make_controller()

        self.assertEqual(controller.get_available_surplus_watts(2500, -1800), 1800.0)
        self.assertEqual(controller.get_available_surplus_watts(1200, -1800), 1200.0)
        self.assertEqual(controller.get_available_surplus_watts(-100, -1800), 0.0)
        self.assertEqual(controller.get_available_surplus_watts(2500, 25), 0.0)

    def test_sample_buffer_prunes_strictly_older_than_average_window(self):
        controller, service = self._make_controller()
        service.auto_average_window_seconds = 10.0
        service.auto_samples = deque([(89.99, 1.0, 2.0), (90.0, 3.0, 4.0), (95.0, 5.0, 6.0)])

        controller.add_auto_sample(100.0, 7, -8)

        self.assertEqual(list(service.auto_samples), [(90.0, 3.0, 4.0), (95.0, 5.0, 6.0), (100.0, 7.0, -8.0)])
        self.assertEqual(controller.average_auto_metric(0), 95.0)
        self.assertEqual(controller.average_auto_metric(1), 5.0)
        self.assertEqual(controller.average_auto_metric(2), (4.0 + 6.0 - 8.0) / 3.0)

    def test_clear_samples_resets_buffer_and_stop_smoothing_state(self):
        controller, service = self._make_controller()
        service.auto_samples = deque([(1.0, 2.0, 3.0)])
        service._stop_smoothed_surplus_power = 10.0
        service._stop_smoothed_grid_power = -5.0

        controller.clear_auto_samples()

        self.assertEqual(list(service.auto_samples), [])
        self.assertIsNone(service._stop_smoothed_surplus_power)
        self.assertIsNone(service._stop_smoothed_grid_power)
        self.assertIsNone(controller.average_auto_metric(1))

    def test_smoothing_metric_contracts_first_sample_and_weighted_update(self):
        self.assertEqual(AutoSampleTracker._smooth_metric(None, 10, 0.25), 10.0)
        self.assertEqual(AutoSampleTracker._smooth_metric(10.0, 18.0, 0.25), 12.0)
        self.assertEqual(AutoSampleTracker._smooth_metric(10.0, 18.0, 1.0), 18.0)
        self.assertEqual(AutoSampleTracker._smooth_metric(10.0, 18.0, 0.0), 10.0)

    def test_learning_state_normalization_and_positive_power_contracts(self):
        controller, service = self._make_controller()

        for raw, expected in (
            (None, "unknown"),
            (" STABLE ", "stable"),
            ("learning", "learning"),
            ("stale", "stale"),
            ("unsupported", "unknown"),
        ):
            with self.subTest(raw=raw):
                self.assertEqual(controller.learning._normalize_learned_charge_power_state(raw), expected)

        service.learned_charge_power_watts = None
        self.assertIsNone(controller.learning._positive_learned_charge_power())
        service.learned_charge_power_watts = 0.0
        self.assertIsNone(controller.learning._positive_learned_charge_power())
        service.learned_charge_power_watts = -1.0
        self.assertIsNone(controller.learning._positive_learned_charge_power())
        service.learned_charge_power_watts = 1.0
        self.assertEqual(controller.learning._positive_learned_charge_power(), 1.0)
        service.learned_charge_power_watts = "2280.5"
        self.assertEqual(controller.learning._positive_learned_charge_power(), 2280.5)
        self.assertTrue(controller.learning._has_positive_learned_charge_power())

        delattr(service, "learned_charge_power_watts")
        self.assertIsNone(controller.learning._positive_learned_charge_power())
        delattr(service, "learned_charge_power_state")
        self.assertEqual(controller.learning._stored_learned_charge_power_state(), "unknown")

    def test_learned_charge_power_age_and_staleness_boundaries(self):
        controller, service = self._make_controller()
        policy = controller.learning._auto_policy()
        policy.learn_charge_power.max_age_seconds = 60.0
        service.learned_charge_power_watts = 2280.0
        service.learned_charge_power_state = "stable"

        service.learned_charge_power_updated_at = None
        self.assertTrue(controller.learning._learned_charge_power_can_expire())
        self.assertTrue(controller.learning._learned_charge_power_missing_update_time())
        delattr(service, "learned_charge_power_updated_at")
        self.assertTrue(controller.learning._learned_charge_power_missing_update_time())
        self.assertIsNone(controller.learning._learned_charge_power_age_seconds(1000.0))
        service.learned_charge_power_updated_at = None
        self.assertIsNone(controller.learning._learned_charge_power_age_seconds(1000.0))
        self.assertTrue(controller.learning._learned_charge_power_expired(1000.0))
        self.assertEqual(controller.learning._stale_learned_charge_power_state("unknown", 1000.0), "unknown")
        self.assertEqual(controller.learning._stale_learned_charge_power_state("stable", 1000.0), "stale")

        service.learned_charge_power_updated_at = 940.0
        self.assertEqual(controller.learning._learned_charge_power_age_seconds(1000.0), 60.0)
        self.assertFalse(controller.learning._learned_charge_power_expired(1000.0))
        self.assertIsNone(controller.learning._stale_learned_charge_power_state("stable", 1000.0))

        service.learned_charge_power_updated_at = 939.9
        self.assertTrue(controller.learning._learned_charge_power_expired(1000.0))
        self.assertEqual(controller.learning._current_learned_charge_power_state(1000.0), "stale")

        policy.learn_charge_power.max_age_seconds = 0.0
        service.learned_charge_power_updated_at = None
        self.assertFalse(controller.learning._learned_charge_power_can_expire())
        self.assertIsNone(controller.learning._stale_learned_charge_power_state("stable", 1000.0))

        policy.learn_charge_power.max_age_seconds = 0.5
        service.learned_charge_power_updated_at = 999.6
        self.assertTrue(controller.learning._learned_charge_power_can_expire())
        self.assertFalse(controller.learning._learned_charge_power_expired(1000.0))
        service.time_now = MagicMock(return_value=2000.0)
        self.assertFalse(controller.learning._learned_charge_power_expired(1000.0))
        self.assertIsNone(controller.learning._stale_learned_charge_power_state("stable", 1000.0))
        self.assertFalse(controller.learning._learned_charge_power_age_invalid_for_auto(1000.0))

    def test_current_and_active_learned_charge_power_delegate_exact_inputs(self):
        controller, _ = self._make_controller()
        controller.learning._stored_learned_charge_power_state = MagicMock(return_value="stable")
        controller.learning._has_positive_learned_charge_power = MagicMock(return_value=True)
        controller.learning._stale_learned_charge_power_state = MagicMock(return_value=None)

        self.assertEqual(controller.learning._current_learned_charge_power_state(123.0), "stable")
        controller.learning._stale_learned_charge_power_state.assert_called_once_with("stable", 123.0)

        controller.learning._stale_learned_charge_power_state.return_value = "stale"
        self.assertEqual(controller.learning._current_learned_charge_power_state(124.0), "stale")
        controller.learning._stale_learned_charge_power_state.assert_called_with("stable", 124.0)

        controller.learning._positive_learned_charge_power = MagicMock(return_value=2280.0)
        controller.learning._learned_charge_power_inactive_for_auto = MagicMock(return_value=False)
        self.assertEqual(controller.learning._active_learned_charge_power(125.0), 2280.0)
        controller.learning._learned_charge_power_inactive_for_auto.assert_called_once_with(2280.0, 125.0)

        controller.learning._learned_charge_power_inactive_for_auto.return_value = True
        self.assertIsNone(controller.learning._active_learned_charge_power(126.0))
        controller.learning._learned_charge_power_inactive_for_auto.assert_called_with(2280.0, 126.0)

    def test_learned_charge_power_auto_inactive_reasons_are_independent(self):
        controller, service = self._make_controller()
        policy = controller.learning._auto_policy()
        policy.learn_charge_power.enabled = True
        policy.learn_charge_power.reference_power_watts = 1900.0
        policy.learn_charge_power.max_age_seconds = 60.0
        service.learned_charge_power_watts = 2280.0
        service.learned_charge_power_updated_at = 995.0
        service.learned_charge_power_state = "stable"

        self.assertFalse(controller.learning._learned_charge_power_inactive_for_auto(2280.0, 1000.0))
        self.assertEqual(controller.learning._active_learned_charge_power(1000.0), 2280.0)
        self.assertEqual(controller.learning._learned_charge_power_scale(1000.0), 1.2)
        service.time_now = MagicMock(return_value=2000.0)
        self.assertFalse(controller.learning._learned_charge_power_inactive_for_auto(2280.0, 1000.0))
        self.assertEqual(controller.learning._learned_charge_power_scale(1000.0), 1.2)

        policy.learn_charge_power.enabled = False
        self.assertTrue(controller.learning._learned_charge_power_inactive_for_auto(2280.0, 1000.0))
        policy.learn_charge_power.enabled = True
        service.learned_charge_power_state = "learning"
        self.assertTrue(controller.learning._learned_charge_power_inactive_for_auto(2280.0, 1000.0))
        service.learned_charge_power_state = "stable"
        self.assertTrue(controller.learning._learned_charge_power_inactive_for_auto(None, 1000.0))
        service.learned_charge_power_updated_at = 900.0
        self.assertTrue(controller.learning._learned_charge_power_inactive_for_auto(2280.0, 1000.0))

    def test_scaled_thresholds_round_and_invalid_scaled_profile_falls_back_to_static_values(self):
        controller, service = self._make_controller()
        service.learned_charge_power_watts = 2280.0
        service.learned_charge_power_updated_at = 995.0
        service.learned_charge_power_state = "stable"

        self.assertEqual(controller.learning._scale_surplus_thresholds(1000.04, 800.04), (1200.0, 960.0))
        with patch.object(controller.learning, "_learned_charge_power_scale", return_value=1.234):
            self.assertEqual(controller.learning._scale_surplus_thresholds(1000.1, 800.1), (1234.1, 987.3))

        delattr(service, "_auto_high_soc_profile_active")
        self.assertEqual(controller.learning._surplus_thresholds_for_soc(50.0), (2400.0, 1920.0, "normal"))
        self.assertFalse(service._auto_high_soc_profile_active)

        with patch.object(controller.learning, "_scale_surplus_thresholds", return_value=(700.0, 900.0)):
            with self.assertLogs(level="WARNING") as logs:
                self.assertEqual(controller.learning._surplus_thresholds_for_soc(50.0), (2000.0, 1600.0, "normal"))
        self.assertEqual(
            logs.output,
            [
                "WARNING:root:Adaptive surplus thresholds became invalid for profile normal: "
                "start=700.0 stop=900.0; falling back to static profile values"
            ],
        )
        self.assertFalse(service._auto_high_soc_profile_active)

    def test_stop_surplus_volatility_returns_population_standard_deviation(self):
        controller, service = self._make_controller()
        service.auto_samples = deque()
        self.assertIsNone(controller.samples._stop_surplus_volatility())
        service.auto_samples = deque([(1.0, 1000.0, 0.0)])
        self.assertIsNone(controller.samples._stop_surplus_volatility())
        service.auto_samples = deque([(1.0, 1000.0, 0.0), (2.0, 1300.0, 0.0)])
        self.assertEqual(controller.samples._stop_surplus_volatility(), 150.0)
        service.auto_samples = deque([(1.0, 1000.0, 0.0), (2.0, 1300.0, 0.0), (3.0, 700.0, 0.0)])
        self.assertEqual(controller.samples._stop_surplus_volatility(), math.sqrt(60000.0))

    def test_relay_change_tracking_preserves_last_off_when_turning_on(self):
        controller, service = self._make_controller()
        service.relay_last_off_at = 12.0

        controller.mark_relay_changed(True, 20.0)

        self.assertEqual(service.relay_last_changed_at, 20.0)
        self.assertEqual(service.relay_last_off_at, 12.0)

    def test_daytime_window_contracts_cover_disabled_default_boundaries_and_wraparound(self):
        controller, service = self._make_controller()
        service.auto_daytime_only = False
        self.assertTrue(controller.is_within_auto_daytime_window(datetime(2026, 1, 1, 3, 0)))

        service.auto_daytime_only = True
        service.auto_month_windows = {}
        delattr(service, "auto_daytime_only")
        self.assertTrue(controller.is_within_auto_daytime_window(datetime(2026, 1, 1, 7, 0)))
        service.auto_daytime_only = True
        delattr(service, "auto_month_windows")
        self.assertEqual(controller.samples._daytime_window_minutes_for_month(1), (8 * 60, 18 * 60))
        service.auto_month_windows = {}
        self.assertEqual(controller.samples._daytime_window_minutes_for_month(1), (8 * 60, 18 * 60))
        self.assertFalse(controller.is_within_auto_daytime_window(datetime(2026, 1, 1, 7, 59)))
        self.assertTrue(controller.is_within_auto_daytime_window(datetime(2026, 1, 1, 8, 0)))
        self.assertTrue(controller.is_within_auto_daytime_window(datetime(2026, 1, 1, 17, 59)))
        self.assertFalse(controller.is_within_auto_daytime_window(datetime(2026, 1, 1, 18, 0)))

        service.auto_month_windows = {6: ((22, 15), (5, 45))}
        self.assertEqual(controller.samples._daytime_window_minutes_for_month(6), (22 * 60 + 15, 5 * 60 + 45))
        self.assertTrue(controller.is_within_auto_daytime_window(datetime(2026, 6, 1, 23, 0)))
        self.assertTrue(controller.is_within_auto_daytime_window(datetime(2026, 6, 1, 5, 44)))
        self.assertFalse(controller.is_within_auto_daytime_window(datetime(2026, 6, 1, 5, 45)))

    def test_minutes_within_daytime_window_boundary_contracts(self):
        self.assertTrue(AutoSampleTracker._minutes_within_daytime_window(10, 10, 10))
        self.assertTrue(AutoSampleTracker._minutes_within_daytime_window(10, 10, 20))
        self.assertTrue(AutoSampleTracker._minutes_within_daytime_window(19, 10, 20))
        self.assertFalse(AutoSampleTracker._minutes_within_daytime_window(20, 10, 20))
        self.assertTrue(AutoSampleTracker._minutes_within_daytime_window(22 * 60, 22 * 60, 6 * 60))
        self.assertTrue(AutoSampleTracker._minutes_within_daytime_window(23 * 60, 22 * 60, 6 * 60))
        self.assertTrue(AutoSampleTracker._minutes_within_daytime_window(5 * 60 + 59, 22 * 60, 6 * 60))
        self.assertFalse(AutoSampleTracker._minutes_within_daytime_window(6 * 60, 22 * 60, 6 * 60))

    def test_scheduled_night_snapshot_receives_local_time_and_configured_contracts(self):
        controller, service = self._make_controller()
        self.assertEqual(controller.samples._service_virtual_mode(), 1)
        service.virtual_mode = None
        self.assertEqual(controller.samples._service_virtual_mode(), 0)
        delattr(service, "virtual_mode")
        self.assertEqual(controller.samples._service_virtual_mode(), 0)
        service.virtual_mode = 2
        service.auto_schedule_timezone = "Europe/Berlin"
        service.auto_month_windows = {4: ((7, 30), (19, 30))}
        service.auto_scheduled_enabled_days = "Tue"
        service.auto_scheduled_night_start_delay_seconds = 1800.0
        service.auto_scheduled_latest_end_time = "05:15"
        snapshot = SimpleNamespace(night_boost_active=True)

        with patch("venus_evcharger.auto.logic_samples._scheduled_mode_snapshot", return_value=snapshot) as scheduled:
            self.assertTrue(controller.samples.scheduled_night_charge_active(utc_timestamp(2026, 4, 21, 2, 0)))

        scheduled.assert_called_once()
        args = scheduled.call_args.args
        kwargs = scheduled.call_args.kwargs
        self.assertEqual(args[0].year, 2026)
        self.assertEqual(args[0].month, 4)
        self.assertEqual(args[0].hour, 4)
        self.assertEqual(args[1], service.auto_month_windows)
        self.assertEqual(args[2], "Tue")
        self.assertEqual(kwargs, {"delay_seconds": 1800.0, "latest_end_time": "05:15"})

        service.virtual_mode = 1
        with patch("venus_evcharger.auto.logic_samples._scheduled_mode_snapshot") as scheduled:
            self.assertFalse(controller.samples.scheduled_night_charge_active(utc_timestamp(2026, 4, 21, 2, 0)))
        scheduled.assert_not_called()

        delattr(service, "virtual_mode")
        with patch("venus_evcharger.auto.logic_samples._scheduled_mode_snapshot") as scheduled:
            self.assertFalse(controller.samples.scheduled_night_charge_active(utc_timestamp(2026, 4, 21, 2, 0)))
        scheduled.assert_not_called()

    def test_scheduled_night_snapshot_uses_service_clock_when_now_is_missing(self):
        controller, service = self._make_controller()
        service.virtual_mode = 2
        service.auto_schedule_timezone = "Europe/Berlin"
        service.time_now = MagicMock(return_value=utc_timestamp(2026, 7, 2, 2, 0))
        snapshot = SimpleNamespace(night_boost_active=True)

        with patch("venus_evcharger.auto.logic_samples._scheduled_mode_snapshot", return_value=snapshot) as scheduled:
            self.assertTrue(controller.samples.scheduled_night_charge_active())

        service.time_now.assert_called_once_with()
        args = scheduled.call_args.args
        self.assertEqual((args[0].year, args[0].month, args[0].hour), (2026, 7, 4))

    def test_scheduled_night_snapshot_uses_documented_defaults_when_config_is_missing(self):
        controller, service = self._make_controller()
        service.virtual_mode = 2
        for name in (
            "auto_schedule_timezone",
            "auto_month_windows",
            "auto_scheduled_enabled_days",
            "auto_scheduled_night_start_delay_seconds",
            "auto_scheduled_latest_end_time",
        ):
            if hasattr(service, name):
                delattr(service, name)

        snapshot = SimpleNamespace(night_boost_active=False)
        with patch("venus_evcharger.auto.logic_samples._scheduled_mode_snapshot", return_value=snapshot) as scheduled:
            self.assertFalse(controller.samples.scheduled_night_charge_active(utc_timestamp(2026, 4, 21, 2, 0)))

        scheduled.assert_called_once()
        args = scheduled.call_args.args
        kwargs = scheduled.call_args.kwargs
        self.assertEqual(args[0].year, 2026)
        self.assertEqual(args[1], {})
        self.assertEqual(args[2], "Mon,Tue,Wed,Thu,Fri")
        self.assertEqual(kwargs, {"delay_seconds": 3600.0, "latest_end_time": "04:30"})

        service.auto_schedule_timezone = None
        with patch("venus_evcharger.auto.logic_samples._scheduled_mode_snapshot", return_value=snapshot) as scheduled:
            self.assertFalse(controller.samples.scheduled_night_charge_active(utc_timestamp(2026, 4, 21, 2, 0)))
        self.assertIsNone(scheduled.call_args.args[0].tzinfo)

    def test_decision_trace_postconditions_update_dict_and_replace_non_dict_metrics(self):
        controller, service = self._make_controller()
        service._last_auto_metrics = {"old": "value"}

        controller.samples._apply_decision_trace_postconditions("running", False, True)

        self.assertEqual(service._last_health_reason, "running")
        self.assertEqual(service._last_health_code, 99)
        self.assertEqual(service._last_auto_state, "charging")
        self.assertEqual(service._last_auto_state_code, 3)
        self.assertEqual(service._last_auto_metrics["old"], "value")
        self.assertEqual(service._last_auto_metrics["relay_intent"], 1)

        service._last_auto_metrics = None
        controller.samples._apply_decision_trace_postconditions("grid-missing", True, False)
        self.assertIsInstance(service._last_auto_metrics, dict)
        self.assertEqual(service._last_health_reason, "grid-missing-cached")
        self.assertEqual(service._last_auto_metrics["relay_intent"], 0)
        self.assertEqual(service._last_auto_metrics["state"], "recovery")

    def test_decision_trace_postconditions_delegate_full_contract_to_normalizer(self):
        controller, service = self._make_controller()
        service.learned_charge_power_state = "learning"
        service._last_auto_metrics = {"surplus": 123.0}
        normalized = {
            "health_reason": "custom",
            "health_code": 77,
            "state": "learning",
            "state_code": 2,
            "metrics": {"relay_intent": 1, "state": "learning"},
        }
        captured_trace_kwargs = {}

        def capture_trace_kwargs(**kwargs):
            captured_trace_kwargs.update(kwargs)
            captured_trace_kwargs["metrics"] = dict(kwargs["metrics"])
            return normalized

        with patch("venus_evcharger.auto.logic_samples.normalized_auto_decision_trace", side_effect=capture_trace_kwargs) as trace:
            controller.samples._apply_decision_trace_postconditions("custom", True, True)

        trace.assert_called_once()
        self.assertEqual(captured_trace_kwargs["health_reason"], "custom")
        self.assertEqual(captured_trace_kwargs["cached_inputs"], True)
        self.assertEqual(captured_trace_kwargs["relay_intent"], True)
        self.assertEqual(captured_trace_kwargs["learned_charge_power_state"], "learning")
        self.assertEqual(captured_trace_kwargs["metrics"], {"surplus": 123.0})
        self.assertIs(captured_trace_kwargs["health_code_func"], controller.context.health_code)
        self.assertTrue(callable(captured_trace_kwargs["derive_auto_state_func"]))
        self.assertEqual(service._last_health_reason, "custom")
        self.assertEqual(service._last_health_code, 77)
        self.assertEqual(service._last_auto_state, "learning")
        self.assertEqual(service._last_auto_state_code, 2)
        self.assertEqual(service._last_auto_metrics, {"relay_intent": 1, "state": "learning"})

        delattr(service, "learned_charge_power_state")
        service._last_auto_metrics = {"surplus": 456.0}
        captured_trace_kwargs.clear()
        with patch("venus_evcharger.auto.logic_samples.normalized_auto_decision_trace", side_effect=capture_trace_kwargs):
            controller.samples._apply_decision_trace_postconditions("custom", False, False)
        self.assertEqual(captured_trace_kwargs["learned_charge_power_state"], "unknown")
        self.assertEqual(captured_trace_kwargs["metrics"], {"surplus": 456.0})

    def test_set_health_uses_observed_relay_default_and_audit_only_when_enabled(self):
        controller, service = self._make_controller()
        controller.samples._observed_relay_state = MagicMock(return_value=True)
        controller.samples._apply_decision_trace_postconditions = MagicMock()
        service.auto_audit_log = False

        controller.set_health("running", cached=True)

        controller.samples._apply_decision_trace_postconditions.assert_called_once_with("running", True, True)
        service.runtime.write_auto_audit_event.assert_not_called()

        controller.samples._apply_decision_trace_postconditions.reset_mock()
        controller.set_health("observed-default")
        controller.samples._apply_decision_trace_postconditions.assert_called_once_with("observed-default", False, True)

        delattr(service, "auto_audit_log")
        controller.samples._apply_decision_trace_postconditions.reset_mock()
        controller.set_health("missing-audit-flag")
        controller.samples._apply_decision_trace_postconditions.assert_called_once_with("missing-audit-flag", False, True)
        service.runtime.write_auto_audit_event.assert_not_called()

        service.auto_audit_log = True
        controller.samples._apply_decision_trace_postconditions.reset_mock()
        controller.set_health("idle", cached=False, relay_intent=False)
        controller.samples._apply_decision_trace_postconditions.assert_called_once_with("idle", False, False)
        service.runtime.write_auto_audit_event.assert_called_once_with("idle", False)

    def test_derive_auto_state_delegates_observed_relay_and_learning_state(self):
        controller, service = self._make_controller()
        service.learned_charge_power_state = "stable"
        controller.samples._observed_relay_state = MagicMock(return_value=True)

        with patch("venus_evcharger.auto.logic_samples._derive_auto_state", return_value="charging") as derive:
            self.assertEqual(controller.samples._derive_auto_state("running"), "charging")

        derive.assert_called_once_with("running", relay_on=True, learned_charge_power_state="stable")

        delattr(service, "learned_charge_power_state")
        with patch("venus_evcharger.auto.logic_samples._derive_auto_state", return_value="idle") as derive:
            self.assertEqual(controller.samples._derive_auto_state("idle"), "idle")
        derive.assert_called_once_with("idle", relay_on=True, learned_charge_power_state="unknown")

    def test_auto_state_helpers_update_metrics_and_tracking_helpers_clear_expected_fields(self):
        controller, service = self._make_controller()
        service._last_auto_metrics = {}
        controller.samples._set_auto_state("charging")
        self.assertEqual(service._last_auto_state, "charging")
        self.assertEqual(service._last_auto_state_code, 3)
        self.assertEqual(service._last_auto_metrics["state"], "charging")

        service._last_auto_metrics = None
        controller.samples._set_auto_state("idle")
        self.assertIsNone(service._last_auto_metrics)
        delattr(service, "_last_auto_metrics")
        controller.samples._set_auto_state("idle")
        self.assertFalse(hasattr(service, "_last_auto_metrics"))

        service.auto_start_condition_since = 1.0
        service.auto_stop_condition_since = 2.0
        service.auto_stop_condition_reason = "auto-stop-grid"
        service.auto_samples = deque([(1.0, 2.0, 3.0)])
        controller.samples._reset_auto_state()
        self.assertIsNone(service.auto_start_condition_since)
        self.assertIsNone(service.auto_stop_condition_since)
        self.assertIsNone(service.auto_stop_condition_reason)
        self.assertEqual(list(service.auto_samples), [])

        service.auto_start_condition_since = 10.0
        service.auto_samples = deque([(1.0, 2.0, 3.0)])
        controller.samples._clear_auto_start_tracking()
        self.assertIsNone(service.auto_start_condition_since)
        self.assertEqual(list(service.auto_samples), [(1.0, 2.0, 3.0)])
        service.auto_start_condition_since = 10.0
        controller.samples._clear_auto_start_tracking(clear_samples=True)
        self.assertIsNone(service.auto_start_condition_since)
        self.assertEqual(list(service.auto_samples), [])

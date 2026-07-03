# SPDX-License-Identifier: GPL-3.0-or-later
from tests.venus_evcharger_auto_controller_support import (
    AutoDecisionController,
    AutoDecisionControllerTestCase,
    MagicMock,
    SimpleNamespace,
    _health_code,
    _mode_uses_auto_logic,
    make_auto_controller_service,
    patch,
)


class TestAutoDecisionControllerRecovery(AutoDecisionControllerTestCase):
    def test_average_metrics_records_active_threshold_profile_for_diagnostics(self):
        controller, service = self._make_controller()
        service._add_auto_sample = MagicMock()
        service._average_auto_metric = MagicMock(side_effect=[1700.0, 50.0])

        controller._update_average_metrics(100.0, 2500.0, -1800.0, 55.0, False)

        self.assertEqual(service._last_auto_metrics["profile"], "high-soc")
        self.assertEqual(service._last_auto_metrics["start_threshold"], 1650.0)
        self.assertEqual(service._last_auto_metrics["stop_threshold"], 800.0)
        self.assertEqual(service._last_auto_metrics["learned_charge_power"], None)
        self.assertEqual(service._last_auto_metrics["learned_charge_power_state"], "unknown")
        self.assertEqual(service._last_auto_metrics["threshold_scale"], 1.0)
        self.assertEqual(service._last_auto_metrics["threshold_mode"], "static")
        self.assertEqual(service._last_auto_metrics["stop_alpha"], 0.25)
        self.assertEqual(service._last_auto_metrics["stop_alpha_stage"], "base")
        self.assertIsNone(service._last_auto_metrics["surplus_volatility"])

    def test_average_metrics_records_scaled_thresholds_when_learned_power_is_available(self):
        controller, service = self._make_controller()
        service.learned_charge_power_watts = 2280.0
        service.learned_charge_power_updated_at = 995.0
        service.learned_charge_power_state = "stable"
        service._add_auto_sample = MagicMock()
        service._average_auto_metric = MagicMock(side_effect=[2100.0, 40.0])

        controller._update_average_metrics(100.0, 2600.0, -2200.0, 55.0, False)

        self.assertEqual(service._last_auto_metrics["profile"], "high-soc")
        self.assertEqual(service._last_auto_metrics["start_threshold"], 1980.0)
        self.assertEqual(service._last_auto_metrics["stop_threshold"], 960.0)
        self.assertEqual(service._last_auto_metrics["learned_charge_power"], 2280.0)
        self.assertEqual(service._last_auto_metrics["learned_charge_power_state"], "stable")
        self.assertEqual(service._last_auto_metrics["threshold_scale"], 1.2)
        self.assertEqual(service._last_auto_metrics["threshold_mode"], "adaptive")

    def test_average_metrics_falls_back_to_static_thresholds_when_learned_value_is_stale(self):
        controller, service = self._make_controller()
        service.learned_charge_power_watts = 2280.0
        service.learned_charge_power_updated_at = 100.0
        service.learned_charge_power_state = "stable"
        service.auto_learn_charge_power_max_age_seconds = 60.0
        service._add_auto_sample = MagicMock()
        service._average_auto_metric = MagicMock(side_effect=[2100.0, 40.0])

        controller._update_average_metrics(1000.0, 2600.0, -2200.0, 55.0, False)

        self.assertEqual(service._last_auto_metrics["start_threshold"], 1650.0)
        self.assertEqual(service._last_auto_metrics["stop_threshold"], 800.0)
        self.assertIsNone(service._last_auto_metrics["learned_charge_power"])
        self.assertEqual(service._last_auto_metrics["learned_charge_power_state"], "stale")
        self.assertEqual(service._last_auto_metrics["threshold_scale"], 1.0)
        self.assertEqual(service._last_auto_metrics["threshold_mode"], "static")

    def test_learning_state_blocks_adaptive_thresholds_until_value_is_stable(self):
        controller, service = self._make_controller()
        service.learned_charge_power_watts = 2280.0
        service.learned_charge_power_updated_at = 995.0
        service.learned_charge_power_state = "learning"
        service._add_auto_sample = MagicMock()
        service._average_auto_metric = MagicMock(side_effect=[2100.0, 40.0])

        controller._update_average_metrics(1000.0, 2600.0, -2200.0, 55.0, False)

        self.assertEqual(service._last_auto_metrics["start_threshold"], 1650.0)
        self.assertEqual(service._last_auto_metrics["stop_threshold"], 800.0)
        self.assertIsNone(service._last_auto_metrics["learned_charge_power"])
        self.assertEqual(service._last_auto_metrics["learned_charge_power_state"], "learning")
        self.assertEqual(service._last_auto_metrics["threshold_mode"], "static")

    def test_stop_timer_resets_when_reason_changes_from_grid_to_surplus(self):
        controller, service = self._make_controller()
        service.auto_stop_condition_since = 100.0
        service.auto_stop_condition_reason = "auto-stop-grid"

        self.assertTrue(
            controller._pending_stop_or_running(
                110.0,
                "auto-stop",
                False,
                "running",
                delay_seconds=90.0,
                stop_key="auto-stop-surplus",
            )
        )
        self.assertEqual(service.auto_stop_condition_since, 110.0)
        self.assertEqual(service.auto_stop_condition_reason, "auto-stop-surplus")

    def test_waiting_health_and_relay_off_cover_grid_wait_default_wait_and_autostart_disabled(self):
        controller, service = self._make_controller()

        controller._set_waiting_health(True, True, 2500.0, 100.0, 60.0, False)
        self.assertEqual(service._last_health_reason, "waiting-grid")

        controller._set_waiting_health(True, True, 2500.0, 0.0, 40.0, False)
        self.assertEqual(service._last_health_reason, "waiting-soc")

        controller._set_waiting_health(True, True, 2500.0, 0.0, 60.0, False)
        self.assertEqual(service._last_health_reason, "waiting")

        service.virtual_autostart = 0
        self.assertFalse(controller._handle_relay_off(2500.0, 0.0, 60.0, True, 100.0, False))
        self.assertEqual(service._last_health_reason, "autostart-disabled")

    def test_disabled_mode_forces_relay_off_and_clears_transient_auto_state(self):
        controller, service = self._make_controller()
        service.auto_samples.append((100.0, 2000.0, 50.0))
        service.auto_start_condition_since = 90.0
        service.auto_stop_condition_since = 95.0
        service._auto_mode_cutover_pending = True
        service._ignore_min_offtime_once = True

        self.assertFalse(controller._handle_disabled_mode(cached_inputs=True))

        self.assertEqual(list(service.auto_samples), [])
        self.assertIsNone(service.auto_start_condition_since)
        self.assertIsNone(service.auto_stop_condition_since)
        self.assertIs(service._auto_mode_cutover_pending, False)
        self.assertIs(service._ignore_min_offtime_once, False)
        self.assertEqual(service._last_health_reason, "disabled-cached")
        service._save_runtime_state.assert_called_once_with()

    def test_auto_start_boundaries_arm_and_fire_with_cached_health(self):
        controller, service = self._make_controller()

        self.assertTrue(
            controller._relay_off_start_conditions_met(
                True,
                True,
                service.auto_start_surplus_watts,
                service.auto_start_max_grid_import_watts,
                service.auto_resume_soc,
                service.auto_start_surplus_watts,
                service,
            )
        )
        self.assertFalse(
            controller._relay_off_start_conditions_met(
                True,
                True,
                service.auto_start_surplus_watts - 0.1,
                service.auto_start_max_grid_import_watts,
                service.auto_resume_soc,
                service.auto_start_surplus_watts,
                service,
            )
        )
        self.assertEqual(
            controller._threshold_waiting_health_reason(2500.0, 0.0, service.auto_resume_soc),
            "waiting",
        )
        self.assertEqual(
            controller._threshold_waiting_health_reason(
                service.auto_high_soc_start_surplus_watts,
                service.auto_start_max_grid_import_watts,
                60.0,
            ),
            "waiting",
        )
        controller._set_waiting_health(True, True, 2500.0, 0.0, service.auto_resume_soc, True)
        self.assertEqual(service._last_health_reason, "waiting-cached")
        self.assertEqual(service._last_auto_metrics["relay_intent"], 0)
        self.assertEqual(service._last_auto_state, "waiting")

        service._last_pm_status_confirmed = True
        service._last_confirmed_pm_status = {"output": False}
        service._last_confirmed_pm_status_at = 100.0
        with patch.object(controller, "_learning_policy_now", return_value=105.0):
            controller._set_waiting_health(True, True, 2500.0, 0.0, 60.0, False)
        self.assertEqual(service._last_health_reason, "waiting")
        self.assertEqual(service._last_auto_metrics["relay_intent"], 0)

        service._last_confirmed_pm_status = {"output": True}
        with patch.object(controller, "_learning_policy_now", return_value=105.0):
            controller._set_waiting_health(True, True, 2500.0, 0.0, 60.0, False)
        self.assertEqual(service._last_health_reason, "waiting")
        self.assertEqual(service._last_auto_metrics["relay_intent"], 0)

        controller._set_waiting_health(
            True,
            True,
            service.auto_high_soc_start_surplus_watts - 0.1,
            0.0,
            60.0,
            False,
        )
        self.assertEqual(service._last_health_reason, "waiting-surplus")
        self.assertEqual(service._last_auto_metrics["relay_intent"], 0)
        controller._set_waiting_health(True, False, 2500.0, 0.0, 60.0, False)
        self.assertEqual(service._last_health_reason, "waiting-daytime")

        service.auto_start_condition_since = None
        self.assertFalse(controller._arm_or_fire_start(100.0, True))
        self.assertEqual(service.auto_start_condition_since, 100.0)

        service._ignore_min_offtime_once = True
        self.assertTrue(controller._arm_or_fire_start(110.0, True))
        self.assertIs(service._ignore_min_offtime_once, False)
        self.assertEqual(service._last_health_reason, "auto-start-cached")
        self.assertEqual(service._last_auto_metrics["relay_intent"], 1)
        service._save_runtime_state.assert_called_once_with()

    def test_relay_on_stop_reason_boundaries_and_running_health_are_explicit(self):
        controller, service = self._make_controller()
        service.auto_start_condition_since = 99.0

        self.assertTrue(controller._handle_relay_on(2500.0, 0.0, 60.0, True, 100.0, True))
        self.assertIsNone(service.auto_start_condition_since)
        self.assertEqual(service._last_health_reason, "running-cached")
        self.assertEqual(service._last_auto_metrics["relay_intent"], 1)

        service.auto_night_lock_stop = True
        self.assertTrue(controller._handle_relay_on(2500.0, 0.0, 60.0, True, 101.0, False))
        self.assertEqual(service._last_health_reason, "running")
        self.assertIsNone(service.auto_stop_condition_reason)
        delattr(service, "auto_night_lock_stop")
        self.assertIs(controller._night_lock_stop_requested(False), False)
        self.assertIsNone(controller._relay_on_stop_reason(2500.0, 0.0, 60.0, False, True))

        self.assertEqual(controller._policy_relay_on_stop_reason(2500.0, 0.0, 39.9), "auto-stop-soc")
        self.assertIsNone(controller._policy_relay_on_stop_reason(2500.0, 0.0, service.auto_min_soc))
        self.assertEqual(controller._policy_relay_on_stop_reason(2500.0, service.auto_stop_grid_import_watts, 60.0), "auto-stop-grid")
        self.assertEqual(controller._policy_relay_on_stop_reason(800.0, 0.0, 60.0), "auto-stop-surplus")

        service.auto_night_lock_stop = True
        self.assertEqual(
            controller._relay_on_stop_reason(2500.0, 0.0, 60.0, False, True),
            "night-lock",
        )
        self.assertIsNone(controller._relay_on_stop_reason(0.0, 500.0, 30.0, False, False))

        service.auto_night_lock_stop = False
        service.auto_stop_condition_since = None
        service.auto_stop_condition_reason = None
        self.assertTrue(
            controller._handle_relay_on(2500.0, service.auto_stop_grid_import_watts, 60.0, True, 102.0, True)
        )
        self.assertEqual(service.auto_stop_condition_reason, "auto-stop-grid")
        self.assertEqual(service._last_health_reason, "running-cached")

        service.auto_stop_condition_since = None
        service.auto_stop_condition_reason = None
        self.assertTrue(controller._handle_relay_on(2500.0, 0.0, 39.9, True, 103.0, False))
        self.assertEqual(service.auto_stop_condition_reason, "auto-stop-soc")
        self.assertEqual(service._last_health_reason, "running")

        service.auto_night_lock_stop = True
        service.auto_stop_condition_since = None
        service.auto_stop_condition_reason = None
        self.assertTrue(controller._handle_relay_on(2500.0, 0.0, 60.0, False, 104.0, True))
        self.assertEqual(service.auto_stop_condition_reason, "night-lock")
        self.assertEqual(service._last_health_reason, "running-cached")

        service.auto_night_lock_stop = False
        service.auto_stop_condition_since = 100.0
        service.auto_stop_condition_reason = "auto-stop-soc"
        self.assertFalse(controller._handle_relay_on(2500.0, 0.0, 39.9, True, 131.0, False))
        self.assertEqual(service._last_health_reason, "auto-stop")

    def test_relay_off_routes_start_or_waiting_with_exact_inputs(self):
        controller, service = self._make_controller()
        service.auto_start_condition_since = 100.0

        self.assertTrue(
            controller._handle_relay_off(
                service.auto_start_surplus_watts,
                service.auto_start_max_grid_import_watts,
                service.auto_resume_soc,
                True,
                110.0,
                True,
            )
        )
        self.assertEqual(service._last_health_reason, "auto-start-cached")

        service._save_runtime_state.reset_mock()
        service.auto_start_condition_since = 100.0
        service.auto_stop_condition_since = 77.0
        self.assertFalse(
            controller._handle_relay_off(
                service.auto_start_surplus_watts - 0.1,
                0.0,
                45.0,
                True,
                111.0,
                True,
            )
        )
        self.assertIsNone(service.auto_start_condition_since)
        self.assertIsNone(service.auto_stop_condition_since)
        self.assertEqual(service._last_health_reason, "waiting-surplus-cached")
        self.assertEqual(service._last_auto_metrics["relay_intent"], 0)

        service.virtual_autostart = 0
        self.assertFalse(controller._handle_relay_off(2500.0, 0.0, 60.0, True, 112.0, True))
        self.assertEqual(service._last_health_reason, "autostart-disabled-cached")

        service.virtual_autostart = 1
        service.auto_start_condition_since = 100.0
        self.assertFalse(
            controller._handle_relay_off(
                service.auto_start_surplus_watts + 100.0,
                service.auto_start_max_grid_import_watts + 0.1,
                service.auto_resume_soc,
                True,
                113.0,
                True,
            )
        )
        self.assertIsNone(service.auto_start_condition_since)
        self.assertEqual(service._last_health_reason, "waiting-grid-cached")

    def test_scheduled_night_clears_stale_daytime_stop_timer_while_relay_stays_on(self):
        controller, service = self._make_controller()
        service.virtual_mode = 2
        service.auto_stop_condition_since = 100.0
        service.auto_stop_condition_reason = "auto-stop-surplus"
        service.auto_start_condition_since = 90.0

        with patch.object(controller, "_handle_common_runtime_gates", return_value=True) as gates:
            self.assertTrue(controller._scheduled_night_decision(False, 199.0, True))
        gates.assert_called_once_with(False, 199.0, True)

        self.assertTrue(controller._scheduled_night_decision(True, 200.0, False))

        self.assertIsNone(service.auto_start_condition_since)
        self.assertIsNone(service.auto_stop_condition_since)
        self.assertIsNone(service.auto_stop_condition_reason)
        self.assertEqual(service._last_health_reason, "scheduled-night-charge")
        self.assertEqual(service._last_auto_metrics["relay_intent"], 1)

        service.auto_stop_condition_since = 100.0
        service.auto_stop_condition_reason = "auto-stop-grid"
        self.assertTrue(controller._scheduled_night_decision(True, 201.0, True))
        self.assertIsNone(service.auto_stop_condition_since)
        self.assertIsNone(service.auto_stop_condition_reason)
        self.assertEqual(service._last_health_reason, "scheduled-night-charge-cached")

    def test_scheduled_night_start_blockers_are_explicit(self):
        controller, service = self._make_controller()
        service.virtual_autostart = 0

        self.assertEqual(controller._scheduled_night_blocked_health(service, 100.0), "autostart-disabled")
        self.assertFalse(controller._scheduled_night_decision(False, 100.0, True))
        self.assertEqual(service._last_health_reason, "autostart-disabled-cached")

        service.virtual_autostart = 1
        service.relay_last_off_at = 95.0
        service.auto_min_offtime_seconds = 10.0
        self.assertEqual(controller._scheduled_night_blocked_health(service, 100.0), "waiting-offtime")
        self.assertFalse(controller._scheduled_night_decision(False, 100.0, True))
        self.assertEqual(service._last_health_reason, "waiting-offtime-cached")

        service.relay_last_off_at = 80.0
        service.auto_start_condition_since = 90.0
        service.auto_stop_condition_since = 91.0
        service.auto_stop_condition_reason = "auto-stop-surplus"
        self.assertTrue(controller._scheduled_night_decision(False, 100.0, True))
        self.assertIsNone(service.auto_start_condition_since)
        self.assertIsNone(service.auto_stop_condition_since)
        self.assertIsNone(service.auto_stop_condition_reason)
        self.assertEqual(service._last_health_reason, "scheduled-night-charge-cached")
        self.assertEqual(service._last_auto_metrics["relay_intent"], 1)

    def test_pre_average_and_auto_decide_cover_terminal_and_averaging_paths(self):
        controller, service = self._make_controller()

        with patch.object(controller, "_resolve_battery_soc", return_value=(None, True)):
            decision, battery_soc = controller._pre_average_decision(False, 2200.0, 55.0, -2100.0, 100.0, False)
        self.assertTrue(decision)
        self.assertIsNone(battery_soc)

        with patch("venus_evcharger.auto.workflow.time.time", return_value=100.0):
            with patch.object(controller, "_pre_average_decision", return_value=(controller._NO_DECISION, 55.0)):
                with patch.object(controller, "_update_average_metrics", return_value=(None, None)):
                    self.assertTrue(controller.auto_decide_relay(True, 2200.0, 55.0, -2100.0))
        self.assertEqual(service._last_health_reason, "averaging")

    def test_grid_recovery_gate_blocks_restart_until_grid_is_stable_again(self):
        controller, service = self._make_controller()

        self.assertFalse(controller._handle_grid_missing(False, 100.0, False))
        self.assertEqual(service._last_health_reason, "grid-missing")
        self.assertTrue(service._grid_recovery_required)
        self.assertIsNone(service._grid_recovery_since)

        decision, battery_soc = controller._pre_average_decision(False, 2200.0, 55.0, -2100.0, 101.0, False)
        self.assertFalse(decision)
        self.assertIsNone(battery_soc)
        self.assertEqual(service._last_health_reason, "waiting-grid-recovery")
        self.assertEqual(service._grid_recovery_since, 101.0)

        decision, battery_soc = controller._pre_average_decision(False, 2200.0, 55.0, -2100.0, 105.0, False)
        self.assertFalse(decision)
        self.assertIsNone(battery_soc)
        self.assertEqual(service._last_health_reason, "waiting-grid-recovery")

        decision, battery_soc = controller._pre_average_decision(False, 2200.0, 55.0, -2100.0, 111.0, False)
        self.assertIs(decision, controller._NO_DECISION)
        self.assertEqual(battery_soc, 55.0)
        self.assertFalse(service._grid_recovery_required)

    def test_grid_recovery_gate_is_not_armed_on_clean_start_without_prior_grid_loss(self):
        controller, service = self._make_controller()

        decision, battery_soc = controller._pre_average_decision(False, 2200.0, 55.0, -2100.0, 101.0, False)

        self.assertIs(decision, controller._NO_DECISION)
        self.assertEqual(battery_soc, 55.0)
        self.assertFalse(service._grid_recovery_required)
        self.assertIsNone(service._grid_recovery_since)

    def test_grid_recovery_gate_does_not_block_running_relay(self):
        controller, service = self._make_controller()

        controller._handle_grid_missing(True, 100.0, False)

        decision, battery_soc = controller._pre_average_decision(True, 2200.0, 55.0, -2100.0, 101.0, False)
        self.assertIs(decision, controller._NO_DECISION)
        self.assertEqual(battery_soc, 55.0)
        self.assertTrue(service._grid_recovery_required)
        self.assertEqual(service._grid_recovery_since, 101.0)

    def test_cutover_pending_stays_blocked_until_relay_off_is_confirmed(self):
        controller, service = self._make_controller()
        service._auto_mode_cutover_pending = True
        service._last_pm_status_confirmed = False
        service._peek_pending_relay_command = MagicMock(return_value=(None, None))

        decision = controller._handle_cutover_pending(False, False)

        self.assertFalse(decision)
        self.assertTrue(service._auto_mode_cutover_pending)
        self.assertIs(service._ignore_min_offtime_once, False)
        self.assertEqual(service._last_health_reason, "mode-transition")
        service._save_runtime_state.assert_not_called()

    def test_cutover_pending_clears_only_after_confirmed_relay_off(self):
        controller, service = self._make_controller()
        service._auto_mode_cutover_pending = True
        service._last_pm_status_confirmed = True
        service._last_confirmed_pm_status = {"output": False}
        service._last_confirmed_pm_status_at = 999.5
        service._relay_sync_requested_at = 999.0
        service._peek_pending_relay_command = MagicMock(return_value=(None, None))

        decision = controller._handle_cutover_pending(False, False)

        self.assertIs(decision, controller._NO_DECISION)
        self.assertIs(service._auto_mode_cutover_pending, False)
        self.assertTrue(service._ignore_min_offtime_once)
        service._save_runtime_state.assert_called_once()

    def test_cutover_pending_ignores_confirmed_off_sample_from_before_cutover_request(self):
        controller, service = self._make_controller()
        service._auto_mode_cutover_pending = True
        service._last_pm_status_confirmed = True
        service._last_confirmed_pm_status = {"output": False}
        service._last_confirmed_pm_status_at = 998.0
        service._relay_sync_requested_at = 999.0
        service._peek_pending_relay_command = MagicMock(return_value=(None, None))

        decision = controller._handle_cutover_pending(False, False)

        self.assertFalse(decision)
        self.assertTrue(service._auto_mode_cutover_pending)
        self.assertIs(service._ignore_min_offtime_once, False)
        self.assertEqual(service._last_health_reason, "mode-transition")
        service._save_runtime_state.assert_not_called()

    def test_cutover_pending_uses_fallback_last_pm_status_when_confirmed_cache_is_missing(self):
        controller, service = self._make_controller()
        service._auto_mode_cutover_pending = True
        service._peek_pending_relay_command = MagicMock(return_value=(None, None))
        service._last_confirmed_pm_status = None
        service._last_confirmed_pm_status_at = None
        service._last_pm_status_confirmed = True
        service._last_pm_status = {"output": False}
        service._last_pm_status_at = 999.5
        service._relay_sync_requested_at = 999.0
        controller._learning_policy_now = MagicMock(return_value=1000.0)

        decision = controller._handle_cutover_pending(False, False)

        self.assertIs(decision, controller._NO_DECISION)
        self.assertIs(service._auto_mode_cutover_pending, False)
        self.assertTrue(service._ignore_min_offtime_once)

    def test_cutover_confirmed_helpers_cover_missing_and_stale_timestamps(self):
        controller, service = self._make_controller()
        service._relay_sync_requested_at = 999.0
        service._worker_poll_interval_seconds = 1.0
        service.relay_sync_timeout_seconds = 2.0

        self.assertFalse(controller._cutover_confirmed_sample_fresh(None, 1000.0))
        self.assertTrue(controller._cutover_confirmed_sample_fresh(999.5, 1000.0))
        self.assertFalse(controller._cutover_confirmed_after_request(None))
        self.assertFalse(controller._cutover_confirmed_after_request(998.0))

    def test_cutover_confirmed_after_request_accepts_missing_request_timestamp(self):
        controller, service = self._make_controller()
        service._relay_sync_requested_at = None

        self.assertTrue(controller._cutover_confirmed_after_request(998.0))

    def test_learned_charge_power_age_helpers_cover_missing_update_timestamp(self):
        controller, service = self._make_controller()
        service.learned_charge_power_updated_at = None

        self.assertIsNone(controller._learned_charge_power_age_seconds(1000.0))
        self.assertTrue(controller._learned_charge_power_expired(1000.0))

    def test_surplus_thresholds_fall_back_to_static_profile_when_scaled_thresholds_become_invalid(self):
        controller, _service = self._make_controller()
        with patch.object(controller, "_scale_surplus_thresholds", return_value=(800.0, 1200.0)):
            self.assertEqual(controller._surplus_thresholds_for_soc(55.0), (1650.0, 800.0, "high-soc"))

    def test_auto_policy_synthesis_tolerates_read_only_service_attributes(self):
        class LockedAutoPolicyService(SimpleNamespace):
            def __setattr__(self, name, value):
                if name == "auto_policy":
                    raise AttributeError("read-only")
                super().__setattr__(name, value)

        service = LockedAutoPolicyService(**vars(make_auto_controller_service()))
        controller = AutoDecisionController(service, _health_code, _mode_uses_auto_logic)

        policy = controller._auto_policy()

        self.assertEqual(policy.normal_profile.start_surplus_watts, 2000.0)
        self.assertFalse(hasattr(service, "auto_policy"))

    def test_grid_recovery_gate_clears_immediately_when_delay_is_zero(self):
        controller, service = self._make_controller()
        service.auto_grid_recovery_start_seconds = 0.0
        service._grid_recovery_required = True
        service._grid_recovery_since = None

        decision = controller._handle_grid_recovery_start_gate(False, 123.0, False)

        self.assertIs(decision, controller._NO_DECISION)
        self.assertFalse(service._grid_recovery_required)
        self.assertEqual(service._grid_recovery_since, 123.0)

        service._grid_recovery_required = True
        service._grid_recovery_since = None
        self.assertFalse(controller._grid_recovery_completes_immediately(124.0, 0.5))
        self.assertTrue(service._grid_recovery_required)
        self.assertIsNone(service._grid_recovery_since)

    def test_grid_recovery_gate_keeps_running_relay_unchanged_while_recovery_window_is_open(self):
        controller, service = self._make_controller()
        service.auto_grid_recovery_start_seconds = 30.0
        service._grid_recovery_required = True
        service._grid_recovery_since = 110.0

        decision = controller._handle_grid_recovery_start_gate(True, 123.0, False)

        self.assertIs(decision, controller._NO_DECISION)
        self.assertTrue(service._grid_recovery_required)
        self.assertEqual(service._grid_recovery_since, 110.0)

    def test_missing_battery_soc_helpers_cover_suppressed_warning_and_invalid_value_without_callback(self):
        controller, service = self._make_controller()
        service._last_battery_allow_warning = 995.0
        service.auto_battery_scan_interval_seconds = 10.0
        service._warning_throttled = None

        with patch("venus_evcharger.auto.logic_gates.logging.warning") as warning_mock:
            soc, decision = controller._allowed_missing_battery_soc(False, 1000.0, False)

        self.assertEqual(soc, float(controller._auto_policy().resume_soc))
        self.assertIs(decision, controller._NO_DECISION)
        warning_mock.assert_not_called()
        self.assertIsNone(controller._normalized_battery_soc(120.0))

    def test_normalized_battery_soc_contract_covers_edges_and_warning_callback(self):
        controller, service = self._make_controller()
        service._last_battery_allow_warning = 123.0
        service._warning_throttled = MagicMock()
        service.auto_battery_scan_interval_seconds = 0.5

        self.assertEqual(controller._normalized_battery_soc(0.0), 0.0)
        self.assertIsNone(service._last_battery_allow_warning)

        service._last_battery_allow_warning = 123.0
        self.assertEqual(controller._normalized_battery_soc(100.0), 100.0)
        self.assertIsNone(service._last_battery_allow_warning)

        self.assertIsNone(controller._normalized_battery_soc(101.0))
        service._warning_throttled.assert_called_once_with(
            "battery-soc-invalid",
            1.0,
            "Auto mode ignored out-of-range battery SOC %s",
            101.0,
        )

    def test_missing_battery_soc_allowed_warning_threshold_and_health_contract(self):
        controller, service = self._make_controller()
        service.auto_allow_without_battery_soc = True
        service._last_battery_allow_warning = None
        service.auto_battery_scan_interval_seconds = 10.0

        with patch("venus_evcharger.auto.logic_gates.logging.warning") as warning_mock:
            soc, decision = controller._resolve_battery_soc(None, True, 1000.0, True)

        self.assertEqual(soc, service.auto_resume_soc)
        self.assertIs(decision, controller._NO_DECISION)
        warning_mock.assert_called_once_with("Auto mode: battery SOC missing, allowing Auto based on resume SOC.")
        self.assertEqual(service._last_battery_allow_warning, 1000.0)
        self.assertEqual(service._last_health_reason, "battery-soc-missing-allowed-cached")
        self.assertEqual(service._last_auto_metrics["relay_intent"], 1)

        with patch("venus_evcharger.auto.logic_gates.logging.warning") as second_warning:
            soc, decision = controller._resolve_battery_soc(None, False, 1010.0, False)

        self.assertEqual(soc, service.auto_resume_soc)
        self.assertIs(decision, controller._NO_DECISION)
        second_warning.assert_not_called()
        self.assertEqual(service._last_battery_allow_warning, 1000.0)
        self.assertEqual(service._last_health_reason, "battery-soc-missing-allowed")
        self.assertEqual(service._last_auto_metrics["relay_intent"], 0)

    def test_missing_battery_soc_blocked_contract_and_missing_allow_flag_default(self):
        controller, service = self._make_controller()
        delattr(service, "auto_allow_without_battery_soc")
        service._last_auto_state = "starting"

        soc, decision = controller._resolve_battery_soc(None, True, 1000.0, True)

        self.assertIsNone(soc)
        self.assertTrue(decision)
        self.assertEqual(service._last_health_reason, "battery-soc-missing-cached")
        self.assertEqual(service._last_auto_metrics["relay_intent"], 1)

        service.auto_allow_without_battery_soc = 1
        soc, decision = controller._resolve_battery_soc(None, False, 1001.0, False)

        self.assertIsNone(soc)
        self.assertFalse(decision)
        self.assertEqual(service._last_health_reason, "battery-soc-missing")

    def test_common_runtime_gates_cover_warmup_and_manual_override_boundaries(self):
        controller, service = self._make_controller()
        service.started_at = 100.0
        service.auto_startup_warmup_seconds = 20.0
        service._last_auto_state = "starting"

        self.assertTrue(controller._handle_common_runtime_gates(True, 119.9, True))
        self.assertEqual(service._last_health_reason, "warmup-cached")
        self.assertEqual(service._last_auto_metrics["relay_intent"], 1)

        self.assertIs(controller._handle_common_runtime_gates(True, 120.0, True), controller._NO_DECISION)

        service.manual_override_until = 200.0
        self.assertFalse(controller._handle_common_runtime_gates(False, 199.9, True))
        self.assertEqual(service._last_health_reason, "manual-override-cached")
        self.assertEqual(service._last_auto_metrics["relay_intent"], 0)

        self.assertIs(controller._handle_common_runtime_gates(False, 200.0, True), controller._NO_DECISION)

    def test_missing_inputs_idle_path_clears_samples_and_preserves_cached_flag(self):
        controller, service = self._make_controller()
        with patch.object(controller, "_clear_auto_start_tracking", wraps=controller._clear_auto_start_tracking) as clear:
            decision = controller._handle_missing_inputs(False, 55.0, None, 1000.0, True)

        self.assertFalse(decision)
        clear.assert_any_call(clear_samples=True)
        self.assertEqual(clear.call_args_list[0].kwargs, {"clear_samples": True})
        self.assertEqual(service._last_health_reason, "inputs-missing-cached")
        self.assertIsNone(service.auto_stop_condition_since)

    def test_missing_inputs_running_path_waits_for_minimum_runtime(self):
        controller, service = self._make_controller()
        service.relay_last_changed_at = 995.0
        service.auto_min_runtime_seconds = 10.0

        decision = controller._handle_missing_inputs(True, 55.0, 400.0, 1000.0, True)

        self.assertTrue(decision)
        self.assertEqual(service._last_health_reason, "inputs-missing-cached")
        self.assertEqual(service._last_auto_metrics["relay_intent"], 1)
        self.assertIsNone(service.auto_stop_condition_since)

    def test_missing_inputs_arms_and_fires_delayed_policy_stop(self):
        controller, service = self._make_controller()
        service.relay_last_changed_at = 0.0
        service.auto_stop_delay_seconds = 5.0

        self.assertTrue(controller._handle_missing_inputs(True, 55.0, 400.0, 1000.0, False))
        self.assertEqual(service.auto_stop_condition_since, 1000.0)
        self.assertEqual(service.auto_stop_condition_reason, "auto-stop")
        self.assertEqual(service._last_health_reason, "inputs-missing")

        self.assertFalse(controller._handle_missing_inputs(True, 55.0, 400.0, 1006.0, True))
        self.assertEqual(service._last_health_reason, "auto-stop-cached")

    def test_grid_recently_read_contract_covers_missing_and_boundary_timestamps(self):
        controller, service = self._make_controller()
        if hasattr(service, "_last_grid_at"):
            delattr(service, "_last_grid_at")

        self.assertFalse(controller._grid_recently_read(None, 1000.0))
        self.assertTrue(controller._grid_recently_read(0.0, 1000.0))

        service._last_grid_at = 940.0
        service.auto_grid_missing_stop_seconds = 60.0
        self.assertTrue(controller._grid_recently_read(None, 1000.0))

        service._last_grid_at = 939.9
        self.assertFalse(controller._grid_recently_read(0.0, 1000.0))

        service.auto_grid_missing_stop_seconds = 5.0
        service._last_grid_at = 995.0
        self.assertTrue(controller._grid_recently_read(None, 1000.0))

        service._last_grid_at = 994.9
        self.assertFalse(controller._grid_recently_read(None, 1000.0))

        delattr(service, "auto_grid_missing_stop_seconds")
        service._last_grid_at = 939.5
        self.assertFalse(controller._grid_recently_read(None, 1000.0))

    def test_cutover_pending_contracts_cover_no_pending_and_blocked_cached_health(self):
        controller, service = self._make_controller()
        service._auto_mode_cutover_pending = False

        self.assertIs(controller._handle_cutover_pending(True, True), controller._NO_DECISION)
        self.assertIs(service._ignore_min_offtime_once, False)

        service._auto_mode_cutover_pending = 1
        self.assertIs(controller._handle_cutover_pending(True, True), controller._NO_DECISION)
        self.assertIs(service._ignore_min_offtime_once, False)

        service._auto_mode_cutover_pending = None
        self.assertIs(controller._handle_cutover_pending(True, True), controller._NO_DECISION)
        self.assertIs(service._ignore_min_offtime_once, False)

        delattr(service, "_auto_mode_cutover_pending")
        self.assertIs(controller._handle_cutover_pending(True, True), controller._NO_DECISION)

        service._auto_mode_cutover_pending = True
        service.auto_start_condition_since = 900.0
        service.auto_stop_condition_since = 901.0
        service.auto_stop_condition_reason = "auto-stop"
        service.auto_samples.append((900.0, 100.0, 10.0))
        service._peek_pending_relay_command = MagicMock(return_value=(False, 999.0))
        service._last_confirmed_pm_status = {"output": True}
        service._last_confirmed_pm_status_at = 1000.0
        controller._learning_policy_now = MagicMock(return_value=1000.0)

        self.assertFalse(controller._handle_cutover_pending(False, True))
        self.assertEqual(list(service.auto_samples), [])
        self.assertIsNone(service.auto_start_condition_since)
        self.assertIsNone(service.auto_stop_condition_since)
        self.assertIsNone(service.auto_stop_condition_reason)
        self.assertTrue(service._auto_mode_cutover_pending)
        self.assertIs(service._ignore_min_offtime_once, False)
        self.assertEqual(service._last_health_reason, "mode-transition-cached")
        self.assertEqual(service._last_auto_metrics["relay_intent"], 0)
        service._save_runtime_state.assert_not_called()

        service._auto_mode_cutover_pending = True
        service._last_confirmed_pm_status = {"output": False}
        service._last_confirmed_pm_status_at = 1000.0
        service._relay_sync_requested_at = None
        service._peek_pending_relay_command = MagicMock(return_value=(None, None))
        controller._learning_policy_now = MagicMock(return_value=1000.0)

        self.assertFalse(controller._handle_cutover_pending(True, True))
        self.assertIs(service._auto_mode_cutover_pending, True)
        self.assertEqual(service._last_health_reason, "mode-transition-cached")
        self.assertEqual(service._last_auto_metrics["relay_intent"], 0)

    def test_cutover_confirmation_contracts_reject_on_relay_and_missing_or_stale_samples(self):
        controller, service = self._make_controller()
        service._peek_pending_relay_command = MagicMock(return_value=(None, None))
        controller._learning_policy_now = MagicMock(return_value=1000.0)
        service._relay_sync_requested_at = 999.0
        service._worker_poll_interval_seconds = 1.0
        service.relay_sync_timeout_seconds = 2.0

        service._last_confirmed_pm_status = {"output": False}
        service._last_confirmed_pm_status_at = 1000.0
        self.assertFalse(controller._cutover_relay_off_confirmed(True, 1000.0))

        service._peek_pending_relay_command = MagicMock(return_value=(True, 999.0))
        self.assertFalse(controller._cutover_relay_off_confirmed(False, 1000.0))

        service._peek_pending_relay_command = MagicMock(return_value=(None, None))
        service._last_confirmed_pm_status = {}
        self.assertFalse(controller._cutover_relay_off_confirmed(False, 1000.0))

        service._last_confirmed_pm_status = {"output": False}
        service._last_confirmed_pm_status_at = None
        self.assertFalse(controller._cutover_relay_off_confirmed(False, 1000.0))

        service._last_confirmed_pm_status = {"output": False}
        service._last_confirmed_pm_status_at = 996.9
        self.assertFalse(controller._cutover_relay_off_confirmed(False, 1000.0))

        service._last_confirmed_pm_status_at = 999.0
        self.assertTrue(controller._cutover_relay_off_confirmed(False, 1000.0))

        service._last_confirmed_pm_status = {"output": True}
        self.assertFalse(controller._cutover_relay_off_confirmed(False, 1000.0))

        service._last_confirmed_pm_status = {"output": False}
        service._last_confirmed_pm_status_at = 998.9
        service._relay_sync_requested_at = 999.0
        self.assertFalse(controller._cutover_relay_off_confirmed(False, 1000.0))

        service._last_confirmed_pm_status = {"output": False}
        service._last_confirmed_pm_status_at = 998.5
        service._relay_sync_requested_at = None
        service._worker_poll_interval_seconds = 0.5
        service.relay_sync_timeout_seconds = 10.0
        self.assertFalse(controller._cutover_relay_off_confirmed(False, 1000.0))

    def test_grid_missing_contracts_cover_running_holdoff_and_delayed_stop(self):
        controller, service = self._make_controller()
        service.auto_start_condition_since = 900.0
        service.auto_samples.append((900.0, 100.0, 100.0))
        service.relay_last_changed_at = 995.0
        service.auto_min_runtime_seconds = 10.0

        self.assertTrue(controller._handle_grid_missing(True, 1000.0, True))
        self.assertEqual(list(service.auto_samples), [])
        self.assertIsNone(service.auto_start_condition_since)
        self.assertEqual(service._last_health_reason, "grid-missing-cached")
        self.assertEqual(service._last_auto_metrics["relay_intent"], 1)
        self.assertTrue(service._grid_recovery_required)
        self.assertIsNone(service._grid_recovery_since)
        self.assertIsNone(service.auto_stop_condition_since)

        service.relay_last_changed_at = 0.0
        service.auto_stop_delay_seconds = 4.0
        self.assertTrue(controller._handle_grid_missing(True, 1001.0, False))
        self.assertEqual(service.auto_stop_condition_since, 1001.0)
        self.assertEqual(service.auto_stop_condition_reason, "grid-missing")
        self.assertEqual(service._last_health_reason, "grid-missing")

        self.assertFalse(controller._handle_grid_missing(True, 1005.0, True))
        self.assertEqual(service._last_health_reason, "grid-missing-cached")
        self.assertEqual(service._last_auto_metrics["relay_intent"], 0)

        service.auto_stop_condition_since = None
        service.auto_stop_condition_reason = None
        self.assertFalse(controller._handle_grid_missing(False, 1006.0, True))
        self.assertEqual(service._last_health_reason, "grid-missing-cached")
        self.assertEqual(service._last_auto_metrics["relay_intent"], 0)

    def test_minimum_runtime_and_offtime_contracts_cover_boundaries_and_override(self):
        controller, service = self._make_controller()
        service.relay_last_changed_at = None
        self.assertTrue(controller._minimum_runtime_elapsed(1000.0))

        service.relay_last_changed_at = 990.0
        service.auto_min_runtime_seconds = 10.0
        self.assertTrue(controller._minimum_runtime_elapsed(1000.0))

        service.relay_last_changed_at = 990.1
        self.assertFalse(controller._minimum_runtime_elapsed(1000.0))

        service._ignore_min_offtime_once = False
        service.relay_last_off_at = None
        self.assertTrue(controller._minimum_offtime_elapsed(1000.0))

        service.relay_last_off_at = 990.0
        service.auto_min_offtime_seconds = 10.0
        self.assertTrue(controller._minimum_offtime_elapsed(1000.0))

        service.relay_last_off_at = 990.1
        self.assertFalse(controller._minimum_offtime_elapsed(1000.0))

        service._ignore_min_offtime_once = True
        self.assertTrue(controller._minimum_offtime_elapsed(1000.0))

        service._ignore_min_offtime_once = 1
        self.assertFalse(controller._minimum_offtime_elapsed(1000.0))

        delattr(service, "_ignore_min_offtime_once")
        service.relay_last_off_at = 990.1
        self.assertFalse(controller._minimum_offtime_elapsed(1000.0))

    def test_mode_reset_contracts_preserve_expected_relay_decision_and_clear_transients(self):
        controller, service = self._make_controller()
        service.auto_samples.append((90.0, 100.0, 10.0))
        service.auto_start_condition_since = 91.0
        service.auto_stop_condition_since = 92.0
        service.auto_stop_condition_reason = "auto-stop"
        service._auto_mode_cutover_pending = True
        service._ignore_min_offtime_once = True

        self.assertTrue(controller._handle_non_auto_mode(True))
        self.assertEqual(list(service.auto_samples), [])
        self.assertIsNone(service.auto_start_condition_since)
        self.assertIsNone(service.auto_stop_condition_since)
        self.assertIs(service._auto_mode_cutover_pending, False)
        self.assertIs(service._ignore_min_offtime_once, False)
        self.assertEqual(service._last_auto_state, "idle")
        service._save_runtime_state.assert_called_once_with()

        service._save_runtime_state.reset_mock()
        service.auto_samples.append((93.0, 200.0, 20.0))
        service.auto_start_condition_since = 94.0
        service.auto_stop_condition_since = 95.0
        service.auto_stop_condition_reason = "grid-missing"
        service._auto_mode_cutover_pending = True
        service._ignore_min_offtime_once = True

        self.assertFalse(controller._handle_non_auto_mode(False))
        self.assertEqual(list(service.auto_samples), [])
        self.assertIsNone(service.auto_start_condition_since)
        self.assertIsNone(service.auto_stop_condition_since)
        self.assertIs(service._auto_mode_cutover_pending, False)
        self.assertFalse(service._ignore_min_offtime_once)
        self.assertEqual(service._last_auto_state, "idle")
        service._save_runtime_state.assert_called_once_with()

    def test_disabled_mode_contract_clears_stop_reason_and_reports_relay_off_intent(self):
        controller, service = self._make_controller()
        service.auto_samples.append((90.0, 100.0, 10.0))
        service.auto_start_condition_since = 91.0
        service.auto_stop_condition_since = 92.0
        service.auto_stop_condition_reason = "auto-stop"
        service._auto_mode_cutover_pending = True
        service._ignore_min_offtime_once = True
        service._last_pm_status_confirmed = True
        service._last_confirmed_pm_status = {"output": True}
        service._last_confirmed_pm_status_at = 1000.0
        controller._learning_policy_now = MagicMock(return_value=1000.0)

        self.assertFalse(controller._handle_disabled_mode(cached_inputs=False))
        self.assertEqual(list(service.auto_samples), [])
        self.assertIsNone(service.auto_start_condition_since)
        self.assertIsNone(service.auto_stop_condition_since)
        self.assertIsNone(service.auto_stop_condition_reason)
        self.assertIs(service._auto_mode_cutover_pending, False)
        self.assertFalse(service._ignore_min_offtime_once)
        self.assertEqual(service._last_health_reason, "disabled")
        self.assertEqual(service._last_auto_metrics["relay_intent"], 0)
        service._save_runtime_state.assert_called_once_with()

    def test_missing_input_stop_reason_contracts_cover_night_window_soc_and_grid(self):
        controller, service = self._make_controller()
        service.auto_night_lock_stop = False
        service.auto_min_soc = 40.0
        service.auto_stop_grid_import_watts = 500.0

        self.assertEqual(controller._known_missing_input_stop_reason(60.0, 0.0, False), "night-lock")

        service.auto_night_lock_stop = True
        self.assertEqual(controller._known_missing_input_stop_reason(60.0, 0.0, True), "night-lock")

        service.auto_night_lock_stop = 1
        self.assertEqual(controller._known_missing_input_stop_reason(60.0, 0.0, True), None)

        service.auto_night_lock_stop = False
        self.assertEqual(controller._known_missing_input_stop_reason(39.9, 0.0, True), "auto-stop")
        self.assertEqual(controller._known_missing_input_stop_reason(40.0, 0.0, True), None)
        self.assertEqual(controller._known_missing_input_stop_reason(60.0, 500.0, True), "auto-stop")
        self.assertEqual(controller._known_missing_input_stop_reason(60.0, 499.9, True), None)
        self.assertEqual(controller._known_missing_input_stop_reason(60.0, None, True), None)

        delattr(service, "auto_night_lock_stop")
        self.assertEqual(controller._known_missing_input_stop_reason(60.0, 0.0, True), None)

    def test_stop_tracking_helpers_preserve_reason_until_reset_is_required(self):
        controller, service = self._make_controller()
        service.auto_stop_condition_since = None
        service.auto_stop_condition_reason = None

        self.assertTrue(controller._reset_stop_tracking(1000.0, "auto-stop"))
        self.assertEqual(service.auto_stop_condition_since, 1000.0)
        self.assertEqual(service.auto_stop_condition_reason, "auto-stop")

        self.assertFalse(controller._reset_stop_tracking(1001.0, "auto-stop"))
        self.assertEqual(service.auto_stop_condition_since, 1000.0)
        self.assertEqual(service.auto_stop_condition_reason, "auto-stop")

        self.assertTrue(controller._reset_stop_tracking(1002.0, "grid-missing"))
        self.assertEqual(service.auto_stop_condition_since, 1002.0)
        self.assertEqual(service.auto_stop_condition_reason, "grid-missing")

        service.auto_stop_condition_reason = None
        controller._ensure_stop_tracking_reason("grid-missing")
        self.assertEqual(service.auto_stop_condition_reason, "grid-missing")

        controller._ensure_stop_tracking_reason("auto-stop")
        self.assertEqual(service.auto_stop_condition_reason, "grid-missing")

        service.auto_stop_condition_since = None
        delattr(service, "auto_stop_condition_reason")
        self.assertTrue(controller._reset_stop_tracking(1003.0, "auto-stop"))
        self.assertEqual(service.auto_stop_condition_reason, "auto-stop")

        delattr(service, "auto_stop_condition_reason")
        controller._ensure_stop_tracking_reason("grid-missing")
        self.assertEqual(service.auto_stop_condition_reason, "grid-missing")

    def test_arm_or_fire_stop_contract_covers_default_custom_and_exact_boundary(self):
        controller, service = self._make_controller()
        service.auto_stop_delay_seconds = 10.0

        self.assertIs(controller._arm_or_fire_stop(1000.0, "auto-stop", False), controller._NO_DECISION)
        self.assertEqual(service.auto_stop_condition_since, 1000.0)
        self.assertEqual(service.auto_stop_condition_reason, "auto-stop")

        self.assertIs(controller._arm_or_fire_stop(1009.9, "auto-stop", False), controller._NO_DECISION)
        self.assertEqual(service._last_health_reason, "init")

        self.assertFalse(controller._arm_or_fire_stop(1010.0, "auto-stop", True))
        self.assertEqual(service._last_health_reason, "auto-stop-cached")
        self.assertEqual(service._last_auto_metrics["relay_intent"], 0)

        service.auto_stop_condition_since = 2000.0
        service.auto_stop_condition_reason = "grid-missing"
        self.assertIs(
            controller._arm_or_fire_stop(
                2004.9,
                "grid-missing",
                False,
                delay_seconds=5.0,
                stop_key="grid-missing",
            ),
            controller._NO_DECISION,
        )
        self.assertFalse(
            controller._arm_or_fire_stop(
                2005.0,
                "grid-missing",
                False,
                delay_seconds=5.0,
                stop_key="grid-missing",
            )
        )
        self.assertEqual(service._last_health_reason, "grid-missing")

        service.auto_stop_condition_since = 3000.0
        service.auto_stop_condition_reason = None
        service._last_confirmed_pm_status = {"output": True}
        service._last_confirmed_pm_status_at = 3009.9
        service._worker_poll_interval_seconds = 1.0
        service.relay_sync_timeout_seconds = 2.0
        controller._learning_policy_now = MagicMock(return_value=3010.0)
        self.assertFalse(controller._arm_or_fire_stop(3010.0, "auto-stop", False))
        self.assertEqual(service.auto_stop_condition_reason, "auto-stop")
        self.assertEqual(service._last_auto_metrics["relay_intent"], 0)

    def test_confirmed_cutover_pm_status_contract_prefers_explicit_confirmed_cache(self):
        controller, service = self._make_controller()
        service._last_confirmed_pm_status = {"output": False}
        service._last_confirmed_pm_status_at = 1000.0
        service._last_pm_status_confirmed = True
        service._last_pm_status = {"output": True}
        service._last_pm_status_at = 1001.0

        self.assertEqual(controller._confirmed_cutover_pm_status(), ({"output": False}, 1000.0))

        service._last_confirmed_pm_status = None
        service._last_confirmed_pm_status_at = None
        self.assertEqual(controller._confirmed_cutover_pm_status(), ({"output": True}, 1001.0))

        service._last_pm_status_confirmed = 1
        self.assertEqual(controller._confirmed_cutover_pm_status(), (None, None))

        service._last_pm_status_confirmed = False
        self.assertEqual(controller._confirmed_cutover_pm_status(), (None, None))

        service._last_pm_status_confirmed = True
        delattr(service, "_last_pm_status")
        self.assertEqual(controller._confirmed_cutover_pm_status(), (None, None))

        service._last_pm_status = {"output": False}
        delattr(service, "_last_pm_status_at")
        self.assertEqual(controller._confirmed_cutover_pm_status(), ({"output": False}, None))

    def test_cutover_freshness_contract_covers_exact_age_and_request_boundaries(self):
        controller, service = self._make_controller()
        service._worker_poll_interval_seconds = 1.0
        service.relay_sync_timeout_seconds = 2.0

        self.assertTrue(controller._cutover_confirmed_sample_fresh(998.0, 1000.0))
        self.assertFalse(controller._cutover_confirmed_sample_fresh(997.9, 1000.0))

        service._relay_sync_requested_at = 999.0
        self.assertTrue(controller._cutover_confirmed_after_request(999.0))
        self.assertFalse(controller._cutover_confirmed_after_request(998.9))

        delattr(service, "_relay_sync_requested_at")
        self.assertTrue(controller._cutover_confirmed_after_request(998.9))

    def test_grid_recovery_gate_active_contract_covers_missing_flags_and_waiting_health(self):
        controller, service = self._make_controller()

        if hasattr(service, "_grid_recovery_required"):
            delattr(service, "_grid_recovery_required")
        self.assertFalse(controller._grid_recovery_gate_active(service))

        service._grid_recovery_required = False
        if hasattr(service, "_grid_recovery_since"):
            delattr(service, "_grid_recovery_since")
        self.assertFalse(controller._grid_recovery_gate_active(service))

        service._grid_recovery_since = None
        self.assertFalse(controller._grid_recovery_gate_active(service))

        service._grid_recovery_required = 1
        self.assertFalse(controller._grid_recovery_gate_active(service))

        service._grid_recovery_required = True
        self.assertTrue(controller._grid_recovery_gate_active(service))

        service.auto_grid_recovery_start_seconds = 10.0
        decision = controller._handle_grid_recovery_start_gate(False, 1000.0, True)

        self.assertFalse(decision)
        self.assertEqual(service._grid_recovery_since, 1000.0)
        self.assertEqual(service._last_health_reason, "waiting-grid-recovery-cached")
        self.assertEqual(service._last_auto_metrics["relay_intent"], 0)

        decision = controller._handle_grid_recovery_start_gate(False, 1010.0, False)

        self.assertIs(decision, controller._NO_DECISION)
        self.assertIs(service._grid_recovery_required, False)

        service._grid_recovery_required = True
        service._grid_recovery_since = None
        self.assertTrue(controller._grid_recovery_completes_immediately(1020.0, 0.0))
        self.assertEqual(service._grid_recovery_since, 1020.0)
        self.assertIs(service._grid_recovery_required, False)

        service._grid_recovery_required = True
        delattr(service, "_grid_recovery_since")
        self.assertTrue(controller._grid_recovery_waiting(1030.0, 10.0))
        self.assertEqual(service._grid_recovery_since, 1030.0)

    def test_normalized_and_resolved_battery_soc_contracts_cover_lower_bound_and_valid_path(self):
        controller, service = self._make_controller()
        service._warning_throttled = MagicMock()
        service.auto_battery_scan_interval_seconds = 2.0
        service.auto_allow_without_battery_soc = False
        service._last_auto_state = "running"

        self.assertIsNone(controller._normalized_battery_soc(-0.1))
        service._warning_throttled.assert_called_once_with(
            "battery-soc-invalid",
            2.0,
            "Auto mode ignored out-of-range battery SOC %s",
            -0.1,
        )

        delattr(service, "_warning_throttled")
        self.assertIsNone(controller._normalized_battery_soc(100.1))

        service._warning_throttled = MagicMock()
        delattr(service, "auto_battery_scan_interval_seconds")
        self.assertIsNone(controller._normalized_battery_soc(100.2))
        service._warning_throttled.assert_called_once_with(
            "battery-soc-invalid",
            60.0,
            "Auto mode ignored out-of-range battery SOC %s",
            100.2,
        )

        service._warning_throttled.reset_mock()
        service.auto_battery_scan_interval_seconds = None
        self.assertIsNone(controller._normalized_battery_soc(100.3))
        service._warning_throttled.assert_called_once_with(
            "battery-soc-invalid",
            60.0,
            "Auto mode ignored out-of-range battery SOC %s",
            100.3,
        )

        service._warning_throttled.reset_mock()
        service.auto_battery_scan_interval_seconds = 1.0
        self.assertIsNone(controller._normalized_battery_soc(100.4))
        service._warning_throttled.assert_called_once_with(
            "battery-soc-invalid",
            1.0,
            "Auto mode ignored out-of-range battery SOC %s",
            100.4,
        )

        soc, decision = controller._resolve_battery_soc(42, True, 1001.0, False)

        self.assertEqual(soc, 42.0)
        self.assertIs(decision, controller._NO_DECISION)
        self.assertEqual(service._last_auto_state, "running")
        self.assertNotEqual(service._last_health_reason, "battery-soc-missing")

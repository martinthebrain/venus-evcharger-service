# SPDX-License-Identifier: GPL-3.0-or-later
from tests.venus_evcharger_update_cycle_controller_support import *
from venus_evcharger.auto.tracking import clear_auto_decision_tracking


class TestUpdateCycleControllerPrimary(UpdateCycleControllerTestBase):
    def test_clear_auto_decision_tracking_reports_whether_state_changed(self):
        service = SimpleNamespace(
            auto_start_condition_since=10.0,
            auto_stop_condition_since=None,
            auto_stop_condition_reason="auto-stop-surplus",
        )

        self.assertTrue(clear_auto_decision_tracking(service))
        self.assertIsNone(service.auto_start_condition_since)
        self.assertIsNone(service.auto_stop_condition_since)
        self.assertIsNone(service.auto_stop_condition_reason)
        self.assertFalse(clear_auto_decision_tracking(service))

    def test_runtime_state_save_best_effort_covers_missing_and_warning_paths(self):
        controller = UpdateCycleController(SimpleNamespace(_save_runtime_state=None), _phase_values, lambda reason: 0)
        controller.save_runtime_state_best_effort("missing")

        service = SimpleNamespace(
            _save_runtime_state=MagicMock(side_effect=RuntimeError("boom")),
            _warning_throttled=MagicMock(),
            auto_shelly_soft_fail_seconds=7.0,
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: 0)
        controller.save_runtime_state_best_effort("test")

        service._warning_throttled.assert_called_once()
        self.assertEqual(service._warning_throttled.call_args.args[0], "runtime-state-save-failed-test")

        service_without_warning = SimpleNamespace(
            _save_runtime_state=MagicMock(side_effect=RuntimeError("boom")),
            _warning_throttled=None,
        )
        controller = UpdateCycleController(service_without_warning, _phase_values, lambda reason: 0)
        controller.save_runtime_state_best_effort("silent")

    def test_update_virtual_state_keeps_dbus_publish_alive_when_runtime_save_fails(self):
        service = SimpleNamespace(
            charging_started_at=90.0,
            energy_at_start=1.0,
            phase="L1",
            virtual_startstop=0,
            virtual_enable=1,
            virtual_mode=1,
            _charger_backend=None,
            _time_now=MagicMock(return_value=100.0),
            _mode_uses_auto_logic=lambda mode: int(mode) in (1, 2),
            _ensure_observability_state=MagicMock(),
            _publish_energy_time_measurements=MagicMock(return_value=True),
            _publish_config_paths=MagicMock(return_value=False),
            _publish_diagnostic_paths=MagicMock(return_value=False),
            _save_runtime_state=MagicMock(side_effect=RuntimeError("save failed")),
            _warning_throttled=MagicMock(),
            auto_shelly_soft_fail_seconds=5.0,
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        self.assertTrue(controller.update_virtual_state(2, 1.5, True))

        service._publish_energy_time_measurements.assert_called_once()
        service._warning_throttled.assert_called_once()
        self.assertEqual(service.last_status, 2)

    def test_update_state_helpers_cover_freshness_and_startstop_edges(self):
        service = SimpleNamespace(
            _charger_backend=object(),
            _worker_poll_interval_seconds=0.4,
            auto_shelly_soft_fail_seconds=10.0,
            _last_charger_state_enabled=True,
            _last_charger_state_at=None,
            virtual_startstop=0,
            virtual_enable=0,
            virtual_mode=0,
            _mode_uses_auto_logic=lambda mode: int(mode) in (1, 2),
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        self.assertEqual(controller._charger_state_max_age_seconds(service), 1.0)
        self.assertTrue(controller._stale_charger_enabled_readback(service, 100.0))
        self.assertIsNone(controller._fresh_charger_enabled_readback(service, 100.0))

        service._last_charger_state_at = 100.0
        self.assertEqual(controller.startstop_display_for_state(service, False, 100.0), 1)

    def test_auto_phase_selection_tracks_candidate_before_staged_upshift(self):
        service = _auto_phase_service(
            _last_confirmed_pm_status={"output": True},
            _last_confirmed_pm_status_at=99.5,
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        override = controller.maybe_apply_auto_phase_selection(
            service,
            True,
            True,
            230.0,
            100.0,
            True,
        )

        self.assertIsNone(override)
        self.assertEqual(service._auto_phase_target_candidate, "P1_P2")
        self.assertEqual(service._auto_phase_target_since, 100.0)
        self.assertIsNone(service._phase_switch_pending_selection)
        self.assertEqual(service._last_auto_metrics["phase_reason"], "phase-upshift-pending")

    def test_auto_phase_selection_stages_upshift_after_delay_when_relay_is_on(self):
        service = _auto_phase_service(
            _last_confirmed_pm_status={"output": True},
            _last_confirmed_pm_status_at=99.5,
            _auto_phase_target_candidate="P1_P2",
            _auto_phase_target_since=80.0,
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        override = controller.maybe_apply_auto_phase_selection(
            service,
            True,
            True,
            230.0,
            100.0,
            True,
        )

        self.assertFalse(override)
        self.assertEqual(service.requested_phase_selection, "P1_P2")
        self.assertEqual(service._phase_switch_pending_selection, "P1_P2")
        self.assertEqual(service._phase_switch_state, "waiting-relay-off")
        self.assertTrue(service._phase_switch_resume_relay)
        service._save_runtime_state.assert_called_once()
        service._publish_local_pm_status.assert_called_once_with(False, 100.0)

    def test_auto_phase_selection_blocks_repeated_upshift_after_confirmed_mismatch(self):
        service = _auto_phase_service(
            _last_confirmed_pm_status={"output": True},
            _last_confirmed_pm_status_at=99.5,
            _phase_switch_last_mismatch_selection="P1_P2",
            _phase_switch_last_mismatch_at=95.0,
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        override = controller.maybe_apply_auto_phase_selection(
            service,
            True,
            True,
            230.0,
            100.0,
            True,
        )

        self.assertIsNone(override)
        self.assertIsNone(service._auto_phase_target_candidate)
        self.assertIsNone(service._phase_switch_pending_selection)
        self.assertEqual(service._last_auto_metrics["phase_reason"], "phase-upshift-blocked-mismatch")

    def test_auto_phase_selection_retries_upshift_after_mismatch_cooldown_expires(self):
        service = _auto_phase_service(
            _last_confirmed_pm_status={"output": True},
            _last_confirmed_pm_status_at=99.5,
            _phase_switch_last_mismatch_selection="P1_P2",
            _phase_switch_last_mismatch_at=10.0,
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        override = controller.maybe_apply_auto_phase_selection(
            service,
            True,
            True,
            230.0,
            100.0,
            True,
        )

        self.assertIsNone(override)
        self.assertEqual(service._auto_phase_target_candidate, "P1_P2")
        self.assertEqual(service._last_auto_metrics["phase_reason"], "phase-upshift-pending")

    def test_auto_phase_selection_blocks_upshift_while_phase_lockout_is_active(self):
        service = _auto_phase_service(
            _last_confirmed_pm_status={"output": True},
            _last_confirmed_pm_status_at=99.5,
            _phase_switch_lockout_selection="P1_P2",
            _phase_switch_lockout_reason="mismatch-threshold",
            _phase_switch_lockout_at=95.0,
            _phase_switch_lockout_until=160.0,
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        override = controller.maybe_apply_auto_phase_selection(
            service,
            True,
            True,
            230.0,
            100.0,
            True,
        )

        self.assertIsNone(override)
        self.assertIsNone(service._auto_phase_target_candidate)
        self.assertEqual(service._last_auto_metrics["phase_reason"], "phase-upshift-blocked-lockout")

    def test_auto_phase_selection_applies_lowest_phase_while_idle_after_delay(self):
        service = _auto_phase_service(
            requested_phase_selection="P1_P2",
            active_phase_selection="P1_P2",
            _last_auto_metrics={"surplus": 400.0},
            _last_confirmed_pm_status={"output": False},
            _last_confirmed_pm_status_at=99.5,
            _auto_phase_target_candidate="P1",
            _auto_phase_target_since=90.0,
            _apply_phase_selection=MagicMock(return_value="P1"),
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        override = controller.maybe_apply_auto_phase_selection(
            service,
            False,
            False,
            230.0,
            100.0,
            True,
        )

        self.assertIsNone(override)
        service._apply_phase_selection.assert_called_once_with("P1")
        self.assertEqual(service.requested_phase_selection, "P1")
        self.assertEqual(service.active_phase_selection, "P1")
        self.assertIsNone(service._phase_switch_pending_selection)
        service._save_runtime_state.assert_called_once()

    def test_auto_phase_selection_keeps_unrelated_lockout_after_successful_apply(self):
        service = _auto_phase_service(
            requested_phase_selection="P1_P2",
            active_phase_selection="P1_P2",
            _last_auto_metrics={"surplus": 400.0},
            _last_confirmed_pm_status={"output": False},
            _last_confirmed_pm_status_at=99.5,
            _auto_phase_target_candidate="P1",
            _auto_phase_target_since=90.0,
            _apply_phase_selection=MagicMock(return_value="P1"),
            _phase_switch_lockout_selection="P1_P2",
            _phase_switch_lockout_reason="mismatch-threshold",
            _phase_switch_lockout_at=80.0,
            _phase_switch_lockout_until=140.0,
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        override = controller.maybe_apply_auto_phase_selection(
            service,
            False,
            False,
            230.0,
            100.0,
            True,
        )

        self.assertIsNone(override)
        service._apply_phase_selection.assert_called_once_with("P1")
        self.assertEqual(service.requested_phase_selection, "P1")
        self.assertEqual(service.active_phase_selection, "P1")
        self.assertEqual(service._phase_switch_lockout_selection, "P1_P2")
        self.assertEqual(service._phase_switch_lockout_until, 140.0)

    def test_auto_phase_helper_edges_cover_fallbacks_thresholds_and_lockouts(self):
        service = _auto_phase_service(
            requested_phase_selection="P1_P2_P3",
            active_phase_selection="P1_P2",
            _last_auto_metrics="bad",
            auto_policy=None,
            min_current=None,
            _phase_switch_last_mismatch_selection=None,
            _phase_switch_last_mismatch_at=None,
            auto_phase_mismatch_retry_seconds=-1.0,
            auto_phase_mismatch_lockout_count=-2,
            auto_phase_mismatch_lockout_seconds=-3.0,
            _phase_switch_mismatch_counts={"P1_P2": 2},
            _phase_switch_lockout_selection="P1_P2",
            _phase_switch_lockout_reason="mismatch-threshold",
            _phase_switch_lockout_at=80.0,
            _phase_switch_lockout_until=90.0,
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        self.assertEqual(controller._current_phase_selection(service, ("P1", "P1_P2")), "P1_P2")
        self.assertIsNone(controller._auto_phase_policy(service))
        self.assertIsNone(controller._auto_phase_metric_surplus_watts(service))
        self.assertIsNone(controller._phase_selection_min_surplus_watts(service, "P1", 230.0))
        self.assertEqual(
            controller._auto_phase_policy_state(service, ("P1",)),
            (None, "phase-policy-disabled", None),
        )

        service.auto_policy = AutoPolicy()
        service.auto_policy.phase.enabled = True
        service.auto_policy.phase.mismatch_lockout_count = -2
        service.auto_policy.phase.mismatch_lockout_seconds = -3.0
        self.assertEqual(
            controller._auto_phase_policy_state(service, ("P1",)),
            (None, "single-phase-only", None),
        )
        self.assertEqual(
            controller._idle_auto_phase_target(service.auto_policy.phase, ("P1",), "P1", False, False),
            (None, "idle-hold-phase", None),
        )
        self.assertEqual(
            controller._surplus_auto_phase_target(service, service.auto_policy.phase, ("P1", "P1_P2"), "P1", 230.0, 100.0),
            (None, "phase-surplus-missing", None),
        )

        service._last_auto_metrics = {"surplus": 100.0}
        self.assertEqual(
            controller._surplus_auto_phase_target(service, service.auto_policy.phase, ("P1", "P1_P2"), "P1", 230.0, 100.0),
            (None, "phase-hold", None),
        )
        self.assertIsNone(
            controller._upshift_auto_phase_target(
                service,
                service.auto_policy.phase,
                ("P1", "P1_P2"),
                1,
                "P1_P2",
                1000.0,
                230.0,
                100.0,
            )
        )
        self.assertIsNone(
            controller._upshift_auto_phase_target(
                service,
                service.auto_policy.phase,
                ("P1", "P1_P2"),
                0,
                "P1",
                1000.0,
                230.0,
                100.0,
            )
        )
        service.min_current = 6.0
        self.assertIsNone(
            controller._upshift_auto_phase_target(
                service,
                service.auto_policy.phase,
                ("P1", "P1_P2"),
                0,
                "P1",
                100.0,
                230.0,
                100.0,
            )
        )

        self.assertFalse(controller._phase_switch_mismatch_retry_active(service, "P1_P2", "P1", 100.0))
        service._phase_switch_last_mismatch_selection = "P1"
        self.assertFalse(controller._phase_switch_mismatch_retry_active(service, "P1", "P1_P2", 100.0))
        service._phase_switch_last_mismatch_selection = "P1_P2"
        self.assertFalse(controller._phase_switch_mismatch_retry_active(service, "P1", "P1_P2", 100.0))
        service._phase_switch_last_mismatch_at = 90.0
        self.assertTrue(controller._phase_switch_mismatch_retry_active(service, "P1", "P1_P2", 100.0))
        service.auto_policy.phase.mismatch_retry_seconds = 20.0
        self.assertTrue(controller._phase_switch_mismatch_retry_active(service, "P1", "P1_P2", 100.0))
        self.assertEqual(controller._phase_switch_lockout_threshold(service), 0)
        self.assertEqual(controller._phase_switch_lockout_seconds(service), 0.0)

        controller._clear_phase_switch_mismatch_tracking(service)
        self.assertEqual(service._phase_switch_mismatch_counts, {})
        self.assertIsNone(service._phase_switch_last_mismatch_selection)
        self.assertIsNone(service._phase_switch_last_mismatch_at)
        service._phase_switch_mismatch_counts = {"P1_P2": 1}
        service._phase_switch_last_mismatch_selection = "P1_P2"
        service._phase_switch_last_mismatch_at = 95.0
        controller._clear_phase_switch_mismatch_tracking(service, "P1_P2")
        self.assertIsNone(service._phase_switch_last_mismatch_selection)
        self.assertIsNone(service._phase_switch_last_mismatch_at)

        controller._engage_phase_switch_lockout(service, "P1_P2", 100.0)
        self.assertIsNone(service._phase_switch_lockout_selection)
        service.auto_policy.phase.mismatch_lockout_seconds = 30.0
        controller._engage_phase_switch_lockout(service, "P1_P2", 100.0)
        self.assertTrue(controller._phase_switch_lockout_active(service, 110.0, "P1_P2"))
        self.assertFalse(controller._phase_switch_lockout_active(service, 131.0, "P1_P2"))

        self.assertEqual(controller._phase_switch_fallback_selection(service, "P1", "P1_P2"), "P1")
        with patch("venus_evcharger.update.relay.normalize_phase_selection", side_effect=["", "P1_P2"]):
            self.assertEqual(controller._phase_switch_fallback_selection(service, None, "P1_P2"), "P1_P2")

        self.assertIsNone(
            controller._downshift_auto_phase_target(
                service,
                service.auto_policy.phase,
                ("P1", "P1_P2"),
                "P1",
                0,
                10.0,
                230.0,
            )
        )
        service.min_current = None
        self.assertIsNone(
            controller._downshift_auto_phase_target(
                service,
                service.auto_policy.phase,
                ("P1", "P1_P2"),
                "P1_P2",
                1,
                10.0,
                230.0,
            )
        )
        service.min_current = 6.0
        self.assertIsNone(
            controller._downshift_auto_phase_target(
                service,
                service.auto_policy.phase,
                ("P1", "P1_P2"),
                "P1_P2",
                1,
                5000.0,
                230.0,
            )
        )
        self.assertEqual(
            controller._downshift_auto_phase_target(
                service,
                service.auto_policy.phase,
                ("P1", "P1_P2"),
                "P1_P2",
                1,
                100.0,
                230.0,
            ),
            ("P1", "phase-downshift", 2610.0),
        )

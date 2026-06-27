# SPDX-License-Identifier: GPL-3.0-or-later
from tests.venus_evcharger_update_cycle_controller_support import *


class TestUpdateCycleControllerSecondary(UpdateCycleControllerTestBase):
    def test_auto_phase_helper_edges_cover_candidate_staging_and_freshness(self):
        service = _auto_phase_service(
            auto_policy=None,
            _phase_selection_requires_pause=lambda: False,
            _peek_pending_relay_command=MagicMock(return_value=(None, None)),
            _last_confirmed_pm_status=None,
            _last_confirmed_pm_status_at=None,
            _charger_backend=None,
            _last_charger_state_at=None,
            _last_switch_feedback_at=None,
            _last_switch_feedback_closed=None,
            _last_switch_interlock_ok=None,
            _last_charger_state_enabled=None,
            _apply_phase_selection=MagicMock(side_effect=RuntimeError("boom")),
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        self.assertEqual(controller._auto_phase_switch_delay_seconds(service, "P1", "P1_P2"), 0.0)
        self.assertFalse(controller._auto_phase_candidate_ready(service, "P1", "P1_P2", 100.0))
        service._auto_phase_target_candidate = "P1_P2"
        service._auto_phase_target_since = None
        self.assertFalse(controller._auto_phase_candidate_ready(service, "P1", "P1_P2", 100.0))
        self.assertFalse(controller._phase_change_requires_staging(service, True, 100.0))
        service._phase_selection_requires_pause = lambda: True
        service._peek_pending_relay_command = MagicMock(return_value=(True, 99.0))
        self.assertTrue(controller._phase_change_requires_staging(service, False, 100.0))
        service._peek_pending_relay_command = MagicMock(return_value=(None, None))
        self.assertTrue(controller._phase_change_requires_staging(service, True, 100.0))

        self.assertEqual(controller._charger_state_max_age_seconds(service), 2.0)
        service._worker_poll_interval_seconds = 0.5
        service.auto_shelly_soft_fail_seconds = 7.0
        self.assertEqual(controller._charger_state_max_age_seconds(service), 1.0)
        self.assertIsInstance(controller._charger_readback_now(service), float)
        self.assertIsNone(controller._fresh_charger_state_timestamp(service, 100.0))
        service._charger_backend = object()
        self.assertIsNone(controller._fresh_charger_state_timestamp(service, 100.0))
        service._last_charger_state_at = 80.0
        self.assertIsNone(controller._fresh_charger_state_timestamp(service, 100.0))
        service._last_switch_feedback_at = 80.0
        self.assertIsNone(controller._fresh_switch_feedback_timestamp(service, 100.0))
        self.assertIsNone(controller._fresh_switch_feedback_closed(service, 100.0))
        self.assertIsNone(controller._fresh_switch_interlock_ok(service, 100.0))
        self.assertIsNone(controller._fresh_charger_enabled_readback(service, 100.0))

        service._phase_selection_requires_pause = lambda: False
        result = controller._apply_auto_phase_target(service, "P1_P2", True, True, 100.0)
        self.assertIsNone(result)
        service._mark_failure.assert_called_once_with("shelly")
        service._warning_throttled.assert_called_once()

    def test_runtime_count_helpers_normalize_invalid_state(self):
        controller = UpdateCycleController(SimpleNamespace(), _phase_values, lambda reason: {"init": 0}.get(reason, 99))
        service = SimpleNamespace(
            _contactor_fault_counts={
                "contactor-suspected-open": 2,
                "contactor-suspected-welded": -1,
                "bogus": 7,
                "bad-bool": True,
            },
            _phase_switch_mismatch_counts={
                "P1": -1,
                "P1_P2": 3,
                "P1_P2_P3": True,
                "bogus": 5,
            },
        )

        self.assertEqual(
            controller._contactor_fault_counts(service),
            {"contactor-suspected-open": 2, "contactor-suspected-welded": 0},
        )
        self.assertEqual(
            service._contactor_fault_counts,
            {"contactor-suspected-open": 2, "contactor-suspected-welded": 0},
        )
        self.assertEqual(controller._phase_switch_mismatch_counts(service), {"P1": 0, "P1_P2": 3})
        self.assertEqual(service._phase_switch_mismatch_counts, {"P1": 0, "P1_P2": 3})

        service._contactor_fault_counts = []
        service._phase_switch_mismatch_counts = []
        self.assertEqual(controller._contactor_fault_counts(service), {})
        self.assertEqual(service._contactor_fault_counts, {})
        self.assertEqual(controller._phase_switch_mismatch_counts(service), {})
        self.assertEqual(service._phase_switch_mismatch_counts, {})

    def test_relay_helper_edges_cover_health_status_and_learned_current_helpers(self):
        service = _auto_phase_service(
            _auto_phase_target_candidate="P1_P2",
            _auto_phase_target_since=80.0,
            _phase_switch_lockout_selection="P1_P2",
            _phase_switch_lockout_reason="mismatch-threshold",
            _phase_switch_lockout_at=50.0,
            _phase_switch_lockout_until=90.0,
            _apply_phase_selection=MagicMock(return_value="P1_P2"),
            _phase_selection_requires_pause=lambda: False,
            _worker_poll_interval_seconds=0.5,
            auto_shelly_soft_fail_seconds=7.0,
            _time_now=MagicMock(return_value=100.0),
            _charger_backend=SimpleNamespace(set_enabled=MagicMock(), set_current=MagicMock()),
            _last_charger_state_at=100.0,
            _last_charger_state_enabled=None,
            _last_switch_feedback_at=100.0,
            _last_switch_feedback_closed=None,
            _last_switch_interlock_ok=None,
            _last_charger_state_power_w=1500.0,
            _last_charger_state_actual_current_amps=7.0,
            _last_charger_state_status="fault waiting",
            _last_charger_state_fault="fault",
            _last_charger_transport_reason=None,
            _last_charger_transport_source=None,
            _last_charger_transport_detail=None,
            _last_charger_transport_at=None,
            _charger_retry_reason=None,
            _charger_retry_source=None,
            _charger_retry_until=None,
            _source_retry_after={},
            _contactor_lockout_reason="contactor-suspected-open",
            _contactor_lockout_source="feedback",
            _contactor_lockout_at=90.0,
            _contactor_fault_counts={"contactor-suspected-open": 1},
            _contactor_fault_active_reason="contactor-suspected-open",
            _contactor_fault_active_since=90.0,
            _contactor_suspected_open_since=80.0,
            _contactor_suspected_welded_since=81.0,
            learned_charge_power_state="stable",
            learned_charge_power_watts=-1.0,
            learned_charge_power_voltage=230.0,
            learned_charge_power_phase="3P",
            learned_charge_power_updated_at=90.0,
            auto_learn_charge_power_max_age_seconds=5.0,
            min_current=6.0,
            max_current=16.0,
            voltage_mode="line",
            idle_status=1,
            virtual_set_current=10.0,
            virtual_mode=1,
            auto_month_windows={},
            auto_scheduled_enabled_days="Mon,Tue,Wed,Thu,Fri",
            auto_scheduled_night_start_delay_seconds=3600.0,
            auto_scheduled_latest_end_time="06:30",
            auto_scheduled_night_current_amps=0.0,
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        self.assertTrue(controller._apply_auto_phase_target(service, "P1_P2", True, False, 100.0) is None)
        self.assertIsNone(service._phase_switch_lockout_selection)
        self.assertIsNone(service._auto_phase_target_candidate)
        service._auto_phase_target_candidate = "P1_P2"
        service._auto_phase_target_since = 95.0
        self.assertIsNone(controller.maybe_apply_auto_phase_selection(service, True, False, 230.0, 100.0, False))
        self.assertIsNone(service._auto_phase_target_candidate)
        self.assertFalse(controller._auto_phase_selection_inactive(service, True))
        self.assertEqual(controller._charger_state_max_age_seconds(service), 1.0)
        self.assertEqual(controller._charger_readback_now(service), 100.0)
        self.assertIsNone(controller._fresh_switch_feedback_closed(service, 100.0))
        self.assertIsNone(controller._fresh_switch_interlock_ok(service, 100.0))
        self.assertIsNone(controller._fresh_charger_enabled_readback(service, 100.0))
        self.assertFalse(controller._pm_load_active(service, 5000.0, 20.0, False))
        self.assertTrue(controller._charger_load_active(service, 100.0))
        service._last_charger_state_power_w = 0.0
        service._last_charger_state_actual_current_amps = 0.0
        service._last_charger_state_status = "charging"
        self.assertTrue(controller._charger_requests_load(service, 100.0))

        class _NoSetattr:
            def __setattr__(self, name, value):
                raise AttributeError(name)

        no_setattr = _NoSetattr()
        controller._set_runtime_attr(no_setattr, "runtime_value", 5)
        self.assertEqual(no_setattr.__dict__["runtime_value"], 5)

        controller._remember_charger_retry(service, "offline", "read", 100.0)
        self.assertEqual(service._source_retry_after["charger"], 120.0)
        controller._clear_charger_retry(service)
        self.assertEqual(service._source_retry_after["charger"], 0.0)
        self.assertEqual(controller._contactor_fault_count(service, "bogus"), 0)
        controller._clear_contactor_lockout(service)
        self.assertEqual(service._contactor_lockout_reason, "")
        self.assertEqual(service._contactor_lockout_source, "")
        self.assertIsNone(service._contactor_lockout_at)
        controller._clear_contactor_fault_tracking(service)
        self.assertEqual(service._contactor_fault_counts, {})
        self.assertIsNone(service._contactor_suspected_open_since)
        self.assertIsNone(service._contactor_suspected_welded_since)
        controller._engage_contactor_lockout(service, "bogus", 100.0, "feedback")
        self.assertEqual(service._contactor_lockout_reason, "")
        self.assertIsNone(controller._remember_contactor_fault(service, "bogus", 100.0))
        self.assertEqual(controller.charger_health_override(service, 100.0), "charger-fault")

        service._contactor_lockout_reason = "contactor-suspected-open"
        service._last_switch_feedback_closed = None
        service._last_switch_interlock_ok = True
        self.assertEqual(
            controller.switch_feedback_health_override(service, False, False, 100.0, power=0.0, current=0.0, pm_confirmed=False),
            "contactor-lockout-open",
        )
        self.assertIsNone(controller._charger_status_override_from_tokens(service, {"mystery"}, True))
        self.assertIsNone(controller._clamped_charger_current_target(service, None))
        self.assertEqual(controller._apply_max_current_limit(12.0, None), 12.0)
        self.assertIsNone(controller._validated_stable_learned_current_inputs((-1.0, 230.0, "L1", 1.0, None)))
        self.assertIsNone(controller._validated_stable_learned_current_inputs((1000.0, 230.0, None, 1.0, None)))
        self.assertIsNone(controller._positive_learned_scalar(0.0))
        self.assertIsNone(controller._learned_phase_and_timestamp(None, 1.0))
        self.assertAlmostEqual(controller._learned_phase_voltage(service, "3P", 400.0), 400.0 / math.sqrt(3.0))
        self.assertIsNone(controller._rounded_learned_current_target(1000.0, 0.0, 3.0))
        self.assertEqual(controller._scheduled_night_current_amps(service), 16.0)
        self.assertIsNone(controller._derived_learned_current_target(service, 100.0))
        self.assertIsNone(controller._charger_current_target_amps(service, True, 100.0, False))
        service._charger_backend = None
        self.assertIsNone(controller._charger_current_target_amps(service, True, 100.0, True))

    def test_relay_helper_edges_cover_non_blocking_health_and_native_power_status(self):
        service = _auto_phase_service(
            _charger_backend=object(),
            _last_charger_state_at=100.0,
            _last_charger_state_power_w=1800.0,
            auto_shelly_soft_fail_seconds=7.0,
            _warning_throttled=MagicMock(),
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        with patch.object(controller, "charger_health_override", return_value="charger-fault"):
            self.assertEqual(controller._blocking_charger_health(False, False, 100.0), "charger-fault")
        service._warning_throttled.assert_not_called()

        with patch.object(controller, "derive_status_code", return_value=7) as derive_status_code:
            effective_power, status = controller._status_after_relay_decision(
                True,
                500.0,
                True,
                None,
                100.0,
            )

        self.assertEqual(effective_power, 1800.0)
        self.assertEqual(status, 7)
        derive_status_code.assert_called_once()

    def test_relay_mixin_startup_publish_falls_back_for_non_dict_helper_result(self):
        svc = SimpleNamespace(
            _publish_local_pm_status=MagicMock(return_value="ignored"),
            auto_shelly_soft_fail_seconds=7.0,
            _warning_throttled=MagicMock(),
        )
        controller = UpdateCycleController(svc, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        published = controller._publish_startup_local_pm_status({"output": False}, True, 100.0)

        self.assertEqual(
            published,
            {"output": True, "apower": 0.0, "current": 0.0},
        )

    def test_relay_mixin_direct_helper_edges_cover_shadowed_remaining_branches(self):
        svc = SimpleNamespace(
            _worker_poll_interval_seconds=None,
            auto_shelly_soft_fail_seconds=None,
            _charger_backend=object(),
            _last_charger_state_at=100.0,
            _last_charger_state_enabled=None,
            _last_charger_state_power_w=0.0,
            _last_charger_state_actual_current_amps=0.0,
            _last_charger_state_status="charging",
            _last_charger_state_fault=None,
            _source_retry_after={},
            learned_charge_power_state="stable",
            learned_charge_power_watts=3000.0,
            learned_charge_power_voltage=230.0,
            learned_charge_power_phase="L1",
            learned_charge_power_updated_at=50.0,
            auto_learn_charge_power_max_age_seconds=10.0,
            min_current=6.0,
            max_current=16.0,
            voltage_mode="line",
            auto_scheduled_night_current_amps=0.0,
            virtual_mode=1,
            auto_month_windows={},
            auto_scheduled_enabled_days="Mon,Tue,Wed,Thu,Fri",
            auto_scheduled_night_start_delay_seconds=3600.0,
            auto_scheduled_latest_end_time="06:30",
            virtual_set_current=None,
        )

        self.assertEqual(_UpdateCycleRelayMixin._charger_state_max_age_seconds(svc), 2.0)
        svc._worker_poll_interval_seconds = 0.5
        svc.auto_shelly_soft_fail_seconds = 7.0
        self.assertEqual(_UpdateCycleRelayMixin._charger_state_max_age_seconds(svc), 1.0)
        self.assertFalse(_UpdateCycleRelayMixin._fresh_charger_enabled_readback(svc, 100.0))
        self.assertTrue(_UpdateCycleRelayMixin._charger_requests_load(svc, 100.0))
        svc.auto_policy = None
        self.assertEqual(_UpdateCycleRelayMixin._phase_switch_mismatch_retry_seconds(svc), 300.0)
        self.assertEqual(_UpdateCycleRelayMixin._phase_switch_lockout_seconds(svc), 1800.0)
        svc._last_auto_metrics = {"surplus": 2600.0}
        self.assertEqual(
            _UpdateCycleRelayMixin._surplus_auto_phase_target(
                svc,
                SimpleNamespace(downshift_margin_watts=150.0, upshift_headroom_watts=250.0),
                ("P1", "P1_P2"),
                "P1_P2",
                230.0,
                100.0,
            ),
            ("P1", "phase-downshift", 2610.0),
        )

        class _NoRuntimeAttr:
            __slots__ = ()

            def __setattr__(self, name, value):
                raise AttributeError(name)

        with self.assertRaises(AttributeError):
            _UpdateCycleRelayMixin._set_runtime_attr(_NoRuntimeAttr(), "x", 1)

        svc._last_charger_state_status = "fault waiting"
        self.assertEqual(_UpdateCycleRelayMixin.charger_health_override(svc, 100.0), "charger-fault")
        self.assertIsNone(_UpdateCycleRelayMixin._derived_learned_current_target(svc, 100.0))
        self.assertIsNone(_UpdateCycleRelayMixin.apply_charger_current_target(svc, True, 100.0, True))
        self.assertEqual(_UpdateCycleRelayMixin._phase_switch_fallback_selection(SimpleNamespace(active_phase_selection="P1_P2"), None, "P1"), "P1_P2")
        self.assertIsNone(_UpdateCycleRelayMixin._phase_tuple_item(True))
        self.assertIsNone(_UpdateCycleRelayMixin._resolved_phase_tuple((1.0, None, 3.0)))
        self.assertAlmostEqual(_UpdateCycleRelayMixin._phase_voltage(400.0, "P1_P2_P3", "line"), 400.0 / math.sqrt(3.0))

    def test_relay_mixin_direct_helper_edges_cover_remaining_small_branches(self):
        svc = SimpleNamespace(
            auto_policy=SimpleNamespace(phase=SimpleNamespace(mismatch_retry_seconds=0.0)),
            _phase_switch_last_mismatch_selection="P1_P2",
            _phase_switch_last_mismatch_at=95.0,
            _worker_poll_interval_seconds=1.0,
            auto_shelly_soft_fail_seconds=7.0,
            _last_charger_state_at=None,
            _last_charger_state_enabled=True,
            _last_charger_state_power_w=2000.0,
            _last_charger_state_actual_current_amps=0.0,
            _last_charger_state_status="idle",
            _last_charger_state_phase_selection="P1_P2",
            _last_auto_metrics={"surplus": 2600.0},
            _phase_switch_requested_at=None,
            phase_switch_pause_seconds=1.0,
            phase_switch_stabilization_seconds=2.0,
            _relay_sync_failure_reported=True,
            _relay_sync_requested_at=90.0,
            _mark_failure=MagicMock(),
            _warning_throttled=MagicMock(),
            _charger_backend=SimpleNamespace(set_current=MagicMock()),
            learned_charge_power_state="unknown",
            _source_retry_after={},
            min_current=6.0,
            max_current=16.0,
        )

        self.assertFalse(_UpdateCycleRelayMixin._phase_switch_mismatch_retry_active(svc, "P1", "P1_P2", 100.0))
        self.assertIsNone(_UpdateCycleRelayMixin._fresh_charger_enabled_readback(svc, 100.0))
        svc._last_charger_state_at = 100.0
        self.assertTrue(_UpdateCycleRelayMixin._fresh_charger_enabled_readback(svc, 100.0))
        self.assertTrue(_UpdateCycleRelayMixin._charger_requests_load(svc, 100.0))
        self.assertIsNone(_UpdateCycleRelayMixin._observed_phase_selection_from_pm_status({}))
        self.assertEqual(_UpdateCycleRelayMixin._observed_phase_selection(svc, {}, 100.0), "P1_P2")
        svc._last_charger_state_phase_selection = None
        self.assertIsNone(_UpdateCycleRelayMixin._observed_phase_selection(svc, {}, 100.0))
        self.assertIsNone(_UpdateCycleRelayMixin._phase_switch_verification_deadline(svc))
        svc._phase_switch_requested_at = 95.0
        self.assertEqual(_UpdateCycleRelayMixin._phase_switch_verification_deadline(svc), 105.0)

        controller = UpdateCycleController(svc, _phase_values, lambda reason: {"init": 0}.get(reason, 99))
        controller._record_relay_sync_timeout(svc, relay_on=False, pm_confirmed=False, expected_relay=True, deadline_at=100.0)
        svc._mark_failure.assert_not_called()
        svc._warning_throttled.assert_not_called()

        with patch.object(_UpdateCycleRelayMixin, "_charger_current_target_amps", return_value=None):
            self.assertIsNone(_UpdateCycleRelayMixin.apply_charger_current_target(svc, True, 100.0, True))

    def test_phase_switch_resume_helper_covers_no_resume_auto_failure_and_noop_paths(self):
        service = _auto_phase_service(
            _phase_switch_resume_relay=False,
            _save_runtime_state=MagicMock(),
            _mark_failure=MagicMock(),
            _warning_throttled=MagicMock(),
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        self.assertEqual(
            controller._resume_after_phase_switch_pause(service, False, 12.0, 3.0, True, 100.0, False),
            (False, 12.0, 3.0, True),
        )
        service._save_runtime_state.assert_called()

        service._phase_switch_resume_relay = True
        service._save_runtime_state.reset_mock()
        self.assertEqual(
            controller._resume_after_phase_switch_pause(service, False, 12.0, 3.0, True, 101.0, True),
            (False, 12.0, 3.0, True),
        )
        self.assertTrue(service._ignore_min_offtime_once)

        service._phase_switch_resume_relay = True
        service._save_runtime_state.reset_mock()
        with patch.object(controller, "_apply_enabled_target", side_effect=RuntimeError("boom")):
            self.assertEqual(
                controller._resume_after_phase_switch_pause(service, False, 12.0, 3.0, True, 102.0, False),
                (False, 12.0, 3.0, True),
            )
        service._mark_failure.assert_called()
        service._warning_throttled.assert_called()

        service._phase_switch_resume_relay = True
        service._save_runtime_state.reset_mock()
        with patch.object(controller, "_apply_enabled_target", return_value=False):
            self.assertEqual(
                controller._resume_after_phase_switch_pause(service, False, 12.0, 3.0, True, 103.0, False),
                (False, 12.0, 3.0, True),
            )

    def test_phase_switch_fallback_selection_uses_requested_selection_when_active_normalizes_empty(self):
        svc = SimpleNamespace(active_phase_selection="", requested_phase_selection="P1_P2")
        with patch(
            "venus_evcharger.update.relay_phase_switch_policy.normalize_phase_selection",
            side_effect=["", "P1_P2"],
        ):
            self.assertEqual(
                _UpdateCycleRelayMixin._phase_switch_fallback_selection(svc, None, "P1"),
                "P1_P2",
            )

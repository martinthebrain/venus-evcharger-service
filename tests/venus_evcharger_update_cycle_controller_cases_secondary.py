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
        service = _phase_switch_mismatch_service(
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

    def test_phase_switch_mismatch_contract_helpers_cover_thresholds_retry_and_lockout(self):
        service = _phase_switch_mismatch_service(
            auto_policy=SimpleNamespace(
                phase=SimpleNamespace(
                    mismatch_retry_seconds=45.0,
                    mismatch_lockout_count=2,
                    mismatch_lockout_seconds=120.0,
                )
            )
        )

        self.assertEqual(_UpdateCycleRelay._phase_switch_mismatch_retry_seconds(service), 45.0)
        self.assertEqual(_UpdateCycleRelay._phase_switch_lockout_threshold(service), 2)
        self.assertEqual(_UpdateCycleRelay._phase_switch_lockout_seconds(service), 120.0)

        count = _UpdateCycleRelay._remember_phase_switch_mismatch(service, "P1_P2", 100.0)
        self.assertEqual(count, 1)
        self.assertTrue(service._phase_switch_mismatch_active)
        self.assertEqual(service._phase_switch_last_mismatch_selection, "P1_P2")
        self.assertEqual(service._phase_switch_last_mismatch_at, 100.0)
        self.assertTrue(_UpdateCycleRelay._phase_switch_mismatch_retry_active(service, "P1", "P1_P2", 130.0))
        self.assertTrue(_UpdateCycleRelay._phase_switch_mismatch_retry_active(service, "P1", "P1_P2", 100.5))
        self.assertFalse(_UpdateCycleRelay._phase_switch_mismatch_retry_active(service, "P1", "P1_P2", 145.0))
        self.assertFalse(_UpdateCycleRelay._phase_switch_mismatch_retry_active(service, "P1_P2", "P1", 130.0))
        self.assertFalse(_UpdateCycleRelay._phase_switch_mismatch_retry_active(service, "P1_P2", "P1_P2", 130.0))

        service._phase_switch_last_mismatch_selection = None
        self.assertFalse(_UpdateCycleRelay._phase_switch_mismatch_retry_active(service, "P1", "P1_P2", 130.0))
        service._phase_switch_last_mismatch_selection = "P1_P2"
        service.auto_policy.phase.mismatch_retry_seconds = 1.0
        self.assertTrue(_UpdateCycleRelay._phase_switch_mismatch_retry_active(service, "P1", "P1_P2", 100.5))
        self.assertFalse(_UpdateCycleRelay._phase_switch_mismatch_retry_active(service, "P1", "P1_P2", 101.0))
        service.auto_policy.phase.mismatch_retry_seconds = 45.0

        _UpdateCycleRelay._engage_phase_switch_lockout(service, "P1_P2", 200.0)
        self.assertEqual(service._phase_switch_lockout_selection, "P1_P2")
        self.assertEqual(service._phase_switch_lockout_reason, "mismatch-threshold")
        self.assertEqual(service._phase_switch_lockout_at, 200.0)
        self.assertEqual(service._phase_switch_lockout_until, 320.0)
        self.assertTrue(_UpdateCycleRelay._phase_switch_lockout_active(service, 250.0, "P1_P2"))
        self.assertFalse(_UpdateCycleRelay._phase_switch_lockout_active(service, 250.0, "P1"))
        self.assertFalse(_UpdateCycleRelay._phase_switch_lockout_active(service, 320.0, "P1_P2"))
        self.assertIsNone(service._phase_switch_lockout_selection)

    def test_phase_switch_mismatch_contract_helpers_cover_fallbacks_and_normalization(self):
        service = _phase_switch_mismatch_service(
            auto_policy=None,
            auto_phase_mismatch_retry_seconds=75.0,
            auto_phase_mismatch_lockout_count=4,
            auto_phase_mismatch_lockout_seconds=600.0,
            _phase_switch_last_mismatch_selection="P1",
            _phase_switch_last_mismatch_at=float("nan"),
        )

        self.assertEqual(_UpdateCycleRelay._phase_switch_mismatch_retry_seconds(service), 75.0)
        self.assertEqual(_UpdateCycleRelay._phase_switch_lockout_threshold(service), 4)
        self.assertEqual(_UpdateCycleRelay._phase_switch_lockout_seconds(service), 600.0)
        service.auto_phase_mismatch_retry_seconds = -1.0
        service.auto_phase_mismatch_lockout_count = -3
        service.auto_phase_mismatch_lockout_seconds = 0.0
        self.assertEqual(_UpdateCycleRelay._phase_switch_mismatch_retry_seconds(service), 0.0)
        self.assertEqual(_UpdateCycleRelay._phase_switch_lockout_threshold(service), 0)
        self.assertEqual(_UpdateCycleRelay._phase_switch_lockout_seconds(service), 0.0)
        self.assertIsNone(_UpdateCycleRelay._phase_switch_mismatch_timestamp(service, "P1"))
        self.assertIsNone(_UpdateCycleRelay._phase_switch_mismatch_timestamp(service, "P1_P2"))
        service._phase_switch_last_mismatch_selection = "P1_P2_P3"
        service._phase_switch_last_mismatch_at = 10.0
        self.assertEqual(_UpdateCycleRelay._phase_switch_mismatch_timestamp(service, "P1_P2_P3"), 10.0)
        self.assertIsNone(_UpdateCycleRelay._phase_switch_mismatch_timestamp(service, "P1_P2"))

        self.assertEqual(
            _UpdateCycleRelay._normalized_phase_switch_mismatch_counts(
                {
                    "bad-first": 11,
                    "P1": -4,
                    "bad-count": "2",
                    "P1_P2": 2,
                    "P1_P2_P3": 4,
                    "bad": 7,
                    1: 8,
                    "P1-bool": False,
                }
            ),
            {"P1": 0, "P1_P2": 2, "P1_P2_P3": 4},
        )
        self.assertEqual(
            _UpdateCycleRelay._normalized_phase_switch_mismatch_counts({"P1": "bad", "P1_P2": 2}),
            {"P1_P2": 2},
        )
        service._phase_switch_mismatch_counts = {"P1": 4, "P1_P2": 6}
        self.assertEqual(_UpdateCycleRelay._phase_switch_mismatch_count(service, "P1"), 4)
        self.assertEqual(_UpdateCycleRelay._phase_switch_mismatch_count(service, "P1_P2"), 6)
        self.assertEqual(_UpdateCycleRelay._phase_switch_mismatch_count(service, "P1_P2_P3"), 0)
        self.assertEqual(_UpdateCycleRelay._remember_phase_switch_mismatch(service, "P1", 50.0), 5)
        self.assertEqual(service._phase_switch_mismatch_counts["P1"], 5)

        _UpdateCycleRelay._engage_phase_switch_lockout(service, "P1", 500.0)
        self.assertIsNone(service._phase_switch_lockout_selection)
        self.assertEqual(service._phase_switch_lockout_reason, "")
        self.assertIsNone(service._phase_switch_lockout_at)
        self.assertIsNone(service._phase_switch_lockout_until)

        service._phase_switch_mismatch_counts = {"P1": 1, "P1_P2": 2}
        service._phase_switch_last_mismatch_selection = "P1_P2"
        service._phase_switch_last_mismatch_at = 600.0
        _UpdateCycleRelay._clear_phase_switch_mismatch_tracking(service, "P1")
        self.assertEqual(service._phase_switch_mismatch_counts, {"P1_P2": 2})
        self.assertEqual(service._phase_switch_last_mismatch_selection, "P1_P2")
        _UpdateCycleRelay._clear_phase_switch_mismatch_tracking(service, "P1_P2")
        self.assertEqual(service._phase_switch_mismatch_counts, {})
        self.assertIs(service._phase_switch_mismatch_active, False)
        self.assertIsNone(service._phase_switch_last_mismatch_selection)
        self.assertIsNone(service._phase_switch_last_mismatch_at)
        service._phase_switch_mismatch_active = True
        service._phase_switch_mismatch_counts = {"P1": 1}
        service._phase_switch_last_mismatch_selection = "P1"
        service._phase_switch_last_mismatch_at = 700.0
        _UpdateCycleRelay._clear_phase_switch_mismatch_tracking(service)
        self.assertIs(service._phase_switch_mismatch_active, False)
        self.assertEqual(service._phase_switch_mismatch_counts, {})
        self.assertIsNone(service._phase_switch_last_mismatch_selection)
        self.assertIsNone(service._phase_switch_last_mismatch_at)
        partial_service = SimpleNamespace(_phase_switch_mismatch_active=True, _phase_switch_mismatch_counts={"P1": 1})
        _UpdateCycleRelay._clear_phase_switch_mismatch_tracking(partial_service, "P1")
        self.assertIs(partial_service._phase_switch_mismatch_active, False)
        self.assertEqual(partial_service._phase_switch_mismatch_counts, {})

    def test_phase_switch_mismatch_contract_helpers_cover_default_and_clamped_policy_values(self):
        policy_service = _phase_switch_mismatch_service(
            auto_policy=SimpleNamespace(phase=SimpleNamespace())
        )
        self.assertEqual(_UpdateCycleRelay._phase_switch_mismatch_retry_seconds(policy_service), 300.0)
        self.assertEqual(_UpdateCycleRelay._phase_switch_lockout_threshold(policy_service), 3)
        self.assertEqual(_UpdateCycleRelay._phase_switch_lockout_seconds(policy_service), 1800.0)

        policy_service.auto_policy.phase.mismatch_retry_seconds = -5.0
        policy_service.auto_policy.phase.mismatch_lockout_count = -2
        policy_service.auto_policy.phase.mismatch_lockout_seconds = -10.0
        self.assertEqual(_UpdateCycleRelay._phase_switch_mismatch_retry_seconds(policy_service), 0.0)
        self.assertEqual(_UpdateCycleRelay._phase_switch_lockout_threshold(policy_service), 0)
        self.assertEqual(_UpdateCycleRelay._phase_switch_lockout_seconds(policy_service), 0.0)
        self.assertFalse(
            _UpdateCycleRelay._phase_switch_mismatch_retry_active(policy_service, "P1", "P1_P2", 10.0)
        )

        legacy_service = _phase_switch_mismatch_service(auto_policy=None)
        self.assertEqual(_UpdateCycleRelay._phase_switch_mismatch_retry_seconds(legacy_service), 300.0)
        self.assertEqual(_UpdateCycleRelay._phase_switch_lockout_threshold(legacy_service), 3)
        self.assertEqual(_UpdateCycleRelay._phase_switch_lockout_seconds(legacy_service), 1800.0)
        policy_service.auto_policy.phase.mismatch_lockout_seconds = 1.0
        _UpdateCycleRelay._engage_phase_switch_lockout(policy_service, "P1_P2", 20.0)
        self.assertEqual(policy_service._phase_switch_lockout_until, 21.0)
        self.assertTrue(_UpdateCycleRelay._phase_switch_lockout_active(policy_service, 20.5, "P1_P2"))
        self.assertFalse(_UpdateCycleRelay._phase_switch_lockout_active(policy_service, 21.0, "P1_P2"))

    def test_phase_switch_mismatch_timestamp_normalizes_empty_selection_to_default_phase(self):
        service = _phase_switch_mismatch_service(
            _phase_switch_last_mismatch_selection="",
            _phase_switch_last_mismatch_at=42.0,
        )

        self.assertEqual(_UpdateCycleRelay._phase_switch_mismatch_timestamp(service, "P1"), 42.0)
        self.assertIsNone(_UpdateCycleRelay._phase_switch_mismatch_timestamp(service, "P1_P2"))

    def test_phase_switch_mismatch_contract_helpers_cover_empty_lockout_and_selection_fallbacks(self):
        service = _phase_switch_mismatch_service(active_phase_selection="P1", requested_phase_selection="P1_P2")

        self.assertFalse(_UpdateCycleRelay._phase_switch_lockout_active(service, 100.0))
        service._phase_switch_lockout_selection = "P1_P2"
        service._phase_switch_lockout_until = None
        self.assertFalse(_UpdateCycleRelay._phase_switch_lockout_active(service, 100.0, "P1_P2"))
        service._phase_switch_lockout_until = float("nan")
        self.assertFalse(_UpdateCycleRelay._phase_switch_lockout_active(service, 100.0, "P1_P2"))
        bare_service = SimpleNamespace()
        self.assertFalse(_UpdateCycleRelay._phase_switch_lockout_active(bare_service, 100.0))
        service._phase_switch_lockout_selection = ""
        service._phase_switch_lockout_until = 200.0
        self.assertFalse(_UpdateCycleRelay._phase_switch_lockout_active(service, 100.0, "P1"))
        self.assertIsNone(service._phase_switch_lockout_selection)
        service._phase_switch_lockout_selection = "bad"
        service._phase_switch_lockout_until = 200.0
        self.assertFalse(_UpdateCycleRelay._phase_switch_lockout_active(service, 100.0, "P1_P2"))
        self.assertIsNone(service._phase_switch_lockout_selection)

        self.assertEqual(_UpdateCycleRelay._phase_switch_fallback_selection(service, "P1_P2_P3", "P1"), "P1_P2_P3")
        self.assertEqual(_UpdateCycleRelay._phase_switch_fallback_selection(service, None, "P1"), "P1")

        service.active_phase_selection = ""
        service.requested_phase_selection = "P1_P2"
        self.assertEqual(_UpdateCycleRelay._phase_switch_fallback_selection(service, None, "P1"), "P1_P2")
        service.requested_phase_selection = ""
        self.assertEqual(_UpdateCycleRelay._phase_switch_fallback_selection(service, None, "P1_P2_P3"), "P1_P2_P3")
        service.active_phase_selection = "bad"
        service.requested_phase_selection = "3P"
        self.assertEqual(_UpdateCycleRelay._phase_switch_fallback_selection(service, None, "P1"), "P1_P2_P3")
        self.assertEqual(_UpdateCycleRelay._phase_switch_fallback_selection(service, None, "P1_P2"), "P1_P2_P3")
        self.assertEqual(_UpdateCycleRelay._phase_switch_fallback_selection(bare_service, None, "P1_P2"), "P1_P2")

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
        svc = _phase_switch_mismatch_service(
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

        self.assertEqual(_UpdateCycleRelay._charger_state_max_age_seconds(svc), 2.0)
        svc._worker_poll_interval_seconds = 0.5
        svc.auto_shelly_soft_fail_seconds = 7.0
        self.assertEqual(_UpdateCycleRelay._charger_state_max_age_seconds(svc), 1.0)
        self.assertFalse(_UpdateCycleRelay._fresh_charger_enabled_readback(svc, 100.0))
        self.assertTrue(_UpdateCycleRelay._charger_requests_load(svc, 100.0))
        svc.auto_policy = None
        self.assertEqual(_UpdateCycleRelay._phase_switch_mismatch_retry_seconds(svc), 300.0)
        self.assertEqual(_UpdateCycleRelay._phase_switch_lockout_seconds(svc), 1800.0)
        svc._last_auto_metrics = {"surplus": 2600.0}
        self.assertEqual(
            _UpdateCycleRelay._surplus_auto_phase_target(
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
            _UpdateCycleRelay._set_runtime_attr(_NoRuntimeAttr(), "x", 1)

        svc._last_charger_state_status = "fault waiting"
        self.assertEqual(_UpdateCycleRelay.charger_health_override(svc, 100.0), "charger-fault")
        self.assertIsNone(_UpdateCycleRelay._derived_learned_current_target(svc, 100.0))
        self.assertIsNone(_UpdateCycleRelay.apply_charger_current_target(svc, True, 100.0, True))
        self.assertEqual(
            _UpdateCycleRelay._phase_switch_fallback_selection(
                _phase_switch_mismatch_service(active_phase_selection="P1_P2"),
                None,
                "P1",
            ),
            "P1_P2",
        )
        self.assertIsNone(_UpdateCycleRelay._phase_tuple_item(True))
        self.assertIsNone(_UpdateCycleRelay._resolved_phase_tuple((1.0, None, 3.0)))
        self.assertAlmostEqual(_UpdateCycleRelay._phase_voltage(400.0, "P1_P2_P3", "line"), 400.0 / math.sqrt(3.0))
        with self.assertRaisesRegex(TypeError, "last charger current target must be available"):
            _UpdateCycleRelay._known_charger_current_target(None)
        self.assertEqual(
            _UpdateCycleRelay._normalized_contactor_fault_counts({"contactor-suspected-open": True}),
            {},
        )
        self.assertEqual(_UpdateCycleRelay._phase_switch_mismatch_count(svc, "P1_P2_P3"), 0)
        with self.assertRaisesRegex(TypeError, "_apply_enabled_target must return bool"):
            _UpdateCycleRelay._relay_apply_result("bad")
        with self.assertRaisesRegex(TypeError, "_phase_values must return dict, got list"):
            _UpdateCycleRelay._checked_phase_data([])
        with self.assertRaisesRegex(TypeError, r"_phase_values must return dict\[str, dict\[str, float\]\]"):
            _UpdateCycleRelay._checked_phase_data({1: {}})
        with self.assertRaisesRegex(TypeError, r"_phase_values must return dict\[str, dict\[str, float\]\]"):
            _UpdateCycleRelay._checked_phase_data({"L1": {"power": True}})

    def test_relay_mixin_direct_helper_edges_cover_remaining_small_branches(self):
        svc = _phase_switch_mismatch_service(
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

        self.assertFalse(_UpdateCycleRelay._phase_switch_mismatch_retry_active(svc, "P1", "P1_P2", 100.0))
        self.assertIsNone(_UpdateCycleRelay._fresh_charger_enabled_readback(svc, 100.0))
        svc._last_charger_state_at = 100.0
        self.assertTrue(_UpdateCycleRelay._fresh_charger_enabled_readback(svc, 100.0))
        self.assertTrue(_UpdateCycleRelay._charger_requests_load(svc, 100.0))
        self.assertIsNone(_UpdateCycleRelay._observed_phase_selection_from_pm_status({}))
        self.assertEqual(_UpdateCycleRelay._observed_phase_selection(svc, {}, 100.0), "P1_P2")
        svc._last_charger_state_phase_selection = None
        self.assertIsNone(_UpdateCycleRelay._observed_phase_selection(svc, {}, 100.0))
        self.assertIsNone(_UpdateCycleRelay._phase_switch_verification_deadline(svc))
        svc._phase_switch_requested_at = 95.0
        self.assertEqual(_UpdateCycleRelay._phase_switch_verification_deadline(svc), 105.0)

        controller = UpdateCycleController(svc, _phase_values, lambda reason: {"init": 0}.get(reason, 99))
        controller._record_relay_sync_timeout(svc, relay_on=False, pm_confirmed=False, expected_relay=True, deadline_at=100.0)
        svc._mark_failure.assert_not_called()
        svc._warning_throttled.assert_not_called()

        with patch.object(_UpdateCycleRelay, "_charger_current_target_amps", return_value=None):
            self.assertIsNone(_UpdateCycleRelay.apply_charger_current_target(svc, True, 100.0, True))

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
        svc = _phase_switch_mismatch_service(active_phase_selection="", requested_phase_selection="P1_P2")
        self.assertEqual(_UpdateCycleRelay._phase_switch_fallback_selection(svc, None, "P1"), "P1_P2")

    def test_phase_switch_policy_contract_helpers_cover_candidate_delay_and_staging_edges(self):
        service = _auto_phase_service(
            _auto_phase_target_candidate="P1_P2",
            _auto_phase_target_since=80.0,
            _last_confirmed_pm_status={"output": False},
            _last_confirmed_pm_status_at=99.5,
            _peek_pending_relay_command=MagicMock(return_value=(None, None)),
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        self.assertEqual(controller._auto_phase_switch_delay_seconds(service, "P1", "P1_P2"), 10.0)
        self.assertEqual(controller._auto_phase_switch_delay_seconds(service, "P1_P2", "P1"), 5.0)
        self.assertEqual(controller._auto_phase_switch_delay_seconds(service, "P1", "P1"), 5.0)
        self.assertEqual(controller._auto_phase_switch_delay_seconds(service, "P1_P2", "P1_P2"), 5.0)
        default_policy_service = _auto_phase_service(auto_policy=SimpleNamespace(phase=SimpleNamespace()))
        self.assertEqual(controller._auto_phase_switch_delay_seconds(default_policy_service, "P1", "P1_P2"), 120.0)
        self.assertEqual(controller._auto_phase_switch_delay_seconds(default_policy_service, "P1_P2", "P1"), 30.0)
        service.auto_policy.phase.upshift_delay_seconds = -10.0
        service.auto_policy.phase.downshift_delay_seconds = -5.0
        self.assertEqual(controller._auto_phase_switch_delay_seconds(service, "P1", "P1_P2"), 0.0)
        self.assertEqual(controller._auto_phase_switch_delay_seconds(service, "P1_P2", "P1"), 0.0)
        service.auto_policy = None
        self.assertEqual(controller._auto_phase_switch_delay_seconds(service, "P1", "P1_P2"), 0.0)

        controller._clear_auto_phase_candidate(service)
        self.assertIsNone(service._auto_phase_target_candidate)
        self.assertIsNone(service._auto_phase_target_since)
        self.assertFalse(controller._auto_phase_candidate_ready(service, "P1", "P1_P2", 100.0))
        self.assertEqual(service._auto_phase_target_candidate, "P1_P2")
        self.assertEqual(service._auto_phase_target_since, 100.0)
        service._auto_phase_target_since = None
        self.assertFalse(controller._auto_phase_candidate_ready(service, "P1", "P1_P2", 101.0))
        self.assertEqual(service._auto_phase_target_since, 101.0)
        bare_candidate_service = SimpleNamespace()
        self.assertFalse(controller._auto_phase_candidate_ready(bare_candidate_service, "P1", "P1_P2", 102.0))
        self.assertEqual(bare_candidate_service._auto_phase_target_candidate, "P1_P2")
        self.assertEqual(bare_candidate_service._auto_phase_target_since, 102.0)
        missing_since_service = SimpleNamespace(_auto_phase_target_candidate="P1_P2")
        self.assertFalse(controller._auto_phase_candidate_ready(missing_since_service, "P1", "P1_P2", 103.0))
        self.assertEqual(missing_since_service._auto_phase_target_since, 103.0)
        service.auto_policy = _auto_phase_service().auto_policy
        service._auto_phase_target_candidate = "P1_P2"
        service._auto_phase_target_since = 90.0
        self.assertFalse(controller._auto_phase_candidate_ready(service, "P1", "P1_P2", 99.9))
        self.assertTrue(controller._auto_phase_candidate_ready(service, "P1", "P1_P2", 100.0))
        with patch.object(UpdateCycleController, "_auto_phase_switch_delay_seconds", return_value=10.0) as delay:
            self.assertTrue(controller._auto_phase_candidate_ready(service, "P1", "P1_P2", 100.0))
        delay.assert_called_once_with(service, "P1", "P1_P2")

        controller._stage_phase_switch(service, "P1_P2", 123.0, resume_relay=False)
        self.assertEqual(service.requested_phase_selection, "P1_P2")
        self.assertEqual(service._phase_switch_pending_selection, "P1_P2")
        self.assertEqual(service._phase_switch_state, controller.PHASE_SWITCH_WAITING_STATE)
        self.assertEqual(service._phase_switch_requested_at, 123.0)
        self.assertIsNone(service._phase_switch_stable_until)
        self.assertFalse(service._phase_switch_resume_relay)

    def test_phase_switch_policy_contract_helpers_cover_downshift_threshold_edges(self):
        service = _auto_phase_service()
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))
        policy = service.auto_policy.phase

        self.assertIsNone(
            controller._downshift_auto_phase_target(
                service,
                policy,
                ("P1", "P1_P2"),
                "P1",
                0,
                0.0,
                230.0,
            )
        )
        self.assertEqual(
            controller._downshift_auto_phase_target(
                service,
                policy,
                ("P1", "P1_P2"),
                "P1_P2",
                1,
                2609.9,
                230.0,
            ),
            ("P1", "phase-downshift", 2610.0),
        )
        self.assertIsNone(
            controller._downshift_auto_phase_target(
                service,
                policy,
                ("P1", "P1_P2"),
                "P1_P2",
                1,
                2610.0,
                230.0,
            )
        )
        default_policy = SimpleNamespace()
        self.assertEqual(
            controller._downshift_auto_phase_target(
                service,
                default_policy,
                ("P1", "P1_P2"),
                "P1_P2",
                1,
                2609.9,
                230.0,
            ),
            ("P1", "phase-downshift", 2610.0),
        )
        policy.downshift_margin_watts = 9999.0
        self.assertIsNone(
            controller._downshift_auto_phase_target(
                service,
                policy,
                ("P1", "P1_P2"),
                "P1_P2",
                1,
                0.0,
                230.0,
            )
        )
        service.min_current = 0.0
        self.assertIsNone(
            controller._downshift_auto_phase_target(
                service,
                policy,
                ("P1", "P1_P2"),
                "P1_P2",
                1,
                -1.0,
                230.0,
            )
        )

    def test_phase_switch_policy_contract_helpers_cover_apply_success_and_staged_paths(self):
        staged_service = _auto_phase_service(
            _phase_selection_requires_pause=MagicMock(return_value=True),
            _peek_pending_relay_command=MagicMock(return_value=(False, 99.0)),
            _auto_phase_target_candidate="P1_P2",
            _auto_phase_target_since=80.0,
        )
        controller = UpdateCycleController(staged_service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        self.assertFalse(controller._apply_auto_phase_target(staged_service, "P1_P2", True, False, 100.0))
        self.assertEqual(staged_service.requested_phase_selection, "P1_P2")
        self.assertEqual(staged_service._phase_switch_pending_selection, "P1_P2")
        self.assertEqual(staged_service._phase_switch_state, controller.PHASE_SWITCH_WAITING_STATE)
        self.assertEqual(staged_service._phase_switch_requested_at, 100.0)
        self.assertTrue(staged_service._phase_switch_resume_relay)
        self.assertIsNone(staged_service._auto_phase_target_candidate)
        staged_service._save_runtime_state.assert_called_once()

        applied_service = _auto_phase_service(
            active_phase_selection="P1",
            requested_phase_selection="P1",
            _phase_selection_requires_pause=MagicMock(return_value=False),
            _apply_phase_selection=MagicMock(return_value="P1_P2"),
            _phase_switch_mismatch_counts={"P1_P2": 2},
            _phase_switch_mismatch_active=True,
            _phase_switch_last_mismatch_selection="P1_P2",
            _phase_switch_last_mismatch_at=90.0,
            _phase_switch_lockout_selection="P1_P2",
            _phase_switch_lockout_reason="mismatch-threshold",
            _phase_switch_lockout_at=80.0,
            _phase_switch_lockout_until=180.0,
            _auto_phase_target_candidate="P1_P2",
            _auto_phase_target_since=80.0,
        )

        with patch.object(controller, "_clear_phase_switch_mismatch_tracking") as clear_mismatch:
            self.assertIsNone(controller._apply_auto_phase_target(applied_service, "P1_P2", True, False, 100.0))
        clear_mismatch.assert_called_once_with(applied_service, "P1_P2")
        applied_service._apply_phase_selection.assert_called_once_with("P1_P2")
        self.assertEqual(applied_service.requested_phase_selection, "P1_P2")
        self.assertEqual(applied_service.active_phase_selection, "P1_P2")
        self.assertIsNone(applied_service._phase_switch_lockout_selection)
        self.assertIsNone(applied_service._auto_phase_target_candidate)
        applied_service._save_runtime_state.assert_called_once()

        invalid_lockout_service = _auto_phase_service(
            active_phase_selection="P1_P2",
            requested_phase_selection="P1_P2",
            _phase_selection_requires_pause=MagicMock(return_value=False),
            _apply_phase_selection=MagicMock(return_value="P1"),
            _phase_switch_lockout_selection="bad-selection",
            _phase_switch_lockout_reason="mismatch-threshold",
            _phase_switch_lockout_at=80.0,
            _phase_switch_lockout_until=180.0,
        )
        self.assertIsNone(controller._apply_auto_phase_target(invalid_lockout_service, "P1", True, False, 100.0))
        self.assertIsNone(invalid_lockout_service._phase_switch_lockout_selection)
        self.assertEqual(invalid_lockout_service._phase_switch_lockout_reason, "")
        self.assertIsNone(invalid_lockout_service._phase_switch_lockout_at)
        self.assertIsNone(invalid_lockout_service._phase_switch_lockout_until)

        relay_on_fallback_service = _auto_phase_service(
            _phase_selection_requires_pause=MagicMock(return_value=True),
            _peek_pending_relay_command=MagicMock(return_value=(None, None)),
            _last_confirmed_pm_status={"output": False},
            _last_confirmed_pm_status_at=0.0,
            _apply_phase_selection=MagicMock(return_value="P1_P2"),
        )
        self.assertFalse(controller._apply_auto_phase_target(relay_on_fallback_service, "P1_P2", True, True, 100.0))
        relay_on_fallback_service._apply_phase_selection.assert_not_called()

        confirmed_output_service = _auto_phase_service(
            _phase_selection_requires_pause=MagicMock(return_value=True),
            _peek_pending_relay_command=MagicMock(return_value=(None, None)),
            _last_confirmed_pm_status={"output": True},
            _last_confirmed_pm_status_at=99.5,
            _apply_phase_selection=MagicMock(return_value="P1_P2"),
        )
        self.assertFalse(controller._apply_auto_phase_target(confirmed_output_service, "P1_P2", True, False, 100.0))
        confirmed_output_service._apply_phase_selection.assert_not_called()

    def test_phase_switch_policy_contract_helpers_cover_apply_failure_warning_contract(self):
        error = RuntimeError("phase relay failed")
        service = _auto_phase_service(
            _phase_selection_requires_pause=MagicMock(return_value=False),
            _apply_phase_selection=MagicMock(side_effect=error),
            _auto_phase_target_candidate="P1_P2",
            _auto_phase_target_since=90.0,
            auto_shelly_soft_fail_seconds=12.5,
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        self.assertIsNone(controller._apply_auto_phase_target(service, "P1_P2", True, False, 100.0))

        service._mark_failure.assert_called_once_with("shelly")
        service._warning_throttled.assert_called_once_with(
            "auto-phase-switch-failed",
            12.5,
            "Failed to apply Auto phase selection %s: %s",
            "P1_P2",
            error,
            exc_info=error,
        )
        self.assertIsNone(service._auto_phase_target_candidate)
        self.assertIsNone(service._auto_phase_target_since)
        service._save_runtime_state.assert_not_called()

    def test_phase_switch_policy_contract_helpers_cover_wrapper_delegation_contracts(self):
        service = _auto_phase_service(_phase_switch_state="waiting-relay-off")
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        with (
            patch.object(controller, "_pending_phase_switch_selection", return_value="P1_P2") as pending,
            patch.object(controller, "_phase_switch_state_active", return_value=True) as active,
        ):
            self.assertTrue(controller._auto_phase_switch_already_active(service))
        pending.assert_called_once_with(service)
        active.assert_called_once_with("P1_P2", "waiting-relay-off")

        partial_service = SimpleNamespace()
        with (
            patch.object(controller, "_pending_phase_switch_selection", return_value=None) as partial_pending,
            patch.object(controller, "_phase_switch_state_active", return_value=False) as partial_active,
        ):
            self.assertFalse(controller._auto_phase_switch_already_active(partial_service))
        partial_pending.assert_called_once_with(partial_service)
        partial_active.assert_called_once_with(None, "")

        with (
            patch.object(controller, "_auto_phase_selection_inactive", return_value=False) as inactive,
            patch.object(controller, "_auto_phase_switch_already_active", return_value=True) as already_active,
        ):
            self.assertTrue(controller._auto_phase_selection_blocked(service, auto_mode_active=True))
        inactive.assert_called_once_with(service, True)
        already_active.assert_called_once_with(service)

        with patch.object(controller, "_clear_auto_phase_candidate") as clear_candidate:
            self.assertTrue(controller._auto_phase_selection_inactive(service, auto_mode_active=False))
        clear_candidate.assert_called_once_with(service)

        with (
            patch.object(controller, "_ordered_auto_phase_selections", return_value=("P1", "P1_P2")) as ordered,
            patch.object(controller, "_current_phase_selection", return_value="P1") as current,
            patch.object(
                controller,
                "_auto_phase_target_selection",
                return_value=("P1_P2", "phase-upshift", 2500.0),
            ) as target,
            patch.object(controller, "_record_auto_phase_metrics") as record,
        ):
            self.assertEqual(
                controller._auto_phase_selection_decision(service, True, False, 231.0, 123.0),
                ("P1", "P1_P2", "phase-upshift", 2500.0),
            )
        ordered.assert_called_once_with(service)
        current.assert_called_once_with(service, ("P1", "P1_P2"))
        target.assert_called_once_with(service, ("P1", "P1_P2"), "P1", True, False, 231.0, 123.0)
        record.assert_called_once_with(
            service,
            current_selection="P1",
            target_selection="P1_P2",
            phase_reason="phase-upshift",
            threshold_watts=2500.0,
        )

        with (
            patch.object(controller, "_auto_phase_candidate_ready", return_value=False) as candidate_ready,
            patch.object(controller, "_record_auto_phase_metrics") as pending_record,
        ):
            self.assertFalse(
                controller._pending_auto_phase_target_ready(
                    service,
                    "P1",
                    "P1_P2",
                    130.0,
                    "phase-upshift",
                    2600.0,
                )
            )
        candidate_ready.assert_called_once_with(service, "P1", "P1_P2", 130.0)
        pending_record.assert_called_once_with(
            service,
            current_selection="P1",
            target_selection="P1_P2",
            phase_reason="phase-upshift-pending",
            threshold_watts=2600.0,
        )

        with (
            patch.object(controller, "_auto_phase_selection_blocked", return_value=False) as blocked,
            patch.object(
                controller,
                "_auto_phase_selection_decision",
                return_value=("P1", "P1_P2", "phase-upshift", 2500.0),
            ) as decision,
            patch.object(controller, "_pending_auto_phase_target_ready", return_value=False) as ready,
        ):
            self.assertIsNone(
                controller.maybe_apply_auto_phase_selection(
                    service,
                    desired_relay=True,
                    relay_on=False,
                    voltage=231.0,
                    now=140.0,
                    auto_mode_active=True,
                )
            )
        blocked.assert_called_once_with(service, True)
        decision.assert_called_once_with(service, True, False, 231.0, 140.0)
        ready.assert_called_once_with(service, "P1", "P1_P2", 140.0, "phase-upshift", 2500.0)

        with (
            patch.object(controller, "_auto_phase_selection_blocked", return_value=False),
            patch.object(
                controller,
                "_auto_phase_selection_decision",
                return_value=("P1", "P1_P2", "phase-upshift", 2500.0),
            ),
            patch.object(controller, "_pending_auto_phase_target_ready", return_value=True),
            patch.object(controller, "_apply_auto_phase_target", return_value=None) as apply_target,
        ):
            self.assertIsNone(
                controller.maybe_apply_auto_phase_selection(
                    service,
                    desired_relay=True,
                    relay_on=True,
                    voltage=231.0,
                    now=141.0,
                    auto_mode_active=True,
                )
            )
        apply_target.assert_called_once_with(service, "P1_P2", True, True, 141.0)

    def test_phase_switch_policy_contract_helpers_cover_staging_gate_inputs(self):
        service = _auto_phase_service(
            _phase_selection_requires_pause=MagicMock(return_value=False),
            _peek_pending_relay_command=MagicMock(return_value=(True, 99.0)),
            _last_confirmed_pm_status={"output": True, "apower": 1200.0, "current": 5.0},
            _last_confirmed_pm_status_at=99.5,
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        self.assertFalse(controller._phase_change_requires_staging(service, relay_on=True, now=100.0))
        service._phase_selection_requires_pause = MagicMock(return_value=True)
        self.assertTrue(controller._phase_change_requires_staging(service, relay_on=False, now=100.0))
        service._peek_pending_relay_command = MagicMock(return_value=(False, 99.0))
        self.assertTrue(controller._phase_change_requires_staging(service, relay_on=False, now=100.0))
        service._peek_pending_relay_command = MagicMock(return_value=(None, None))
        self.assertTrue(controller._phase_change_requires_staging(service, relay_on=False, now=100.0))
        service._last_confirmed_pm_status = {"output": False, "apower": 0.0, "current": 0.0}
        self.assertFalse(controller._phase_change_requires_staging(service, relay_on=False, now=100.0))
        service._last_confirmed_pm_status_at = 0.0
        self.assertTrue(controller._phase_change_requires_staging(service, relay_on=True, now=100.0))
        self.assertFalse(controller._phase_change_requires_staging(SimpleNamespace(), relay_on=True, now=100.0))

    def test_phase_switch_runtime_contract_helpers_cover_deadlines_observation_and_clearing(self):
        service = _auto_phase_service(
            _phase_switch_pending_selection="P1_P2",
            _phase_switch_requested_at=90.0,
            _phase_switch_stable_until=None,
            phase_switch_pause_seconds=-2.0,
            phase_switch_stabilization_seconds=-3.0,
            auto_shelly_soft_fail_seconds=-4.0,
            _charger_backend=object(),
            _last_charger_state_at=None,
            _last_charger_state_phase_selection="P1_P2",
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        self.assertEqual(controller._phase_switch_pause_seconds(service), 0.0)
        self.assertEqual(controller._phase_switch_stabilization_seconds(service), 0.0)
        self.assertEqual(controller._pending_phase_switch_selection(service), "P1_P2")
        self.assertEqual(controller._observed_phase_selection_from_pm_status({"_phase_selection": "3P"}), "P1_P2_P3")
        self.assertIsNone(controller._observed_phase_selection_from_pm_status({}))
        self.assertIsNone(controller._observed_phase_selection(service, {}, 100.0))
        service._last_charger_state_at = 100.0
        self.assertEqual(controller._observed_phase_selection(service, {}, 100.0), "P1_P2")
        self.assertEqual(controller._phase_switch_verification_deadline(service), 90.0)
        self.assertTrue(controller._phase_switch_verification_expired(service, 90.0))
        service._phase_switch_stable_until = 95.0
        service.auto_shelly_soft_fail_seconds = 7.0
        self.assertEqual(controller._phase_switch_verification_deadline(service), 102.0)
        self.assertFalse(controller._phase_switch_verification_expired(service, 101.9))
        self.assertTrue(controller._phase_switch_verification_expired(service, 102.0))

        service._phase_switch_resume_relay = True
        service._phase_switch_mismatch_active = True
        controller._clear_phase_switch_state(service)
        self.assertIsNone(service._phase_switch_pending_selection)
        self.assertIsNone(service._phase_switch_state)
        self.assertIsNone(service._phase_switch_requested_at)
        self.assertIsNone(service._phase_switch_stable_until)
        self.assertFalse(service._phase_switch_resume_relay)
        self.assertFalse(service._phase_switch_mismatch_active)

    def test_phase_switch_runtime_contract_helpers_cover_mismatch_reporting_and_abort(self):
        service = _auto_phase_service(
            auto_policy=SimpleNamespace(
                phase=SimpleNamespace(
                    mismatch_retry_seconds=60.0,
                    mismatch_lockout_count=1,
                    mismatch_lockout_seconds=30.0,
                )
            ),
            _phase_switch_requested_at=80.0,
            _phase_switch_mismatch_counts={},
            _phase_switch_last_mismatch_selection=None,
            _phase_switch_last_mismatch_at=None,
            _phase_switch_lockout_selection=None,
            _phase_switch_lockout_reason="",
            _phase_switch_lockout_at=None,
            _phase_switch_lockout_until=None,
            _set_health=MagicMock(),
            _mark_failure=MagicMock(),
            _warning_throttled=MagicMock(),
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        controller._report_phase_switch_mismatch(service, "P1_P2", None, 100.0)

        self.assertEqual(service._phase_switch_mismatch_counts, {"P1_P2": 1})
        self.assertEqual(service._phase_switch_last_mismatch_selection, "P1_P2")
        self.assertEqual(service._phase_switch_last_mismatch_at, 100.0)
        self.assertEqual(service._phase_switch_lockout_selection, "P1_P2")
        self.assertEqual(service._phase_switch_lockout_reason, "mismatch-threshold")
        self.assertEqual(service._phase_switch_lockout_at, 100.0)
        self.assertEqual(service._phase_switch_lockout_until, 130.0)
        service._mark_failure.assert_called_once_with("shelly")
        service._set_health.assert_called_once_with("phase-switch-mismatch", cached=False)
        warning_args = service._warning_throttled.call_args.args
        self.assertEqual(warning_args[0], "phase-switch-mismatch")
        self.assertEqual(warning_args[1], 10.0)
        self.assertEqual(warning_args[3:8], ("P1_P2", 20.0, "unknown", 1, 1))

        service._phase_switch_pending_selection = "P1_P2"
        service._phase_switch_state = controller.PHASE_SWITCH_STABILIZING_STATE
        service._phase_switch_resume_relay = False
        service.active_phase_selection = "P1"
        service.requested_phase_selection = "P1_P2"
        relay_on, power, current, confirmed = controller._abort_phase_switch_after_mismatch(
            service,
            "P1_P2",
            "P1",
            True,
            1200.0,
            5.0,
            True,
            101.0,
            False,
        )
        self.assertEqual((relay_on, power, current, confirmed), (True, 1200.0, 5.0, True))
        self.assertEqual(service.requested_phase_selection, "P1")
        self.assertEqual(service.active_phase_selection, "P1")
        self.assertIsNone(service._phase_switch_pending_selection)

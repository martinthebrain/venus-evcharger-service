# SPDX-License-Identifier: GPL-3.0-or-later
import venus_evcharger.update.relay_phase_switch_runtime as phase_switch_runtime
from venus_evcharger.update.relay_charger_readback import _RelayChargerReadback

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

    def test_charger_current_contracts_cover_contactor_thresholds_and_limits(self):
        controller = UpdateCycleController(SimpleNamespace(), _phase_values, lambda reason: {"init": 0}.get(reason, 99))
        service = SimpleNamespace()

        self.assertEqual(controller._contactor_heuristic_delay_seconds(service), 0.0)
        self.assertEqual(controller._contactor_lockout_threshold(service), 3)
        self.assertEqual(controller._contactor_lockout_persistence_seconds(service), 60.0)
        self.assertEqual(controller._contactor_power_threshold_w(service), 100.0)
        self.assertEqual(controller._contactor_current_threshold_a(service), 1.0)

        service.auto_shelly_soft_fail_seconds = -5.0
        service.auto_contactor_fault_latch_count = -2
        service.auto_contactor_fault_latch_seconds = -9.0
        service.charging_threshold_watts = -40.0
        service.min_current = -8.0
        self.assertEqual(controller._contactor_heuristic_delay_seconds(service), 0.0)
        self.assertEqual(controller._contactor_lockout_threshold(service), 0)
        self.assertEqual(controller._contactor_lockout_persistence_seconds(service), 0.0)
        self.assertEqual(controller._contactor_power_threshold_w(service), 100.0)
        self.assertEqual(controller._contactor_current_threshold_a(service), 1.0)

        service.auto_shelly_soft_fail_seconds = 7.5
        service.auto_contactor_fault_latch_count = 4
        service.auto_contactor_fault_latch_seconds = 121.0
        service.charging_threshold_watts = 250.5
        service.min_current = 12.0
        service.max_current = 15.0
        self.assertEqual(controller._contactor_heuristic_delay_seconds(service), 7.5)
        self.assertEqual(controller._contactor_lockout_threshold(service), 4)
        self.assertEqual(controller._contactor_lockout_persistence_seconds(service), 121.0)
        self.assertEqual(controller._contactor_power_threshold_w(service), 250.5)
        service.charging_threshold_watts = 100.5
        self.assertEqual(controller._contactor_power_threshold_w(service), 100.5)
        service.charging_threshold_watts = 250.5
        self.assertEqual(controller._contactor_current_threshold_a(service), 3.0)
        self.assertEqual(controller._charger_current_limits(service), (12.0, 15.0))
        self.assertEqual(controller._apply_min_current_limit(11.0, 12.0), 12.0)
        self.assertEqual(controller._apply_min_current_limit(13.0, 12.0), 13.0)
        self.assertEqual(controller._apply_min_current_limit(11.0, None), 11.0)
        self.assertEqual(controller._apply_max_current_limit(16.0, 15.0), 15.0)
        self.assertEqual(controller._apply_max_current_limit(14.0, 15.0), 14.0)
        self.assertEqual(controller._apply_max_current_limit(16.0, 0.0), 16.0)

    def test_charger_health_contracts_treat_load_thresholds_as_inclusive(self):
        controller = UpdateCycleController(SimpleNamespace(), _phase_values, lambda reason: {"init": 0}.get(reason, 99))
        service = SimpleNamespace(charging_threshold_watts=250.0, min_current=12.0)

        self.assertFalse(controller._pm_load_active(service, 5000.0, 20.0, False))
        self.assertFalse(controller._pm_load_active(service, None, None, True))
        self.assertTrue(controller._pm_load_active(service, 250.0, None, True))
        self.assertFalse(controller._pm_load_active(service, 249.99, 2.99, True))
        self.assertTrue(controller._pm_load_active(service, 249.99, 3.0, True))
        self.assertTrue(controller._pm_load_active(service, None, 3.0, True))

    def test_charger_health_contracts_use_only_fresh_charger_readback_for_load(self):
        controller = UpdateCycleController(SimpleNamespace(), _phase_values, lambda reason: {"init": 0}.get(reason, 99))
        service = SimpleNamespace(
            _charger_backend=object(),
            _last_charger_state_at=100.0,
            _worker_poll_interval_seconds=1.0,
            auto_shelly_soft_fail_seconds=10.0,
            charging_threshold_watts=250.0,
            min_current=8.0,
            _last_charger_state_power_w=250.0,
            _last_charger_state_actual_current_amps=0.0,
        )

        self.assertTrue(controller._charger_load_active(service, 100.0))
        service._last_charger_state_power_w = 249.99
        service._last_charger_state_actual_current_amps = 2.0
        self.assertTrue(controller._charger_load_active(service, 100.0))
        service._last_charger_state_actual_current_amps = 1.99
        self.assertFalse(controller._charger_load_active(service, 100.0))
        service._last_charger_state_power_w = 250.0
        service._last_charger_state_actual_current_amps = 2.0
        self.assertFalse(controller._charger_load_active(service, 102.1))
        service._last_charger_state_at = 100.0
        service._charger_backend = None
        self.assertFalse(controller._charger_load_active(service, 100.0))

    def test_charger_health_contracts_track_heuristic_age_and_clear_inactive_conditions(self):
        controller = UpdateCycleController(SimpleNamespace(), _phase_values, lambda reason: {"init": 0}.get(reason, 99))
        service = SimpleNamespace(_time_now=MagicMock(return_value=100.0), _contactor_suspected_open_since=80.0)

        self.assertIsNone(controller._heuristic_condition_age(service, "_contactor_suspected_open_since", False, None))
        self.assertIsNone(service._contactor_suspected_open_since)
        self.assertEqual(controller._heuristic_condition_age(service, "_contactor_suspected_open_since", True, None), 0.0)
        self.assertEqual(service._contactor_suspected_open_since, 100.0)
        self.assertEqual(controller._heuristic_condition_age(service, "_contactor_suspected_open_since", True, 112.0), 12.0)
        service._contactor_suspected_open_since = 120.0
        self.assertEqual(controller._heuristic_condition_age(service, "_contactor_suspected_open_since", True, 112.0), 0.0)
        missing_service = SimpleNamespace()
        self.assertEqual(controller._heuristic_condition_age(missing_service, "_contactor_suspected_open_since", True, 50.0), 0.0)
        self.assertEqual(missing_service._contactor_suspected_open_since, 50.0)

    def test_charger_health_contracts_latch_contactor_faults_by_count_or_persistence(self):
        controller = UpdateCycleController(SimpleNamespace(), _phase_values, lambda reason: {"init": 0}.get(reason, 99))
        service = SimpleNamespace(
            _time_now=MagicMock(return_value=500.0),
            auto_contactor_fault_latch_count=2,
            auto_contactor_fault_latch_seconds=60.0,
        )

        self.assertIsNone(controller._remember_contactor_fault(service, "bogus", 100.0))
        self.assertIsNone(service._contactor_fault_active_reason)
        self.assertEqual(controller._remember_contactor_fault(service, "contactor-suspected-open", 100.0), "contactor-suspected-open")
        self.assertEqual(service._contactor_fault_counts, {"contactor-suspected-open": 1})
        self.assertEqual(service._contactor_fault_active_since, 100.0)
        self.assertEqual(controller._remember_contactor_fault(service, "contactor-suspected-open", 105.0), "contactor-suspected-open")
        self.assertEqual(service._contactor_fault_counts, {"contactor-suspected-open": 1})
        controller._clear_contactor_fault_active_state(service)
        self.assertEqual(controller._remember_contactor_fault(service, "contactor-suspected-open", 110.0), "contactor-lockout-open")
        self.assertEqual(service._contactor_lockout_reason, "contactor-suspected-open")
        self.assertEqual(service._contactor_lockout_source, "count-threshold")
        self.assertEqual(service._contactor_lockout_at, 110.0)

        service = SimpleNamespace(auto_contactor_fault_latch_count=99, auto_contactor_fault_latch_seconds=5.0)
        self.assertEqual(controller._remember_contactor_fault(service, "contactor-suspected-welded", 200.0), "contactor-suspected-welded")
        self.assertEqual(controller._remember_contactor_fault(service, "contactor-suspected-welded", 205.0), "contactor-lockout-welded")
        self.assertEqual(service._contactor_lockout_source, "persistent")

        timed_service = SimpleNamespace(_time_now=MagicMock(return_value=300.0))
        controller._engage_contactor_lockout(timed_service, "contactor-suspected-welded", None, "  ")
        self.assertEqual(timed_service._contactor_lockout_reason, "contactor-suspected-welded")
        self.assertEqual(timed_service._contactor_lockout_source, "count-threshold")
        self.assertEqual(timed_service._contactor_lockout_at, 300.0)

    def test_charger_health_contracts_prioritize_feedback_safety_before_heuristics(self):
        controller = UpdateCycleController(SimpleNamespace(), _phase_values, lambda reason: {"init": 0}.get(reason, 99))
        service = SimpleNamespace(
            _charger_backend=object(),
            _last_charger_state_at=100.0,
            _last_charger_state_power_w=0.0,
            _last_charger_state_actual_current_amps=0.0,
            _last_charger_state_status="charging",
            _last_switch_feedback_at=100.0,
            _last_switch_feedback_closed=None,
            _last_switch_interlock_ok=False,
            _worker_poll_interval_seconds=1.0,
            auto_shelly_soft_fail_seconds=5.0,
            charging_threshold_watts=250.0,
            min_current=8.0,
            _contactor_suspected_open_since=80.0,
            _contactor_suspected_welded_since=81.0,
        )

        self.assertEqual(
            controller.switch_feedback_health_override(service, True, True, 100.0, power=0.0, current=0.0, pm_confirmed=True),
            "contactor-interlock",
        )
        self.assertIsNone(service._contactor_suspected_open_since)
        self.assertIsNone(service._contactor_suspected_welded_since)

        service._last_switch_interlock_ok = True
        service._last_switch_feedback_closed = False
        service._contactor_suspected_open_since = 80.0
        self.assertEqual(
            controller.switch_feedback_health_override(service, True, True, 100.0, power=0.0, current=0.0, pm_confirmed=True),
            "contactor-feedback-mismatch",
        )
        self.assertIsNone(service._contactor_suspected_open_since)

        service._last_switch_feedback_closed = None
        service._contactor_suspected_open_since = 80.0
        service._contactor_suspected_welded_since = None
        self.assertEqual(
            controller.switch_feedback_health_override(service, True, True, 100.0, power=0.0, current=0.0, pm_confirmed=True),
            "contactor-suspected-open",
        )

        service._contactor_suspected_open_since = None
        service._contactor_suspected_welded_since = 80.0
        self.assertEqual(
            controller.switch_feedback_health_override(service, False, False, 100.0, power=250.0, current=0.0, pm_confirmed=True),
            "contactor-suspected-welded",
        )

    def test_charger_health_contracts_keep_status_token_priorities_stable(self):
        controller = UpdateCycleController(SimpleNamespace(), _phase_values, lambda reason: {"init": 0}.get(reason, 99))
        service = SimpleNamespace(idle_status=9)

        self.assertEqual(
            controller._charger_status_override_from_tokens(service, {"waiting"}, True),
            (4, "charger-status-waiting"),
        )
        self.assertEqual(
            controller._charger_status_override_from_tokens(service, {"waiting"}, False),
            (6, "charger-status-waiting"),
        )
        self.assertEqual(
            controller._charger_status_override_from_tokens(service, {"ready"}, True),
            (9, "charger-status-ready"),
        )
        self.assertEqual(
            controller._charger_status_override_from_tokens(service, {"finished", "charging"}, True),
            (3, "charger-status-finished"),
        )
        self.assertEqual(
            controller._charger_status_override_from_tokens(service, {"charging", "ready"}, True),
            (2, "charger-status-charging"),
        )

        current_service = SimpleNamespace(min_current=12.0, max_current=15.0)
        self.assertEqual(controller._apply_max_current_limit(2.0, 1.0), 1.0)
        self.assertEqual(controller._clamped_charger_current_target(current_service, 11.0), 12.0)
        self.assertEqual(controller._clamped_charger_current_target(current_service, 16.0), 15.0)

        current_service.min_current = float("nan")
        current_service.max_current = float("inf")
        self.assertEqual(controller._charger_current_limits(current_service), (None, None))
        self.assertIsNone(controller._clamped_charger_current_target(current_service, -1.0))
        self.assertIsNone(controller._clamped_charger_current_target(current_service, 0.0))
        self.assertEqual(controller._clamped_charger_current_target(current_service, 0.5), 0.5)
        self.assertEqual(controller._charger_current_limits(SimpleNamespace()), (None, None))

    def test_charger_health_contracts_observed_load_short_circuits_and_delegates_explicitly(self):
        controller = UpdateCycleController(SimpleNamespace(), _phase_values, lambda reason: {"init": 0}.get(reason, 99))
        service = SimpleNamespace()

        with patch.object(UpdateCycleController, "_pm_load_active", return_value=True) as pm_load, patch.object(
            UpdateCycleController, "_charger_load_active", return_value=True
        ) as charger_load:
            self.assertTrue(controller._observed_load_active(service, 12.0, 3.0, True, 123.0))
        pm_load.assert_called_once_with(service, 12.0, 3.0, True)
        charger_load.assert_not_called()

        with patch.object(UpdateCycleController, "_pm_load_active", return_value=False) as pm_load, patch.object(
            UpdateCycleController, "_charger_load_active", return_value=True
        ) as charger_load:
            self.assertTrue(controller._observed_load_active(service, 12.0, 3.0, False, 124.0))
        pm_load.assert_called_once_with(service, 12.0, 3.0, False)
        charger_load.assert_called_once_with(service, 124.0)

        with patch.object(UpdateCycleController, "_pm_load_active", return_value=False), patch.object(
            UpdateCycleController, "_charger_load_active", return_value=False
        ):
            self.assertFalse(controller._observed_load_active(service, None, None, False, None))

    def test_charger_health_contracts_normalize_fault_reasons_and_counts_strictly(self):
        controller = UpdateCycleController(SimpleNamespace(), _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        self.assertEqual(controller._base_contactor_fault_reason(" contactor-suspected-open "), "contactor-suspected-open")
        self.assertEqual(controller._base_contactor_fault_reason("contactor-suspected-welded"), "contactor-suspected-welded")
        self.assertIsNone(controller._base_contactor_fault_reason(""))
        self.assertIsNone(controller._base_contactor_fault_reason(None))
        self.assertIsNone(controller._base_contactor_fault_reason("contactor-lockout-open"))

        self.assertEqual(
            controller._normalized_contactor_fault_counts(
                {
                    "contactor-suspected-open": True,
                    "contactor-suspected-welded": False,
                    "open": 2,
                    b"contactor-suspected-open": 3,
                }
            ),
            {},
        )
        self.assertEqual(
            controller._normalized_contactor_fault_counts(
                {"contactor-suspected-open": -4, "contactor-suspected-welded": 3}
            ),
            {"contactor-suspected-open": 0, "contactor-suspected-welded": 3},
        )
        self.assertEqual(
            controller._normalized_contactor_fault_counts({"bad": 7, "contactor-suspected-open": 2}),
            {"contactor-suspected-open": 2},
        )
        self.assertEqual(
            controller._normalized_contactor_fault_counts(
                {"contactor-suspected-open": True, "contactor-suspected-welded": 4}
            ),
            {"contactor-suspected-welded": 4},
        )

        service = SimpleNamespace(_contactor_fault_counts={"contactor-suspected-open": -4, "contactor-suspected-welded": 3})
        self.assertEqual(controller._contactor_fault_count(service, "contactor-suspected-open"), 0)
        self.assertEqual(controller._contactor_fault_count(service, " contactor-suspected-welded "), 3)
        self.assertEqual(controller._contactor_fault_count(service, None), 0)
        self.assertEqual(controller._contactor_fault_count(service, "contactor-lockout-open"), 0)
        self.assertEqual(controller._contactor_fault_count(SimpleNamespace(_contactor_fault_counts={}), "contactor-suspected-open"), 0)

    def test_charger_health_contracts_clear_and_report_contactor_lockout_state(self):
        controller = UpdateCycleController(SimpleNamespace(), _phase_values, lambda reason: {"init": 0}.get(reason, 99))
        service = SimpleNamespace(_contactor_fault_active_reason="contactor-suspected-open", _contactor_fault_active_since=100.0)

        controller._clear_contactor_fault_active_state(service)
        self.assertIsNone(service._contactor_fault_active_reason)
        self.assertIsNone(service._contactor_fault_active_since)
        self.assertEqual(controller._contactor_lockout_health_reason("contactor-suspected-open"), "contactor-lockout-open")
        self.assertEqual(controller._contactor_lockout_health_reason("contactor-suspected-welded"), "contactor-lockout-welded")
        self.assertIsNone(controller._contactor_lockout_health_reason("bogus"))

        service._contactor_lockout_reason = "contactor-suspected-welded"
        self.assertEqual(controller._active_contactor_lockout_health(service), "contactor-lockout-welded")
        service._contactor_lockout_reason = ""
        self.assertIsNone(controller._active_contactor_lockout_health(service))
        self.assertIsNone(controller._active_contactor_lockout_health(SimpleNamespace()))

    def test_charger_health_contracts_cover_contactor_lockout_threshold_boundaries(self):
        controller = UpdateCycleController(SimpleNamespace(), _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        threshold_service = SimpleNamespace(auto_contactor_fault_latch_count=1)
        self.assertTrue(controller._contactor_fault_exceeds_count_threshold(threshold_service, 1))
        threshold_service = SimpleNamespace(auto_contactor_fault_latch_count=2)
        self.assertFalse(controller._contactor_fault_exceeds_count_threshold(threshold_service, 1))
        self.assertTrue(controller._contactor_fault_exceeds_count_threshold(threshold_service, 2))
        threshold_service.auto_contactor_fault_latch_count = 0
        self.assertFalse(controller._contactor_fault_exceeds_count_threshold(threshold_service, 99))

        persistence_service = SimpleNamespace(auto_contactor_fault_latch_count=99, auto_contactor_fault_latch_seconds=5.0)
        self.assertEqual(
            controller._remember_contactor_fault(persistence_service, "contactor-suspected-open", 100.0),
            "contactor-suspected-open",
        )
        self.assertEqual(
            controller._remember_contactor_fault(persistence_service, "contactor-suspected-open", 104.99),
            "contactor-suspected-open",
        )
        self.assertEqual(
            controller._remember_contactor_fault(persistence_service, "contactor-suspected-open", 105.0),
            "contactor-lockout-open",
        )

        no_persistence_service = SimpleNamespace(auto_contactor_fault_latch_count=99, auto_contactor_fault_latch_seconds=0.0)
        self.assertEqual(
            controller._remember_contactor_fault(no_persistence_service, "contactor-suspected-open", 200.0),
            "contactor-suspected-open",
        )
        self.assertEqual(
            controller._remember_contactor_fault(no_persistence_service, "contactor-suspected-open", 200.0),
            "contactor-suspected-open",
        )
        self.assertFalse(hasattr(no_persistence_service, "_contactor_lockout_reason"))

        subsecond_service = SimpleNamespace(auto_contactor_fault_latch_count=99, auto_contactor_fault_latch_seconds=0.5)
        self.assertEqual(
            controller._remember_contactor_fault(subsecond_service, "contactor-suspected-welded", 300.0),
            "contactor-suspected-welded",
        )
        self.assertEqual(
            controller._remember_contactor_fault(subsecond_service, "contactor-suspected-welded", 300.5),
            "contactor-lockout-welded",
        )

        clocked_service = SimpleNamespace(
            _time_now=MagicMock(return_value=400.0),
            auto_contactor_fault_latch_count=99,
            auto_contactor_fault_latch_seconds=60.0,
        )
        self.assertEqual(
            controller._remember_contactor_fault(clocked_service, "contactor-suspected-open", None),
            "contactor-suspected-open",
        )
        self.assertEqual(clocked_service._contactor_fault_active_since, 400.0)

    def test_charger_health_contracts_activate_contactor_fault_switches_reasons_with_existing_since(self):
        controller = UpdateCycleController(SimpleNamespace(), _phase_values, lambda reason: {"init": 0}.get(reason, 99))
        service = SimpleNamespace(
            _contactor_fault_counts={"contactor-suspected-open": 2},
            _contactor_fault_active_reason="contactor-suspected-open",
            _contactor_fault_active_since=50.0,
        )

        self.assertEqual(controller._activate_contactor_fault_reason(service, "contactor-suspected-welded", 100.0), 100.0)
        self.assertEqual(service._contactor_fault_active_reason, "contactor-suspected-welded")
        self.assertEqual(service._contactor_fault_active_since, 100.0)
        self.assertEqual(service._contactor_fault_counts["contactor-suspected-welded"], 1)

    def test_charger_health_contracts_detect_fault_status_and_fault_field_independently(self):
        controller = UpdateCycleController(SimpleNamespace(), _phase_values, lambda reason: {"init": 0}.get(reason, 99))
        service = SimpleNamespace(
            _charger_backend=object(),
            _last_charger_state_at=100.0,
            _worker_poll_interval_seconds=1.0,
            auto_shelly_soft_fail_seconds=10.0,
            _last_charger_transport_reason=None,
            _last_charger_transport_source=None,
            _last_charger_transport_detail=None,
            _last_charger_transport_at=None,
            _charger_retry_reason=None,
            _charger_retry_source=None,
            _charger_retry_until=None,
            _last_charger_state_fault="no fault",
            _last_charger_state_status="fault",
        )

        self.assertEqual(controller.charger_health_override(service, 100.0), "charger-fault")
        service._last_charger_state_status = "ready"
        service._last_charger_state_fault = "fault"
        self.assertEqual(controller.charger_health_override(service, 100.0), "charger-fault")
        service._last_charger_state_fault = "no fault"
        self.assertIsNone(controller.charger_health_override(service, 100.0))

    def test_charger_health_contracts_switch_feedback_override_order_is_explicit(self):
        controller = UpdateCycleController(SimpleNamespace(), _phase_values, lambda reason: {"init": 0}.get(reason, 99))
        service = SimpleNamespace()

        with patch.object(UpdateCycleController, "_switch_feedback_safety_override", return_value="contactor-interlock") as safety, patch.object(
            UpdateCycleController, "_active_contactor_lockout_health", return_value="contactor-lockout-open"
        ) as lockout, patch.object(UpdateCycleController, "_switch_feedback_heuristic_override", return_value="contactor-suspected-open") as heuristic:
            self.assertEqual(
                controller.switch_feedback_health_override(
                    service,
                    True,
                    False,
                    100.0,
                    power=1.0,
                    current=2.0,
                    pm_confirmed=True,
                ),
                "contactor-interlock",
            )
        safety.assert_called_once_with(service, True, False, 100.0)
        lockout.assert_not_called()
        heuristic.assert_not_called()

        with patch.object(UpdateCycleController, "_switch_feedback_safety_override", return_value=None), patch.object(
            UpdateCycleController, "_active_contactor_lockout_health", return_value="contactor-lockout-open"
        ) as lockout, patch.object(UpdateCycleController, "_switch_feedback_heuristic_override", return_value="contactor-suspected-open") as heuristic:
            self.assertEqual(controller.switch_feedback_health_override(service, False, True, None), "contactor-lockout-open")
        lockout.assert_called_once_with(service)
        heuristic.assert_not_called()

        with patch.object(UpdateCycleController, "_switch_feedback_safety_override", return_value=None), patch.object(
            UpdateCycleController, "_active_contactor_lockout_health", return_value=None
        ), patch.object(UpdateCycleController, "_switch_feedback_heuristic_override", return_value=None) as heuristic:
            self.assertIsNone(
                controller.switch_feedback_health_override(
                    service,
                    False,
                    False,
                    101.0,
                    power=3.0,
                    current=4.0,
                    pm_confirmed=False,
                )
            )
        heuristic.assert_called_once_with(service, False, 3.0, 4.0, False, 101.0)

        with patch.object(UpdateCycleController, "_switch_feedback_safety_override", return_value=None), patch.object(
            UpdateCycleController, "_active_contactor_lockout_health", return_value=None
        ), patch.object(UpdateCycleController, "_switch_feedback_heuristic_override", return_value=None) as heuristic:
            self.assertIsNone(controller.switch_feedback_health_override(service, True, False, 102.0))
        heuristic.assert_called_once_with(service, False, None, None, False, 102.0)

    def test_charger_health_contracts_switch_feedback_safety_needs_active_command_or_mismatch(self):
        controller = UpdateCycleController(SimpleNamespace(), _phase_values, lambda reason: {"init": 0}.get(reason, 99))
        service = SimpleNamespace(
            _last_switch_feedback_at=100.0,
            _worker_poll_interval_seconds=1.0,
            auto_shelly_soft_fail_seconds=10.0,
            _last_switch_interlock_ok=False,
            _last_switch_feedback_closed=None,
            _contactor_suspected_open_since=80.0,
        )

        self.assertIsNone(controller._switch_feedback_safety_override(service, False, False, 100.0))
        self.assertEqual(service._contactor_suspected_open_since, 80.0)
        self.assertEqual(controller._switch_feedback_safety_override(service, False, True, 100.0), "contactor-interlock")
        self.assertIsNone(service._contactor_suspected_open_since)

        service._last_switch_interlock_ok = True
        service._last_switch_feedback_closed = True
        self.assertIsNone(controller._switch_feedback_safety_override(service, True, True, 100.0))
        self.assertEqual(controller._switch_feedback_safety_override(service, False, False, 100.0), "contactor-feedback-mismatch")

    def test_charger_health_contracts_heuristic_override_prefers_welded_and_clears_inactive(self):
        controller = UpdateCycleController(SimpleNamespace(), _phase_values, lambda reason: {"init": 0}.get(reason, 99))
        service = SimpleNamespace(auto_shelly_soft_fail_seconds=5.0, _contactor_fault_active_reason="contactor-suspected-open")

        with patch.object(UpdateCycleController, "_contactor_suspected_ages", return_value=(10.0, 10.0)) as ages, patch.object(
            UpdateCycleController, "_remember_contactor_fault", return_value="contactor-suspected-welded"
        ) as remember:
            self.assertEqual(
                controller._switch_feedback_heuristic_override(service, True, 1.0, 2.0, True, 100.0),
                "contactor-suspected-welded",
            )
        ages.assert_called_once_with(service, True, 1.0, 2.0, True, 100.0)
        remember.assert_called_once_with(service, "contactor-suspected-welded", 100.0)

        with patch.object(UpdateCycleController, "_contactor_suspected_ages", return_value=(None, 5.0)), patch.object(
            UpdateCycleController, "_remember_contactor_fault", return_value="contactor-suspected-welded"
        ) as remember:
            self.assertEqual(
                controller._switch_feedback_heuristic_override(service, False, None, None, False, 100.0),
                "contactor-suspected-welded",
            )
        remember.assert_called_once_with(service, "contactor-suspected-welded", 100.0)

        with patch.object(UpdateCycleController, "_contactor_suspected_ages", return_value=(4.99, None)):
            self.assertIsNone(controller._switch_feedback_heuristic_override(service, True, None, None, False, 101.0))
        self.assertIsNone(service._contactor_fault_active_reason)
        self.assertIsNone(service._contactor_fault_active_since)

    def test_charger_health_contracts_contactor_suspected_ages_delegate_conditions_explicitly(self):
        controller = UpdateCycleController(SimpleNamespace(), _phase_values, lambda reason: {"init": 0}.get(reason, 99))
        service = SimpleNamespace()

        with patch.object(UpdateCycleController, "_observed_load_active", return_value=False) as observed, patch.object(
            UpdateCycleController, "_charger_requests_load", return_value=True
        ) as requests, patch.object(UpdateCycleController, "_heuristic_condition_age", side_effect=(12.0, None)) as age:
            self.assertEqual(controller._contactor_suspected_ages(service, True, 10.0, 1.0, True, 200.0), (12.0, None))
        observed.assert_called_once_with(service, 10.0, 1.0, True, 200.0)
        requests.assert_called_once_with(service, 200.0)
        age.assert_any_call(service, "_contactor_suspected_open_since", True, 200.0)
        age.assert_any_call(service, "_contactor_suspected_welded_since", False, 200.0)

        with patch.object(UpdateCycleController, "_observed_load_active", return_value=True), patch.object(
            UpdateCycleController, "_charger_requests_load", return_value=False
        ), patch.object(UpdateCycleController, "_heuristic_condition_age", side_effect=(None, 7.0)) as age:
            self.assertEqual(controller._contactor_suspected_ages(service, False, None, None, False, 201.0), (None, 7.0))
        age.assert_any_call(service, "_contactor_suspected_open_since", False, 201.0)
        age.assert_any_call(service, "_contactor_suspected_welded_since", True, 201.0)

    def test_charger_health_contracts_status_token_rules_are_public_contracts(self):
        controller = UpdateCycleController(SimpleNamespace(), _phase_values, lambda reason: {"init": 0}.get(reason, 99))
        service = SimpleNamespace(idle_status=8)

        self.assertEqual(
            controller._charger_status_token_rules(service, True),
            (
                ({"complete", "completed", "finished", "done"}, 3, "charger-status-finished"),
                ({"paused", "waiting", "suspended", "sleeping"}, 4, "charger-status-waiting"),
                ({"charging"}, 2, "charger-status-charging"),
                ({"ready", "connected", "available", "idle"}, 8, "charger-status-ready"),
            ),
        )
        self.assertEqual(controller._charger_status_token_rules(service, False)[1], ({"paused", "waiting", "suspended", "sleeping"}, 6, "charger-status-waiting"))
        self.assertEqual(
            controller._charger_status_token_rules(SimpleNamespace(), True)[3],
            ({"ready", "connected", "available", "idle"}, 1, "charger-status-ready"),
        )

    def test_charger_current_contracts_cover_learned_target_inputs_and_staleness(self):
        controller = UpdateCycleController(SimpleNamespace(), _phase_values, lambda reason: {"init": 0}.get(reason, 99))
        service = SimpleNamespace(
            learned_charge_power_state="stable",
            learned_charge_power_watts=1900.0,
            learned_charge_power_voltage=230.0,
            learned_charge_power_phase="L1",
            learned_charge_power_updated_at=100.0,
            auto_learn_charge_power_max_age_seconds=20.0,
            phase="P1_P2_P3",
            voltage_mode="phase",
            min_current=6.0,
            max_current=16.0,
        )

        self.assertTrue(controller._stable_learned_current_state(service))
        self.assertEqual(
            controller._raw_stable_learned_current_inputs(service),
            (1900.0, 230.0, "L1", 100.0, 20.0),
        )
        self.assertEqual(
            controller._stable_learned_current_inputs(service),
            (1900.0, 230.0, "L1", 100.0, 20.0),
        )
        self.assertFalse(controller._stable_learned_current_state(SimpleNamespace()))
        self.assertEqual(
            controller._raw_stable_learned_current_inputs(SimpleNamespace()),
            (None, None, "L1", None, 21600.0),
        )
        self.assertEqual(
            controller._raw_stable_learned_current_inputs(SimpleNamespace(phase="3P")),
            (None, None, "3P", None, 21600.0),
        )

    def test_charger_readback_contracts_cover_max_age_candidates_and_boundaries(self):
        readback = _RelayChargerReadback

        self.assertIsNone(readback._positive_freshness_candidate(SimpleNamespace(), "_worker_poll_interval_seconds"))
        self.assertIsNone(readback._positive_freshness_candidate(SimpleNamespace(_worker_poll_interval_seconds=0.0), "_worker_poll_interval_seconds"))
        self.assertIsNone(readback._positive_freshness_candidate(SimpleNamespace(_worker_poll_interval_seconds=-1.0), "_worker_poll_interval_seconds"))
        self.assertEqual(
            readback._positive_freshness_candidate(SimpleNamespace(_worker_poll_interval_seconds=1.25), "_worker_poll_interval_seconds"),
            1.25,
        )
        self.assertEqual(readback._charger_state_max_age_seconds(SimpleNamespace()), 2.0)
        self.assertEqual(
            readback._charger_state_max_age_seconds(
                SimpleNamespace(_worker_poll_interval_seconds=0.0, auto_shelly_soft_fail_seconds=10.0)
            ),
            2.0,
        )
        self.assertEqual(
            readback._charger_state_max_age_seconds(
                SimpleNamespace(_worker_poll_interval_seconds=3.0, auto_shelly_soft_fail_seconds=10.0)
            ),
            2.0,
        )
        self.assertEqual(
            readback._charger_state_max_age_seconds(
                SimpleNamespace(_worker_poll_interval_seconds=3.0, auto_shelly_soft_fail_seconds=1.5)
            ),
            1.5,
        )
        self.assertEqual(
            readback._charger_state_max_age_seconds(
                SimpleNamespace(_worker_poll_interval_seconds=3.0, auto_shelly_soft_fail_seconds=0.5)
            ),
            1.0,
        )
        self.assertEqual(
            readback._charger_state_max_age_seconds(
                SimpleNamespace(_worker_poll_interval_seconds=3.0, auto_shelly_soft_fail_seconds=-1.0)
            ),
            2.0,
        )

    def test_charger_readback_contracts_cover_freshness_edges_and_service_clock(self):
        readback = _RelayChargerReadback
        service = SimpleNamespace(
            _charger_backend=object(),
            _time_now=MagicMock(return_value=100.0),
            _worker_poll_interval_seconds=1.0,
            auto_shelly_soft_fail_seconds=10.0,
            _last_charger_state_at=98.0,
            _last_switch_feedback_at=98.0,
        )

        self.assertEqual(readback._fresh_charger_state_timestamp(service, None), 98.0)
        self.assertEqual(readback._fresh_switch_feedback_timestamp(service, None), 98.0)
        service._last_charger_state_at = 97.99
        service._last_switch_feedback_at = 97.99
        self.assertIsNone(readback._fresh_charger_state_timestamp(service, None))
        self.assertIsNone(readback._fresh_switch_feedback_timestamp(service, None))

        service._last_charger_state_at = 99.0
        service._last_switch_feedback_at = 99.0
        service.auto_shelly_soft_fail_seconds = 1.5
        self.assertEqual(readback._fresh_charger_state_timestamp(service, 100.5), 99.0)
        self.assertEqual(readback._fresh_switch_feedback_timestamp(service, 100.5), 99.0)
        self.assertIsNone(readback._fresh_charger_state_timestamp(service, 100.51))
        self.assertIsNone(readback._fresh_switch_feedback_timestamp(service, 100.51))

    def test_charger_readback_contracts_missing_readback_fields_are_unknown_not_errors(self):
        readback = _RelayChargerReadback
        service = SimpleNamespace(
            _charger_backend=object(),
            _last_charger_state_at=100.0,
            _last_switch_feedback_at=100.0,
            _worker_poll_interval_seconds=1.0,
            auto_shelly_soft_fail_seconds=10.0,
        )

        self.assertIsNone(readback._fresh_switch_feedback_closed(service, 100.0))
        self.assertIsNone(readback._fresh_switch_interlock_ok(service, 100.0))
        self.assertIsNone(readback._fresh_charger_enabled_readback(service, 100.0))
        self.assertIsNone(readback._fresh_charger_state_timestamp(SimpleNamespace(_charger_backend=object()), 100.0))
        self.assertIsNone(readback._fresh_switch_feedback_timestamp(SimpleNamespace(), 100.0))
        self.assertIsNone(readback._optional_bool_readback(SimpleNamespace(), "_last_charger_state_enabled"))
        self.assertIsNone(readback._optional_bool_readback(SimpleNamespace(_last_charger_state_enabled=None), "_last_charger_state_enabled"))
        self.assertTrue(readback._optional_bool_readback(SimpleNamespace(_last_charger_state_enabled=1), "_last_charger_state_enabled"))
        self.assertFalse(readback._optional_bool_readback(SimpleNamespace(_last_charger_state_enabled=0), "_last_charger_state_enabled"))

    def test_charger_readback_contracts_float_values_are_clamped_to_zero_not_one(self):
        readback = _RelayChargerReadback
        service = SimpleNamespace(
            _charger_backend=object(),
            _last_charger_state_at=100.0,
            _worker_poll_interval_seconds=1.0,
            auto_shelly_soft_fail_seconds=10.0,
            _last_charger_state_power_w=0.25,
            _last_charger_state_actual_current_amps=0.25,
            _last_charger_state_energy_kwh=0.25,
        )

        self.assertEqual(readback._fresh_charger_power_readback(service, 100.0), 0.25)
        self.assertEqual(readback._fresh_charger_actual_current_readback(service, 100.0), 0.25)
        self.assertEqual(readback._fresh_charger_energy_readback(service, 100.0), 0.25)
        service._last_charger_state_power_w = -5.0
        service._last_charger_state_actual_current_amps = -5.0
        service._last_charger_state_energy_kwh = -5.0
        self.assertEqual(readback._fresh_charger_power_readback(service, 100.0), 0.0)
        self.assertEqual(readback._fresh_charger_actual_current_readback(service, 100.0), 0.0)
        self.assertEqual(readback._fresh_charger_energy_readback(service, 100.0), 0.0)

    def test_charger_readback_contracts_tokenize_all_status_separators(self):
        controller = UpdateCycleController(SimpleNamespace(), _phase_values, lambda reason: {"init": 0}.get(reason, 99))
        readback = _RelayChargerReadback

        self.assertEqual(
            readback._charger_text_tokens("  A-B_C/D.E,F;G:H  "),
            {"a", "b", "c", "d", "e", "f", "g", "h"},
        )
        self.assertEqual(readback._charger_text_tokens(None), set())
        self.assertEqual(readback._charger_text_tokens(" READY "), {"ready"})

        service = SimpleNamespace(
            learned_charge_power_state="stable",
            learned_charge_power_watts=1900.0,
            learned_charge_power_voltage=230.0,
            learned_charge_power_phase="L1",
            learned_charge_power_updated_at=100.0,
            auto_learn_charge_power_max_age_seconds=20.0,
            phase="P1",
            voltage_mode="phase",
        )
        self.assertEqual(controller._positive_learned_scalar(0.1), 0.1)
        self.assertIsNone(controller._positive_learned_scalar(None))
        self.assertIsNone(controller._positive_learned_scalar(0.0))
        self.assertEqual(controller._learned_phase_and_timestamp("L1", 2.5), ("L1", 2.5))
        self.assertIsNone(controller._learned_phase_and_timestamp(None, 2.5))
        self.assertIsNone(controller._learned_phase_and_timestamp("L1", None))
        self.assertFalse(controller._learned_current_target_stale(120.0, 100.0, 20.0))
        self.assertTrue(controller._learned_current_target_stale(120.001, 100.0, 20.0))
        self.assertTrue(controller._learned_current_target_stale(100.6, 100.0, 0.5))
        self.assertFalse(controller._learned_current_target_stale(500.0, 100.0, 0.0))
        self.assertFalse(controller._learned_current_target_stale(500.0, 100.0, None))
        self.assertEqual(controller._learned_phase_voltage(service, "L1", 230.0), 230.0)
        self.assertEqual(controller._learned_phase_voltage(service, "3P", 400.0), 400.0)
        self.assertEqual(controller._learned_phase_voltage(SimpleNamespace(), "3P", 400.0), 400.0)
        service.voltage_mode = "line"
        self.assertAlmostEqual(controller._learned_phase_voltage(service, "3P", 400.0), 400.0 / math.sqrt(3.0))
        self.assertEqual(controller._learned_phase_voltage(service, "3P", 0.0), 0.0)
        self.assertAlmostEqual(controller._learned_phase_voltage(service, "3P", 1.0), 1.0 / math.sqrt(3.0))
        self.assertEqual(controller._rounded_learned_current_target(1900.0, 230.0, 1.0), 8.0)
        self.assertEqual(controller._rounded_learned_current_target(3000.0, 230.0, 3.0), 4.0)
        self.assertEqual(controller._rounded_learned_current_target(1.0, 0.5, 1.0), 2.0)
        self.assertIsNone(controller._rounded_learned_current_target(1900.0, 0.0, 1.0))
        self.assertIsNone(controller._rounded_learned_current_target(1900.0, 230.0, 0.0))

        service.learned_charge_power_phase = ""
        self.assertIsNone(controller._raw_stable_learned_current_inputs(service)[2])
        self.assertIsNone(controller._stable_learned_current_inputs(service))
        service.learned_charge_power_state = "learning"
        self.assertFalse(controller._stable_learned_current_state(service))
        self.assertIsNone(controller._stable_learned_current_inputs(service))

    def test_charger_current_contracts_cover_target_selection_reset_and_known_target(self):
        charger_backend = SimpleNamespace(set_current=MagicMock())
        controller = UpdateCycleController(SimpleNamespace(), _phase_values, lambda reason: {"init": 0}.get(reason, 99))
        service = SimpleNamespace(
            _charger_backend=charger_backend,
            virtual_mode=1,
            virtual_set_current=9.0,
            min_current=6.0,
            max_current=16.0,
            auto_month_windows={},
            auto_scheduled_enabled_days="Mon,Tue,Wed,Thu,Fri",
            auto_scheduled_night_start_delay_seconds=3600.0,
            auto_scheduled_latest_end_time="04:30",
            auto_scheduled_night_current_amps=0.0,
            learned_charge_power_state="unknown",
            learned_charge_power_watts=None,
            learned_charge_power_voltage=None,
            learned_charge_power_phase=None,
            learned_charge_power_updated_at=None,
            auto_learn_charge_power_max_age_seconds=21600.0,
            _charger_target_current_amps=9.0,
            _charger_target_current_applied_at=50.0,
        )

        self.assertFalse(controller._charger_current_target_allowed(service, False, True))
        self.assertFalse(controller._charger_current_target_allowed(service, True, False))
        self.assertTrue(controller._charger_current_target_allowed(service, True, True))
        service._charger_backend = None
        self.assertFalse(controller._charger_current_target_allowed(service, True, True))
        service._charger_backend = charger_backend
        self.assertFalse(controller._scheduled_night_charge_active(service, 100.0))
        self.assertEqual(controller._scheduled_night_current_amps(service), 16.0)
        self.assertEqual(controller._scheduled_night_current_amps(SimpleNamespace(max_current=12.0)), 12.0)
        self.assertIsNone(controller._scheduled_night_current_amps(SimpleNamespace()))
        service.auto_scheduled_night_current_amps = 1.0
        self.assertEqual(controller._scheduled_night_current_amps(service), 1.0)
        service.auto_scheduled_night_current_amps = 13.0
        self.assertEqual(controller._scheduled_night_current_amps(service), 13.0)
        self.assertEqual(controller._charger_current_target_amps(service, True, 100.0, True), 9.0)
        self.assertTrue(controller._charger_current_reset_needed(False, True))
        self.assertTrue(controller._charger_current_reset_needed(True, False))
        self.assertFalse(controller._charger_current_reset_needed(True, True))
        self.assertTrue(controller._charger_target_unchanged(9.0, 9.009))
        self.assertFalse(controller._charger_target_unchanged(0.0, 0.01))
        self.assertFalse(controller._charger_target_unchanged(9.0, 9.02))
        self.assertEqual(controller._known_charger_current_target(9.0), 9.0)
        with self.assertRaises(TypeError) as captured:
            controller._known_charger_current_target(None)
        self.assertEqual(str(captured.exception), "last charger current target must be available when unchanged")

        controller._reset_charger_current_target(service)
        self.assertIsNone(service._charger_target_current_amps)
        self.assertIsNone(service._charger_target_current_applied_at)

    def test_scheduled_night_charge_active_passes_explicit_schedule_contract(self):
        controller = UpdateCycleController(SimpleNamespace(), _phase_values, lambda reason: {"init": 0}.get(reason, 99))
        service = SimpleNamespace(
            virtual_mode=2,
            auto_schedule_timezone="Europe/Berlin",
            auto_month_windows={4: ((7, 30), (19, 30))},
            auto_scheduled_enabled_days="Sat,Sun",
            auto_scheduled_night_start_delay_seconds=1800.0,
            auto_scheduled_latest_end_time="05:45",
        )
        local_marker = object()
        snapshot = SimpleNamespace(night_boost_active=True)

        with (
            patch(
                "venus_evcharger.update.relay_charger_current_targets.local_datetime_from_timestamp",
                return_value=local_marker,
            ) as local_dt,
            patch(
                "venus_evcharger.update.relay_charger_current_targets.scheduled_mode_snapshot",
                return_value=snapshot,
            ) as schedule_snapshot,
        ):
            self.assertTrue(controller._scheduled_night_charge_active(service, 1234.5))

        local_dt.assert_called_once_with(1234.5, "Europe/Berlin")
        schedule_snapshot.assert_called_once_with(
            local_marker,
            {4: ((7, 30), (19, 30))},
            "Sat,Sun",
            delay_seconds=1800.0,
            latest_end_time="05:45",
        )

        service.virtual_mode = 1
        with patch("venus_evcharger.update.relay_charger_current_targets.scheduled_mode_snapshot") as schedule_snapshot:
            self.assertFalse(controller._scheduled_night_charge_active(service, 1234.5))
        schedule_snapshot.assert_not_called()

        default_service = SimpleNamespace(virtual_mode=2)
        default_marker = object()
        default_snapshot = SimpleNamespace(night_boost_active=False)
        with (
            patch(
                "venus_evcharger.update.relay_charger_current_targets.local_datetime_from_timestamp",
                return_value=default_marker,
            ) as local_dt,
            patch(
                "venus_evcharger.update.relay_charger_current_targets.scheduled_mode_snapshot",
                return_value=default_snapshot,
            ) as schedule_snapshot,
        ):
            self.assertFalse(controller._scheduled_night_charge_active(default_service, 321.0))
        local_dt.assert_called_once_with(321.0, "UTC")
        schedule_snapshot.assert_called_once_with(
            default_marker,
            {},
            "Mon,Tue,Wed,Thu,Fri",
            delay_seconds=3600.0,
            latest_end_time="04:30",
        )

    def test_charger_current_contracts_cover_scheduled_and_learned_target_precedence(self):
        charger_backend = SimpleNamespace(set_current=MagicMock())
        controller = UpdateCycleController(SimpleNamespace(), _phase_values, lambda reason: {"init": 0}.get(reason, 99))
        service = SimpleNamespace(
            _charger_backend=charger_backend,
            virtual_set_current=9.0,
            min_current=6.0,
            max_current=16.0,
            auto_scheduled_night_current_amps=20.0,
            learned_charge_power_state="stable",
            learned_charge_power_watts=6900.0,
            learned_charge_power_voltage=400.0,
            learned_charge_power_phase="3P",
            learned_charge_power_updated_at=100.0,
            auto_learn_charge_power_max_age_seconds=20.0,
            voltage_mode="line",
        )

        self.assertEqual(controller._derived_learned_current_target(service, 110.0), 10.0)
        service.learned_charge_power_watts = 20000.0
        self.assertEqual(controller._derived_learned_current_target(service, 110.0), 16.0)
        service.learned_charge_power_watts = 6900.0
        with patch.object(UpdateCycleController, "_scheduled_night_charge_active", return_value=True):
            self.assertEqual(controller._charger_current_target_amps(service, True, 110.0, True), 16.0)
        with patch.object(UpdateCycleController, "_scheduled_night_charge_active", return_value=False):
            self.assertEqual(controller._charger_current_target_amps(service, True, 110.0, True), 10.0)

        service.learned_charge_power_updated_at = 80.0
        with patch.object(UpdateCycleController, "_scheduled_night_charge_active", return_value=False):
            self.assertEqual(controller._charger_current_target_amps(service, True, 110.0, True), 9.0)
        service.virtual_set_current = 20.0
        with patch.object(UpdateCycleController, "_scheduled_night_charge_active", return_value=False):
            self.assertEqual(controller._charger_current_target_amps(service, True, 110.0, True), 16.0)
        del service.virtual_set_current
        with patch.object(UpdateCycleController, "_scheduled_night_charge_active", return_value=False):
            self.assertIsNone(controller._charger_current_target_amps(service, True, 110.0, True))

    def test_charger_current_contracts_cover_success_failure_and_enable_targets(self):
        charger_backend = SimpleNamespace(set_current=MagicMock(), set_enabled=MagicMock())
        controller = UpdateCycleController(SimpleNamespace(), _phase_values, lambda reason: {"init": 0}.get(reason, 99))
        service = SimpleNamespace(
            _charger_backend=charger_backend,
            _charger_retry_until=None,
            _source_retry_after={},
            _mark_failure=MagicMock(),
            _mark_recovery=MagicMock(),
            _warning_throttled=MagicMock(),
            auto_shelly_soft_fail_seconds=10.0,
            virtual_set_current=10.0,
            min_current=6.0,
            max_current=16.0,
            learned_charge_power_state="unknown",
            learned_charge_power_watts=None,
            learned_charge_power_voltage=None,
            learned_charge_power_phase=None,
            learned_charge_power_updated_at=None,
            auto_learn_charge_power_max_age_seconds=21600.0,
            _charger_target_current_amps=None,
            _charger_target_current_applied_at=None,
            _queue_relay_command=MagicMock(),
        )

        self.assertEqual(controller._apply_new_charger_current_target(service, charger_backend, 10.0, 100.0, None), 10.0)
        charger_backend.set_current.assert_called_once_with(10.0)
        self.assertEqual(service._charger_target_current_amps, 10.0)
        self.assertEqual(service._charger_target_current_applied_at, 100.0)
        service._mark_recovery.assert_any_call("charger", "Charger current writes recovered")

        service._charger_retry_until = 120.0
        charger_backend.set_current.reset_mock()
        self.assertEqual(controller._apply_new_charger_current_target(service, charger_backend, 12.0, 110.0, 10.0), 10.0)
        charger_backend.set_current.assert_not_called()

        service._charger_retry_until = None
        charger_backend.set_current.side_effect = RuntimeError("boom")
        self.assertEqual(controller._apply_new_charger_current_target(service, charger_backend, 12.0, 130.0, 10.0), 10.0)
        service._mark_failure.assert_called_once_with("charger")
        service._warning_throttled.assert_called_once()

        charger_backend.set_enabled.reset_mock()
        service._charger_retry_until = 120.0
        self.assertFalse(controller._apply_enabled_target(service, True, 110.0))
        charger_backend.set_enabled.assert_not_called()
        service._charger_retry_until = None
        self.assertTrue(controller._apply_enabled_target(service, True, 130.0))
        charger_backend.set_enabled.assert_called_once_with(True)
        service._mark_recovery.assert_any_call("charger", "Charger enable writes recovered")
        service._charger_backend = None
        self.assertTrue(controller._apply_enabled_target(service, False, 140.0))
        service._queue_relay_command.assert_called_once_with(False, 140.0)

        no_last_target_service = SimpleNamespace(
            _charger_backend=SimpleNamespace(set_current=MagicMock()),
            _charger_retry_until=None,
            _source_retry_after={},
            _mark_failure=MagicMock(),
            _mark_recovery=MagicMock(),
            _warning_throttled=MagicMock(),
            auto_shelly_soft_fail_seconds=10.0,
            virtual_set_current=7.0,
            min_current=6.0,
            max_current=16.0,
            learned_charge_power_state="unknown",
            learned_charge_power_watts=None,
            learned_charge_power_voltage=None,
            learned_charge_power_phase=None,
            learned_charge_power_updated_at=None,
            auto_learn_charge_power_max_age_seconds=21600.0,
        )
        self.assertEqual(controller.apply_charger_current_target(no_last_target_service, True, 150.0, True), 7.0)
        no_last_target_service._charger_backend.set_current.assert_called_once_with(7.0)

    def test_charger_current_failure_contract_records_transport_metadata_and_warning(self):
        controller = UpdateCycleController(SimpleNamespace(), _phase_values, lambda reason: {"init": 0}.get(reason, 99))
        error = ModbusSlaveOfflineError("Modbus slave 1 did not respond")
        service = SimpleNamespace(
            _mark_failure=MagicMock(),
            _warning_throttled=MagicMock(),
            _source_retry_after={},
            auto_shelly_soft_fail_seconds=10.0,
            auto_dbus_backoff_base_seconds=5.0,
            _worker_poll_interval_seconds=0.5,
        )

        controller._handle_charger_current_target_failure(service, error, 100.0)

        self.assertEqual(service._last_charger_transport_reason, "offline")
        self.assertEqual(service._last_charger_transport_source, "current")
        self.assertIn("Modbus slave 1", service._last_charger_transport_detail)
        self.assertEqual(service._last_charger_transport_at, 100.0)
        self.assertEqual(service._charger_retry_reason, "offline")
        self.assertEqual(service._charger_retry_source, "current")
        self.assertEqual(service._charger_retry_until, 120.0)
        self.assertEqual(service._source_retry_after["charger"], 120.0)
        service._mark_failure.assert_called_once_with("charger")
        service._warning_throttled.assert_called_once_with(
            "charger-current-failed",
            10.0,
            "Charger current request failed: %s",
            error,
            exc_info=error,
        )

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
            _phase_switch_pending_selection="P1_P2",
            _phase_switch_state="stabilizing",
            _phase_switch_requested_at=90.0,
            _phase_switch_stable_until=95.0,
            _phase_switch_mismatch_active=True,
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
        service._save_runtime_state.assert_called_once_with()
        self.assertIsNone(service._phase_switch_pending_selection)
        self.assertIsNone(service._phase_switch_state)
        self.assertIsNone(service._phase_switch_requested_at)
        self.assertIsNone(service._phase_switch_stable_until)
        self.assertIs(service._phase_switch_mismatch_active, False)

        service._phase_switch_pending_selection = "P1_P2"
        service._phase_switch_state = "stabilizing"
        service._phase_switch_requested_at = 90.0
        service._phase_switch_stable_until = 95.0
        service._phase_switch_mismatch_active = True
        service._phase_switch_resume_relay = True
        service._save_runtime_state.reset_mock()
        self.assertEqual(
            controller._resume_after_phase_switch_pause(service, False, 12.0, 3.0, True, 101.0, True),
            (False, 12.0, 3.0, True),
        )
        self.assertTrue(service._ignore_min_offtime_once)
        service._save_runtime_state.assert_called_once_with()
        self.assertIsNone(service._phase_switch_pending_selection)

        service._phase_switch_pending_selection = "P1_P2"
        service._phase_switch_state = "stabilizing"
        service._phase_switch_resume_relay = True
        service._phase_switch_resume_relay = True
        service._save_runtime_state.reset_mock()
        resume_error = RuntimeError("boom")
        with patch.object(controller, "_apply_enabled_target", side_effect=resume_error):
            self.assertEqual(
                controller._resume_after_phase_switch_pause(service, False, 12.0, 3.0, True, 102.0, False),
                (False, 12.0, 3.0, True),
            )
        service._mark_failure.assert_called_with("shelly")
        service._warning_throttled.assert_called_with(
            "phase-switch-resume-failed",
            10.0,
            "Failed to resume %s after phase switch: %s",
            "Shelly relay",
            resume_error,
            exc_info=resume_error,
        )
        service._save_runtime_state.assert_called_once_with()

        service._phase_switch_pending_selection = "P1_P2"
        service._phase_switch_state = "stabilizing"
        service._phase_switch_resume_relay = True
        service._save_runtime_state.reset_mock()
        with patch.object(controller, "_apply_enabled_target", return_value=False):
            self.assertEqual(
                controller._resume_after_phase_switch_pause(service, False, 12.0, 3.0, True, 103.0, False),
                (False, 12.0, 3.0, True),
            )
        service._save_runtime_state.assert_called_once_with()

        service._phase_switch_pending_selection = "P1_P2"
        service._phase_switch_state = "stabilizing"
        service._phase_switch_resume_relay = True
        service._save_runtime_state.reset_mock()
        service._publish_local_pm_status.reset_mock()
        with patch.object(controller, "_apply_enabled_target", return_value=True):
            self.assertEqual(
                controller._resume_after_phase_switch_pause(service, False, 12.0, 3.0, True, 104.0, False),
                (True, 0.0, 0.0, False),
            )
        service._publish_local_pm_status.assert_called_once_with(True, 104.0)
        service._save_runtime_state.assert_called_once_with()

        missing_resume_flag_service = _auto_phase_service(
            _save_runtime_state=MagicMock(),
            _mark_failure=MagicMock(),
            _warning_throttled=MagicMock(),
        )
        delattr(missing_resume_flag_service, "_phase_switch_resume_relay")
        with patch.object(controller, "_apply_enabled_target", side_effect=AssertionError("resume should stay idle")):
            self.assertEqual(
                controller._resume_after_phase_switch_pause(
                    missing_resume_flag_service,
                    False,
                    44.0,
                    2.0,
                    True,
                    105.0,
                    False,
                ),
                (False, 44.0, 2.0, True),
            )
        missing_resume_flag_service._save_runtime_state.assert_called_once_with()

        service._phase_switch_resume_relay = True
        service._mark_failure.reset_mock()
        service._warning_throttled.reset_mock()
        with (
            patch.object(controller, "_enable_control_source_key", wraps=controller._enable_control_source_key) as source,
            patch.object(controller, "_enable_control_label", wraps=controller._enable_control_label) as label,
            patch.object(controller, "_apply_enabled_target", side_effect=RuntimeError("resume failed")),
        ):
            self.assertEqual(
                controller._resume_after_phase_switch_pause(service, False, 12.0, 3.0, True, 106.0, False),
                (False, 12.0, 3.0, True),
            )
        source.assert_called_once_with(service)
        label.assert_called_once_with(service)
        service._mark_failure.assert_called_with("shelly")

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
        service.phase_switch_pause_seconds = 0.0
        service.phase_switch_stabilization_seconds = 0.0
        self.assertEqual(controller._phase_switch_pause_seconds(service), 0.0)
        self.assertEqual(controller._phase_switch_stabilization_seconds(service), 0.0)
        self.assertEqual(controller._pending_phase_switch_selection(service), "P1_P2")
        service.phase_switch_pause_seconds = None
        service.phase_switch_stabilization_seconds = None
        self.assertEqual(controller._phase_switch_pause_seconds(service), 1.0)
        self.assertEqual(controller._phase_switch_stabilization_seconds(service), 2.0)
        self.assertEqual(controller._phase_switch_pause_seconds(SimpleNamespace()), 1.0)
        self.assertEqual(controller._phase_switch_stabilization_seconds(SimpleNamespace()), 2.0)
        service._phase_switch_pending_selection = None
        self.assertIsNone(controller._pending_phase_switch_selection(service))
        service._phase_switch_pending_selection = "bad-selection"
        self.assertEqual(controller._pending_phase_switch_selection(service), "P1")
        service._phase_switch_pending_selection = "3P"
        self.assertEqual(controller._pending_phase_switch_selection(service), "P1_P2_P3")
        self.assertEqual(controller._observed_phase_selection_from_pm_status({"_phase_selection": "3P"}), "P1_P2_P3")
        self.assertEqual(controller._observed_phase_selection_from_pm_status({"_phase_selection": "bad"}), "P1")
        self.assertIsNone(controller._observed_phase_selection_from_pm_status({}))
        self.assertIsNone(controller._observed_phase_selection(service, {}, 100.0))
        service._last_charger_state_at = 100.0
        self.assertEqual(controller._observed_phase_selection(service, {}, 100.0), "P1_P2")
        service._last_charger_state_phase_selection = "bad"
        self.assertEqual(controller._observed_phase_selection(service, {}, 100.0), "P1")
        service._last_charger_state_phase_selection = None
        self.assertIsNone(controller._observed_phase_selection(service, {}, 100.0))
        delattr(service, "_last_charger_state_phase_selection")
        self.assertIsNone(controller._observed_phase_selection(service, {}, 100.0))
        service._last_charger_state_phase_selection = "P1_P2"
        service.phase_switch_pause_seconds = 1.0
        service.phase_switch_stabilization_seconds = 2.0
        self.assertEqual(controller._phase_switch_verification_deadline(service), 93.0)
        self.assertFalse(controller._phase_switch_verification_expired(service, 92.9))
        self.assertTrue(controller._phase_switch_verification_expired(service, 93.0))
        service.auto_shelly_soft_fail_seconds = 7.0
        self.assertEqual(controller._phase_switch_verification_deadline(service), 100.0)
        service._phase_switch_stable_until = 95.0
        self.assertEqual(controller._phase_switch_verification_deadline(service), 102.0)
        self.assertFalse(controller._phase_switch_verification_expired(service, 101.9))
        self.assertTrue(controller._phase_switch_verification_expired(service, 102.0))
        service._phase_switch_stable_until = None
        service._phase_switch_requested_at = None
        self.assertIsNone(controller._phase_switch_verification_deadline(service))

        service._phase_switch_resume_relay = True
        service._phase_switch_mismatch_active = True
        controller._clear_phase_switch_state(service)
        self.assertIsNone(service._phase_switch_pending_selection)
        self.assertIsNone(service._phase_switch_state)
        self.assertIsNone(service._phase_switch_requested_at)
        self.assertIsNone(service._phase_switch_stable_until)
        self.assertIs(service._phase_switch_resume_relay, False)
        self.assertIs(service._phase_switch_mismatch_active, False)

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

        with patch.object(controller, "_phase_switch_lockout_active", wraps=controller._phase_switch_lockout_active) as active:
            controller._report_phase_switch_mismatch(service, "P1_P2", None, 100.0)

        self.assertEqual(service._phase_switch_mismatch_counts, {"P1_P2": 1})
        self.assertEqual(service._phase_switch_last_mismatch_selection, "P1_P2")
        self.assertEqual(service._phase_switch_last_mismatch_at, 100.0)
        self.assertEqual(service._phase_switch_lockout_selection, "P1_P2")
        self.assertEqual(service._phase_switch_lockout_reason, "mismatch-threshold")
        self.assertEqual(service._phase_switch_lockout_at, 100.0)
        self.assertEqual(service._phase_switch_lockout_until, 130.0)
        active.assert_called_once_with(service, 100.0, "P1_P2")
        service._mark_failure.assert_called_once_with("shelly")
        service._set_health.assert_called_once_with("phase-switch-mismatch", cached=False)
        warning_args = service._warning_throttled.call_args.args
        self.assertEqual(warning_args[0], "phase-switch-mismatch")
        self.assertEqual(warning_args[1], 10.0)
        self.assertEqual(
            warning_args[2],
            "Phase selection %s did not confirm after %.1fs (observed=%s count=%s lockout=%s)",
        )
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

        no_lockout_service = _auto_phase_service(
            auto_policy=SimpleNamespace(
                phase=SimpleNamespace(
                    mismatch_retry_seconds=60.0,
                    mismatch_lockout_count=0,
                    mismatch_lockout_seconds=30.0,
                )
            ),
            _phase_switch_requested_at=None,
            auto_shelly_soft_fail_seconds=0.5,
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
        controller._report_phase_switch_mismatch(no_lockout_service, "P1_P2", "P1", 111.0)
        self.assertEqual(no_lockout_service._phase_switch_mismatch_counts, {"P1_P2": 1})
        self.assertIsNone(no_lockout_service._phase_switch_lockout_selection)
        no_lockout_service._mark_failure.assert_called_once_with("shelly")
        no_lockout_service._set_health.assert_called_once_with("phase-switch-mismatch", cached=False)
        self.assertEqual(
            no_lockout_service._warning_throttled.call_args.args,
            (
                "phase-switch-mismatch",
                1.0,
                "Phase selection %s did not confirm after %.1fs (observed=%s count=%s lockout=%s)",
                "P1_P2",
                0.5,
                "P1",
                1,
                0,
            ),
        )

        defaulted_service = _auto_phase_service(
            auto_policy=SimpleNamespace(
                phase=SimpleNamespace(
                    mismatch_retry_seconds=60.0,
                    mismatch_lockout_count=0,
                    mismatch_lockout_seconds=30.0,
                )
            ),
            _set_health=MagicMock(),
            _mark_failure=MagicMock(),
            _warning_throttled=MagicMock(),
        )
        delattr(defaulted_service, "_phase_switch_requested_at")
        delattr(defaulted_service, "auto_shelly_soft_fail_seconds")
        controller._report_phase_switch_mismatch(defaulted_service, "P1_P2", "P1", 112.0)
        self.assertEqual(
            defaulted_service._warning_throttled.call_args.args,
            (
                "phase-switch-mismatch",
                10.0,
                "Phase selection %s did not confirm after %.1fs (observed=%s count=%s lockout=%s)",
                "P1_P2",
                10.0,
                "P1",
                1,
                0,
            ),
        )

        future_requested_service = _auto_phase_service(
            auto_policy=SimpleNamespace(
                phase=SimpleNamespace(
                    mismatch_retry_seconds=60.0,
                    mismatch_lockout_count=0,
                    mismatch_lockout_seconds=30.0,
                )
            ),
            _phase_switch_requested_at=120.0,
            _set_health=MagicMock(),
            _mark_failure=MagicMock(),
            _warning_throttled=MagicMock(),
        )
        controller._report_phase_switch_mismatch(future_requested_service, "P1_P2", "P1", 112.0)
        self.assertEqual(future_requested_service._warning_throttled.call_args.args[4], 0.0)

    def test_phase_switch_runtime_contracts_cover_waiting_gates_and_abort_warning(self):
        service = _auto_phase_service(
            _phase_switch_pending_selection="P1_P2",
            _phase_switch_state="waiting-relay-off",
            _phase_switch_requested_at=100.0,
            phase_switch_pause_seconds=2.0,
            _save_runtime_state=MagicMock(),
            _mark_failure=MagicMock(),
            _warning_throttled=MagicMock(),
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        service._peek_pending_relay_command = MagicMock(return_value=(None, None))
        self.assertFalse(controller._phase_switch_waiting_ready(service, True, True, 103.0))
        self.assertFalse(controller._phase_switch_waiting_ready(service, False, False, 103.0))
        service._peek_pending_relay_command = MagicMock(return_value=(False, 101.0))
        self.assertFalse(controller._phase_switch_waiting_ready(service, False, True, 103.0))
        service._peek_pending_relay_command = MagicMock(return_value=(None, None))
        self.assertFalse(controller._phase_switch_waiting_ready(service, False, True, 101.9))
        self.assertTrue(controller._phase_switch_waiting_ready(service, False, True, 102.0))
        service._phase_switch_requested_at = None
        self.assertTrue(controller._phase_switch_waiting_ready(service, False, True, 100.0))

        error = RuntimeError("apply failed")
        result = controller._abort_pending_phase_switch(
            service,
            True,
            1234.0,
            5.6,
            True,
            105.0,
            False,
            error,
        )
        self.assertEqual(result, (True, 1234.0, 5.6, True))
        self.assertEqual(service.requested_phase_selection, "P1")
        service._mark_failure.assert_called_once_with("shelly")
        service._warning_throttled.assert_called_once_with(
            "phase-switch-apply-failed",
            10.0,
            "Failed to apply phase selection %s: %s",
            "P1_P2",
            error,
            exc_info=error,
        )
        service._save_runtime_state.assert_called_once_with()

    def test_phase_switch_runtime_recovery_passes_abort_and_mismatch_resume_arguments(self):
        service = _auto_phase_service(
            active_phase_selection="P1_P2",
            requested_phase_selection="P1",
            _phase_switch_pending_selection="P1_P2",
            _phase_switch_state="waiting-relay-off",
            _phase_switch_resume_relay=True,
            _mark_failure=MagicMock(),
            _warning_throttled=MagicMock(),
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        error = RuntimeError("apply failed")
        with patch.object(controller, "_resume_after_phase_switch_pause", return_value=(False, 1.0, 2.0, False)) as resume:
            self.assertEqual(
                controller._abort_pending_phase_switch(
                    service,
                    True,
                    1234.0,
                    5.6,
                    True,
                    105.0,
                    True,
                    error,
                ),
                (False, 1.0, 2.0, False),
            )
        self.assertEqual(service.requested_phase_selection, "P1_P2")
        service._mark_failure.assert_called_once_with("shelly")
        service._warning_throttled.assert_called_once_with(
            "phase-switch-apply-failed",
            10.0,
            "Failed to apply phase selection %s: %s",
            "P1_P2",
            error,
            exc_info=error,
        )
        resume.assert_called_once_with(service, True, 1234.0, 5.6, True, 105.0, True)

        service.requested_phase_selection = "P1_P2"
        service.active_phase_selection = "P1"
        service._auto_phase_target_candidate = "P1_P2"
        service._auto_phase_target_since = 90.0
        with patch.object(controller, "_resume_after_phase_switch_pause", return_value=(True, 7.0, 8.0, True)) as resume:
            with patch.object(
                controller,
                "_phase_switch_fallback_selection",
                wraps=controller._phase_switch_fallback_selection,
            ) as fallback:
                self.assertEqual(
                    controller._abort_phase_switch_after_mismatch(
                        service,
                        "P1_P2",
                        "P1",
                        False,
                        900.0,
                        4.5,
                        False,
                        106.0,
                        False,
                    ),
                    (True, 7.0, 8.0, True),
                )
        fallback.assert_called_once_with(service, "P1", "P1_P2")
        self.assertEqual(service.requested_phase_selection, "P1")
        self.assertEqual(service.active_phase_selection, "P1")
        self.assertIsNone(service._auto_phase_target_candidate)
        self.assertIsNone(service._auto_phase_target_since)
        resume.assert_called_once_with(service, False, 900.0, 4.5, False, 106.0, False)

        fallback_service = _auto_phase_service(
            requested_phase_selection="P1_P2",
            _phase_switch_pending_selection="P1",
            _mark_failure=MagicMock(),
            _warning_throttled=MagicMock(),
        )
        delattr(fallback_service, "active_phase_selection")
        with patch.object(controller, "_resume_after_phase_switch_pause", return_value=(False, 0.0, 0.0, False)) as resume:
            self.assertEqual(
                controller._abort_pending_phase_switch(
                    fallback_service,
                    False,
                    100.0,
                    1.0,
                    False,
                    107.0,
                    False,
                    RuntimeError("apply failed"),
                ),
                (False, 0.0, 0.0, False),
            )
        self.assertEqual(fallback_service.requested_phase_selection, "P1_P2")
        resume.assert_called_once_with(fallback_service, False, 100.0, 1.0, False, 107.0, False)

        default_fallback_service = _auto_phase_service(
            _mark_failure=MagicMock(),
            _warning_throttled=MagicMock(),
        )
        delattr(default_fallback_service, "active_phase_selection")
        delattr(default_fallback_service, "requested_phase_selection")
        delattr(default_fallback_service, "_phase_switch_pending_selection")
        with patch.object(controller, "_resume_after_phase_switch_pause", return_value=(False, 0.0, 0.0, False)) as resume:
            self.assertEqual(
                controller._abort_pending_phase_switch(
                    default_fallback_service,
                    False,
                    101.0,
                    1.1,
                    False,
                    108.0,
                    False,
                    RuntimeError("apply failed"),
                ),
                (False, 0.0, 0.0, False),
            )
        self.assertEqual(default_fallback_service.requested_phase_selection, "P1")
        self.assertEqual(default_fallback_service._warning_throttled.call_args.args[3], None)
        resume.assert_called_once_with(default_fallback_service, False, 101.0, 1.1, False, 108.0, False)

    def test_phase_switch_runtime_contracts_cover_orchestration_and_stabilization_edges(self):
        service = _auto_phase_service(
            _phase_switch_pending_selection=None,
            _phase_switch_state="waiting-relay-off",
            _save_runtime_state=MagicMock(),
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        self.assertEqual(
            controller.orchestrate_pending_phase_switch({}, True, 2000.0, 8.0, True, 100.0, False),
            (True, 2000.0, 8.0, True, None),
        )
        self.assertIsNone(service._phase_switch_state)

        service._phase_switch_pending_selection = "P1_P2"
        service._phase_switch_state = controller.PHASE_SWITCH_WAITING_STATE
        service._phase_switch_requested_at = 90.0
        service._peek_pending_relay_command = MagicMock(return_value=(None, None))
        service._apply_phase_selection = MagicMock(return_value="P1_P2")
        self.assertEqual(
            controller.orchestrate_pending_phase_switch({}, False, 1200.0, 5.0, True, 100.0, False),
            (False, 0.0, 0.0, False, False),
        )
        service._apply_phase_selection.assert_called_once_with("P1_P2")
        self.assertEqual(service.requested_phase_selection, "P1_P2")
        self.assertEqual(service._phase_switch_state, controller.PHASE_SWITCH_STABILIZING_STATE)
        self.assertEqual(service._phase_switch_stable_until, 102.0)

        service._phase_switch_pending_selection = "P1_P2"
        service._phase_switch_state = controller.PHASE_SWITCH_STABILIZING_STATE
        service._phase_switch_stable_until = 103.0
        self.assertEqual(
            controller._orchestrate_stabilizing_phase_switch(
                service,
                "P1_P2",
                {"_phase_selection": "P1"},
                True,
                900.0,
                4.0,
                True,
                102.9,
                False,
            ),
            (False, 0.0, 0.0, False, False),
        )

        service._phase_switch_stable_until = 100.0
        service._phase_switch_requested_at = 90.0
        service.auto_shelly_soft_fail_seconds = 5.0
        self.assertEqual(
            controller._stabilizing_phase_switch_mismatch_result(
                service,
                "P1_P2",
                "P1",
                True,
                900.0,
                4.0,
                True,
                104.9,
                False,
            ),
            (False, 0.0, 0.0, False, False),
        )
        service._set_health = MagicMock()
        service._mark_failure = MagicMock()
        service._warning_throttled = MagicMock()
        result = controller._stabilizing_phase_switch_mismatch_result(
            service,
            "P1_P2",
            "P1",
            True,
            900.0,
            4.0,
            True,
            105.0,
            False,
        )
        self.assertEqual(result, (True, 900.0, 4.0, True, None))
        service._mark_failure.assert_called_once_with("shelly")

        service._phase_switch_pending_selection = "P1_P2"
        service._phase_switch_state = controller.PHASE_SWITCH_STABILIZING_STATE
        service._phase_switch_resume_relay = False
        service._phase_switch_lockout_selection = "P1_P2"
        self.assertEqual(
            controller._complete_stabilized_phase_switch(
                service,
                "P1_P2",
                False,
                0.0,
                0.0,
                False,
                106.0,
                False,
            ),
            (False, 0.0, 0.0, False, None),
        )
        self.assertEqual(service.active_phase_selection, "P1_P2")
        self.assertIsNone(service._phase_switch_lockout_selection)

    def test_phase_switch_runtime_orchestrator_delegates_waiting_and_stabilizing_states(self):
        service = _auto_phase_service(
            _phase_switch_pending_selection="bad",
            _phase_switch_state="waiting-relay-off",
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        with patch.object(
            controller,
            "_orchestrate_waiting_phase_switch",
            return_value=(False, 1.0, 2.0, False, True),
        ) as waiting:
            self.assertEqual(
                controller.orchestrate_pending_phase_switch({"x": 1}, True, 100.0, 6.0, True, 10.0, False),
                (False, 1.0, 2.0, False, True),
            )
        waiting.assert_called_once_with(service, "P1", True, 100.0, 6.0, True, 10.0, False)

        service._phase_switch_pending_selection = "P1_P2"
        service._phase_switch_state = controller.PHASE_SWITCH_STABILIZING_STATE
        with patch.object(
            controller,
            "_orchestrate_stabilizing_phase_switch",
            return_value=(True, 3.0, 4.0, True, None),
        ) as stabilizing:
            self.assertEqual(
                controller.orchestrate_pending_phase_switch({"_phase_selection": "P1"}, False, 200.0, 7.0, False, 11.0, True),
                (True, 3.0, 4.0, True, None),
            )
        stabilizing.assert_called_once_with(
            service,
            "P1_P2",
            {"_phase_selection": "P1"},
            False,
            200.0,
            7.0,
            False,
            11.0,
            True,
        )

    def test_phase_switch_runtime_waiting_state_contracts_cover_ready_apply_and_abort(self):
        service = _auto_phase_service()
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        with patch.object(controller, "_phase_switch_waiting_ready", return_value=False) as ready:
            self.assertEqual(
                controller._orchestrate_waiting_phase_switch(service, "P1_P2", True, 900.0, 4.0, True, 20.0, False),
                (True, 900.0, 4.0, True, False),
            )
        ready.assert_called_once_with(service, True, True, 20.0)

        with (
            patch.object(controller, "_phase_switch_waiting_ready", return_value=True),
            patch.object(
                controller,
                "_apply_pending_phase_selection",
                return_value=(False, 0.0, 0.0, False, False),
            ) as apply,
        ):
            self.assertEqual(
                controller._orchestrate_waiting_phase_switch(service, "P1_P2", False, 901.0, 4.1, True, 21.0, False),
                (False, 0.0, 0.0, False, False),
            )
        apply.assert_called_once_with(service, "P1_P2", 21.0)

        error = RuntimeError("phase failed")
        with (
            patch.object(controller, "_phase_switch_waiting_ready", return_value=True),
            patch.object(controller, "_apply_pending_phase_selection", side_effect=error),
            patch.object(controller, "_abort_pending_phase_switch", return_value=(True, 5.0, 6.0, True)) as abort,
        ):
            self.assertEqual(
                controller._orchestrate_waiting_phase_switch(service, "P1_P2", True, 902.0, 4.2, False, 22.0, True),
                (True, 5.0, 6.0, True, None),
            )
        abort.assert_called_once_with(service, True, 902.0, 4.2, False, 22.0, True, error)

    def test_phase_switch_runtime_stabilizing_state_contracts_cover_all_branches(self):
        service = _auto_phase_service()
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        with (
            patch.object(controller, "_remember_observed_phase_selection", return_value="P1") as observed,
            patch.object(controller, "_phase_switch_still_stabilizing", return_value=True) as still_stabilizing,
        ):
            self.assertEqual(
                controller._orchestrate_stabilizing_phase_switch(
                    service,
                    "P1_P2",
                    {"_phase_selection": "P1"},
                    True,
                    1000.0,
                    5.0,
                    True,
                    30.0,
                    False,
                ),
                (False, 0.0, 0.0, False, False),
            )
        observed.assert_called_once_with(service, {"_phase_selection": "P1"}, 30.0)
        still_stabilizing.assert_called_once_with(service, 30.0)

        with (
            patch.object(controller, "_remember_observed_phase_selection", return_value="P1"),
            patch.object(controller, "_phase_switch_still_stabilizing", return_value=False),
            patch.object(
                controller,
                "_stabilizing_phase_switch_mismatch_result",
                return_value=(False, 1.0, 2.0, False, False),
            ) as mismatch,
        ):
            self.assertEqual(
                controller._orchestrate_stabilizing_phase_switch(
                    service,
                    "P1_P2",
                    {},
                    True,
                    1001.0,
                    5.1,
                    True,
                    31.0,
                    False,
                ),
                (False, 1.0, 2.0, False, False),
            )
        mismatch.assert_called_once_with(service, "P1_P2", "P1", True, 1001.0, 5.1, True, 31.0, False)

        with (
            patch.object(controller, "_remember_observed_phase_selection", return_value="P1_P2"),
            patch.object(controller, "_phase_switch_still_stabilizing", return_value=False),
            patch.object(controller, "_stabilizing_phase_switch_mismatch_result", return_value=None),
            patch.object(controller, "_complete_stabilized_phase_switch", return_value=(True, 3.0, 4.0, True, None)) as complete,
        ):
            self.assertEqual(
                controller._orchestrate_stabilizing_phase_switch(
                    service,
                    "P1_P2",
                    {},
                    False,
                    1002.0,
                    5.2,
                    False,
                    32.0,
                    True,
                ),
                (True, 3.0, 4.0, True, None),
            )
        complete.assert_called_once_with(service, "P1_P2", False, 1002.0, 5.2, False, 32.0, True)

    def test_phase_switch_runtime_helpers_cover_default_and_edge_contracts(self):
        service = _auto_phase_service(
            _phase_switch_stable_until=None,
            _phase_switch_requested_at=10.0,
            auto_shelly_soft_fail_seconds=-5.0,
            phase_switch_pause_seconds=0.0,
            phase_switch_stabilization_seconds=0.0,
            active_phase_selection="P1",
            _phase_switch_lockout_selection="P1_P2",
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        self.assertEqual(controller._phase_switch_verification_deadline(service), 10.0)
        delattr(service, "auto_shelly_soft_fail_seconds")
        service._phase_switch_stable_until = 20.0
        self.assertEqual(controller._phase_switch_verification_deadline(service), 30.0)
        service._phase_switch_stable_until = None
        delattr(service, "_phase_switch_requested_at")
        self.assertIsNone(controller._phase_switch_verification_deadline(service))
        service.auto_shelly_soft_fail_seconds = -5.0
        service._phase_switch_requested_at = 10.0
        self.assertFalse(controller._phase_switch_still_stabilizing(service, 10.0))
        service._phase_switch_stable_until = 10.1
        self.assertTrue(controller._phase_switch_still_stabilizing(service, 10.0))
        service._phase_switch_stable_until = 10.0
        self.assertFalse(controller._phase_switch_still_stabilizing(service, 10.0))
        service._phase_switch_stable_until = None
        delattr(service, "_phase_switch_stable_until")
        self.assertFalse(controller._phase_switch_still_stabilizing(service, 10.0))

        service._last_charger_state_at = 40.0
        self.assertIsNone(controller._observed_phase_selection(service, {}, 40.0))
        service._last_charger_state_phase_selection = None
        self.assertIsNone(controller._remember_observed_phase_selection(service, {}, 40.0))
        self.assertEqual(service.active_phase_selection, "P1")
        self.assertEqual(controller._remember_observed_phase_selection(service, {"_phase_selection": "bad"}, 40.0), "P1")
        self.assertEqual(service.active_phase_selection, "P1")
        with patch.object(controller, "_observed_phase_selection", return_value="P1_P2") as observed:
            self.assertEqual(controller._remember_observed_phase_selection(service, {"sample": True}, 41.0), "P1_P2")
        observed.assert_called_once_with(service, {"sample": True}, 41.0)
        self.assertEqual(service.active_phase_selection, "P1_P2")

        no_state_service = _auto_phase_service(_phase_switch_pending_selection="P1_P2")
        delattr(no_state_service, "_phase_switch_state")
        no_state_controller = UpdateCycleController(
            no_state_service,
            _phase_values,
            lambda reason: {"init": 0}.get(reason, 99),
        )
        with patch.object(
            no_state_controller,
            "_phase_switch_state_active",
            wraps=no_state_controller._phase_switch_state_active,
        ) as state_active:
            self.assertEqual(
                no_state_controller.orchestrate_pending_phase_switch({}, True, 123.0, 4.0, True, 43.0, False),
                (True, 123.0, 4.0, True, None),
            )
        state_active.assert_called_once_with("P1_P2", "")

        service._phase_switch_lockout_selection = "P1_P2"
        with patch.object(controller, "_clear_phase_switch_lockout") as clear_lockout:
            controller._clear_matching_phase_switch_lockout(service, "P1")
        clear_lockout.assert_not_called()
        service._phase_switch_lockout_selection = "bad"
        with patch.object(controller, "_clear_phase_switch_lockout") as clear_lockout:
            with patch.object(
                phase_switch_runtime,
                "normalize_phase_selection",
                wraps=phase_switch_runtime.normalize_phase_selection,
            ) as normalize:
                controller._clear_matching_phase_switch_lockout(service, "P1")
        clear_lockout.assert_called_once_with(service)
        normalize.assert_called_once_with("bad", "P1")
        with patch.object(controller, "_clear_phase_switch_lockout") as clear_lockout:
            service._phase_switch_lockout_selection = "P1_P2"
            controller._clear_matching_phase_switch_lockout(service, "P1_P2")
        clear_lockout.assert_called_once_with(service)

        delattr(service, "_phase_switch_requested_at")
        service._peek_pending_relay_command = MagicMock(return_value=(None, None))
        self.assertTrue(controller._phase_switch_waiting_ready(service, False, True, 42.0))

    def test_phase_switch_runtime_apply_pending_contract_records_exact_state(self):
        service = _auto_phase_service(
            _phase_switch_mismatch_active=True,
            _apply_phase_selection=MagicMock(return_value="P1_P2"),
            _save_runtime_state=MagicMock(),
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        with patch.object(controller, "_phase_switch_stabilization_seconds", return_value=3.5) as stabilization:
            self.assertEqual(
                controller._apply_pending_phase_selection(service, "P1_P2", 50.0),
                (False, 0.0, 0.0, False, False),
            )
        service._apply_phase_selection.assert_called_once_with("P1_P2")
        stabilization.assert_called_once_with(service)
        self.assertIs(service._phase_switch_mismatch_active, False)
        self.assertEqual(service.requested_phase_selection, "P1_P2")
        self.assertEqual(service._phase_switch_state, controller.PHASE_SWITCH_STABILIZING_STATE)
        self.assertEqual(service._phase_switch_stable_until, 53.5)
        service._save_runtime_state.assert_called_once_with()

    def test_phase_switch_runtime_stabilized_and_mismatch_contracts_pass_exact_arguments(self):
        service = _auto_phase_service(
            _phase_switch_stable_until=100.0,
            auto_shelly_soft_fail_seconds=5.0,
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        with (
            patch.object(controller, "_clear_phase_switch_mismatch_tracking") as clear_mismatch,
            patch.object(controller, "_clear_matching_phase_switch_lockout") as clear_lockout,
            patch.object(controller, "_resume_after_phase_switch_pause", return_value=(True, 7.0, 8.0, True)) as resume,
        ):
            self.assertEqual(
                controller._complete_stabilized_phase_switch(service, "P1_P2", False, 10.0, 1.0, False, 50.0, True),
                (True, 7.0, 8.0, True, None),
            )
        self.assertEqual(service.active_phase_selection, "P1_P2")
        clear_mismatch.assert_called_once_with(service, "P1_P2")
        clear_lockout.assert_called_once_with(service, "P1_P2")
        resume.assert_called_once_with(service, False, 10.0, 1.0, False, 50.0, True)

        with (
            patch.object(controller, "_phase_switch_verification_expired", return_value=False) as expired,
            patch.object(controller, "_report_phase_switch_mismatch") as report,
        ):
            self.assertEqual(
                controller._stabilizing_phase_switch_mismatch_result(
                    service,
                    "P1_P2",
                    "P1",
                    True,
                    900.0,
                    4.5,
                    True,
                    101.0,
                    False,
                ),
                (False, 0.0, 0.0, False, False),
            )
        expired.assert_called_once_with(service, 101.0)
        report.assert_not_called()

        with (
            patch.object(controller, "_phase_switch_verification_expired", return_value=True),
            patch.object(controller, "_report_phase_switch_mismatch") as report,
            patch.object(controller, "_abort_phase_switch_after_mismatch", return_value=(False, 1.0, 2.0, False)) as abort,
        ):
            self.assertEqual(
                controller._stabilizing_phase_switch_mismatch_result(
                    service,
                    "P1_P2",
                    "P1",
                    True,
                    901.0,
                    4.6,
                    False,
                    102.0,
                    True,
                ),
                (False, 1.0, 2.0, False, None),
            )
        report.assert_called_once_with(service, "P1_P2", "P1", 102.0)
        abort.assert_called_once_with(service, "P1_P2", "P1", True, 901.0, 4.6, False, 102.0, True)

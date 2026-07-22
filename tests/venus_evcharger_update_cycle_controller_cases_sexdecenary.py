# SPDX-License-Identifier: GPL-3.0-or-later
from tests.venus_evcharger_update_cycle_controller_support import *


class TestUpdateCycleControllerSexdecenary(UpdateCycleControllerTestBase):
    def test_runtime_cycle_completion_records_success_and_companion_publish(self):
        service = SimpleNamespace(
            time_now=MagicMock(return_value=105.5),
            state=SimpleNamespace(publish_companion_bridge=MagicMock()),
            virtual_mode=2,
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        with patch("venus_evcharger.update.runtime_cycle.logging.debug") as debug:
            controller.components.runtime_cycle.complete_update_cycle(
                True,
                1234.5,
                5.5,
                2,
                777.0,
                80.0,
                -50.0,
            )

        self.assertEqual(service._last_successful_update_at, 105.5)
        self.assertIsNone(service._last_recovery_attempt_at)
        self.assertEqual(service.last_update, 105.5)
        service.state.publish_companion_bridge.assert_called_once_with(105.5)
        self.assertEqual(
            debug.call_args.args[0],
            "Wallbox relay=%s power=%sW current=%sA status=%s pv=%sW soc=%s%% grid=%sW mode=%s",
        )
        self.assertEqual(debug.call_args.args[1:], (True, 1234.5, 5.5, 2, 777.0, 80.0, -50.0, 2))

    def test_runtime_cycle_completion_records_zero_measurements(self):
        service = SimpleNamespace(
            time_now=MagicMock(return_value=205.0),
            state=SimpleNamespace(publish_companion_bridge=MagicMock()),
            virtual_mode=0,
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        controller.components.runtime_cycle.complete_update_cycle(False, 0.0, 0.0, 6, None, None, None)

        self.assertEqual(service.last_update, 205.0)
        service.state.publish_companion_bridge.assert_called_once_with(205.0)

    def test_run_update_cycle_routes_offline_and_online_paths(self):
        service = SimpleNamespace(time_now=MagicMock(return_value=100.0))
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))
        controller.components.state.prepare_update_cycle = MagicMock(return_value={"snapshot": True})
        controller.components.pm_snapshots.resolve_pm_status_for_update = MagicMock(return_value=None)
        controller.components.offline.publish_offline_update = MagicMock(return_value=False)
        controller.components.runtime_cycle._run_online_update_cycle = MagicMock()

        self.assertFalse(controller.components.runtime_cycle.run())
        controller.components.state.prepare_update_cycle.assert_called_once_with(service, 100.0)
        controller.components.pm_snapshots.resolve_pm_status_for_update.assert_called_once_with(service, {"snapshot": True}, 100.0)
        controller.components.offline.publish_offline_update.assert_called_once_with(100.0)
        controller.components.runtime_cycle._run_online_update_cycle.assert_not_called()

        controller.components.state.prepare_update_cycle.reset_mock()
        controller.components.pm_snapshots.resolve_pm_status_for_update.reset_mock(return_value=True)
        controller.components.pm_snapshots.resolve_pm_status_for_update.return_value = {"output": True}
        controller.components.offline.publish_offline_update.reset_mock()

        self.assertTrue(controller.components.runtime_cycle.run())
        controller.components.runtime_cycle._run_online_update_cycle.assert_called_once_with({"output": True}, {"snapshot": True}, 100.0)
        controller.components.offline.publish_offline_update.assert_not_called()

    def test_run_online_update_cycle_passes_context_through_each_runtime_step(self):
        service = SimpleNamespace()
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))
        prepared_status = {"output": True, "aenergy": {"total": 12.5}}
        worker_snapshot = {"worker": True}

        controller.components.runtime_cycle._prepared_online_update_state = MagicMock(
            return_value=(prepared_status, True, 1200.0, 230.0, 5.2, 12.5, True, True)
        )
        controller.components.runtime_cycle._refresh_learning_before_decision = MagicMock(return_value=True)
        controller.components.inputs.resolve_auto_inputs = MagicMock(return_value=(2000.0, 80.0, -300.0))
        controller.components.runtime_cycle._resolved_relay_decision = MagicMock(
            return_value=(False, 0.0, 0.0, False, False, "charger-fault")
        )
        controller.components.victron_ess_balance.apply_victron_ess_balance_bias = MagicMock()
        controller.components.relay.status.apply_relay_decision = MagicMock(return_value=(False, 0.0, 0.0, False))
        controller.components.runtime_cycle._status_after_relay_decision = MagicMock(return_value=(0.0, 6))
        controller.components.runtime_cycle._apply_post_decision_health = MagicMock()
        controller.components.relay.status.publish_online_update = MagicMock(return_value=True)
        controller.components.learning.update_learned_charge_power = MagicMock(return_value=False)
        controller.components.state.save_runtime_state_best_effort = MagicMock()
        controller.components.runtime_cycle.complete_update_cycle = MagicMock()

        controller.components.runtime_cycle._run_online_update_cycle({"raw": True}, worker_snapshot, 100.0)

        controller.components.runtime_cycle._prepared_online_update_state.assert_called_once_with({"raw": True}, 100.0)
        controller.components.runtime_cycle._refresh_learning_before_decision.assert_called_once_with(True, 1200.0, 230.0, 100.0, True)
        controller.components.inputs.resolve_auto_inputs.assert_called_once_with(worker_snapshot, 100.0, True)
        controller.components.runtime_cycle._resolved_relay_decision.assert_called_once_with(
            prepared_status,
            True,
            1200.0,
            230.0,
            5.2,
            True,
            100.0,
            True,
            2000.0,
            80.0,
            -300.0,
        )
        controller.components.victron_ess_balance.apply_victron_ess_balance_bias.assert_called_once_with(service, 100.0, True)
        controller.components.relay.status.apply_relay_decision.assert_called_once_with(
            service, False, False, prepared_status, 0.0, 0.0, 100.0, True
        )
        controller.components.runtime_cycle._status_after_relay_decision.assert_called_once_with(False, 0.0, True, "charger-fault", 100.0)
        controller.components.runtime_cycle._apply_post_decision_health.assert_called_once_with(False, False, 100.0, "charger-fault")
        controller.components.relay.status.publish_online_update.assert_called_once_with(
            service, prepared_status, 6, 12.5, False, 0.0, 230.0, 100.0
        )
        controller.components.learning.update_learned_charge_power.assert_called_once_with(
            False,
            6,
            0.0,
            230.0,
            100.0,
            pm_confirmed=False,
        )
        controller.components.state.save_runtime_state_best_effort.assert_called_once_with("learning-state")
        controller.components.runtime_cycle.complete_update_cycle.assert_called_once_with(
            False,
            0.0,
            0.0,
            6,
            2000.0,
            80.0,
            -300.0,
        )

    def test_run_online_update_cycle_saves_learning_state_when_post_update_learning_changes(self):
        service = SimpleNamespace()
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))
        controller.components.runtime_cycle._prepared_online_update_state = MagicMock(return_value=({}, False, 0.0, 230.0, 0.0, 0.0, False, False))
        controller.components.runtime_cycle._refresh_learning_before_decision = MagicMock(return_value=False)
        controller.components.inputs.resolve_auto_inputs = MagicMock(return_value=(None, None, None))
        controller.components.runtime_cycle._resolved_relay_decision = MagicMock(return_value=(False, 0.0, 0.0, False, False, None))
        controller.components.victron_ess_balance.apply_victron_ess_balance_bias = MagicMock()
        controller.components.relay.status.apply_relay_decision = MagicMock(return_value=(False, 0.0, 0.0, False))
        controller.components.runtime_cycle._status_after_relay_decision = MagicMock(return_value=(0.0, 6))
        controller.components.runtime_cycle._apply_post_decision_health = MagicMock()
        controller.components.relay.status.publish_online_update = MagicMock(return_value=False)
        controller.components.learning.update_learned_charge_power = MagicMock(return_value=True)
        controller.components.state.save_runtime_state_best_effort = MagicMock()
        controller.components.runtime_cycle.complete_update_cycle = MagicMock()

        controller.components.runtime_cycle._run_online_update_cycle({}, {}, 101.0)

        controller.components.state.save_runtime_state_best_effort.assert_called_once_with("learning-state")
        controller.components.runtime_cycle.complete_update_cycle.assert_called_once()

    def test_run_online_update_cycle_skips_learning_save_when_learning_is_unchanged(self):
        service = SimpleNamespace()
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))
        controller.components.runtime_cycle._prepared_online_update_state = MagicMock(return_value=({}, False, 0.0, 230.0, 0.0, 0.0, False, False))
        controller.components.runtime_cycle._refresh_learning_before_decision = MagicMock(return_value=False)
        controller.components.inputs.resolve_auto_inputs = MagicMock(return_value=(None, None, None))
        controller.components.runtime_cycle._resolved_relay_decision = MagicMock(return_value=(False, 0.0, 0.0, False, False, None))
        controller.components.victron_ess_balance.apply_victron_ess_balance_bias = MagicMock()
        controller.components.relay.status.apply_relay_decision = MagicMock(return_value=(False, 0.0, 0.0, False))
        controller.components.runtime_cycle._status_after_relay_decision = MagicMock(return_value=(0.0, 6))
        controller.components.runtime_cycle._apply_post_decision_health = MagicMock()
        controller.components.relay.status.publish_online_update = MagicMock(return_value=False)
        controller.components.learning.update_learned_charge_power = MagicMock(return_value=False)
        controller.components.state.save_runtime_state_best_effort = MagicMock()
        controller.components.runtime_cycle.complete_update_cycle = MagicMock()

        controller.components.runtime_cycle._run_online_update_cycle({}, {}, 102.0)

        controller.components.state.save_runtime_state_best_effort.assert_not_called()
        controller.components.runtime_cycle.complete_update_cycle.assert_called_once()

    def test_resolved_relay_decision_applies_switch_charger_and_phase_overrides_in_order(self):
        service = SimpleNamespace()
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))
        controller.components.relay.foundation.phase_switch.orchestrate_pending_phase_switch = MagicMock(return_value=(True, 111.0, 4.8, True, None))
        controller.components.runtime_cycle._desired_relay_target = MagicMock(return_value=True)
        controller.components.runtime_cycle._blocking_switch_feedback_health = MagicMock(return_value="contactor-lockout-open")
        controller.components.runtime_cycle._blocking_charger_health = MagicMock(return_value="charger-fault")
        controller.components.relay.foundation.auto_phase.maybe_apply_auto_phase_selection = MagicMock(return_value=True)
        controller.components.relay.foundation.targets.apply_current_target = MagicMock()

        result = controller.components.runtime_cycle._resolved_relay_decision(
            {"output": True},
            False,
            100.0,
            230.0,
            0.5,
            False,
            200.0,
            True,
            900.0,
            70.0,
            -100.0,
        )

        self.assertEqual(result, (True, 111.0, 4.8, True, True, "contactor-lockout-open"))
        controller.components.relay.foundation.phase_switch.orchestrate_pending_phase_switch.assert_called_once_with(
            service,
            {"output": True},
            False,
            100.0,
            0.5,
            False,
            200.0,
            True,
        )
        controller.components.runtime_cycle._desired_relay_target.assert_called_once_with(service, True, None, 900.0, 70.0, -100.0)
        controller.components.runtime_cycle._blocking_switch_feedback_health.assert_called_once_with(True, True, 111.0, 4.8, True, 200.0)
        controller.components.runtime_cycle._blocking_charger_health.assert_called_once_with(False, True, 200.0)
        controller.components.relay.foundation.auto_phase.maybe_apply_auto_phase_selection.assert_called_once_with(service, False, True, 230.0, 200.0, True)
        controller.components.relay.foundation.targets.apply_current_target.assert_called_once_with(service, True, 200.0, True)

    def test_resolved_relay_decision_passes_phase_switch_override_to_desired_target(self):
        service = SimpleNamespace()
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))
        controller.components.relay.foundation.phase_switch.orchestrate_pending_phase_switch = MagicMock(return_value=(False, 0.0, 0.0, True, False))
        controller.components.runtime_cycle._desired_relay_target = MagicMock(return_value=False)
        controller.components.runtime_cycle._blocking_switch_feedback_health = MagicMock(return_value=None)
        controller.components.runtime_cycle._blocking_charger_health = MagicMock(return_value=None)
        controller.components.relay.foundation.auto_phase.maybe_apply_auto_phase_selection = MagicMock(return_value=None)
        controller.components.relay.foundation.targets.apply_current_target = MagicMock()

        self.assertEqual(
            controller.components.runtime_cycle._resolved_relay_decision({}, True, 10.0, 230.0, 1.0, False, 301.0, True, 11.0, 22.0, 33.0),
            (False, 0.0, 0.0, True, False, None),
        )

        controller.components.runtime_cycle._desired_relay_target.assert_called_once_with(service, False, False, 11.0, 22.0, 33.0)

    def test_resolved_relay_decision_uses_charger_health_when_switch_is_healthy(self):
        service = SimpleNamespace()
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))
        controller.components.relay.foundation.phase_switch.orchestrate_pending_phase_switch = MagicMock(return_value=(False, 0.0, 0.0, False, False))
        controller.components.runtime_cycle._desired_relay_target = MagicMock(return_value=False)
        controller.components.runtime_cycle._blocking_switch_feedback_health = MagicMock(return_value=None)
        controller.components.runtime_cycle._blocking_charger_health = MagicMock(return_value="charger-health")
        controller.components.relay.foundation.auto_phase.maybe_apply_auto_phase_selection = MagicMock(return_value=None)
        controller.components.relay.foundation.targets.apply_current_target = MagicMock()

        result = controller.components.runtime_cycle._resolved_relay_decision({}, True, 10.0, 230.0, 1.0, True, 300.0, False, None, None, None)

        self.assertEqual(result, (False, 0.0, 0.0, False, False, "charger-health"))
        controller.components.runtime_cycle._blocking_charger_health.assert_called_once_with(False, False, 300.0)
        controller.components.relay.foundation.targets.apply_current_target.assert_called_once_with(service, False, 300.0, False)

    def test_desired_relay_target_contracts_phase_override_and_auto_decision(self):
        service = install_update_cycle_roles(
            SimpleNamespace(_auto_decide_relay=MagicMock(return_value=1))
        )

        self.assertTrue(RuntimeCycleCoordinator._desired_relay_target(service, False, True, 1.0, 2.0, 3.0))
        self.assertFalse(RuntimeCycleCoordinator._desired_relay_target(service, True, False, 1.0, 2.0, 3.0))
        service._auto_decide_relay.assert_not_called()

        self.assertTrue(RuntimeCycleCoordinator._desired_relay_target(service, True, None, 4.0, 5.0, 6.0))
        service._auto_decide_relay.assert_called_once_with(True, 4.0, 5.0, 6.0)

    def test_charger_health_blocking_warns_only_when_charging_is_requested(self):
        service = SimpleNamespace(
            auto_shelly_soft_fail_seconds=12.0,
            _warning_throttled=MagicMock(),
            _last_charger_state_status="fault",
            _last_charger_state_fault="overcurrent",
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))
        controller.components.relay.foundation.health.charger_health_override = MagicMock(return_value=None)

        self.assertIsNone(controller.components.runtime_cycle._blocking_charger_health(True, False, 100.0))
        service._warning_throttled.assert_not_called()

        controller.components.relay.foundation.health.charger_health_override.return_value = "charger-fault"
        self.assertEqual(controller.components.runtime_cycle._blocking_charger_health(False, False, 101.0), "charger-fault")
        service._warning_throttled.assert_not_called()

        service._readback_store.replace_charger(
            TimedChargerState(
                ChargerState(None, None, None, status_text="fault", fault_text="overcurrent"),
                102.0,
            )
        )
        self.assertEqual(controller.components.runtime_cycle._blocking_charger_health(True, False, 102.0), "charger-fault")
        service._warning_throttled.assert_called_once_with(
            "charger-health-blocking",
            12.0,
            "Native charger health override %s blocks charging (status=%s fault=%s)",
            "charger-fault",
            "fault",
            "overcurrent",
        )
        controller.components.relay.foundation.health.charger_health_override.assert_any_call(service, 100.0)
        controller.components.relay.foundation.health.charger_health_override.assert_any_call(service, 101.0)
        controller.components.relay.foundation.health.charger_health_override.assert_any_call(service, 102.0)

    def test_charger_health_blocking_warns_when_existing_relay_is_on(self):
        service = SimpleNamespace(
            auto_shelly_soft_fail_seconds=12.0,
            _warning_throttled=MagicMock(),
            _last_charger_state_status="idle",
            _last_charger_state_fault=None,
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))
        controller.components.relay.foundation.health.charger_health_override = MagicMock(return_value="charger-offline")
        service._readback_store.replace_charger(
            TimedChargerState(ChargerState(None, None, None, status_text="idle"), 103.0)
        )

        self.assertEqual(controller.components.runtime_cycle._blocking_charger_health(False, True, 103.0), "charger-offline")

        service._warning_throttled.assert_called_once_with(
            "charger-health-blocking",
            12.0,
            "Native charger health override %s blocks charging (status=%s fault=%s)",
            "charger-offline",
            "idle",
            None,
        )
        controller.components.relay.foundation.health.charger_health_override.assert_called_once_with(service, 103.0)

    def test_charger_health_warning_spec_distinguishes_transport_from_health(self):
        service = SimpleNamespace(
            _last_charger_transport_source="modbus",
            _last_charger_transport_detail="timeout",
            _last_charger_state_status="fault",
            _last_charger_state_fault="overcurrent",
        )

        self.assertEqual(
            blocking_charger_health_warning_spec(
                service,
                "charger-transport-offline",
                None,
            ),
            (
                "charger-transport-blocking",
                "Native charger transport override %s blocks charging (source=%s detail=%s)",
                ("charger-transport-offline", "modbus", "timeout"),
            ),
        )
        self.assertEqual(
            blocking_charger_health_warning_spec(
                service,
                "charger-fault",
                ChargerState(None, None, None, status_text="fault", fault_text="overcurrent"),
            ),
            (
                "charger-health-blocking",
                "Native charger health override %s blocks charging (status=%s fault=%s)",
                ("charger-fault", "fault", "overcurrent"),
            ),
        )

    def test_charger_health_warning_spec_defaults_missing_metadata(self):
        service = SimpleNamespace(
            _last_charger_transport_source=None,
            _last_charger_transport_detail=None,
        )

        self.assertEqual(
            blocking_charger_health_warning_spec(
                service,
                "charger-transport-timeout",
                None,
            ),
            (
                "charger-transport-blocking",
                "Native charger transport override %s blocks charging (source=%s detail=%s)",
                ("charger-transport-timeout", None, None),
            ),
        )
        self.assertEqual(
            blocking_charger_health_warning_spec(
                service,
                "charger-state-unknown",
                None,
            ),
            (
                "charger-health-blocking",
                "Native charger health override %s blocks charging (status=%s fault=%s)",
                ("charger-state-unknown", None, None),
            ),
        )

    def test_switch_feedback_warning_specs_are_explicit_for_all_known_reasons(self):
        service = SimpleNamespace(
            _last_switch_interlock_ok=False,
            _last_charger_state_status="idle",
            _contactor_fault_counts={"contactor-suspected-open": 2, "contactor-suspected-welded": 3},
            _contactor_lockout_source="feedback",
            _last_switch_feedback_closed=True,
        )
        charger_state = ChargerState(None, None, None, status_text="idle")
        switch_state = SwitchState(True, "P1", feedback_closed=True, interlock_ok=False)

        cases = {
            "contactor-interlock": (
                "switch-interlock-blocking",
                "Switch interlock blocks charging (desired=%s relay=%s interlock_ok=%s)",
                (1, 0, False),
            ),
            "contactor-suspected-open": (
                "switch-suspected-open-blocking",
                "Contactor heuristics suspect OPEN state (relay=%s power=%.1f current=%.1f charger_status=%s)",
                (0, 123.4, 5.6, "idle"),
            ),
            "contactor-suspected-welded": (
                "switch-suspected-welded-blocking",
                "Contactor heuristics suspect WELDED state (relay=%s power=%.1f current=%.1f)",
                (0, 123.4, 5.6),
            ),
            "contactor-lockout-open": (
                "switch-lockout-open-blocking",
                "Latched contactor OPEN lockout blocks charging (count=%s source=%s)",
                (2, "feedback"),
            ),
            "contactor-lockout-welded": (
                "switch-lockout-welded-blocking",
                "Latched contactor WELDED lockout blocks charging (count=%s source=%s)",
                (3, "feedback"),
            ),
            "other": (
                "switch-feedback-blocking",
                "Switch feedback mismatch blocks charging (relay=%s feedback_closed=%s)",
                (0, True),
            ),
        }

        for reason, expected in cases.items():
            with self.subTest(reason=reason):
                self.assertEqual(
                    switch_feedback_warning_spec(
                        reason,
                        True,
                        False,
                        123.4,
                        5.6,
                        service,
                        charger_state,
                        switch_state,
                    ),
                    expected,
                )

    def test_switch_feedback_warning_specs_default_missing_metadata_and_preserve_relay_flags(self):
        service = SimpleNamespace(
            _last_charger_transport_source=None,
            _last_charger_transport_detail=None,
            _contactor_fault_counts={},
            _contactor_lockout_source="",
        )
        cases = {
            "contactor-interlock": (
                "switch-interlock-blocking",
                "Switch interlock blocks charging (desired=%s relay=%s interlock_ok=%s)",
                (0, 1, None),
            ),
            "contactor-suspected-open": (
                "switch-suspected-open-blocking",
                "Contactor heuristics suspect OPEN state (relay=%s power=%.1f current=%.1f charger_status=%s)",
                (1, 123.4, 5.6, None),
            ),
            "contactor-suspected-welded": (
                "switch-suspected-welded-blocking",
                "Contactor heuristics suspect WELDED state (relay=%s power=%.1f current=%.1f)",
                (1, 123.4, 5.6),
            ),
            "contactor-lockout-open": (
                "switch-lockout-open-blocking",
                "Latched contactor OPEN lockout blocks charging (count=%s source=%s)",
                (0, ""),
            ),
            "contactor-lockout-welded": (
                "switch-lockout-welded-blocking",
                "Latched contactor WELDED lockout blocks charging (count=%s source=%s)",
                (0, ""),
            ),
            "other": (
                "switch-feedback-blocking",
                "Switch feedback mismatch blocks charging (relay=%s feedback_closed=%s)",
                (1, None),
            ),
        }

        for reason, expected in cases.items():
            with self.subTest(reason=reason):
                self.assertEqual(
                    switch_feedback_warning_spec(
                        reason,
                        False,
                        True,
                        123.4,
                        5.6,
                        service,
                        None,
                        None,
                    ),
                    expected,
                )

    def test_blocking_switch_feedback_health_emits_warning_with_exact_metadata(self):
        service = SimpleNamespace(
            auto_shelly_soft_fail_seconds=9.0,
            _warning_throttled=MagicMock(),
            _contactor_fault_counts={},
            _contactor_lockout_source="",
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))
        controller.components.relay.foundation.health.switch_feedback_health_override = MagicMock(return_value=None)

        self.assertIsNone(controller.components.runtime_cycle._blocking_switch_feedback_health(True, False, 10.0, 1.0, False, 100.0))
        service._warning_throttled.assert_not_called()

        controller.components.relay.foundation.health.switch_feedback_health_override.return_value = "contactor-interlock"
        with patch(
            "venus_evcharger.update.runtime_cycle.switch_feedback_warning_spec",
            return_value=("key", "message %s", ("arg",)),
        ) as warning_spec:
            self.assertEqual(
                controller.components.runtime_cycle._blocking_switch_feedback_health(
                    True, False, 10.0, 1.0, False, 101.0
                ),
                "contactor-interlock",
            )
        controller.components.relay.foundation.health.switch_feedback_health_override.assert_called_with(
            service,
            True,
            False,
            101.0,
            power=10.0,
            current=1.0,
            pm_confirmed=False,
        )
        warning_spec.assert_called_once_with(
            "contactor-interlock",
            True,
            False,
            10.0,
            1.0,
            service,
            None,
            None,
        )
        service._warning_throttled.assert_called_once_with("key", 9.0, "message %s", "arg")

    def test_status_after_relay_decision_prefers_fresh_power_readback_and_delegates_status(self):
        service = SimpleNamespace()
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))
        service._readback_store.replace_charger(
            TimedChargerState(ChargerState(None, None, None, power_w=2222.0), 100.0)
        )
        controller.components.relay.status.derive_status_code = MagicMock(return_value=2)

        self.assertEqual(controller.components.runtime_cycle._status_after_relay_decision(True, 1111.0, True, None, 100.0), (2222.0, 2))
        controller.components.relay.status.derive_status_code.assert_called_once_with(
            service,
            True,
            2222.0,
            True,
            health_reason=None,
            now=100.0,
        )

    def test_status_after_relay_decision_falls_back_to_meter_power_when_readback_is_missing(self):
        service = SimpleNamespace()
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))
        controller.components.relay.status.derive_status_code = MagicMock(return_value=6)

        self.assertEqual(controller.components.runtime_cycle._status_after_relay_decision(False, 12.0, False, "health", 101.0), (12.0, 6))
        controller.components.relay.status.derive_status_code.assert_called_once_with(
            service,
            False,
            12.0,
            False,
            health_reason="health",
            now=101.0,
        )

    def test_post_decision_health_prefers_relay_sync_health_over_charger_health(self):
        service = SimpleNamespace(_set_health=MagicMock())
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))
        controller.components.runtime_cycle._apply_relay_sync_health = MagicMock(return_value="relay-sync")

        controller.components.runtime_cycle._apply_post_decision_health(True, False, 100.0, "charger-fault")

        controller.components.runtime_cycle._apply_relay_sync_health.assert_called_once_with(True, False, 100.0)
        service._set_health.assert_not_called()

        controller.components.runtime_cycle._apply_relay_sync_health.return_value = None
        controller.components.runtime_cycle._apply_post_decision_health(False, True, 101.0, "charger-fault")
        service._set_health.assert_called_once_with("charger-fault", cached=False)

    def test_prepared_online_update_state_refreshes_after_startup_target_and_records_voltage(self):
        service = SimpleNamespace(_mode_uses_auto_logic=MagicMock(return_value=True), virtual_mode=2)
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))
        first_status = {"first": True}
        second_status = {"second": True, "_pm_confirmed": True}
        controller.components.inputs.extract_pm_measurements = MagicMock(
            side_effect=[
                (False, 0.0, 0.0, 0.0, 1.0),
                (True, 1200.0, 230.0, 5.2, 1.5),
            ]
        )
        controller.components.state.apply_startup_manual_target = MagicMock(return_value=second_status)
        controller.components.relay.foundation.telemetry.pm_status_confirmed = MagicMock(return_value=True)

        self.assertEqual(
            controller.components.runtime_cycle._prepared_online_update_state(first_status, 100.0),
            (second_status, True, 1200.0, 230.0, 5.2, 1.5, True, True),
        )
        self.assertEqual(service._last_voltage, 230.0)
        controller.components.inputs.extract_pm_measurements.assert_any_call(service, first_status)
        controller.components.inputs.extract_pm_measurements.assert_any_call(service, second_status)
        controller.components.state.apply_startup_manual_target.assert_called_once_with(first_status, 100.0)
        controller.components.relay.foundation.telemetry.pm_status_confirmed.assert_called_once_with(second_status)
        service._mode_uses_auto_logic.assert_called_once_with(2)

    def test_prepared_online_update_state_does_not_overwrite_voltage_with_zero(self):
        service = SimpleNamespace(_mode_uses_auto_logic=MagicMock(return_value=False), virtual_mode=0, _last_voltage=229.0)
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))
        controller.components.inputs.extract_pm_measurements = MagicMock(return_value=(False, 0.0, 0.0, 0.0, 0.0))
        controller.components.state.apply_startup_manual_target = MagicMock(return_value={})
        controller.components.relay.foundation.telemetry.pm_status_confirmed = MagicMock(return_value=False)

        result = controller.components.runtime_cycle._prepared_online_update_state({}, 101.0)

        self.assertEqual(result, ({}, False, 0.0, 0.0, 0.0, 0.0, False, False))
        self.assertEqual(service._last_voltage, 229.0)

    def test_prepared_online_update_state_records_small_positive_voltage(self):
        service = SimpleNamespace(_mode_uses_auto_logic=MagicMock(return_value=True), virtual_mode=1, _last_voltage=229.0)
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))
        controller.components.inputs.extract_pm_measurements = MagicMock(return_value=(True, 1.0, 0.5, 2.0, 3.0))
        controller.components.state.apply_startup_manual_target = MagicMock(return_value={})
        controller.components.relay.foundation.telemetry.pm_status_confirmed = MagicMock(return_value=True)

        result = controller.components.runtime_cycle._prepared_online_update_state({}, 102.0)

        self.assertEqual(result, ({}, True, 1.0, 0.5, 2.0, 3.0, True, True))
        self.assertEqual(service._last_voltage, 0.5)

    def test_refresh_learning_before_decision_combines_refresh_and_signature_results(self):
        service = SimpleNamespace()
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))
        controller.components.learning.refresh_learned_charge_power_state = MagicMock(return_value=False)
        controller.components.learning.reconcile_learned_charge_power_signature = MagicMock(return_value=True)

        self.assertTrue(controller.components.runtime_cycle._refresh_learning_before_decision(True, 1200.0, 230.0, 100.0, False))
        controller.components.learning.refresh_learned_charge_power_state.assert_called_once_with(100.0)
        controller.components.learning.reconcile_learned_charge_power_signature.assert_called_once_with(
            True,
            1200.0,
            230.0,
            100.0,
            pm_confirmed=False,
        )

        controller.components.learning.refresh_learned_charge_power_state.return_value = False
        controller.components.learning.reconcile_learned_charge_power_signature.return_value = False
        self.assertFalse(controller.components.runtime_cycle._refresh_learning_before_decision(False, 0.0, 0.0, 101.0, True))

        controller.components.learning.refresh_learned_charge_power_state.return_value = True
        controller.components.learning.reconcile_learned_charge_power_signature.return_value = False
        self.assertTrue(controller.components.runtime_cycle._refresh_learning_before_decision(False, 0.0, 0.0, 102.0, True))

    def test_apply_relay_sync_health_sets_service_health_only_for_sync_override(self):
        service = SimpleNamespace(_set_health=MagicMock())
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))
        controller.components.relay.foundation.telemetry.relay_sync_health_override = MagicMock(return_value=None)

        self.assertIsNone(controller.components.runtime_cycle._apply_relay_sync_health(False, True, 100.0))
        service._set_health.assert_not_called()

        controller.components.relay.foundation.telemetry.relay_sync_health_override.return_value = "relay-sync-timeout"
        self.assertEqual(controller.components.runtime_cycle._apply_relay_sync_health(True, False, 101.0), "relay-sync-timeout")
        controller.components.relay.foundation.telemetry.relay_sync_health_override.assert_called_with(
            service, True, False, 101.0
        )
        service._set_health.assert_called_once_with("relay-sync-timeout", cached=False)

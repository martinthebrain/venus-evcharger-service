# SPDX-License-Identifier: GPL-3.0-or-later
from tests.venus_evcharger_update_cycle_controller_support import *


class TestUpdateCycleControllerUndenary(UpdateCycleControllerTestBase):
    def test_derive_status_code_prefers_feedback_fault_over_fresh_native_charger_ready(self):
        service = SimpleNamespace(
            _charger_backend=SimpleNamespace(),
            _last_charger_state_status="ready",
            _last_charger_state_at=100.0,
            _last_health_reason="contactor-feedback-mismatch",
            auto_shelly_soft_fail_seconds=10.0,
            charging_threshold_watts=1500.0,
            idle_status=6,
        )

        status = relay_components_for_test(service).status.derive_status_code(
            service,
            True,
            0.0,
            True,
            health_reason="contactor-feedback-mismatch",
            now=100.0,
        )

        self.assertEqual(status, 0)
        self.assertEqual(service._last_status_source, "contactor-feedback-fault")

    def test_derive_status_code_maps_fresh_native_charger_charging_status_to_charging(self):
        service = SimpleNamespace(
            _charger_backend=SimpleNamespace(),
            _last_charger_state_status="charging",
            _last_charger_state_at=100.0,
            auto_shelly_soft_fail_seconds=10.0,
            charging_threshold_watts=1500.0,
            idle_status=6,
        )
        sync_readback_test_service(service)

        status = relay_components_for_test(service).status.derive_status_code(service, False, 0.0, False, 100.0)

        self.assertEqual(status, 2)
        self.assertEqual(service._last_status_source, "charger-status-charging")
        self.assertEqual(service._last_charger_fault_active, 0)

    def test_derive_status_code_keeps_native_charger_charging_truth_when_meter_power_is_zero(self):
        service = SimpleNamespace(
            _charger_backend=SimpleNamespace(),
            _last_charger_state_status="charging",
            _last_charger_state_at=100.0,
            auto_shelly_soft_fail_seconds=10.0,
            charging_threshold_watts=1500.0,
            idle_status=6,
        )
        sync_readback_test_service(service)

        status = relay_components_for_test(service).status.derive_status_code(service, True, 0.0, True, 100.0)

        self.assertEqual(status, 2)
        self.assertEqual(service._last_status_source, "charger-status-charging")

    def test_derive_status_code_maps_fresh_native_charger_ready_status_to_idle_status(self):
        service = SimpleNamespace(
            _charger_backend=SimpleNamespace(),
            _last_charger_state_status="ready",
            _last_charger_state_at=100.0,
            auto_shelly_soft_fail_seconds=10.0,
            charging_threshold_watts=1500.0,
            idle_status=6,
        )
        sync_readback_test_service(service)

        status = relay_components_for_test(service).status.derive_status_code(service, False, 0.0, False, 100.0)

        self.assertEqual(status, 6)
        self.assertEqual(service._last_status_source, "charger-status-ready")
        self.assertEqual(service._last_charger_fault_active, 0)

    def test_derive_status_code_maps_fresh_native_charger_paused_status_to_auto_waiting(self):
        service = SimpleNamespace(
            _charger_backend=SimpleNamespace(),
            _last_charger_state_status="paused",
            _last_charger_state_at=100.0,
            auto_shelly_soft_fail_seconds=10.0,
            charging_threshold_watts=1500.0,
            idle_status=1,
        )
        sync_readback_test_service(service)

        status = relay_components_for_test(service).status.derive_status_code(service, True, 0.0, True, 100.0)

        self.assertEqual(status, 4)
        self.assertEqual(service._last_status_source, "charger-status-waiting")
        self.assertEqual(service._last_charger_fault_active, 0)

    def test_derive_status_code_maps_fresh_native_charger_paused_status_to_manual_waiting(self):
        service = SimpleNamespace(
            _charger_backend=SimpleNamespace(),
            _last_charger_state_status="paused",
            _last_charger_state_at=100.0,
            auto_shelly_soft_fail_seconds=10.0,
            charging_threshold_watts=1500.0,
            idle_status=1,
        )
        sync_readback_test_service(service)

        status = relay_components_for_test(service).status.derive_status_code(service, True, 0.0, False, 100.0)

        self.assertEqual(status, 6)
        self.assertEqual(service._last_status_source, "charger-status-waiting")
        self.assertEqual(service._last_charger_fault_active, 0)

    def test_derive_status_code_maps_fresh_native_charger_completed_status_to_finished(self):
        service = SimpleNamespace(
            _charger_backend=SimpleNamespace(),
            _last_charger_state_status="completed",
            _last_charger_state_at=100.0,
            auto_shelly_soft_fail_seconds=10.0,
            charging_threshold_watts=1500.0,
            idle_status=1,
        )
        sync_readback_test_service(service)

        status = relay_components_for_test(service).status.derive_status_code(service, True, 0.0, False, 100.0)

        self.assertEqual(status, 3)
        self.assertEqual(service._last_status_source, "charger-status-finished")
        self.assertEqual(service._last_charger_fault_active, 0)

    def test_session_state_from_status_resets_idle_native_charger_session(self):
        service = SimpleNamespace(
            _charger_backend=SimpleNamespace(),
            _last_charger_state_enabled=True,
            _last_charger_state_at=100.0,
            auto_shelly_soft_fail_seconds=10.0,
            charging_started_at=90.0,
            energy_at_start=1.0,
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        charging_time, session_energy = controller.components.state.session_state_from_status(service, 1, 2.0, False, 100.0)

        self.assertEqual((charging_time, session_energy), (0, 0.0))
        self.assertIsNone(service.charging_started_at)
        self.assertEqual(service.energy_at_start, 2.0)

    def test_apply_relay_decision_uses_native_charger_backend_when_available(self):
        charger_backend = SimpleNamespace(set_enabled=MagicMock())
        service = SimpleNamespace(
            _last_auto_metrics={"surplus": None, "grid": None, "soc": None},
            _last_health_reason="init",
            auto_audit_log=False,
            auto_shelly_soft_fail_seconds=10.0,
            _charger_backend=charger_backend,
            _queue_relay_command=MagicMock(),
            _mark_failure=MagicMock(),
            _mark_recovery=MagicMock(),
            _warning_throttled=MagicMock(),
            _publish_local_pm_status=MagicMock(),
            _relay_sync_expected_state=None,
            _relay_sync_requested_at=None,
            _relay_sync_deadline_at=None,
            _relay_sync_failure_reported=False,
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        relay_on, power, current, confirmed = controller.components.relay.status.apply_relay_decision(
            service,
            True,
            False,
            {"output": False, "_pm_confirmed": True},
            0.0,
            0.0,
            100.0,
            False,
        )

        self.assertEqual((relay_on, power, current, confirmed), (True, 0.0, 0.0, False))
        charger_backend.set_enabled.assert_called_once_with(True)
        service._queue_relay_command.assert_not_called()
        service._publish_local_pm_status.assert_called_once_with(True, 100.0)

    def test_charger_health_override_detects_fault_like_readback(self):
        service = SimpleNamespace(
            _charger_backend=SimpleNamespace(set_enabled=MagicMock()),
            _last_charger_state_status="error",
            _last_charger_state_fault="overcurrent fault",
            _last_charger_state_at=100.0,
            auto_shelly_soft_fail_seconds=10.0,
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"charger-fault": 26}.get(reason, 99))

        self.assertEqual(controller.components.relay.foundation.health.charger_health_override(service, 100.0), "charger-fault")

    def test_charger_health_override_ignores_benign_readback_text(self):
        service = SimpleNamespace(
            _charger_backend=SimpleNamespace(set_enabled=MagicMock()),
            _last_charger_state_status="paused",
            _last_charger_state_fault="vehicle-sleeping",
            _last_charger_state_at=100.0,
            auto_shelly_soft_fail_seconds=10.0,
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"charger-fault": 26}.get(reason, 99))

        self.assertIsNone(controller.components.relay.foundation.health.charger_health_override(service, 100.0))

    def test_charger_health_override_prefers_fresh_transport_issue(self):
        service = SimpleNamespace(
            _charger_backend=SimpleNamespace(set_enabled=MagicMock()),
            _last_charger_state_status="charging",
            _last_charger_state_fault="",
            _last_charger_state_at=100.0,
            _last_charger_transport_reason="offline",
            _last_charger_transport_source="read",
            _last_charger_transport_detail="Modbus slave 1 did not respond",
            _last_charger_transport_at=100.0,
            auto_shelly_soft_fail_seconds=10.0,
        )
        controller = UpdateCycleController(
            service,
            _phase_values,
            lambda reason: {
                "charger-fault": 26,
                "charger-transport-offline": 37,
            }.get(reason, 99),
        )

        self.assertEqual(controller.components.relay.foundation.health.charger_health_override(service, 100.0), "charger-transport-offline")

    def test_charger_health_override_falls_back_to_active_retry_reason(self):
        service = SimpleNamespace(
            _charger_backend=SimpleNamespace(set_enabled=MagicMock()),
            _last_charger_state_status="charging",
            _last_charger_state_fault="",
            _last_charger_state_at=100.0,
            _last_charger_transport_reason=None,
            _last_charger_transport_source=None,
            _last_charger_transport_detail=None,
            _last_charger_transport_at=None,
            _charger_retry_reason="offline",
            _charger_retry_source="read",
            _charger_retry_until=105.0,
            auto_shelly_soft_fail_seconds=10.0,
        )
        controller = UpdateCycleController(
            service,
            _phase_values,
            lambda reason: {
                "charger-fault": 26,
                "charger-transport-offline": 37,
            }.get(reason, 99),
        )

        self.assertEqual(controller.components.relay.foundation.health.charger_health_override(service, 100.0), "charger-transport-offline")

    def test_switch_feedback_health_override_detects_interlock_block(self):
        service = SimpleNamespace(
            _last_switch_feedback_closed=True,
            _last_switch_interlock_ok=False,
            _last_switch_feedback_at=100.0,
            auto_shelly_soft_fail_seconds=10.0,
        )
        controller = UpdateCycleController(
            service,
            _phase_values,
            lambda reason: {"contactor-interlock": 28, "contactor-feedback-mismatch": 29}.get(reason, 99),
        )

        self.assertEqual(controller.components.relay.foundation.health.switch_feedback_health_override(service, True, False, 100.0), "contactor-interlock")

    def test_switch_feedback_health_override_detects_feedback_mismatch(self):
        service = SimpleNamespace(
            _last_switch_feedback_closed=False,
            _last_switch_interlock_ok=True,
            _last_switch_feedback_at=100.0,
            auto_shelly_soft_fail_seconds=10.0,
        )
        controller = UpdateCycleController(
            service,
            _phase_values,
            lambda reason: {"contactor-interlock": 28, "contactor-feedback-mismatch": 29}.get(reason, 99),
        )

        self.assertEqual(
            controller.components.relay.foundation.health.switch_feedback_health_override(service, True, True, 100.0),
            "contactor-feedback-mismatch",
        )

    def test_switch_feedback_health_override_prefers_interlock_block_over_other_signals(self):
        service = SimpleNamespace(
            _last_switch_feedback_closed=True,
            _last_switch_interlock_ok=False,
            _last_switch_feedback_at=100.0,
            _contactor_suspected_open_since=90.0,
            _contactor_suspected_welded_since=91.0,
            auto_shelly_soft_fail_seconds=10.0,
        )
        controller = UpdateCycleController(
            service,
            _phase_values,
            lambda reason: {"contactor-interlock": 28, "contactor-feedback-mismatch": 29}.get(reason, 99),
        )

        self.assertEqual(
            controller.components.relay.foundation.health.switch_feedback_health_override(
                service,
                True,
                True,
                100.0,
                power=2300.0,
                current=10.0,
                pm_confirmed=True,
            ),
            "contactor-interlock",
        )
        self.assertIsNone(service._contactor_suspected_open_since)
        self.assertIsNone(service._contactor_suspected_welded_since)

    def test_switch_feedback_health_override_prefers_explicit_open_feedback_over_open_contactor_heuristic(self):
        service = SimpleNamespace(
            _last_switch_feedback_closed=False,
            _last_switch_interlock_ok=True,
            _last_switch_feedback_at=100.0,
            _contactor_suspected_open_since=90.0,
            _contactor_suspected_welded_since=None,
            _charger_backend=SimpleNamespace(),
            _last_charger_state_status="charging",
            _last_charger_state_at=100.0,
            charging_threshold_watts=100.0,
            min_current=6.0,
            auto_shelly_soft_fail_seconds=10.0,
        )
        controller = UpdateCycleController(
            service,
            _phase_values,
            lambda reason: {"contactor-interlock": 28, "contactor-feedback-mismatch": 29}.get(reason, 99),
        )

        self.assertEqual(
            controller.components.relay.foundation.health.switch_feedback_health_override(
                service,
                True,
                True,
                100.0,
                power=0.0,
                current=0.0,
                pm_confirmed=True,
            ),
            "contactor-feedback-mismatch",
        )
        self.assertIsNone(service._contactor_suspected_open_since)

    def test_switch_feedback_health_override_prefers_explicit_closed_feedback_over_welded_heuristic(self):
        service = SimpleNamespace(
            _last_switch_feedback_closed=True,
            _last_switch_interlock_ok=True,
            _last_switch_feedback_at=100.0,
            _contactor_suspected_open_since=None,
            _contactor_suspected_welded_since=90.0,
            auto_shelly_soft_fail_seconds=10.0,
        )
        controller = UpdateCycleController(
            service,
            _phase_values,
            lambda reason: {"contactor-interlock": 28, "contactor-feedback-mismatch": 29}.get(reason, 99),
        )

        self.assertEqual(
            controller.components.relay.foundation.health.switch_feedback_health_override(
                service,
                False,
                False,
                100.0,
                power=2300.0,
                current=10.0,
                pm_confirmed=True,
            ),
            "contactor-feedback-mismatch",
        )
        self.assertIsNone(service._contactor_suspected_welded_since)

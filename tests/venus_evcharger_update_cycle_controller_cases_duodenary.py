# SPDX-License-Identifier: GPL-3.0-or-later
from tests.venus_evcharger_update_cycle_controller_support import *


class TestUpdateCycleControllerDuodenary(UpdateCycleControllerTestBase):
    def test_switch_feedback_health_override_suspects_welded_contactor_without_feedback(self):
        service = SimpleNamespace(
            _last_switch_feedback_closed=None,
            _last_switch_interlock_ok=None,
            _last_switch_feedback_at=None,
            _contactor_suspected_open_since=None,
            _contactor_suspected_welded_since=None,
            _charger_backend=None,
            charging_threshold_watts=100.0,
            min_current=6.0,
            auto_shelly_soft_fail_seconds=10.0,
        )
        controller = UpdateCycleController(
            service,
            _phase_values,
            lambda reason: {"contactor-suspected-welded": 31}.get(reason, 99),
        )

        self.assertIsNone(
            controller.components.relay.foundation.health.switch_feedback_health_override(
                service,
                False,
                False,
                100.0,
                power=1200.0,
                current=5.2,
                pm_confirmed=True,
            )
        )
        self.assertEqual(service._contactor_suspected_welded_since, 100.0)
        self.assertEqual(
            controller.components.relay.foundation.health.switch_feedback_health_override(
                service,
                False,
                False,
                111.0,
                power=1200.0,
                current=5.2,
                pm_confirmed=True,
            ),
            "contactor-suspected-welded",
        )

    def test_switch_feedback_health_override_suspects_open_contactor_from_charger_activity(self):
        service = SimpleNamespace(
            _last_switch_feedback_closed=None,
            _last_switch_interlock_ok=None,
            _last_switch_feedback_at=None,
            _contactor_suspected_open_since=None,
            _contactor_suspected_welded_since=None,
            _charger_backend=object(),
            _last_charger_state_at=100.0,
            _last_charger_state_status="charging",
            _last_charger_state_power_w=0.0,
            _last_charger_state_actual_current_amps=0.0,
            charging_threshold_watts=100.0,
            min_current=6.0,
            auto_shelly_soft_fail_seconds=10.0,
        )
        controller = UpdateCycleController(
            service,
            _phase_values,
            lambda reason: {"contactor-suspected-open": 30}.get(reason, 99),
        )

        self.assertIsNone(
            controller.components.relay.foundation.health.switch_feedback_health_override(
                service,
                True,
                True,
                100.0,
                power=0.0,
                current=0.0,
                pm_confirmed=True,
            )
        )
        self.assertEqual(service._contactor_suspected_open_since, 100.0)
        service._last_charger_state_at = 110.0
        sync_readback_test_service(service)
        self.assertEqual(
            controller.components.relay.foundation.health.switch_feedback_health_override(
                service,
                True,
                True,
                110.0,
                power=0.0,
                current=0.0,
                pm_confirmed=True,
            ),
            "contactor-suspected-open",
        )

    def test_switch_feedback_health_override_latches_repeated_welded_contactor_faults(self):
        service = SimpleNamespace(
            _last_switch_feedback_closed=None,
            _last_switch_interlock_ok=None,
            _last_switch_feedback_at=None,
            _contactor_suspected_open_since=None,
            _contactor_suspected_welded_since=None,
            _contactor_fault_counts={},
            _contactor_fault_active_reason=None,
            _contactor_fault_active_since=None,
            _contactor_lockout_reason="",
            _contactor_lockout_source="",
            _contactor_lockout_at=None,
            _charger_backend=None,
            charging_threshold_watts=100.0,
            min_current=6.0,
            auto_shelly_soft_fail_seconds=10.0,
            auto_contactor_fault_latch_count=2,
            auto_contactor_fault_latch_seconds=120.0,
        )
        controller = UpdateCycleController(
            service,
            _phase_values,
            lambda reason: {
                "contactor-suspected-welded": 31,
                "contactor-lockout-welded": 33,
            }.get(reason, 99),
        )

        self.assertIsNone(
            controller.components.relay.foundation.health.switch_feedback_health_override(
                service,
                False,
                False,
                100.0,
                power=1200.0,
                current=5.2,
                pm_confirmed=True,
            )
        )
        self.assertEqual(
            controller.components.relay.foundation.health.switch_feedback_health_override(
                service,
                False,
                False,
                111.0,
                power=1200.0,
                current=5.2,
                pm_confirmed=True,
            ),
            "contactor-suspected-welded",
        )
        self.assertEqual(service._contactor_fault_counts, {"contactor-suspected-welded": 1})

        self.assertIsNone(
            controller.components.relay.foundation.health.switch_feedback_health_override(
                service,
                False,
                False,
                112.0,
                power=0.0,
                current=0.0,
                pm_confirmed=True,
            )
        )
        self.assertIsNone(service._contactor_fault_active_reason)
        self.assertIsNone(
            controller.components.relay.foundation.health.switch_feedback_health_override(
                service,
                False,
                False,
                113.0,
                power=1200.0,
                current=5.2,
                pm_confirmed=True,
            )
        )

        self.assertEqual(
            controller.components.relay.foundation.health.switch_feedback_health_override(
                service,
                False,
                False,
                124.0,
                power=1200.0,
                current=5.2,
                pm_confirmed=True,
            ),
            "contactor-lockout-welded",
        )
        self.assertEqual(service._contactor_fault_counts, {"contactor-suspected-welded": 2})
        self.assertEqual(service._contactor_lockout_reason, "contactor-suspected-welded")
        self.assertEqual(service._contactor_lockout_source, "count-threshold")
        self.assertEqual(service._contactor_lockout_at, 124.0)

    def test_switch_feedback_health_override_latches_persistent_open_contactor_fault(self):
        service = SimpleNamespace(
            _last_switch_feedback_closed=None,
            _last_switch_interlock_ok=None,
            _last_switch_feedback_at=None,
            _contactor_suspected_open_since=None,
            _contactor_suspected_welded_since=None,
            _contactor_fault_counts={},
            _contactor_fault_active_reason=None,
            _contactor_fault_active_since=None,
            _contactor_lockout_reason="",
            _contactor_lockout_source="",
            _contactor_lockout_at=None,
            _charger_backend=object(),
            _last_charger_state_at=100.0,
            _last_charger_state_status="charging",
            _last_charger_state_power_w=0.0,
            _last_charger_state_actual_current_amps=0.0,
            charging_threshold_watts=100.0,
            min_current=6.0,
            auto_shelly_soft_fail_seconds=10.0,
            auto_contactor_fault_latch_count=3,
            auto_contactor_fault_latch_seconds=15.0,
        )
        controller = UpdateCycleController(
            service,
            _phase_values,
            lambda reason: {
                "contactor-suspected-open": 30,
                "contactor-lockout-open": 32,
            }.get(reason, 99),
        )

        self.assertIsNone(
            controller.components.relay.foundation.health.switch_feedback_health_override(
                service,
                True,
                True,
                100.0,
                power=0.0,
                current=0.0,
                pm_confirmed=True,
            )
        )
        service._last_charger_state_at = 110.0
        sync_readback_test_service(service)
        self.assertEqual(
            controller.components.relay.foundation.health.switch_feedback_health_override(
                service,
                True,
                True,
                110.0,
                power=0.0,
                current=0.0,
                pm_confirmed=True,
            ),
            "contactor-suspected-open",
        )
        self.assertEqual(service._contactor_fault_counts, {"contactor-suspected-open": 1})

        service._last_charger_state_at = 126.0
        sync_readback_test_service(service)
        self.assertEqual(
            controller.components.relay.foundation.health.switch_feedback_health_override(
                service,
                True,
                True,
                126.0,
                power=0.0,
                current=0.0,
                pm_confirmed=True,
            ),
            "contactor-lockout-open",
        )
        self.assertEqual(service._contactor_lockout_reason, "contactor-suspected-open")
        self.assertEqual(service._contactor_lockout_source, "persistent")
        self.assertEqual(service._contactor_lockout_at, 126.0)

    def test_contactor_feedback_scenario_ready_but_no_power_does_not_false_positive_as_open_fault(self):
        service = SimpleNamespace(
            _last_switch_feedback_closed=None,
            _last_switch_interlock_ok=None,
            _last_switch_feedback_at=None,
            _contactor_suspected_open_since=None,
            _contactor_suspected_welded_since=None,
            _charger_backend=object(),
            _last_charger_state_at=100.0,
            _last_charger_state_status="ready",
            _last_charger_state_power_w=0.0,
            _last_charger_state_actual_current_amps=0.0,
            charging_threshold_watts=100.0,
            min_current=6.0,
            auto_shelly_soft_fail_seconds=10.0,
        )
        controller = UpdateCycleController(
            service,
            _phase_values,
            lambda reason: {"contactor-suspected-open": 30}.get(reason, 99),
        )

        self.assertIsNone(
            controller.components.relay.foundation.health.switch_feedback_health_override(
                service,
                True,
                True,
                100.0,
                power=0.0,
                current=0.0,
                pm_confirmed=True,
            )
        )
        self.assertIsNone(service._contactor_suspected_open_since)

        service._last_charger_state_at = 110.0
        sync_readback_test_service(service)
        self.assertIsNone(
            controller.components.relay.foundation.health.switch_feedback_health_override(
                service,
                True,
                True,
                110.0,
                power=0.0,
                current=0.0,
                pm_confirmed=True,
            )
        )
        self.assertIsNone(service._contactor_suspected_open_since)

# SPDX-License-Identifier: GPL-3.0-or-later
from tests.venus_evcharger_update_cycle_controller_support import *


class TestUpdateCycleControllerTrecenary(UpdateCycleControllerTestBase):
    def test_contactor_feedback_scenario_stuck_welded_escalates_from_suspicion_to_lockout(self):
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
        self.assertEqual(service._contactor_lockout_reason, "contactor-suspected-welded")
        self.assertEqual(service._contactor_lockout_source, "count-threshold")

    def test_contactor_feedback_scenario_stuck_open_escalates_from_suspicion_to_lockout(self):
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

    def test_apply_charger_current_target_prefers_stable_learned_current(self):
        charger_backend = SimpleNamespace(set_current=MagicMock())
        service = SimpleNamespace(
            _charger_backend=charger_backend,
            auto_shelly_soft_fail_seconds=10.0,
            _mark_failure=MagicMock(),
            _mark_recovery=MagicMock(),
            _warning_throttled=MagicMock(),
            virtual_set_current=16.0,
            min_current=6.0,
            max_current=16.0,
            learned_charge_power_state="stable",
            learned_charge_power_watts=2990.0,
            learned_charge_power_updated_at=95.0,
            learned_charge_power_phase="L1",
            learned_charge_power_voltage=230.0,
            phase="L1",
            voltage_mode="phase",
            auto_policy=_learning_policy(),
            _charger_target_current_amps=None,
            _charger_target_current_applied_at=None,
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        applied = controller.components.relay.foundation.targets.apply_current_target(service, True, 100.0, True)

        self.assertEqual(applied, 13.0)
        charger_backend.set_current.assert_called_once_with(13.0)
        self.assertEqual(service._charger_target_current_amps, 13.0)
        self.assertEqual(service._charger_target_current_applied_at, 100.0)

    def test_apply_charger_current_target_falls_back_to_virtual_current_and_skips_duplicate_write(self):
        charger_backend = SimpleNamespace(set_current=MagicMock())
        service = SimpleNamespace(
            _charger_backend=charger_backend,
            auto_shelly_soft_fail_seconds=10.0,
            _mark_failure=MagicMock(),
            _mark_recovery=MagicMock(),
            _warning_throttled=MagicMock(),
            virtual_set_current=11.0,
            min_current=6.0,
            max_current=16.0,
            learned_charge_power_state="learning",
            learned_charge_power_watts=2990.0,
            learned_charge_power_updated_at=95.0,
            learned_charge_power_phase="L1",
            learned_charge_power_voltage=230.0,
            phase="L1",
            voltage_mode="phase",
            auto_policy=_learning_policy(),
            _charger_target_current_amps=None,
            _charger_target_current_applied_at=None,
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        first = controller.components.relay.foundation.targets.apply_current_target(service, True, 100.0, True)
        second = controller.components.relay.foundation.targets.apply_current_target(service, True, 101.0, True)

        self.assertEqual(first, 11.0)
        self.assertEqual(second, 11.0)
        charger_backend.set_current.assert_called_once_with(11.0)
        self.assertEqual(service._charger_target_current_amps, 11.0)

    def test_apply_charger_current_target_uses_scheduled_night_current_during_scheduled_night_charge(self):
        charger_backend = SimpleNamespace(set_current=MagicMock())
        service = SimpleNamespace(
            _charger_backend=charger_backend,
            auto_shelly_soft_fail_seconds=10.0,
            _mark_failure=MagicMock(),
            _mark_recovery=MagicMock(),
            _warning_throttled=MagicMock(),
            virtual_mode=2,
            virtual_set_current=9.0,
            min_current=6.0,
            max_current=16.0,
            auto_month_windows={4: ((7, 30), (19, 30))},
            auto_scheduled_enabled_days="Mon,Tue,Wed,Thu,Fri",
            auto_scheduled_night_start_delay_seconds=3600.0,
            auto_scheduled_latest_end_time="06:30",
            auto_scheduled_night_current_amps=13.0,
            learned_charge_power_state="stable",
            learned_charge_power_watts=2990.0,
            learned_charge_power_updated_at=95.0,
            learned_charge_power_phase="L1",
            learned_charge_power_voltage=230.0,
            phase="L1",
            voltage_mode="phase",
            auto_policy=_learning_policy(),
            _charger_target_current_amps=None,
            _charger_target_current_applied_at=None,
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        applied = controller.components.relay.foundation.targets.apply_current_target(
            service,
            True,
            utc_timestamp(2026, 4, 20, 21, 0),
            True,
        )

        self.assertEqual(applied, 13.0)
        charger_backend.set_current.assert_called_once_with(13.0)
        self.assertEqual(service._charger_target_current_amps, 13.0)

    def test_apply_charger_current_target_marks_charger_failure_when_current_write_raises(self):
        charger_backend = SimpleNamespace(
            set_current=MagicMock(side_effect=ModbusSlaveOfflineError("Modbus slave 1 did not respond"))
        )
        service = SimpleNamespace(
            _charger_backend=charger_backend,
            auto_shelly_soft_fail_seconds=10.0,
            _mark_failure=MagicMock(),
            _mark_recovery=MagicMock(),
            _warning_throttled=MagicMock(),
            _delay_source_retry=MagicMock(),
            virtual_set_current=11.0,
            min_current=6.0,
            max_current=16.0,
            learned_charge_power_state="unknown",
            learned_charge_power_watts=None,
            learned_charge_power_updated_at=None,
            learned_charge_power_phase="L1",
            learned_charge_power_voltage=230.0,
            phase="L1",
            voltage_mode="phase",
            auto_dbus_backoff_base_seconds=5.0,
            auto_policy=_learning_policy(),
            _charger_target_current_amps=None,
            _charger_target_current_applied_at=None,
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        applied = controller.components.relay.foundation.targets.apply_current_target(service, True, 100.0, True)

        self.assertIsNone(applied)
        service._mark_failure.assert_called_once_with("charger")
        service._warning_throttled.assert_called_once()
        self.assertEqual(service._last_charger_transport_reason, "offline")
        self.assertEqual(service._charger_retry_reason, "offline")
        self.assertEqual(service._charger_retry_source, "current")
        self.assertEqual(service._charger_retry_until, 120.0)
        service._delay_source_retry.assert_called_once_with("charger", 100.0, 20.0)

    def test_apply_charger_current_target_skips_write_while_retry_backoff_is_active(self):
        charger_backend = SimpleNamespace(set_current=MagicMock())
        service = SimpleNamespace(
            _charger_backend=charger_backend,
            auto_shelly_soft_fail_seconds=10.0,
            _mark_failure=MagicMock(),
            _mark_recovery=MagicMock(),
            _warning_throttled=MagicMock(),
            virtual_set_current=11.0,
            min_current=6.0,
            max_current=16.0,
            learned_charge_power_state="unknown",
            learned_charge_power_watts=None,
            learned_charge_power_updated_at=None,
            learned_charge_power_phase="L1",
            learned_charge_power_voltage=230.0,
            phase="L1",
            voltage_mode="phase",
            auto_policy=_learning_policy(),
            _charger_target_current_amps=9.0,
            _charger_target_current_applied_at=95.0,
            _charger_retry_reason="offline",
            _charger_retry_source="current",
            _charger_retry_until=105.0,
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        applied = controller.components.relay.foundation.targets.apply_current_target(service, True, 100.0, True)

        self.assertEqual(applied, 9.0)
        charger_backend.set_current.assert_not_called()

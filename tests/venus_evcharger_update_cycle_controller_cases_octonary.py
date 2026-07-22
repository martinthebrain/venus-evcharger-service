# SPDX-License-Identifier: GPL-3.0-or-later
from tests.venus_evcharger_update_cycle_controller_support import *


class TestUpdateCycleControllerOctonary(UpdateCycleControllerTestBase):
    def test_update_learned_charge_power_keeps_previous_voltage_signature_when_no_voltage_is_available(self):
        service = SimpleNamespace(
            charging_started_at=50.0,
            learned_charge_power_watts=1900.0,
            learned_charge_power_updated_at=100.0,
            learned_charge_power_state="stable",
            learned_charge_power_learning_since=None,
            learned_charge_power_sample_count=3,
            learned_charge_power_phase="L1",
            learned_charge_power_voltage=229.0,
            learned_charge_power_signature_mismatch_sessions=0,
            learned_charge_power_signature_checked_session_started_at=50.0,
            auto_policy=_learning_policy(),
            phase="L1",
            max_current=16.0,
            _last_voltage=None,
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        self.assertTrue(controller.components.learning.update_learned_charge_power(True, 2, 1920.0, 0.0, 110.0))
        self.assertEqual(service.learned_charge_power_voltage, 229.0)

    def test_plausible_learning_power_max_uses_phase_voltage_for_three_phase_line_voltage(self):
        service = SimpleNamespace(
            phase="3P",
            voltage_mode="line",
            max_current=16.0,
            _last_voltage=400.0,
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        self.assertAlmostEqual(
            controller.components.learning._plausible_learning_power_max(400.0),
            16.0 * (400.0 / math.sqrt(3.0)) * 3.0 * 1.1,
            places=6,
        )

    def test_update_learned_charge_power_rejects_spike_for_three_phase_line_voltage(self):
        service = SimpleNamespace(
            charging_started_at=50.0,
            learned_charge_power_watts=None,
            learned_charge_power_updated_at=None,
            learned_charge_power_state="unknown",
            learned_charge_power_learning_since=None,
            learned_charge_power_sample_count=0,
            learned_charge_power_phase=None,
            learned_charge_power_voltage=None,
            learned_charge_power_signature_mismatch_sessions=0,
            learned_charge_power_signature_checked_session_started_at=None,
            auto_policy=_learning_policy(),
            phase="3P",
            voltage_mode="line",
            max_current=16.0,
            _last_voltage=400.0,
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        self.assertFalse(controller.components.learning.update_learned_charge_power(True, 2, 15000.0, 400.0, 100.0))
        self.assertIsNone(service.learned_charge_power_watts)
        self.assertEqual(service.learned_charge_power_state, "unknown")

    def test_apply_startup_manual_target_returns_unchanged_when_relay_already_matches(self):
        service = SimpleNamespace(
            _startup_manual_target=True,
            virtual_mode=0,
            auto_shelly_soft_fail_seconds=10.0,
            _mode_uses_auto_logic=lambda mode: int(mode) in (1, 2),
            _queue_relay_command=MagicMock(),
            _mark_failure=MagicMock(),
            _warning_throttled=MagicMock(),
        )

        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))
        pm_status = {"output": True, "apower": 1200.0, "current": 5.2}

        updated = controller.components.state.apply_startup_manual_target(pm_status, 123.0)

        self.assertIs(updated, pm_status)
        service._queue_relay_command.assert_not_called()
        self.assertIsNone(service._startup_manual_target)

    def test_apply_startup_manual_target_queues_requested_state(self):
        published_pm_status = {"output": False, "apower": 0.0, "current": 0.0}
        service = SimpleNamespace(
            _startup_manual_target=False,
            virtual_mode=0,
            auto_shelly_soft_fail_seconds=10.0,
            _mode_uses_auto_logic=lambda mode: int(mode) in (1, 2),
            _queue_relay_command=MagicMock(),
            _publish_local_pm_status=MagicMock(return_value=published_pm_status),
            _mark_failure=MagicMock(),
            _warning_throttled=MagicMock(),
        )

        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))
        updated = controller.components.state.apply_startup_manual_target(
            {"output": True, "apower": 1200.0, "current": 5.2},
            123.0,
        )

        service._queue_relay_command.assert_called_once_with(False, 123.0)
        service._publish_local_pm_status.assert_called_once_with(False, 123.0)
        self.assertIsNone(service._startup_manual_target)
        self.assertIs(updated, published_pm_status)

    def test_apply_startup_manual_target_uses_native_charger_backend_when_available(self):
        published_pm_status = {"output": False, "apower": 0.0, "current": 0.0}
        charger_backend = SimpleNamespace(set_enabled=MagicMock())
        service = SimpleNamespace(
            _startup_manual_target=False,
            _charger_backend=charger_backend,
            virtual_mode=0,
            auto_shelly_soft_fail_seconds=10.0,
            _mode_uses_auto_logic=lambda mode: int(mode) in (1, 2),
            _queue_relay_command=MagicMock(),
            _publish_local_pm_status=MagicMock(return_value=published_pm_status),
            _mark_failure=MagicMock(),
            _mark_recovery=MagicMock(),
            _warning_throttled=MagicMock(),
        )

        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))
        updated = controller.components.state.apply_startup_manual_target(
            {"output": True, "apower": 1200.0, "current": 5.2},
            123.0,
        )

        charger_backend.set_enabled.assert_called_once_with(False)
        service._queue_relay_command.assert_not_called()
        service._publish_local_pm_status.assert_called_once_with(False, 123.0)
        self.assertIsNone(service._startup_manual_target)
        self.assertIs(updated, published_pm_status)

    def test_apply_startup_manual_target_keeps_pending_target_while_charger_retry_is_active(self):
        charger_backend = SimpleNamespace(set_enabled=MagicMock())
        service = SimpleNamespace(
            _startup_manual_target=False,
            _charger_backend=charger_backend,
            _charger_retry_reason="offline",
            _charger_retry_source="enable",
            _charger_retry_until=130.0,
            virtual_mode=0,
            auto_shelly_soft_fail_seconds=10.0,
            _mode_uses_auto_logic=lambda mode: int(mode) in (1, 2),
            _queue_relay_command=MagicMock(),
            _publish_local_pm_status=MagicMock(),
            _mark_failure=MagicMock(),
            _mark_recovery=MagicMock(),
            _warning_throttled=MagicMock(),
        )

        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))
        pm_status = {"output": True, "apower": 1200.0, "current": 5.2}

        updated = controller.components.state.apply_startup_manual_target(pm_status, 123.0)

        charger_backend.set_enabled.assert_not_called()
        service._publish_local_pm_status.assert_not_called()
        self.assertIs(updated, pm_status)
        self.assertIs(service._startup_manual_target, False)

    def test_apply_startup_manual_target_uses_canonical_local_pm_status_runtime_port(self):
        service = SimpleNamespace(
            _startup_manual_target=False,
            virtual_mode=0,
            auto_shelly_soft_fail_seconds=10.0,
            _mode_uses_auto_logic=lambda mode: int(mode) in (1, 2),
            _queue_relay_command=MagicMock(),
            _mark_failure=MagicMock(),
            _warning_throttled=MagicMock(),
            _publish_local_pm_status=MagicMock(
                return_value={"output": False, "apower": 0.0, "current": 0.0}
            ),
        )

        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))
        updated = controller.components.state.apply_startup_manual_target(
            {"output": True, "apower": 1200.0, "current": 5.2},
            123.0,
        )

        service._queue_relay_command.assert_called_once_with(False, 123.0)
        service._publish_local_pm_status.assert_called_once_with(False, 123.0)
        self.assertIsNone(service._startup_manual_target)
        self.assertFalse(updated["output"])
        self.assertEqual(updated["apower"], 0.0)
        self.assertEqual(updated["current"], 0.0)

    def test_apply_startup_manual_target_marks_failure_when_queueing_raises(self):
        service = SimpleNamespace(
            _startup_manual_target=False,
            virtual_mode=0,
            auto_shelly_soft_fail_seconds=10.0,
            _mode_uses_auto_logic=lambda mode: int(mode) in (1, 2),
            _queue_relay_command=MagicMock(side_effect=RuntimeError("boom")),
            _mark_failure=MagicMock(),
            _warning_throttled=MagicMock(),
        )

        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))
        pm_status = {"output": True, "apower": 1200.0, "current": 5.2}

        updated = controller.components.state.apply_startup_manual_target(pm_status, 123.0)

        self.assertIs(updated, pm_status)
        self.assertIs(service._startup_manual_target, False)
        service._mark_failure.assert_called_once_with("shelly")
        service._warning_throttled.assert_called_once()

    def test_apply_startup_manual_target_marks_charger_failure_when_native_backend_raises(self):
        charger_backend = SimpleNamespace(set_enabled=MagicMock(side_effect=RuntimeError("boom")))
        service = SimpleNamespace(
            _startup_manual_target=False,
            _charger_backend=charger_backend,
            virtual_mode=0,
            auto_shelly_soft_fail_seconds=10.0,
            _mode_uses_auto_logic=lambda mode: int(mode) in (1, 2),
            _queue_relay_command=MagicMock(),
            _mark_failure=MagicMock(),
            _warning_throttled=MagicMock(),
        )

        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))
        pm_status = {"output": True, "apower": 1200.0, "current": 5.2}

        updated = controller.components.state.apply_startup_manual_target(pm_status, 123.0)

        self.assertIs(updated, pm_status)
        self.assertIs(service._startup_manual_target, False)
        service._mark_failure.assert_called_once_with("charger")
        service._warning_throttled.assert_called_once()
        service._queue_relay_command.assert_not_called()

    def test_apply_startup_manual_target_falls_back_when_local_placeholder_publish_fails(self):
        service = SimpleNamespace(
            _startup_manual_target=False,
            virtual_mode=0,
            auto_shelly_soft_fail_seconds=10.0,
            _mode_uses_auto_logic=lambda mode: int(mode) in (1, 2),
            _queue_relay_command=MagicMock(),
            _publish_local_pm_status=MagicMock(side_effect=RuntimeError("publish failed")),
            _mark_failure=MagicMock(),
            _warning_throttled=MagicMock(),
        )

        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))
        updated = controller.components.state.apply_startup_manual_target(
            {"output": True, "apower": 1200.0, "current": 5.2},
            123.0,
        )

        service._queue_relay_command.assert_called_once_with(False, 123.0)
        service._publish_local_pm_status.assert_called_once_with(False, 123.0)
        self.assertIsNone(service._startup_manual_target)
        self.assertFalse(updated["output"])
        self.assertEqual(updated["apower"], 0.0)
        self.assertEqual(updated["current"], 0.0)
        service._mark_failure.assert_not_called()
        service._warning_throttled.assert_called_once()

    def test_resolve_auto_inputs_uses_recent_cache_and_counts_hit(self):
        service = SimpleNamespace(
            auto_input_cache_seconds=120.0,
            auto_pv_poll_interval_seconds=2.0,
            auto_grid_poll_interval_seconds=2.0,
            auto_battery_poll_interval_seconds=10.0,
            auto_input_validation_poll_seconds=30.0,
            dbus_gateway_max_age_seconds=10.0,
            _auto_cached_inputs_used=False,
            _error_state={"cache_hits": 0},
            _last_pv_value=2300.0,
            _last_pv_at=98.0,
            _last_grid_value=-1700.0,
            _last_grid_at=97.0,
            _last_battery_soc_value=61.0,
            _last_battery_soc_at=90.0,
        )

        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))
        pv_power, battery_soc, grid_power = controller.components.inputs.resolve_auto_inputs(
            {
                "captured_at": 100.0,
                "pv_power": None,
                "grid_power": None,
                "battery_soc": None,
            },
            100.0,
            True,
        )

        self.assertEqual(pv_power, 2300.0)
        self.assertEqual(battery_soc, 61.0)
        self.assertEqual(grid_power, -1700.0)
        self.assertTrue(service._auto_cached_inputs_used)
        self.assertEqual(service._error_state["cache_hits"], 1)

    def test_resolve_auto_inputs_rejects_stale_per_source_values_before_cache_fallback(self):
        service = SimpleNamespace(
            auto_input_cache_seconds=20.0,
            auto_pv_poll_interval_seconds=2.0,
            auto_grid_poll_interval_seconds=2.0,
            auto_battery_poll_interval_seconds=10.0,
            auto_input_validation_poll_seconds=30.0,
            dbus_gateway_max_age_seconds=10.0,
            _auto_cached_inputs_used=False,
            _error_state={"cache_hits": 0},
            _last_pv_value=2100.0,
            _last_pv_at=98.0,
            _last_grid_value=-1400.0,
            _last_grid_at=92.0,
            _last_battery_soc_value=None,
            _last_battery_soc_at=None,
        )

        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))
        pv_power, battery_soc, grid_power = controller.components.inputs.resolve_auto_inputs(
            {
                "captured_at": 100.0,
                "pv_power": 2300.0,
                "pv_captured_at": 50.0,
                "grid_power": -1500.0,
                "grid_captured_at": 96.0,
                "battery_soc": 55.0,
                "battery_captured_at": 60.0,
            },
            100.0,
            True,
        )

        self.assertEqual(pv_power, 2100.0)
        self.assertIsNone(battery_soc)
        self.assertEqual(grid_power, -1500.0)
        self.assertTrue(service._auto_cached_inputs_used)
        self.assertEqual(service._error_state["cache_hits"], 1)

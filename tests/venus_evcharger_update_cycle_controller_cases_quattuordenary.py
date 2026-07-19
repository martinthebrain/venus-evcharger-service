# SPDX-License-Identifier: GPL-3.0-or-later
from tests.venus_evcharger_update_cycle_controller_support import *


class TestUpdateCycleControllerQuattuordenary(UpdateCycleControllerTestBase):
    def test_charger_current_scenario_offline_backoff_then_retry_after_window(self):
        charger_backend = SimpleNamespace(
            set_current=MagicMock(
                side_effect=[
                    ModbusSlaveOfflineError("Modbus slave 1 did not respond"),
                    None,
                ]
            )
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
            _charger_retry_reason=None,
            _charger_retry_source=None,
            _charger_retry_until=None,
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        first = controller.components.relay.foundation.targets.apply_current_target(service, True, 100.0, True)
        second = controller.components.relay.foundation.targets.apply_current_target(service, True, 105.0, True)
        third = controller.components.relay.foundation.targets.apply_current_target(service, True, 121.0, True)

        self.assertIsNone(first)
        self.assertIsNone(second)
        self.assertEqual(third, 11.0)
        self.assertEqual(charger_backend.set_current.call_count, 2)
        charger_backend.set_current.assert_called_with(11.0)
        self.assertEqual(service._charger_target_current_amps, 11.0)
        self.assertEqual(service._charger_target_current_applied_at, 121.0)
        self.assertIsNone(service._charger_retry_reason)
        self.assertIsNone(service._charger_retry_source)
        self.assertIsNone(service._charger_retry_until)

    def test_charger_current_failure_without_transport_reason_still_marks_failure(self):
        service = SimpleNamespace(
            auto_shelly_soft_fail_seconds=10.0,
            _mark_failure=MagicMock(),
            _warning_throttled=MagicMock(),
        )

        relay_components_for_test(service).foundation.targets._handle_current_target_failure(
            service, RuntimeError("boom"), now=100.0
        )

        service._mark_failure.assert_called_once_with("charger")
        service._warning_throttled.assert_called_once()

    def test_remember_charger_retry_uses_source_retry_dict_without_delay_helper(self):
        service = SimpleNamespace(
            _source_retry_after={},
            auto_dbus_backoff_base_seconds=5.0,
        )

        ChargerTransportTracker.remember_retry(service, "offline", "read", 100.0)

        self.assertEqual(service._source_retry_after["charger"], 120.0)

    def test_remember_charger_retry_without_delay_helper_and_without_retry_dict_still_sets_runtime_state(self):
        service = SimpleNamespace(
            _source_retry_after=None,
            auto_dbus_backoff_base_seconds=5.0,
        )

        ChargerTransportTracker.remember_retry(service, "offline", "read", 100.0)

        self.assertEqual(service._charger_retry_reason, "offline")
        self.assertEqual(service._charger_retry_source, "read")
        self.assertEqual(service._charger_retry_until, 120.0)

    def test_relay_sync_confirmed_match_clears_tracking_without_recovery_log_when_not_failed(self):
        service = SimpleNamespace(
            _relay_sync_expected_state=True,
            _relay_sync_requested_at=90.0,
            _relay_sync_deadline_at=95.0,
            _relay_sync_failure_reported=False,
            _mark_recovery=MagicMock(),
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        self.assertTrue(controller.components.relay.foundation.telemetry._relay_sync_confirmed_match(service, True, True, True))
        service._mark_recovery.assert_not_called()
        self.assertIsNone(service._relay_sync_expected_state)

    def test_apply_relay_decision_marks_charger_failure_when_native_backend_raises(self):
        charger_backend = SimpleNamespace(set_enabled=MagicMock(side_effect=RuntimeError("boom")))
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
            1200.0,
            5.2,
            100.0,
            False,
        )
        self.assertEqual((relay_on, power, current, confirmed), (False, 1200.0, 5.2, True))
        service._mark_failure.assert_called_once_with("charger")
        service._warning_throttled.assert_called_once()
        service._queue_relay_command.assert_not_called()

    def test_apply_relay_decision_skips_native_enable_while_charger_retry_is_active(self):
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
            _charger_retry_reason="offline",
            _charger_retry_source="enable",
            _charger_retry_until=105.0,
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        relay_on, power, current, confirmed = controller.components.relay.status.apply_relay_decision(
            service,
            True,
            False,
            {"output": False, "_pm_confirmed": True},
            1200.0,
            5.2,
            100.0,
            False,
        )

        self.assertEqual((relay_on, power, current, confirmed), (False, 1200.0, 5.2, True))
        charger_backend.set_enabled.assert_not_called()
        service._queue_relay_command.assert_not_called()
        service._mark_failure.assert_not_called()
        service._warning_throttled.assert_not_called()

    def test_apply_relay_decision_does_not_requeue_same_target_while_confirmation_is_pending(self):
        service = SimpleNamespace(
            _last_auto_metrics={"surplus": None, "grid": None, "soc": None},
            auto_audit_log=False,
            auto_shelly_soft_fail_seconds=10.0,
            _queue_relay_command=MagicMock(),
            _mark_failure=MagicMock(),
            _mark_recovery=MagicMock(),
            _warning_throttled=MagicMock(),
            _publish_local_pm_status=MagicMock(),
            _relay_sync_expected_state=True,
            _relay_sync_requested_at=90.0,
            _relay_sync_deadline_at=95.0,
            _relay_sync_failure_reported=False,
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        relay_on, power, current, confirmed = controller.components.relay.status.apply_relay_decision(
            service,
            True,
            False,
            {"output": False, "_pm_confirmed": True},
            1200.0,
            5.2,
            100.0,
            False,
        )

        self.assertEqual((relay_on, power, current, confirmed), (False, 1200.0, 5.2, True))

    def test_apply_relay_decision_keeps_in_flight_transition_when_placeholder_publish_fails(self):
        service = SimpleNamespace(
            _last_auto_metrics={"surplus": None, "grid": None, "soc": None},
            _last_health_reason="init",
            auto_audit_log=False,
            auto_shelly_soft_fail_seconds=10.0,
            relay_sync_timeout_seconds=2.0,
            _queue_relay_command=MagicMock(),
            _mark_failure=MagicMock(),
            _mark_recovery=MagicMock(),
            _warning_throttled=MagicMock(),
            _publish_local_pm_status=MagicMock(side_effect=RuntimeError("publish failed")),
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        relay_on, power, current, confirmed = controller.components.relay.status.apply_relay_decision(
            service,
            True,
            False,
            {"output": False, "apower": 0.0, "current": 0.0, "_pm_confirmed": True},
            0.0,
            0.0,
            100.0,
            False,
        )

        self.assertEqual((relay_on, power, current, confirmed), (True, 0.0, 0.0, False))
        service._queue_relay_command.assert_called_once_with(True, 100.0)
        service._warning_throttled.assert_called_once()

    def test_relay_sync_health_override_reports_mismatch_and_clears_tracking_after_timeout(self):
        service = SimpleNamespace(
            _relay_sync_expected_state=True,
            _relay_sync_requested_at=100.0,
            _relay_sync_deadline_at=104.0,
            _relay_sync_failure_reported=False,
            _mark_failure=MagicMock(),
            _mark_recovery=MagicMock(),
            _warning_throttled=MagicMock(),
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        self.assertIsNone(
            controller.components.relay.foundation.telemetry.relay_sync_health_override(
                service, True, False, 102.0
            )
        )
        self.assertFalse(service._relay_sync_failure_reported)
        self.assertEqual(
            controller.components.relay.foundation.telemetry.relay_sync_health_override(
                service, False, True, 102.0
            ),
            "command-mismatch",
        )
        self.assertFalse(service._relay_sync_failure_reported)
        service._mark_failure.assert_not_called()

        self.assertEqual(
            controller.components.relay.foundation.telemetry.relay_sync_health_override(
                service, False, False, 105.0
            ),
            "relay-sync-failed",
        )
        service._mark_failure.assert_called_once_with("shelly")
        service._warning_throttled.assert_called_once()
        self.assertIsNone(service._relay_sync_expected_state)
        self.assertIsNone(service._relay_sync_requested_at)
        self.assertIsNone(service._relay_sync_deadline_at)
        self.assertFalse(service._relay_sync_failure_reported)

        service._mark_failure.reset_mock()
        service._warning_throttled.reset_mock()
        self.assertIsNone(
            controller.components.relay.foundation.telemetry.relay_sync_health_override(
                service, False, False, 106.0
            )
        )
        service._mark_failure.assert_not_called()
        service._warning_throttled.assert_not_called()

        service._queue_relay_command = MagicMock()
        service._publish_local_pm_status = MagicMock()
        relay_on, power, current, confirmed = controller.components.relay.status.apply_relay_decision(
            service,
            True,
            False,
            {"output": False, "_pm_confirmed": True},
            1200.0,
            5.2,
            106.5,
            False,
        )
        self.assertEqual((relay_on, power, current, confirmed), (True, 0.0, 0.0, False))
        service._queue_relay_command.assert_called_once_with(True, 106.5)
        service._publish_local_pm_status.assert_called_once_with(True, 106.5)
        service._mark_recovery.assert_not_called()

    def test_relay_sync_health_override_marks_recovery_after_confirmed_match(self):
        service = SimpleNamespace(
            _relay_sync_expected_state=True,
            _relay_sync_requested_at=100.0,
            _relay_sync_deadline_at=104.0,
            _relay_sync_failure_reported=True,
            _mark_failure=MagicMock(),
            _mark_recovery=MagicMock(),
            _warning_throttled=MagicMock(),
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        self.assertIsNone(
            controller.components.relay.foundation.telemetry.relay_sync_health_override(
                service, True, True, 103.0
            )
        )

        service._mark_recovery.assert_called_once_with("shelly", "Shelly relay confirmation recovered")
        self.assertIsNone(service._relay_sync_expected_state)
        self.assertIsNone(service._relay_sync_requested_at)
        self.assertIsNone(service._relay_sync_deadline_at)
        self.assertFalse(service._relay_sync_failure_reported)

    def test_update_overrides_health_when_relay_confirmation_times_out(self):
        service = SimpleNamespace(
            time_now=MagicMock(return_value=105.0),
            _state_summary=lambda: "state",
            _watchdog_recover=MagicMock(),
            _ensure_auto_input_helper_process=MagicMock(),
            _refresh_auto_input_snapshot=MagicMock(),
            _get_worker_snapshot=MagicMock(
                return_value={
                    "pm_status": {
                        "output": False,
                        "apower": 0.0,
                        "voltage": 230.0,
                        "current": 0.0,
                        "aenergy": {"total": 1.0},
                    },
                    "pm_confirmed": True,
                }
            ),
            _last_pm_status=None,
            _last_pm_status_at=None,
            _last_pm_status_confirmed=False,
            _safe_float=lambda value, default=0.0: float(value) if value is not None else default,
            virtual_mode=1,
            phase="L1",
            voltage_mode="phase",
            _mode_uses_auto_logic=lambda mode: int(mode) in (1, 2),
            _auto_decide_relay=MagicMock(return_value=True),
            _publish_live_measurements=MagicMock(return_value=False),
            _publish_energy_time_measurements=MagicMock(return_value=False),
            _publish_config_paths=MagicMock(return_value=False),
            _publish_diagnostic_paths=MagicMock(return_value=False),
            _save_runtime_state=MagicMock(),
            _ensure_observability_state=MagicMock(),
            _set_health=MagicMock(),
            _last_health_reason="waiting",
            _last_health_code=0,
            charging_started_at=None,
            energy_at_start=0.0,
            last_status=0,
            virtual_enable=1,
            _dbusservice={"/Ac/Power": 0.0},
            service_name="com.victronenergy.evcharger.http_60",
            last_update=0.0,
            _dbus_publish_state={},
            _dbus_live_publish_interval_seconds=1.0,
            _dbus_slow_publish_interval_seconds=5.0,
            _last_voltage=230.0,
            virtual_startstop=0,
            charging_threshold_watts=100.0,
            idle_status=1,
            _last_successful_update_at=None,
            _last_recovery_attempt_at=None,
            _error_state={"cache_hits": 0},
            _last_pv_value=None,
            _last_pv_at=None,
            _last_grid_value=None,
            _last_grid_at=None,
            _last_battery_soc_value=None,
            _last_battery_soc_at=None,
            auto_input_cache_seconds=0.0,
            auto_shelly_soft_fail_seconds=10.0,
            auto_audit_log=False,
            _last_auto_metrics={"surplus": None, "grid": None, "soc": None},
            learned_charge_power_watts=None,
            learned_charge_power_updated_at=None,
            learned_charge_power_state="unknown",
            learned_charge_power_learning_since=None,
            learned_charge_power_sample_count=0,
            learned_charge_power_phase=None,
            learned_charge_power_voltage=None,
            learned_charge_power_signature_mismatch_sessions=0,
            learned_charge_power_signature_checked_session_started_at=None,
            auto_policy=_learning_policy(enabled=False),
            max_current=16.0,
            _relay_sync_expected_state=True,
            _relay_sync_requested_at=100.0,
            _relay_sync_deadline_at=104.0,
            _relay_sync_failure_reported=False,
            _mark_failure=MagicMock(),
            _mark_recovery=MagicMock(),
            _warning_throttled=MagicMock(),
            _queue_relay_command=MagicMock(),
            _publish_local_pm_status=MagicMock(),
            _bump_update_index=MagicMock(),
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0, "relay-sync-failed": 25}.get(reason, 99))

        self.assertTrue(controller.update())

        service._set_health.assert_any_call("relay-sync-failed", cached=False)
        service._mark_failure.assert_called_once_with("shelly")
        service._warning_throttled.assert_called_once()

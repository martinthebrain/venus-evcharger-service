# SPDX-License-Identifier: GPL-3.0-or-later
from tests.venus_evcharger_update_cycle_controller_support import *


class TestUpdateCycleControllerDenary(UpdateCycleControllerTestBase):
    def test_prepare_update_cycle_runs_runtime_hooks_without_extra_discovery_process(self):
        configured_service = SimpleNamespace(
            topology_configured=True,
            _start_io_worker=MagicMock(),
            _watchdog_recover=MagicMock(),
            _ensure_auto_input_helper_process=MagicMock(),
            _refresh_auto_input_snapshot=MagicMock(),
            _get_worker_snapshot=MagicMock(return_value={"ok": True}),
        )

        install_update_cycle_roles(configured_service)
        self.assertEqual(UpdateStateController.prepare_update_cycle(configured_service, 100.0), {"ok": True})

        configured_service._start_io_worker.assert_called_once_with()
        configured_service._watchdog_recover.assert_called_once_with(100.0)

        unconfigured_service = SimpleNamespace(
            topology_configured=False,
            _start_io_worker=MagicMock(),
            _watchdog_recover=MagicMock(),
            _ensure_auto_input_helper_process=MagicMock(),
            _refresh_auto_input_snapshot=MagicMock(),
            _get_worker_snapshot=MagicMock(return_value={}),
        )

        install_update_cycle_roles(unconfigured_service)
        self.assertEqual(UpdateStateController.prepare_update_cycle(unconfigured_service, 101.0), {})

        unconfigured_service._start_io_worker.assert_not_called()
        unconfigured_service._watchdog_recover.assert_called_once_with(101.0)

    def test_publish_online_update_prefers_fresh_native_charger_measurements(self):
        service = SimpleNamespace(
            phase="L1",
            voltage_mode="phase",
            _charger_backend=SimpleNamespace(),
            _last_charger_state_actual_current_amps=12.3,
            _last_charger_state_power_w=2830.0,
            _last_charger_state_energy_kwh=7.25,
            _last_charger_state_at=200.0,
            auto_shelly_soft_fail_seconds=10.0,
            _publish_live_measurements=MagicMock(return_value=False),
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))
        controller.components.state.update_virtual_state = MagicMock(return_value=False)

        changed = controller.components.relay.status.publish_online_update(
            service,
            {
                "output": True,
                "apower": 1200.0,
                "current": 5.2,
                "aenergy": {"total": 1000.0},
            },
            2,
            1.0,
            True,
            1200.0,
            230.0,
            200.0,
        )

        self.assertFalse(changed)
        self.assertEqual(service._publish_live_measurements.call_args.args[0], 2830.0)
        self.assertAlmostEqual(service._publish_live_measurements.call_args.args[2], 12.3)
        controller.components.state.update_virtual_state.assert_called_once_with(2, 7.25, True)

    def test_publish_offline_update_uses_backend_phase_metadata_for_display(self):
        service = SimpleNamespace(
            time_now=MagicMock(return_value=200.0),
            _last_voltage=230.0,
            _last_confirmed_pm_status={
                "output": True,
                "_phase_selection": "P1",
                "_phase_powers_w": (0.0, 0.0, 0.0),
                "_phase_currents_a": (0.0, 0.0, 0.0),
            },
            _last_confirmed_pm_status_at=199.0,
            _worker_poll_interval_seconds=1.0,
            relay_sync_timeout_seconds=2.0,
            virtual_startstop=0,
            phase="L1",
            voltage_mode="phase",
            _set_health=MagicMock(),
            _publish_live_measurements=MagicMock(return_value=False),
            _publish_energy_time_measurements=MagicMock(return_value=False),
            _publish_config_paths=MagicMock(return_value=False),
            _publish_diagnostic_paths=MagicMock(return_value=False),
            _publish_dbus_field=MagicMock(return_value=False),
            _save_runtime_state=MagicMock(),
            _ensure_observability_state=MagicMock(),
            _publish_companion_dbus_bridge=MagicMock(),
            _mode_uses_auto_logic=lambda mode: int(mode) in (1, 2),
            _last_health_reason="init",
            _last_health_code=0,
            charging_started_at=None,
            energy_at_start=0.0,
            last_status=0,
            virtual_mode=0,
            virtual_enable=1,
            _dbusservice={"/Ac/Power": 0.0},
            service_name="svc",
            last_update=0.0,
            _dbus_publish_state={},
            _dbus_live_publish_interval_seconds=1.0,
            _dbus_slow_publish_interval_seconds=5.0,
        )

        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        self.assertTrue(controller.components.offline.publish_offline_update(200.0))
        self.assertEqual(
            service._publish_live_measurements.call_args.args[3],
            {
                "L1": {"power": 0.0, "voltage": 230.0, "current": 0.0},
                "L2": {"power": 0.0, "voltage": 230.0, "current": 0.0},
                "L3": {"power": 0.0, "voltage": 230.0, "current": 0.0},
            },
        )
        service._publish_companion_dbus_bridge.assert_called_once_with(200.0)

    def test_cached_input_from_service_rejects_future_cached_timestamp(self):
        service = SimpleNamespace(_last_pv_value=2400.0, _last_pv_at=102.5)

        self.assertEqual(
            InputCacheResolver._cached_input_from_service(
                service,
                "_last_pv_value",
                "_last_pv_at",
                100.0,
                20.0,
            ),
            (None, False),
        )

    def test_update_cycle_helpers_cover_cached_pm_status_session_branches_and_logging(self):
        service = SimpleNamespace(
            charging_started_at=None,
            energy_at_start=1.5,
            virtual_mode=1,
            virtual_enable=1,
            phase="3P",
            _mode_uses_auto_logic=lambda mode: int(mode) in (1, 2),
            _last_pm_status={"output": True},
            _last_pm_status_at=95.0,
            _last_pm_status_confirmed=True,
            auto_shelly_soft_fail_seconds=10.0,
            _last_auto_metrics={"surplus": 2500.0, "grid": -2200.0, "soc": 63.0},
            _last_health_reason="running",
            auto_audit_log=True,
            _queue_relay_command=MagicMock(),
            _mark_failure=MagicMock(),
            _mark_recovery=MagicMock(),
            _warning_throttled=MagicMock(),
            _publish_local_pm_status=MagicMock(),
            _relay_sync_expected_state=None,
            _relay_sync_requested_at=None,
            _relay_sync_deadline_at=None,
            _relay_sync_failure_reported=False,
            charging_threshold_watts=1500.0,
            idle_status=1,
            time_now=MagicMock(return_value=123.0),
            virtual_startstop=1,
            service_name="com.victronenergy.evcharger.http_60",
            _accepted_publication_fields={"ac_power_w": 321.0},
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        charging_time, session_energy = controller.components.state.session_state_from_status(service, 1, 2.0, True, 100.0)
        self.assertEqual((charging_time, session_energy), (0, 0.0))
        self.assertIsNone(service.charging_started_at)
        self.assertEqual(service.energy_at_start, 2.0)

        self.assertEqual(
            controller.components.state.phase_energies_for_total(service, 6.0),
            {"L1": 2.0, "L2": 2.0, "L3": 2.0},
        )

        self.assertEqual(
            controller.components.pm_snapshots.resolve_pm_status_for_update(service, {"pm_status": None}, 100.0),
            {"output": True, "_pm_confirmed": True},
        )

        confirmed_pm_status = controller.components.pm_snapshots.resolve_pm_status_for_update(
            service,
            {"pm_status": {"output": False}, "pm_confirmed": True, "pm_captured_at": 101.0},
            101.0,
        )
        self.assertEqual(confirmed_pm_status, {"output": False, "_pm_confirmed": True})
        self.assertTrue(service._last_pm_status_confirmed)

        with patch("venus_evcharger.update.controller.logging.info") as info_mock:
            controller.components.relay.foundation.telemetry.log_auto_relay_change(service, True)
            controller.sign_of_life()

        self.assertEqual(info_mock.call_count, 2)

        relay_on, power, current, confirmed = controller.components.relay.status.apply_relay_decision(
            service,
            False,
            True,
            {"output": True, "apower": 1200.0, "current": 5.2, "_pm_confirmed": True},
            1200.0,
            5.2,
            123.0,
            True,
        )
        self.assertEqual((relay_on, power, current, confirmed), (False, 0.0, 0.0, False))
        service._publish_local_pm_status.assert_called_once_with(False, 123.0)

    def test_apply_relay_decision_and_update_cover_failure_and_warning_paths(self):
        service = SimpleNamespace(
            _last_auto_metrics={"surplus": None, "grid": None, "soc": None},
            _last_health_reason="init",
            auto_audit_log=False,
            auto_shelly_soft_fail_seconds=10.0,
            _queue_relay_command=MagicMock(side_effect=RuntimeError("boom")),
            _mark_failure=MagicMock(),
            _mark_recovery=MagicMock(),
            _warning_throttled=MagicMock(),
            _publish_local_pm_status=MagicMock(),
            time_now=MagicMock(return_value=100.0),
            _state_summary=lambda: "state",
            _watchdog_recover=MagicMock(),
            _ensure_auto_input_helper_process=MagicMock(),
            _refresh_auto_input_snapshot=MagicMock(),
            _get_worker_snapshot=MagicMock(return_value={"pm_status": {"output": False}}),
            _last_pm_status=None,
            _last_pm_status_at=None,
            _last_pm_status_confirmed=False,
            _safe_float=lambda value, default=0.0: float(value) if value is not None else default,
            virtual_mode=1,
            phase="L1",
            voltage_mode="line",
            _mode_uses_auto_logic=lambda mode: int(mode) in (1, 2),
            _auto_decide_relay=MagicMock(side_effect=RuntimeError("auto failed")),
            _publish_live_measurements=MagicMock(return_value=False),
            _publish_energy_time_measurements=MagicMock(return_value=False),
            _publish_config_paths=MagicMock(return_value=False),
            _publish_diagnostic_paths=MagicMock(return_value=False),
            _save_runtime_state=MagicMock(),
            _ensure_observability_state=MagicMock(),
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
            charging_threshold_watts=1500.0,
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
            _relay_sync_expected_state=None,
            _relay_sync_requested_at=None,
            _relay_sync_deadline_at=None,
            _relay_sync_failure_reported=False,
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        relay_on, power, current, confirmed = controller.components.relay.status.apply_relay_decision(
            service,
            False,
            True,
            {"output": True, "apower": 1200.0, "current": 5.2, "_pm_confirmed": True},
            1200.0,
            5.2,
            100.0,
            False,
        )
        self.assertEqual((relay_on, power, current, confirmed), (True, 1200.0, 5.2, True))
        service._mark_failure.assert_called_once_with("shelly")
        service._warning_throttled.assert_called_once()

        with patch.object(
            controller.components.runtime_cycle,
            "run",
            side_effect=RuntimeError("update failed"),
        ), patch("venus_evcharger.update.controller.logging.warning") as warning_mock:
            self.assertTrue(controller.update())
        warning_mock.assert_called_once()

    def test_derive_status_code_prefers_fresh_native_charger_enabled_readback(self):
        service = SimpleNamespace(
            _charger_backend=SimpleNamespace(),
            _last_charger_state_enabled=True,
            _last_charger_state_at=100.0,
            auto_shelly_soft_fail_seconds=10.0,
            charging_threshold_watts=1500.0,
            idle_status=1,
        )
        sync_readback_test_service(service)

        status = relay_components_for_test(service).status.derive_status_code(service, False, 0.0, False, 100.0)

        self.assertEqual(status, 1)
        self.assertEqual(service._last_status_source, "enabled-idle")
        self.assertEqual(service._last_charger_fault_active, 0)

    def test_derive_status_code_maps_fresh_native_charger_fault_to_disconnected(self):
        service = SimpleNamespace(
            _charger_backend=SimpleNamespace(),
            _last_charger_state_status="fault",
            _last_charger_state_fault="overcurrent error",
            _last_charger_state_at=100.0,
            auto_shelly_soft_fail_seconds=10.0,
            charging_threshold_watts=1500.0,
            idle_status=1,
        )
        sync_readback_test_service(service)

        status = relay_components_for_test(service).status.derive_status_code(service, True, 2000.0, True, 100.0)

        self.assertEqual(status, 0)
        self.assertEqual(service._last_status_source, "charger-fault")
        self.assertEqual(service._last_charger_fault_active, 1)

    def test_derive_status_code_maps_contactor_lockout_to_disconnected_fault_status(self):
        service = SimpleNamespace(
            _last_health_reason="contactor-lockout-open",
            charging_threshold_watts=1500.0,
            idle_status=1,
        )

        status = relay_components_for_test(service).status.derive_status_code(
            service,
            True,
            2000.0,
            True,
            health_reason="contactor-lockout-open",
            now=100.0,
        )

        self.assertEqual(status, 0)
        self.assertEqual(service._last_status_source, "contactor-lockout-open")

    def test_derive_status_code_prefers_contactor_lockout_over_fresh_native_charger_charging(self):
        service = SimpleNamespace(
            _charger_backend=SimpleNamespace(),
            _last_charger_state_status="charging",
            _last_charger_state_at=100.0,
            _last_health_reason="contactor-lockout-open",
            auto_shelly_soft_fail_seconds=10.0,
            charging_threshold_watts=1500.0,
            idle_status=1,
        )

        status = relay_components_for_test(service).status.derive_status_code(
            service,
            True,
            2000.0,
            True,
            health_reason="contactor-lockout-open",
            now=100.0,
        )

        self.assertEqual(status, 0)
        self.assertEqual(service._last_status_source, "contactor-lockout-open")

    def test_derive_status_code_maps_switch_feedback_mismatch_to_disconnected_fault_status(self):
        service = SimpleNamespace(
            _last_health_reason="contactor-feedback-mismatch",
            charging_threshold_watts=1500.0,
            idle_status=1,
        )

        status = relay_components_for_test(service).status.derive_status_code(
            service,
            True,
            2000.0,
            True,
            health_reason="contactor-feedback-mismatch",
            now=100.0,
        )

        self.assertEqual(status, 0)
        self.assertEqual(service._last_status_source, "contactor-feedback-fault")

    def test_relay_status_publish_fault_source_and_fallback_contracts_are_explicit(self):
        self.assertEqual(
            RelayStatusPublisher._evse_fault_status_source("contactor-feedback-mismatch"),
            "contactor-feedback-fault",
        )
        self.assertEqual(
            RelayStatusPublisher._evse_fault_status_source("contactor-lockout-open"),
            "contactor-lockout-open",
        )
        self.assertEqual(
            RelayStatusPublisher._evse_fault_status_source("contactor-lockout-welded"),
            "contactor-lockout-welded",
        )
        self.assertEqual(RelayStatusPublisher._evse_fault_status_source("other"), "evse-fault")

        service = SimpleNamespace(_last_health_reason="contactor-lockout-welded")
        self.assertEqual(relay_components_for_test(service).status._hard_evse_fault_status_override(service), 0)
        self.assertEqual(service._last_status_source, "contactor-lockout-welded")
        service._last_health_reason = "running"
        self.assertIsNone(relay_components_for_test(service).status._hard_evse_fault_status_override(service))

        fallback_service = SimpleNamespace(charging_threshold_watts=1500.0, idle_status=1)
        self.assertEqual(RelayStatusPublisher._enabled_fallback_status_code(fallback_service, 1499.9), 1)
        self.assertEqual(fallback_service._last_status_source, "enabled-idle")
        self.assertEqual(RelayStatusPublisher._enabled_fallback_status_code(fallback_service, 1500.0), 2)
        self.assertEqual(fallback_service._last_status_source, "charging")
        self.assertEqual(RelayStatusPublisher._disabled_fallback_status_code(fallback_service, True), 4)
        self.assertEqual(fallback_service._last_status_source, "auto-waiting")
        self.assertEqual(RelayStatusPublisher._disabled_fallback_status_code(fallback_service, False), 6)
        self.assertEqual(fallback_service._last_status_source, "manual-off")

    def test_relay_status_publish_charger_fault_override_records_active_flag(self):
        service = SimpleNamespace()
        status = relay_components_for_test(service).status

        with patch.object(status._health, "charger_health_override", return_value=None) as health:
            self.assertIsNone(status._charger_fault_status_override(service, 100.0))
        health.assert_called_once_with(service, 100.0)
        self.assertEqual(service._last_charger_fault_active, 0)

        with patch.object(status._health, "charger_health_override", return_value="charger-fault") as health:
            self.assertEqual(status._charger_fault_status_override(service, 101.0), 0)
        health.assert_called_once_with(service, 101.0)
        self.assertEqual(service._last_status_source, "charger-fault")
        self.assertEqual(service._last_charger_fault_active, 1)

    def test_relay_status_publish_failure_contracts_distinguish_charger_and_shelly_sources(self):
        charger_error = ModbusSlaveOfflineError("Modbus slave 1 did not respond")
        charger_service = SimpleNamespace(
            _charger_backend=SimpleNamespace(set_enabled=MagicMock()),
            auto_shelly_soft_fail_seconds=10.0,
            _mark_failure=MagicMock(),
            _warning_throttled=MagicMock(),
        )
        charger_status = relay_components_for_test(charger_service).status

        with patch.object(charger_status._transport, "remember_issue") as transport, patch.object(
            charger_status._transport,
            "remember_retry",
        ) as retry:
            charger_status._handle_relay_decision_failure(charger_service, charger_error)

        transport.assert_called_once_with(charger_service, "offline", "enable", charger_error)
        retry.assert_called_once_with(charger_service, "offline", "enable")
        charger_service._mark_failure.assert_called_once_with("charger")
        warning_args = charger_service._warning_throttled.call_args.args
        warning_kwargs = charger_service._warning_throttled.call_args.kwargs
        self.assertEqual(warning_args[:4], ("charger-switch-failed", 10.0, "%s switch request failed: %s", "charger backend"))
        self.assertIs(warning_args[4], charger_error)
        self.assertIs(warning_kwargs["exc_info"], charger_error)

        charger_runtime_error = RuntimeError("backend failed")
        charger_service._mark_failure.reset_mock()
        charger_service._warning_throttled.reset_mock()
        with patch.object(charger_status._transport, "remember_issue") as transport, patch.object(
            charger_status._transport,
            "remember_retry",
        ) as retry:
            charger_status._handle_relay_decision_failure(charger_service, charger_runtime_error)
        transport.assert_not_called()
        retry.assert_not_called()
        charger_service._mark_failure.assert_called_once_with("charger")
        self.assertIs(charger_service._warning_throttled.call_args.args[4], charger_runtime_error)

        shelly_error = RuntimeError("switch failed")
        shelly_service = SimpleNamespace(
            auto_shelly_soft_fail_seconds=7.0,
            _mark_failure=MagicMock(),
            _warning_throttled=MagicMock(),
        )
        shelly_status = relay_components_for_test(shelly_service).status

        with patch.object(shelly_status._transport, "remember_issue") as transport, patch.object(
            shelly_status._transport,
            "remember_retry",
        ) as retry:
            shelly_status._handle_relay_decision_failure(shelly_service, shelly_error)

        transport.assert_not_called()
        retry.assert_not_called()
        shelly_service._mark_failure.assert_called_once_with("shelly")
        warning_args = shelly_service._warning_throttled.call_args.args
        warning_kwargs = shelly_service._warning_throttled.call_args.kwargs
        self.assertEqual(warning_args[:4], ("shelly-switch-failed", 7.0, "%s switch request failed: %s", "Shelly relay"))
        self.assertIs(warning_args[4], shelly_error)
        self.assertIs(warning_kwargs["exc_info"], shelly_error)

    def test_relay_status_publish_decision_logging_noop_and_success_contracts(self):
        service = SimpleNamespace(
            auto_audit_log=True,
            _relay_sync_expected_state=None,
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))
        controller.components.relay.foundation.telemetry.log_auto_relay_change = MagicMock()
        controller.components.relay.foundation.telemetry.publish_local_pm_status_best_effort = MagicMock()

        self.assertTrue(controller.components.relay.status._relay_decision_noop(service, True, True))
        service._relay_sync_expected_state = True
        self.assertTrue(controller.components.relay.status._relay_decision_noop(service, True, False))
        service._relay_sync_expected_state = None
        self.assertFalse(controller.components.relay.status._relay_decision_noop(service, True, False))

        controller.components.relay.status._log_auto_relay_change_if_needed(service, True, False)
        controller.components.relay.foundation.telemetry.log_auto_relay_change.assert_not_called()
        controller.components.relay.status._log_auto_relay_change_if_needed(service, True, True)
        controller.components.relay.foundation.telemetry.log_auto_relay_change.assert_called_once_with(service, True)

        self.assertEqual(
            controller.components.relay.status._successful_relay_decision_result(service, True, 123.0),
            (True, 0.0, 0.0, False),
        )
        controller.components.relay.foundation.telemetry.publish_local_pm_status_best_effort.assert_called_once_with(
            service, True, 123.0
        )

    def test_relay_status_publish_apply_paths_cover_pending_success_and_type_failure(self):
        service = SimpleNamespace(auto_shelly_soft_fail_seconds=10.0)
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        with patch.object(
            controller.components.relay.foundation.targets,
            "apply_enabled_target",
            return_value=False,
        ) as apply_target:
            self.assertEqual(
                controller.components.relay.status._unsuccessful_relay_decision_result(service, True, False, 1200.0, 5.2, True, 100.0),
                (False, 1200.0, 5.2, True),
            )
        apply_target.assert_called_once_with(service, True, 100.0)

        with patch.object(
            controller.components.relay.foundation.targets,
            "apply_enabled_target",
            return_value=True,
        ) as apply_target:
            self.assertIsNone(
                controller.components.relay.status._unsuccessful_relay_decision_result(service, True, False, 1200.0, 5.2, True, 101.0)
            )
        apply_target.assert_called_once_with(service, True, 101.0)

        service._mark_failure = MagicMock()
        service._warning_throttled = MagicMock()
        with patch.object(controller.components.relay.foundation.targets, "apply_enabled_target", return_value="bad"):
            self.assertIsNone(controller.components.relay.status._apply_relay_target_best_effort(service, True, 102.0))
        service._mark_failure.assert_called_once_with("shelly")
        service._warning_throttled.assert_called_once()

        apply_error = RuntimeError("switch failed")
        with patch.object(
            controller.components.relay.foundation.targets,
            "apply_enabled_target",
            side_effect=apply_error,
        ), patch.object(
            controller.components.relay.status,
            "_handle_relay_decision_failure",
        ) as handle_failure:
            self.assertIsNone(controller.components.relay.status._apply_relay_target_best_effort(service, False, 103.0))
        handle_failure.assert_called_once_with(service, apply_error)

    def test_relay_status_publish_apply_relay_decision_branch_contracts(self):
        service = SimpleNamespace()
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        with patch.object(
            controller.components.relay.foundation.telemetry,
            "pm_status_confirmed",
            return_value=True,
        ) as confirmed, patch.object(
            controller.components.relay.status,
            "_relay_decision_noop",
            return_value=True,
        ) as noop:
            self.assertEqual(
                controller.components.relay.status.apply_relay_decision(
                    service, True, True, {"output": True}, 1.0, 2.0, 100.0, True
                ),
                (True, 1.0, 2.0, True),
            )
        confirmed.assert_called_once_with({"output": True})
        noop.assert_called_once_with(service, True, True)

        pending = (False, 1.0, 2.0, True)
        with patch.object(
            controller.components.relay.foundation.telemetry,
            "pm_status_confirmed",
            return_value=True,
        ), patch.object(
            controller.components.relay.status,
            "_relay_decision_noop",
            return_value=False,
        ), patch.object(
            controller.components.relay.status,
            "_log_auto_relay_change_if_needed",
        ) as log_change, patch.object(
            controller.components.relay.status,
            "_unsuccessful_relay_decision_result",
            return_value=pending,
        ) as pending_result, patch.object(
            controller.components.relay.status,
            "_successful_relay_decision_result",
        ) as success_result:
            self.assertEqual(
                controller.components.relay.status.apply_relay_decision(
                    service, True, False, {}, 1.0, 2.0, 101.0, False
                ),
                pending,
            )
        log_change.assert_called_once_with(service, True, False)
        pending_result.assert_called_once_with(service, True, False, 1.0, 2.0, True, 101.0)
        success_result.assert_not_called()

        success = (True, 0.0, 0.0, False)
        with patch.object(
            controller.components.relay.foundation.telemetry,
            "pm_status_confirmed",
            return_value=False,
        ), patch.object(
            controller.components.relay.status,
            "_relay_decision_noop",
            return_value=False,
        ), patch.object(
            controller.components.relay.status,
            "_log_auto_relay_change_if_needed",
        ), patch.object(
            controller.components.relay.status,
            "_unsuccessful_relay_decision_result",
            return_value=None,
        ) as pending_result, patch.object(
            controller.components.relay.status,
            "_successful_relay_decision_result",
            return_value=success,
        ) as success_result:
            self.assertEqual(
                controller.components.relay.status.apply_relay_decision(
                    service, True, False, {}, 1.0, 2.0, 102.0, True
                ),
                success,
            )
        pending_result.assert_called_once_with(service, True, False, 1.0, 2.0, False, 102.0)
        success_result.assert_called_once_with(service, True, 102.0)

    def test_relay_status_publish_status_precedence_and_override_contracts(self):
        service = SimpleNamespace(_last_health_reason="contactor-lockout-open", charging_threshold_watts=1500.0, idle_status=1)
        status = relay_components_for_test(service).status

        with patch.object(status, "_charger_fault_status_override") as fault_override, patch.object(
            status._health,
            "_charger_status_override",
        ) as status_override:
            self.assertEqual(status.derive_status_code(service, True, 2000.0, True, 100.0), 0)
        fault_override.assert_not_called()
        status_override.assert_not_called()

        service._last_health_reason = "running"
        with patch.object(status, "_charger_fault_status_override", return_value=0) as fault_override, patch.object(
            status._health,
            "_charger_status_override",
        ) as status_override:
            self.assertEqual(status.derive_status_code(service, True, 2000.0, True, 101.0), 0)
        fault_override.assert_called_once_with(service, 101.0)
        status_override.assert_not_called()

        with patch.object(status, "_charger_fault_status_override", return_value=None), patch.object(
            status._health,
            "_charger_status_override",
            return_value=("3", "charger-status-finished"),
        ) as status_override:
            self.assertEqual(status.derive_status_code(service, True, 2000.0, True, 102.0), 3)
        status_override.assert_called_once_with(service, True, 102.0)
        self.assertEqual(service._last_status_source, "charger-status-finished")

        with patch.object(status, "_hard_evse_fault_status_override", return_value=None) as hard_fault, patch.object(
            status,
            "_charger_fault_status_override",
            return_value=None,
        ) as fault_override, patch.object(
            status._health,
            "_charger_status_override",
            return_value=None,
        ) as status_override, patch.object(
            status,
            "_fallback_status_code",
            return_value=6,
        ) as fallback:
            self.assertEqual(
                status.derive_status_code(service, False, 0.0, False, now=103.0, health_reason="running"),
                6,
            )
        hard_fault.assert_called_once_with(service, "running")
        fault_override.assert_called_once_with(service, 103.0)
        status_override.assert_called_once_with(service, False, 103.0)
        fallback.assert_called_once_with(service, False, 0.0, False, 103.0)

    def test_relay_status_publish_fallback_status_uses_effective_enabled_state_with_timestamp(self):
        service = SimpleNamespace(charging_threshold_watts=1500.0, idle_status=1)
        status = relay_components_for_test(service).status

        with patch.object(status._health, "_effective_enabled_state", return_value=True) as enabled:
            self.assertEqual(status._fallback_status_code(service, False, 1600.0, False, 100.0), 2)
        enabled.assert_called_once_with(service, False, 100.0)
        self.assertEqual(service._last_status_source, "charging")

        with patch.object(status._health, "_effective_enabled_state", return_value=False) as enabled:
            self.assertEqual(status._fallback_status_code(service, True, 1600.0, True, 101.0), 4)
        enabled.assert_called_once_with(service, True, 101.0)
        self.assertEqual(service._last_status_source, "auto-waiting")

    def test_publish_online_update_uses_fallback_measurements_and_reports_any_change(self):
        service = SimpleNamespace(
            phase="L1",
            voltage_mode="phase",
            _publish_live_measurements=MagicMock(return_value=False),
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))
        controller.components.state.update_virtual_state = MagicMock(return_value=True)

        self.assertTrue(
            controller.components.relay.status.publish_online_update(
                service, {}, 4, 2.5, False, 1150.0, 230.0, 100.0
            )
        )
        service._publish_live_measurements.assert_called_once()
        live_args = service._publish_live_measurements.call_args.args
        self.assertEqual(live_args[0], 1150.0)
        self.assertEqual(live_args[1], 230.0)
        self.assertEqual(live_args[2], 5.0)
        self.assertEqual(live_args[4], 100.0)
        controller.components.state.update_virtual_state.assert_called_once_with(4, 2.5, False)

    def test_publish_online_update_prefers_positive_current_readback_over_phase_sum(self):
        service = SimpleNamespace(
            phase="L1",
            voltage_mode="phase",
            _charger_backend=SimpleNamespace(),
            _last_charger_state_at=200.0,
            _last_charger_state_power_w=2000.0,
            _last_charger_state_actual_current_amps=9.5,
            _last_charger_state_energy_kwh=4.25,
            auto_shelly_soft_fail_seconds=10.0,
            _publish_live_measurements=MagicMock(return_value=True),
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))
        controller.components.state.update_virtual_state = MagicMock(return_value=False)

        self.assertTrue(
            controller.components.relay.status.publish_online_update(
                service, {}, 2, 1.0, True, 1150.0, 230.0, 200.0
            )
        )
        live_args = service._publish_live_measurements.call_args.args
        self.assertEqual(live_args[0], 2000.0)
        self.assertEqual(live_args[2], 9.5)
        controller.components.state.update_virtual_state.assert_called_once_with(2, 4.25, True)

    def test_publish_online_update_prefers_small_positive_current_readback_over_phase_sum(self):
        service = SimpleNamespace(
            _charger_backend=SimpleNamespace(),
            _last_charger_state_at=200.0,
            _last_charger_state_power_w=None,
            _last_charger_state_actual_current_amps=0.5,
            _last_charger_state_energy_kwh=None,
            auto_shelly_soft_fail_seconds=10.0,
            _publish_live_measurements=MagicMock(return_value=False),
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))
        controller.components.state.update_virtual_state = MagicMock(return_value=False)

        self.assertFalse(
            controller.components.relay.status.publish_online_update(
                service,
                {
                    "_phase_selection": "P1_P2_P3",
                    "_phase_powers_w": (100.0, 200.0, 300.0),
                    "_phase_currents_a": (1.0, 2.0, 3.0),
                },
                2,
                1.0,
                True,
                1150.0,
                230.0,
                200.0,
            )
        )
        self.assertEqual(service._publish_live_measurements.call_args.args[2], 0.5)

    def test_publish_online_update_uses_phase_metadata_and_keeps_zero_current_readback_as_phase_sum(self):
        service = SimpleNamespace(
            phase="L1",
            voltage_mode="phase",
            _charger_backend=SimpleNamespace(),
            _last_charger_state_at=200.0,
            _last_charger_state_power_w=2000.0,
            _last_charger_state_actual_current_amps=0.0,
            _last_charger_state_energy_kwh=4.25,
            auto_shelly_soft_fail_seconds=10.0,
            _publish_live_measurements=MagicMock(return_value=False),
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))
        controller.components.state.update_virtual_state = MagicMock(return_value=False)

        self.assertFalse(
            controller.components.relay.status.publish_online_update(
                service,
                {
                    "_phase_selection": "P1_P2_P3",
                    "_phase_powers_w": (100.0, 200.0, 300.0),
                    "_phase_currents_a": (1.0, 2.0, 3.0),
                },
                2,
                1.0,
                True,
                1150.0,
                230.0,
                200.0,
            )
        )
        live_args = service._publish_live_measurements.call_args.args
        self.assertEqual(live_args[0], 2000.0)
        self.assertEqual(live_args[2], 6.0)
        self.assertEqual(live_args[3]["L1"]["power"], 100.0)
        self.assertEqual(live_args[3]["L2"]["current"], 2.0)
        controller.components.state.update_virtual_state.assert_called_once_with(2, 4.25, True)

    def test_publish_online_update_readback_and_phase_delegate_contracts(self):
        service = SimpleNamespace(_publish_live_measurements=MagicMock(return_value=False))
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))
        controller.components.state.update_virtual_state = MagicMock(return_value=False)
        phase_data = {
            "L1": {"power": 100.0, "voltage": 230.0, "current": 1.0},
            "L2": {"power": 200.0, "voltage": 230.0, "current": 2.0},
            "L3": {"power": 300.0, "voltage": 230.0, "current": 3.0},
        }

        with patch.object(
            controller.components.relay.foundation.telemetry,
            "_phase_data_for_pm_status",
            return_value=phase_data,
        ) as phase_data_for_pm_status, patch.object(
            controller.components.relay.status,
            "_total_phase_current",
            return_value=6.0,
        ) as total_current:
            self.assertFalse(
                controller.components.relay.status.publish_online_update(
                    service, {"output": True}, 4, 2.5, False, 1150.0, 230.0, 300.0
                )
            )

        self.assertIsNone(controller.components.readbacks.resolve(300.0).charger)
        phase_data_for_pm_status.assert_called_once_with(service, {"output": True}, 1150.0, 230.0)
        total_current.assert_called_once_with(phase_data)
        service._publish_live_measurements.assert_called_once_with(1150.0, 230.0, 6.0, phase_data, 300.0)
        controller.components.state.update_virtual_state.assert_called_once_with(4, 2.5, False)

# SPDX-License-Identifier: GPL-3.0-or-later
from tests.venus_evcharger_update_cycle_controller_support import *


class TestUpdateCycleControllerDenary(UpdateCycleControllerTestBase):
    def test_prepare_update_cycle_supervises_io_worker_only_when_topology_is_configured(self):
        configured_service = SimpleNamespace(
            topology_configured=True,
            _start_io_worker=MagicMock(),
            _ensure_dbus_introspection_worker_process=MagicMock(),
            _watchdog_recover=MagicMock(),
            _ensure_auto_input_helper_process=MagicMock(),
            _refresh_auto_input_snapshot=MagicMock(),
            _get_worker_snapshot=MagicMock(return_value={"ok": True}),
        )

        self.assertEqual(UpdateCycleController.prepare_update_cycle(configured_service, 100.0), {"ok": True})

        configured_service._start_io_worker.assert_called_once_with()
        configured_service._ensure_dbus_introspection_worker_process.assert_called_once_with(100.0)
        configured_service._watchdog_recover.assert_called_once_with(100.0)

        unconfigured_service = SimpleNamespace(
            topology_configured=False,
            _start_io_worker=MagicMock(),
            _ensure_dbus_introspection_worker_process=MagicMock(),
            _watchdog_recover=MagicMock(),
            _ensure_auto_input_helper_process=MagicMock(),
            _refresh_auto_input_snapshot=MagicMock(),
            _get_worker_snapshot=MagicMock(return_value={}),
        )

        self.assertEqual(UpdateCycleController.prepare_update_cycle(unconfigured_service, 101.0), {})

        unconfigured_service._start_io_worker.assert_not_called()
        unconfigured_service._ensure_dbus_introspection_worker_process.assert_not_called()

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
        controller.update_virtual_state = MagicMock(return_value=False)

        changed = controller.publish_online_update(
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
        controller.update_virtual_state.assert_called_once_with(2, 7.25, True)

    def test_publish_offline_update_uses_backend_phase_metadata_for_display(self):
        service = SimpleNamespace(
            _time_now=MagicMock(return_value=200.0),
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
            _publish_dbus_path=MagicMock(return_value=False),
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

        self.assertTrue(controller.publish_offline_update(200.0))
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
            UpdateCycleController._cached_input_from_service(
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
            _time_now=MagicMock(return_value=123.0),
            _bump_update_index=MagicMock(),
            virtual_startstop=1,
            service_name="com.victronenergy.evcharger.http_60",
            _dbusservice={"/Ac/Power": 321.0},
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        charging_time, session_energy = controller.session_state_from_status(service, 1, 2.0, True, 100.0)
        self.assertEqual((charging_time, session_energy), (0, 0.0))
        self.assertIsNone(service.charging_started_at)
        self.assertEqual(service.energy_at_start, 2.0)

        self.assertEqual(
            controller.phase_energies_for_total(service, 6.0),
            {"L1": 2.0, "L2": 2.0, "L3": 2.0},
        )

        self.assertEqual(
            controller.resolve_pm_status_for_update(service, {"pm_status": None}, 100.0),
            {"output": True, "_pm_confirmed": True},
        )

        confirmed_pm_status = controller.resolve_pm_status_for_update(
            service,
            {"pm_status": {"output": False}, "pm_confirmed": True, "pm_captured_at": 101.0},
            101.0,
        )
        self.assertEqual(confirmed_pm_status, {"output": False, "_pm_confirmed": True})
        self.assertTrue(service._last_pm_status_confirmed)

        with patch("venus_evcharger.update.controller.logging.info") as info_mock:
            controller.log_auto_relay_change(service, True)
            controller.sign_of_life()

        self.assertEqual(info_mock.call_count, 2)

        relay_on, power, current, confirmed = controller.apply_relay_decision(
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
            _time_now=MagicMock(return_value=100.0),
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

        relay_on, power, current, confirmed = controller.apply_relay_decision(
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

        with patch("venus_evcharger.update.controller.logging.warning") as warning_mock:
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

        status = UpdateCycleController.derive_status_code(service, False, 0.0, False, 100.0)

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

        status = UpdateCycleController.derive_status_code(service, True, 2000.0, True, 100.0)

        self.assertEqual(status, 0)
        self.assertEqual(service._last_status_source, "charger-fault")
        self.assertEqual(service._last_charger_fault_active, 1)

    def test_derive_status_code_maps_contactor_lockout_to_disconnected_fault_status(self):
        service = SimpleNamespace(
            _last_health_reason="contactor-lockout-open",
            charging_threshold_watts=1500.0,
            idle_status=1,
        )

        status = UpdateCycleController.derive_status_code(
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

        status = UpdateCycleController.derive_status_code(
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

        status = UpdateCycleController.derive_status_code(
            service,
            True,
            2000.0,
            True,
            health_reason="contactor-feedback-mismatch",
            now=100.0,
        )

        self.assertEqual(status, 0)
        self.assertEqual(service._last_status_source, "contactor-feedback-fault")

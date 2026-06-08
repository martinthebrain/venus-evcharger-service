# SPDX-License-Identifier: GPL-3.0-or-later
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from tests.service_mixins_cases_common import _AutoService, _FactoryService, _RuntimeService, _StateService, _UpdateService
from venus_evcharger.control import ControlCommand


class _ServiceMixinsRuntimeUpdateCases:
    def test_runtime_helper_mixin_delegates_runtime_supervisor_and_io_calls(self):
        service = _RuntimeService()
        service._runtime_support_controller = MagicMock()
        service._runtime_support_controller.assert_dbus_mainloop_thread = MagicMock()
        service._auto_input_supervisor = MagicMock()
        service._dbus_introspection_supervisor = MagicMock()
        service._shelly_io_controller = MagicMock()

        service._reset_system_bus()
        service._ensure_system_bus_state()
        service._create_system_bus()
        service._init_worker_state()
        service._worker_state_defaults()
        service._ensure_missing_attributes({"x": 1})
        service._ensure_worker_state()
        service._mark_mainloop_thread()
        service._dbus_publish_direct_allowed()
        service._assert_dbus_mainloop_thread("test")
        service._enqueue_dbus_publish_values([("/Path", 1)], 12.0)
        service._enqueue_dbus_update_index_bump(12.0)
        service._enqueue_companion_dbus_publish(12.0)
        service._flush_dbus_publish_queue()
        service._start_update_worker()
        service._schedule_update_cycle()
        service._start_control_command_worker()
        service._enqueue_control_command(ControlCommand(name="set_mode", path="/Mode", value=1))
        service._mainloop_heartbeat_tick()
        service._start_mainloop_watchdog()
        service._set_worker_snapshot({"captured_at": 1.0})
        service._update_worker_snapshot(pv_power=123.0)
        service._get_worker_snapshot()
        service._ensure_observability_state()
        service._is_update_stale(12.0)
        service._watchdog_recover(12.0)
        service._warning_throttled("warn", 5.0, "msg %s", "x")
        service._write_auto_audit_event("waiting-surplus", cached=True)
        service._mark_failure("dbus")
        service._mark_recovery("dbus", "recovered %s", "ok")
        service._source_retry_ready("dbus", 12.0)
        service._source_retry_remaining("dbus", 12.0)
        service._delay_source_retry("dbus", 12.0)
        service._delay_source_retry("dbus", 12.0, 7.0)
        service._stop_auto_input_helper(force=True)
        service._spawn_auto_input_helper(12.0)
        service._ensure_auto_input_helper_process(12.0)
        service._stop_dbus_introspection_worker(force=True)
        service._ensure_dbus_introspection_worker_process(12.0)
        service._refresh_auto_input_snapshot(12.0)
        service._request_with_session("sess", "http://example.invalid")
        service._rpc_call_with_session("sess", "Switch.Set", on=True)
        service._worker_fetch_pm_status()
        service._build_local_pm_status(True)
        service._publish_local_pm_status(True, 12.0)
        service._queue_relay_command(True, 12.0)
        service._peek_pending_relay_command()
        service._clear_pending_relay_command(True)
        service._worker_apply_pending_relay_command()
        service._io_worker_once()
        service._io_worker_loop()
        service._start_io_worker()
        service._request("http://example.invalid")
        service.rpc_call("Switch.GetStatus", id=0)
        service.fetch_rpc("Shelly.GetDeviceInfo")
        service.fetch_pm_status()
        service.set_relay(True)
        service._phase_selection_requires_pause()
        service._apply_phase_selection("P1_P2")

        runtime = service._runtime_support_controller
        runtime.reset_system_bus.assert_called_once_with()
        runtime.ensure_system_bus_state.assert_called_once_with()
        runtime.create_system_bus.assert_called_once_with()
        runtime.init_worker_state.assert_called_once_with()
        runtime.worker_state_defaults.assert_called_once_with()
        runtime.ensure_missing_attributes.assert_called_once_with(service, {"x": 1})
        runtime.ensure_worker_state.assert_called_once_with()
        runtime.mark_mainloop_thread.assert_called_once_with()
        runtime.dbus_publish_direct_allowed.assert_called_once_with()
        runtime.assert_dbus_mainloop_thread.assert_called_once_with("test")
        runtime.enqueue_dbus_publish_values.assert_called_once_with([("/Path", 1)], 12.0)
        runtime.enqueue_dbus_update_index_bump.assert_called_once_with(12.0)
        runtime.enqueue_companion_dbus_publish.assert_called_once_with(12.0)
        runtime.flush_dbus_publish_queue.assert_called_once_with()
        runtime.start_update_worker.assert_called_once_with()
        runtime.schedule_update_cycle.assert_called_once_with()
        runtime.start_control_command_worker.assert_called_once_with()
        runtime.enqueue_control_command.assert_called_once_with(ControlCommand(name="set_mode", path="/Mode", value=1))
        runtime.mainloop_heartbeat_tick.assert_called_once_with()
        runtime.start_mainloop_watchdog.assert_called_once_with()
        runtime.set_worker_snapshot.assert_called_once_with({"captured_at": 1.0})
        runtime.update_worker_snapshot.assert_called_once_with(pv_power=123.0)
        runtime.get_worker_snapshot.assert_called_once_with()
        runtime.ensure_observability_state.assert_called_once_with()
        runtime.is_update_stale.assert_called_once_with(12.0)
        runtime.watchdog_recover.assert_called_once_with(12.0)
        runtime.warning_throttled.assert_called_once_with("warn", 5.0, "msg %s", "x")
        runtime.write_auto_audit_event.assert_called_once_with("waiting-surplus", True)
        runtime.mark_failure.assert_called_once_with("dbus")
        runtime.mark_recovery.assert_called_once_with("dbus", "recovered %s", "ok")
        runtime.source_retry_ready.assert_called_once_with("dbus", 12.0)
        runtime.source_retry_remaining.assert_called_once_with("dbus", 12.0)
        runtime.delay_source_retry.assert_any_call("dbus", 12.0)
        runtime.delay_source_retry.assert_any_call("dbus", 12.0, 7.0)
        service._auto_input_supervisor.stop_helper.assert_called_once_with(True)
        service._auto_input_supervisor.spawn_helper.assert_called_once_with(12.0)
        service._auto_input_supervisor.ensure_helper_process.assert_called_once_with(12.0)
        service._dbus_introspection_supervisor.stop_worker.assert_called_once_with(True)
        service._dbus_introspection_supervisor.ensure_worker_process.assert_called_once_with(12.0)
        service._auto_input_supervisor.refresh_snapshot.assert_called_once_with(12.0)
        io = service._shelly_io_controller
        io.request_with_session.assert_called_once_with("sess", "http://example.invalid")
        io.rpc_call_with_session.assert_called_once_with("sess", "Switch.Set", on=True)
        io.worker_fetch_pm_status.assert_called_once_with()
        io.build_local_pm_status.assert_called_once_with(True)
        io.publish_local_pm_status.assert_called_once_with(True, 12.0)
        io.queue_relay_command.assert_called_once_with(True, 12.0)
        io.peek_pending_relay_command.assert_called_once_with()
        io.clear_pending_relay_command.assert_called_once_with(True)
        io.worker_apply_pending_relay_command.assert_called_once_with()
        io.io_worker_once.assert_called_once_with()
        io.io_worker_loop.assert_called_once_with()
        io.start_io_worker.assert_called_once_with()
        io.request.assert_called_once_with("http://example.invalid")
        io.rpc_call.assert_any_call("Switch.GetStatus", id=0)
        io.rpc_call.assert_any_call("Shelly.GetDeviceInfo")
        io.fetch_pm_status.assert_called_once_with()
        io.set_relay.assert_called_once_with(True)
        io.phase_selection_requires_pause.assert_called_once_with()
        io.set_phase_selection.assert_called_once_with("P1_P2")

    def test_update_cycle_mixin_delegates_all_calls(self):
        service = _UpdateService()
        service._update_controller = MagicMock()

        service._ensure_virtual_state_defaults()
        service._session_state_from_status(2, 1.5, True, 100.0)
        service._startstop_display_for_state(True)
        service._phase_energies_for_total(2.5)
        service._publish_virtual_state_paths(3.0, 4, 5.0, 1, 100.0)
        service._update_virtual_state(2, 1.5, True)
        service._prepare_update_cycle(100.0)
        service._resolve_pm_status_for_update({"pm_status": {}}, 100.0)
        service._publish_offline_update(100.0)
        service._extract_pm_measurements({"output": True})
        service._resolve_cached_input_value(1.0, 90.0, "_last", "_last_at", 100.0, max_age_seconds=30.0)
        service._resolve_auto_inputs({"captured_at": 100.0}, 100.0, True)
        service._log_auto_relay_change(True)
        service._apply_relay_decision(True, False, {"output": False}, 0.0, 0.0, 100.0, True)
        service._derive_status_code(True, 2000.0, True)
        service._publish_online_update({"output": True}, 2, 1.5, True, 2000.0, 230.0, 100.0)
        service._complete_update_cycle(True, 100.0, True, 2000.0, 8.7, 2, 2500.0, 55.0, -1800.0)
        service._sign_of_life()
        service._update()

        controller = service._update_controller
        controller.ensure_virtual_state_defaults.assert_called_once_with()
        controller.session_state_from_status.assert_called_once_with(service, 2, 1.5, True, 100.0)
        controller.startstop_display_for_state.assert_called_once_with(service, True)
        controller.phase_energies_for_total.assert_called_once_with(service, 2.5)
        controller.publish_virtual_state_paths.assert_called_once_with(3.0, 4, 5.0, 1, 100.0)
        controller.update_virtual_state.assert_called_once_with(2, 1.5, True)
        controller.prepare_update_cycle.assert_called_once_with(service, 100.0)
        controller.resolve_pm_status_for_update.assert_called_once_with(service, {"pm_status": {}}, 100.0)
        controller.publish_offline_update.assert_called_once_with(100.0)
        controller.extract_pm_measurements.assert_called_once_with(service, {"output": True})
        controller.resolve_cached_input_value.assert_called_once_with(
            service,
            1.0,
            90.0,
            "_last",
            "_last_at",
            100.0,
            max_age_seconds=30.0,
        )
        controller.resolve_auto_inputs.assert_called_once_with({"captured_at": 100.0}, 100.0, True)
        controller.log_auto_relay_change.assert_called_once_with(service, True)
        controller.apply_relay_decision.assert_called_once_with(True, False, {"output": False}, 0.0, 0.0, 100.0, True)
        controller.derive_status_code.assert_called_once_with(service, True, 2000.0, True)
        controller.publish_online_update.assert_called_once_with({"output": True}, 2, 1.5, True, 2000.0, 230.0, 100.0)
        controller.complete_update_cycle.assert_called_once_with(
            service,
            True,
            100.0,
            True,
            2000.0,
            8.7,
            2,
            2500.0,
            55.0,
            -1800.0,
        )
        controller.sign_of_life.assert_called_once_with()
        controller.update.assert_called_once_with()

    def test_state_publish_mixin_delegates_state_and_publish_calls(self):
        service = _StateService()
        service._state_controller = MagicMock()
        service._dbus_publisher = MagicMock()
        service._companion_dbus_bridge = MagicMock()

        service._state_summary()
        service._current_runtime_state()
        service._load_runtime_state()
        service._save_runtime_state()
        service._save_runtime_overrides()
        service._flush_runtime_overrides(100.0)
        service._validate_runtime_config()
        service._load_config()
        service._ensure_dbus_publish_state()
        service._publish_dbus_path("/Path", 1, 100.0, force=True)
        service._bump_update_index(100.0)
        service._publish_live_measurements(1000.0, 230.0, 4.3, {"L1": {}}, 100.0)
        service._publish_energy_time_measurements(1.2, {"L1": 1.2}, 10, 0.3, 100.0)
        service._publish_config_paths(1, 100.0)
        service._publish_diagnostic_paths(100.0)
        service._start_companion_dbus_bridge()
        service._publish_companion_dbus_bridge(100.0)
        service._stop_companion_dbus_bridge()

        state = service._state_controller
        state.state_summary.assert_called_once_with()
        state.current_runtime_state.assert_called_once_with()
        state.load_runtime_state.assert_called_once_with()
        state.save_runtime_state.assert_called_once_with()
        state.save_runtime_overrides.assert_called_once_with()
        state.flush_runtime_overrides.assert_called_once_with(100.0)
        state.validate_runtime_config.assert_called_once_with()
        state.load_config.assert_called_once_with()
        publisher = service._dbus_publisher
        publisher.ensure_state.assert_called_once_with()
        publisher.publish_path.assert_called_once_with("/Path", 1, 100.0, force=True)
        publisher.bump_update_index.assert_called_once_with(100.0)
        publisher.publish_live_measurements.assert_called_once_with(1000.0, 230.0, 4.3, {"L1": {}}, 100.0)
        publisher.publish_energy_time_measurements.assert_called_once_with(1.2, {"L1": 1.2}, 10, 0.3, 100.0)
        publisher.publish_config_paths.assert_called_once_with(1, 100.0)
        publisher.publish_diagnostic_paths.assert_called_once_with(100.0)
        service._companion_dbus_bridge.start.assert_called_once_with()
        service._companion_dbus_bridge.publish.assert_called_once_with(100.0)
        service._companion_dbus_bridge.stop.assert_called_once_with()
        self.assertTrue(service._config_path().endswith("/config.venus_evcharger.ini"))
        self.assertEqual(service._coerce_runtime_int("7"), 7)
        self.assertEqual(service._coerce_runtime_float("1.5"), 1.5)
        self.assertIn("captured_at", service._empty_worker_snapshot())
        cloned = service._clone_worker_snapshot({"captured_at": 1.0, "pm_status": {"output": True}})
        self.assertEqual(cloned["pm_status"], {"output": True})
        defaults = service._observability_state_defaults()
        self.assertIn("_error_state", defaults)

    def test_service_controller_factory_skips_recreating_existing_controllers(self):
        service = _FactoryService()
        existing = object()
        service._dbus_publisher = existing
        service._auto_controller = existing
        service._shelly_io_controller = existing
        service._state_controller = existing
        service._write_controller = existing
        service._auto_input_supervisor = existing
        service._dbus_introspection_supervisor = existing
        service._runtime_support_controller = existing
        service._dbus_input_controller = existing
        service._bootstrap_controller = existing
        service._update_controller = existing
        service._companion_dbus_bridge = existing

        service._ensure_dbus_publisher()
        service._ensure_auto_controller()
        service._ensure_shelly_io_controller()
        service._ensure_state_controller()
        service._ensure_write_controller()
        service._ensure_auto_input_supervisor()
        service._ensure_dbus_introspection_supervisor()
        service._ensure_runtime_support_controller()
        service._ensure_dbus_input_controller()
        service._ensure_bootstrap_controller()
        service._ensure_update_controller()
        service._ensure_companion_dbus_bridge()

        self.assertIs(service._dbus_publisher, existing)
        self.assertIs(service._auto_controller, existing)
        self.assertIs(service._shelly_io_controller, existing)
        self.assertIs(service._state_controller, existing)
        self.assertIs(service._write_controller, existing)
        self.assertIs(service._auto_input_supervisor, existing)
        self.assertIs(service._dbus_introspection_supervisor, existing)
        self.assertIs(service._runtime_support_controller, existing)
        self.assertIs(service._dbus_input_controller, existing)
        self.assertIs(service._bootstrap_controller, existing)
        self.assertIs(service._update_controller, existing)
        self.assertIs(service._companion_dbus_bridge, existing)

    def test_service_controller_factory_creates_companion_bridge_once(self):
        service = _FactoryService()

        with patch("venus_evcharger.service.factory.EnergyCompanionDbusBridge", return_value="bridge") as factory:
            service._ensure_companion_dbus_bridge()
            service._ensure_companion_dbus_bridge()

        factory.assert_called_once_with(service, "")
        self.assertEqual(service._companion_dbus_bridge, "bridge")

    def test_service_controller_factory_creates_dbus_introspection_supervisor_once(self):
        service = _FactoryService()

        with patch("venus_evcharger.service.factory.DbusIntrospectionSupervisor", return_value="introspection") as factory:
            service._ensure_dbus_introspection_supervisor()
            service._ensure_dbus_introspection_supervisor()

        factory.assert_called_once_with(service)
        self.assertEqual(service._dbus_introspection_supervisor, "introspection")

    def test_auto_logic_mixin_delegates_and_exposes_static_helpers(self):
        service = _AutoService()
        service._mode_uses_auto_logic_func = MagicMock(return_value=True)
        service._normalize_mode_func = MagicMock(return_value=1)
        service._dbus_input_controller = MagicMock()
        service._auto_controller = MagicMock()
        service._write_controller = MagicMock()
        service._bootstrap_controller = MagicMock()

        self.assertEqual(service._get_available_surplus_watts(2500, -1800), 1800.0)
        self.assertTrue(service._mode_uses_auto_logic(1))
        self.assertEqual(service._normalize_mode("1"), 1)
        service._get_dbus_value("svc", "/Path")
        service._list_dbus_services()
        service._invalidate_auto_pv_services()
        service._invalidate_auto_battery_service()
        service._resolve_auto_pv_services()
        service._get_pv_power()
        service._resolve_auto_battery_service()
        service._get_battery_soc()
        service._get_grid_power()
        service._add_auto_sample(100.0, 2000.0, -1800.0)
        service._clear_auto_samples()
        service._average_auto_metric(1)
        service._mark_relay_changed(True, 100.0)
        service._is_within_auto_daytime_window()
        service._set_health("running", cached=True)
        service._auto_decide_relay(False, 2200.0, 55.0, -1800.0)
        service._handle_write("/Mode", 1)
        service._control_command_from_write("/AutoStart", 1, source="mqtt")
        service._handle_control_command(ControlCommand(name="set_mode", path="/Mode", value=1))
        service._register_paths()
        service._fetch_device_info_with_fallback()

        service._mode_uses_auto_logic_func.assert_called_once_with(1)
        service._normalize_mode_func.assert_called_once_with("1")
        dbus_input = service._dbus_input_controller
        dbus_input.get_dbus_value.assert_called_once_with("svc", "/Path")
        dbus_input.list_dbus_services.assert_called_once_with()
        dbus_input.invalidate_auto_pv_services.assert_called_once_with()
        dbus_input.invalidate_auto_battery_service.assert_called_once_with()
        dbus_input.resolve_auto_pv_services.assert_called_once_with()
        dbus_input.get_pv_power.assert_called_once_with()
        dbus_input.resolve_auto_battery_service.assert_called_once_with()
        dbus_input.get_battery_soc.assert_called_once_with()
        dbus_input.get_grid_power.assert_called_once_with()
        auto = service._auto_controller
        auto.add_auto_sample.assert_called_once_with(100.0, 2000.0, -1800.0)
        auto.clear_auto_samples.assert_called_once_with()
        auto.average_auto_metric.assert_called_once_with(1)
        auto.mark_relay_changed.assert_called_once_with(True, 100.0)
        auto.is_within_auto_daytime_window.assert_called_once_with(None)
        auto.set_health.assert_called_once_with("running", True)
        auto.auto_decide_relay.assert_called_once_with(False, 2200.0, 55.0, -1800.0)
        service._write_controller.build_control_command.assert_any_call("/Mode", 1, source="dbus")
        service._write_controller.build_control_command.assert_any_call("/AutoStart", 1, source="mqtt")
        service._write_controller.handle_control_command.assert_any_call(
            service._write_controller.build_control_command.return_value
        )
        service._write_controller.handle_control_command.assert_any_call(
            ControlCommand(name="set_mode", path="/Mode", value=1)
        )
        service._bootstrap_controller.register_paths.assert_called_once_with()
        service._bootstrap_controller.fetch_device_info_with_fallback.assert_called_once_with()

# SPDX-License-Identifier: GPL-3.0-or-later
import os
import tempfile
import threading
import time
from types import SimpleNamespace
from unittest.mock import MagicMock, mock_open, patch

from venus_evcharger.runtime.audit import _RuntimeSupportAuditMixin
from venus_evcharger.runtime.support import RuntimeSupportController
from venus_evcharger.control import ControlCommand
from tests.venus_evcharger_runtime_support_support import RuntimeSupportTestCaseBase
from tests.venus_evcharger_test_fixtures import make_auto_metrics, make_runtime_support_service


class TestRuntimeSupportControllerState(RuntimeSupportTestCaseBase):
    def test_runtime_and_worker_state_helpers_cover_defaults_snapshot_and_retries(self) -> None:
        service = make_runtime_support_service(_time_now=lambda: 100.0)
        controller = RuntimeSupportController(service, self._age_five, self._health_nine)
        controller.initialize_runtime_support()
        controller.init_worker_state()
        self.assertEqual(service._worker_poll_interval_seconds, 1.0)
        self.assertFalse(service._worker_snapshot["pm_confirmed"])
        self.assertEqual(service._last_auto_state, "idle")

        partial_service = SimpleNamespace(poll_interval_ms=500, deviceinstance=61)
        partial_controller = RuntimeSupportController(partial_service, self._age_zero, self._health_zero)
        partial_controller.ensure_worker_state()
        partial_controller.ensure_observability_state()
        self.assertEqual(partial_service.auto_input_snapshot_path, "/run/dbus-venus-evcharger-auto-61.json")
        pm_status: dict[str, object] = {"output": True}
        snapshot: dict[str, object] = {"captured_at": 1.0, "pm_status": pm_status}
        partial_service._ensure_worker_state = MagicMock()
        partial_controller.set_worker_snapshot(snapshot)
        pm_status["output"] = False
        self.assertTrue(partial_service._worker_snapshot["pm_status"]["output"])
        partial_controller.update_worker_snapshot(grid_power=-500.0)
        self.assertEqual(partial_controller.get_worker_snapshot()["grid_power"], -500.0)

    def test_runtime_support_setup_helpers_cover_uptime_and_local_version_edges(self) -> None:
        controller = RuntimeSupportController(SimpleNamespace(), self._age_zero, self._health_zero)
        with patch("builtins.open", side_effect=OSError("no uptime")):
            self.assertIsNone(controller._system_uptime_seconds())
        with patch("builtins.open", mock_open(read_data="nope\n")):
            self.assertIsNone(controller._system_uptime_seconds())
        with patch("builtins.open", mock_open(read_data="12.5 0.0\n")):
            self.assertEqual(controller._system_uptime_seconds(), 12.5)
        self.assertIsNone(controller._boot_delayed_update_due_at(100.0, 10.0))
        with patch.object(RuntimeSupportController, "_system_uptime_seconds", return_value=3.0):
            self.assertEqual(controller._boot_delayed_update_due_at(100.0, 10.0), 107.0)
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_root = temp_dir
            state_dir = os.path.join(repo_root, ".bootstrap-state")
            os.makedirs(state_dir, exist_ok=True)
            with open(os.path.join(state_dir, "installed_version"), "w", encoding="utf-8") as handle:
                handle.write("\n")
            with open(os.path.join(repo_root, "version.txt"), "w", encoding="utf-8") as handle:
                handle.write("2.3.4\n")
            self.assertEqual(controller._read_local_version(repo_root), "2.3.4")
        with patch("os.path.isfile", return_value=True), patch("builtins.open", side_effect=OSError("no version")):
            self.assertEqual(controller._read_local_version("/tmp/repo"), "")

    def test_runtime_support_defaults_manifest_source_to_empty_unless_overridden(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            service = make_runtime_support_service()
            controller = RuntimeSupportController(service, self._age_zero, self._health_zero)
            controller.initialize_runtime_support()
            self.assertEqual(service.software_update_manifest_source, "")

        manifest_url = "https://example.invalid/bootstrap_manifest.json"
        with patch.dict(os.environ, {"VENUS_EVCHARGER_MANIFEST_SOURCE": manifest_url}, clear=False):
            service = make_runtime_support_service()
            controller = RuntimeSupportController(service, self._age_zero, self._health_zero)
            controller.initialize_runtime_support()
            self.assertEqual(service.software_update_manifest_source, manifest_url)

    def test_get_system_bus_reuses_cached_bus_until_generation_changes(self) -> None:
        partial_service = SimpleNamespace()
        controller = RuntimeSupportController(partial_service, self._age_zero, self._health_zero)
        partial_service._ensure_system_bus_state = controller.ensure_system_bus_state

        with patch.object(controller, "create_system_bus", side_effect=["bus-a", "bus-b"]) as create_bus:
            first_bus = controller.get_system_bus()
            second_bus = controller.get_system_bus()
            self.assertEqual(first_bus, "bus-a")
            self.assertEqual(second_bus, "bus-a")

            partial_service._system_bus_generation = 1
            third_bus = controller.get_system_bus()
            self.assertEqual(third_bus, "bus-b")

        self.assertEqual(create_bus.call_count, 2)

    def test_runtime_audit_helpers_cover_remaining_scalar_edges(self) -> None:
        service = SimpleNamespace(_last_charger_state_phase_selection=0, _time_now=lambda: "bad", _phase_switch_lockout_selection=None, _phase_switch_lockout_until=200.0, _contactor_fault_counts=[], _contactor_fault_active_reason="")
        self.assertEqual(_RuntimeSupportAuditMixin._observed_phase_for_audit(service), "0")
        self.assertFalse(_RuntimeSupportAuditMixin._phase_lockout_active_for_audit(service))
        self.assertEqual(_RuntimeSupportAuditMixin._contactor_fault_count_for_audit(service), 0)

    def test_worker_snapshot_contract_normalizes_pm_invariants(self) -> None:
        partial_service = SimpleNamespace(poll_interval_ms=500, deviceinstance=61, _time_now=lambda: 100.0)
        controller = RuntimeSupportController(partial_service, self._age_zero, self._health_zero)
        controller.ensure_worker_state()
        partial_service._ensure_worker_state = MagicMock()
        controller.set_worker_snapshot({"captured_at": 10.0, "pm_captured_at": 12.0, "pm_status": {"apower": 1800.0}, "pm_confirmed": True})
        snapshot = controller.get_worker_snapshot()
        self.assertIsNone(snapshot["pm_status"])
        self.assertFalse(snapshot["pm_confirmed"])
        controller.update_worker_snapshot(captured_at=20.0, pm_status={"output": True}, pm_confirmed=True)
        snapshot = controller.get_worker_snapshot()
        self.assertEqual(snapshot["pm_status"], {"output": True})
        self.assertTrue(snapshot["pm_confirmed"])

    def test_audit_helpers_and_watchdog_cover_remaining_branches(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = f"{temp_dir}/auto.log"
            service = make_runtime_support_service(_time_now=lambda: 1000.0, _last_pm_status=None, virtual_startstop=1, _last_auto_metrics={"surplus": None, "grid": None, "soc": None}, auto_audit_log_path=path, auto_audit_log_max_age_hours=0.0, auto_audit_log_repeat_seconds=0.0, _last_auto_audit_event_at=995.0, auto_watchdog_stale_seconds=0.0, started_at=900.0, auto_watchdog_recovery_seconds=0.0, _last_recovery_attempt_at=990.0)
            controller = RuntimeSupportController(service, self._age_zero, self._health_ten)
            self.assertEqual(controller._relay_state_for_audit(service), 0)
            self.assertIn("surplus=na", controller._format_auto_audit_line(service, "waiting", False, 1000.0))
            self.assertEqual(controller._prune_auto_audit_payload(["", "bad-line", "500\told\n", "1500\tnew\n"], 1000.0), ["bad-line", "1500\tnew\n"])
            service.auto_watchdog_stale_seconds = 10.0
            service.auto_watchdog_recovery_seconds = 5.0
            service._last_recovery_attempt_at = None
            service.started_at = 0.0
            service._is_update_stale = self._always_stale
            controller.watchdog_recover(20.0)
            service._reset_system_bus.assert_called_once_with()

    def test_audit_prefers_last_confirmed_relay_state_over_local_placeholder(self) -> None:
        service = make_runtime_support_service(_last_pm_status={"output": True}, _last_pm_status_confirmed=False, _last_confirmed_pm_status={"output": False}, _last_confirmed_pm_status_at=95.0, virtual_startstop=1)
        controller = RuntimeSupportController(service, self._age_zero, self._health_zero)
        self.assertEqual(controller._relay_state_for_audit(service), 0)

    def test_audit_normalizes_state_and_sanitizes_invalid_threshold_metrics(self) -> None:
        service = make_runtime_support_service(_last_auto_state="odd-state", _last_auto_state_code=99, _last_auto_metrics={"surplus": "bad", "grid": -900.0, "soc": 150.0, "profile": 7, "start_threshold": 1200.0, "stop_threshold": 1850.0, "learned_charge_power": -1.0, "learned_charge_power_state": "mystery", "threshold_scale": "bad", "threshold_mode": 4})
        controller = RuntimeSupportController(service, self._age_zero, self._health_zero)
        line = controller._format_auto_audit_line(service, "waiting", False, 100.0)
        self.assertIn("state=idle", line)
        self.assertIn("surplus=na", line)

    def test_audit_ignores_stale_confirmed_relay_state_instead_of_virtual_placeholder(self) -> None:
        service = make_runtime_support_service(_time_now=lambda: 100.0, _last_confirmed_pm_status={"output": True}, _last_confirmed_pm_status_at=80.0, virtual_startstop=1)
        controller = RuntimeSupportController(service, self._age_zero, self._health_zero)
        self.assertEqual(controller._relay_state_for_audit(service), 0)

    def test_audit_and_watchdog_early_returns_cover_remaining_branches(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = f"{temp_dir}/auto.log"
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("999999\tfresh\n")
            service = make_runtime_support_service(_time_now=lambda: 1000.0, auto_audit_log=False, auto_audit_log_path=path, _last_pm_status=None, _last_auto_metrics={"surplus": None, "grid": None, "soc": None}, _last_recovery_attempt_at=995.0, _is_update_stale=self._always_stale)
            controller = RuntimeSupportController(service, self._age_zero, self._health_ten)
            controller._cleanup_auto_audit_log(1000.0)
            controller.write_auto_audit_event("waiting-grid", cached=False)
            controller.watchdog_recover(1000.0)
            service._reset_system_bus.assert_not_called()

    def test_watchdog_retry_helpers_cover_suppression_and_remaining_time_paths(self) -> None:
        service = make_runtime_support_service(
            _last_recovery_attempt_at=95.0,
            auto_watchdog_recovery_seconds=0.0,
            _source_retry_after={"dbus": 110.0},
        )
        controller = RuntimeSupportController(service, self._age_zero, self._health_zero)

        self.assertTrue(controller._watchdog_recovery_suppressed(service, 100.0))
        self.assertEqual(controller.source_retry_remaining("dbus", 100.0), 10)

    def test_watchdog_restarts_configured_service_after_repeated_stale_recoveries(self) -> None:
        service = make_runtime_support_service(
            _is_update_stale=self._always_stale,
            topology_configured=True,
            auto_watchdog_stale_seconds=10.0,
            auto_watchdog_recovery_seconds=0.0,
            auto_watchdog_restart_attempts=2,
            _last_recovery_attempt_at=None,
            _recovery_attempts=1,
            started_at=0.0,
        )
        controller = RuntimeSupportController(service, self._age_zero, self._health_zero)

        with patch.object(RuntimeSupportController, "_exit_for_watchdog_restart") as restart, patch(
            "venus_evcharger.runtime.health.faulthandler.dump_traceback"
        ):
            controller.watchdog_recover(100.0)

        restart.assert_called_once_with()
        service._reset_system_bus.assert_called_once_with()

    def test_watchdog_does_not_restart_unconfigured_service(self) -> None:
        service = make_runtime_support_service(
            _is_update_stale=self._always_stale,
            topology_configured=False,
            host_configured=False,
            auto_watchdog_stale_seconds=10.0,
            auto_watchdog_recovery_seconds=0.0,
            auto_watchdog_restart_attempts=1,
            _last_recovery_attempt_at=None,
            _recovery_attempts=0,
            started_at=0.0,
        )
        controller = RuntimeSupportController(service, self._age_zero, self._health_zero)

        with patch.object(RuntimeSupportController, "_exit_for_watchdog_restart") as restart:
            controller.watchdog_recover(100.0)

        restart.assert_not_called()
        service._reset_system_bus.assert_called_once_with()

    def test_health_helpers_ignore_unknown_failure_keys(self) -> None:
        service = make_runtime_support_service(
            _error_state={"dbus": 0},
            _failure_active={"dbus": False},
            _source_retry_after={},
        )
        controller = RuntimeSupportController(service, self._age_zero, self._health_zero)

        controller.mark_failure("unknown")

        self.assertEqual(service._error_state, {"dbus": 0})
        self.assertEqual(service._failure_active, {"dbus": False})

    def test_async_publish_queue_coalesces_and_flushes_from_mainloop(self) -> None:
        service = make_runtime_support_service()
        service._dbusservice = {"/A": 0, "/UpdateIndex": 0}
        controller = RuntimeSupportController(service, self._age_zero, self._health_zero)
        controller.initialize_runtime_support()
        service._dbus_publish_state = {}

        controller.enqueue_dbus_publish_values([("/A", 1), ("/A", 2)], 100.0)
        controller.enqueue_dbus_update_index_bump(100.0)
        self.assertTrue(controller.flush_dbus_publish_queue())

        self.assertEqual(service._dbusservice["/A"], 2)
        self.assertEqual(service._dbusservice["/UpdateIndex"], 1)
        self.assertEqual(service._dbus_publish_state["/A"], {"value": 2, "updated_at": 100.0})

    def test_async_publish_flush_batches_gateway_proxy_writes(self) -> None:
        service = make_runtime_support_service()
        gateway_proxy = SimpleNamespace(publish_paths=MagicMock())
        service._dbusservice = gateway_proxy
        controller = RuntimeSupportController(service, self._age_zero, self._health_zero)
        controller.initialize_runtime_support()
        controller.mark_mainloop_thread()
        service._dbus_publish_state = {}

        controller.enqueue_dbus_publish_values([("/A", 1), ("/B", 2)], 100.0)
        self.assertTrue(controller.flush_dbus_publish_queue())

        gateway_proxy.publish_paths.assert_called_once_with({"/A": 1, "/B": 2})
        self.assertEqual(service._dbus_publish_state["/A"], {"value": 1, "updated_at": 100.0})
        self.assertEqual(service._dbus_publish_state["/B"], {"value": 2, "updated_at": 100.0})

    def test_async_gateway_publish_edges_skip_empty_and_retry_failed_batch(self) -> None:
        service = make_runtime_support_service()
        gateway_proxy = SimpleNamespace(publish_paths=MagicMock(side_effect=RuntimeError("gateway down")))
        service._dbusservice = gateway_proxy
        controller = RuntimeSupportController(service, self._age_zero, self._health_zero)
        controller.initialize_runtime_support()
        controller.mark_mainloop_thread()
        service._dbus_publish_state = {}
        service._mark_failure = MagicMock()

        self.assertEqual(controller._apply_gateway_publish_values(service, [], gateway_proxy.publish_paths), [])
        gateway_proxy.publish_paths.assert_not_called()

        controller.enqueue_dbus_publish_values([("/A", 1), ("/B", 2)], 100.0)
        self.assertTrue(controller.flush_dbus_publish_queue())

        gateway_proxy.publish_paths.assert_called_once_with({"/A": 1, "/B": 2})
        self.assertEqual(list(service._dbus_publish_pending), [])
        self.assertEqual(service._dbus_publish_state, {})
        service._mark_failure.assert_called_once_with("dbus")

    def test_update_worker_scheduler_returns_while_cycle_is_blocked(self) -> None:
        started = threading.Event()
        release = threading.Event()
        service = make_runtime_support_service()

        def blocking_update() -> bool:
            started.set()
            release.wait(2.0)
            return True

        service._update = blocking_update
        controller = RuntimeSupportController(service, self._age_zero, self._health_zero)
        controller.initialize_runtime_support()
        controller.start_update_worker()
        try:
            first_start = time.monotonic()
            self.assertTrue(controller.schedule_update_cycle())
            self.assertTrue(started.wait(1.0))
            self.assertLess(time.monotonic() - first_start, 1.0)

            second_start = time.monotonic()
            self.assertTrue(controller.schedule_update_cycle())
            self.assertLess(time.monotonic() - second_start, 0.1)
            self.assertGreaterEqual(service._update_worker_skipped_count, 1)
        finally:
            release.set()
            service._update_worker_stop_event.set()
            service._update_worker_event.set()
            service._update_worker_thread.join(1.0)

    def test_control_command_worker_returns_while_backend_command_is_blocked(self) -> None:
        started = threading.Event()
        release = threading.Event()
        command = ControlCommand(name="set_start_stop", path="/StartStop", value=1)
        service = make_runtime_support_service()

        def blocking_command(_command: ControlCommand) -> SimpleNamespace:
            started.set()
            release.wait(2.0)
            return SimpleNamespace(accepted=True)

        service._handle_control_command = blocking_command
        controller = RuntimeSupportController(service, self._age_zero, self._health_zero)
        controller.initialize_runtime_support()
        controller.start_control_command_worker()
        try:
            enqueue_started = time.monotonic()
            self.assertTrue(controller.enqueue_control_command(command))
            self.assertLess(time.monotonic() - enqueue_started, 0.1)
            self.assertTrue(started.wait(1.0))
            self.assertEqual(service._desired_control_values["/StartStop"], 1)
        finally:
            release.set()
            service._control_command_stop_event.set()
            service._control_command_event.set()
            service._control_command_thread.join(1.0)

    def test_runtime_executor_serializes_commands_before_update_cycles(self) -> None:
        order: list[str] = []
        command = ControlCommand(name="set_start_stop", path="/StartStop", value=1)
        service = make_runtime_support_service()
        service._handle_control_command = MagicMock(side_effect=lambda _command: order.append("command") or SimpleNamespace(accepted=True))
        service._update = MagicMock(side_effect=lambda: order.append("update") or True)
        controller = RuntimeSupportController(service, self._age_zero, self._health_zero)
        controller.initialize_runtime_support()
        controller.start_update_worker()
        controller.start_control_command_worker()
        try:
            self.assertTrue(controller.enqueue_control_command(command))
            self.assertTrue(controller.schedule_update_cycle())
            deadline = time.monotonic() + 1.0
            while len(order) < 2 and time.monotonic() < deadline:
                time.sleep(0.01)
        finally:
            service._runtime_executor_stop_event.set()
            service._runtime_executor_event.set()
            service._runtime_executor_thread.join(1.0)

        self.assertEqual(order, ["command", "update"])
        self.assertIs(service._update_worker_thread, service._control_command_thread)

    def test_mainloop_watchdog_dumps_traceback_and_exits_on_stale_heartbeat(self) -> None:
        service = make_runtime_support_service()
        controller = RuntimeSupportController(service, self._age_zero, self._health_zero)
        controller.initialize_runtime_support()
        service._mainloop_heartbeat_at = 1.0
        service._mainloop_watchdog_stale_seconds = 1.0
        service._mainloop_watchdog_stop_event = SimpleNamespace(wait=MagicMock(return_value=False))

        with (
            patch("venus_evcharger.runtime.async_mainloop_watchdog.time.time", return_value=10.0),
            patch.object(RuntimeSupportController, "_dump_mainloop_watchdog_traceback") as dump_traceback,
            patch.object(RuntimeSupportController, "_exit_for_mainloop_watchdog", side_effect=SystemExit) as exit_restart,
            self.assertRaises(SystemExit),
        ):
            controller._mainloop_watchdog_loop()

        dump_traceback.assert_called_once_with(service)
        exit_restart.assert_called_once_with()

    def test_companion_publish_is_coalesced_and_flushed_in_mainloop(self) -> None:
        service = make_runtime_support_service()
        service._companion_dbus_bridge = SimpleNamespace(publish=MagicMock(return_value=True))
        controller = RuntimeSupportController(service, self._age_zero, self._health_zero)
        controller.initialize_runtime_support()
        controller.mark_mainloop_thread()

        self.assertTrue(controller.enqueue_companion_dbus_publish(123.0))
        self.assertTrue(controller.flush_companion_dbus_publish_queue())

        service._companion_dbus_bridge.publish.assert_called_once_with(123.0)

    def test_dbus_thread_guard_rejects_worker_thread_access(self) -> None:
        service = make_runtime_support_service()
        controller = RuntimeSupportController(service, self._age_zero, self._health_zero)
        controller.initialize_runtime_support()
        controller.mark_mainloop_thread()
        errors: list[Exception] = []
        done = threading.Event()

        def worker() -> None:
            try:
                controller.assert_dbus_mainloop_thread("test dbus write")
            except Exception as error:  # pylint: disable=broad-except
                errors.append(error)
            finally:
                done.set()

        thread = threading.Thread(target=worker)
        thread.start()
        self.assertTrue(done.wait(1.0))
        thread.join(1.0)

        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], RuntimeError)

    def test_async_publish_queue_covers_empty_trim_lag_failure_and_budget_paths(self) -> None:
        service = make_runtime_support_service()
        controller = RuntimeSupportController(service, self._age_zero, self._health_zero)
        controller.initialize_runtime_support()
        controller.mark_mainloop_thread()
        self.assertFalse(controller.enqueue_dbus_publish_values([], 100.0))

        service._dbus_publish_max_paths = 1
        self.assertTrue(controller.enqueue_dbus_publish_values([("/A", 1), ("/B", 2)], 100.0))
        self.assertEqual(service._dbus_publish_dropped_count, 1)
        self.assertEqual(list(service._dbus_publish_pending), ["/B"])

        controller._remember_oldest_dbus_publish(service, service._dbus_publish_pending.__class__())
        service._dbusservice = {"/UpdateIndex": 255}
        service._dbus_publish_state = {}
        controller.enqueue_dbus_update_index_bump(100.0)
        self.assertIsNotNone(service._dbus_publish_oldest_queued_at)

        class FailingDbus(dict):
            def __setitem__(self, key: str, value: object) -> None:
                if key == "/B":
                    raise RuntimeError("dbus down")
                super().__setitem__(key, value)

        service._dbusservice = FailingDbus({"/UpdateIndex": 255})
        service._mark_failure = MagicMock()
        service._dbus_publish_budget_seconds = -1.0
        with patch("venus_evcharger.runtime.async_mainloop_publish.logging.warning") as warning:
            self.assertTrue(controller.flush_dbus_publish_queue())

        service._mark_failure.assert_called_once_with("dbus")
        self.assertEqual(service._dbusservice["/UpdateIndex"], 0)
        self.assertGreaterEqual(service._last_dbus_publish_queue_lag_seconds, 0.0)
        self.assertGreaterEqual(warning.call_count, 2)

    def test_async_publish_flush_returns_when_no_dbus_service_and_stops_failed_bumps(self) -> None:
        service = make_runtime_support_service()
        controller = RuntimeSupportController(service, self._age_zero, self._health_zero)
        controller.initialize_runtime_support()
        self.assertTrue(controller.flush_dbus_publish_queue())

        service._dbusservice = {}
        service._dbus_publish_state = {}
        self.assertFalse(controller._bump_update_index_best_effort(service, 100.0))
        controller._flush_update_index_bumps(service, 100.0, 2)

        fresh_service = make_runtime_support_service()
        fresh_controller = RuntimeSupportController(fresh_service, self._age_zero, self._health_zero)
        fresh_controller.initialize_runtime_support()
        fresh_controller.enqueue_dbus_update_index_bump(100.0)
        self.assertIsNotNone(fresh_service._dbus_publish_oldest_queued_at)
        fresh_controller._remember_dbus_publish_queue_lag(fresh_service, 100.0, None)
        fresh_controller._report_dbus_publish_failures(SimpleNamespace(), ["/Path"])

    def test_async_publish_queue_contract_rejects_malformed_pending_queue(self) -> None:
        service = make_runtime_support_service()
        controller = RuntimeSupportController(service, self._age_zero, self._health_zero)
        controller.initialize_runtime_support()

        service._dbus_publish_pending["/bad"] = ("payload", object(), 1.0)
        with self.assertRaisesRegex(TypeError, "_dbus_publish_pending current must be float"):
            controller.enqueue_dbus_publish_values([("/A", 1)], 100.0)

        service._dbus_publish_pending = {}
        with self.assertRaisesRegex(TypeError, "_dbus_publish_pending must be OrderedDict"):
            controller.enqueue_dbus_publish_values([("/A", 1)], 100.0)

    def test_runtime_executor_covers_sync_fallbacks_errors_trimming_and_budget_warnings(self) -> None:
        service = make_runtime_support_service()
        controller = RuntimeSupportController(service, self._age_zero, self._health_zero)
        controller.initialize_runtime_support()

        service._update = MagicMock(return_value=True)
        self.assertTrue(controller.schedule_update_cycle())
        service._update.assert_called_once_with()
        controller._update_worker_loop = controller._update_worker_loop

        command = ControlCommand(name="set_start_stop", path="/StartStop", value=1)
        service._handle_control_command = MagicMock(return_value=SimpleNamespace(accepted=True))
        self.assertTrue(controller.enqueue_control_command(command))
        service._handle_control_command.assert_called_once_with(command)

        service._control_command_async_enabled = True
        service._control_command_max_paths = 1
        first = ControlCommand(name="set_mode", path="/Mode", value=1)
        second = ControlCommand(name="set_current", path="/Current", value=12)
        third = ControlCommand(name="set_current", path="/Current", value=13)
        self.assertTrue(controller.enqueue_control_command(first))
        self.assertTrue(controller.enqueue_control_command(second))
        self.assertTrue(controller.enqueue_control_command(third))
        self.assertNotIn("/Mode", service._desired_control_values)
        self.assertEqual(service._desired_control_values["/Current"], 13)

        service._handle_control_command = MagicMock(side_effect=RuntimeError("boom"))
        service._write_command_budget_seconds = -1.0
        with patch("venus_evcharger.runtime.async_mainloop_control.logging.exception") as log_exception, patch(
            "venus_evcharger.runtime.async_mainloop_control.logging.warning"
        ) as log_warning:
            self.assertTrue(controller._drain_control_commands_once())
        log_exception.assert_called_once()
        log_warning.assert_called_once()

        service._update_worker_pending = True
        service._update = MagicMock(side_effect=RuntimeError("update boom"))
        service._update_worker_budget_seconds = -1.0
        with patch("venus_evcharger.runtime.async_mainloop_executor.logging.exception") as log_exception, patch(
            "venus_evcharger.runtime.async_mainloop_executor.logging.warning"
        ) as log_warning:
            self.assertTrue(controller._run_pending_update_cycle_once())
        log_exception.assert_called_once()
        log_warning.assert_called_once()

    def test_async_control_queue_contract_rejects_malformed_pending_queue(self) -> None:
        service = make_runtime_support_service()
        controller = RuntimeSupportController(service, self._age_zero, self._health_zero)
        controller.initialize_runtime_support()
        service._control_command_async_enabled = True
        command = ControlCommand(name="set_mode", path="/Mode", value=1)

        service._control_command_pending["/bad"] = (True, time.time(), command)
        with self.assertRaisesRegex(TypeError, "_control_command_pending sequence must be int"):
            controller.enqueue_control_command(command)

        service._control_command_pending = {}
        with self.assertRaisesRegex(TypeError, "_control_command_pending must be OrderedDict"):
            controller.enqueue_control_command(command)

    def test_runtime_executor_gateway_core_command_paths(self) -> None:
        service = make_runtime_support_service()
        controller = RuntimeSupportController(service, self._age_zero, self._health_zero)
        controller.initialize_runtime_support()

        service._gateway_core_commands = None
        self.assertFalse(controller._drain_gateway_core_commands_once())

        inbox = SimpleNamespace(load_pending=MagicMock(return_value=[]))
        service._gateway_core_commands = inbox
        self.assertFalse(controller._drain_gateway_core_commands_once())

        service._dbusservice = SimpleNamespace(apply_gateway_write=MagicMock(return_value=True))
        inbox.load_pending = MagicMock(return_value=[("first", {"kind": "user_command", "path": "/Mode", "value": 2})])
        inbox.coalesce = MagicMock(return_value=[("first", {"kind": "user_command", "path": "/Mode", "value": 2})])
        inbox.remove = MagicMock()
        service._dbusservice = SimpleNamespace()
        self.assertFalse(controller._apply_gateway_write_if_supported({"path": "/Mode", "value": 2}))

        service._dbusservice = SimpleNamespace(apply_gateway_write=MagicMock(return_value=True))
        self.assertTrue(controller._drain_gateway_core_commands_once())
        service._dbusservice.apply_gateway_write.assert_called_once_with("/Mode", 2)
        inbox.remove.assert_called_once_with("first")

        service._dbusservice = SimpleNamespace(apply_gateway_write=MagicMock(return_value="yes"))
        with self.assertRaisesRegex(TypeError, "apply_gateway_write must return bool"):
            controller._apply_gateway_write_if_supported({"path": "/Mode", "value": 1})

        service._dbusservice = SimpleNamespace(apply_gateway_write=MagicMock(return_value=False))
        service._control_command_from_write = MagicMock(return_value=ControlCommand(name="set_mode", path="/Mode", value=1))
        service._handle_control_command = MagicMock()
        inbox.coalesce.return_value = [
            ("ignored", {"kind": "refresh_value", "path": "/Mode", "value": 3}),
            ("second", {"kind": "user_command", "path": "/Mode", "value": 1}),
        ]
        self.assertTrue(controller._drain_gateway_core_commands_once())
        service._control_command_from_write.assert_called_once_with("/Mode", 1, source="dbus")
        service._handle_control_command.assert_called_once()
        self.assertEqual(inbox.remove.call_args_list[-2][0][0], "ignored")
        self.assertEqual(inbox.remove.call_args_list[-1][0][0], "second")

    def test_runtime_executor_compatibility_loops_delegate_to_serialized_executor(self) -> None:
        service = make_runtime_support_service()
        controller = RuntimeSupportController(service, self._age_zero, self._health_zero)
        controller.initialize_runtime_support()
        controller._runtime_executor_loop = MagicMock()

        controller._update_worker_loop()
        controller._control_command_worker_loop()

        self.assertEqual(controller._runtime_executor_loop.call_count, 2)

    def test_companion_flush_heartbeat_watchdog_start_and_dump_paths(self) -> None:
        service = make_runtime_support_service()
        controller = RuntimeSupportController(service, self._age_zero, self._health_zero)
        controller.initialize_runtime_support()

        self.assertFalse(controller.flush_companion_dbus_publish_queue())
        self.assertTrue(controller.enqueue_companion_dbus_publish())
        self.assertFalse(controller.flush_companion_dbus_publish_queue())
        self.assertTrue(controller.mainloop_heartbeat_tick())
        self.assertIsNotNone(service._mainloop_heartbeat_at)

        fake_thread = SimpleNamespace(start=MagicMock())
        with patch("venus_evcharger.runtime.async_mainloop_watchdog.threading.Thread", return_value=fake_thread):
            controller.start_mainloop_watchdog()
            controller.start_mainloop_watchdog()
        fake_thread.start.assert_called_once_with()

        with tempfile.TemporaryDirectory() as temp_dir:
            dump_path = os.path.join(temp_dir, "watchdog.log")
            service._mainloop_watchdog_log_path = dump_path
            controller._dump_mainloop_watchdog_traceback(service)
            with open(dump_path, encoding="utf-8") as handle:
                self.assertIn("mainloop watchdog dump", handle.read())

        service._mainloop_watchdog_log_path = "/"
        with patch("venus_evcharger.runtime.async_mainloop_watchdog.logging.debug") as log_debug:
            controller._dump_mainloop_watchdog_traceback(service)
        log_debug.assert_called_once()

    def test_mainloop_watchdog_loop_continues_for_fresh_or_disabled_heartbeat(self) -> None:
        service = make_runtime_support_service()
        controller = RuntimeSupportController(service, self._age_zero, self._health_zero)
        controller.initialize_runtime_support()
        service._mainloop_watchdog_stop_event = SimpleNamespace(wait=MagicMock(side_effect=[False, False, True]))
        service._mainloop_watchdog_stale_seconds = 0.0
        service._mainloop_heartbeat_at = 100.0

        with patch.object(RuntimeSupportController, "_dump_mainloop_watchdog_traceback") as dump_traceback:
            controller._mainloop_watchdog_loop()
        dump_traceback.assert_not_called()

    def test_exit_for_mainloop_watchdog_delegates_to_os_exit(self) -> None:
        with patch("venus_evcharger.runtime.async_mainloop_watchdog.os._exit", side_effect=SystemExit) as os_exit, self.assertRaises(SystemExit):
            RuntimeSupportController._exit_for_mainloop_watchdog()
        os_exit.assert_called_once_with(75)

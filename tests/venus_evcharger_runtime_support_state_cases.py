# SPDX-License-Identifier: GPL-3.0-or-later
import os
import tempfile
import threading
import time
from types import SimpleNamespace
from unittest.mock import MagicMock, mock_open, patch

from venus_evcharger.runtime.audit_fields import RuntimeAuditFields
from venus_evcharger.runtime.async_mainloop_watchdog import MainloopWatchdog
from venus_evcharger.runtime.setup import RuntimeSetup
from venus_evcharger.runtime import async_mainloop_executor as executor_module
from venus_evcharger.control import ControlCommand
from tests.venus_evcharger_runtime_support_support import RuntimeSupportController, RuntimeSupportTestCaseBase
from tests.venus_evcharger_test_fixtures import make_runtime_support_service


class TestRuntimeSupportControllerState(RuntimeSupportTestCaseBase):
    def test_runtime_and_worker_state_helpers_cover_defaults_snapshot_and_retries(self) -> None:
        service = make_runtime_support_service(time_now=lambda: 100.0)
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
            self.assertIsNone(controller.setup._system_uptime_seconds())
        with patch("builtins.open", mock_open(read_data="nope\n")):
            self.assertIsNone(controller.setup._system_uptime_seconds())
        with patch("builtins.open", mock_open(read_data="12.5 0.0\n")):
            self.assertEqual(controller.setup._system_uptime_seconds(), 12.5)
        self.assertIsNone(controller.setup._boot_delayed_update_due_at(100.0, 10.0))
        with patch.object(RuntimeSetup, "_system_uptime_seconds", return_value=3.0):
            self.assertEqual(controller.setup._boot_delayed_update_due_at(100.0, 10.0), 107.0)
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_root = temp_dir
            state_dir = os.path.join(repo_root, ".bootstrap-state")
            os.makedirs(state_dir, exist_ok=True)
            with open(os.path.join(state_dir, "installed_version"), "w", encoding="utf-8") as handle:
                handle.write("\n")
            with open(os.path.join(repo_root, "version.txt"), "w", encoding="utf-8") as handle:
                handle.write("2.3.4\n")
            self.assertEqual(controller.setup._read_local_version(repo_root), "2.3.4")
        with patch("os.path.isfile", return_value=True), patch("builtins.open", side_effect=OSError("no version")):
            self.assertEqual(controller.setup._read_local_version("/tmp/repo"), "")

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

    def test_runtime_audit_helpers_cover_remaining_scalar_edges(self) -> None:
        service = SimpleNamespace(_last_charger_state_phase_selection=0, time_now=lambda: "bad", _phase_switch_lockout_selection=None, _phase_switch_lockout_until=200.0, _contactor_fault_counts=[], _contactor_fault_active_reason="")
        self.assertEqual(RuntimeAuditFields.observed_phase(service), "0")
        self.assertFalse(RuntimeAuditFields.phase_lockout_active(service))
        self.assertEqual(RuntimeAuditFields.contactor_fault_count(service), 0)

    def test_worker_snapshot_contract_normalizes_pm_invariants(self) -> None:
        partial_service = SimpleNamespace(poll_interval_ms=500, deviceinstance=61, time_now=lambda: 100.0)
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
            service = make_runtime_support_service(time_now=lambda: 1000.0, _last_pm_status=None, virtual_startstop=1, _last_auto_metrics={"surplus": None, "grid": None, "soc": None}, auto_audit_log_path=path, auto_audit_log_max_age_hours=0.0, auto_audit_log_repeat_seconds=0.0, _last_auto_audit_event_at=995.0, auto_watchdog_stale_seconds=0.0, started_at=900.0, auto_watchdog_recovery_seconds=0.0, _last_recovery_attempt_at=990.0)
            controller = RuntimeSupportController(service, self._age_zero, self._health_ten)
            controller.reset_system_bus = MagicMock()
            self.assertEqual(controller.audit._relay_state_for_audit(service), 0)
            self.assertIn("surplus=na", controller.audit._format_auto_audit_line(service, "waiting", False, 1000.0))
            self.assertEqual(controller.audit._prune_auto_audit_payload(["", "bad-line", "500\told\n", "1500\tnew\n"], 1000.0), ["bad-line", "1500\tnew\n"])
            service.auto_watchdog_stale_seconds = 10.0
            service.auto_watchdog_recovery_seconds = 5.0
            service._last_recovery_attempt_at = None
            service.started_at = 0.0
            service._is_update_stale = self._always_stale
            controller.watchdog_recover(20.0)
            controller.reset_system_bus.assert_called_once_with()

    def test_audit_prefers_last_confirmed_relay_state_over_local_placeholder(self) -> None:
        service = make_runtime_support_service(_last_pm_status={"output": True}, _last_pm_status_confirmed=False, _last_confirmed_pm_status={"output": False}, _last_confirmed_pm_status_at=95.0, virtual_startstop=1)
        controller = RuntimeSupportController(service, self._age_zero, self._health_zero)
        self.assertEqual(controller.audit._relay_state_for_audit(service), 0)

    def test_audit_normalizes_state_and_sanitizes_invalid_threshold_metrics(self) -> None:
        service = make_runtime_support_service(_last_auto_state="odd-state", _last_auto_state_code=99, _last_auto_metrics={"surplus": "bad", "grid": -900.0, "soc": 150.0, "profile": 7, "start_threshold": 1200.0, "stop_threshold": 1850.0, "learned_charge_power": -1.0, "learned_charge_power_state": "mystery", "threshold_scale": "bad", "threshold_mode": 4})
        controller = RuntimeSupportController(service, self._age_zero, self._health_zero)
        line = controller.audit._format_auto_audit_line(service, "waiting", False, 100.0)
        self.assertIn("state=idle", line)
        self.assertIn("surplus=na", line)

    def test_audit_ignores_stale_confirmed_relay_state_instead_of_virtual_placeholder(self) -> None:
        service = make_runtime_support_service(time_now=lambda: 100.0, _last_confirmed_pm_status={"output": True}, _last_confirmed_pm_status_at=80.0, virtual_startstop=1)
        controller = RuntimeSupportController(service, self._age_zero, self._health_zero)
        self.assertEqual(controller.audit._relay_state_for_audit(service), 0)

    def test_audit_and_watchdog_early_returns_cover_remaining_branches(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = f"{temp_dir}/auto.log"
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("999999\tfresh\n")
            service = make_runtime_support_service(time_now=lambda: 1000.0, auto_audit_log=False, auto_audit_log_path=path, _last_pm_status=None, _last_auto_metrics={"surplus": None, "grid": None, "soc": None}, _last_recovery_attempt_at=995.0, _is_update_stale=self._always_stale)
            controller = RuntimeSupportController(service, self._age_zero, self._health_ten)
            controller.audit._cleanup_auto_audit_log(1000.0)
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

        self.assertTrue(controller.health._watchdog_recovery_suppressed(service, 100.0))
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
        controller.reset_system_bus = MagicMock()

        with patch.object(controller.health, "_exit_for_watchdog_restart") as restart, patch(
            "venus_evcharger.runtime.health.faulthandler.dump_traceback"
        ):
            controller.watchdog_recover(100.0)

        restart.assert_called_once_with()
        controller.reset_system_bus.assert_called_once_with()

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
        controller.reset_system_bus = MagicMock()

        with patch.object(controller.health, "_exit_for_watchdog_restart") as restart:
            controller.watchdog_recover(100.0)

        restart.assert_not_called()
        controller.reset_system_bus.assert_called_once_with()

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
        gateway_proxy = SimpleNamespace(publish_paths=MagicMock(), publish_fields=MagicMock())
        service._dbusservice = gateway_proxy
        controller = RuntimeSupportController(service, self._age_zero, self._health_zero)
        controller.initialize_runtime_support()
        controller.mark_mainloop_thread()
        service._dbus_publish_state = {}

        controller.enqueue_dbus_publish_values([("/A", 1), ("/B", 2)], 100.0)
        controller.enqueue_dbus_publish_fields([("ac_power_w", 1200.0), ("session_time_s", 30)], 101.0)
        self.assertTrue(controller.flush_dbus_publish_queue())

        gateway_proxy.publish_paths.assert_called_once_with({"/A": 1, "/B": 2})
        gateway_proxy.publish_fields.assert_called_once_with({"ac_power_w": 1200.0, "session_time_s": 30})
        self.assertEqual(service._dbus_publish_state["/A"], {"value": 1, "updated_at": 100.0})
        self.assertEqual(service._dbus_publish_state["/B"], {"value": 2, "updated_at": 100.0})
        self.assertEqual(service._dbus_publish_state["/Ac/Power"], {"value": 1200.0, "updated_at": 101.0})
        self.assertEqual(service._dbus_publish_state["/Session/Time"], {"value": 30, "updated_at": 101.0})

    def test_async_publish_fields_skip_empty_and_fallback_to_paths_without_gateway_fields(self) -> None:
        service = make_runtime_support_service()
        service._dbusservice = {"/Ac/Power": 0, "/Session/Time": 0, "/UpdateIndex": 0}
        controller = RuntimeSupportController(service, self._age_zero, self._health_zero)
        controller.initialize_runtime_support()
        controller.mark_mainloop_thread()
        service._dbus_publish_state = {}

        self.assertFalse(controller.enqueue_dbus_publish_fields([], 100.0))
        self.assertTrue(controller.enqueue_dbus_publish_fields([("ac_power_w", 1200.0)], 101.0))
        self.assertTrue(controller.flush_dbus_publish_queue())

        self.assertEqual(service._dbusservice["/Ac/Power"], 1200.0)
        self.assertEqual(service._dbus_publish_state["/Ac/Power"], {"value": 1200.0, "updated_at": 101.0})

    def test_async_publish_fields_report_gateway_field_failures_as_paths(self) -> None:
        service = make_runtime_support_service()
        gateway_proxy = SimpleNamespace(publish_fields=MagicMock(side_effect=RuntimeError("fields down")))
        service._dbusservice = gateway_proxy
        controller = RuntimeSupportController(service, self._age_zero, self._health_zero)
        controller.initialize_runtime_support()

        failures = controller.publish_queue._apply_gateway_publish_fields(
            service,
            [("ac_power_w", (1200.0, 101.0, 99.0)), ("unknown_field", (1, 101.0, 99.0))],
            gateway_proxy.publish_fields,
        )

        gateway_proxy.publish_fields.assert_called_once_with({"ac_power_w": 1200.0, "unknown_field": 1})
        self.assertEqual(failures, ["/Ac/Power"])

    def test_async_gateway_publish_edges_skip_empty_and_retry_failed_batch(self) -> None:
        service = make_runtime_support_service()
        gateway_proxy = SimpleNamespace(publish_paths=MagicMock(side_effect=RuntimeError("gateway down")))
        service._dbusservice = gateway_proxy
        controller = RuntimeSupportController(service, self._age_zero, self._health_zero)
        controller.initialize_runtime_support()
        controller.mark_mainloop_thread()
        service._dbus_publish_state = {}
        controller.mark_failure = MagicMock()

        self.assertEqual(controller.publish_queue._apply_gateway_publish_values(service, [], gateway_proxy.publish_paths), [])
        gateway_proxy.publish_paths.assert_not_called()

        controller.enqueue_dbus_publish_values([("/A", 1), ("/B", 2)], 100.0)
        self.assertTrue(controller.flush_dbus_publish_queue())

        gateway_proxy.publish_paths.assert_called_once_with({"/A": 1, "/B": 2})
        self.assertEqual(list(service._dbus_publish_pending), [])
        self.assertEqual(service._dbus_publish_state, {})
        controller.mark_failure.assert_called_once_with("dbus")

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

        service.handle_control_command = blocking_command
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
        service.handle_control_command = MagicMock(side_effect=lambda _command: order.append("command") or SimpleNamespace(accepted=True))
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

    def test_runtime_executor_start_contract_uses_single_named_owner_thread(self) -> None:
        service = make_runtime_support_service()
        controller = RuntimeSupportController(service, self._age_zero, self._health_zero)
        controller.initialize_runtime_support()

        fake_thread = SimpleNamespace(start=MagicMock())
        with patch("venus_evcharger.runtime.async_mainloop_executor.threading.Thread", return_value=fake_thread) as thread_ctor:
            controller.executor.start()
            controller.executor.start()

        thread_ctor.assert_called_once()
        _, kwargs = thread_ctor.call_args
        self.assertIs(kwargs["target"].__self__, controller.executor)
        self.assertEqual(kwargs["target"].__name__, "_executor_loop")
        self.assertEqual(kwargs["name"], "evcharger-runtime-executor")
        self.assertIs(kwargs["daemon"], True)
        self.assertIs(service._runtime_executor_thread, fake_thread)
        self.assertIs(service._update_worker_thread, fake_thread)
        self.assertIs(service._control_command_thread, fake_thread)
        fake_thread.start.assert_called_once_with()

        partial_service = SimpleNamespace()
        partial_controller = RuntimeSupportController(partial_service, self._age_zero, self._health_zero)
        second_thread = SimpleNamespace(start=MagicMock())
        with patch("venus_evcharger.runtime.async_mainloop_executor.threading.Thread", return_value=second_thread):
            partial_controller.executor.start()
        self.assertIs(partial_service._runtime_executor_thread, second_thread)
        second_thread.start.assert_called_once_with()

    def test_start_update_worker_enables_and_starts_runtime_executor(self) -> None:
        service = make_runtime_support_service()
        controller = RuntimeSupportController(service, self._age_zero, self._health_zero)
        controller.initialize_runtime_support()
        service._update_worker_enabled = False

        with patch.object(controller.executor, "start") as start_runtime:
            controller.start_update_worker()

        self.assertIs(service._update_worker_enabled, True)
        start_runtime.assert_called_once_with()

    def test_schedule_update_cycle_sets_wake_events_and_records_skips(self) -> None:
        partial_service = SimpleNamespace(_update=MagicMock(return_value=True))
        partial_controller = RuntimeSupportController(partial_service, self._age_zero, self._health_zero)
        self.assertTrue(partial_controller.schedule_update_cycle())
        partial_service._update.assert_called_once_with()

        service = make_runtime_support_service()
        controller = RuntimeSupportController(service, self._age_zero, self._health_zero)
        controller.initialize_runtime_support()
        service._update_worker_enabled = True
        service._update_worker_event = SimpleNamespace(set=MagicMock())
        service._runtime_executor_event = SimpleNamespace(set=MagicMock())

        self.assertTrue(controller.schedule_update_cycle())
        self.assertIs(service._update_worker_pending, True)
        self.assertEqual(service._update_worker_skipped_count, 0)
        service._update_worker_event.set.assert_called_once_with()
        service._runtime_executor_event.set.assert_called_once_with()

        service._update_worker_event.set.reset_mock()
        service._runtime_executor_event.set.reset_mock()
        self.assertTrue(controller.schedule_update_cycle())
        self.assertEqual(service._update_worker_skipped_count, 1)
        service._update_worker_event.set.assert_called_once_with()
        service._runtime_executor_event.set.assert_called_once_with()

        service._update_worker_pending = False
        service._update_worker_running = True
        self.assertTrue(controller.schedule_update_cycle())
        self.assertEqual(service._update_worker_skipped_count, 2)

    def test_runtime_executor_stop_requested_observes_each_stop_event(self) -> None:
        service = make_runtime_support_service()
        controller = RuntimeSupportController(service, self._age_zero, self._health_zero)
        controller.initialize_runtime_support()

        self.assertFalse(controller.executor.stop_requested())
        for event in (
            service._runtime_executor_stop_event,
            service._update_worker_stop_event,
            service._control_command_stop_event,
        ):
            event.set()
            self.assertTrue(controller.executor.stop_requested())
            event.clear()

    def test_runtime_executor_wait_for_work_uses_runtime_event_and_clears_all_wakeups(self) -> None:
        service = make_runtime_support_service()
        controller = RuntimeSupportController(service, self._age_zero, self._health_zero)
        controller.initialize_runtime_support()
        service._runtime_executor_event = SimpleNamespace(wait=MagicMock(), clear=MagicMock())
        service._update_worker_event = SimpleNamespace(clear=MagicMock())
        service._control_command_event = SimpleNamespace(clear=MagicMock())

        controller.executor.wait_for_work(service)

        service._runtime_executor_event.wait.assert_called_once_with(0.5)
        service._runtime_executor_event.clear.assert_called_once_with()
        service._update_worker_event.clear.assert_called_once_with()
        service._control_command_event.clear.assert_called_once_with()

    def test_runtime_executor_run_once_reports_each_work_source(self) -> None:
        service = make_runtime_support_service()
        controller = RuntimeSupportController(service, self._age_zero, self._health_zero)
        controller.initialize_runtime_support()

        cases = (
            (False, False, False, False),
            (True, False, False, True),
            (False, True, False, True),
            (False, False, True, True),
        )
        for gateway_work, control_work, update_work, expected in cases:
            with (
                patch.object(controller.executor, "drain_gateway_commands_once", return_value=gateway_work),
                patch.object(controller.control_commands, "drain_once", return_value=control_work),
                patch.object(controller.executor, "run_pending_update_once", return_value=update_work),
            ):
                self.assertIs(controller.executor.run_once(), expected)

    def test_runtime_executor_drain_available_work_stops_when_idle_or_requested(self) -> None:
        service = make_runtime_support_service()
        controller = RuntimeSupportController(service, self._age_zero, self._health_zero)
        controller.initialize_runtime_support()

        with (
            patch.object(controller.executor, "should_continue", side_effect=[True, True, False]) as should_continue,
            patch.object(controller.executor, "run_once", return_value=True) as run_once,
        ):
            controller.executor.drain_available_work()
        self.assertEqual(should_continue.call_count, 3)
        self.assertEqual(run_once.call_count, 2)

        with (
            patch.object(controller.executor, "should_continue", return_value=True) as should_continue,
            patch.object(controller.executor, "run_once", return_value=False) as run_once,
        ):
            controller.executor.drain_available_work()
        should_continue.assert_called_once_with()
        run_once.assert_called_once_with()

    def test_runtime_executor_loop_waits_drains_and_honors_continue_gate(self) -> None:
        service = make_runtime_support_service()
        controller = RuntimeSupportController(service, self._age_zero, self._health_zero)
        controller.initialize_runtime_support()

        with (
            patch.object(controller.executor, "stop_requested", side_effect=[False, True]),
            patch.object(controller.executor, "wait_for_work") as wait_for_work,
            patch.object(controller.executor, "should_continue", return_value=True) as should_continue,
            patch.object(controller.executor, "drain_available_work") as drain_work,
        ):
            controller.executor._executor_loop()
        wait_for_work.assert_called_once_with(service)
        should_continue.assert_called_once_with()
        drain_work.assert_called_once_with()

        with (
            patch.object(controller.executor, "stop_requested", side_effect=[False, True]),
            patch.object(controller.executor, "wait_for_work") as wait_for_work,
            patch.object(controller.executor, "should_continue", return_value=False),
            patch.object(controller.executor, "drain_available_work") as drain_work,
        ):
            controller.executor._executor_loop()
        wait_for_work.assert_called_once_with(service)
        drain_work.assert_not_called()

    def test_run_pending_update_cycle_marks_lifecycle_timestamps_and_budget(self) -> None:
        service = make_runtime_support_service()
        controller = RuntimeSupportController(service, self._age_zero, self._health_zero)
        controller.initialize_runtime_support()

        self.assertFalse(controller.executor.run_pending_update_once())

        service._update_worker_pending = True
        service._update_worker_budget_seconds = 1.0
        service._update = MagicMock(return_value=True)
        with (
            patch("venus_evcharger.runtime.async_mainloop_executor.time.time", side_effect=[100.0, 103.0]),
            patch("venus_evcharger.runtime.async_mainloop_executor.time.monotonic", side_effect=[50.0, 52.5]),
            patch("venus_evcharger.runtime.async_mainloop_executor.logging.warning") as log_warning,
        ):
            self.assertTrue(controller.executor.run_pending_update_once())

        service._update.assert_called_once_with()
        self.assertIs(service._update_worker_pending, False)
        self.assertIs(service._update_worker_running, False)
        self.assertEqual(service._last_update_cycle_started_at, 100.0)
        self.assertEqual(service._last_update_cycle_finished_at, 103.0)
        self.assertEqual(service._last_update_cycle_duration_seconds, 2.5)
        log_warning.assert_called_once_with("Update worker cycle exceeded budget: %.3fs", 2.5)

        service._update_worker_pending = True
        service._update_worker_running = False
        service._update_worker_budget_seconds = 10.0
        service._update = MagicMock(return_value=False)
        with (
            patch("venus_evcharger.runtime.async_mainloop_executor.time.time", side_effect=[200.0, 201.0]),
            patch("venus_evcharger.runtime.async_mainloop_executor.time.monotonic", side_effect=[70.0, 70.25]),
            patch("venus_evcharger.runtime.async_mainloop_executor.logging.warning") as log_warning,
        ):
            self.assertTrue(controller.executor.run_pending_update_once())

        service._update.assert_called_once_with()
        self.assertIs(service._update_worker_running, False)
        self.assertEqual(service._last_update_cycle_duration_seconds, 0.25)
        log_warning.assert_not_called()

    def test_run_pending_update_cycle_logs_errors_and_respects_budget_edge_cases(self) -> None:
        service = make_runtime_support_service()
        controller = RuntimeSupportController(service, self._age_zero, self._health_zero)
        controller.initialize_runtime_support()

        service._update_worker_pending = True
        service._update_worker_budget_seconds = 2.0
        service._update = MagicMock(side_effect=RuntimeError("boom"))
        with (
            patch("venus_evcharger.runtime.async_mainloop_executor.time.time", side_effect=[300.0, 302.0]),
            patch("venus_evcharger.runtime.async_mainloop_executor.time.monotonic", side_effect=[80.0, 82.0]),
            patch("venus_evcharger.runtime.async_mainloop_executor.logging.exception") as log_exception,
            patch("venus_evcharger.runtime.async_mainloop_executor.logging.warning") as log_warning,
        ):
            self.assertTrue(controller.executor.run_pending_update_once())

        log_exception.assert_called_once_with("Async update worker cycle failed")
        log_warning.assert_not_called()
        self.assertIs(service._update_worker_running, False)

        service._update_worker_pending = True
        service._update = MagicMock(return_value=True)
        if hasattr(service, "_update_worker_budget_seconds"):
            delattr(service, "_update_worker_budget_seconds")
        with (
            patch("venus_evcharger.runtime.async_mainloop_executor.time.time", side_effect=[400.0, 406.0]),
            patch("venus_evcharger.runtime.async_mainloop_executor.time.monotonic", side_effect=[90.0, 95.5]),
            patch("venus_evcharger.runtime.async_mainloop_executor.logging.warning") as log_warning,
        ):
            self.assertTrue(controller.executor.run_pending_update_once())

        log_warning.assert_called_once_with("Update worker cycle exceeded budget: %.3fs", 5.5)

        service._update_worker_pending = True
        service._update_worker_budget_seconds = "bad"
        with (
            patch("venus_evcharger.runtime.async_mainloop_executor.time.time", side_effect=[500.0, 506.0]),
            patch("venus_evcharger.runtime.async_mainloop_executor.time.monotonic", side_effect=[100.0, 105.5]),
            patch("venus_evcharger.runtime.async_mainloop_executor.logging.warning") as log_warning,
        ):
            self.assertTrue(controller.executor.run_pending_update_once())

        log_warning.assert_called_once_with("Update worker cycle exceeded budget: %.3fs", 5.5)

    def test_mainloop_watchdog_dumps_traceback_and_exits_on_stale_heartbeat(self) -> None:
        service = make_runtime_support_service()
        controller = RuntimeSupportController(service, self._age_zero, self._health_zero)
        controller.initialize_runtime_support()
        service._mainloop_heartbeat_at = 1.0
        service._mainloop_watchdog_stale_seconds = 1.0
        service._mainloop_watchdog_stop_event = SimpleNamespace(wait=MagicMock(return_value=False))

        with (
            patch("venus_evcharger.runtime.async_mainloop_watchdog.time.time", return_value=10.0),
            patch.object(controller.mainloop_watchdog, "dump_traceback") as dump_traceback,
            patch.object(controller.mainloop_watchdog, "exit_for_restart", side_effect=SystemExit) as exit_restart,
            self.assertRaises(SystemExit),
        ):
            controller.mainloop_watchdog._watchdog_loop()

        dump_traceback.assert_called_once_with(service)
        exit_restart.assert_called_once_with()

    def test_companion_publish_is_coalesced_and_flushed_in_mainloop(self) -> None:
        service = make_runtime_support_service()
        service._companion_dbus_bridge = SimpleNamespace(publish=MagicMock(return_value=True))
        controller = RuntimeSupportController(service, self._age_zero, self._health_zero)
        controller.initialize_runtime_support()
        controller.mark_mainloop_thread()

        self.assertTrue(controller.enqueue_companion_dbus_publish(123.0))
        self.assertTrue(controller.mainloop_watchdog.flush_companion_publish())

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

        controller.publish_queue._remember_oldest_dbus_publish(service, service._dbus_publish_pending.__class__())
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
        controller.mark_failure = MagicMock()
        service._dbus_publish_budget_seconds = -1.0
        with patch("venus_evcharger.runtime.async_mainloop_publish.logging.warning") as warning:
            self.assertTrue(controller.flush_dbus_publish_queue())

        controller.mark_failure.assert_called_once_with("dbus")
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
        self.assertFalse(controller.publish_queue._bump_update_index_best_effort(service, 100.0))
        controller.publish_queue._flush_update_index_bumps(service, 100.0, 2)

        fresh_service = make_runtime_support_service()
        fresh_controller = RuntimeSupportController(fresh_service, self._age_zero, self._health_zero)
        fresh_controller.initialize_runtime_support()
        fresh_controller.enqueue_dbus_update_index_bump(100.0)
        self.assertIsNotNone(fresh_service._dbus_publish_oldest_queued_at)
        fresh_controller.publish_queue._remember_dbus_publish_queue_lag(fresh_service, 100.0, None)
        failure_runtime = SimpleNamespace(mark_failure=MagicMock())
        fresh_controller.publish_queue._report_dbus_publish_failures(
            SimpleNamespace(runtime=failure_runtime),
            ["/Path"],
        )
        failure_runtime.mark_failure.assert_called_once_with("dbus")

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

        command = ControlCommand(name="set_start_stop", path="/StartStop", value=1)
        service.handle_control_command = MagicMock(return_value=SimpleNamespace(accepted=True))
        self.assertTrue(controller.enqueue_control_command(command))
        service.handle_control_command.assert_called_once_with(command)

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

        service.handle_control_command = MagicMock(side_effect=RuntimeError("boom"))
        service._write_command_budget_seconds = -1.0
        with patch("venus_evcharger.runtime.async_mainloop_control.logging.exception") as log_exception, patch(
            "venus_evcharger.runtime.async_mainloop_control.logging.warning"
        ) as log_warning:
            self.assertTrue(controller.control_commands.drain_once())
        log_exception.assert_called_once()
        log_warning.assert_called_once()

        service._update_worker_pending = True
        service._update = MagicMock(side_effect=RuntimeError("update boom"))
        service._update_worker_budget_seconds = -1.0
        with patch("venus_evcharger.runtime.async_mainloop_executor.logging.exception") as log_exception, patch(
            "venus_evcharger.runtime.async_mainloop_executor.logging.warning"
        ) as log_warning:
            self.assertTrue(controller.executor.run_pending_update_once())
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

        if hasattr(service, "_gateway_core_commands"):
            delattr(service, "_gateway_core_commands")
        self.assertFalse(controller.executor.drain_gateway_commands_once())

        service._gateway_core_commands = None
        self.assertFalse(controller.executor.drain_gateway_commands_once())

        inbox = SimpleNamespace(load_pending=MagicMock(return_value=[]))
        service._gateway_core_commands = inbox
        self.assertFalse(controller.executor.drain_gateway_commands_once())

        first_payload = {
            "kind": "user_command",
            "source": "dbus-gui",
            "origin": "gateway-local-write-callback",
            "id": "cmd-mode",
            "created_at": 98.5,
            "path": "/Mode",
            "value": 2,
        }
        service._dbusservice = SimpleNamespace(apply_gateway_write=MagicMock(return_value=True))
        inbox.load_pending = MagicMock(return_value=[("first", first_payload)])
        inbox.coalesce = MagicMock(return_value=[("first", first_payload)])
        inbox.remove = MagicMock()
        service._dbusservice = SimpleNamespace()
        self.assertFalse(controller.executor.apply_gateway_write_if_supported({"path": "/Mode", "value": 2}))

        service._dbusservice = SimpleNamespace(apply_gateway_write=MagicMock(return_value=True))
        with patch("venus_evcharger.runtime.async_mainloop_executor.time.time", return_value=100.0), patch(
            "venus_evcharger.runtime.async_mainloop_executor.logging.info"
        ) as log_info:
            self.assertTrue(controller.executor.drain_gateway_commands_once())
        service._dbusservice.apply_gateway_write.assert_called_once_with("/Mode", 2)
        inbox.coalesce.assert_called_once_with([("first", first_payload)])
        log_info.assert_called_once_with(
            "Gateway user command source=%s origin=%s id=%s path=%s value=%r age_s=%s file=%s",
            "dbus-gui",
            "gateway-local-write-callback",
            "cmd-mode",
            "/Mode",
            2,
            "1.500",
            "first",
        )
        inbox.remove.assert_called_once_with("first")

        service._dbusservice = SimpleNamespace(apply_gateway_write=MagicMock(return_value="yes"))
        with self.assertRaisesRegex(TypeError, "apply_gateway_write must return bool"):
            controller.executor.apply_gateway_write_if_supported({"path": "/Mode", "value": 1})

        service._dbusservice = SimpleNamespace(apply_gateway_write=MagicMock(return_value=False))
        service._control_command_from_write = MagicMock(return_value=ControlCommand(name="set_mode", path="/Mode", value=1))
        service.handle_control_command = MagicMock()
        inbox.coalesce.return_value = [
            ("ignored", {"kind": "refresh_value", "path": "/Mode", "value": 3}),
            ("second", {"kind": "user_command", "path": "/Mode", "value": 1}),
        ]
        self.assertTrue(controller.executor.drain_gateway_commands_once())
        service._control_command_from_write.assert_called_once_with("/Mode", 1, source="dbus")
        service.handle_control_command.assert_called_once()
        self.assertEqual(inbox.remove.call_args_list[-2][0][0], "ignored")
        self.assertEqual(inbox.remove.call_args_list[-1][0][0], "second")

    def test_runtime_executor_gateway_command_boundary_contracts(self) -> None:
        service = make_runtime_support_service()
        controller = RuntimeSupportController(service, self._age_zero, self._health_zero)
        controller.initialize_runtime_support()

        self.assertTrue(controller.executor.is_gateway_user_command({"type": "user_command"}))
        self.assertFalse(controller.executor.is_gateway_user_command({}))

        if hasattr(service, "_dbusservice"):
            delattr(service, "_dbusservice")
        self.assertFalse(controller.executor.apply_gateway_write_if_supported({"path": "/Mode", "value": 1}))

        service._dbusservice = SimpleNamespace(apply_gateway_write=MagicMock(return_value=True))
        self.assertTrue(controller.executor.apply_gateway_write_if_supported({"value": 5}))
        service._dbusservice.apply_gateway_write.assert_called_once_with("", 5)

        command = ControlCommand(name="set_mode", path="", value=1)
        service._control_command_from_write = MagicMock(return_value=command)
        service.handle_control_command = MagicMock()
        controller.executor.dispatch_gateway_control_command({"value": 1})
        service._control_command_from_write.assert_called_once_with("", 1, source="dbus")
        service.handle_control_command.assert_called_once_with(command)

        with patch("venus_evcharger.runtime.async_mainloop_executor.logging.info") as log_info:
            controller.executor.handle_gateway_command({"kind": "user_command", "path": "/Mode", "value": 1})
        self.assertEqual(log_info.call_args.args[-1], "unknown")

    def test_runtime_executor_gateway_command_metadata_helpers(self) -> None:
        self.assertFalse(executor_module._update_worker_enabled(SimpleNamespace()))
        self.assertFalse(executor_module._update_worker_enabled(SimpleNamespace(_update_worker_enabled=False)))
        self.assertTrue(executor_module._update_worker_enabled(SimpleNamespace(_update_worker_enabled=True)))
        self.assertEqual(executor_module._update_worker_budget_seconds(SimpleNamespace()), 5.0)
        self.assertEqual(executor_module._update_worker_budget_seconds(SimpleNamespace(_update_worker_budget_seconds="bad")), 5.0)
        self.assertEqual(executor_module._update_worker_budget_seconds(SimpleNamespace(_update_worker_budget_seconds=0.0)), 0.0)
        self.assertEqual(executor_module._update_worker_budget_seconds(SimpleNamespace(_update_worker_budget_seconds=2.5)), 2.5)
        self.assertEqual(executor_module._gateway_command_text({"source": " dbus-gui "}, "source", "unknown"), "dbus-gui")
        self.assertEqual(executor_module._gateway_command_text({"source": " "}, "source", "unknown"), "unknown")
        self.assertEqual(executor_module._gateway_command_age_label({"created_at": 98.0}, 100.0), "2.000")
        self.assertEqual(executor_module._gateway_command_age_label({"created_at": 101.0}, 100.0), "0.000")
        self.assertEqual(executor_module._gateway_command_age_label({"created_at": 1.0}, 100.0), "99.000")
        self.assertEqual(executor_module._gateway_command_age_label({"created_at": 0.0}, 100.0), "unknown")
        self.assertEqual(executor_module._gateway_command_age_label({"created_at": "bad"}, 100.0), "unknown")
        with patch("venus_evcharger.runtime.async_mainloop_executor.logging.info") as log_info:
            executor_module._log_gateway_user_command({}, command_file="", now=100.0)
        log_info.assert_called_once_with(
            "Gateway user command source=%s origin=%s id=%s path=%s value=%r age_s=%s file=%s",
            "unknown",
            "unknown",
            "unknown",
            "",
            None,
            "unknown",
            "unknown",
        )

    def test_companion_flush_heartbeat_watchdog_start_and_dump_paths(self) -> None:
        service = make_runtime_support_service()
        controller = RuntimeSupportController(service, self._age_zero, self._health_zero)
        controller.initialize_runtime_support()

        self.assertFalse(controller.mainloop_watchdog.flush_companion_publish())
        self.assertTrue(controller.enqueue_companion_dbus_publish())
        self.assertFalse(controller.mainloop_watchdog.flush_companion_publish())
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
            controller.mainloop_watchdog.dump_traceback(service)
            with open(dump_path, encoding="utf-8") as handle:
                self.assertIn("mainloop watchdog dump", handle.read())

        service._mainloop_watchdog_log_path = "/"
        with patch("venus_evcharger.runtime.async_mainloop_watchdog.logging.debug") as log_debug:
            controller.mainloop_watchdog.dump_traceback(service)
        log_debug.assert_called_once()

    def test_mainloop_watchdog_loop_continues_for_fresh_or_disabled_heartbeat(self) -> None:
        service = make_runtime_support_service()
        controller = RuntimeSupportController(service, self._age_zero, self._health_zero)
        controller.initialize_runtime_support()
        service._mainloop_watchdog_stop_event = SimpleNamespace(wait=MagicMock(side_effect=[False, False, True]))
        service._mainloop_watchdog_stale_seconds = 0.0
        service._mainloop_heartbeat_at = 100.0

        with patch.object(MainloopWatchdog, "dump_traceback") as dump_traceback:
            controller.mainloop_watchdog._watchdog_loop()
        dump_traceback.assert_not_called()

    def test_exit_for_mainloop_watchdog_delegates_to_os_exit(self) -> None:
        with patch("venus_evcharger.runtime.async_mainloop_watchdog.os._exit", side_effect=SystemExit) as os_exit, self.assertRaises(SystemExit):
            MainloopWatchdog.exit_for_restart()
        os_exit.assert_called_once_with(75)

# SPDX-License-Identifier: GPL-3.0-or-later
import os
import tempfile
from types import SimpleNamespace
from unittest.mock import MagicMock, mock_open, patch

from venus_evcharger.runtime.audit import _RuntimeSupportAuditMixin
from venus_evcharger.runtime.support import RuntimeSupportController
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

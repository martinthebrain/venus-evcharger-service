# SPDX-License-Identifier: GPL-3.0-or-later
"""Deterministic contracts for runtime health and watchdog behavior."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

from venus_evcharger.runtime.health import RuntimeHealthMonitor


def _health(service: object) -> RuntimeHealthMonitor:
    age_seconds_mock = MagicMock(return_value=17)
    state_store_mock = MagicMock()
    setattr(service, "_test_age_seconds_mock", age_seconds_mock)
    setattr(service, "_test_state_store_mock", state_store_mock)
    monitor = RuntimeHealthMonitor(service, age_seconds_mock, state_store_mock)
    return monitor


def _service(**overrides: object) -> SimpleNamespace:
    values: dict[str, object] = {
        "_ensure_observability_state": MagicMock(),
        "auto_watchdog_stale_seconds": 10.0,
        "_last_successful_update_at": 90.0,
        "started_at": 80.0,
        "auto_watchdog_recovery_seconds": 5.0,
        "_last_recovery_attempt_at": None,
        "auto_watchdog_restart_attempts": 3,
        "topology_configured": True,
        "host_configured": True,
        "_recovery_attempts": 0,
        "_refresh_auto_input_snapshot": MagicMock(),
        "_is_update_stale": MagicMock(return_value=False),
        "_state_summary": MagicMock(return_value="state"),
        "_warning_state": {},
        "_error_state": {"dbus": 0},
        "_failure_active": {"dbus": False},
        "_source_retry_after": {},
        "auto_dbus_backoff_base_seconds": 5.0,
    }
    values.update(overrides)
    service = SimpleNamespace(**values)
    service.runtime = SimpleNamespace(
        refresh_auto_input_snapshot=service._refresh_auto_input_snapshot,
    )
    service.state = SimpleNamespace(summary=service._state_summary)
    return service


class RuntimeHealthContractTests(unittest.TestCase):
    def test_float_attr_accepts_numeric_values_and_uses_numeric_default(self) -> None:
        self.assertEqual(RuntimeHealthMonitor._float_attr(2), 2.0)
        self.assertEqual(RuntimeHealthMonitor._float_attr(2.5), 2.5)
        self.assertEqual(RuntimeHealthMonitor._float_attr(True), 1.0)
        self.assertEqual(RuntimeHealthMonitor._float_attr("2", 7.5), 7.5)
        self.assertEqual(RuntimeHealthMonitor._float_attr(None), 0.0)

    def test_update_stale_honors_disabled_boundary_and_last_success(self) -> None:
        service = _service()
        health = _health(service)
        self.assertFalse(health.is_update_stale(100.0))
        self.assertTrue(health.is_update_stale(100.01))
        service.auto_watchdog_stale_seconds = 0.0
        self.assertFalse(health.is_update_stale(1000.0))
        self.assertEqual(service._test_state_store_mock.ensure_observability_state.call_count, 3)

    def test_update_stale_uses_started_at_until_first_success_and_system_clock(self) -> None:
        service = _service(_last_successful_update_at=None, started_at=90.0)
        health = _health(service)
        self.assertFalse(health.is_update_stale(100.0))
        self.assertTrue(health.is_update_stale(100.01))
        service.started_at = "bad"
        with patch("venus_evcharger.runtime.health.time.time", return_value=11.0) as clock:
            self.assertTrue(health.is_update_stale())
        clock.assert_called_once_with()

    def test_update_stale_handles_partially_initialized_runtime_state(self) -> None:
        ensure_state = MagicMock()
        service = SimpleNamespace(
            _ensure_observability_state=ensure_state,
            started_at=-100.0,
            _last_successful_update_at=None,
        )
        health = _health(service)
        self.assertFalse(health.is_update_stale(100.0))

        health.service = SimpleNamespace(
            _ensure_observability_state=ensure_state,
            auto_watchdog_stale_seconds=1.0,
            _last_successful_update_at=0.0,
        )
        self.assertTrue(health.is_update_stale(2.0))

        health.service = SimpleNamespace(
            _ensure_observability_state=ensure_state,
            auto_watchdog_stale_seconds=10.0,
            started_at=90.0,
        )
        self.assertFalse(health.is_update_stale(100.0))

        health.service = SimpleNamespace(
            _ensure_observability_state=ensure_state,
            auto_watchdog_stale_seconds=10.0,
            _last_successful_update_at=None,
        )
        self.assertTrue(health.is_update_stale(11.0))
        self.assertEqual(service._test_state_store_mock.ensure_observability_state.call_count, 4)

    def test_watchdog_base_timestamp_prefers_success_then_start_and_default(self) -> None:
        self.assertEqual(RuntimeHealthMonitor._watchdog_base_timestamp(_service()), 90.0)
        self.assertEqual(
            RuntimeHealthMonitor._watchdog_base_timestamp(
                _service(_last_successful_update_at=None, started_at=80)
            ),
            80.0,
        )
        self.assertEqual(RuntimeHealthMonitor._watchdog_base_timestamp(SimpleNamespace()), 0.0)

    def test_watchdog_recovery_suppression_contracts_are_strict(self) -> None:
        self.assertFalse(
            RuntimeHealthMonitor._watchdog_recovery_suppressed(
                _service(_last_recovery_attempt_at=None),
                100.0,
            )
        )
        self.assertTrue(
            RuntimeHealthMonitor._watchdog_recovery_suppressed(
                _service(_last_recovery_attempt_at=96.0),
                100.0,
            )
        )
        self.assertFalse(
            RuntimeHealthMonitor._watchdog_recovery_suppressed(
                _service(_last_recovery_attempt_at=95.0),
                100.0,
            )
        )
        self.assertTrue(
            RuntimeHealthMonitor._watchdog_recovery_suppressed(
                _service(auto_watchdog_recovery_seconds=0.0, _last_recovery_attempt_at=1.0),
                100.0,
            )
        )
        self.assertTrue(
            RuntimeHealthMonitor._watchdog_recovery_suppressed(
                SimpleNamespace(_last_recovery_attempt_at=1.0),
                100.0,
            )
        )
        self.assertFalse(
            RuntimeHealthMonitor._watchdog_recovery_suppressed(
                _service(auto_watchdog_recovery_seconds=1.0, _last_recovery_attempt_at=1.0),
                100.0,
            )
        )
        self.assertFalse(RuntimeHealthMonitor._watchdog_recovery_suppressed(SimpleNamespace(), 100.0))

    def test_watchdog_reset_refreshes_the_semantic_input_snapshot(self) -> None:
        service = _service()
        RuntimeHealthMonitor._perform_watchdog_reset(service)
        service._refresh_auto_input_snapshot.assert_called_once_with()

    def test_watchdog_restart_attempts_validate_type_sign_and_truncation(self) -> None:
        self.assertEqual(RuntimeHealthMonitor._watchdog_restart_attempts(_service(auto_watchdog_restart_attempts=3.9)), 3)
        self.assertEqual(RuntimeHealthMonitor._watchdog_restart_attempts(_service(auto_watchdog_restart_attempts=-2)), 0)
        self.assertEqual(RuntimeHealthMonitor._watchdog_restart_attempts(_service(auto_watchdog_restart_attempts=True)), 0)
        self.assertEqual(RuntimeHealthMonitor._watchdog_restart_attempts(_service(auto_watchdog_restart_attempts="3")), 0)
        self.assertEqual(RuntimeHealthMonitor._watchdog_restart_attempts(SimpleNamespace()), 0)

    def test_watchdog_restart_topology_prefers_explicit_topology_then_host_fallback(self) -> None:
        self.assertTrue(RuntimeHealthMonitor._watchdog_restart_enabled_for_topology(_service()))
        self.assertFalse(
            RuntimeHealthMonitor._watchdog_restart_enabled_for_topology(
                _service(topology_configured=False, host_configured=True)
            )
        )
        self.assertTrue(
            RuntimeHealthMonitor._watchdog_restart_enabled_for_topology(
                SimpleNamespace(host_configured=True)
            )
        )
        self.assertFalse(RuntimeHealthMonitor._watchdog_restart_enabled_for_topology(SimpleNamespace()))

    def test_watchdog_restart_due_requires_budget_topology_and_threshold(self) -> None:
        service = _service(_recovery_attempts=3)
        self.assertTrue(RuntimeHealthMonitor._watchdog_restart_due(service))
        service._recovery_attempts = 2
        self.assertFalse(RuntimeHealthMonitor._watchdog_restart_due(service))
        service.auto_watchdog_restart_attempts = 0
        self.assertFalse(RuntimeHealthMonitor._watchdog_restart_due(service))
        service.auto_watchdog_restart_attempts = 1
        service.topology_configured = False
        self.assertFalse(RuntimeHealthMonitor._watchdog_restart_due(service))

        service = _service(
            auto_watchdog_restart_attempts=1,
            _recovery_attempts=1,
            topology_configured=True,
        )
        self.assertTrue(RuntimeHealthMonitor._watchdog_restart_due(service))
        del service._recovery_attempts
        self.assertFalse(RuntimeHealthMonitor._watchdog_restart_due(service))

    def test_watchdog_exit_uses_supervisor_restart_code(self) -> None:
        with patch("venus_evcharger.runtime.health.os._exit", side_effect=SystemExit) as exit_process:
            with self.assertRaises(SystemExit):
                RuntimeHealthMonitor._exit_for_watchdog_restart()
        exit_process.assert_called_once_with(75)

    def test_restart_process_logs_dumps_and_exits_with_exact_context(self) -> None:
        service = _service(_recovery_attempts=4)
        health = _health(service)
        with (
            patch.object(health, "_watchdog_base_timestamp", return_value=80.0) as base,
            patch("venus_evcharger.runtime.health.logging.critical") as critical,
            patch("venus_evcharger.runtime.health.faulthandler.dump_traceback") as dump,
            patch.object(health, "_exit_for_watchdog_restart") as restart,
        ):
            health._restart_process_after_stale_watchdog(service, 100.0)
        base.assert_called_once_with(service)
        service._test_age_seconds_mock.assert_called_once_with(80.0, 100.0)
        service._state_summary.assert_called_once_with()
        critical.assert_called_once_with(
            "Watchdog restart after %s stale recovery attempts over %ss (%s)",
            4,
            17,
            "state",
        )
        dump.assert_called_once_with(all_threads=True)
        restart.assert_called_once_with()

    def test_restart_process_tolerates_supported_traceback_errors(self) -> None:
        service = _service()
        health = _health(service)
        with (
            patch("venus_evcharger.runtime.health.faulthandler.dump_traceback", side_effect=RuntimeError("blocked")),
            patch("venus_evcharger.runtime.health.logging.debug") as debug,
            patch.object(health, "_exit_for_watchdog_restart") as restart,
        ):
            health._restart_process_after_stale_watchdog(service, 100.0)
        debug.assert_called_once_with(
            "Unable to dump watchdog traceback before restart: %s",
            unittest.mock.ANY,
        )
        self.assertIsInstance(debug.call_args.args[1], RuntimeError)
        self.assertEqual(str(debug.call_args.args[1]), "blocked")
        restart.assert_called_once_with()

    def test_watchdog_recover_returns_for_fresh_or_suppressed_service(self) -> None:
        service = _service()
        health = _health(service)
        health.watchdog_recover(100.0)
        service._refresh_auto_input_snapshot.assert_not_called()
        service._last_successful_update_at = 80.0
        service._last_recovery_attempt_at = 99.0
        health.watchdog_recover(100.0)
        service._refresh_auto_input_snapshot.assert_not_called()

    def test_watchdog_recover_updates_state_resets_logs_and_checks_restart(self) -> None:
        service = _service(_last_successful_update_at=80.0, _recovery_attempts=1)
        health = _health(service)
        with (
            patch.object(health, "_perform_watchdog_reset") as reset,
            patch.object(health, "_watchdog_base_timestamp", return_value=80.0) as base,
            patch.object(health, "_watchdog_restart_due", return_value=False) as due,
            patch("venus_evcharger.runtime.health.logging.warning") as warning,
        ):
            health.watchdog_recover(100.0)
        self.assertEqual(service._last_recovery_attempt_at, 100.0)
        self.assertEqual(service._recovery_attempts, 2)
        reset.assert_called_once_with(service)
        base.assert_called_once_with(service)
        service._test_age_seconds_mock.assert_called_once_with(80.0, 100.0)
        service._state_summary.assert_called_once_with()
        warning.assert_called_once_with(
            "Watchdog recovery attempt %s after stale update period of %ss (%s)",
            2,
            17,
            "state",
        )
        due.assert_called_once_with(service)

    def test_watchdog_recover_escalates_when_restart_is_due(self) -> None:
        service = _service(_last_successful_update_at=80.0)
        health = _health(service)
        with (
            patch.object(health, "_watchdog_restart_due", return_value=True),
            patch.object(health, "_restart_process_after_stale_watchdog") as restart,
        ):
            health.watchdog_recover(100.0)
        restart.assert_called_once_with(service, 100.0)

    def test_warning_throttle_logs_new_and_expired_keys_but_not_boundary(self) -> None:
        service = _service(_warning_state={"key": 70.0})
        health = _health(service)
        with (
            patch("venus_evcharger.runtime.health.time.time", side_effect=(100.0, 100.01, 100.02)),
            patch("venus_evcharger.runtime.health.logging.warning") as warning,
        ):
            health.warning_throttled("key", 30.0, "message %s", "first", extra={"x": 1})
            health.warning_throttled(
                "key",
                30.0,
                "message %s",
                "second",
                extra={"x": 2},
            )
            health.warning_throttled("new", 30.0, "new message")
        self.assertEqual(
            warning.call_args_list,
            [call("message %s", "second", extra={"x": 2}), call("new message")],
        )
        self.assertEqual(service._warning_state, {"key": 100.01, "new": 100.02})
        self.assertEqual(service._test_state_store_mock.ensure_observability_state.call_count, 3)

    def test_failure_and_recovery_tracking_only_changes_known_counters(self) -> None:
        service = _service()
        health = _health(service)
        health.mark_failure("dbus")
        health.mark_failure("dbus")
        health.mark_failure("unknown")
        self.assertEqual(service._error_state, {"dbus": 2})
        self.assertEqual(service._failure_active, {"dbus": True})
        with patch("venus_evcharger.runtime.health.logging.info") as info:
            health.mark_recovery("dbus", "recovered %s", "dbus")
            health.mark_recovery("dbus", "again")
        info.assert_called_once_with("recovered %s", "dbus")
        self.assertIs(service._failure_active["dbus"], False)
        self.assertEqual(service._source_retry_after["dbus"], 0.0)

    def test_source_retry_ready_uses_clock_and_inclusive_deadline(self) -> None:
        service = _service(_source_retry_after={"dbus": 100.0})
        health = _health(service)
        self.assertFalse(health.source_retry_ready("dbus", 99.99))
        self.assertTrue(health.source_retry_ready("dbus", 100.0))
        self.assertTrue(health.source_retry_ready("missing", 0.0))
        with patch("venus_evcharger.runtime.health.time.time", return_value=101.0) as clock:
            self.assertTrue(health.source_retry_ready("dbus"))
        clock.assert_called_once_with()

    def test_source_retry_remaining_truncates_future_delay_and_clamps_expired(self) -> None:
        service = _service(_source_retry_after={"dbus": 101.9})
        health = _health(service)
        self.assertEqual(health.source_retry_remaining("dbus", 100.0), 1)
        service._source_retry_after["short"] = 100.5
        self.assertEqual(health.source_retry_remaining("short", 100.0), 0)
        self.assertEqual(health.source_retry_remaining("dbus", 101.9), 0)
        self.assertEqual(health.source_retry_remaining("missing", 100.0), 0)
        with patch("venus_evcharger.runtime.health.time.time", return_value=100.0) as clock:
            self.assertEqual(health.source_retry_remaining("dbus"), 1)
        clock.assert_called_once_with()

    def test_delay_source_retry_uses_documented_fallback_when_config_is_absent(self) -> None:
        service = _service()
        del service.auto_dbus_backoff_base_seconds
        health = _health(service)
        health.delay_source_retry("dbus", now=100.0)
        self.assertEqual(service._source_retry_after, {"dbus": 105.0})

    def test_delay_source_retry_uses_default_minimum_explicit_clamp_and_clock(self) -> None:
        service = _service(auto_dbus_backoff_base_seconds=0.5)
        health = _health(service)
        health.delay_source_retry("default", now=100.0)
        health.delay_source_retry("negative", now=100.0, delay_seconds=-2.0)
        health.delay_source_retry("explicit", now=100.0, delay_seconds=2.5)
        with patch("venus_evcharger.runtime.health.time.time", return_value=200.0) as clock:
            health.delay_source_retry("clock")
        self.assertEqual(
            service._source_retry_after,
            {"default": 101.0, "negative": 100.0, "explicit": 102.5, "clock": 201.0},
        )
        clock.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()

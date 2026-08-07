# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, mock_open, patch

from venus_evcharger.runtime.support import RuntimeSupportController
from tests.venus_evcharger_test_fixtures import make_runtime_support_service


def _controller() -> tuple[SimpleNamespace, RuntimeSupportController]:
    service = make_runtime_support_service()
    controller = RuntimeSupportController(service, lambda _value, _now: 0, lambda _reason: 0)
    controller.initialize_runtime_support()
    return service, controller


class RuntimeAsyncMainloopWatchdogContractTests(unittest.TestCase):
    def test_heartbeat_records_exact_time_and_returns_true(self) -> None:
        service, controller = _controller()
        service._process_started_at = 100.0
        with (
            patch("venus_evcharger.runtime.async_mainloop_watchdog.time.monotonic", return_value=456.0),
            patch("venus_evcharger.runtime.async_mainloop_watchdog.time.time", return_value=123.5),
            patch("venus_evcharger.runtime.async_mainloop_watchdog.os.getpid", return_value=77),
            patch("venus_evcharger.runtime.async_mainloop_watchdog.write_text_atomically") as write_heartbeat,
        ):
            self.assertTrue(controller.mainloop_heartbeat_tick())
        self.assertEqual(service._mainloop_heartbeat_at, 123.5)
        self.assertEqual(service._mainloop_heartbeat_monotonic, 456.0)
        self.assertEqual(service._process_heartbeat_last_write_monotonic, 456.0)
        write_heartbeat.assert_called_once_with(
            "/run/dbus-venus-evcharger-60.heartbeat.json",
            (
                '{"mainloop_heartbeat_at":123.5,"pid":77,'
                '"process_heartbeat_at":123.5,"process_started_at":100.0}'
            ),
        )

    def test_process_heartbeat_is_throttled_and_recovers_from_monotonic_reset(self) -> None:
        service, controller = _controller()
        service._process_started_at = 100.0
        with (
            patch(
                "venus_evcharger.runtime.async_mainloop_watchdog.time.monotonic",
                side_effect=(100.0, 104.999, 105.0, 90.0),
            ),
            patch(
                "venus_evcharger.runtime.async_mainloop_watchdog.time.time",
                side_effect=(10.0, 11.0, 12.0, 13.0),
            ),
            patch("venus_evcharger.runtime.async_mainloop_watchdog.write_text_atomically") as write_heartbeat,
        ):
            for _unused in range(4):
                controller.mainloop_heartbeat_tick()

        self.assertEqual(write_heartbeat.call_count, 3)
        self.assertEqual(service._process_heartbeat_last_write_monotonic, 90.0)

    def test_process_heartbeat_due_normalizes_invalid_state_and_interval(self) -> None:
        service, controller = _controller()
        del service._process_heartbeat_last_write_monotonic
        self.assertTrue(controller.mainloop_watchdog._heartbeat_due(service, 100.0))
        for last_write in (None, True, "invalid"):
            with self.subTest(last_write=last_write):
                service._process_heartbeat_last_write_monotonic = last_write
                self.assertTrue(controller.mainloop_watchdog._heartbeat_due(service, 100.0))

        service._process_heartbeat_last_write_monotonic = 100.0
        service._process_heartbeat_interval_seconds = 0.0
        self.assertFalse(controller.mainloop_watchdog._heartbeat_due(service, 100.0))
        self.assertFalse(controller.mainloop_watchdog._heartbeat_due(service, 100.999))
        self.assertTrue(controller.mainloop_watchdog._heartbeat_due(service, 101.0))

    def test_process_heartbeat_rejects_non_run_path_and_throttles_write_failures(self) -> None:
        service, controller = _controller()
        service._process_heartbeat_path = "/data/heartbeat.json"
        with (
            patch("venus_evcharger.runtime.async_mainloop_watchdog.logging.error") as error,
            patch("venus_evcharger.runtime.async_mainloop_watchdog.write_text_atomically") as write_heartbeat,
        ):
            controller.mainloop_watchdog._write_process_heartbeat_if_due(service, 100.0, 10.0)
        write_heartbeat.assert_not_called()
        error.assert_called_once_with("Refusing process heartbeat path outside /run: %s", "/data/heartbeat.json")

        service._process_heartbeat_path = "/run/heartbeat.json"
        failure = OSError("read-only")
        with (
            patch(
                "venus_evcharger.runtime.async_mainloop_watchdog.write_text_atomically",
                side_effect=failure,
            ),
            patch("venus_evcharger.runtime.async_mainloop_watchdog.logging.warning") as warning,
        ):
            controller.mainloop_watchdog._write_process_heartbeat_if_due(service, 100.0, 10.0)
            controller.mainloop_watchdog._write_process_heartbeat_if_due(service, 101.0, 11.0)

        self.assertEqual(service._process_heartbeat_last_write_monotonic, 100.0)
        warning.assert_called_once_with(
            "Unable to write process heartbeat to %s: %s",
            "/run/heartbeat.json",
            failure,
        )

    def test_watchdog_start_is_idempotent_and_constructs_exact_thread(self) -> None:
        service, controller = _controller()
        existing = object()
        service._mainloop_watchdog_thread = existing
        with patch("venus_evcharger.runtime.async_mainloop_watchdog.threading.Thread") as thread_type:
            controller.start_mainloop_watchdog()
        thread_type.assert_not_called()
        self.assertIs(service._mainloop_watchdog_thread, existing)

        service._mainloop_watchdog_thread = None
        thread = MagicMock()
        with patch("venus_evcharger.runtime.async_mainloop_watchdog.threading.Thread", return_value=thread) as thread_type:
            controller.start_mainloop_watchdog()
        thread_type.assert_called_once_with(
            target=controller.mainloop_watchdog._watchdog_loop,
            name="evcharger-mainloop-watchdog",
            daemon=True,
        )
        self.assertIs(service._mainloop_watchdog_thread, thread)
        thread.start.assert_called_once_with()

    def test_watchdog_loop_waits_exact_interval_and_ignores_disabled_staleness(self) -> None:
        service, controller = _controller()
        service._mainloop_watchdog_interval_seconds = 1.5
        service._mainloop_watchdog_stale_seconds = 0.0
        service._mainloop_watchdog_stop_event.wait = MagicMock(side_effect=(False, True))
        with (
            patch.object(controller.mainloop_watchdog, "check") as check,
            patch("venus_evcharger.runtime.async_mainloop_watchdog.time.monotonic") as now,
        ):
            controller.mainloop_watchdog._watchdog_loop()

        self.assertEqual(service._mainloop_watchdog_stop_event.wait.call_args_list[0].args, (1.5,))
        self.assertEqual(service._mainloop_watchdog_stop_event.wait.call_count, 2)
        now.assert_not_called()
        check.assert_called_once_with(service)

    def test_watchdog_loop_ignores_recent_heartbeat(self) -> None:
        service, controller = _controller()
        service._mainloop_heartbeat_monotonic = 95.0
        service._mainloop_watchdog_stale_seconds = 10.0

        with (
            patch.object(controller.mainloop_watchdog, "dump_traceback") as dump_traceback,
            patch.object(controller.mainloop_watchdog, "exit_for_restart") as exit_for_restart,
            patch("venus_evcharger.runtime.async_mainloop_watchdog.time.monotonic", return_value=100.0) as now,
        ):
            controller.mainloop_watchdog.check(service)

        now.assert_called_once_with()
        dump_traceback.assert_not_called()
        exit_for_restart.assert_not_called()

    def test_watchdog_loop_dumps_logs_and_exits_on_stale_heartbeat(self) -> None:
        service, controller = _controller()
        service._mainloop_heartbeat_monotonic = 80.0
        service._mainloop_watchdog_stale_seconds = 0.5

        with (
            patch.object(controller.mainloop_watchdog, "dump_traceback") as dump_traceback,
            patch.object(controller.mainloop_watchdog, "exit_for_restart") as exit_for_restart,
            patch("venus_evcharger.runtime.async_mainloop_watchdog.time.monotonic", return_value=101.0),
            patch("venus_evcharger.runtime.async_mainloop_watchdog.logging.critical") as critical,
        ):
            controller.mainloop_watchdog.check(service)

        dump_traceback.assert_called_once_with(service)
        critical.assert_called_once_with(
            "Mainloop heartbeat stale for %.1fs; exiting for supervisor restart",
            21.0,
        )
        exit_for_restart.assert_called_once_with()

    def test_watchdog_check_disables_non_positive_threshold_without_reading_time(self) -> None:
        service, controller = _controller()
        with (
            patch.object(controller.mainloop_watchdog, "dump_traceback") as dump_traceback,
            patch.object(controller.mainloop_watchdog, "exit_for_restart") as exit_for_restart,
        ):
            for stale_seconds in (0.0, -1.0):
                service._mainloop_watchdog_stale_seconds = stale_seconds
                with patch("venus_evcharger.runtime.async_mainloop_watchdog.time.monotonic") as now:
                    controller.mainloop_watchdog.check(service)
                now.assert_not_called()

        dump_traceback.assert_not_called()
        exit_for_restart.assert_not_called()

    def test_watchdog_check_treats_exact_stale_threshold_as_healthy(self) -> None:
        service, controller = _controller()
        service._mainloop_heartbeat_monotonic = 90.0
        service._mainloop_watchdog_stale_seconds = 10.0

        with (
            patch.object(controller.mainloop_watchdog, "dump_traceback") as dump_traceback,
            patch.object(controller.mainloop_watchdog, "exit_for_restart") as exit_for_restart,
            patch("venus_evcharger.runtime.async_mainloop_watchdog.time.monotonic", return_value=100.0),
        ):
            controller.mainloop_watchdog.check(service)

        dump_traceback.assert_not_called()
        exit_for_restart.assert_not_called()

    def test_watchdog_check_ignores_missing_clock_state_and_backward_monotonic_sample(self) -> None:
        service, controller = _controller()
        service._mainloop_watchdog_stale_seconds = 10.0
        del service._mainloop_heartbeat_monotonic
        controller.mainloop_watchdog.check(service)
        for invalid_heartbeat in (None, True, "invalid"):
            with self.subTest(invalid_heartbeat=invalid_heartbeat):
                service._mainloop_heartbeat_monotonic = invalid_heartbeat
                with patch("venus_evcharger.runtime.async_mainloop_watchdog.time.monotonic") as now:
                    controller.mainloop_watchdog.check(service)
                now.assert_not_called()

        service._mainloop_heartbeat_monotonic = 101.0
        with (
            patch.object(controller.mainloop_watchdog, "exit_for_restart") as exit_for_restart,
            patch("venus_evcharger.runtime.async_mainloop_watchdog.time.monotonic", return_value=100.0),
        ):
            controller.mainloop_watchdog.check(service)
        exit_for_restart.assert_not_called()

    def test_watchdog_clamps_backward_clock_age_to_zero(self) -> None:
        service, controller = _controller()
        service._mainloop_heartbeat_monotonic = 101.0
        service._mainloop_watchdog_stale_seconds = 0.5
        with (
            patch.object(controller.mainloop_watchdog, "dump_traceback") as dump,
            patch.object(controller.mainloop_watchdog, "exit_for_restart") as restart,
            patch(
                "venus_evcharger.runtime.async_mainloop_watchdog.time.monotonic",
                return_value=100.0,
            ),
        ):
            controller.mainloop_watchdog.check(service)

        dump.assert_not_called()
        restart.assert_not_called()

    def test_traceback_dump_uses_exact_path_content_and_faulthandler_options(self) -> None:
        service, controller = _controller()
        service._mainloop_watchdog_log_path = "/tmp/watchdog/trace.log"
        opened = mock_open()

        with (
            patch("venus_evcharger.runtime.async_mainloop_watchdog.os.makedirs") as makedirs,
            patch("builtins.open", opened),
            patch("venus_evcharger.runtime.async_mainloop_watchdog.time.time", return_value=123.4567),
            patch("venus_evcharger.runtime.async_mainloop_watchdog.faulthandler.dump_traceback") as dump,
        ):
            controller.mainloop_watchdog.dump_traceback(service)

        makedirs.assert_called_once_with("/tmp/watchdog", exist_ok=True)
        opened.assert_called_once_with("/tmp/watchdog/trace.log", "w", encoding="utf-8")
        handle = opened()
        handle.write.assert_called_once_with("mainloop watchdog dump at 123.457\n")
        dump.assert_called_once_with(file=handle, all_threads=True)

    def test_traceback_dump_uses_run_parent_for_basename_and_logs_exact_error(self) -> None:
        service, controller = _controller()
        service._mainloop_watchdog_log_path = "trace.log"
        failure = OSError("read-only")
        with (
            patch("venus_evcharger.runtime.async_mainloop_watchdog.os.makedirs", side_effect=failure) as makedirs,
            patch("venus_evcharger.runtime.async_mainloop_watchdog.logging.debug") as debug,
        ):
            controller.mainloop_watchdog.dump_traceback(service)

        makedirs.assert_called_once_with("/run", exist_ok=True)
        debug.assert_called_once_with("Unable to write mainloop watchdog traceback: %s", failure)

    def test_watchdog_exit_uses_supervisor_restart_code(self) -> None:
        _service, controller = _controller()
        with patch("venus_evcharger.runtime.async_mainloop_watchdog.os._exit") as exit_process:
            controller.mainloop_watchdog.exit_for_restart()
        exit_process.assert_called_once_with(75)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

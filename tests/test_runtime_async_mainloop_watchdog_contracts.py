# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, mock_open, patch

from venus_evcharger.runtime.support import RuntimeSupportController
from tests.venus_evcharger_test_fixtures import make_runtime_support_service


def _controller() -> tuple[SimpleNamespace, RuntimeSupportController]:
    service = make_runtime_support_service()
    controller = RuntimeSupportController(service, lambda _value, _now: 0.0, lambda _reason: 0)
    controller.initialize_runtime_support()
    return service, controller


class RuntimeAsyncMainloopWatchdogContractTests(unittest.TestCase):
    def test_companion_flush_handles_empty_missing_and_exact_publish(self) -> None:
        service, controller = _controller()
        controller.assert_dbus_mainloop_thread = MagicMock()
        self.assertFalse(controller.flush_companion_dbus_publish_queue())

        service._companion_publish_pending = True
        service._companion_publish_now = 55.0
        self.assertFalse(controller.flush_companion_dbus_publish_queue())
        self.assertIs(service._companion_publish_pending, False)
        self.assertIsNone(service._companion_publish_now)
        controller.assert_dbus_mainloop_thread.assert_not_called()

        bridge = SimpleNamespace(publish=MagicMock(return_value=False))
        service._companion_dbus_bridge = bridge
        service._companion_publish_pending = True
        service._companion_publish_now = 56.0
        self.assertFalse(controller.flush_companion_dbus_publish_queue())
        self.assertIs(service._companion_publish_pending, False)
        self.assertIsNone(service._companion_publish_now)
        controller.assert_dbus_mainloop_thread.assert_called_once_with("companion DBus publish flush")
        bridge.publish.assert_called_once_with(56.0)

        bridge.publish.return_value = True
        service._companion_publish_pending = True
        service._companion_publish_now = None
        self.assertTrue(controller.flush_companion_dbus_publish_queue())
        bridge.publish.assert_called_with(None)

    def test_heartbeat_records_exact_time_and_returns_true(self) -> None:
        service, controller = _controller()
        with patch("venus_evcharger.runtime.async_mainloop_watchdog.time.time", return_value=123.5):
            self.assertTrue(controller.mainloop_heartbeat_tick())
        self.assertEqual(service._mainloop_heartbeat_at, 123.5)

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
            target=controller._mainloop_watchdog_loop,
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
        controller._dump_mainloop_watchdog_traceback = MagicMock()
        controller._exit_for_mainloop_watchdog = MagicMock()

        controller._check_mainloop_watchdog = MagicMock()
        with patch("venus_evcharger.runtime.async_mainloop_watchdog.time.time") as now:
            controller._mainloop_watchdog_loop()

        self.assertEqual(service._mainloop_watchdog_stop_event.wait.call_args_list[0].args, (1.5,))
        self.assertEqual(service._mainloop_watchdog_stop_event.wait.call_count, 2)
        now.assert_not_called()
        controller._check_mainloop_watchdog.assert_called_once_with(service)

    def test_watchdog_loop_ignores_recent_heartbeat(self) -> None:
        service, controller = _controller()
        service._mainloop_heartbeat_at = 95.0
        service._mainloop_watchdog_stale_seconds = 10.0
        controller._dump_mainloop_watchdog_traceback = MagicMock()
        controller._exit_for_mainloop_watchdog = MagicMock()

        with patch("venus_evcharger.runtime.async_mainloop_watchdog.time.time", return_value=100.0) as now:
            controller._check_mainloop_watchdog(service)

        now.assert_called_once_with()
        controller._dump_mainloop_watchdog_traceback.assert_not_called()
        controller._exit_for_mainloop_watchdog.assert_not_called()

    def test_watchdog_loop_dumps_logs_and_exits_on_stale_heartbeat(self) -> None:
        service, controller = _controller()
        service._mainloop_heartbeat_at = 80.0
        service._mainloop_watchdog_stale_seconds = 0.5
        controller._dump_mainloop_watchdog_traceback = MagicMock()
        controller._exit_for_mainloop_watchdog = MagicMock()

        with (
            patch("venus_evcharger.runtime.async_mainloop_watchdog.time.time", return_value=101.0),
            patch("venus_evcharger.runtime.async_mainloop_watchdog.logging.critical") as critical,
        ):
            controller._check_mainloop_watchdog(service)

        controller._dump_mainloop_watchdog_traceback.assert_called_once_with(service)
        critical.assert_called_once_with(
            "Mainloop heartbeat stale for %.1fs; exiting for supervisor restart",
            21.0,
        )
        controller._exit_for_mainloop_watchdog.assert_called_once_with()

    def test_watchdog_check_disables_non_positive_threshold_without_reading_time(self) -> None:
        service, controller = _controller()
        controller._dump_mainloop_watchdog_traceback = MagicMock()
        controller._exit_for_mainloop_watchdog = MagicMock()

        for stale_seconds in (0.0, -1.0):
            service._mainloop_watchdog_stale_seconds = stale_seconds
            with patch("venus_evcharger.runtime.async_mainloop_watchdog.time.time") as now:
                controller._check_mainloop_watchdog(service)
            now.assert_not_called()

        controller._dump_mainloop_watchdog_traceback.assert_not_called()
        controller._exit_for_mainloop_watchdog.assert_not_called()

    def test_watchdog_check_treats_exact_stale_threshold_as_healthy(self) -> None:
        service, controller = _controller()
        service._mainloop_heartbeat_at = 90.0
        service._mainloop_watchdog_stale_seconds = 10.0
        controller._dump_mainloop_watchdog_traceback = MagicMock()
        controller._exit_for_mainloop_watchdog = MagicMock()

        with patch("venus_evcharger.runtime.async_mainloop_watchdog.time.time", return_value=100.0):
            controller._check_mainloop_watchdog(service)

        controller._dump_mainloop_watchdog_traceback.assert_not_called()
        controller._exit_for_mainloop_watchdog.assert_not_called()

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
            controller._dump_mainloop_watchdog_traceback(service)

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
            controller._dump_mainloop_watchdog_traceback(service)

        makedirs.assert_called_once_with("/run", exist_ok=True)
        debug.assert_called_once_with("Unable to write mainloop watchdog traceback: %s", failure)

    def test_watchdog_exit_uses_supervisor_restart_code(self) -> None:
        _service, controller = _controller()
        with patch("venus_evcharger.runtime.async_mainloop_watchdog.os._exit") as exit_process:
            controller._exit_for_mainloop_watchdog()
        exit_process.assert_called_once_with(75)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

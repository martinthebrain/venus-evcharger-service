# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import threading
import unittest
from collections import OrderedDict
from unittest.mock import patch

from venus_evcharger.runtime.async_mainloop_state import _RuntimeAsyncMainloopState
from venus_evcharger.runtime.support import RuntimeSupportController
from tests.venus_evcharger_test_fixtures import make_runtime_support_service


def _controller(**service_values: object) -> tuple[object, RuntimeSupportController]:
    service = make_runtime_support_service(**service_values)
    controller = RuntimeSupportController(service, lambda _value, _now: 0.0, lambda _reason: 0)
    return service, controller


class RuntimeAsyncMainloopStateContractTests(unittest.TestCase):
    def test_float_attr_has_exact_numeric_and_default_contract(self) -> None:
        self.assertEqual(_RuntimeAsyncMainloopState._float_attr(12), 12.0)
        self.assertEqual(_RuntimeAsyncMainloopState._float_attr(1.5), 1.5)
        self.assertEqual(_RuntimeAsyncMainloopState._float_attr(True, 7.0), 7.0)
        self.assertEqual(_RuntimeAsyncMainloopState._float_attr("bad"), 0.0)

    def test_initialize_sets_all_scalar_runtime_defaults(self) -> None:
        service, controller = _controller(poll_interval_ms=1000, auto_watchdog_stale_seconds=180.0)
        with patch("venus_evcharger.runtime.async_mainloop_state.time.time", return_value=123.0):
            controller.initialize_async_runtime_state()

        expected = {
            "_dbus_mainloop_thread_id": None,
            "_dbus_async_publish_enabled": False,
            "_dbus_publish_bump_pending": 0,
            "_dbus_publish_oldest_queued_at": None,
            "_dbus_publish_dropped_count": 0,
            "_dbus_publish_max_paths": 256,
            "_dbus_publish_budget_seconds": 0.1,
            "_dbus_publish_flush_interval_ms": 200,
            "_last_publish_flush_duration_seconds": 0.0,
            "_last_dbus_publish_queue_lag_seconds": 0.0,
            "_companion_publish_pending": False,
            "_companion_publish_requested_at": None,
            "_companion_publish_now": None,
            "_update_worker_enabled": False,
            "_runtime_executor_thread": None,
            "_update_worker_thread": None,
            "_update_worker_running": False,
            "_update_worker_pending": False,
            "_update_worker_skipped_count": 0,
            "_last_update_cycle_duration_seconds": 0.0,
            "_last_update_cycle_started_at": None,
            "_last_update_cycle_finished_at": None,
            "_update_worker_budget_seconds": 5.0,
            "_control_command_async_enabled": False,
            "_control_command_thread": None,
            "_control_command_sequence": 0,
            "_control_command_max_paths": 32,
            "_last_write_command_duration_seconds": 0.0,
            "_last_write_command_queue_lag_seconds": 0.0,
            "_write_command_budget_seconds": 2.0,
            "_mainloop_heartbeat_at": 123.0,
            "_mainloop_watchdog_thread": None,
            "_mainloop_watchdog_interval_seconds": 1.0,
            "_mainloop_watchdog_stale_seconds": 180.0,
            "_mainloop_watchdog_log_path": "/run/dbus-venus-evcharger-mainloop-hang.log",
        }
        for name, value in expected.items():
            with self.subTest(name=name):
                self.assertEqual(getattr(service, name), value)

    def test_initialize_sets_empty_typed_queues_and_independent_sync_objects(self) -> None:
        service, controller = _controller()
        controller.initialize_async_runtime_state()

        self.assertEqual(service._dbus_publish_pending, OrderedDict())
        self.assertEqual(service._dbus_publish_field_pending, OrderedDict())
        self.assertEqual(service._control_command_pending, OrderedDict())
        self.assertEqual(service._desired_control_values, {})
        self.assertIsNot(service._dbus_publish_pending, service._dbus_publish_field_pending)

        locks = (
            service._dbus_publish_queue_lock,
            service._companion_publish_lock,
            service._update_worker_lock,
            service._control_command_lock,
        )
        for lock in locks:
            with self.subTest(lock=lock):
                self.assertTrue(lock.acquire(blocking=False))
                lock.release()
        self.assertEqual(len({id(lock) for lock in locks}), 4)

        events = (
            service._runtime_executor_event,
            service._runtime_executor_stop_event,
            service._update_worker_event,
            service._update_worker_stop_event,
            service._control_command_event,
            service._control_command_stop_event,
            service._mainloop_watchdog_stop_event,
        )
        self.assertEqual(len({id(event) for event in events}), 7)
        self.assertTrue(all(not event.is_set() for event in events))

    def test_initialize_derives_update_and_watchdog_budgets_at_boundaries(self) -> None:
        cases = (
            (250, 10.0, 5.0, 30.0),
            (2000, 45.0, 8.0, 45.0),
            ("invalid", "invalid", 5.0, 180.0),
        )
        for poll_interval, watchdog_stale, update_budget, watchdog_budget in cases:
            with self.subTest(poll_interval=poll_interval, watchdog_stale=watchdog_stale):
                service, controller = _controller(
                    poll_interval_ms=poll_interval,
                    auto_watchdog_stale_seconds=watchdog_stale,
                )
                controller.initialize_async_runtime_state()
                self.assertEqual(service._update_worker_budget_seconds, update_budget)
                self.assertEqual(service._mainloop_watchdog_stale_seconds, watchdog_budget)

        service, controller = _controller()
        del service.poll_interval_ms
        del service.auto_watchdog_stale_seconds
        controller.initialize_async_runtime_state()
        self.assertEqual(service._update_worker_budget_seconds, 5.0)
        self.assertEqual(service._mainloop_watchdog_stale_seconds, 180.0)

    def test_mark_and_direct_access_follow_exact_thread_ownership_rules(self) -> None:
        service, controller = _controller()
        self.assertTrue(controller.dbus_publish_direct_allowed())
        controller.initialize_async_runtime_state()
        self.assertTrue(controller.dbus_publish_direct_allowed())

        service._dbus_async_publish_enabled = True
        service._dbus_mainloop_thread_id = None
        self.assertTrue(controller.dbus_publish_direct_allowed())
        del service._dbus_mainloop_thread_id
        self.assertTrue(controller.dbus_publish_direct_allowed())
        service._dbus_mainloop_thread_id = None

        with patch("venus_evcharger.runtime.async_mainloop_state.threading.get_ident", return_value=42):
            controller.mark_mainloop_thread()
            self.assertEqual(service._dbus_mainloop_thread_id, 42)
            self.assertTrue(service._dbus_async_publish_enabled)
            self.assertTrue(controller.dbus_publish_direct_allowed())
        with patch("venus_evcharger.runtime.async_mainloop_state.threading.get_ident", return_value=43):
            self.assertFalse(controller.dbus_publish_direct_allowed())

    def test_thread_assertion_is_silent_when_allowed_and_exact_when_denied(self) -> None:
        service, controller = _controller()
        controller.initialize_async_runtime_state()
        with patch("venus_evcharger.runtime.async_mainloop_state.logging.error") as error_log:
            controller.assert_dbus_mainloop_thread("publish")
        error_log.assert_not_called()

        service._dbus_async_publish_enabled = True
        service._dbus_mainloop_thread_id = 42
        with (
            patch("venus_evcharger.runtime.async_mainloop_state.threading.get_ident", return_value=43),
            patch("venus_evcharger.runtime.async_mainloop_state.logging.error") as error_log,
            self.assertRaisesRegex(RuntimeError, "^publish attempted outside GLib/DBus mainloop thread$"),
        ):
            controller.assert_dbus_mainloop_thread("publish")
        error_log.assert_called_once_with("publish attempted outside GLib/DBus mainloop thread")

        with (
            patch.object(controller, "dbus_publish_direct_allowed", return_value=False),
            self.assertRaisesRegex(RuntimeError, "^dbus access attempted outside GLib/DBus mainloop thread$"),
        ):
            controller.assert_dbus_mainloop_thread()


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

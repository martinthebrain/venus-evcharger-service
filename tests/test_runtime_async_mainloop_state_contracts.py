# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import unittest
from collections import OrderedDict
from types import SimpleNamespace
from unittest.mock import patch

from venus_evcharger.runtime.async_mainloop_state import AsyncRuntimeState
from tests.venus_evcharger_test_fixtures import make_runtime_support_service


def _controller(**service_values: object) -> tuple[SimpleNamespace, AsyncRuntimeState]:
    service = make_runtime_support_service(**service_values)
    controller = AsyncRuntimeState(service)
    return service, controller


class RuntimeAsyncMainloopStateContractTests(unittest.TestCase):
    def test_float_attr_has_exact_numeric_and_default_contract(self) -> None:
        self.assertEqual(AsyncRuntimeState._float_attr(12), 12.0)
        self.assertEqual(AsyncRuntimeState._float_attr(1.5), 1.5)
        self.assertEqual(AsyncRuntimeState._float_attr(True, 7.0), 7.0)
        self.assertEqual(AsyncRuntimeState._float_attr("bad"), 0.0)

    def test_initialize_sets_all_scalar_runtime_defaults(self) -> None:
        service, controller = _controller(
            poll_interval_ms=1000,
            auto_watchdog_stale_seconds=180.0,
            runtime_state_path="/data/runtime/dbus-venus-evcharger-60.json",
            started_at=100.0,
        )
        with (
            patch("venus_evcharger.runtime.async_mainloop_state.time.time", return_value=123.0),
            patch("venus_evcharger.runtime.async_mainloop_state.time.monotonic", return_value=456.0),
        ):
            controller.initialize()

        expected = {
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
            "_mainloop_heartbeat_monotonic": 456.0,
            "_mainloop_watchdog_thread": None,
            "_mainloop_watchdog_interval_seconds": 1.0,
            "_mainloop_watchdog_stale_seconds": 180.0,
            "_mainloop_watchdog_log_path": "/run/dbus-venus-evcharger-mainloop-hang.log",
            "_process_heartbeat_path": "/run/dbus-venus-evcharger-60.heartbeat.json",
            "_process_heartbeat_interval_seconds": 5.0,
            "_process_heartbeat_last_write_monotonic": None,
            "_process_started_at": 100.0,
        }
        for name, value in expected.items():
            with self.subTest(name=name):
                self.assertEqual(getattr(service, name), value)

    def test_initialize_sets_empty_typed_queues_and_independent_sync_objects(self) -> None:
        service, controller = _controller()
        controller.initialize()

        self.assertEqual(service._control_command_pending, OrderedDict())
        self.assertEqual(service._desired_control_values, {})

        locks = (
            service._update_worker_lock,
            service._control_command_lock,
        )
        for lock in locks:
            with self.subTest(lock=lock):
                self.assertTrue(lock.acquire(blocking=False))
                lock.release()
        self.assertEqual(len({id(lock) for lock in locks}), 2)

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
                controller.initialize()
                self.assertEqual(service._update_worker_budget_seconds, update_budget)
                self.assertEqual(service._mainloop_watchdog_stale_seconds, watchdog_budget)

        service, controller = _controller()
        del service.poll_interval_ms
        del service.auto_watchdog_stale_seconds
        controller.initialize()
        self.assertEqual(service._update_worker_budget_seconds, 5.0)
        self.assertEqual(service._mainloop_watchdog_stale_seconds, 180.0)

    def test_initialize_keeps_heartbeat_on_run_and_normalizes_missing_start_time(self) -> None:
        cases: tuple[tuple[dict[str, object], str], ...] = (
            ({}, "/run/dbus-venus-evcharger-60.heartbeat.json"),
            ({"runtime_state_path": "/data/custom-state"}, "/run/custom-state.heartbeat.json"),
            ({"runtime_state_path": "/"}, "/run/dbus-venus-evcharger-60.heartbeat.json"),
            ({"runtime_state_path": "/data/.."}, "/run/dbus-venus-evcharger-60.heartbeat.json"),
        )
        for overrides, expected_path in cases:
            with self.subTest(overrides=overrides):
                service, controller = _controller(started_at=0.0, **overrides)
                with (
                    patch("venus_evcharger.runtime.async_mainloop_state.time.time", return_value=123.0),
                    patch("venus_evcharger.runtime.async_mainloop_state.time.monotonic", return_value=456.0),
                ):
                    controller.initialize()
                self.assertEqual(service._process_heartbeat_path, expected_path)
                self.assertEqual(service._process_started_at, 123.0)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import unittest
from collections import OrderedDict
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

from venus_evcharger.runtime.support import RuntimeSupportController
from tests.venus_evcharger_test_fixtures import make_runtime_support_service


def _controller() -> tuple[SimpleNamespace, RuntimeSupportController]:
    service = make_runtime_support_service()
    controller = RuntimeSupportController(service, lambda _value, _now: 0.0, lambda _reason: 0)
    controller.initialize_runtime_support()
    return service, controller


class RuntimeAsyncMainloopPublishContractTests(unittest.TestCase):
    def test_enqueue_paths_coalesces_trims_and_records_exact_oldest_time(self) -> None:
        service, controller = _controller()
        service._dbus_publish_max_paths = 2
        service._dbus_publish_dropped_count = 7

        with patch("venus_evcharger.runtime.async_mainloop_publish.time.time", side_effect=(90.0, 100.0)):
            self.assertTrue(controller.enqueue_dbus_publish_values([("/A", 1), ("/B", 2)], 12))
            self.assertTrue(controller.enqueue_dbus_publish_values([("/A", 3), ("/C", 4)], 13))

        self.assertEqual(
            service._dbus_publish_pending,
            OrderedDict((("/A", (3, 13.0, 100.0)), ("/C", (4, 13.0, 100.0)))),
        )
        self.assertEqual(service._dbus_publish_dropped_count, 8)
        self.assertEqual(service._dbus_publish_oldest_queued_at, 100.0)

    def test_enqueue_fields_uses_its_own_queue_contract_and_timestamp(self) -> None:
        service, controller = _controller()
        with patch("venus_evcharger.runtime.async_mainloop_publish.time.time", return_value=123.0):
            self.assertTrue(controller.enqueue_dbus_publish_fields([("ac_power_w", 800)], 14))

        self.assertEqual(
            service._dbus_publish_field_pending,
            OrderedDict((("ac_power_w", (800, 14.0, 123.0)),)),
        )
        self.assertEqual(service._dbus_publish_oldest_queued_at, 123.0)

        service._dbus_publish_field_pending = {}
        with self.assertRaisesRegex(TypeError, "_dbus_publish_field_pending must be OrderedDict"):
            controller.enqueue_dbus_publish_fields([("ac_power_w", 900)], 15)

    def test_update_index_and_companion_enqueue_preserve_exact_request_state(self) -> None:
        service, controller = _controller()
        service._dbus_publish_oldest_queued_at = 50.0

        with patch("venus_evcharger.runtime.async_mainloop_publish.time.time", side_effect=(100.0, 101.0)):
            controller.enqueue_dbus_update_index_bump(1.0)
            controller.enqueue_dbus_update_index_bump(2.0)
            self.assertTrue(controller.enqueue_companion_dbus_publish(now=77.0))

        self.assertEqual(service._dbus_publish_bump_pending, 2)
        self.assertEqual(service._dbus_publish_oldest_queued_at, 50.0)
        self.assertTrue(service._companion_publish_pending)
        self.assertEqual(service._companion_publish_requested_at, 100.0)
        self.assertEqual(service._companion_publish_now, 77.0)

        service._dbus_publish_oldest_queued_at = None
        with patch("venus_evcharger.runtime.async_mainloop_publish.time.time", return_value=200.0):
            controller.enqueue_dbus_update_index_bump(3.0)
        self.assertEqual(service._dbus_publish_bump_pending, 3)
        self.assertEqual(service._dbus_publish_oldest_queued_at, 200.0)

    def test_update_index_direct_wraps_only_after_255_and_records_state(self) -> None:
        service, controller = _controller()
        service._dbusservice = {"/UpdateIndex": 253}
        service._dbus_publish_state = {}

        controller._bump_update_index_direct(service, 10.0)
        self.assertEqual(service._dbusservice["/UpdateIndex"], 254)
        self.assertEqual(service._dbus_publish_state["/UpdateIndex"], {"value": 254, "updated_at": 10.0})
        controller._bump_update_index_direct(service, 11.0)
        self.assertEqual(service._dbusservice["/UpdateIndex"], 255)
        self.assertEqual(service._dbus_publish_state["/UpdateIndex"], {"value": 255, "updated_at": 11.0})
        controller._bump_update_index_direct(service, 12.0)
        self.assertEqual(service._dbusservice["/UpdateIndex"], 0)
        self.assertEqual(service._dbus_publish_state["/UpdateIndex"], {"value": 0, "updated_at": 12.0})

    def test_drain_returns_every_value_and_resets_all_queue_metadata(self) -> None:
        service, controller = _controller()
        service._dbus_publish_pending["/A"] = (1, 10.0, 8.0)
        service._dbus_publish_field_pending["ac_power_w"] = (2, 11.0, 9.0)
        service._dbus_publish_bump_pending = 3
        service._dbus_publish_oldest_queued_at = 8.0

        drained = controller._drain_dbus_publish_queue(service)

        self.assertEqual(
            drained,
            ([("/A", (1, 10.0, 8.0))], [("ac_power_w", (2, 11.0, 9.0))], 3, 8.0),
        )
        self.assertEqual(service._dbus_publish_pending, OrderedDict())
        self.assertEqual(service._dbus_publish_field_pending, OrderedDict())
        self.assertEqual(service._dbus_publish_bump_pending, 0)
        self.assertIsNone(service._dbus_publish_oldest_queued_at)

    def test_drain_reports_each_queue_contract_name(self) -> None:
        service, controller = _controller()
        service._dbus_publish_pending = {}
        with self.assertRaisesRegex(TypeError, "_dbus_publish_pending must be OrderedDict"):
            controller._drain_dbus_publish_queue(service)

        service, controller = _controller()
        service._dbus_publish_field_pending = {}
        with self.assertRaisesRegex(TypeError, "_dbus_publish_field_pending must be OrderedDict"):
            controller._drain_dbus_publish_queue(service)

    def test_queue_lag_is_exact_clamped_and_unchanged_without_timestamp(self) -> None:
        service, controller = _controller()
        service._last_dbus_publish_queue_lag_seconds = 7.0
        controller._remember_dbus_publish_queue_lag(service, 100.0, None)
        self.assertEqual(service._last_dbus_publish_queue_lag_seconds, 7.0)
        controller._remember_dbus_publish_queue_lag(service, 100.0, 98.5)
        self.assertEqual(service._last_dbus_publish_queue_lag_seconds, 1.5)
        controller._remember_dbus_publish_queue_lag(service, 100.0, 101.0)
        self.assertEqual(service._last_dbus_publish_queue_lag_seconds, 0.0)

    def test_field_conversion_keeps_only_known_consistent_paths(self) -> None:
        _service, controller = _controller()
        fields = [
            ("unknown", (1, 12.0, 10.0)),
            ("ac_power_w", (1200.0, 10.0, 8.0)),
            ("session_time_s", (30, 11.0, 9.0)),
        ]
        self.assertEqual(
            controller._path_values_from_fields(fields),
            [
                ("/Ac/Power", (1200.0, 10.0, 8.0)),
                ("/Session/Time", (30, 11.0, 9.0)),
            ],
        )
        self.assertEqual(controller._field_paths(fields), ["/Ac/Power", "/Session/Time"])

    def test_failure_reporting_marks_dbus_and_logs_exact_paths(self) -> None:
        _service, controller = _controller()
        mark_failure = MagicMock()
        service = SimpleNamespace(_mark_failure=mark_failure)

        with patch("venus_evcharger.runtime.async_mainloop_publish.logging.warning") as warning:
            controller._report_dbus_publish_failures(service, [])
            warning.assert_not_called()
            controller._report_dbus_publish_failures(service, ["/A", "/B"])

        mark_failure.assert_called_once_with("dbus")
        warning.assert_called_once_with("DBus publish queue failed for paths %s", "/A,/B")

    def test_update_index_flushes_exact_count_and_stops_after_failure(self) -> None:
        service, controller = _controller()
        controller._bump_update_index_best_effort = MagicMock(side_effect=(True, False, True))

        controller._flush_update_index_bumps(service, 44.0, 0)
        controller._bump_update_index_best_effort.assert_not_called()
        controller._flush_update_index_bumps(service, 44.0, 3)

        self.assertEqual(
            controller._bump_update_index_best_effort.call_args_list,
            [call(service, 44.0), call(service, 44.0)],
        )

    def test_best_effort_bump_reports_exact_success_and_failure(self) -> None:
        service, controller = _controller()
        controller._bump_update_index_direct = MagicMock()
        self.assertTrue(controller._bump_update_index_best_effort(service, 55.0))
        controller._bump_update_index_direct.assert_called_once_with(service, 55.0)

        controller._bump_update_index_direct.side_effect = RuntimeError("down")
        with patch("venus_evcharger.runtime.async_mainloop_publish.logging.warning") as warning:
            self.assertFalse(controller._bump_update_index_best_effort(service, 56.0))
        warning.assert_called_once_with("DBus publish queue failed to bump /UpdateIndex")

    def test_flush_duration_uses_strict_budget_and_invalid_default(self) -> None:
        service, controller = _controller()
        service._dbus_publish_budget_seconds = 0.25
        with (
            patch("venus_evcharger.runtime.async_mainloop_publish.time.monotonic", return_value=10.25),
            patch("venus_evcharger.runtime.async_mainloop_publish.logging.warning") as warning,
        ):
            controller._record_publish_flush_duration(service, 10.0)
        self.assertEqual(service._last_publish_flush_duration_seconds, 0.25)
        warning.assert_not_called()

        service._dbus_publish_budget_seconds = "invalid"
        with (
            patch("venus_evcharger.runtime.async_mainloop_publish.time.monotonic", return_value=20.05),
            patch("venus_evcharger.runtime.async_mainloop_publish.logging.warning") as warning,
        ):
            controller._record_publish_flush_duration(service, 20.0)
        warning.assert_not_called()

        with (
            patch("venus_evcharger.runtime.async_mainloop_publish.time.monotonic", return_value=30.2),
            patch("venus_evcharger.runtime.async_mainloop_publish.logging.warning") as warning,
        ):
            controller._record_publish_flush_duration(service, 30.0)
        self.assertAlmostEqual(service._last_publish_flush_duration_seconds, 0.2)
        warning.assert_called_once_with("DBus publish flush exceeded budget: %.3fs", service._last_publish_flush_duration_seconds)

    def test_flush_orchestrates_all_steps_with_exact_timing_values(self) -> None:
        service, controller = _controller()
        service._dbusservice = {}
        values = [("/A", (1, 10.0, 8.0))]
        fields = [("ac_power_w", (2, 11.0, 9.0))]
        controller.assert_dbus_mainloop_thread = MagicMock()
        controller._drain_dbus_publish_queue = MagicMock(return_value=(values, fields, 2, 8.0))
        controller._remember_dbus_publish_queue_lag = MagicMock()
        controller._apply_dbus_publish_values = MagicMock(return_value=["/A"])
        controller._apply_dbus_publish_fields = MagicMock(return_value=["/Ac/Power"])
        controller._report_dbus_publish_failures = MagicMock()
        controller._flush_update_index_bumps = MagicMock()
        controller._record_publish_flush_duration = MagicMock()
        controller.flush_companion_dbus_publish_queue = MagicMock()

        with (
            patch("venus_evcharger.runtime.async_mainloop_publish.time.monotonic", return_value=50.0),
            patch("venus_evcharger.runtime.async_mainloop_publish.time.time", return_value=60.0),
        ):
            self.assertTrue(controller.flush_dbus_publish_queue())

        controller.assert_dbus_mainloop_thread.assert_called_once_with("main DBus publish flush")
        controller._remember_dbus_publish_queue_lag.assert_called_once_with(service, 60.0, 8.0)
        controller._apply_dbus_publish_values.assert_called_once_with(service, values)
        controller._apply_dbus_publish_fields.assert_called_once_with(service, fields)
        controller._report_dbus_publish_failures.assert_called_once_with(service, ["/A", "/Ac/Power"])
        controller._flush_update_index_bumps.assert_called_once_with(service, 60.0, 2)
        controller._record_publish_flush_duration.assert_called_once_with(service, 50.0)
        controller.flush_companion_dbus_publish_queue.assert_called_once_with()


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import unittest
from collections import OrderedDict
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

from venus_evcharger.control import ControlCommand
from venus_evcharger.runtime.support import RuntimeSupportController
from tests.venus_evcharger_test_fixtures import make_runtime_support_service


def _controller() -> tuple[SimpleNamespace, RuntimeSupportController]:
    service = make_runtime_support_service()
    controller = RuntimeSupportController(service, lambda _value, _now: 0.0, lambda _reason: 0)
    controller.initialize_runtime_support()
    return service, controller


def _command(path: str, value: object = 1) -> ControlCommand:
    return ControlCommand(name=f"set-{path}", path=path, value=value)


class RuntimeAsyncMainloopControlContractTests(unittest.TestCase):
    def test_sync_fallback_returns_exact_acceptance(self) -> None:
        service, controller = _controller()
        command = _command("/Mode")
        service._handle_control_command = MagicMock(
            side_effect=(SimpleNamespace(accepted=False), SimpleNamespace(accepted=True))
        )

        self.assertFalse(controller.enqueue_control_command(command))
        self.assertTrue(controller.enqueue_control_command(command))
        self.assertEqual(service._handle_control_command.call_args_list, [call(command), call(command)])

    def test_async_enqueue_increments_coalesces_and_signals(self) -> None:
        service, controller = _controller()
        service._control_command_async_enabled = True
        service._control_command_sequence = 10
        command = _command("/Mode", 1)
        replacement = _command("/Mode", 2)

        with patch("venus_evcharger.runtime.async_mainloop_control.time.time", side_effect=(100.0, 101.0)):
            self.assertTrue(controller.enqueue_control_command(command))
            self.assertTrue(controller.enqueue_control_command(replacement))

        self.assertEqual(service._control_command_sequence, 12)
        self.assertEqual(
            list(service._control_command_pending.items()),
            [("/Mode", (12, 101.0, replacement))],
        )
        self.assertEqual(service._desired_control_values, {"/Mode": 2})
        self.assertTrue(service._control_command_event.is_set())
        self.assertTrue(service._runtime_executor_event.is_set())

    def test_async_enqueue_default_limit_keeps_latest_32_paths(self) -> None:
        service, controller = _controller()
        service._control_command_async_enabled = True
        del service._control_command_max_paths

        with patch("venus_evcharger.runtime.async_mainloop_control.time.time", return_value=100.0):
            for index in range(33):
                self.assertTrue(controller.enqueue_control_command(_command(f"/P{index}", index)))

        self.assertEqual(len(service._control_command_pending), 32)
        self.assertNotIn("/P0", service._control_command_pending)
        self.assertNotIn("/P0", service._desired_control_values)
        self.assertIn("/P32", service._control_command_pending)

    def test_async_enqueue_tolerates_missing_desired_value_for_dropped_path(self) -> None:
        service, controller = _controller()
        service._control_command_async_enabled = True
        service._control_command_max_paths = 1
        old = _command("/Old")
        service._control_command_pending[old.path] = (1, 90.0, old)
        service._control_command_sequence = 1
        service._desired_control_values.clear()

        self.assertTrue(controller.enqueue_control_command(_command("/New")))
        self.assertEqual(list(service._control_command_pending), ["/New"])

    def test_drain_uses_queue_order_and_records_exact_lag_and_duration(self) -> None:
        service, controller = _controller()
        later = _command("/Later")
        earlier = _command("/Earlier")
        service._control_command_pending = OrderedDict(
            ((earlier.path, (1, 103.0, earlier)), (later.path, (2, 104.0, later)))
        )
        service._handle_control_command = MagicMock()
        service._write_command_budget_seconds = 10.0

        with (
            patch("venus_evcharger.runtime.async_mainloop_control.time.time", side_effect=(105.0, 106.0)),
            patch(
                "venus_evcharger.runtime.async_mainloop_control.time.monotonic",
                side_effect=(10.0, 10.25, 20.0, 20.5),
            ),
            patch("venus_evcharger.runtime.async_mainloop_control.logging.warning") as warning,
        ):
            self.assertTrue(controller._drain_control_commands_once())

        self.assertEqual(service._handle_control_command.call_args_list, [call(earlier), call(later)])
        self.assertEqual(service._control_command_pending, OrderedDict())
        self.assertEqual(service._last_write_command_queue_lag_seconds, 2.0)
        self.assertEqual(service._last_write_command_duration_seconds, 0.5)
        warning.assert_not_called()
        self.assertFalse(controller._drain_control_commands_once())

    def test_drain_clamps_negative_lag_and_logs_exact_failure_and_budget(self) -> None:
        service, controller = _controller()
        command = _command("/Mode")
        service._control_command_pending[command.path] = (1, 110.0, command)
        service._handle_control_command = MagicMock(side_effect=RuntimeError("failed"))
        service._write_command_budget_seconds = 0.5

        with (
            patch("venus_evcharger.runtime.async_mainloop_control.time.time", return_value=100.0),
            patch(
                "venus_evcharger.runtime.async_mainloop_control.time.monotonic",
                side_effect=(10.0, 11.0),
            ),
            patch("venus_evcharger.runtime.async_mainloop_control.logging.exception") as exception,
            patch("venus_evcharger.runtime.async_mainloop_control.logging.warning") as warning,
        ):
            self.assertTrue(controller._drain_control_commands_once())

        self.assertEqual(service._last_write_command_queue_lag_seconds, 0.0)
        self.assertEqual(service._last_write_command_duration_seconds, 1.0)
        exception.assert_called_once_with("Async control command failed path=%s", "/Mode")
        warning.assert_called_once_with(
            "Control command path=%s exceeded budget: %.3fs",
            "/Mode",
            1.0,
        )

    def test_drain_budget_is_strict_and_defaults_to_two_seconds(self) -> None:
        service, controller = _controller()
        service._handle_control_command = MagicMock()
        first = _command("/Equal")
        service._control_command_pending[first.path] = (1, 0.0, first)
        service._write_command_budget_seconds = 2.0

        with (
            patch("venus_evcharger.runtime.async_mainloop_control.time.time", return_value=1.0),
            patch(
                "venus_evcharger.runtime.async_mainloop_control.time.monotonic",
                side_effect=(10.0, 12.0),
            ),
            patch("venus_evcharger.runtime.async_mainloop_control.logging.warning") as warning,
        ):
            self.assertTrue(controller._drain_control_commands_once())
        warning.assert_not_called()

        second = _command("/Default")
        service._control_command_pending[second.path] = (2, 0.0, second)
        del service._write_command_budget_seconds
        with (
            patch("venus_evcharger.runtime.async_mainloop_control.time.time", return_value=1.0),
            patch(
                "venus_evcharger.runtime.async_mainloop_control.time.monotonic",
                side_effect=(20.0, 22.5),
            ),
            patch("venus_evcharger.runtime.async_mainloop_control.logging.warning") as warning,
        ):
            self.assertTrue(controller._drain_control_commands_once())
        warning.assert_called_once_with(
            "Control command path=%s exceeded budget: %.3fs",
            "/Default",
            2.5,
        )

        third = _command("/Invalid")
        service._control_command_pending[third.path] = (3, 0.0, third)
        service._write_command_budget_seconds = "invalid"
        with (
            patch("venus_evcharger.runtime.async_mainloop_control.time.time", return_value=1.0),
            patch(
                "venus_evcharger.runtime.async_mainloop_control.time.monotonic",
                side_effect=(30.0, 31.0),
            ),
            patch("venus_evcharger.runtime.async_mainloop_control.logging.warning") as warning,
        ):
            self.assertTrue(controller._drain_control_commands_once())
        warning.assert_not_called()

    def test_drain_queue_contract_reports_the_exact_field_name(self) -> None:
        service, controller = _controller()
        service._control_command_pending = {}

        with self.assertRaisesRegex(TypeError, "_control_command_pending must be OrderedDict"):
            controller._drain_control_commands_once()


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

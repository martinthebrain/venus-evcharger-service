# SPDX-License-Identifier: GPL-3.0-or-later
"""Contracts for the serialized runtime control-command queue."""

from __future__ import annotations

import unittest
from collections import OrderedDict
from collections.abc import Callable

from venus_evcharger.control import ControlCommand
from venus_evcharger.runtime.async_control_types import (
    is_control_command_queue,
    require_control_command_queue,
)


class RuntimeAsyncControlTypeContractTests(unittest.TestCase):
    def assert_type_error(self, expected: str, function: Callable[..., object], *args: object) -> None:
        with self.assertRaises(TypeError) as raised:
            function(*args)
        self.assertEqual(str(raised.exception), expected)

    @staticmethod
    def command() -> ControlCommand:
        return ControlCommand(name="set_mode", target="mode", value=1)

    def test_control_command_queue_accepts_valid_ordered_dict(self) -> None:
        queue = OrderedDict([("cmd-1", (1, 2.0, self.command()))])

        self.assertTrue(is_control_command_queue(queue))
        self.assertIs(require_control_command_queue(queue, "control"), queue)

    def test_control_command_queue_rejects_non_ordered_dict(self) -> None:
        value = {"cmd-1": (1, 2.0, self.command())}

        self.assertFalse(is_control_command_queue(value))
        self.assert_type_error(
            "control must be OrderedDict, got dict",
            require_control_command_queue,
            value,
            "control",
        )

    def test_control_command_queue_rejects_invalid_tuple_fields(self) -> None:
        command = self.command()
        invalid_cases = (
            (OrderedDict([(1, (1, 2.0, command))]), "control command queue keys must be str, got int"),
            (OrderedDict([("cmd", [1, 2.0, command])]), "control command queue values must be control command tuples"),
            (OrderedDict([("cmd", (1, 2.0))]), "control command queue values must be control command tuples"),
            (OrderedDict([("cmd", (True, 2.0, command))]), "control command queue sequence must be int"),
            (OrderedDict([("cmd", ("1", 2.0, command))]), "control command queue sequence must be int"),
            (OrderedDict([("cmd", (1, False, command))]), "control command queue queued_at must be float"),
            (OrderedDict([("cmd", (1, "bad", command))]), "control command queue queued_at must be float"),
            (OrderedDict([("cmd", (1, 2.0, object()))]), "control command queue command must be ControlCommand"),
        )

        for queue, message in invalid_cases:
            with self.subTest(message=message):
                self.assert_type_error(message, is_control_command_queue, queue)

        self.assert_type_error(
            "named sequence must be int",
            require_control_command_queue,
            OrderedDict([("cmd", (True, 2.0, command))]),
            "named",
        )
        self.assert_type_error(
            "named command must be ControlCommand",
            require_control_command_queue,
            OrderedDict([("cmd", (1, 2.0, object()))]),
            "named",
        )


if __name__ == "__main__":
    unittest.main()

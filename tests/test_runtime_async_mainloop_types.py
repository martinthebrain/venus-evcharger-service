# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable
import unittest

from venus_evcharger.control import ControlCommand
from venus_evcharger.runtime.async_mainloop_types import (
    is_control_command_queue,
    is_publish_queue,
    require_control_command_queue,
    require_publish_queue,
)
from venus_evcharger.service import return_contracts as service_return_contracts


class RuntimeAsyncMainloopTypeContractTests(unittest.TestCase):
    def assert_type_error(self, expected: str, function: Callable[..., object], *args: object) -> None:
        with self.assertRaises(TypeError) as raised:
            function(*args)
        self.assertEqual(str(raised.exception), expected)

    def test_publish_queue_accepts_valid_ordered_dict(self) -> None:
        queue = OrderedDict([("/Path", (12.5, 1.0, 2.0))])

        self.assertTrue(is_publish_queue(queue))
        self.assertIs(require_publish_queue(queue, "publish"), queue)

    def test_publish_queue_rejects_non_ordered_dict(self) -> None:
        value = {"/Path": (12.5, 1.0, 2.0)}

        self.assertFalse(is_publish_queue(value))
        self.assert_type_error("publish must be OrderedDict, got dict", require_publish_queue, value, "publish")

    def test_publish_queue_rejects_invalid_key_value_and_numeric_fields(self) -> None:
        invalid_cases = [
            (OrderedDict([(1, (12.5, 1.0, 2.0))]), "keys must be str"),
            (OrderedDict([("/Path", [12.5, 1.0, 2.0])]), "values must be publish tuples"),
            (OrderedDict([("/Path", (12.5, 1.0))]), "values must be publish tuples"),
            (OrderedDict([("/Path", (12.5, True, 2.0))]), "current must be float"),
            (OrderedDict([("/Path", (12.5, "bad", 2.0))]), "current must be float"),
            (OrderedDict([("/Path", (12.5, 1.0, False))]), "queued_at must be float"),
        ]

        for queue, message in invalid_cases:
            with self.subTest(message=message):
                with self.assertRaisesRegex(TypeError, message):
                    is_publish_queue(queue)

        self.assert_type_error(
            "publish queue keys must be str, got int",
            is_publish_queue,
            OrderedDict([(1, (12.5, 1.0, 2.0))]),
        )
        self.assert_type_error(
            "named values must be publish tuples",
            require_publish_queue,
            OrderedDict([("/Path", (12.5, 1.0))]),
            "named",
        )

    def test_control_command_queue_accepts_valid_ordered_dict(self) -> None:
        command = ControlCommand(name="set_mode", path="/Mode", value=1)
        queue = OrderedDict([("cmd-1", (1, 2.0, command))])

        self.assertTrue(is_control_command_queue(queue))
        self.assertIs(require_control_command_queue(queue, "control"), queue)

    def test_control_command_queue_rejects_non_ordered_dict(self) -> None:
        command = ControlCommand(name="set_mode", path="/Mode", value=1)
        value = {"cmd-1": (1, 2.0, command)}

        self.assertFalse(is_control_command_queue(value))
        self.assert_type_error(
            "control must be OrderedDict, got dict",
            require_control_command_queue,
            value,
            "control",
        )

    def test_control_command_queue_rejects_invalid_tuple_fields(self) -> None:
        command = ControlCommand(name="set_mode", path="/Mode", value=1)
        invalid_cases = [
            (OrderedDict([(1, (1, 2.0, command))]), "keys must be str"),
            (OrderedDict([("cmd", [1, 2.0, command])]), "values must be control command tuples"),
            (OrderedDict([("cmd", (1, 2.0))]), "values must be control command tuples"),
            (OrderedDict([("cmd", (True, 2.0, command))]), "sequence must be int"),
            (OrderedDict([("cmd", ("1", 2.0, command))]), "sequence must be int"),
            (OrderedDict([("cmd", (1, False, command))]), "queued_at must be float"),
            (OrderedDict([("cmd", (1, "bad", command))]), "queued_at must be float"),
            (OrderedDict([("cmd", (1, 2.0, object()))]), "command must be ControlCommand"),
        ]

        for queue, message in invalid_cases:
            with self.subTest(message=message):
                with self.assertRaisesRegex(TypeError, message):
                    is_control_command_queue(queue)

        self.assert_type_error(
            "control command queue keys must be str, got int",
            is_control_command_queue,
            OrderedDict([(1, (1, 2.0, command))]),
        )
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

    def test_service_return_contract_compatibility_path_exports_core_contracts(self) -> None:
        self.assertIs(service_return_contracts.require_bool(True, "flag"), True)
        self.assertIsNone(service_return_contracts.require_none(None, "noop"))
        self.assertEqual(service_return_contracts.require_tuple2(("a", "b"), "pair"), ("a", "b"))

        with self.assertRaisesRegex(TypeError, "noop must return None, got int"):
            service_return_contracts.require_none(1, "noop")


if __name__ == "__main__":
    unittest.main()

# SPDX-License-Identifier: GPL-3.0-or-later
"""Exact runtime return-value contracts."""

from __future__ import annotations

import unittest
from collections.abc import Callable

from venus_evcharger.core.return_contracts import (
    require_bool,
    require_dict,
    require_float,
    require_float_or_none,
    require_instance,
    require_int,
    require_none,
    require_str,
    require_str_list,
    require_str_or_none,
    require_tuple2,
    require_tuple3,
    require_tuple4,
    require_tuple5,
)


class SampleType:
    pass


class TestCoreReturnContracts(unittest.TestCase):
    def assert_type_error(self, expected: str, callback: Callable[[], object]) -> None:
        with self.assertRaises(TypeError) as raised:
            callback()
        self.assertEqual(str(raised.exception), expected)

    def test_bool_and_none_contracts(self) -> None:
        self.assertIs(require_bool(True, "flag"), True)
        self.assertIs(require_bool(False, "flag"), False)
        self.assert_type_error("flag must return bool, got int", lambda: require_bool(1, "flag"))
        self.assertIsNone(require_none(None, "action"))
        self.assert_type_error("action must return None, got int", lambda: require_none(0, "action"))

    def test_integer_contract_rejects_bool(self) -> None:
        self.assertEqual(require_int(7, "count"), 7)
        self.assert_type_error("count must return int, got str", lambda: require_int("7", "count"))
        self.assert_type_error("count must return int, got bool", lambda: require_int(True, "count"))

    def test_float_contracts(self) -> None:
        self.assertEqual(require_float(7, "power"), 7.0)
        self.assertEqual(require_float(7.5, "power"), 7.5)
        self.assert_type_error("power must return float, got bool", lambda: require_float(True, "power"))
        self.assert_type_error("power must return float, got str", lambda: require_float("7.5", "power"))
        self.assertIsNone(require_float_or_none(None, "power"))
        self.assertEqual(require_float_or_none(8, "power"), 8.0)
        self.assert_type_error("power must return float, got str", lambda: require_float_or_none("8", "power"))

    def test_string_contracts(self) -> None:
        self.assertEqual(require_str("value", "name"), "value")
        self.assert_type_error("name must return str, got int", lambda: require_str(1, "name"))
        self.assertIsNone(require_str_or_none(None, "name"))
        self.assertEqual(require_str_or_none("value", "name"), "value")
        self.assert_type_error("name must return str, got int", lambda: require_str_or_none(1, "name"))

    def test_string_list_contract_preserves_identity(self) -> None:
        values = ["a", "b"]
        self.assertIs(require_str_list(values, "names"), values)
        self.assertEqual(require_str_list([], "names"), [])
        self.assert_type_error("names must return list, got tuple", lambda: require_str_list(("a",), "names"))
        self.assert_type_error("names must return list[str]", lambda: require_str_list(["a", 2], "names"))

    def test_dict_and_instance_contracts_preserve_identity(self) -> None:
        mapping = {"key": "value"}
        self.assertIs(require_dict(mapping, "payload"), mapping)
        self.assert_type_error("payload must return dict, got list", lambda: require_dict([], "payload"))
        instance = SampleType()
        self.assertIs(require_instance(instance, "sample", SampleType), instance)
        self.assert_type_error(
            "sample must return SampleType, got object",
            lambda: require_instance(object(), "sample", SampleType),
        )

    def test_tuple_contracts_preserve_every_position(self) -> None:
        self.assertEqual(require_tuple2(("a", "b"), "pair"), ("a", "b"))
        self.assertEqual(require_tuple3(("a", "b", "c"), "triple"), ("a", "b", "c"))
        self.assertEqual(require_tuple4((1, 2, 3, 4), "quad"), (1, 2, 3, 4))
        self.assertEqual(require_tuple5((1, 2, 3, 4, 5), "five"), (1, 2, 3, 4, 5))

    def test_tuple_contracts_reject_wrong_container_and_each_length(self) -> None:
        self.assert_type_error("pair must return tuple, got list", lambda: require_tuple2([1, 2], "pair"))
        for callback, name, expected_length, actual in (
            (require_tuple2, "pair", 2, (1,)),
            (require_tuple3, "triple", 3, (1, 2)),
            (require_tuple4, "quad", 4, (1, 2, 3)),
            (require_tuple5, "five", 5, (1, 2, 3, 4)),
        ):
            self.assert_type_error(
                f"{name} must return tuple length {expected_length}, got {len(actual)}",
                lambda callback=callback, actual=actual, name=name: callback(actual, name),
            )


if __name__ == "__main__":
    unittest.main()

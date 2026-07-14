# SPDX-License-Identifier: GPL-3.0-or-later
"""Exact contracts for helpers shared by external energy connectors."""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from venus_evcharger.energy.connectors_common import (
    _cache_map,
    _csv_filter,
    _normalized_connector_type,
    _normalized_optional_bool_value,
    _optional_bool_path,
    _optional_confidence_path,
    _optional_float_path,
    _optional_path,
    _optional_text_path,
    _runtime_owner,
    _sum_optional,
    _typed_cache_map,
)


class EnergyConnectorsCommonContractTests(unittest.TestCase):
    def test_runtime_owner_and_cache_normalization_are_exact(self) -> None:
        runtime = SimpleNamespace()
        owner = SimpleNamespace(service=runtime)
        self.assertIs(_runtime_owner(owner), runtime)
        self.assertIs(_runtime_owner(runtime), runtime)

        runtime.cache = {1: "one", "two": 2}
        normalized = _cache_map(runtime, "cache")
        self.assertEqual(normalized, {"1": "one", "two": 2})
        self.assertIs(runtime.cache, normalized)

        runtime.missing = "not-a-map"
        missing = _cache_map(runtime, "missing")
        self.assertEqual(missing, {})
        self.assertIs(runtime.missing, missing)

        absent = _cache_map(runtime, "absent")
        self.assertEqual(absent, {})
        self.assertIs(runtime.absent, absent)

        runtime.typed = {"valid": 3, "invalid": "3"}
        typed = _typed_cache_map(runtime, "typed", int)
        self.assertEqual(typed, {"valid": 3})
        self.assertIs(runtime.typed, typed)

    def test_connector_and_optional_path_normalization_is_exact(self) -> None:
        self.assertEqual(_normalized_connector_type(" TEMPLATE_HTTP_ENERGY "), "template_http")
        self.assertEqual(_normalized_connector_type(" MODBUS "), "modbus")
        self.assertEqual(_normalized_connector_type("  "), "dbus")

        self.assertIsNone(_optional_path("  "))
        self.assertIsNone(_optional_path(None))
        self.assertEqual(_optional_path(" value.path "), "value.path")

        payload: dict[str, object] = {
            "number": "12.5",
            "invalid": "not-a-number",
            "text": " mode ",
            "empty": "  ",
            "null": None,
        }
        self.assertIsNone(_optional_float_path(payload, None))
        self.assertEqual(_optional_float_path(payload, "number"), 12.5)
        self.assertIsNone(_optional_float_path(payload, "invalid"))
        self.assertIsNone(_optional_text_path(payload, None))
        self.assertEqual(_optional_text_path(payload, "text"), "mode")
        self.assertIsNone(_optional_text_path(payload, "empty"))
        self.assertIsNone(_optional_text_path(payload, "null"))

    def test_optional_boolean_normalization_covers_every_input_family(self) -> None:
        for value, expected in (
            (True, True),
            (False, False),
            (1, True),
            (0, False),
            (2, True),
            (0.5, False),
            (" TRUE ", True),
            ("false", False),
            ("yes", True),
            ("no", False),
            ("on", True),
            ("off", False),
            ("enabled", True),
            ("disabled", False),
        ):
            with self.subTest(value=value):
                self.assertIs(_normalized_optional_bool_value(value), expected)

        for value in (None, object(), "1", "unexpected", ""):
            with self.subTest(unsupported=value):
                self.assertIsNone(_normalized_optional_bool_value(value))

        payload: dict[str, object] = {
            "direct": "enabled",
            "numeric_string": "1",
            "unknown": "unexpected",
        }
        self.assertIsNone(_optional_bool_path(payload, None))
        self.assertIs(_optional_bool_path(payload, "direct"), True)
        self.assertIs(_optional_bool_path(payload, "numeric_string"), True)
        self.assertIs(_optional_bool_path(payload, "unknown"), False)

    def test_confidence_sum_and_csv_boundaries_are_exact(self) -> None:
        payload: dict[str, object] = {
            "low": -0.1,
            "zero": 0.0,
            "middle": 0.25,
            "one": 1.0,
            "high": 1.1,
            "invalid": "invalid",
        }
        self.assertIsNone(_optional_confidence_path(payload, None))
        self.assertIsNone(_optional_confidence_path(payload, "invalid"))
        self.assertEqual(_optional_confidence_path(payload, "low"), 0.0)
        self.assertEqual(_optional_confidence_path(payload, "zero"), 0.0)
        self.assertEqual(_optional_confidence_path(payload, "middle"), 0.25)
        self.assertEqual(_optional_confidence_path(payload, "one"), 1.0)
        self.assertEqual(_optional_confidence_path(payload, "high"), 1.0)

        self.assertIsNone(_sum_optional((None, None)))
        self.assertEqual(_sum_optional((None, 1, 2.5)), 3.5)
        self.assertEqual(_sum_optional((0.0,)), 0.0)

        self.assertEqual(_csv_filter(None), ())
        self.assertEqual(_csv_filter("  "), ())
        self.assertEqual(_csv_filter(" first, , second ,third "), ("first", "second", "third"))


if __name__ == "__main__":
    unittest.main()

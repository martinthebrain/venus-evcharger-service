# SPDX-License-Identifier: GPL-3.0-or-later
"""Complete boundary contracts for primitive state normalization."""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from venus_evcharger.core.contracts_basic import (
    exception_detail,
    finite_float_or_none,
    mutable_dict_attr,
    non_negative_float_or_none,
    non_negative_int,
    normalize_auto_state,
    normalize_binary_flag,
    normalize_learning_phase,
    normalize_learning_state,
    normalize_optional_binary_state,
    normalized_auto_state_pair,
    normalized_fault_state,
    normalized_status_source,
    optional_text,
    paired_optional_values,
    thresholds_ordered,
    timestamp_age_within,
    timestamp_not_future,
    valid_battery_soc,
)


class TestCoreContractsBasicContracts(unittest.TestCase):
    def test_finite_float_contract(self) -> None:
        for value, expected in ((0, 0.0), (-1, -1.0), ("2.5", 2.5)):
            self.assertEqual(finite_float_or_none(value), expected)
        for value in (None, True, False, "bad", float("nan"), float("inf"), float("-inf")):
            with self.subTest(value=value):
                self.assertIsNone(finite_float_or_none(value))

    def test_mutable_dict_attribute_contract(self) -> None:
        existing = {"value": 1}
        target = SimpleNamespace(state=existing)
        self.assertIs(mutable_dict_attr(target, "state"), existing)
        missing = SimpleNamespace()
        created = mutable_dict_attr(missing, "state")
        self.assertEqual(created, {})
        self.assertIs(missing.state, created)
        invalid = SimpleNamespace(state=[])
        replacement = mutable_dict_attr(invalid, "state")
        self.assertEqual(replacement, {})
        self.assertIs(invalid.state, replacement)

    def test_exception_and_optional_text_contract(self) -> None:
        self.assertEqual(exception_detail(ValueError(" problem ")), "problem")
        self.assertEqual(exception_detail(ValueError("")), "ValueError")
        self.assertEqual(optional_text(" value "), "value")
        self.assertEqual(optional_text(12), "12")
        self.assertIsNone(optional_text(" "))
        self.assertIsNone(optional_text(None))

    def test_non_negative_number_contracts(self) -> None:
        self.assertEqual(non_negative_float_or_none(0), 0.0)
        self.assertEqual(non_negative_float_or_none("2.5"), 2.5)
        for value in (-0.1, None, True, "bad"):
            self.assertIsNone(non_negative_float_or_none(value))
        for value, expected in ((0, 0), (4.9, 4), ("5", 5), (-1, 0), (True, 7), (None, 7), ("bad", 7)):
            with self.subTest(value=value):
                self.assertEqual(non_negative_int(value, 7), expected)
        self.assertEqual(non_negative_int("bad"), 0)

    def test_binary_normalization_contracts(self) -> None:
        for value, expected in ((True, 1), (False, 0), (-1, 0), (0, 0), (1, 1), (2, 1), ("bad", 0)):
            with self.subTest(value=value):
                self.assertEqual(normalize_binary_flag(value), expected)
        self.assertEqual(normalize_binary_flag("bad", 2), 1)
        self.assertIsNone(normalize_optional_binary_state(None))
        self.assertIs(normalize_optional_binary_state(0), False)
        self.assertIs(normalize_optional_binary_state(2), True)

    def test_learning_normalization_contracts(self) -> None:
        for value in ("unknown", "learning", "stable", "stale"):
            self.assertEqual(normalize_learning_state(value.upper()), value)
        self.assertEqual(normalize_learning_state(None), "unknown")
        self.assertEqual(normalize_learning_state("invalid"), "unknown")
        for value in ("L1", "L2", "L3", "3P"):
            self.assertEqual(normalize_learning_phase(value.lower()), value)
        self.assertIsNone(normalize_learning_phase(None))
        self.assertIsNone(normalize_learning_phase("L4"))

    def test_optional_pair_contract(self) -> None:
        self.assertTrue(paired_optional_values(None, None))
        self.assertTrue(paired_optional_values(0, False))
        self.assertFalse(paired_optional_values(None, 0))
        self.assertFalse(paired_optional_values(0, None))

    def test_battery_soc_contract(self) -> None:
        for value in (None, 0, 50.5, 100, "75", True, "bad", float("nan")):
            self.assertTrue(valid_battery_soc(value))
        for value in (-0.01, 100.01):
            self.assertFalse(valid_battery_soc(value))

    def test_timestamp_not_future_contract(self) -> None:
        self.assertTrue(timestamp_not_future(101.0, 100.0))
        self.assertFalse(timestamp_not_future(101.001, 100.0))
        self.assertTrue(timestamp_not_future(102.0, 100.0, 2.0))
        self.assertFalse(timestamp_not_future(None, 100.0))

    def test_timestamp_age_contract(self) -> None:
        self.assertTrue(timestamp_age_within(90.0, 100.0, 10.0))
        self.assertFalse(timestamp_age_within(89.999, 100.0, 10.0))
        self.assertTrue(timestamp_age_within(101.0, 100.0, 10.0))
        self.assertFalse(timestamp_age_within(101.001, 100.0, 10.0))
        self.assertTrue(timestamp_age_within(102.0, 100.0, 10.0, future_tolerance_seconds=2.0))
        self.assertFalse(timestamp_age_within(None, 100.0, 10.0))

    def test_threshold_order_contract(self) -> None:
        self.assertTrue(thresholds_ordered(100, 0))
        self.assertTrue(thresholds_ordered(100, 100))
        self.assertFalse(thresholds_ordered(100, 100.001))
        self.assertFalse(thresholds_ordered(-1, 0))
        self.assertFalse(thresholds_ordered(100, None))

    def test_auto_state_contract(self) -> None:
        expected_codes = {"idle": 0, "waiting": 1, "learning": 2, "charging": 3, "blocked": 4, "recovery": 5}
        for state, code in expected_codes.items():
            self.assertEqual(normalize_auto_state(f" {state.upper()} "), state)
            self.assertEqual(normalized_auto_state_pair(state, 999), (state, code))
        self.assertEqual(normalize_auto_state(None), "idle")
        self.assertEqual(normalize_auto_state("invalid"), "idle")
        self.assertEqual(normalized_auto_state_pair("invalid", 5), ("idle", 0))

    def test_status_and_fault_contract(self) -> None:
        self.assertEqual(normalized_status_source(" source "), "source")
        self.assertEqual(normalized_status_source(12), "12")
        self.assertEqual(normalized_status_source(None), "unknown")
        self.assertEqual(normalized_status_source(" "), "unknown")
        self.assertEqual(normalized_fault_state(None), ("", 0))
        self.assertEqual(normalized_fault_state(" "), ("", 0))
        self.assertEqual(normalized_fault_state(" fault "), ("fault", 1))


if __name__ == "__main__":
    unittest.main()

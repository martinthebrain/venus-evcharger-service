# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared assertions for typed runtime-state contracts."""

from __future__ import annotations

import unittest


def typed_values(
    *,
    none: tuple[str, ...] = (),
    false: tuple[str, ...] = (),
    true: tuple[str, ...] = (),
    integers: tuple[str, ...] = (),
    floats: tuple[str, ...] = (),
    ones: tuple[str, ...] = (),
    fives: tuple[str, ...] = (),
    empty_text: tuple[str, ...] = (),
    empty_dicts: tuple[str, ...] = (),
    empty_lists: tuple[str, ...] = (),
    text: dict[str, str] | None = None,
) -> dict[str, object]:
    """Build an explicit expected-state mapping grouped by typed defaults."""
    values: dict[str, object] = {}
    values.update(dict.fromkeys(none, None))
    values.update(dict.fromkeys(false, False))
    values.update(dict.fromkeys(true, True))
    values.update(dict.fromkeys(integers, 0))
    values.update(dict.fromkeys(floats, 0.0))
    values.update(dict.fromkeys(ones, 1.0))
    values.update(dict.fromkeys(fives, 5.0))
    values.update(dict.fromkeys(empty_text, ""))
    values.update({key: {} for key in empty_dicts})
    values.update({key: [] for key in empty_lists})
    values.update(text or {})
    return values


def assert_typed_mapping(
    case: unittest.TestCase,
    actual: dict[str, object],
    expected: dict[str, object],
) -> None:
    """Assert exact keys, values, and concrete value types."""
    case.assertEqual(set(actual), set(expected))
    for key, expected_value in expected.items():
        actual_value = actual[key]
        case.assertIs(type(actual_value), type(expected_value), key)
        case.assertEqual(actual_value, expected_value, key)


__all__ = ["assert_typed_mapping", "typed_values"]

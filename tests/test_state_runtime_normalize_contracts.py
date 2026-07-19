# SPDX-License-Identifier: GPL-3.0-or-later
"""Exact contracts for volatile runtime-state normalization."""

from __future__ import annotations

import math
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from venus_evcharger.controllers.state_runtime_normalize import RuntimeStateNormalizer, require_runtime_clock


class TestStateRuntimeNormalizeContracts(unittest.TestCase):
    def test_runtime_clock_boundary_requires_callable_time_now_and_preserves_identity(self) -> None:
        clock = SimpleNamespace(time_now=lambda: 12.5)

        self.assertIs(require_runtime_clock(clock), clock)
        for invalid in (object(), SimpleNamespace(time_now=12.5)):
            with self.subTest(invalid=invalid):
                with self.assertRaisesRegex(AttributeError, r"^state service must expose time_now\(\)$"):
                    require_runtime_clock(invalid)

    def test_integer_and_float_coercion_preserve_exact_defaults_and_types(self) -> None:
        self.assertEqual(RuntimeStateNormalizer.coerce_runtime_int(True), 0)
        self.assertEqual(RuntimeStateNormalizer.coerce_runtime_int(False, 7), 7)
        self.assertEqual(RuntimeStateNormalizer.coerce_runtime_int(object(), 8), 8)
        self.assertEqual(RuntimeStateNormalizer.coerce_runtime_int("3.5", 9), 9)
        self.assertEqual(RuntimeStateNormalizer.coerce_runtime_int("3"), 3)
        self.assertEqual(RuntimeStateNormalizer.coerce_runtime_int(4.9), 4)
        self.assertEqual(RuntimeStateNormalizer.coerce_runtime_float("2.5"), 2.5)
        self.assertEqual(RuntimeStateNormalizer.coerce_runtime_float(None), 0.0)
        self.assertEqual(RuntimeStateNormalizer.coerce_runtime_float(math.inf, 6.5), 6.5)
        self.assertIsNone(RuntimeStateNormalizer.optional_float(None))
        self.assertEqual(RuntimeStateNormalizer.optional_float("3.25"), 3.25)

    def test_past_time_rejects_future_values_at_the_exact_one_second_boundary(self) -> None:
        self.assertIsNone(RuntimeStateNormalizer.optional_past_time(None, 10.0))
        self.assertEqual(RuntimeStateNormalizer.optional_past_time(11.0, 10.0), 11.0)
        self.assertIsNone(RuntimeStateNormalizer.optional_past_time(11.0001, 10.0))
        with patch("venus_evcharger.controllers.state_runtime_normalize.time.time", return_value=20.0) as now:
            self.assertEqual(RuntimeStateNormalizer.optional_past_time(19.0), 19.0)
        now.assert_called_once_with()

    def test_learning_and_phase_normalizers_delegate_with_exact_arguments(self) -> None:
        with patch(
            "venus_evcharger.controllers.state_runtime_normalize.normalize_learning_state",
            return_value="stable",
        ) as state:
            self.assertEqual(RuntimeStateNormalizer.learned_charge_power_state(" Stable "), "stable")
        state.assert_called_once_with(" Stable ")
        with patch(
            "venus_evcharger.controllers.state_runtime_normalize.normalize_learning_phase",
            return_value="L1",
        ) as phase:
            self.assertEqual(RuntimeStateNormalizer.learned_charge_power_phase("l1"), "L1")
        phase.assert_called_once_with("l1")
        with patch(
            "venus_evcharger.controllers.state_runtime_normalize.normalize_phase_selection",
            return_value="P1_P2",
        ) as normalize:
            self.assertEqual(RuntimeStateNormalizer.phase_selection("bad", "P1_P2"), "P1_P2")
        normalize.assert_called_once_with("bad", "P1_P2")
        with patch(
            "venus_evcharger.controllers.state_runtime_normalize.normalize_phase_selection",
            return_value="P1",
        ) as normalize:
            RuntimeStateNormalizer.phase_selection("bad")
        normalize.assert_called_once_with("bad", "P1")

        with patch(
            "venus_evcharger.controllers.state_runtime_normalize.normalize_phase_selection_tuple",
            return_value=("P1", "P1_P2"),
        ) as normalize_tuple:
            self.assertEqual(
                RuntimeStateNormalizer.supported_phase_selections(["P1"], ("P1_P2",)),
                ("P1", "P1_P2"),
            )
        normalize_tuple.assert_called_once_with(["P1"], ("P1_P2",))

    def test_optional_phase_text_and_switch_state_contracts_are_exact(self) -> None:
        self.assertIsNone(RuntimeStateNormalizer.optional_phase_selection(None))
        with patch.object(
            RuntimeStateNormalizer,
            "phase_selection",
            return_value="P1_P2_P3",
        ) as normalize:
            self.assertEqual(
                RuntimeStateNormalizer.optional_phase_selection("bad", "P1_P2"),
                "P1_P2_P3",
            )
        normalize.assert_called_once_with("bad", "P1_P2")
        with patch.object(
            RuntimeStateNormalizer,
            "phase_selection",
            return_value="P1",
        ) as normalize:
            RuntimeStateNormalizer.optional_phase_selection("bad")
        normalize.assert_called_once_with("bad", "P1")

        self.assertIsNone(RuntimeStateNormalizer.optional_text(None))
        self.assertIsNone(RuntimeStateNormalizer.optional_text("  "))
        self.assertEqual(RuntimeStateNormalizer.optional_text(" value "), "value")
        self.assertEqual(RuntimeStateNormalizer.optional_text(17), "17")
        for raw, expected in (
            (" waiting-relay-off ", "waiting-relay-off"),
            ("STABILIZING", "stabilizing"),
            (None, None),
            ("", None),
            ("unknown", None),
        ):
            self.assertEqual(RuntimeStateNormalizer.phase_switch_state(raw), expected)

    def test_load_time_requires_service_clock_and_normalizes_its_value(self) -> None:
        service_clock = MagicMock(return_value="12.5")
        service = require_runtime_clock(SimpleNamespace(time_now=service_clock))
        with patch("venus_evcharger.controllers.state_runtime_normalize.time.time", return_value=99.0) as system:
            self.assertEqual(RuntimeStateNormalizer.load_time(service), 12.5)
        service_clock.assert_called_once_with()
        system.assert_called_once_with()

        bad_clock = MagicMock(return_value="bad")
        bad_service = require_runtime_clock(SimpleNamespace(time_now=bad_clock))
        with patch("venus_evcharger.controllers.state_runtime_normalize.time.time", return_value=30.0):
            self.assertEqual(RuntimeStateNormalizer.load_time(bad_service), 30.0)


if __name__ == "__main__":
    unittest.main()

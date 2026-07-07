# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import unittest
from types import SimpleNamespace

from venus_evcharger.auto.tracking import clear_auto_decision_tracking


class TestAutoDecisionTracking(unittest.TestCase):
    def test_clear_auto_decision_tracking_ignores_absent_fields(self) -> None:
        service = SimpleNamespace(unrelated="keep")

        self.assertIs(clear_auto_decision_tracking(service), False)

        self.assertEqual(vars(service), {"unrelated": "keep"})

    def test_clear_auto_decision_tracking_reports_unchanged_when_all_fields_are_none(self) -> None:
        service = SimpleNamespace(
            auto_start_condition_since=None,
            auto_stop_condition_since=None,
            auto_stop_condition_reason=None,
        )

        self.assertIs(clear_auto_decision_tracking(service), False)

        self.assertIsNone(service.auto_start_condition_since)
        self.assertIsNone(service.auto_stop_condition_since)
        self.assertIsNone(service.auto_stop_condition_reason)

    def test_clear_auto_decision_tracking_clears_each_timer_field_individually(self) -> None:
        field_values = {
            "auto_start_condition_since": 10.0,
            "auto_stop_condition_since": 20.0,
            "auto_stop_condition_reason": "auto-stop-surplus",
        }

        for field_name, field_value in field_values.items():
            with self.subTest(field_name=field_name):
                service = SimpleNamespace(**{field_name: field_value}, unrelated="keep")

                self.assertTrue(clear_auto_decision_tracking(service))

                self.assertIsNone(getattr(service, field_name))
                self.assertEqual(service.unrelated, "keep")

    def test_clear_auto_decision_tracking_clears_falsey_but_present_values(self) -> None:
        service = SimpleNamespace(
            auto_start_condition_since=0.0,
            auto_stop_condition_since=False,
            auto_stop_condition_reason="",
        )

        self.assertTrue(clear_auto_decision_tracking(service))

        self.assertIsNone(service.auto_start_condition_since)
        self.assertIsNone(service.auto_stop_condition_since)
        self.assertIsNone(service.auto_stop_condition_reason)


if __name__ == "__main__":
    unittest.main()

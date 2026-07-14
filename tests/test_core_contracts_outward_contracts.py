# SPDX-License-Identifier: GPL-3.0-or-later
"""Exact outward-state normalization contracts."""

from __future__ import annotations

import unittest

from venus_evcharger.core.contracts_outward import (
    AUTO_METRIC_NUMERIC_FIELDS,
    AUTO_METRIC_TEXT_FIELDS,
    _normalized_metric_text,
    sanitized_auto_metrics,
)


class TestCoreContractsOutwardContracts(unittest.TestCase):
    def test_metric_text_preserves_non_null_values(self) -> None:
        self.assertIsNone(_normalized_metric_text(None))
        self.assertEqual(_normalized_metric_text("profile"), "profile")
        self.assertEqual(_normalized_metric_text(17), "17")

    def test_every_numeric_and_text_metric_is_normalized(self) -> None:
        metrics = {
            field: str(index + 1.25)
            for index, field in enumerate(AUTO_METRIC_NUMERIC_FIELDS)
        }
        metrics["start_threshold"] = "1850.5"
        metrics["stop_threshold"] = "1350.25"
        metrics.update(
            {
                field: index + 10
                for index, field in enumerate(AUTO_METRIC_TEXT_FIELDS)
            }
        )

        sanitized = sanitized_auto_metrics(metrics)

        self.assertEqual(
            {field: sanitized[field] for field in AUTO_METRIC_NUMERIC_FIELDS},
            {
                field: float(metrics[field])
                for field in AUTO_METRIC_NUMERIC_FIELDS
            },
        )
        self.assertEqual(
            {field: sanitized[field] for field in AUTO_METRIC_TEXT_FIELDS},
            {
                field: str(index + 10)
                for index, field in enumerate(AUTO_METRIC_TEXT_FIELDS)
            },
        )

    def test_valid_threshold_order_is_preserved(self) -> None:
        sanitized = sanitized_auto_metrics(
            {"start_threshold": "1850.5", "stop_threshold": "1350.25"}
        )
        self.assertEqual(sanitized["start_threshold"], 1850.5)
        self.assertEqual(sanitized["stop_threshold"], 1350.25)


if __name__ == "__main__":
    unittest.main()

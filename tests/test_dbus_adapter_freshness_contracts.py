#!/usr/bin/env python3
"""Behavioral contracts for DBus adapter cache freshness metrics."""

from __future__ import annotations

import unittest

from venus_evcharger.dbus_adapter.health.freshness import (
    cache_freshness,
    cached_entry_age,
    cached_entry_float,
    important_freshness,
    max_cached_path_age,
    missing_cached_path_count,
    status_counts,
)
from venus_evcharger.dbus_gateway import DbusCacheStore, dbus_path_key


class DbusAdapterFreshnessContractTests(unittest.TestCase):
    def test_status_counts_include_unknown_and_accumulate(self) -> None:
        values = {
            "a": {"status": "fresh"},
            "b": {"status": "fresh"},
            "c": {"status": "error"},
            "d": {},
            "e": {"status": None},
        }
        self.assertEqual(status_counts(values), {"fresh": 2, "error": 1, "unknown": 1, "None": 1})
        self.assertEqual(status_counts({}), {})

    def test_important_freshness_has_all_and_only_fast_read_fields(self) -> None:
        values = {
            "grid_power_w": {"age_s": "1.25", "status": "fresh"},
            "pv_power_w": {"age_s": 2.5, "status": "error"},
            "battery_soc": {"age_s": object(), "status": None},
            "unrelated": {"age_s": 99.0, "status": "fresh"},
        }
        self.assertEqual(
            important_freshness(values),
            {
                "grid_power_w_age_s": 1.25,
                "pv_power_w_age_s": 2.5,
                "battery_soc_age_s": 0.0,
                "grid_power_w_status": "fresh",
                "pv_power_w_status": "error",
                "battery_soc_status": "None",
            },
        )
        self.assertEqual(
            important_freshness({}),
            {
                "grid_power_w_age_s": 0.0,
                "pv_power_w_age_s": 0.0,
                "battery_soc_age_s": 0.0,
                "grid_power_w_status": "missing",
                "pv_power_w_status": "missing",
                "battery_soc_status": "missing",
            },
        )

    def test_cached_entry_age_rejects_invalid_zero_and_future_timestamps(self) -> None:
        self.assertEqual(cached_entry_age({"updated_at": 90.0}, 100.0), 10.0)
        self.assertEqual(cached_entry_age({"updated_at": "99.5"}, 100.0), 0.5)
        self.assertEqual(cached_entry_age({"updated_at": 0.5}, 1.0), 0.5)
        for entry in (None, object(), [], {}, {"updated_at": 0.0}, {"updated_at": -1.0}):
            with self.subTest(entry=entry):
                self.assertEqual(cached_entry_age(entry, 100.0), 0.0)
        self.assertEqual(cached_entry_age({"updated_at": 110.0}, 100.0), 0.0)

    def test_cached_path_metrics_use_service_qualified_keys(self) -> None:
        values = {
            dbus_path_key("svc", "/Fresh"): {"updated_at": 90.0},
            dbus_path_key("svc", "/Recent"): {"updated_at": 98.0},
            dbus_path_key("other", "/Fresh"): {"updated_at": 1.0},
        }
        paths = {"/Fresh", "/Recent", "/Missing"}
        self.assertEqual(max_cached_path_age(values, "svc", paths, 100.0), 10.0)
        self.assertEqual(max_cached_path_age(values, "missing", paths, 100.0), 0.0)
        self.assertEqual(max_cached_path_age(values, "svc", set(), 100.0), 0.0)
        self.assertEqual(missing_cached_path_count(values, "svc", paths), 1.0)
        self.assertEqual(missing_cached_path_count(values, "missing", paths), 3.0)
        self.assertEqual(missing_cached_path_count(values, "svc", set()), 0.0)

    def test_cached_entry_float_requires_mapping_and_normalizes_value(self) -> None:
        self.assertEqual(cached_entry_float({"value": "12.5"}), 12.5)
        self.assertEqual(cached_entry_float({"value": -3}), -3.0)
        for entry in (None, object(), [], {}, {"value": object()}):
            with self.subTest(entry=entry):
                self.assertEqual(cached_entry_float(entry), 0.0)

    def test_cache_freshness_combines_count_status_and_important_fields(self) -> None:
        cache = DbusCacheStore(stale_after_seconds=10.0)
        cache.update_value("grid_power_w", 123.0, source="grid", status="fresh", now=99.0)
        cache.update_value("pv_power_w", 45.0, source="pv", status="cached", now=98.0)
        cache.mark_error("pv_power_w", source="pv", error="no reply", now=100.0)

        self.assertEqual(
            cache_freshness(cache, 100.0),
            {
                "value_count": 2,
                "status_counts": {"fresh": 1, "error": 1},
                "grid_power_w_age_s": 1.0,
                "pv_power_w_age_s": 2.0,
                "battery_soc_age_s": 0.0,
                "grid_power_w_status": "fresh",
                "pv_power_w_status": "error",
                "battery_soc_status": "missing",
            },
        )


if __name__ == "__main__":
    unittest.main()

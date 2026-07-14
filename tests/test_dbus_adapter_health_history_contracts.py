#!/usr/bin/env python3
"""Behavioral contracts for compact DBus adapter health history."""

from __future__ import annotations

import unittest
from unittest.mock import patch

import venus_evcharger.dbus_adapter_health_history as history


class DbusAdapterHealthHistoryContractTests(unittest.TestCase):
    def test_mapping_child_accepts_only_mappings(self) -> None:
        child = {"value": 1}
        self.assertIs(history.mapping_child({"child": child}, "child"), child)
        for value in (None, "bad", [], 3):
            with self.subTest(value=value):
                self.assertEqual(history.mapping_child({"child": value}, "child"), {})
        self.assertEqual(history.mapping_child({}, "missing"), {})

    def test_cache_freshness_selects_exact_historical_fields(self) -> None:
        source = {
            "grid_power_w_age_s": 1.0,
            "grid_power_w_status": "fresh",
            "pv_power_w_age_s": 2.0,
            "pv_power_w_status": "stale",
            "battery_soc_age_s": 3.0,
            "battery_soc_status": "missing",
            "ignored": "not logged",
        }
        self.assertEqual(
            history.health_log_cache_freshness(source),
            {
                "grid_power_w_age_s": 1.0,
                "grid_power_w_status": "fresh",
                "pv_power_w_age_s": 2.0,
                "pv_power_w_status": "stale",
                "battery_soc_age_s": 3.0,
                "battery_soc_status": "missing",
            },
        )
        self.assertEqual(
            history.health_log_cache_freshness({}),
            {
                "grid_power_w_age_s": None,
                "grid_power_w_status": None,
                "pv_power_w_age_s": None,
                "pv_power_w_status": None,
                "battery_soc_age_s": None,
                "battery_soc_status": None,
            },
        )

    def test_health_payload_is_compact_stable_and_defaulted(self) -> None:
        health = {
            "state": "degraded",
            "timeouts_60s": 3,
            "queues": {"oldest_command_age_s": 4.5, "oldest_core_command_age_s": 5.5, "ignored": 9},
            "eventloop": {"max_tick_gap_ms_60s": 123.0, "ignored": 9},
            "backpressure": {"state": "slow", "ignored": 9},
            "cache_freshness": {
                "grid_power_w_age_s": 1.0,
                "grid_power_w_status": "fresh",
                "pv_power_w_age_s": 2.0,
                "pv_power_w_status": "stale",
                "battery_soc_age_s": 3.0,
                "battery_soc_status": "missing",
            },
            "ignored": "not logged",
        }
        with patch.object(history.time, "time", return_value=123.456):
            self.assertEqual(
                history.health_log_payload(health),
                {
                    "at": 123.456,
                    "state": "degraded",
                    "backpressure": "slow",
                    "queue_oldest_age_s": 4.5,
                    "core_queue_oldest_age_s": 5.5,
                    "max_tick_gap_ms_60s": 123.0,
                    "timeouts_60s": 3,
                    "cache_freshness": history.health_log_cache_freshness(health["cache_freshness"]),
                },
            )

        with patch.object(history.time, "time", return_value=222.0):
            self.assertEqual(
                history.health_log_payload(
                    {"queues": "bad", "eventloop": [], "backpressure": object(), "cache_freshness": None}
                ),
                {
                    "at": 222.0,
                    "state": "unknown",
                    "backpressure": "unknown",
                    "queue_oldest_age_s": 0.0,
                    "core_queue_oldest_age_s": 0.0,
                    "max_tick_gap_ms_60s": 0.0,
                    "timeouts_60s": 0,
                    "cache_freshness": history.health_log_cache_freshness({}),
                },
            )

    def test_append_health_log_delegates_payload_and_limit(self) -> None:
        health = {"state": "ok"}
        with (
            patch.object(history.time, "time", return_value=10.0),
            patch.object(history, "append_jsonl") as append_jsonl,
        ):
            history.append_health_log("/run/gateway/health.jsonl", health, max_bytes=321)
        append_jsonl.assert_called_once_with(
            "/run/gateway/health.jsonl",
            history.health_log_payload(health) | {"at": 10.0},
            max_bytes=321,
        )


if __name__ == "__main__":
    unittest.main()

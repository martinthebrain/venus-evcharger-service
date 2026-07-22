#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Health-history payloads for the DBus adapter."""

from __future__ import annotations

import time
from collections.abc import Mapping

from venus_evcharger.dbus_adapter.jsonl import DEFAULT_HEALTH_HISTORY_MAX_BYTES, append_jsonl
from venus_evcharger.ipc.command_types import CommandPayload


def append_health_log(
    path: str,
    health: Mapping[str, object],
    *,
    max_bytes: int = DEFAULT_HEALTH_HISTORY_MAX_BYTES,
) -> None:
    append_jsonl(path, health_log_payload(health), max_bytes=max_bytes)


def health_log_payload(health: Mapping[str, object]) -> CommandPayload:
    queues = mapping_child(health, "queues")
    eventloop = mapping_child(health, "eventloop")
    cache = mapping_child(health, "cache_freshness")
    backpressure = mapping_child(health, "backpressure")
    return {
        "at": time.time(),
        "state": health.get("state", "unknown"),
        "backpressure": backpressure.get("state", "unknown"),
        "queue_oldest_age_s": queues.get("oldest_command_age_s", 0.0),
        "queue_oldest_slo_age_s": queues.get("oldest_slo_command_age_s", 0.0),
        "core_queue_oldest_age_s": queues.get("oldest_core_command_age_s", 0.0),
        "max_tick_gap_ms_60s": eventloop.get("max_tick_gap_ms_60s", 0.0),
        "timeouts_60s": health.get("timeouts_60s", 0),
        "cache_freshness": health_log_cache_freshness(cache),
    }


def mapping_child(parent: Mapping[str, object], key: str) -> Mapping[str, object]:
    value = parent.get(key)
    return value if isinstance(value, Mapping) else {}


def health_log_cache_freshness(cache_freshness: Mapping[str, object]) -> CommandPayload:
    return {
        key: cache_freshness.get(key)
        for key in (
            "grid_power_w_age_s",
            "grid_power_w_status",
            "pv_power_w_age_s",
            "pv_power_w_status",
            "battery_soc_age_s",
            "battery_soc_status",
            "optional_source_error_count",
            "optional_source_unavailable_count",
        )
    }

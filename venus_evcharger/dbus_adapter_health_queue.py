#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Queue health metrics for the dedicated DBus adapter process."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from venus_evcharger.dbus_gateway import command_queue_class


def queue_class_health(pending: list[tuple[str, dict[str, Any]]], now: float) -> dict[str, Any]:
    classes: dict[str, dict[str, Any]] = {}
    for _path, command in pending:
        queue_class = str(command.get("queue_class") or command_queue_class(command))
        entry = classes.setdefault(queue_class, {"pending": 0, "oldest_age_s": 0.0})
        entry["pending"] = int(entry["pending"]) + 1
        entry["oldest_age_s"] = max(float(entry["oldest_age_s"]), 0.0, now - command_activity_at(command, now))
    return dict(sorted(classes.items()))


def queue_health(
    pending: list[tuple[str, dict[str, Any]]],
    core_pending: list[tuple[str, dict[str, Any]]],
    now: float,
    *,
    physical_count: int | None = None,
    write_scheduler_health: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    scheduler = write_scheduler_health or {}
    processed_commands_60s = int(scheduler.get("processed_commands_60s", 0) or 0)
    return {
        "pending_command_count": len(pending),
        "physical_command_count": physical_command_count_from_pending(pending, physical_count),
        "oldest_command_age_s": oldest_command_age(pending, now),
        "core_command_count": len(core_pending),
        "oldest_core_command_age_s": oldest_command_age(core_pending, now),
        "processed_commands_60s": processed_commands_60s,
        "queue_drain_rate_per_s": float(processed_commands_60s) / 60.0,
        "last_processed_at": float(scheduler.get("last_processed_at", 0.0) or 0.0),
    }


def oldest_command_age(commands: list[tuple[str, dict[str, Any]]], now: float) -> float:
    ages = [max(0.0, now - command_activity_at(command, now)) for _path, command in commands]
    return max(ages) if ages else 0.0


def command_activity_at(command: Mapping[str, Any], now: float) -> float:
    timestamp = command.get("updated_at") if command.get("updated_at") is not None else command.get("created_at")
    try:
        return float(timestamp if timestamp is not None else now)
    except (TypeError, ValueError):
        return now


def physical_command_count_from_pending(
    pending: list[tuple[str, dict[str, Any]]],
    physical_count: int | None,
) -> int:
    return len(pending) if physical_count is None else int(physical_count)

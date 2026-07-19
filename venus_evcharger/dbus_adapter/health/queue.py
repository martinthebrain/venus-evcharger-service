#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Queue health metrics for the DBus adapter."""

from __future__ import annotations

from collections.abc import Mapping

from venus_evcharger.dbus_gateway import command_queue_class
from venus_evcharger.dbus_gateway_command_types import CommandFileList, CommandMapping, CommandPayload
from venus_evcharger.dbus_gateway_core import float_or_zero


def queue_class_health(pending: CommandFileList, now: float) -> CommandPayload:
    classes: dict[str, dict[str, object]] = {}
    for _path, command in pending:
        queue_class = str(command.get("queue_class") or command_queue_class(command))
        entry = classes.setdefault(queue_class, {"pending": 0, "oldest_age_s": 0.0})
        entry["pending"] = int(float_or_zero(entry.get("pending"))) + 1
        entry["oldest_age_s"] = max(float_or_zero(entry.get("oldest_age_s")), now - command_activity_at(command, now))
    return dict(sorted(classes.items()))


def queue_health(
    pending: CommandFileList,
    core_pending: CommandFileList,
    now: float,
    *,
    physical_count: int | None = None,
    write_scheduler_health: Mapping[str, object] | None = None,
) -> CommandPayload:
    scheduler = write_scheduler_health or {}
    processed_commands_60s = int(float_or_zero(scheduler.get("processed_commands_60s")))
    return {
        "pending_command_count": len(pending),
        "physical_command_count": physical_command_count_from_pending(pending, physical_count),
        "oldest_command_age_s": oldest_command_age(pending, now),
        "core_command_count": len(core_pending),
        "oldest_core_command_age_s": oldest_command_age(core_pending, now),
        "processed_commands_60s": processed_commands_60s,
        "queue_drain_rate_per_s": float(processed_commands_60s) / 60.0,
        "last_processed_at": float_or_zero(scheduler.get("last_processed_at")),
    }


def oldest_command_age(commands: CommandFileList, now: float) -> float:
    ages = [max(0.0, now - command_activity_at(command, now)) for _path, command in commands]
    return max(ages) if ages else 0.0


def command_activity_at(command: CommandMapping, now: float) -> float:
    timestamp = command.get("updated_at") if command.get("updated_at") is not None else command.get("created_at")
    parsed = float_or_zero(timestamp)
    return parsed if parsed > 0.0 else now


def physical_command_count_from_pending(
    pending: CommandFileList,
    physical_count: int | None,
) -> int:
    return len(pending) if physical_count is None else int(physical_count)

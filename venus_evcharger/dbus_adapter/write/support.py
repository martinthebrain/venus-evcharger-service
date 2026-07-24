# SPDX-License-Identifier: GPL-3.0-or-later
"""Pure command helpers for scheduled DBus writes."""

from __future__ import annotations

import time

from venus_evcharger.dbus_gateway_core import float_or_zero
from venus_evcharger.ipc.command_mailbox import command_priority_rank as priority_rank
from venus_evcharger.ipc.command_types import CommandFileList, CommandMapping, CommandPayload
from venus_evcharger.ipc.deadline import command_deadline_expired

__all__ = (
    "budget_elapsed",
    "command_deadline_expired",
    "command_kind",
    "command_ready",
    "float_or_zero",
    "is_local_publish_command",
    "is_urgent_durable_command",
    "lifecycle_payload",
    "local_publish_action_result",
    "priority_rank",
    "stale_coalesced_paths",
)

def command_ready(command: CommandMapping, now: float) -> bool:
    """Return whether an asynchronous command step may run now."""
    return bool(float_or_zero(command.get("not_before")) <= float(now))


def is_local_publish_command(command: CommandMapping) -> bool:
    return command_kind(command) in {"publish_evcs_fields", "publish_companion_fields"}


def is_urgent_durable_command(command: CommandMapping) -> bool:
    """Return whether durable work must overtake transient publication bursts."""
    priority = str(command.get("priority") or "").strip().lower()
    return priority in {"safety", "user"}


def local_publish_action_result(processed: int, action: str) -> tuple[int, bool]:
    if action == "break":
        return processed, True
    return processed + 1 if action == "processed" else processed, False


def budget_elapsed(started: float, budget_seconds: float) -> bool:
    return time.monotonic() - started >= budget_seconds


def command_kind(command: CommandMapping) -> str:
    return str(command.get("kind") or command.get("type") or "")


def stale_coalesced_paths(
    commands: CommandFileList,
    *,
    processed_path: str,
    key: str,
) -> list[str]:
    stale: list[str] = []
    for path, command in commands:
        coalesce_key = command.get("coalesce_key")
        if path != processed_path and coalesce_key and str(coalesce_key) == key:
            stale.append(path)
    return stale


def lifecycle_payload(command: CommandMapping, state: str, queue_class: str, now: float) -> CommandPayload:
    return {
        "at": now,
        "state": state,
        "queue_class": queue_class,
        "kind": command_kind(command),
        "id": command.get("id", ""),
        "coalesce_key": command.get("coalesce_key", ""),
    }

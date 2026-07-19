# SPDX-License-Identifier: GPL-3.0-or-later
"""Pure command helpers for scheduled DBus writes."""

from __future__ import annotations

import time

from venus_evcharger.dbus_adapter.contracts import CommandOutcome
from venus_evcharger.dbus_gateway_command_types import CommandFile, CommandFileList, CommandMapping, CommandPayload
from venus_evcharger.dbus_gateway_core import float_or_zero

__all__ = (
    "budget_elapsed",
    "command_kind",
    "deadline_pair",
    "float_or_zero",
    "has_startup_registration",
    "is_local_publish_command",
    "lifecycle_payload",
    "local_publish_action_result",
    "priority_rank",
    "register_service_command",
    "should_follow_with_local_burst",
    "stale_coalesced_paths",
)

_PRIORITY_RANKS = {
    "safety": 0,
    "user": 1,
    "publish": 2,
    "read": 3,
    "normal": 4,
    "optional": 5,
    "discovery": 5,
    "diagnostic": 6,
}


def priority_rank(priority: object) -> int:
    normalized = str(priority).strip().lower()
    return _PRIORITY_RANKS.get(normalized, _PRIORITY_RANKS["diagnostic"])


def deadline_pair(command: CommandMapping) -> tuple[float, float]:
    return float_or_zero(command.get("deadline_s")), float_or_zero(command.get("created_at"))


def has_startup_registration(*, commands: CommandFileList) -> bool:
    return any(command_kind(command) in {"register_path", "register_service"} for _path, command in commands)


def is_local_publish_command(command: CommandMapping) -> bool:
    return command_kind(command) in {"publish_value", "publish_desired", "publish_fields"}


def should_follow_with_local_burst(command: CommandMapping, outcome: CommandOutcome) -> bool:
    return outcome in ("applied", "dropped") and is_local_publish_command(command)


def local_publish_action_result(processed: int, action: str) -> tuple[int, bool]:
    if action == "break":
        return processed, True
    return processed + 1 if action == "processed" else processed, False


def budget_elapsed(started: float, budget_seconds: float) -> bool:
    return time.monotonic() - started >= budget_seconds


def command_kind(command: CommandMapping) -> str:
    return str(command.get("kind") or command.get("type") or "")


def register_service_command(commands: CommandFileList) -> CommandFile | None:
    matches = [(path, command) for path, command in commands if command_kind(command) == "register_service"]
    return matches[-1] if matches else None


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

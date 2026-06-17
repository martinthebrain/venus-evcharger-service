# SPDX-License-Identifier: GPL-3.0-or-later
"""Small helpers for the DBus gateway write scheduler."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from venus_evcharger.dbus_adapter_components import CommandOutcome

_PRIORITY_RANKS = {
    "safety": 0,
    "user": 1,
    "publish": 2,
    "read": 3,
    "optional": 4,
    "discovery": 5,
    "diagnostic": 6,
}


def _priority_rank(priority: object) -> int:
    return _PRIORITY_RANKS.get(str(priority or "diagnostic").strip().lower(), _PRIORITY_RANKS["diagnostic"])


def _float_or_zero(value: object) -> float:
    try:
        return float(value) if value is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


def _deadline_pair(command: Mapping[str, Any]) -> tuple[float, float]:
    try:
        deadline = float(command.get("deadline_s", 0.0) or 0.0)
        created_at = float(command.get("created_at", 0.0) or 0.0)
    except (TypeError, ValueError):
        return 0.0, 0.0
    return deadline, created_at


def _has_startup_registration(*, commands: list[tuple[str, dict[str, Any]]]) -> bool:
    return any(_command_kind(command) in {"register_path", "register_service"} for _path, command in commands)


def _is_local_publish_command(command: Mapping[str, Any]) -> bool:
    return _command_kind(command) in {"publish_value", "publish_desired"}


def _should_follow_with_local_burst(command: Mapping[str, Any], outcome: CommandOutcome) -> bool:
    return outcome in ("applied", "dropped") and _is_local_publish_command(command)


def _local_publish_action_result(processed: int, action: str) -> tuple[int, bool]:
    if action == "break":
        return processed, True
    return processed + 1 if action == "processed" else processed, False


def _command_kind(command: Mapping[str, Any]) -> str:
    return str(command.get("kind") or command.get("type") or "")


def _register_service_command(
    commands: list[tuple[str, dict[str, Any]]],
) -> tuple[str, dict[str, Any]] | None:
    matches = [(path, command) for path, command in commands if _command_kind(command) == "register_service"]
    return matches[-1] if matches else None


def _stale_coalesced_paths(
    commands: list[tuple[str, dict[str, Any]]],
    *,
    processed_path: str,
    key: str,
) -> list[str]:
    return [
        path
        for path, command in commands
        if path != processed_path and str(command.get("coalesce_key") or "") == key
    ]


def _lifecycle_payload(command: Mapping[str, Any], state: str, queue_class: str, now: float) -> dict[str, Any]:
    return {
        "at": now,
        "state": state,
        "queue_class": queue_class,
        "kind": _command_kind(command),
        "id": command.get("id", ""),
        "coalesce_key": command.get("coalesce_key", ""),
    }

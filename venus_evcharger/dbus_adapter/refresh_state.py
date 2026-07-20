# SPDX-License-Identifier: GPL-3.0-or-later
"""Completion rules for coalesced DBus refresh requests."""

from __future__ import annotations

import math
from collections.abc import Mapping

from venus_evcharger.dbus_adapter.contracts import CommandOutcome
from venus_evcharger.dbus_gateway_command_types import CommandMapping

_TIMESTAMP_TYPES = (str, bytes, bytearray, int, float)


def command_request_at(command: CommandMapping) -> float:
    """Return the timestamp of the newest request represented by a command."""
    raw = command.get("updated_at")
    if raw is None:
        raw = command.get("created_at")
    return _timestamp(raw)


def cached_refresh_outcome(
    entry: Mapping[str, object] | None,
    command: CommandMapping,
) -> CommandOutcome | None:
    """Resolve a refresh already satisfied by a newer cache confirmation."""
    requested_at = command_request_at(command)
    if not entry or requested_at <= 0.0:
        return None
    return _cache_event_outcome(entry, requested_at)


def _cache_event_outcome(entry: Mapping[str, object], requested_at: float) -> CommandOutcome | None:
    status = str(entry.get("status"))
    if status == "fresh":
        return _confirmed_cache_outcome(entry, requested_at)
    return _unreadable_cache_outcome(entry, requested_at, status)


def _confirmed_cache_outcome(entry: Mapping[str, object], requested_at: float) -> CommandOutcome | None:
    return "applied" if _event_is_newer(entry.get("confirmed_at"), requested_at) else None


def _failed_cache_outcome(entry: Mapping[str, object], requested_at: float) -> CommandOutcome | None:
    return "dropped" if _event_is_newer(entry.get("error_at"), requested_at) else None


def _unreadable_cache_outcome(
    entry: Mapping[str, object],
    requested_at: float,
    status: str,
) -> CommandOutcome | None:
    if status not in {"error", "unavailable"}:
        return None
    return _failed_cache_outcome(entry, requested_at)


def services_refresh_satisfied(
    services: Mapping[str, Mapping[str, object]],
    command: CommandMapping,
) -> bool:
    """Report whether service discovery completed after the represented request."""
    requested_at = command_request_at(command)
    return requested_at > 0.0 and any(
        _timestamp(details.get("seen_at")) >= requested_at for details in services.values()
    )


def _timestamp(value: object) -> float:
    if not isinstance(value, _TIMESTAMP_TYPES):
        return 0.0
    try:
        timestamp = float(value)
    except ValueError:
        return 0.0
    if not math.isfinite(timestamp):
        return 0.0
    return max(0.0, timestamp)


def _event_is_newer(value: object, requested_at: float) -> bool:
    return _timestamp(value) >= requested_at

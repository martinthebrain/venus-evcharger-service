# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared wall-clock deadline contracts for transient IPC commands."""

from __future__ import annotations

import math

from venus_evcharger.ipc.command_mailbox import command_float
from venus_evcharger.ipc.command_types import CommandMapping

COMMAND_DEADLINE_FUTURE_SKEW_SECONDS = 5.0
TRANSIENT_PUBLICATION_DEADLINE_SECONDS = 30.0


def deadline_pair(command: CommandMapping) -> tuple[float, float]:
    """Return normalized numeric deadline and creation timestamps."""
    return command_float(command.get("deadline_s")), command_float(command.get("created_at"))


def command_deadline_expired(command: CommandMapping, now: float) -> bool:
    """Reject expired commands and invalid anchors for positive deadlines."""
    if "deadline_s" not in command:
        return False
    deadline, created_at = deadline_pair(command)
    if not math.isfinite(deadline):
        return True
    if deadline <= 0.0:
        return False
    if not valid_deadline_anchor(created_at, now):
        return True
    return now > created_at + deadline


def normalized_transient_deadline(value: object) -> float:
    """Clamp a transient deadline to its finite positive maximum."""
    deadline = command_float(value)
    if not math.isfinite(deadline) or deadline <= 0.0:
        return TRANSIENT_PUBLICATION_DEADLINE_SECONDS
    return min(deadline, TRANSIENT_PUBLICATION_DEADLINE_SECONDS)


def remaining_transient_ttl(command: CommandMapping, now: float) -> float:
    """Return remaining fast-lane lifetime, failing closed on bad anchors."""
    deadline = normalized_transient_deadline(command.get("deadline_s"))
    if "created_at" not in command:
        return 0.0 if "deadline_s" in command else deadline
    created_at = command_float(command.get("created_at"))
    if not valid_deadline_anchor(created_at, now):
        return 0.0
    return max(0.0, min(deadline, created_at + deadline - now))


def valid_deadline_anchor(created_at: float, now: float) -> bool:
    """Accept a positive finite timestamp with only a small future skew."""
    return (
        math.isfinite(created_at)
        and created_at > 0.0
        and created_at <= now + COMMAND_DEADLINE_FUTURE_SKEW_SECONDS
    )


__all__ = [
    "COMMAND_DEADLINE_FUTURE_SKEW_SECONDS",
    "TRANSIENT_PUBLICATION_DEADLINE_SECONDS",
    "command_deadline_expired",
    "deadline_pair",
    "normalized_transient_deadline",
    "remaining_transient_ttl",
    "valid_deadline_anchor",
]

# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared contracts at the DBus adapter boundary."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

CommandOutcome = Literal["applied", "dropped", "deferred"]
CommandCompletion = Callable[[CommandOutcome], None]


@dataclass(frozen=True, slots=True)
class CommandExecution:
    """Describe an immediate outcome or an accepted asynchronous operation."""

    outcome: CommandOutcome
    in_flight: bool = False

    @classmethod
    def immediate(cls, outcome: CommandOutcome) -> CommandExecution:
        return cls(outcome=outcome)

    @classmethod
    def pending(cls) -> CommandExecution:
        return cls(outcome="deferred", in_flight=True)


__all__ = ["CommandCompletion", "CommandExecution", "CommandOutcome"]

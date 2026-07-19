# SPDX-License-Identifier: GPL-3.0-or-later
"""Stable command and result models for Control API v1."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

ControlCommandName = Literal[
    "reset_contactor_lockout",
    "reset_phase_lockout",
    "set_auto_runtime_setting",
    "set_auto_start",
    "set_current_setting",
    "set_enable",
    "set_mode",
    "set_phase_selection",
    "set_start_stop",
    "trigger_software_update",
]
ControlCommandSource = Literal["dbus", "http", "internal", "mqtt"]
ControlCommandStatus = Literal["accepted_in_flight", "applied", "rejected"]


@dataclass(frozen=True, slots=True)
class ControlCommand:
    """One canonical control request independent of transport details."""

    name: ControlCommandName
    path: str
    value: Any
    source: ControlCommandSource = "dbus"
    detail: str = ""
    command_id: str = ""
    idempotency_key: str = ""


@dataclass(frozen=True, slots=True)
class ControlResult:
    """Structured outcome for one canonical control request."""

    command: ControlCommand
    status: ControlCommandStatus
    accepted: bool
    applied: bool
    persisted: bool
    reversible_failure: bool
    external_side_effect_started: bool
    detail: str = ""

    @classmethod
    def applied_result(
        cls,
        command: ControlCommand,
        *,
        detail: str = "",
        external_side_effect_started: bool = False,
    ) -> ControlResult:
        """Return one successful result after the command was persisted."""
        return cls(
            command=command,
            status="applied",
            accepted=True,
            applied=True,
            persisted=True,
            reversible_failure=False,
            external_side_effect_started=external_side_effect_started,
            detail=detail,
        )

    @classmethod
    def rejected_result(
        cls,
        command: ControlCommand,
        *,
        detail: str = "",
        reversible_failure: bool = True,
    ) -> ControlResult:
        """Return one rejected result after a reversible failure and rollback."""
        return cls(
            command=command,
            status="rejected",
            accepted=False,
            applied=False,
            persisted=False,
            reversible_failure=reversible_failure,
            external_side_effect_started=False,
            detail=detail,
        )

    @classmethod
    def accepted_in_flight_result(
        cls,
        command: ControlCommand,
        *,
        detail: str = "",
        external_side_effect_started: bool = True,
    ) -> ControlResult:
        """Return one accepted result after irreversible side effects started."""
        return cls(
            command=command,
            status="accepted_in_flight",
            accepted=True,
            applied=False,
            persisted=False,
            reversible_failure=False,
            external_side_effect_started=external_side_effect_started,
            detail=detail,
        )

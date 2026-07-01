# SPDX-License-Identifier: GPL-3.0-or-later
"""Typed software-update state contracts used by the runtime update role."""

from __future__ import annotations

from typing import Final, Literal

SoftwareUpdateState = Literal[
    "idle",
    "checking",
    "up-to-date",
    "available",
    "available-blocked",
    "running",
    "installed",
    "check-failed",
    "install-failed",
    "update-unavailable",
]
SoftwareUpdateLastResult = Literal["", "running", "success", "failed"]

UPDATE_STATE_IDLE: Final[SoftwareUpdateState] = "idle"
UPDATE_STATE_CHECKING: Final[SoftwareUpdateState] = "checking"
UPDATE_STATE_UP_TO_DATE: Final[SoftwareUpdateState] = "up-to-date"
UPDATE_STATE_AVAILABLE: Final[SoftwareUpdateState] = "available"
UPDATE_STATE_AVAILABLE_BLOCKED: Final[SoftwareUpdateState] = "available-blocked"
UPDATE_STATE_RUNNING: Final[SoftwareUpdateState] = "running"
UPDATE_STATE_INSTALLED: Final[SoftwareUpdateState] = "installed"
UPDATE_STATE_CHECK_FAILED: Final[SoftwareUpdateState] = "check-failed"
UPDATE_STATE_INSTALL_FAILED: Final[SoftwareUpdateState] = "install-failed"
UPDATE_STATE_UNAVAILABLE: Final[SoftwareUpdateState] = "update-unavailable"

UPDATE_RESULT_NONE: Final[SoftwareUpdateLastResult] = ""
UPDATE_RESULT_RUNNING: Final[SoftwareUpdateLastResult] = "running"
UPDATE_RESULT_SUCCESS: Final[SoftwareUpdateLastResult] = "success"
UPDATE_RESULT_FAILED: Final[SoftwareUpdateLastResult] = "failed"


__all__ = [
    "SoftwareUpdateLastResult",
    "SoftwareUpdateState",
    "UPDATE_RESULT_FAILED",
    "UPDATE_RESULT_NONE",
    "UPDATE_RESULT_RUNNING",
    "UPDATE_RESULT_SUCCESS",
    "UPDATE_STATE_AVAILABLE",
    "UPDATE_STATE_AVAILABLE_BLOCKED",
    "UPDATE_STATE_CHECK_FAILED",
    "UPDATE_STATE_CHECKING",
    "UPDATE_STATE_IDLE",
    "UPDATE_STATE_INSTALL_FAILED",
    "UPDATE_STATE_INSTALLED",
    "UPDATE_STATE_RUNNING",
    "UPDATE_STATE_UNAVAILABLE",
    "UPDATE_STATE_UP_TO_DATE",
]

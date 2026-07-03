# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared expected-error contracts for DBus-backed input reads."""

from __future__ import annotations

DBUS_INPUT_READ_ERRORS: tuple[type[Exception], ...] = (OSError, RuntimeError, ValueError)
DBUS_INPUT_RESOLUTION_ERRORS: tuple[type[Exception], ...] = (
    *DBUS_INPUT_READ_ERRORS,
)
DBUS_INPUT_SNAPSHOT_ERRORS: tuple[type[Exception], ...] = (
    *DBUS_INPUT_RESOLUTION_ERRORS,
    TypeError,
)

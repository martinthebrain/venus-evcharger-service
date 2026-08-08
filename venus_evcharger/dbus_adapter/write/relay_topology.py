# SPDX-License-Identifier: GPL-3.0-or-later
"""Map semantic GX relay operations to Venus relay topology."""

from __future__ import annotations

from venus_evcharger.core.shared import coerce_dbus_numeric

SYSTEM_SERVICE = "com.victronenergy.system"
SETTINGS_SERVICE = "com.victronenergy.settings"
MANUAL_FUNCTION_VALUE = 2


def relay_state_path(relay_index: int) -> str:
    """Return the system-service state path for a GX relay."""
    return f"/Relay/{relay_index}/State"


def manual_function_paths(relay_index: int) -> tuple[str, ...]:
    """Return supported settings paths for a GX relay function."""
    primary = f"/Settings/Relay/{relay_index}/Function"
    return (primary, "/Settings/Relay/Function") if relay_index == 0 else (primary,)


def binary_relay_state(value: object) -> int | None:
    """Normalize a DBus value to an exact binary relay state."""
    numeric = coerce_dbus_numeric(value)
    return int(numeric) if isinstance(numeric, (int, float)) and numeric in (0, 1) else None


def manual_function_selected(value: object) -> bool:
    """Return whether a DBus setting selects the manual relay function."""
    numeric = coerce_dbus_numeric(value)
    return bool(isinstance(numeric, (int, float)) and numeric == MANUAL_FUNCTION_VALUE)


def relay_state_matches(state: int | None, target_state: int) -> bool:
    """Return whether a readable relay state equals its binary target."""
    return state is not None and state == target_state

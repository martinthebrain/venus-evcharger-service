# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared DBus gateway helpers for auto-input source resolution."""

from __future__ import annotations

from venus_evcharger.inputs.dbus_errors import DBUS_INPUT_READ_ERRORS

_EXPECTED_MISSING_DBUS_ERROR_NAMES = frozenset(
    (
        "org.freedesktop.DBus.Error.NameHasNoOwner",
        "org.freedesktop.DBus.Error.ServiceUnknown",
        "org.freedesktop.DBus.Error.UnknownInterface",
        "org.freedesktop.DBus.Error.UnknownMethod",
        "org.freedesktop.DBus.Error.UnknownObject",
    )
)

_EXPECTED_MISSING_DBUS_ERROR_TEXT = (
    "NameHasNoOwner",
    "ServiceUnknown",
    "UnknownInterface",
    "UnknownMethod",
    "UnknownObject",
    "was not provided by any .service files",
)
DBUS_SOURCE_READ_ERRORS = DBUS_INPUT_READ_ERRORS
DBUS_ERROR_NAME_ACCESS_ERRORS = (OSError, RuntimeError, TypeError, ValueError)


class _ResolvedAutoBatteryServiceState:
    """Shared state contract for DBus source-resolution roles."""

    _resolved_auto_battery_service: str | None


def _dbus_error_name(error: BaseException) -> str:
    getter = getattr(error, "get_dbus_name", None)
    if callable(getter):
        try:
            return str(getter() or "")
        except DBUS_ERROR_NAME_ACCESS_ERRORS:  # pragma: no cover - defensive for foreign DBus objects
            return ""
    attribute_name = getattr(error, "_dbus_error_name", None)
    return "" if attribute_name is None else str(attribute_name)


def _is_expected_missing_dbus_error(error: BaseException) -> bool:
    """Return whether a DBus error means absent data, not a broken connection."""
    error_name = _dbus_error_name(error)
    if error_name in _EXPECTED_MISSING_DBUS_ERROR_NAMES:
        return True
    error_text = str(error)
    return any(marker in error_text for marker in _EXPECTED_MISSING_DBUS_ERROR_TEXT)

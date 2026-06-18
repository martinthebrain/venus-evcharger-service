# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared DBus gateway helpers for auto-input source resolution."""

from __future__ import annotations

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


class _ResolvedAutoBatteryServiceState:
    """Shared state contract for DBus source-resolution mixins."""

    _resolved_auto_battery_service: str | None


def _dbus_error_name(error: BaseException) -> str:
    getter = getattr(error, "get_dbus_name", None)
    if callable(getter):
        try:
            return str(getter() or "")
        except Exception:  # pragma: no cover - defensive for foreign DBus objects
            return ""
    return str(getattr(error, "_dbus_error_name", "") or "")


def _is_expected_missing_dbus_error(error: BaseException) -> bool:
    """Return whether a DBus error means absent data, not a broken connection."""
    error_name = _dbus_error_name(error)
    if error_name in _EXPECTED_MISSING_DBUS_ERROR_NAMES:
        return True
    error_text = str(error)
    return any(marker in error_text for marker in _EXPECTED_MISSING_DBUS_ERROR_TEXT)

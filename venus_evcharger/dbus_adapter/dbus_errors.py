# SPDX-License-Identifier: GPL-3.0-or-later
"""Classification and bounded diagnostics for gateway DBus failures."""

from __future__ import annotations

import dbus

DBUS_EXCEPTION_ATTRIBUTE = "DBusException"


def _dbus_exception_type() -> type[BaseException]:
    if not hasattr(dbus, DBUS_EXCEPTION_ATTRIBUTE):
        return RuntimeError
    candidate = getattr(dbus, DBUS_EXCEPTION_ATTRIBUTE)
    if isinstance(candidate, type) and issubclass(candidate, BaseException):
        return candidate
    return RuntimeError


DBUS_GATEWAY_OPERATION_ERRORS: tuple[type[BaseException], ...] = (
    _dbus_exception_type(),
    OSError,
    RuntimeError,
    TypeError,
    ValueError,
)


def dbus_error_is_timeout(error: BaseException) -> bool:
    """Return whether a DBus failure represents a transient missing reply."""
    if isinstance(error, TimeoutError):
        return True
    detail = str(error).lower()
    name = _dbus_error_name(error)
    reply_markers = ("timeout", "timed out", "noreply", "no_reply")
    return any(marker in detail for marker in reply_markers) or "noreply" in name


def dbus_error_code(error: BaseException) -> str:
    """Return a bounded transport-neutral code for one DBus failure."""
    name = _dbus_error_name(error)
    if name:
        return _bounded_text(name)
    if isinstance(error, TimeoutError):
        return "timeout"
    type_name = type(error).__name__.strip().lower().replace("_", "-")
    return _bounded_text(type_name or "dbus-error")


def _dbus_error_name(error: BaseException) -> str:
    getter = getattr(error, "get_dbus_name", None)
    if not callable(getter):
        return ""
    try:
        return str(getter()).lower()
    except DBUS_GATEWAY_OPERATION_ERRORS:
        return ""


def _bounded_text(value: object, *, maximum: int = 256) -> str:
    return str(value).strip()[:maximum]


__all__ = [
    "DBUS_GATEWAY_OPERATION_ERRORS",
    "dbus_error_code",
    "dbus_error_is_timeout",
]

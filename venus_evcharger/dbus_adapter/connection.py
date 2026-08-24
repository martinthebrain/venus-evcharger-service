# SPDX-License-Identifier: GPL-3.0-or-later
"""Private DBus connection ownership for the gateway adapter."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import suppress

import dbus

from venus_evcharger.dbus_adapter.async_request import DbusWireRequest


class DbusConnectionManager:
    """Own and recover the gateway's private system-bus connection."""

    def __init__(self) -> None:
        self._bus: object | None = None

    def bus(self) -> object:
        if self._bus is None:
            self._bus = dbus.SystemBus(private=True)
        return self._bus

    def connect(self) -> None:
        """Establish the private bus connection before the GLib loop starts."""
        self.bus()

    def send_async(
        self,
        request: DbusWireRequest,
        reply_handler: Callable[..., None],
        error_handler: Callable[[object], None],
    ) -> object:
        """Return the real dbus-python PendingCall for one bounded request."""
        raw_call_async = getattr(self.bus(), "call_async", None)
        if not callable(raw_call_async):
            raise TypeError("DBus bus does not provide call_async")
        call_async: Callable[..., object] = raw_call_async
        return call_async(
            request.service,
            request.path,
            request.interface,
            request.method_name,
            request.signature,
            request.args,
            reply_handler,
            error_handler,
            timeout=request.timeout_seconds,
            require_main_loop=True,
        )

    def reset(self) -> None:
        """Close the current private connection best-effort and forget it."""
        close = getattr(self._bus, "close", None)
        if callable(close):
            with suppress(Exception):
                close()
        self._bus = None


__all__ = ["DbusConnectionManager"]

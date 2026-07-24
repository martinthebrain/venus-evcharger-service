#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Runtime lifecycle for the dedicated DBus adapter."""

from __future__ import annotations

import signal

from gi.repository import GLib

from venus_evcharger.dbus_adapter.process.protocols.runtime import DbusAdapterRuntimeContext


class DbusAdapterRuntime:
    def __init__(self, context: DbusAdapterRuntimeContext) -> None:
        self._context = context

    def install_signal_handlers(self) -> None:
        def _stop(_signum: int, _frame: object) -> None:
            self._context._stop = True
            if self._context._main_loop is not None:
                GLib.idle_add(self._context._main_loop.quit)

        signal.signal(signal.SIGTERM, _stop)
        signal.signal(signal.SIGINT, _stop)

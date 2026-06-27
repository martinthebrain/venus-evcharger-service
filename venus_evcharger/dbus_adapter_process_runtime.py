#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Process runtime lifecycle for the dedicated DBus adapter."""

from __future__ import annotations

import signal

from gi.repository import GLib

from venus_evcharger.dbus_adapter_process_protocol_runtime import DbusAdapterRuntimeContext


class DbusAdapterRuntimeMixin:
    def install_signal_handlers(self: DbusAdapterRuntimeContext) -> None:
        def _stop(_signum: int, _frame: object) -> None:
            self._stop = True
            if self._main_loop is not None:
                GLib.idle_add(self._main_loop.quit)

        signal.signal(signal.SIGTERM, _stop)
        signal.signal(signal.SIGINT, _stop)

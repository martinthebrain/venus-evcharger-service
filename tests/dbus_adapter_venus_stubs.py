# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared host-side substitutes for Venus-only adapter dependencies."""

from __future__ import annotations

import sys
from types import ModuleType
from unittest.mock import MagicMock


class FakeVeDbusService(dict[str, object]):
    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__()
        self.args = args
        self.kwargs = kwargs
        self.registered = False
        self.added_paths: dict[str, dict[str, object]] = {}

    def register(self) -> None:
        self.registered = True

    def add_path(self, path: str, value: object, **kwargs: object) -> None:
        self.added_paths[path] = {"value": value, **kwargs}
        self[path] = value


def install_venus_adapter_stubs() -> None:
    vedbus = ModuleType("vedbus")
    setattr(vedbus, "VeDbusService", FakeVeDbusService)
    sys.modules["vedbus"] = vedbus

    dbus_mainloop = ModuleType("dbus.mainloop.glib")
    setattr(dbus_mainloop, "DBusGMainLoop", MagicMock())
    sys.modules["dbus.mainloop.glib"] = dbus_mainloop

    loaded_identity = sys.modules.get("venus_evcharger.dbus_adapter.process.identity")
    if loaded_identity is not None:
        setattr(loaded_identity, "VeDbusService", FakeVeDbusService)

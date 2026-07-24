# SPDX-License-Identifier: GPL-3.0-or-later
"""Concrete service and path targets for adapter reads."""

from __future__ import annotations

from dataclasses import dataclass

from venus_evcharger.dbus_gateway import dbus_path_key

DbusPathKey = str


@dataclass(frozen=True)
class ReadTarget:
    service: str
    path: str

    @property
    def source(self) -> str:
        return f"{self.service}{self.path}"

    @property
    def cache_key(self) -> DbusPathKey:
        return dbus_path_key(self.service, self.path)


def read_target(service: object, path: object) -> ReadTarget | None:
    if not service or not path:
        return None
    service_name = str(service).strip()
    dbus_path = str(path).strip()
    if not service_name or not dbus_path.startswith("/"):
        return None
    return ReadTarget(service_name, dbus_path)

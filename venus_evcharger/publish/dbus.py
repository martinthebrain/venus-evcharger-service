# SPDX-License-Identifier: GPL-3.0-or-later
"""Helpers for throttled DBus publishing in the Venus EV charger service."""

from __future__ import annotations

from typing import Any, Callable

from venus_evcharger.publish.dbus_diagnostics import _DbusPublishDiagnostics


class DbusPublishController(_DbusPublishDiagnostics):
    """Publish Venus EV charger DBus paths with simple change and interval throttling."""

    PHASE_NAMES: tuple[str, str, str] = ("L1", "L2", "L3")

    def __init__(self, service: Any, age_seconds_func: Callable[[Any, float], float]) -> None:
        self.service: Any = service
        self._age_seconds = age_seconds_func

# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared DBus input-reading helpers for the Venus EV charger service."""

from __future__ import annotations

import time
from typing import Any

from venus_evcharger.inputs.pv import _DbusInputPvMixin
from venus_evcharger.inputs.storage import _DbusInputStorageMixin


class DbusInputController(_DbusInputPvMixin, _DbusInputStorageMixin):
    """Encapsulate PV, battery, and grid DBus discovery/reads for the main service."""

    def __init__(self, port: Any) -> None:
        self.port = port
        self.service = port
        if hasattr(port, "bind_controller"):
            port.bind_controller(self)

# SPDX-License-Identifier: GPL-3.0-or-later
"""Gateway-backed input helpers for the Venus EV charger service."""

from __future__ import annotations

import time
from typing import Any

from venus_evcharger.inputs.storage import _DbusInputStorage


class DbusInputController(_DbusInputStorage):
    """Expose semantic PV, battery, and grid inputs from the DBus gateway."""

    def __init__(self, port: Any) -> None:
        self.port = port
        self.service = port
        if hasattr(port, "bind_controller"):
            port.bind_controller(self)

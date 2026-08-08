# SPDX-License-Identifier: GPL-3.0-or-later
"""Validated transport-neutral requests for asynchronous DBus calls."""

from __future__ import annotations

import math
from dataclasses import dataclass


def _require_complete_target(*parts: str) -> None:
    if all(parts):
        return
    raise ValueError("DBus method target must be complete")


def _require_finite_positive_timeout(timeout_seconds: float) -> None:
    if math.isfinite(timeout_seconds) and timeout_seconds > 0.0:
        return
    raise ValueError("DBus method timeout must be finite and positive")


@dataclass(frozen=True, slots=True)
class DbusWireRequest:
    """Represent one validated DBus request independent of dbus-python."""

    service: str
    path: str
    interface: str
    method_name: str
    signature: str
    timeout_seconds: float
    args: tuple[object, ...] = ()

    def __post_init__(self) -> None:
        """Reject incomplete targets and unsafe transport deadlines."""
        _require_complete_target(
            self.service,
            self.path,
            self.interface,
            self.method_name,
        )
        _require_finite_positive_timeout(self.timeout_seconds)

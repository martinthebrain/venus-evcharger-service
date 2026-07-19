# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared immutable dependencies for composed Auto decision components."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from venus_evcharger.ports.auto import AutoDecisionPort


@dataclass(frozen=True, slots=True)
class AutoDecisionContext:
    """Runtime dependencies that are common to all Auto components."""

    port: AutoDecisionPort
    health_code: Callable[[str], int]
    mode_uses_auto_logic: Callable[[Any], bool]

    @property
    def service(self) -> Any:
        return self.port.service

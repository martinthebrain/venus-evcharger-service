# SPDX-License-Identifier: GPL-3.0-or-later
"""Protocol definitions for meter, switch, and charger backends."""

from __future__ import annotations

from typing import Any, Protocol, TypeGuard

from .models import (
    ChargerState,
    MeterReading,
    PhaseSelection,
    SwitchCapabilities,
    SwitchState,
)


class MeterBackend(Protocol):
    """Read normalized wallbox power and energy values."""

    def read_meter(self) -> MeterReading: ...  # pragma: no cover


class SwitchBackend(Protocol):
    """Read and command normalized switching state."""

    def capabilities(self) -> SwitchCapabilities: ...  # pragma: no cover

    def read_switch_state(self) -> SwitchState: ...  # pragma: no cover

    def set_enabled(self, enabled: bool) -> None: ...  # pragma: no cover

    def set_phase_selection(self, selection: PhaseSelection) -> None: ...  # pragma: no cover


class ChargerBackend(Protocol):
    """Optional direct charger-control backend."""

    def read_charger_state(self) -> ChargerState: ...  # pragma: no cover

    def set_enabled(self, enabled: bool) -> None: ...  # pragma: no cover

    def set_current(self, amps: float) -> None: ...  # pragma: no cover

    def set_phase_selection(self, selection: PhaseSelection) -> None: ...  # pragma: no cover


class BackendConstructor(Protocol):
    """Constructor signature used by the backend registry/factory."""

    def __call__(self, service: Any, config_path: str) -> Any: ...  # pragma: no cover


def is_switch_backend(value: object) -> TypeGuard[SwitchBackend]:
    """Return whether one object satisfies the switch-backend runtime surface."""
    return (
        callable(getattr(value, "capabilities", None))
        and callable(getattr(value, "read_switch_state", None))
        and callable(getattr(value, "set_enabled", None))
        and callable(getattr(value, "set_phase_selection", None))
    )

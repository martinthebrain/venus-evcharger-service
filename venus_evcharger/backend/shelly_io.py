# SPDX-License-Identifier: GPL-3.0-or-later
"""Shelly HTTP and relay-worker helpers for the Venus EV charger service."""

from __future__ import annotations

from venus_evcharger.backend.shelly_io_capabilities import ShellyIoCapabilities
from venus_evcharger.backend.shelly_io_requests import ShellyIoRequests
from venus_evcharger.backend.shelly_io_runtime import ShellyIoRuntime
from venus_evcharger.backend.shelly_io_split import ShellyIoSplit
from venus_evcharger.backend.shelly_io_types import (
    JsonObject,
    PendingRelayCommand,
    ShellyEnergyData,
    ShellyIoHost,
    ShellyPmStatus,
    ShellyRpcScalar,
    _phase_currents_for_selection,
    _phase_powers_for_selection,
    _single_phase_vector,
)
from venus_evcharger.backend.shelly_io_worker import ShellyIoWorker


class ShellyIoController(
    ShellyIoRequests,
    ShellyIoCapabilities,
    ShellyIoRuntime,
    ShellyIoSplit,
    ShellyIoWorker,
):
    """Encapsulate Shelly HTTP access and relay queue/worker behavior."""

    def __init__(self, service: ShellyIoHost) -> None:
        self.service = service

    def _runtime_now(self) -> float:
        """Return one best-effort current timestamp for runtime helpers and tests."""
        time_now = getattr(self.service, "_time_now", None)
        if callable(time_now):
            value = time_now()
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                return float(value)
        return 0.0


__all__ = [
    "JsonObject",
    "PendingRelayCommand",
    "ShellyEnergyData",
    "ShellyIoController",
    "ShellyIoHost",
    "ShellyPmStatus",
    "ShellyRpcScalar",
    "_phase_currents_for_selection",
    "_phase_powers_for_selection",
    "_single_phase_vector",
]

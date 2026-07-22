# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared public types and wire constants for semantic energy IPC."""

from __future__ import annotations

from typing import Literal

ENERGY_IPC_SCHEMA_VERSION = 1
ENERGY_REFRESH_COMMAND_KIND = "refresh_energy_inputs"

EnergyValueStatus = Literal["fresh", "stale", "unavailable", "error", "unknown"]
EnergySourceKind = Literal["grid", "pv_ac", "pv_dc", "battery"]
EnergySourceState = Literal["online", "offline", "unknown"]
EnergyRefreshScope = Literal["all", "grid", "pv", "battery", "topology", "energy_source"]
EnergyRefreshUrgency = Literal["normal", "priority"]

__all__ = [
    "ENERGY_IPC_SCHEMA_VERSION",
    "ENERGY_REFRESH_COMMAND_KIND",
    "EnergyRefreshScope",
    "EnergyRefreshUrgency",
    "EnergySourceKind",
    "EnergySourceState",
    "EnergyValueStatus",
]

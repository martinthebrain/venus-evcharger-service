# SPDX-License-Identifier: GPL-3.0-or-later
"""Typed payloads shared by wizard inventory editor and CLI flows."""

from __future__ import annotations

from typing import TypedDict

from venus_evcharger.inventory import PhaseLabel, SwitchingMode


class InventoryCapabilityChoice(TypedDict):
    """One eligible concrete device capability for guided binding assignment."""

    device_id: str
    device_label: str
    profile_id: str
    profile_label: str
    capability_id: str
    adapter_type: str
    supported_phases: tuple[PhaseLabel, ...]


class GuidedCapabilityFlags(TypedDict):
    """Prompted capability booleans for one guided profile capability."""

    measures_power: bool
    measures_energy: bool
    switching_mode: SwitchingMode | None
    supports_feedback: bool
    supports_phase_selection: bool

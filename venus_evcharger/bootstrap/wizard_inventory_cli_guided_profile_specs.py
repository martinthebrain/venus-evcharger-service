# SPDX-License-Identifier: GPL-3.0-or-later
"""Guided profile field defaults and capability rules."""

from __future__ import annotations

import argparse

from venus_evcharger.bootstrap.wizard_inventory_prompts import (
    inventory_bool_field,
    inventory_choice_field,
)
from venus_evcharger.bootstrap.wizard_inventory_support import (
    parse_inventory_kind,
    parse_inventory_switching_mode,
)
from venus_evcharger.bootstrap.wizard_inventory_types import GuidedCapabilityFlags
from venus_evcharger.inventory import BindingRole, CapabilityKind, PhaseLabel


def guided_profile_kind(namespace: argparse.Namespace) -> CapabilityKind:
    return parse_inventory_kind(
        inventory_choice_field(
            namespace,
            "inventory_kind",
            "Choose the capability kind:",
            ("switch", "meter", "charger"),
            "switch",
        )
    )


def guided_capability_defaults(kind: CapabilityKind) -> tuple[str, str]:
    capability_default = "meter" if kind == "meter" else "switch" if kind == "switch" else "charger"
    adapter_default = (
        "template_switch"
        if kind == "switch"
        else "template_meter"
        if kind == "meter"
        else "template_charger"
    )
    return capability_default, adapter_default


def guided_capability_flags(
    namespace: argparse.Namespace,
    kind: str,
    supported_phases: tuple[PhaseLabel, ...],
) -> GuidedCapabilityFlags:
    flags: GuidedCapabilityFlags = {
        "measures_power": False,
        "measures_energy": False,
        "switching_mode": None,
        "supports_feedback": False,
        "supports_phase_selection": False,
    }
    if kind == "meter":
        flags["measures_power"] = inventory_bool_field(namespace, "inventory_measures_power", "Measures power", True)
        flags["measures_energy"] = inventory_bool_field(namespace, "inventory_measures_energy", "Measures energy", True)
        return flags
    if kind == "switch":
        flags["switching_mode"] = parse_inventory_switching_mode(
            inventory_choice_field(
                namespace,
                "inventory_switching_mode",
                "Choose the switching mode:",
                ("contactor", "direct"),
                "contactor",
            )
        )
        flags["supports_feedback"] = inventory_bool_field(namespace, "inventory_supports_feedback", "Supports feedback", True)
        flags["supports_phase_selection"] = inventory_bool_field(
            namespace,
            "inventory_supports_phase_selection",
            "Supports phase selection",
            len(supported_phases) > 1,
        )
    return flags


def guided_role_for_kind(kind: str) -> BindingRole:
    return "measurement" if kind == "meter" else "actuation" if kind == "switch" else "charger"

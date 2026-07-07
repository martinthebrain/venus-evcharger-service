# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared guided inventory editor helpers."""

from __future__ import annotations

from venus_evcharger.bootstrap.wizard_inventory_types import InventoryCapabilityChoice
from venus_evcharger.inventory import BindingRole, DeviceInventory, RoleBinding


_ROLE_LABEL_DEFAULTS: dict[BindingRole, str] = {
    "actuation": "Actuation",
    "measurement": "Measurement",
    "charger": "Charger",
}


def default_binding_id(inventory: DeviceInventory, profile_id: str, role: BindingRole) -> str:
    binding_id: str = role
    if any(binding.id == binding_id for binding in inventory.bindings):
        binding_id = f"{profile_id}_{role}"
    return binding_id


def binding_label_default(existing_binding: RoleBinding | None, role: BindingRole) -> str:
    if existing_binding is None:
        return _ROLE_LABEL_DEFAULTS[role]
    return existing_binding.label


def binding_scope_default(existing_binding: RoleBinding | None) -> str:
    if existing_binding is None:
        return "L1"
    return ",".join(existing_binding.phase_scope)


def print_capability_choices(choices: tuple[InventoryCapabilityChoice, ...]) -> None:
    print("Eligible device capabilities:")
    for index, item in enumerate(choices, start=1):
        print(
            "  "
            + f"{index}. {item['device_id']} ({item['device_label']}) -> "
            + f"{item['capability_id']}/{item['adapter_type']} "
            + f"[{','.join(item['supported_phases'])}]"
        )

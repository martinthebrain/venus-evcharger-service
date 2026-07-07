# SPDX-License-Identifier: GPL-3.0-or-later
"""Guided role binding edits for the inventory editor CLI."""

from __future__ import annotations

import argparse
from pathlib import Path

from venus_evcharger.bootstrap.wizard_inventory_cli_guided_common import (
    binding_label_default,
    binding_scope_default as default_binding_scope_text,
    default_binding_id,
    print_capability_choices,
)
from venus_evcharger.bootstrap.wizard_inventory_cli_payload import save_and_payload
from venus_evcharger.bootstrap.wizard_inventory_editor import (
    inventory_role_capability_choices,
    remove_inventory_binding,
    set_inventory_binding_member,
)
from venus_evcharger.bootstrap.wizard_inventory_prompts import (
    inventory_bool_field,
    inventory_choice_field,
    inventory_field_with_default,
)
from venus_evcharger.bootstrap.wizard_inventory_support import (
    parse_inventory_binding_role,
    parse_inventory_phases,
)
from venus_evcharger.bootstrap.wizard_inventory_types import InventoryCapabilityChoice
from venus_evcharger.inventory import BindingRole, DeviceInventory, PhaseLabel, RoleBinding


def prompt_binding_choice(
    namespace: argparse.Namespace,
    inventory: DeviceInventory,
    role: BindingRole,
) -> tuple[str, RoleBinding | None, str, tuple[PhaseLabel, ...]]:
    binding_default = default_binding_id(inventory, role, role)
    existing_ids = {binding.id for binding in inventory.bindings}
    if binding_default in existing_ids:
        binding_default = f"{role}_group"
    binding_id = inventory_field_with_default(namespace, "inventory_binding_id", "Binding id", binding_default)
    existing_binding = next((binding for binding in inventory.bindings if binding.id == binding_id), None)
    label_default = binding_label_default(existing_binding, role)
    binding_label = inventory_field_with_default(namespace, "inventory_binding_label", "Binding label", label_default)
    scope_default = default_binding_scope_text(existing_binding)
    binding_scope = parse_inventory_phases(
        inventory_field_with_default(
            namespace,
            "inventory_binding_phase_scope",
            "Binding phase scope",
            scope_default,
        )
    )
    return binding_id, existing_binding, binding_label, binding_scope


def maybe_replace_binding(
    namespace: argparse.Namespace,
    inventory: DeviceInventory,
    existing_binding: RoleBinding | None,
    binding_id: str,
) -> tuple[DeviceInventory, RoleBinding | None]:
    if existing_binding is None:
        return inventory, None
    if not inventory_bool_field(namespace, "_inventory_replace_binding", "Replace existing binding members", False):
        return inventory, existing_binding
    return remove_inventory_binding(inventory, binding_id=binding_id), None


def selected_binding_choice(choices: tuple[InventoryCapabilityChoice, ...]) -> InventoryCapabilityChoice:
    print_capability_choices(choices)
    raw_member = input("Select member [1]: ").strip()
    member_index = int(raw_member or "1")
    if member_index < 1 or member_index > len(choices):
        raise ValueError("Selected binding member is out of range")
    return choices[member_index - 1]


def binding_member_phases(
    namespace: argparse.Namespace,
    selected: InventoryCapabilityChoice,
    binding_scope: tuple[PhaseLabel, ...],
) -> tuple[PhaseLabel, ...]:
    suggested_phases = tuple(phase for phase in binding_scope if phase in selected["supported_phases"]) or tuple(selected["supported_phases"])
    return parse_inventory_phases(
        inventory_field_with_default(
            namespace,
            "inventory_member_phases",
            f"Phases for {selected['device_id']}",
            ",".join(suggested_phases),
        )
    )


def set_guided_binding_member(
    inventory: DeviceInventory,
    *,
    binding_id: str,
    binding_label: str,
    role: BindingRole,
    existing_binding: RoleBinding | None,
    first_member: bool,
    selected: InventoryCapabilityChoice,
    member_phases: tuple[PhaseLabel, ...],
) -> DeviceInventory:
    return set_inventory_binding_member(
        inventory,
        binding_id=binding_id,
        device_id=selected["device_id"],
        capability_id=selected["capability_id"],
        member_phases=member_phases,
        role=parse_inventory_binding_role(role),
        label=binding_label,
        phase_scope=member_phases if first_member and existing_binding is None else None,
    )


def validated_guided_binding(
    updated: DeviceInventory,
    binding_id: str,
    binding_scope: tuple[PhaseLabel, ...],
) -> None:
    final_binding = next((binding for binding in updated.bindings if binding.id == binding_id), None)
    if final_binding is None:
        raise ValueError(f"Binding {binding_id} was not created")
    if tuple(final_binding.phase_scope) != tuple(binding_scope):
        left = ",".join(final_binding.phase_scope)
        right = ",".join(binding_scope)
        raise ValueError(
            "Binding member phases do not match the requested binding phase scope "
            f"({left} != {right})"
        )


def extend_guided_binding(
    namespace: argparse.Namespace,
    updated: DeviceInventory,
    *,
    role: BindingRole,
    binding_id: str,
    binding_label: str,
    binding_scope: tuple[PhaseLabel, ...],
    existing_binding: RoleBinding | None,
    choices: tuple[InventoryCapabilityChoice, ...],
) -> DeviceInventory:
    first_member = True
    while True:
        if not first_member and not inventory_bool_field(namespace, "_inventory_add_binding_member", "Add another binding member", False):
            break
        selected = selected_binding_choice(choices)
        member_phases = binding_member_phases(namespace, selected, binding_scope)
        updated = set_guided_binding_member(
            updated,
            binding_id=binding_id,
            binding_label=binding_label,
            role=role,
            existing_binding=existing_binding,
            first_member=first_member,
            selected=selected,
            member_phases=member_phases,
        )
        first_member = False
    return updated


def guided_inventory_edit_binding(
    namespace: argparse.Namespace,
    inventory_path: Path,
    inventory: DeviceInventory,
) -> dict[str, object]:
    if getattr(namespace, "non_interactive", False):
        raise ValueError("guided-edit-binding requires interactive input")
    role = parse_inventory_binding_role(
        inventory_choice_field(
            namespace,
            "inventory_binding_role",
            "Choose the binding role:",
            ("actuation", "measurement", "charger"),
            "measurement",
        )
    )
    choices = inventory_role_capability_choices(inventory, role=role)
    if not choices:
        raise ValueError(f"No eligible devices with role capability '{role}' are available")
    binding_id, existing_binding, binding_label, binding_scope = prompt_binding_choice(namespace, inventory, role)
    updated, existing_binding = maybe_replace_binding(namespace, inventory, existing_binding, binding_id)
    updated = extend_guided_binding(
        namespace,
        updated,
        role=role,
        binding_id=binding_id,
        binding_label=binding_label,
        binding_scope=binding_scope,
        existing_binding=existing_binding,
        choices=choices,
    )
    validated_guided_binding(updated, binding_id, binding_scope)
    return save_and_payload(
        "guided-edit-binding",
        inventory_path,
        updated,
        binding_id=binding_id,
    )

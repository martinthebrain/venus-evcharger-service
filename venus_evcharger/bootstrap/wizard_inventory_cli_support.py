# SPDX-License-Identifier: GPL-3.0-or-later
"""Support helpers for inventory editor command handling."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import cast

from venus_evcharger.bootstrap.wizard_inventory_editor import (
    add_inventory_device,
    add_inventory_profile,
    inventory_role_capability_choices,
    remove_inventory_binding,
    set_inventory_binding_member,
)
from venus_evcharger.bootstrap.wizard_inventory_prompts import (
    inventory_bool_field,
    inventory_choice_field,
    inventory_field,
    inventory_field_with_default,
    inventory_optional_field,
)
from venus_evcharger.bootstrap.wizard_inventory_support import (
    inventory_summary_payload,
    parse_inventory_binding_role,
    parse_inventory_kind,
    parse_inventory_phases,
    parse_inventory_switching_mode,
    save_inventory,
)
from venus_evcharger.inventory import BindingRole, CapabilityKind, DeviceInventory, PhaseLabel, RoleBinding, SwitchingMode


def _guided_profile_kind(namespace: argparse.Namespace) -> CapabilityKind:
    return parse_inventory_kind(inventory_choice_field(
        namespace,
        "inventory_kind",
        "Choose the capability kind:",
        ("switch", "meter", "charger"),
        "switch",
    ))


def _guided_capability_defaults(kind: CapabilityKind) -> tuple[str, str]:
    capability_default = "meter" if kind == "meter" else "switch" if kind == "switch" else "charger"
    adapter_default = (
        "template_switch"
        if kind == "switch"
        else "template_meter"
        if kind == "meter"
        else "template_charger"
    )
    return capability_default, adapter_default


def _guided_capability_flags(
    namespace: argparse.Namespace,
    kind: str,
    supported_phases: tuple[str, ...],
) -> dict[str, object]:
    flags: dict[str, object] = {
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


def _guided_role_for_kind(kind: str) -> BindingRole:
    return "measurement" if kind == "meter" else "actuation" if kind == "switch" else "charger"


def _default_binding_id(inventory: DeviceInventory, profile_id: str, role: BindingRole) -> str:
    binding_id: str = role
    if any(binding.id == binding_id for binding in inventory.bindings):
        binding_id = f"{profile_id}_{role}"
    return binding_id


def _binding_label_default(existing_binding: RoleBinding | None, role: BindingRole) -> str:
    if existing_binding is None:
        return role.replace("_", " ").title()
    return existing_binding.label


def _binding_scope_default(existing_binding: RoleBinding | None) -> str:
    if existing_binding is None:
        return "L1"
    return ",".join(existing_binding.phase_scope)


def _prompt_binding_choice(
    namespace: argparse.Namespace,
    inventory: DeviceInventory,
    role: BindingRole,
) -> tuple[str, RoleBinding | None, str, tuple[PhaseLabel, ...]]:
    binding_default = _default_binding_id(inventory, role, role)
    existing_ids = {binding.id for binding in inventory.bindings}
    if binding_default in existing_ids:
        binding_default = f"{role}_group"
    binding_id = inventory_field_with_default(namespace, "inventory_binding_id", "Binding id", binding_default)
    existing_binding = next((binding for binding in inventory.bindings if binding.id == binding_id), None)
    label_default = _binding_label_default(existing_binding, role)
    binding_label = inventory_field_with_default(namespace, "inventory_binding_label", "Binding label", label_default)
    binding_scope_default = _binding_scope_default(existing_binding)
    binding_scope = parse_inventory_phases(
        inventory_field_with_default(
            namespace,
            "inventory_binding_phase_scope",
            "Binding phase scope",
            binding_scope_default,
        )
    )
    return binding_id, existing_binding, binding_label, binding_scope


def _maybe_replace_binding(
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


def _print_capability_choices(choices: tuple[dict[str, object], ...]) -> None:
    print("Eligible device capabilities:")
    for index, item in enumerate(choices, start=1):
        phases = cast(tuple[str, ...], item["supported_phases"])
        print(
            "  "
            + f"{index}. {item['device_id']} ({item['device_label']}) -> "
            + f"{item['capability_id']}/{item['adapter_type']} "
            + f"[{','.join(phases)}]"
        )


def _selected_binding_choice(choices: tuple[dict[str, object], ...]) -> dict[str, object]:
    _print_capability_choices(choices)
    raw_member = input("Select member [1]: ").strip()
    member_index = int(raw_member or "1")
    if member_index < 1 or member_index > len(choices):
        raise ValueError("Selected binding member is out of range")
    return choices[member_index - 1]


def _binding_member_phases(
    namespace: argparse.Namespace,
    selected: dict[str, object],
    binding_scope: tuple[PhaseLabel, ...],
) -> tuple[PhaseLabel, ...]:
    supported = cast(tuple[PhaseLabel, ...], selected["supported_phases"])
    suggested_phases = tuple(phase for phase in binding_scope if phase in supported) or tuple(supported)
    return parse_inventory_phases(
        inventory_field_with_default(
            namespace,
            "inventory_member_phases",
            f"Phases for {selected['device_id']}",
            ",".join(suggested_phases),
        )
    )


def _set_guided_binding_member(
    inventory: DeviceInventory,
    *,
    binding_id: str,
    binding_label: str,
    role: BindingRole,
    existing_binding: RoleBinding | None,
    first_member: bool,
    selected: dict[str, object],
    member_phases: tuple[PhaseLabel, ...],
) -> DeviceInventory:
    return set_inventory_binding_member(
        inventory,
        binding_id=binding_id,
        device_id=cast(str, selected["device_id"]),
        capability_id=cast(str, selected["capability_id"]),
        member_phases=member_phases,
        role=parse_inventory_binding_role(role),
        label=binding_label,
        phase_scope=member_phases if first_member and existing_binding is None else None,
    )
def action_payload(
    action: str,
    inventory_path: Path,
    inventory: DeviceInventory,
    **extra: object,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "ok": True,
        "action": action,
        "inventory_path": str(inventory_path),
        "inventory": inventory_summary_payload(inventory_path, inventory),
    }
    payload.update(extra)
    return payload


def save_and_payload(
    action: str,
    inventory_path: Path,
    inventory: DeviceInventory,
    **extra: object,
) -> dict[str, object]:
    save_inventory(inventory_path, inventory)
    return action_payload(action, inventory_path, inventory, **extra)


def _guided_profile_base_update(
    namespace: argparse.Namespace,
    inventory: DeviceInventory,
) -> tuple[DeviceInventory, str, str, tuple[PhaseLabel, ...], CapabilityKind, str]:
    profile_id = inventory_field(namespace, "inventory_profile_id", "Profile id")
    label = inventory_field(namespace, "inventory_label", "Profile label")
    kind = _guided_profile_kind(namespace)
    capability_default, adapter_default = _guided_capability_defaults(kind)
    capability_id = inventory_field_with_default(
        namespace,
        "inventory_capability_id",
        "Capability id",
        capability_default,
    )
    adapter_type = inventory_field_with_default(
        namespace,
        "inventory_adapter_type",
        "Adapter type",
        adapter_default,
    )
    supported_phases = parse_inventory_phases(
        inventory_field_with_default(
            namespace,
            "inventory_supported_phases",
            "Supported phases",
            "L1",
        )
    )
    capability_flags = _guided_capability_flags(namespace, kind, supported_phases)
    updated = add_inventory_profile(
        inventory,
        profile_id=profile_id,
        label=label,
        capability_id=capability_id,
        kind=kind,
        adapter_type=adapter_type,
        supported_phases=supported_phases,
        vendor=inventory_optional_field(namespace, "inventory_vendor", "Vendor"),
        model=inventory_optional_field(namespace, "inventory_model", "Model"),
        description=inventory_optional_field(namespace, "inventory_description", "Description"),
        channel=inventory_optional_field(namespace, "inventory_channel", "Channel"),
        measures_power=cast(bool, capability_flags["measures_power"]),
        measures_energy=cast(bool, capability_flags["measures_energy"]),
        switching_mode=cast(SwitchingMode | None, capability_flags["switching_mode"]),
        supports_feedback=cast(bool, capability_flags["supports_feedback"]),
        supports_phase_selection=cast(bool, capability_flags["supports_phase_selection"]),
    )
    return updated, profile_id, label, supported_phases, kind, capability_id


def _guided_binding_assignment(
    namespace: argparse.Namespace,
    updated: DeviceInventory,
    *,
    profile_id: str,
    device_id: str,
    capability_id: str,
    supported_phases: tuple[PhaseLabel, ...],
    inferred_role: BindingRole,
) -> tuple[DeviceInventory, str | None]:
    binding_default = _default_binding_id(updated, profile_id, inferred_role)
    binding_id = inventory_field_with_default(
        namespace,
        "inventory_binding_id",
        "Binding id",
        binding_default,
    )
    binding_label = inventory_field_with_default(
        namespace,
        "inventory_binding_label",
        "Binding label",
        inferred_role.replace("_", " ").title(),
    )
    member_phases = parse_inventory_phases(
        inventory_field_with_default(
            namespace,
            "inventory_member_phases",
            "Binding phases",
            ",".join(supported_phases),
        )
    )
    existing_binding = any(binding.id == binding_id for binding in updated.bindings)
    return (
        set_inventory_binding_member(
            updated,
            binding_id=binding_id,
            device_id=device_id,
            capability_id=capability_id,
            member_phases=member_phases,
            role=parse_inventory_binding_role(inferred_role),
            label=binding_label,
            phase_scope=None if existing_binding else member_phases,
        ),
        binding_id,
    )


def _maybe_add_guided_device_and_binding(
    namespace: argparse.Namespace,
    updated: DeviceInventory,
    *,
    profile_id: str,
    label: str,
    capability_id: str,
    supported_phases: tuple[PhaseLabel, ...],
    inferred_role: BindingRole,
) -> tuple[DeviceInventory, str | None, str | None]:
    if not inventory_bool_field(namespace, "_inventory_prompt_device", "Add one device instance for this profile now", True):
        return updated, None, None
    device_id = inventory_field_with_default(
        namespace,
        "inventory_device_id",
        "Device id",
        f"{profile_id}_device",
    )
    device_label = inventory_field_with_default(
        namespace,
        "inventory_label",
        "Device label",
        f"{label} device",
    )
    endpoint = inventory_optional_field(namespace, "inventory_endpoint", "Device endpoint")
    updated = add_inventory_device(
        updated,
        profile_id=profile_id,
        device_id=device_id,
        label=device_label,
        endpoint=endpoint,
    )
    if not inventory_bool_field(namespace, "_inventory_prompt_binding", "Assign this device capability to one role now", True):
        return updated, device_id, None
    updated, binding_id = _guided_binding_assignment(
        namespace,
        updated,
        profile_id=profile_id,
        device_id=device_id,
        capability_id=capability_id,
        supported_phases=supported_phases,
        inferred_role=inferred_role,
    )
    return updated, device_id, binding_id


def guided_inventory_add_profile(
    namespace: argparse.Namespace,
    inventory_path: Path,
    inventory: DeviceInventory,
) -> dict[str, object]:
    if getattr(namespace, "non_interactive", False):
        raise ValueError("guided-add-profile requires interactive input")
    updated, profile_id, label, supported_phases, kind, capability_id = _guided_profile_base_update(namespace, inventory)
    updated, device_id, binding_id = _maybe_add_guided_device_and_binding(
        namespace,
        updated,
        profile_id=profile_id,
        label=label,
        capability_id=capability_id,
        supported_phases=supported_phases,
        inferred_role=_guided_role_for_kind(kind),
    )
    return save_and_payload(
        "guided-add-profile",
        inventory_path,
        updated,
        profile_id=profile_id,
        device_id=device_id,
        binding_id=binding_id,
    )


def _validated_guided_binding(
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


def _extend_guided_binding(
    namespace: argparse.Namespace,
    updated: DeviceInventory,
    *,
    role: BindingRole,
    binding_id: str,
    binding_label: str,
    binding_scope: tuple[PhaseLabel, ...],
    existing_binding: RoleBinding | None,
    choices: tuple[dict[str, object], ...],
) -> DeviceInventory:
    first_member = True
    while True:
        if not first_member and not inventory_bool_field(namespace, "_inventory_add_binding_member", "Add another binding member", False):
            break
        selected = _selected_binding_choice(choices)
        member_phases = _binding_member_phases(namespace, selected, binding_scope)
        updated = _set_guided_binding_member(
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
    binding_id, existing_binding, binding_label, binding_scope = _prompt_binding_choice(namespace, inventory, role)
    updated, existing_binding = _maybe_replace_binding(namespace, inventory, existing_binding, binding_id)
    updated = _extend_guided_binding(
        namespace,
        updated,
        role=role,
        binding_id=binding_id,
        binding_label=binding_label,
        binding_scope=binding_scope,
        existing_binding=existing_binding,
        choices=choices,
    )
    _validated_guided_binding(updated, binding_id, binding_scope)
    return save_and_payload(
        "guided-edit-binding",
        inventory_path,
        updated,
        binding_id=binding_id,
    )

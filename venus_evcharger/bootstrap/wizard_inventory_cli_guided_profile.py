# SPDX-License-Identifier: GPL-3.0-or-later
"""Guided profile creation for the inventory editor CLI."""

from __future__ import annotations

import argparse
from pathlib import Path

from venus_evcharger.bootstrap.wizard_inventory_cli_guided_common import default_binding_id
from venus_evcharger.bootstrap.wizard_inventory_cli_payload import save_and_payload
from venus_evcharger.bootstrap.wizard_inventory_cli_guided_profile_specs import (
    guided_capability_defaults,
    guided_capability_flags,
    guided_profile_kind,
    guided_role_for_kind,
)
from venus_evcharger.bootstrap.wizard_inventory_editor import (
    add_inventory_device,
    add_inventory_profile,
    set_inventory_binding_member,
)
from venus_evcharger.bootstrap.wizard_inventory_prompts import (
    inventory_bool_field,
    inventory_field,
    inventory_field_with_default,
    inventory_optional_field,
)
from venus_evcharger.bootstrap.wizard_inventory_support import (
    parse_inventory_binding_role,
    parse_inventory_phases,
)
from venus_evcharger.inventory import BindingRole, CapabilityKind, DeviceInventory, PhaseLabel


def guided_profile_base_update(
    namespace: argparse.Namespace,
    inventory: DeviceInventory,
) -> tuple[DeviceInventory, str, str, tuple[PhaseLabel, ...], CapabilityKind, str]:
    profile_id = inventory_field(namespace, "inventory_profile_id", "Profile id")
    label = inventory_field(namespace, "inventory_label", "Profile label")
    kind = guided_profile_kind(namespace)
    capability_default, adapter_default = guided_capability_defaults(kind)
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
    capability_flags = guided_capability_flags(namespace, kind, supported_phases)
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
        measures_power=capability_flags["measures_power"],
        measures_energy=capability_flags["measures_energy"],
        switching_mode=capability_flags["switching_mode"],
        supports_feedback=capability_flags["supports_feedback"],
        supports_phase_selection=capability_flags["supports_phase_selection"],
    )
    return updated, profile_id, label, supported_phases, kind, capability_id


def guided_binding_assignment(
    namespace: argparse.Namespace,
    updated: DeviceInventory,
    *,
    profile_id: str,
    device_id: str,
    capability_id: str,
    supported_phases: tuple[PhaseLabel, ...],
    inferred_role: BindingRole,
) -> tuple[DeviceInventory, str | None]:
    binding_default = default_binding_id(updated, profile_id, inferred_role)
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


def maybe_add_guided_device_and_binding(
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
    updated, binding_id = guided_binding_assignment(
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
    updated, profile_id, label, supported_phases, kind, capability_id = guided_profile_base_update(namespace, inventory)
    updated, device_id, binding_id = maybe_add_guided_device_and_binding(
        namespace,
        updated,
        profile_id=profile_id,
        label=label,
        capability_id=capability_id,
        supported_phases=supported_phases,
        inferred_role=guided_role_for_kind(kind),
    )
    return save_and_payload(
        "guided-add-profile",
        inventory_path,
        updated,
        profile_id=profile_id,
        device_id=device_id,
        binding_id=binding_id,
    )

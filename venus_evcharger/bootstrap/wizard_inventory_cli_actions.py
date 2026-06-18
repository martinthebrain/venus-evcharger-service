# SPDX-License-Identifier: GPL-3.0-or-later
"""Simple inventory editor action handlers."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import TypedDict

from venus_evcharger.bootstrap.wizard_inventory_editor import (
    add_inventory_capability,
    add_inventory_device,
    add_inventory_profile,
    remove_inventory_binding_member,
    remove_inventory_device,
    set_inventory_binding_member,
    set_inventory_device_endpoint,
)
from venus_evcharger.bootstrap.wizard_inventory_prompts import (
    inventory_bool_field,
    inventory_field,
    inventory_optional_field,
)
from venus_evcharger.bootstrap.wizard_inventory_support import (
    parse_inventory_binding_role,
    parse_inventory_kind,
    parse_inventory_phases,
    parse_inventory_switching_mode,
)
from venus_evcharger.inventory import DeviceInventory, SwitchingMode

from venus_evcharger.bootstrap.wizard_inventory_cli_support import save_and_payload


class _InventoryCapabilityFields(TypedDict):
    channel: str | None
    measures_power: bool
    measures_energy: bool
    switching_mode: SwitchingMode | None
    supports_feedback: bool
    supports_phase_selection: bool


def _inventory_capability_fields(namespace: argparse.Namespace) -> _InventoryCapabilityFields:
    return {
        "channel": inventory_optional_field(namespace, "inventory_channel", "Channel"),
        "measures_power": inventory_bool_field(namespace, "inventory_measures_power", "Measures power"),
        "measures_energy": inventory_bool_field(namespace, "inventory_measures_energy", "Measures energy"),
        "switching_mode": parse_inventory_switching_mode(
            inventory_optional_field(namespace, "inventory_switching_mode", "Switching mode")
        ),
        "supports_feedback": inventory_bool_field(namespace, "inventory_supports_feedback", "Supports feedback"),
        "supports_phase_selection": inventory_bool_field(
            namespace,
            "inventory_supports_phase_selection",
            "Supports phase selection",
        ),
    }


def _run_add_profile_action(
    namespace: argparse.Namespace,
    inventory_path: Path,
    inventory: DeviceInventory,
) -> dict[str, object]:
    profile_id = inventory_field(namespace, "inventory_profile_id", "Profile id")
    label = inventory_field(namespace, "inventory_label", "Profile label")
    capability_id = inventory_field(namespace, "inventory_capability_id", "Capability id")
    kind = parse_inventory_kind(inventory_field(namespace, "inventory_kind", "Capability kind"))
    adapter_type = inventory_field(namespace, "inventory_adapter_type", "Adapter type")
    supported_phases = parse_inventory_phases(inventory_field(namespace, "inventory_supported_phases", "Supported phases"))
    capability_fields = _inventory_capability_fields(namespace)
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
        channel=capability_fields["channel"],
        measures_power=capability_fields["measures_power"],
        measures_energy=capability_fields["measures_energy"],
        switching_mode=capability_fields["switching_mode"],
        supports_feedback=capability_fields["supports_feedback"],
        supports_phase_selection=capability_fields["supports_phase_selection"],
    )
    return save_and_payload("add-profile", inventory_path, updated, profile_id=profile_id)


def _run_add_capability_action(
    namespace: argparse.Namespace,
    inventory_path: Path,
    inventory: DeviceInventory,
) -> dict[str, object]:
    profile_id = inventory_field(namespace, "inventory_profile_id", "Profile id")
    capability_id = inventory_field(namespace, "inventory_capability_id", "Capability id")
    kind = parse_inventory_kind(inventory_field(namespace, "inventory_kind", "Capability kind"))
    adapter_type = inventory_field(namespace, "inventory_adapter_type", "Adapter type")
    supported_phases = parse_inventory_phases(inventory_field(namespace, "inventory_supported_phases", "Supported phases"))
    capability_fields = _inventory_capability_fields(namespace)
    updated = add_inventory_capability(
        inventory,
        profile_id=profile_id,
        capability_id=capability_id,
        kind=kind,
        adapter_type=adapter_type,
        supported_phases=supported_phases,
        channel=capability_fields["channel"],
        measures_power=capability_fields["measures_power"],
        measures_energy=capability_fields["measures_energy"],
        switching_mode=capability_fields["switching_mode"],
        supports_feedback=capability_fields["supports_feedback"],
        supports_phase_selection=capability_fields["supports_phase_selection"],
    )
    return save_and_payload(
        "add-capability",
        inventory_path,
        updated,
        profile_id=profile_id,
        capability_id=capability_id,
    )


def _run_add_device_action(
    namespace: argparse.Namespace,
    inventory_path: Path,
    inventory: DeviceInventory,
) -> dict[str, object]:
    profile_id = inventory_field(namespace, "inventory_profile_id", "Profile id")
    device_id = inventory_field(namespace, "inventory_device_id", "Device id")
    label = inventory_field(namespace, "inventory_label", "Device label")
    endpoint = inventory_optional_field(namespace, "inventory_endpoint", "Device endpoint")
    updated = add_inventory_device(inventory, profile_id=profile_id, device_id=device_id, label=label, endpoint=endpoint)
    return save_and_payload("add-device", inventory_path, updated, device_id=device_id)


def _run_remove_device_action(
    namespace: argparse.Namespace,
    inventory_path: Path,
    inventory: DeviceInventory,
) -> dict[str, object]:
    device_id = inventory_field(namespace, "inventory_device_id", "Device id")
    updated = remove_inventory_device(inventory, device_id=device_id)
    return save_and_payload("remove-device", inventory_path, updated, device_id=device_id)


def _run_set_endpoint_action(
    namespace: argparse.Namespace,
    inventory_path: Path,
    inventory: DeviceInventory,
) -> dict[str, object]:
    device_id = inventory_field(namespace, "inventory_device_id", "Device id")
    endpoint = inventory_optional_field(namespace, "inventory_endpoint", "Device endpoint")
    updated = set_inventory_device_endpoint(inventory, device_id=device_id, endpoint=endpoint)
    return save_and_payload("set-endpoint", inventory_path, updated, device_id=device_id, endpoint=endpoint)


def _run_set_binding_member_action(
    namespace: argparse.Namespace,
    inventory_path: Path,
    inventory: DeviceInventory,
) -> dict[str, object]:
    binding_id = inventory_field(namespace, "inventory_binding_id", "Binding id")
    device_id = inventory_field(namespace, "inventory_device_id", "Device id")
    capability_id = inventory_field(namespace, "inventory_capability_id", "Capability id")
    member_phases = parse_inventory_phases(inventory_field(namespace, "inventory_member_phases", "Member phases"))
    raw_role = getattr(namespace, "inventory_binding_role", None)
    raw_label = getattr(namespace, "inventory_binding_label", None)
    raw_scope = getattr(namespace, "inventory_binding_phase_scope", None)
    updated = set_inventory_binding_member(
        inventory,
        binding_id=binding_id,
        device_id=device_id,
        capability_id=capability_id,
        member_phases=member_phases,
        role=parse_inventory_binding_role(str(raw_role)) if raw_role else None,
        label=str(raw_label).strip() if isinstance(raw_label, str) and raw_label.strip() else None,
        phase_scope=parse_inventory_phases(str(raw_scope)) if raw_scope else None,
    )
    return save_and_payload(
        "set-binding-member",
        inventory_path,
        updated,
        binding_id=binding_id,
        device_id=device_id,
    )


def _run_remove_binding_member_action(
    namespace: argparse.Namespace,
    inventory_path: Path,
    inventory: DeviceInventory,
) -> dict[str, object]:
    binding_id = inventory_field(namespace, "inventory_binding_id", "Binding id")
    device_id = inventory_field(namespace, "inventory_device_id", "Device id")
    updated = remove_inventory_binding_member(inventory, binding_id=binding_id, device_id=device_id)
    return save_and_payload(
        "remove-binding-member",
        inventory_path,
        updated,
        binding_id=binding_id,
        device_id=device_id,
    )


def run_simple_inventory_action(
    action: str,
    namespace: argparse.Namespace,
    inventory_path: Path,
    inventory: DeviceInventory,
) -> dict[str, object]:
    handlers = {
        "add-profile": _run_add_profile_action,
        "add-capability": _run_add_capability_action,
        "add-device": _run_add_device_action,
        "remove-device": _run_remove_device_action,
        "set-endpoint": _run_set_endpoint_action,
        "set-binding-member": _run_set_binding_member_action,
        "remove-binding-member": _run_remove_binding_member_action,
    }
    handler = handlers.get(action)
    if handler is not None:
        return handler(namespace, inventory_path, inventory)
    raise ValueError(f"Unsupported inventory action: {action}")

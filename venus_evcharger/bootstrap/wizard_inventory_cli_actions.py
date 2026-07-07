# SPDX-License-Identifier: GPL-3.0-or-later
"""Simple inventory editor action handlers."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
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

from venus_evcharger.bootstrap.wizard_inventory_cli_payload import save_and_payload


@dataclass(frozen=True)
class _InventoryFieldSpec:
    attr: str
    prompt: str


_PROFILE_ID = _InventoryFieldSpec("inventory_profile_id", "Profile id")
_PROFILE_LABEL = _InventoryFieldSpec("inventory_label", "Profile label")
_DEVICE_LABEL = _InventoryFieldSpec("inventory_label", "Device label")
_CAPABILITY_ID = _InventoryFieldSpec("inventory_capability_id", "Capability id")
_CAPABILITY_KIND = _InventoryFieldSpec("inventory_kind", "Capability kind")
_ADAPTER_TYPE = _InventoryFieldSpec("inventory_adapter_type", "Adapter type")
_SUPPORTED_PHASES = _InventoryFieldSpec("inventory_supported_phases", "Supported phases")
_CHANNEL = _InventoryFieldSpec("inventory_channel", "Channel")
_MEASURES_POWER = _InventoryFieldSpec("inventory_measures_power", "Measures power")
_MEASURES_ENERGY = _InventoryFieldSpec("inventory_measures_energy", "Measures energy")
_SWITCHING_MODE = _InventoryFieldSpec("inventory_switching_mode", "Switching mode")
_SUPPORTS_FEEDBACK = _InventoryFieldSpec("inventory_supports_feedback", "Supports feedback")
_SUPPORTS_PHASE_SELECTION = _InventoryFieldSpec("inventory_supports_phase_selection", "Supports phase selection")
_VENDOR = _InventoryFieldSpec("inventory_vendor", "Vendor")
_MODEL = _InventoryFieldSpec("inventory_model", "Model")
_DESCRIPTION = _InventoryFieldSpec("inventory_description", "Description")
_DEVICE_ID = _InventoryFieldSpec("inventory_device_id", "Device id")
_DEVICE_ENDPOINT = _InventoryFieldSpec("inventory_endpoint", "Device endpoint")
_BINDING_ID = _InventoryFieldSpec("inventory_binding_id", "Binding id")
_MEMBER_PHASES = _InventoryFieldSpec("inventory_member_phases", "Member phases")
_BINDING_ROLE = _InventoryFieldSpec("inventory_binding_role", "Binding role")
_BINDING_LABEL = _InventoryFieldSpec("inventory_binding_label", "Binding label")
_BINDING_PHASE_SCOPE = _InventoryFieldSpec("inventory_binding_phase_scope", "Binding phase scope")

_ALL_FIELD_SPECS: tuple[_InventoryFieldSpec, ...] = (
    _PROFILE_ID,
    _PROFILE_LABEL,
    _DEVICE_LABEL,
    _CAPABILITY_ID,
    _CAPABILITY_KIND,
    _ADAPTER_TYPE,
    _SUPPORTED_PHASES,
    _CHANNEL,
    _MEASURES_POWER,
    _MEASURES_ENERGY,
    _SWITCHING_MODE,
    _SUPPORTS_FEEDBACK,
    _SUPPORTS_PHASE_SELECTION,
    _VENDOR,
    _MODEL,
    _DESCRIPTION,
    _DEVICE_ID,
    _DEVICE_ENDPOINT,
    _BINDING_ID,
    _MEMBER_PHASES,
    _BINDING_ROLE,
    _BINDING_LABEL,
    _BINDING_PHASE_SCOPE,
)


class _InventoryCapabilityFields(TypedDict):
    channel: str | None
    measures_power: bool
    measures_energy: bool
    switching_mode: SwitchingMode | None
    supports_feedback: bool
    supports_phase_selection: bool


def _required_field(namespace: argparse.Namespace, spec: _InventoryFieldSpec) -> str:
    return inventory_field(namespace, spec.attr, spec.prompt)


def _optional_field(namespace: argparse.Namespace, spec: _InventoryFieldSpec) -> str | None:
    return inventory_optional_field(namespace, spec.attr, spec.prompt)


def _bool_field(namespace: argparse.Namespace, spec: _InventoryFieldSpec) -> bool:
    return inventory_bool_field(namespace, spec.attr, spec.prompt)


def _optional_namespace_text(namespace: argparse.Namespace, spec: _InventoryFieldSpec) -> str | None:
    raw = getattr(namespace, spec.attr, None)
    if not isinstance(raw, str):
        return None
    stripped = raw.strip()
    return stripped or None


def _inventory_capability_fields(namespace: argparse.Namespace) -> _InventoryCapabilityFields:
    return {
        "channel": _optional_field(namespace, _CHANNEL),
        "measures_power": _bool_field(namespace, _MEASURES_POWER),
        "measures_energy": _bool_field(namespace, _MEASURES_ENERGY),
        "switching_mode": parse_inventory_switching_mode(_optional_field(namespace, _SWITCHING_MODE)),
        "supports_feedback": _bool_field(namespace, _SUPPORTS_FEEDBACK),
        "supports_phase_selection": _bool_field(namespace, _SUPPORTS_PHASE_SELECTION),
    }


def _run_add_profile_action(
    namespace: argparse.Namespace,
    inventory_path: Path,
    inventory: DeviceInventory,
) -> dict[str, object]:
    profile_id = _required_field(namespace, _PROFILE_ID)
    label = _required_field(namespace, _PROFILE_LABEL)
    capability_id = _required_field(namespace, _CAPABILITY_ID)
    kind = parse_inventory_kind(_required_field(namespace, _CAPABILITY_KIND))
    adapter_type = _required_field(namespace, _ADAPTER_TYPE)
    supported_phases = parse_inventory_phases(_required_field(namespace, _SUPPORTED_PHASES))
    capability_fields = _inventory_capability_fields(namespace)
    updated = add_inventory_profile(
        inventory,
        profile_id=profile_id,
        label=label,
        capability_id=capability_id,
        kind=kind,
        adapter_type=adapter_type,
        supported_phases=supported_phases,
        vendor=_optional_field(namespace, _VENDOR),
        model=_optional_field(namespace, _MODEL),
        description=_optional_field(namespace, _DESCRIPTION),
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
    profile_id = _required_field(namespace, _PROFILE_ID)
    capability_id = _required_field(namespace, _CAPABILITY_ID)
    kind = parse_inventory_kind(_required_field(namespace, _CAPABILITY_KIND))
    adapter_type = _required_field(namespace, _ADAPTER_TYPE)
    supported_phases = parse_inventory_phases(_required_field(namespace, _SUPPORTED_PHASES))
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
    profile_id = _required_field(namespace, _PROFILE_ID)
    device_id = _required_field(namespace, _DEVICE_ID)
    label = _required_field(namespace, _DEVICE_LABEL)
    endpoint = _optional_field(namespace, _DEVICE_ENDPOINT)
    updated = add_inventory_device(inventory, profile_id=profile_id, device_id=device_id, label=label, endpoint=endpoint)
    return save_and_payload("add-device", inventory_path, updated, device_id=device_id)


def _run_remove_device_action(
    namespace: argparse.Namespace,
    inventory_path: Path,
    inventory: DeviceInventory,
) -> dict[str, object]:
    device_id = _required_field(namespace, _DEVICE_ID)
    updated = remove_inventory_device(inventory, device_id=device_id)
    return save_and_payload("remove-device", inventory_path, updated, device_id=device_id)


def _run_set_endpoint_action(
    namespace: argparse.Namespace,
    inventory_path: Path,
    inventory: DeviceInventory,
) -> dict[str, object]:
    device_id = _required_field(namespace, _DEVICE_ID)
    endpoint = _optional_field(namespace, _DEVICE_ENDPOINT)
    updated = set_inventory_device_endpoint(inventory, device_id=device_id, endpoint=endpoint)
    return save_and_payload("set-endpoint", inventory_path, updated, device_id=device_id, endpoint=endpoint)


def _run_set_binding_member_action(
    namespace: argparse.Namespace,
    inventory_path: Path,
    inventory: DeviceInventory,
) -> dict[str, object]:
    binding_id = _required_field(namespace, _BINDING_ID)
    device_id = _required_field(namespace, _DEVICE_ID)
    capability_id = _required_field(namespace, _CAPABILITY_ID)
    member_phases = parse_inventory_phases(_required_field(namespace, _MEMBER_PHASES))
    role = _optional_namespace_text(namespace, _BINDING_ROLE)
    label = _optional_namespace_text(namespace, _BINDING_LABEL)
    phase_scope = _optional_namespace_text(namespace, _BINDING_PHASE_SCOPE)
    updated = set_inventory_binding_member(
        inventory,
        binding_id=binding_id,
        device_id=device_id,
        capability_id=capability_id,
        member_phases=member_phases,
        role=parse_inventory_binding_role(role) if role else None,
        label=label,
        phase_scope=parse_inventory_phases(phase_scope) if phase_scope else None,
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
    binding_id = _required_field(namespace, _BINDING_ID)
    device_id = _required_field(namespace, _DEVICE_ID)
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

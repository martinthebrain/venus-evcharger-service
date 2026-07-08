# SPDX-License-Identifier: GPL-3.0-or-later
"""Small inventory editing helpers for the setup wizard CLI."""

from __future__ import annotations

import argparse

from venus_evcharger.bootstrap.wizard_inventory_support import PHASE_ORDER
from venus_evcharger.bootstrap.wizard_inventory_types import InventoryCapabilityChoice
from venus_evcharger.inventory import (
    BindingRole,
    CapabilityKind,
    DeviceCapability,
    DeviceInstance,
    DeviceInventory,
    DeviceProfile,
    PhaseLabel,
    RoleBinding,
    RoleBindingMember,
    SwitchingMode,
    validate_device_inventory,
)
from venus_evcharger.bootstrap.wizard_inventory_editor_bindings import (
    remove_inventory_binding_member,
    set_inventory_binding_member,
)

__all__ = [
    "add_inventory_capability",
    "add_inventory_device",
    "add_inventory_profile",
    "InventoryCapabilityChoice",
    "inventory_role_capability_choices",
    "remove_inventory_binding",
    "remove_inventory_binding_member",
    "remove_inventory_device",
    "set_inventory_binding_member",
    "set_inventory_device_endpoint",
]


def remove_inventory_binding(inventory: DeviceInventory, *, binding_id: str) -> DeviceInventory:
    """Return one inventory without one whole binding."""
    if not any(binding.id == binding_id for binding in inventory.bindings):
        raise ValueError(f"Unknown binding id: {binding_id}")
    return validate_device_inventory(
        DeviceInventory(
            profiles=inventory.profiles,
            devices=inventory.devices,
            bindings=tuple(binding for binding in inventory.bindings if binding.id != binding_id),
        )
    )


def inventory_role_capability_choices(
    inventory: DeviceInventory,
    *,
    role: BindingRole,
) -> tuple[InventoryCapabilityChoice, ...]:
    """Return one stable list of eligible device/capability choices for one role."""
    expected_kind = _binding_capability_kind_for_role(role)
    choices: list[InventoryCapabilityChoice] = []
    profile_by_id = _profile_lookup(inventory)
    for device in inventory.devices:
        profile = profile_by_id.get(device.profile_id)
        if profile is None:
            continue
        choices.extend(_device_capability_choices(device, profile, expected_kind))
    return tuple(choices)


def add_inventory_device(
    inventory: DeviceInventory,
    *,
    profile_id: str,
    device_id: str,
    label: str,
    endpoint: str | None,
) -> DeviceInventory:
    """Return one inventory with one additional device instance."""
    _require_profile_exists(inventory, profile_id)
    _require_device_missing(inventory, device_id)
    devices = inventory.devices + (_device_instance(profile_id, device_id, label, endpoint),)
    return _validated_inventory(inventory, devices=devices)


def remove_inventory_device(inventory: DeviceInventory, *, device_id: str) -> DeviceInventory:
    """Return one inventory without one device and without orphaned binding members."""
    _require_device_exists(inventory, device_id)
    devices = tuple(device for device in inventory.devices if device.id != device_id)
    bindings = _bindings_without_device(inventory.bindings, device_id)
    return _validated_inventory(inventory, devices=devices, bindings=bindings)


def set_inventory_device_endpoint(
    inventory: DeviceInventory,
    *,
    device_id: str,
    endpoint: str | None,
) -> DeviceInventory:
    """Return one inventory with one updated device endpoint."""
    if not any(device.id == device_id for device in inventory.devices):
        raise ValueError(f"Unknown device id: {device_id}")
    return validate_device_inventory(
        DeviceInventory(
            profiles=inventory.profiles,
            devices=tuple(
                _device_with_endpoint(device, endpoint) if device.id == device_id else device for device in inventory.devices
            ),
            bindings=inventory.bindings,
        )
    )


def add_inventory_profile(
    inventory: DeviceInventory,
    *,
    profile_id: str,
    label: str,
    capability_id: str,
    kind: CapabilityKind,
    adapter_type: str,
    supported_phases: tuple[PhaseLabel, ...],
    vendor: str | None = None,
    model: str | None = None,
    description: str | None = None,
    channel: str | None = None,
    measures_power: bool = False,
    measures_energy: bool = False,
    switching_mode: SwitchingMode | None = None,
    supports_feedback: bool = False,
    supports_phase_selection: bool = False,
) -> DeviceInventory:
    """Return one inventory with one additional device profile."""
    _require_profile_missing(inventory, profile_id)
    profile = DeviceProfile(
        id=profile_id,
        label=label,
        vendor=vendor or None,
        model=model or None,
        description=description or None,
        capabilities=(
            _device_capability(
                capability_id=capability_id,
                kind=kind,
                adapter_type=adapter_type,
                supported_phases=supported_phases,
                channel=channel,
                measures_power=measures_power,
                measures_energy=measures_energy,
                switching_mode=switching_mode,
                supports_feedback=supports_feedback,
                supports_phase_selection=supports_phase_selection,
            ),
        ),
    )
    return _validated_inventory(inventory, profiles=inventory.profiles + (profile,))


def add_inventory_capability(
    inventory: DeviceInventory,
    *,
    profile_id: str,
    capability_id: str,
    kind: CapabilityKind,
    adapter_type: str,
    supported_phases: tuple[PhaseLabel, ...],
    channel: str | None = None,
    measures_power: bool = False,
    measures_energy: bool = False,
    switching_mode: SwitchingMode | None = None,
    supports_feedback: bool = False,
    supports_phase_selection: bool = False,
) -> DeviceInventory:
    """Return one inventory with one appended capability on one profile."""
    _require_profile_exists(inventory, profile_id)
    new_capability = _device_capability(
        capability_id=capability_id,
        kind=kind,
        adapter_type=adapter_type,
        supported_phases=supported_phases,
        channel=channel,
        measures_power=measures_power,
        measures_energy=measures_energy,
        switching_mode=switching_mode,
        supports_feedback=supports_feedback,
        supports_phase_selection=supports_phase_selection,
    )
    profiles = tuple(
        profile if profile.id != profile_id else _profile_with_capability(profile, profile_id, capability_id, new_capability)
        for profile in inventory.profiles
    )
    return _validated_inventory(inventory, profiles=tuple(profiles))


def _binding_without_device(binding: RoleBinding, device_id: str) -> RoleBinding | None:
    members = tuple(member for member in binding.members if member.device_id != device_id)
    if not members:
        return None
    phase_scope = _binding_phase_scope_subset(binding.phase_scope, members)
    return RoleBinding(
        id=binding.id,
        role=binding.role,
        label=binding.label,
        phase_scope=phase_scope or binding.phase_scope,
        members=members,
    )


def _profile_lookup(inventory: DeviceInventory) -> dict[str, DeviceProfile]:
    """Return profiles keyed by profile id."""
    return {profile.id: profile for profile in inventory.profiles}


def _device_capability_choices(
    device: DeviceInstance,
    profile: DeviceProfile,
    expected_kind: CapabilityKind,
) -> list[InventoryCapabilityChoice]:
    """Return eligible capability choices for one concrete device."""
    return [
        {
            "device_id": device.id,
            "device_label": device.label,
            "profile_id": profile.id,
            "profile_label": profile.label,
            "capability_id": capability.id,
            "adapter_type": capability.adapter_type,
            "supported_phases": capability.supported_phases,
        }
        for capability in profile.capabilities
        if capability.kind == expected_kind
    ]


def _validated_inventory(
    inventory: DeviceInventory,
    *,
    profiles: tuple[DeviceProfile, ...] | None = None,
    devices: tuple[DeviceInstance, ...] | None = None,
    bindings: tuple[RoleBinding, ...] | None = None,
) -> DeviceInventory:
    """Return one validated inventory with selectively replaced collections."""
    return validate_device_inventory(
        DeviceInventory(
            profiles=inventory.profiles if profiles is None else profiles,
            devices=inventory.devices if devices is None else devices,
            bindings=inventory.bindings if bindings is None else bindings,
        )
    )


def _require_profile_exists(inventory: DeviceInventory, profile_id: str) -> None:
    """Require one known profile id in the inventory."""
    if any(profile.id == profile_id for profile in inventory.profiles):
        return
    raise ValueError(f"Unknown profile id: {profile_id}")


def _require_profile_missing(inventory: DeviceInventory, profile_id: str) -> None:
    """Require that one profile id is not yet used."""
    if any(profile.id == profile_id for profile in inventory.profiles):
        raise ValueError(f"Profile id already exists: {profile_id}")


def _require_device_exists(inventory: DeviceInventory, device_id: str) -> None:
    """Require one known device id in the inventory."""
    if any(device.id == device_id for device in inventory.devices):
        return
    raise ValueError(f"Unknown device id: {device_id}")


def _require_device_missing(inventory: DeviceInventory, device_id: str) -> None:
    """Require that one device id is not yet used."""
    if any(device.id == device_id for device in inventory.devices):
        raise ValueError(f"Device id already exists: {device_id}")


def _device_instance(profile_id: str, device_id: str, label: str, endpoint: str | None) -> DeviceInstance:
    """Return one normalized device instance."""
    return DeviceInstance(
        id=device_id,
        profile_id=profile_id,
        label=label,
        endpoint=endpoint or None,
    )


def _device_with_endpoint(device: DeviceInstance, endpoint: str | None) -> DeviceInstance:
    """Return one device with one normalized endpoint and unchanged identity metadata."""
    return DeviceInstance(
        id=device.id,
        profile_id=device.profile_id,
        label=device.label,
        endpoint=endpoint or None,
        enabled=device.enabled,
        notes=device.notes,
    )


def _device_capability(
    *,
    capability_id: str,
    kind: CapabilityKind,
    adapter_type: str,
    supported_phases: tuple[PhaseLabel, ...],
    channel: str | None,
    measures_power: bool,
    measures_energy: bool,
    switching_mode: SwitchingMode | None,
    supports_feedback: bool,
    supports_phase_selection: bool,
) -> DeviceCapability:
    """Return one normalized device capability."""
    return DeviceCapability(
        id=capability_id,
        kind=kind,
        adapter_type=adapter_type,
        supported_phases=supported_phases,
        channel=channel or None,
        measures_power=measures_power,
        measures_energy=measures_energy,
        switching_mode=switching_mode or None,
        supports_feedback=supports_feedback,
        supports_phase_selection=supports_phase_selection,
    )


def _binding_phase_scope_subset(
    phase_scope: tuple[PhaseLabel, ...],
    members: tuple[RoleBindingMember, ...],
) -> tuple[PhaseLabel, ...]:
    """Return the phase-scope subset still covered by the remaining binding members."""
    return tuple(phase for phase in phase_scope if any(phase in member.phases for member in members))


def _bindings_without_device(
    bindings: tuple[RoleBinding, ...],
    device_id: str,
) -> tuple[RoleBinding, ...]:
    """Return bindings with one device removed from all binding members."""
    return tuple(
        updated_binding
        for binding in bindings
        for updated_binding in [_binding_without_device(binding, device_id)]
        if updated_binding is not None
    )


def _profile_with_capability(
    profile: DeviceProfile,
    profile_id: str,
    capability_id: str,
    capability: DeviceCapability,
) -> DeviceProfile:
    """Return one profile with an appended capability."""
    if any(cap.id == capability_id for cap in profile.capabilities):
        raise ValueError(f"Capability id already exists in profile {profile_id}: {capability_id}")
    return DeviceProfile(
        id=profile.id,
        label=profile.label,
        vendor=profile.vendor,
        model=profile.model,
        description=profile.description,
        capabilities=profile.capabilities + (capability,),
    )


def _binding_capability_kind_for_role(role: BindingRole) -> CapabilityKind:
    if role == "actuation":
        return "switch"
    if role == "charger":
        return "charger"
    return "meter"

# SPDX-License-Identifier: GPL-3.0-or-later
"""Validate inventory-domain invariants and cross references."""

from __future__ import annotations

from collections.abc import Mapping

from .config_contracts import DeviceInventoryConfigError
from .schema import (
    BindingRole,
    CapabilityKind,
    DeviceCapability,
    DeviceInstance,
    DeviceInventory,
    DeviceProfile,
    PhaseLabel,
    RoleBinding,
    RoleBindingMember,
)


def validate_device_inventory(inventory: DeviceInventory) -> DeviceInventory:
    """Validate one device inventory root object."""
    profile_map = _profile_map(inventory.profiles)
    device_map = _device_map(inventory.devices)
    binding_ids: set[str] = set()
    for device in inventory.devices:
        if device.profile_id not in profile_map:
            raise DeviceInventoryConfigError(
                f"device '{device.id}' references unknown profile '{device.profile_id}'"
            )
    for binding in inventory.bindings:
        if binding.id in binding_ids:
            raise DeviceInventoryConfigError(f"duplicate binding id '{binding.id}'")
        binding_ids.add(binding.id)
        _validate_binding(binding, profile_map, device_map)
    return inventory


def _validate_binding(
    binding: RoleBinding,
    profile_map: Mapping[str, DeviceProfile],
    device_map: Mapping[str, DeviceInstance],
) -> None:
    if not binding.members:
        raise DeviceInventoryConfigError(f"binding '{binding.id}' requires at least one member")
    covered_phases: set[PhaseLabel] = set()
    for member in binding.members:
        capability = _binding_member_capability(binding, member, profile_map, device_map)
        _validate_binding_member(binding, member, capability)
        _update_covered_phases(binding.id, covered_phases, member.phases)
    _validate_phase_scope_coverage(binding, covered_phases)


def _binding_member_capability(
    binding: RoleBinding,
    member: RoleBindingMember,
    profile_map: Mapping[str, DeviceProfile],
    device_map: Mapping[str, DeviceInstance],
) -> DeviceCapability:
    """Return the referenced capability for one binding member."""
    if member.device_id not in device_map:
        raise DeviceInventoryConfigError(
            f"binding '{binding.id}' references unknown device '{member.device_id}'"
        )
    profile = profile_map[device_map[member.device_id].profile_id]
    return _profile_capability(profile, member.capability_id, binding.id)


def _update_covered_phases(
    binding_id: str,
    covered_phases: set[PhaseLabel],
    member_phases: tuple[PhaseLabel, ...],
) -> None:
    """Track covered phases and reject duplicate binding assignments."""
    overlap = covered_phases.intersection(member_phases)
    if overlap:
        formatted = ",".join(sorted(overlap))
        raise DeviceInventoryConfigError(
            f"binding '{binding_id}' assigns duplicate phases {formatted}"
        )
    covered_phases.update(member_phases)


def _validate_phase_scope_coverage(binding: RoleBinding, covered_phases: set[PhaseLabel]) -> None:
    """Validate that one binding exactly covers its declared phase scope."""
    if covered_phases == set(binding.phase_scope):
        return
    formatted_covered = ",".join(sorted(covered_phases))
    formatted_scope = ",".join(binding.phase_scope)
    raise DeviceInventoryConfigError(
        f"binding '{binding.id}' covers phases {formatted_covered} but scope is {formatted_scope}"
    )


def _profile_capability(
    profile: DeviceProfile,
    capability_id: str,
    binding_id: str,
) -> DeviceCapability:
    for capability in profile.capabilities:
        if capability.id == capability_id:
            return capability
    raise DeviceInventoryConfigError(
        f"binding '{binding_id}' references unknown capability '{capability_id}' in profile '{profile.id}'"
    )


def _validate_binding_member(
    binding: RoleBinding,
    member: RoleBindingMember,
    capability: DeviceCapability,
) -> None:
    expected_kind = capability_kind_for_binding_role(binding.role)
    if capability.kind != expected_kind:
        raise DeviceInventoryConfigError(
            f"binding '{binding.id}' role '{binding.role}' requires capability kind '{expected_kind}'"
        )
    if not set(member.phases).issubset(capability.supported_phases):
        formatted = ",".join(member.phases)
        supported = ",".join(capability.supported_phases)
        raise DeviceInventoryConfigError(
            f"binding '{binding.id}' assigns phases {formatted} outside capability support {supported}"
        )
    if not set(member.phases).issubset(binding.phase_scope):
        formatted = ",".join(member.phases)
        scope = ",".join(binding.phase_scope)
        raise DeviceInventoryConfigError(
            f"binding '{binding.id}' assigns phases {formatted} outside binding scope {scope}"
        )


def capability_kind_for_binding_role(role: BindingRole) -> CapabilityKind:
    """Return the capability kind required by a logical binding role."""
    if role == "actuation":
        return "switch"
    if role == "measurement":
        return "meter"
    return "charger"


def _profile_map(profiles: tuple[DeviceProfile, ...]) -> dict[str, DeviceProfile]:
    profile_map: dict[str, DeviceProfile] = {}
    for profile in profiles:
        if profile.id in profile_map:
            raise DeviceInventoryConfigError(f"duplicate profile id '{profile.id}'")
        profile_map[profile.id] = profile
        _validate_profile(profile)
    return profile_map


def _device_map(devices: tuple[DeviceInstance, ...]) -> dict[str, DeviceInstance]:
    device_map: dict[str, DeviceInstance] = {}
    for device in devices:
        if device.id in device_map:
            raise DeviceInventoryConfigError(f"duplicate device id '{device.id}'")
        device_map[device.id] = device
    return device_map


def _validate_profile(profile: DeviceProfile) -> None:
    if not profile.capabilities:
        raise DeviceInventoryConfigError(f"profile '{profile.id}' requires at least one capability")
    capability_ids: set[str] = set()
    for capability in profile.capabilities:
        if capability.id in capability_ids:
            raise DeviceInventoryConfigError(
                f"duplicate capability id '{capability.id}' for profile '{profile.id}'"
            )
        capability_ids.add(capability.id)
        _validate_capability(profile.id, capability)


def _validate_capability(profile_id: str, capability: DeviceCapability) -> None:
    _validate_measurement_capability(profile_id, capability)
    _validate_switch_capability(profile_id, capability)


def _validate_measurement_capability(profile_id: str, capability: DeviceCapability) -> None:
    """Validate measurement-specific fields for one capability."""
    if capability.kind == "meter":
        _require_meter_measurement_flags(profile_id, capability)
        return
    _reject_non_meter_measurement_flags(profile_id, capability)


def _require_meter_measurement_flags(profile_id: str, capability: DeviceCapability) -> None:
    """Require at least one measurement flag for meter capabilities."""
    if capability.measures_power or capability.measures_energy:
        return
    raise DeviceInventoryConfigError(
        f"meter capability '{capability.id}' in profile '{profile_id}' must measure power or energy"
    )


def _reject_non_meter_measurement_flags(profile_id: str, capability: DeviceCapability) -> None:
    """Reject measurement flags declared by non-meter capabilities."""
    if not (capability.measures_power or capability.measures_energy):
        return
    raise DeviceInventoryConfigError(
        f"non-meter capability '{capability.id}' in profile '{profile_id}' may not declare measurement flags"
    )


def _validate_switch_capability(profile_id: str, capability: DeviceCapability) -> None:
    """Validate switch-specific fields for one capability."""
    if capability.kind == "switch":
        if capability.switching_mode is not None:
            return
        raise DeviceInventoryConfigError(
            f"switch capability '{capability.id}' in profile '{profile_id}' requires SwitchingMode"
        )
    if capability.switching_mode is not None:
        raise DeviceInventoryConfigError(
            f"non-switch capability '{capability.id}' in profile '{profile_id}' may not declare SwitchingMode"
        )


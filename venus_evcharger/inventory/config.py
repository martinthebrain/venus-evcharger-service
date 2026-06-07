# SPDX-License-Identifier: GPL-3.0-or-later
"""Parsing and validation helpers for device profile and inventory configs.

The inventory format is intentionally explicit: profiles describe capabilities,
devices bind profiles to endpoints, and bindings map capabilities to roles.
Keeping parsing, rendering, and validation together preserves round-trip safety
for the wizard and for hand-edited inventory files.
"""

from __future__ import annotations

import configparser
from typing import cast

from venus_evcharger.core.contracts import optional_text

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
    SwitchingMode,
)
from .render import _render_switch_capability_fields, render_validated_device_inventory_config


class DeviceInventoryConfigError(ValueError):
    """Raised when one device inventory config is invalid."""


def parse_device_inventory_config(config: configparser.ConfigParser) -> DeviceInventory:
    """Parse one normalized device inventory from config sections."""
    profiles = _profiles(config)
    devices = _devices(config)
    bindings = _bindings(config)
    parsed = DeviceInventory(
        profiles=tuple(profiles.values()),
        devices=tuple(devices.values()),
        bindings=tuple(bindings.values()),
    )
    validate_device_inventory(parsed)
    return parsed


def render_device_inventory_config(inventory: DeviceInventory) -> str:
    """Render one normalized device inventory into one INI-style text."""
    validate_device_inventory(inventory)
    return render_validated_device_inventory_config(inventory)


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


def _profiles(config: configparser.ConfigParser) -> dict[str, DeviceProfile]:
    profiles: dict[str, DeviceProfile] = {}
    profile_sections = sorted(
        section for section in config.sections() if section.startswith("Profile:")
    )
    capabilities_by_profile = _capabilities(config)
    for section_name in profile_sections:
        profile_id = _suffix(section_name, "Profile:")
        section = config[section_name]
        profiles[profile_id] = DeviceProfile(
            id=profile_id,
            label=_required_text(section, "Label"),
            vendor=_optional_text(section.get("Vendor")),
            model=_optional_text(section.get("Model")),
            description=_optional_text(section.get("Description")),
            capabilities=tuple(capabilities_by_profile.get(profile_id, {}).values()),
        )
    return profiles


def _capabilities(
    config: configparser.ConfigParser,
) -> dict[str, dict[str, DeviceCapability]]:
    capabilities_by_profile: dict[str, dict[str, DeviceCapability]] = {}
    capability_sections = sorted(
        section for section in config.sections() if section.startswith("Capability:")
    )
    for section_name in capability_sections:
        remainder = _suffix(section_name, "Capability:")
        profile_id, capability_id = _split_section_id(
            remainder,
            expected_parts=2,
            label=section_name,
        )
        section = config[section_name]
        capability = DeviceCapability(
            id=capability_id,
            kind=_capability_kind(_required_text(section, "Kind")),
            adapter_type=_required_text(section, "AdapterType"),
            supported_phases=_phase_labels(_required_text(section, "SupportedPhases")),
            channel=_optional_text(section.get("Channel")),
            measures_power=_as_bool(section.get("MeasuresPower", "0")),
            measures_energy=_as_bool(section.get("MeasuresEnergy", "0")),
            switching_mode=_optional_switching_mode(section.get("SwitchingMode")),
            supports_feedback=_as_bool(section.get("SupportsFeedback", "0")),
            supports_phase_selection=_as_bool(section.get("SupportsPhaseSelection", "0")),
        )
        profile_capabilities = capabilities_by_profile.setdefault(profile_id, {})
        if capability_id in profile_capabilities:
            raise DeviceInventoryConfigError(
                f"duplicate capability id '{capability_id}' for profile '{profile_id}'"
            )
        profile_capabilities[capability_id] = capability
    return capabilities_by_profile


def _devices(config: configparser.ConfigParser) -> dict[str, DeviceInstance]:
    devices: dict[str, DeviceInstance] = {}
    device_sections = sorted(
        section for section in config.sections() if section.startswith("Device:")
    )
    for section_name in device_sections:
        device_id = _suffix(section_name, "Device:")
        if device_id in devices:
            raise DeviceInventoryConfigError(f"duplicate device id '{device_id}'")
        section = config[section_name]
        devices[device_id] = DeviceInstance(
            id=device_id,
            profile_id=_required_text(section, "Profile"),
            label=_required_text(section, "Label"),
            endpoint=_optional_text(section.get("Endpoint")),
            enabled=_as_bool(section.get("Enabled", "1")),
            notes=_optional_text(section.get("Notes")),
        )
    return devices


def _bindings(config: configparser.ConfigParser) -> dict[str, RoleBinding]:
    members_by_binding = _binding_members(config)
    bindings: dict[str, RoleBinding] = {}
    binding_sections = sorted(
        section for section in config.sections() if section.startswith("Binding:")
    )
    for section_name in binding_sections:
        binding_id = _suffix(section_name, "Binding:")
        if binding_id in bindings:
            raise DeviceInventoryConfigError(f"duplicate binding id '{binding_id}'")
        section = config[section_name]
        bindings[binding_id] = RoleBinding(
            id=binding_id,
            role=_binding_role(_required_text(section, "Role")),
            label=_required_text(section, "Label"),
            phase_scope=_phase_labels(_required_text(section, "PhaseScope")),
            members=tuple(members_by_binding.get(binding_id, {}).values()),
        )
    return bindings


def _binding_members(
    config: configparser.ConfigParser,
) -> dict[str, dict[str, RoleBindingMember]]:
    members_by_binding: dict[str, dict[str, RoleBindingMember]] = {}
    member_sections = sorted(
        section for section in config.sections() if section.startswith("BindingMember:")
    )
    for section_name in member_sections:
        remainder = _suffix(section_name, "BindingMember:")
        binding_id, member_id = _split_section_id(
            remainder,
            expected_parts=2,
            label=section_name,
        )
        section = config[section_name]
        member = RoleBindingMember(
            device_id=_required_text(section, "Device"),
            capability_id=_required_text(section, "Capability"),
            phases=_phase_labels(_required_text(section, "Phases")),
        )
        binding_members = members_by_binding.setdefault(binding_id, {})
        if member_id in binding_members:
            raise DeviceInventoryConfigError(
                f"duplicate binding member id '{member_id}' for binding '{binding_id}'"
            )
        binding_members[member_id] = member
    return members_by_binding


def _validate_binding(
    binding: RoleBinding,
    profile_map: dict[str, DeviceProfile],
    device_map: dict[str, DeviceInstance],
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
    profile_map: dict[str, DeviceProfile],
    device_map: dict[str, DeviceInstance],
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
    expected_kind = _binding_capability_kind(binding.role)
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


def _binding_capability_kind(role: BindingRole) -> CapabilityKind:
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


def _phase_labels(value: str) -> tuple[PhaseLabel, ...]:
    normalized: list[PhaseLabel] = []
    for raw in _phase_tokens(value):
        if not raw:
            continue
        phase = cast(
            PhaseLabel,
            _literal_choice(
                value=raw,
                allowed={"L1", "L2", "L3"},
                label="phase list",
            ),
        )
        if phase not in normalized:
            normalized.append(phase)
    if not normalized:
        raise DeviceInventoryConfigError("phase list may not be empty")
    return tuple(normalized)


def _phase_tokens(value: str) -> list[str]:
    """Return normalized uppercase phase tokens from one CSV payload."""
    return [part.strip().upper() for part in value.split(",")]


def _capability_kind(value: str) -> CapabilityKind:
    return cast(
        CapabilityKind,
        _literal_choice(
            value=value,
            allowed={"switch", "meter", "charger"},
            label="Capability.Kind",
        ),
    )


def _optional_switching_mode(value: object) -> SwitchingMode | None:
    text = _optional_text(value)
    if text is None:
        return None
    return cast(
        SwitchingMode,
        _literal_choice(
            value=text,
            allowed={"direct", "contactor"},
            label="Capability.SwitchingMode",
        ),
    )


def _binding_role(value: str) -> BindingRole:
    return cast(
        BindingRole,
        _literal_choice(
            value=value,
            allowed={"actuation", "measurement", "charger"},
            label="Binding.Role",
        ),
    )


def _literal_choice(value: str, allowed: set[str], label: str) -> str:
    normalized = value.strip()
    if normalized not in allowed:
        allowed_values = ", ".join(sorted(allowed))
        raise DeviceInventoryConfigError(
            f"{label} must be one of: {allowed_values} (got '{value}')"
        )
    return normalized


def _required_text(section: configparser.SectionProxy, key: str) -> str:
    value = _optional_text(section.get(key))
    if value is None:
        raise DeviceInventoryConfigError(f"missing required key {section.name}.{key}")
    return value


def _optional_text(value: object) -> str | None:
    return optional_text(value)


def _as_bool(value: object) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _suffix(value: str, prefix: str) -> str:
    remainder = value[len(prefix) :].strip()
    if not remainder:
        raise DeviceInventoryConfigError(f"invalid section name '{value}'")
    return remainder


def _split_section_id(value: str, *, expected_parts: int, label: str) -> tuple[str, ...]:
    parts = tuple(part.strip() for part in value.split(":"))
    if len(parts) != expected_parts or any(not part for part in parts):
        raise DeviceInventoryConfigError(f"invalid section name '{label}'")
    return parts

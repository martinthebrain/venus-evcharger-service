# SPDX-License-Identifier: GPL-3.0-or-later
"""Parse typed inventory objects from normalized INI sections."""

from __future__ import annotations

from .config_contracts import DeviceInventoryConfigError, InventoryConfigSections
from .config_values import (
    _binding_role,
    _capability_kind,
    _optional_switching_mode,
    _optional_text,
    _phase_labels,
    _required_text,
    _section_bool,
    _split_section_id,
    _suffix,
)
from .schema import (
    DeviceCapability,
    DeviceInstance,
    DeviceInventory,
    DeviceProfile,
    RoleBinding,
    RoleBindingMember,
)


def parse_inventory_sections(config: InventoryConfigSections) -> DeviceInventory:
    """Parse inventory sections without applying cross-reference validation."""
    profiles = _profiles(config)
    devices = _devices(config)
    bindings = _bindings(config)
    return DeviceInventory(
        profiles=tuple(profiles.values()),
        devices=tuple(devices.values()),
        bindings=tuple(bindings.values()),
    )


def _profiles(config: InventoryConfigSections) -> dict[str, DeviceProfile]:
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
    config: InventoryConfigSections,
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
            measures_power=_section_bool(section, "MeasuresPower"),
            measures_energy=_section_bool(section, "MeasuresEnergy"),
            switching_mode=_optional_switching_mode(section.get("SwitchingMode")),
            supports_feedback=_section_bool(section, "SupportsFeedback"),
            supports_phase_selection=_section_bool(section, "SupportsPhaseSelection"),
        )
        profile_capabilities = capabilities_by_profile.setdefault(profile_id, {})
        if capability_id in profile_capabilities:
            raise DeviceInventoryConfigError(
                f"duplicate capability id '{capability_id}' for profile '{profile_id}'"
            )
        profile_capabilities[capability_id] = capability
    return capabilities_by_profile


def _devices(config: InventoryConfigSections) -> dict[str, DeviceInstance]:
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
            enabled=_section_bool(section, "Enabled", default=True),
            notes=_optional_text(section.get("Notes")),
        )
    return devices


def _bindings(config: InventoryConfigSections) -> dict[str, RoleBinding]:
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
    config: InventoryConfigSections,
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


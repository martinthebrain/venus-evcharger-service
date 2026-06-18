# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared inventory parsing, IO, and summary helpers for the setup wizard."""

from __future__ import annotations

import argparse
import configparser
from pathlib import Path
from typing import cast

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
    parse_device_inventory_config,
    render_device_inventory_config,
)

PHASE_ORDER: tuple[PhaseLabel, ...] = ("L1", "L2", "L3")


def inventory_default_path(config_path: str) -> Path:
    """Return the default wizard inventory sidecar path for one config path."""
    target = Path(config_path)
    return target.with_name(f"{target.name}.wizard-inventory.ini")


def load_inventory(path: Path) -> DeviceInventory:
    """Load one persisted inventory config."""
    if not path.exists():
        raise ValueError(f"Inventory does not exist: {path}")
    parser = configparser.ConfigParser()
    parser.read(path, encoding="utf-8")
    return parse_device_inventory_config(parser)


def save_inventory(path: Path, inventory: DeviceInventory) -> None:
    """Persist one inventory config."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_device_inventory_config(inventory), encoding="utf-8")


def inventory_summary_payload(path: Path, inventory: DeviceInventory) -> dict[str, object]:
    """Return one compact JSON-ready inventory summary."""
    return {
        "inventory_path": str(path),
        "profiles": [_profile_payload(profile) for profile in inventory.profiles],
        "devices": [_device_payload(device) for device in inventory.devices],
        "bindings": [_binding_payload(binding) for binding in inventory.bindings],
    }


def inventory_summary_text(path: Path, inventory: DeviceInventory) -> str:
    """Render one human-readable inventory summary."""
    lines = [
        f"Inventory path: {path}",
        f"Profiles: {len(inventory.profiles)}",
        f"Devices: {len(inventory.devices)}",
        f"Bindings: {len(inventory.bindings)}",
        "Profiles:",
    ]
    lines.extend(_profile_summary_lines(inventory))
    lines.append("Device instances:")
    lines.extend(_device_summary_lines(inventory))
    lines.append("Bindings:")
    lines.extend(_binding_summary_lines(inventory))
    return "\n".join(lines)


def parse_inventory_phases(raw_value: str) -> tuple[PhaseLabel, ...]:
    """Parse one comma-separated phase list for inventory editing."""
    normalized: list[PhaseLabel] = []
    for raw_part in _phase_tokens(raw_value):
        item = raw_part.strip().upper()
        if not item:
            continue
        phase = _parse_phase_label(item, raw_part)
        if phase not in normalized:
            normalized.append(phase)
    if not normalized:
        raise ValueError("Phase list must not be empty")
    return tuple(normalized)


def _profile_payload(profile: DeviceProfile) -> dict[str, object]:
    """Return one JSON-ready payload for a device profile."""
    return {
        "id": profile.id,
        "label": profile.label,
        "vendor": profile.vendor,
        "model": profile.model,
        "capabilities": [_capability_payload(cap) for cap in profile.capabilities],
    }


def _capability_payload(capability: DeviceCapability) -> dict[str, object]:
    """Return one JSON-ready payload for a device capability."""
    return {
        "id": capability.id,
        "kind": capability.kind,
        "adapter_type": capability.adapter_type,
        "supported_phases": list(capability.supported_phases),
        "channel": capability.channel,
        "measures_power": capability.measures_power,
        "measures_energy": capability.measures_energy,
        "switching_mode": capability.switching_mode,
        "supports_feedback": capability.supports_feedback,
        "supports_phase_selection": capability.supports_phase_selection,
    }


def _device_payload(device: DeviceInstance) -> dict[str, object]:
    """Return one JSON-ready payload for a device instance."""
    return {
        "id": device.id,
        "profile_id": device.profile_id,
        "label": device.label,
        "endpoint": device.endpoint,
        "enabled": device.enabled,
    }


def _binding_payload(binding: RoleBinding) -> dict[str, object]:
    """Return one JSON-ready payload for a role binding."""
    return {
        "id": binding.id,
        "role": binding.role,
        "label": binding.label,
        "phase_scope": list(binding.phase_scope),
        "members": [_binding_member_payload(member) for member in binding.members],
    }


def _binding_member_payload(member: RoleBindingMember) -> dict[str, object]:
    """Return one JSON-ready payload for a role-binding member."""
    return {
        "device_id": member.device_id,
        "capability_id": member.capability_id,
        "phases": list(member.phases),
    }


def _profile_summary_lines(inventory: DeviceInventory) -> list[str]:
    """Return human-readable profile summary lines."""
    if not inventory.profiles:
        return ["  - none"]
    return [f"  - {profile.id}: {_profile_capability_summary(profile)}" for profile in inventory.profiles]


def _profile_capability_summary(profile: DeviceProfile) -> str:
    """Return one compact profile capability summary."""
    return ", ".join(
        f"{cap.id}/{cap.kind}@{cap.adapter_type}[{','.join(cap.supported_phases)}]"
        for cap in profile.capabilities
    )


def _device_summary_lines(inventory: DeviceInventory) -> list[str]:
    """Return human-readable device summary lines."""
    if not inventory.devices:
        return ["  - none"]
    return [
        f"  - {device.id}: profile={device.profile_id}, label={device.label}, endpoint={device.endpoint or 'n/a'}"
        for device in inventory.devices
    ]


def _binding_summary_lines(inventory: DeviceInventory) -> list[str]:
    """Return human-readable binding summary lines."""
    if not inventory.bindings:
        return ["  - none"]
    return [f"  - {binding.id}: {_binding_summary(binding)}" for binding in inventory.bindings]


def _binding_summary(binding: RoleBinding) -> str:
    """Return one compact binding summary string."""
    members = ", ".join(
        f"{member.device_id}:{member.capability_id}[{','.join(member.phases)}]"
        for member in binding.members
    )
    return f"role={binding.role}, phases={','.join(binding.phase_scope)}, members={members}"


def _phase_tokens(raw_value: str) -> list[str]:
    """Return raw CSV phase tokens from one CLI value."""
    return raw_value.split(",")


def _parse_phase_label(item: str, raw_part: str) -> PhaseLabel:
    """Parse and validate one normalized phase label."""
    if item not in PHASE_ORDER:
        raise ValueError(f"Unknown phase label: {raw_part}")
    return cast(PhaseLabel, item)


def parse_inventory_kind(raw_value: str) -> CapabilityKind:
    """Parse one capability kind from CLI input."""
    normalized = raw_value.strip().lower()
    if normalized not in {"switch", "meter", "charger"}:
        raise ValueError("Capability kind must be one of: charger, meter, switch")
    return cast(CapabilityKind, normalized)


def parse_inventory_binding_role(raw_value: str) -> BindingRole:
    """Parse one binding role from CLI input."""
    normalized = raw_value.strip().lower()
    if normalized not in {"actuation", "measurement", "charger"}:
        raise ValueError("Binding role must be one of: actuation, charger, measurement")
    return cast(BindingRole, normalized)


def parse_inventory_switching_mode(raw_value: str | None) -> SwitchingMode | None:
    """Parse one switching mode from CLI input."""
    if raw_value is None:
        return None
    normalized = raw_value.strip().lower()
    if not normalized:
        return None
    if normalized not in {"direct", "contactor"}:
        raise ValueError("Switching mode must be one of: contactor, direct")
    return cast(SwitchingMode, normalized)


def inventory_action_path(namespace: argparse.Namespace) -> Path:
    """Resolve one effective inventory path from CLI args."""
    raw = getattr(namespace, "inventory_path", None)
    if raw:
        return Path(raw)
    return inventory_default_path(str(namespace.config_path))

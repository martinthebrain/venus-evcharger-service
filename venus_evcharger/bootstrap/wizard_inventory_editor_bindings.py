# SPDX-License-Identifier: GPL-3.0-or-later
"""Binding-member editing helpers for wizard inventories."""

from __future__ import annotations

from venus_evcharger.bootstrap.wizard_inventory_support import PHASE_ORDER
from venus_evcharger.inventory import (
    BindingRole,
    DeviceInventory,
    PhaseLabel,
    RoleBinding,
    RoleBindingMember,
    validate_device_inventory,
)


def set_inventory_binding_member(
    inventory: DeviceInventory,
    *,
    binding_id: str,
    device_id: str,
    capability_id: str,
    member_phases: tuple[PhaseLabel, ...],
    role: BindingRole | None = None,
    label: str | None = None,
    phase_scope: tuple[PhaseLabel, ...] | None = None,
) -> DeviceInventory:
    """Return one inventory with one upserted binding member."""
    _require_device_exists(inventory, device_id)
    target = RoleBindingMember(
        device_id=device_id,
        capability_id=capability_id,
        phases=member_phases,
    )
    if any(binding.id == binding_id for binding in inventory.bindings):
        return _validated_inventory(
            inventory,
            bindings=tuple(
                _updated_binding_with_member(binding, target, role=role, label=label, phase_scope=phase_scope)
                if binding.id == binding_id
                else binding
                for binding in inventory.bindings
            ),
        )
    return _validated_inventory(
        inventory,
        bindings=inventory.bindings + (_new_binding_with_member(binding_id, target, role=role, label=label, phase_scope=phase_scope),),
    )


def remove_inventory_binding_member(
    inventory: DeviceInventory,
    *,
    binding_id: str,
    device_id: str,
) -> DeviceInventory:
    """Return one inventory with one binding member removed."""
    bindings = _bindings_without_member(inventory.bindings, binding_id, device_id)
    return _validated_inventory(inventory, bindings=bindings)


def _members_phase_scope(members: tuple[RoleBindingMember, ...]) -> tuple[PhaseLabel, ...]:
    covered = {
        phase
        for member in members
        for phase in member.phases
    }
    return tuple(phase for phase in PHASE_ORDER if phase in covered)


def _members_with_upserted_target(
    members: tuple[RoleBindingMember, ...],
    target: RoleBindingMember,
) -> tuple[RoleBindingMember, ...]:
    updated = list(members)
    for index, member in enumerate(updated):
        if member.device_id == target.device_id:
            updated[index] = target
            return tuple(updated)
    updated.append(target)
    return tuple(updated)


def _updated_binding_with_member(
    binding: RoleBinding,
    target: RoleBindingMember,
    *,
    role: BindingRole | None,
    label: str | None,
    phase_scope: tuple[PhaseLabel, ...] | None,
) -> RoleBinding:
    members = _members_with_upserted_target(binding.members, target)
    return RoleBinding(
        id=binding.id,
        role=role or binding.role,
        label=label or binding.label,
        phase_scope=phase_scope or _members_phase_scope(members),
        members=members,
    )


def _new_binding_with_member(
    binding_id: str,
    target: RoleBindingMember,
    *,
    role: BindingRole | None,
    label: str | None,
    phase_scope: tuple[PhaseLabel, ...] | None,
) -> RoleBinding:
    resolved_role = role or _infer_binding_role(target.capability_id)
    return RoleBinding(
        id=binding_id,
        role=resolved_role,
        label=label or _default_binding_label(binding_id),
        phase_scope=phase_scope or target.phases,
        members=(target,),
    )


def _binding_without_member(binding: RoleBinding, device_id: str) -> RoleBinding | None | RoleBinding:
    members = tuple(member for member in binding.members if member.device_id != device_id)
    if len(members) == len(binding.members):
        return binding
    if not members:
        return None
    return RoleBinding(
        id=binding.id,
        role=binding.role,
        label=binding.label,
        phase_scope=_members_phase_scope(members),
        members=members,
    )


def _bindings_without_member(
    bindings: tuple[RoleBinding, ...],
    binding_id: str,
    device_id: str,
) -> tuple[RoleBinding, ...]:
    """Return bindings with one specific member removed from one binding."""
    _require_binding_exists(bindings, binding_id)
    updated = tuple(_updated_binding_removal(binding, binding_id, device_id) for binding in bindings)
    return tuple(binding for binding in updated if binding is not None)


def _require_binding_exists(bindings: tuple[RoleBinding, ...], binding_id: str) -> None:
    """Require one known binding id in the current binding collection."""
    if any(binding.id == binding_id for binding in bindings):
        return
    raise ValueError(f"Unknown binding id: {binding_id}")


def _updated_binding_removal(
    binding: RoleBinding,
    binding_id: str,
    device_id: str,
) -> RoleBinding | None:
    """Return one binding after optionally removing one member device."""
    if binding.id != binding_id:
        return binding
    updated_binding = _binding_without_member(binding, device_id)
    if updated_binding is binding:
        raise ValueError(f"Binding {binding_id} has no member for device {device_id}")
    return updated_binding


def _validated_inventory(
    inventory: DeviceInventory,
    *,
    bindings: tuple[RoleBinding, ...],
) -> DeviceInventory:
    return validate_device_inventory(
        DeviceInventory(
            profiles=inventory.profiles,
            devices=inventory.devices,
            bindings=bindings,
        )
    )


def _require_device_exists(inventory: DeviceInventory, device_id: str) -> None:
    if any(device.id == device_id for device in inventory.devices):
        return
    raise ValueError(f"Unknown device id: {device_id}")


def _infer_binding_role(capability_id: str) -> BindingRole:
    if capability_id == "switch":
        return "actuation"
    if capability_id == "charger":
        return "charger"
    return "measurement"


def _default_binding_label(binding_id: str) -> str:
    return binding_id.replace("_", " ").replace("-", " ").title()

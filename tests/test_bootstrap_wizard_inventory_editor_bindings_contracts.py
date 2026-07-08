# SPDX-License-Identifier: GPL-3.0-or-later
"""Contracts for inventory binding-member editor helpers."""

from __future__ import annotations

import unittest

from venus_evcharger.bootstrap.wizard_inventory_editor_bindings import (
    remove_inventory_binding_member,
    set_inventory_binding_member,
)
from venus_evcharger.inventory import (
    DeviceCapability,
    DeviceInstance,
    DeviceInventory,
    DeviceProfile,
    RoleBinding,
    RoleBindingMember,
)


def _inventory() -> DeviceInventory:
    profile = DeviceProfile(
        id="multi_profile",
        label="Multi profile",
        capabilities=(
            DeviceCapability(
                id="meter",
                kind="meter",
                adapter_type="template_meter",
                supported_phases=("L1", "L2", "L3"),
                measures_power=True,
                measures_energy=True,
            ),
            DeviceCapability(
                id="switch",
                kind="switch",
                adapter_type="template_switch",
                supported_phases=("L1",),
                switching_mode="direct",
            ),
            DeviceCapability(
                id="charger",
                kind="charger",
                adapter_type="template_charger",
                supported_phases=("L1",),
            ),
        ),
    )
    return DeviceInventory(
        profiles=(profile,),
        devices=(
            DeviceInstance(id="meter_l1", profile_id="multi_profile", label="Meter L1"),
            DeviceInstance(id="meter_l2", profile_id="multi_profile", label="Meter L2"),
            DeviceInstance(id="charger", profile_id="multi_profile", label="Charger"),
        ),
        bindings=(
            RoleBinding(
                id="measurement",
                role="measurement",
                label="Measurement",
                phase_scope=("L1", "L2"),
                members=(
                    RoleBindingMember(device_id="meter_l1", capability_id="meter", phases=("L1",)),
                    RoleBindingMember(device_id="meter_l2", capability_id="meter", phases=("L2",)),
                ),
            ),
        ),
    )


class WizardInventoryEditorBindingsContractTests(unittest.TestCase):
    def test_set_inventory_binding_member_updates_existing_binding_metadata(self) -> None:
        inventory = _inventory()
        stale_role_inventory = DeviceInventory(
            profiles=inventory.profiles,
            devices=inventory.devices,
            bindings=(
                RoleBinding(
                    id="measurement",
                    role="charger",
                    label="Old label",
                    phase_scope=("L1",),
                    members=(RoleBindingMember(device_id="meter_l1", capability_id="meter", phases=("L1",)),),
                ),
            ),
        )

        updated = set_inventory_binding_member(
            stale_role_inventory,
            binding_id="measurement",
            device_id="meter_l1",
            capability_id="meter",
            member_phases=("L1", "L2"),
            role="measurement",
            label="Updated measurement",
            phase_scope=("L1", "L2"),
        )

        binding = updated.bindings[0]
        self.assertEqual(binding.id, "measurement")
        self.assertEqual(binding.role, "measurement")
        self.assertEqual(binding.label, "Updated measurement")
        self.assertEqual(binding.phase_scope, ("L1", "L2"))
        self.assertEqual(
            binding.members,
            (
                RoleBindingMember(device_id="meter_l1", capability_id="meter", phases=("L1", "L2")),
            ),
        )

        reordered_scope = set_inventory_binding_member(
            _inventory(),
            binding_id="measurement",
            device_id="meter_l1",
            capability_id="meter",
            member_phases=("L1",),
            role="measurement",
            label="Measurement",
            phase_scope=("L2", "L1"),
        )
        self.assertEqual(reordered_scope.bindings[0].phase_scope, ("L2", "L1"))

    def test_set_inventory_binding_member_creates_new_binding_with_inferred_defaults(self) -> None:
        inventory = _inventory()

        updated = set_inventory_binding_member(
            inventory,
            binding_id="charger-main",
            device_id="charger",
            capability_id="charger",
            member_phases=("L1",),
        )

        binding = updated.bindings[-1]
        self.assertEqual(binding.id, "charger-main")
        self.assertEqual(binding.role, "charger")
        self.assertEqual(binding.label, "Charger Main")
        self.assertEqual(binding.phase_scope, ("L1",))
        self.assertEqual(binding.members, (RoleBindingMember(device_id="charger", capability_id="charger", phases=("L1",)),))

    def test_set_inventory_binding_member_creates_new_binding_with_explicit_metadata(self) -> None:
        inventory = _inventory()

        updated = set_inventory_binding_member(
            inventory,
            binding_id="actuation",
            device_id="meter_l1",
            capability_id="switch",
            member_phases=("L1",),
            role="actuation",
            label="Relay output",
            phase_scope=("L1",),
        )

        binding = updated.bindings[-1]
        self.assertEqual(binding.id, "actuation")
        self.assertEqual(binding.role, "actuation")
        self.assertEqual(binding.label, "Relay output")
        self.assertEqual(binding.phase_scope, ("L1",))

        reordered_scope = set_inventory_binding_member(
            inventory,
            binding_id="measurement-reversed",
            device_id="meter_l1",
            capability_id="meter",
            member_phases=("L1", "L2"),
            role="measurement",
            label="Measurement reversed",
            phase_scope=("L2", "L1"),
        )
        self.assertEqual(reordered_scope.bindings[-1].phase_scope, ("L2", "L1"))

        with self.assertRaisesRegex(Exception, "requires capability kind 'charger'"):
            set_inventory_binding_member(
                inventory,
                binding_id="wrong-role",
                device_id="meter_l1",
                capability_id="meter",
                member_phases=("L1",),
                role="charger",
            )

    def test_remove_inventory_binding_member_preserves_remaining_binding_metadata(self) -> None:
        inventory = _inventory()

        updated = remove_inventory_binding_member(inventory, binding_id="measurement", device_id="meter_l2")

        binding = updated.bindings[0]
        self.assertEqual(binding.id, "measurement")
        self.assertEqual(binding.role, "measurement")
        self.assertEqual(binding.label, "Measurement")
        self.assertEqual(binding.phase_scope, ("L1",))
        self.assertEqual(binding.members, (RoleBindingMember(device_id="meter_l1", capability_id="meter", phases=("L1",)),))

    def test_remove_inventory_binding_member_errors_are_exact(self) -> None:
        with self.assertRaises(ValueError) as unknown:
            remove_inventory_binding_member(_inventory(), binding_id="missing", device_id="meter_l1")
        self.assertEqual(str(unknown.exception), "Unknown binding id: missing")

        with self.assertRaises(ValueError) as missing_member:
            remove_inventory_binding_member(_inventory(), binding_id="measurement", device_id="charger")
        self.assertEqual(str(missing_member.exception), "Binding measurement has no member for device charger")


if __name__ == "__main__":
    unittest.main()

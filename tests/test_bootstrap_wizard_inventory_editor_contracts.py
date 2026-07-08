# SPDX-License-Identifier: GPL-3.0-or-later
"""Contracts for inventory editor mutations."""

from __future__ import annotations

import unittest

from venus_evcharger.bootstrap.wizard_inventory_editor import (
    add_inventory_capability,
    add_inventory_device,
    add_inventory_profile,
    inventory_role_capability_choices,
    remove_inventory_binding,
    remove_inventory_device,
    set_inventory_device_endpoint,
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
    meter_profile = DeviceProfile(
        id="meter_profile",
        label="Meter profile",
        vendor="Vendor",
        model="Model",
        description="Description",
        capabilities=(
            DeviceCapability(
                id="meter",
                kind="meter",
                adapter_type="template_meter",
                supported_phases=("L1", "L2"),
                channel="power",
                measures_power=True,
                measures_energy=True,
            ),
        ),
    )
    switch_profile = DeviceProfile(
        id="switch_profile",
        label="Switch profile",
        capabilities=(
            DeviceCapability(
                id="switch",
                kind="switch",
                adapter_type="template_switch",
                supported_phases=("L1",),
                switching_mode="direct",
                supports_feedback=True,
            ),
        ),
    )
    return DeviceInventory(
        profiles=(meter_profile, switch_profile),
        devices=(
            DeviceInstance(
                id="meter_l1",
                profile_id="meter_profile",
                label="Meter L1",
                endpoint="http://meter-l1.local",
                enabled=False,
                notes="calibrated",
            ),
            DeviceInstance(id="meter_l2", profile_id="meter_profile", label="Meter L2"),
            DeviceInstance(id="switch_l1", profile_id="switch_profile", label="Switch L1"),
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


class WizardInventoryEditorContractTests(unittest.TestCase):
    def test_remove_inventory_binding_preserves_profiles_and_devices(self) -> None:
        inventory = _inventory()
        with_second_binding = DeviceInventory(
            profiles=inventory.profiles,
            devices=inventory.devices,
            bindings=inventory.bindings
            + (
                RoleBinding(
                    id="actuation",
                    role="actuation",
                    label="Actuation",
                    phase_scope=("L1",),
                    members=(RoleBindingMember(device_id="switch_l1", capability_id="switch", phases=("L1",)),),
                ),
            ),
        )

        updated = remove_inventory_binding(with_second_binding, binding_id="measurement")

        self.assertEqual(updated.profiles, with_second_binding.profiles)
        self.assertEqual(updated.devices, with_second_binding.devices)
        self.assertEqual(tuple(binding.id for binding in updated.bindings), ("actuation",))

    def test_inventory_role_capability_choices_skips_orphans_and_keeps_choice_schema(self) -> None:
        inventory = _inventory()
        with_orphan_first = DeviceInventory(
            profiles=inventory.profiles,
            devices=(DeviceInstance(id="orphan", profile_id="missing", label="Orphan"),) + inventory.devices,
            bindings=inventory.bindings,
        )

        choices = inventory_role_capability_choices(with_orphan_first, role="measurement")

        self.assertEqual(
            choices,
            (
                {
                    "device_id": "meter_l1",
                    "device_label": "Meter L1",
                    "profile_id": "meter_profile",
                    "profile_label": "Meter profile",
                    "capability_id": "meter",
                    "adapter_type": "template_meter",
                    "supported_phases": ("L1", "L2"),
                },
                {
                    "device_id": "meter_l2",
                    "device_label": "Meter L2",
                    "profile_id": "meter_profile",
                    "profile_label": "Meter profile",
                    "capability_id": "meter",
                    "adapter_type": "template_meter",
                    "supported_phases": ("L1", "L2"),
                },
            ),
        )

    def test_add_inventory_device_preserves_label_endpoint_and_existing_state(self) -> None:
        inventory = _inventory()

        updated = add_inventory_device(
            inventory,
            profile_id="meter_profile",
            device_id="meter_l3",
            label="Meter L3",
            endpoint="",
        )

        self.assertEqual(updated.profiles, inventory.profiles)
        self.assertEqual(updated.bindings, inventory.bindings)
        self.assertEqual(updated.devices[-1].id, "meter_l3")
        self.assertEqual(updated.devices[-1].profile_id, "meter_profile")
        self.assertEqual(updated.devices[-1].label, "Meter L3")
        self.assertIsNone(updated.devices[-1].endpoint)

    def test_set_inventory_device_endpoint_preserves_device_identity_and_bindings(self) -> None:
        inventory = _inventory()

        updated = set_inventory_device_endpoint(inventory, device_id="meter_l1", endpoint="http://new-meter.local")

        self.assertEqual(updated.profiles, inventory.profiles)
        self.assertEqual(updated.bindings, inventory.bindings)
        self.assertEqual(updated.devices[0].id, "meter_l1")
        self.assertEqual(updated.devices[0].profile_id, "meter_profile")
        self.assertEqual(updated.devices[0].label, "Meter L1")
        self.assertIs(updated.devices[0].enabled, False)
        self.assertEqual(updated.devices[0].notes, "calibrated")
        self.assertEqual(updated.devices[0].endpoint, "http://new-meter.local")
        self.assertEqual(updated.devices[1:], inventory.devices[1:])

    def test_add_inventory_profile_defaults_and_normalization_contract(self) -> None:
        inventory = _inventory()

        updated = add_inventory_profile(
            inventory,
            profile_id="reference_profile",
            label="Reference profile",
            capability_id="reference",
            kind="switch",
            adapter_type="template_switch",
            supported_phases=("L3",),
            vendor="",
            model="",
            description="",
            channel="",
            switching_mode="direct",
        )

        profile = updated.profiles[-1]
        capability = profile.capabilities[0]
        self.assertEqual(updated.profiles[:-1], inventory.profiles)
        self.assertEqual(profile.id, "reference_profile")
        self.assertEqual(profile.label, "Reference profile")
        self.assertIsNone(profile.vendor)
        self.assertIsNone(profile.model)
        self.assertIsNone(profile.description)
        self.assertEqual(capability.id, "reference")
        self.assertEqual(capability.kind, "switch")
        self.assertEqual(capability.adapter_type, "template_switch")
        self.assertEqual(capability.supported_phases, ("L3",))
        self.assertIsNone(capability.channel)
        self.assertIs(capability.measures_power, False)
        self.assertIs(capability.measures_energy, False)
        self.assertEqual(capability.switching_mode, "direct")
        self.assertIs(capability.supports_feedback, False)
        self.assertIs(capability.supports_phase_selection, False)

    def test_add_inventory_capability_preserves_profile_metadata_and_appends_capability(self) -> None:
        inventory = _inventory()

        updated = add_inventory_capability(
            inventory,
            profile_id="meter_profile",
            capability_id="reference",
            kind="switch",
            adapter_type="template_switch",
            supported_phases=("L3",),
            channel="relay_2",
            switching_mode="direct",
        )

        profile = updated.profiles[0]
        capability = profile.capabilities[-1]
        self.assertEqual(profile.id, "meter_profile")
        self.assertEqual(profile.label, "Meter profile")
        self.assertEqual(profile.vendor, "Vendor")
        self.assertEqual(profile.model, "Model")
        self.assertEqual(profile.description, "Description")
        self.assertEqual(profile.capabilities[:-1], inventory.profiles[0].capabilities)
        self.assertEqual(updated.profiles[1:], inventory.profiles[1:])
        self.assertEqual(capability.id, "reference")
        self.assertEqual(capability.kind, "switch")
        self.assertEqual(capability.adapter_type, "template_switch")
        self.assertEqual(capability.supported_phases, ("L3",))
        self.assertEqual(capability.channel, "relay_2")
        self.assertIs(capability.measures_power, False)
        self.assertIs(capability.measures_energy, False)
        self.assertEqual(capability.switching_mode, "direct")
        self.assertIs(capability.supports_feedback, False)
        self.assertIs(capability.supports_phase_selection, False)

        with self.assertRaises(ValueError) as error:
            add_inventory_capability(
                inventory,
                profile_id="meter_profile",
                capability_id="meter",
                kind="meter",
                adapter_type="template_meter",
                supported_phases=("L1",),
                measures_power=True,
            )
        self.assertEqual(str(error.exception), "Capability id already exists in profile meter_profile: meter")

    def test_remove_inventory_device_preserves_remaining_binding_identity(self) -> None:
        inventory = _inventory()

        updated = remove_inventory_device(inventory, device_id="meter_l2")

        self.assertEqual(updated.bindings[0].id, "measurement")
        self.assertEqual(updated.bindings[0].role, "measurement")
        self.assertEqual(updated.bindings[0].label, "Measurement")
        self.assertEqual(updated.bindings[0].phase_scope, ("L1",))
        self.assertEqual(updated.bindings[0].members, (RoleBindingMember(device_id="meter_l1", capability_id="meter", phases=("L1",)),))


if __name__ == "__main__":
    unittest.main()

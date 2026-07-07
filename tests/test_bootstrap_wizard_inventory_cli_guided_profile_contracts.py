# SPDX-License-Identifier: GPL-3.0-or-later
"""Contracts for guided inventory profile creation."""

from __future__ import annotations

import argparse
import unittest
from pathlib import Path
from unittest.mock import patch

from venus_evcharger.bootstrap.wizard_inventory_cli_guided_profile import (
    guided_inventory_add_profile,
    guided_profile_base_update,
    maybe_add_guided_device_and_binding,
)
from venus_evcharger.bootstrap.wizard_inventory_cli_guided_profile_specs import (
    guided_capability_defaults,
    guided_capability_flags,
    guided_profile_kind,
    guided_role_for_kind,
)
from venus_evcharger.inventory import DeviceCapability, DeviceInventory, DeviceProfile


def _namespace(**overrides: object) -> argparse.Namespace:
    values: dict[str, object] = {
        "inventory_kind": "switch",
        "inventory_profile_id": "switch_profile",
        "inventory_label": "Switch profile",
        "inventory_capability_id": "switch",
        "inventory_adapter_type": "template_switch",
        "inventory_supported_phases": "L1,L2",
        "inventory_measures_power": True,
        "inventory_measures_energy": True,
        "inventory_switching_mode": "direct",
        "inventory_supports_feedback": True,
        "inventory_supports_phase_selection": True,
        "inventory_vendor": "Vendor",
        "inventory_model": "Model",
        "inventory_description": "Description",
        "inventory_channel": "relay_0",
        "inventory_device_id": "switch_l1",
        "inventory_endpoint": "http://switch.local",
        "inventory_binding_id": "actuation",
        "inventory_binding_label": "Actuation",
        "inventory_member_phases": "L1",
        "_inventory_prompt_device": True,
        "_inventory_prompt_binding": True,
        "non_interactive": True,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


class WizardInventoryCliGuidedProfileContractTests(unittest.TestCase):
    def test_kind_defaults_flags_and_roles_are_stable(self) -> None:
        self.assertEqual(guided_profile_kind(_namespace(inventory_kind="meter")), "meter")
        self.assertEqual(guided_capability_defaults("switch"), ("switch", "template_switch"))
        self.assertEqual(guided_capability_defaults("meter"), ("meter", "template_meter"))
        self.assertEqual(guided_capability_defaults("charger"), ("charger", "template_charger"))
        self.assertEqual(guided_role_for_kind("switch"), "actuation")
        self.assertEqual(guided_role_for_kind("meter"), "measurement")
        self.assertEqual(guided_role_for_kind("charger"), "charger")

        self.assertEqual(
            guided_capability_flags(_namespace(inventory_kind="meter"), "meter", ("L1",)),
            {
                "measures_power": True,
                "measures_energy": True,
                "switching_mode": None,
                "supports_feedback": False,
                "supports_phase_selection": False,
            },
        )
        self.assertEqual(
            guided_capability_flags(_namespace(), "switch", ("L1", "L2")),
            {
                "measures_power": False,
                "measures_energy": False,
                "switching_mode": "direct",
                "supports_feedback": True,
                "supports_phase_selection": True,
            },
        )
        self.assertEqual(
            guided_capability_flags(_namespace(inventory_kind="charger"), "charger", ("L1", "L2", "L3")),
            {
                "measures_power": False,
                "measures_energy": False,
                "switching_mode": None,
                "supports_feedback": False,
                "supports_phase_selection": False,
            },
        )

    def test_guided_profile_base_update_creates_profile_contract(self) -> None:
        updated, profile_id, label, phases, kind, capability_id = guided_profile_base_update(_namespace(), DeviceInventory())

        self.assertEqual(profile_id, "switch_profile")
        self.assertEqual(label, "Switch profile")
        self.assertEqual(phases, ("L1", "L2"))
        self.assertEqual(kind, "switch")
        self.assertEqual(capability_id, "switch")
        self.assertEqual(updated.profiles[0].id, "switch_profile")
        self.assertEqual(updated.profiles[0].label, "Switch profile")
        self.assertEqual(updated.profiles[0].vendor, "Vendor")
        self.assertEqual(updated.profiles[0].model, "Model")
        self.assertEqual(updated.profiles[0].description, "Description")
        capability = updated.profiles[0].capabilities[0]
        self.assertEqual(capability.id, "switch")
        self.assertEqual(capability.kind, "switch")
        self.assertEqual(capability.adapter_type, "template_switch")
        self.assertEqual(capability.supported_phases, ("L1", "L2"))
        self.assertEqual(capability.channel, "relay_0")
        self.assertEqual(capability.switching_mode, "direct")
        self.assertTrue(capability.supports_feedback)
        self.assertTrue(capability.supports_phase_selection)

    def test_maybe_add_guided_device_and_binding_paths(self) -> None:
        profile = DeviceProfile(
            id="switch_profile",
            label="Switch profile",
            capabilities=(
                DeviceCapability(
                    id="switch",
                    kind="switch",
                    adapter_type="template_switch",
                    supported_phases=("L1",),
                    switching_mode="direct",
                ),
            ),
        )
        inventory = DeviceInventory(profiles=(profile,))

        skipped, device_id, binding_id = maybe_add_guided_device_and_binding(
            _namespace(_inventory_prompt_device=False),
            inventory,
            profile_id="switch_profile",
            label="Switch profile",
            capability_id="switch",
            supported_phases=("L1",),
            inferred_role="actuation",
        )
        self.assertEqual(skipped, inventory)
        self.assertIsNone(device_id)
        self.assertIsNone(binding_id)

        device_only, device_id, binding_id = maybe_add_guided_device_and_binding(
            _namespace(_inventory_prompt_binding=False),
            inventory,
            profile_id="switch_profile",
            label="Switch profile",
            capability_id="switch",
            supported_phases=("L1",),
            inferred_role="actuation",
        )
        self.assertEqual(device_id, "switch_l1")
        self.assertIsNone(binding_id)
        self.assertEqual(device_only.devices[0].endpoint, "http://switch.local")

        with_binding, device_id, binding_id = maybe_add_guided_device_and_binding(
            _namespace(),
            inventory,
            profile_id="switch_profile",
            label="Switch profile",
            capability_id="switch",
            supported_phases=("L1",),
            inferred_role="actuation",
        )
        self.assertEqual(device_id, "switch_l1")
        self.assertEqual(binding_id, "actuation")
        self.assertEqual(with_binding.bindings[0].phase_scope, ("L1",))
        self.assertEqual(with_binding.bindings[0].members[0].device_id, "switch_l1")

    def test_guided_inventory_add_profile_rejects_noninteractive_and_saves_payload(self) -> None:
        with self.assertRaisesRegex(ValueError, "guided-add-profile requires interactive input"):
            guided_inventory_add_profile(_namespace(non_interactive=True), Path("/tmp/inventory.ini"), DeviceInventory())

        namespace = _namespace(non_interactive=False)
        with (
            patch(
                "venus_evcharger.bootstrap.wizard_inventory_cli_guided_profile.guided_profile_base_update",
                return_value=(DeviceInventory(), "switch_profile", "Switch profile", ("L1",), "switch", "switch"),
            ) as base_update,
            patch(
                "venus_evcharger.bootstrap.wizard_inventory_cli_guided_profile.maybe_add_guided_device_and_binding",
                return_value=(DeviceInventory(), "switch_l1", "actuation"),
            ) as maybe_add,
            patch(
                "venus_evcharger.bootstrap.wizard_inventory_cli_guided_profile.save_and_payload",
                return_value={"ok": True},
            ) as save_payload,
        ):
            result = guided_inventory_add_profile(namespace, Path("/tmp/inventory.ini"), DeviceInventory())

        self.assertEqual(result, {"ok": True})
        base_update.assert_called_once()
        maybe_add.assert_called_once()
        save_payload.assert_called_once_with(
            "guided-add-profile",
            Path("/tmp/inventory.ini"),
            DeviceInventory(),
            profile_id="switch_profile",
            device_id="switch_l1",
            binding_id="actuation",
        )


if __name__ == "__main__":
    unittest.main()

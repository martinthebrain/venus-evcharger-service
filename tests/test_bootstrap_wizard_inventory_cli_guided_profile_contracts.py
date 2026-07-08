# SPDX-License-Identifier: GPL-3.0-or-later
"""Contracts for guided inventory profile creation."""

from __future__ import annotations

import argparse
import unittest
from pathlib import Path
from typing import cast
from unittest.mock import patch

from venus_evcharger.bootstrap.wizard_inventory_cli_guided_profile import (
    guided_binding_assignment,
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
from venus_evcharger.inventory import BindingRole, DeviceCapability, DeviceInventory, DeviceProfile, RoleBinding


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

    def test_guided_profile_base_update_calls_field_contracts(self) -> None:
        inventory = DeviceInventory()
        flags = {
            "measures_power": True,
            "measures_energy": False,
            "switching_mode": "direct",
            "supports_feedback": True,
            "supports_phase_selection": False,
        }
        updated_inventory = DeviceInventory()
        with (
            patch(
                "venus_evcharger.bootstrap.wizard_inventory_cli_guided_profile.inventory_field",
                side_effect=["profile_id", "Profile label"],
            ) as field,
            patch(
                "venus_evcharger.bootstrap.wizard_inventory_cli_guided_profile.guided_profile_kind",
                return_value="switch",
            ) as profile_kind,
            patch(
                "venus_evcharger.bootstrap.wizard_inventory_cli_guided_profile.guided_capability_defaults",
                return_value=("switch", "template_switch"),
            ) as defaults,
            patch(
                "venus_evcharger.bootstrap.wizard_inventory_cli_guided_profile.inventory_field_with_default",
                side_effect=["capability", "adapter", "L1,L2"],
            ) as field_default,
            patch(
                "venus_evcharger.bootstrap.wizard_inventory_cli_guided_profile.parse_inventory_phases",
                return_value=("L1", "L2"),
            ) as parse_phases,
            patch(
                "venus_evcharger.bootstrap.wizard_inventory_cli_guided_profile.guided_capability_flags",
                return_value=flags,
            ) as capability_flags,
            patch(
                "venus_evcharger.bootstrap.wizard_inventory_cli_guided_profile.inventory_optional_field",
                side_effect=["Vendor", "Model", "Description", "relay_0"],
            ) as optional_field,
            patch(
                "venus_evcharger.bootstrap.wizard_inventory_cli_guided_profile.add_inventory_profile",
                return_value=updated_inventory,
            ) as add_profile,
        ):
            result = guided_profile_base_update(_namespace(), inventory)

        self.assertEqual(result, (updated_inventory, "profile_id", "Profile label", ("L1", "L2"), "switch", "capability"))
        self.assertEqual(
            [call.args for call in field.call_args_list],
            [(_namespace(), "inventory_profile_id", "Profile id"), (_namespace(), "inventory_label", "Profile label")],
        )
        profile_kind.assert_called_once()
        defaults.assert_called_once_with("switch")
        self.assertEqual(
            [call.args for call in field_default.call_args_list],
            [
                (_namespace(), "inventory_capability_id", "Capability id", "switch"),
                (_namespace(), "inventory_adapter_type", "Adapter type", "template_switch"),
                (_namespace(), "inventory_supported_phases", "Supported phases", "L1"),
            ],
        )
        parse_phases.assert_called_once_with("L1,L2")
        capability_flags.assert_called_once_with(_namespace(), "switch", ("L1", "L2"))
        self.assertEqual(
            [call.args for call in optional_field.call_args_list],
            [
                (_namespace(), "inventory_vendor", "Vendor"),
                (_namespace(), "inventory_model", "Model"),
                (_namespace(), "inventory_description", "Description"),
                (_namespace(), "inventory_channel", "Channel"),
            ],
        )
        add_profile.assert_called_once_with(
            inventory,
            profile_id="profile_id",
            label="Profile label",
            capability_id="capability",
            kind="switch",
            adapter_type="adapter",
            supported_phases=("L1", "L2"),
            vendor="Vendor",
            model="Model",
            description="Description",
            channel="relay_0",
            measures_power=True,
            measures_energy=False,
            switching_mode="direct",
            supports_feedback=True,
            supports_phase_selection=False,
        )

    def test_guided_profile_base_update_uses_kind_defaults(self) -> None:
        meter_inventory, profile_id, label, phases, kind, capability_id = guided_profile_base_update(
            _namespace(
                inventory_kind="meter",
                inventory_profile_id="meter_profile",
                inventory_label="Meter profile",
                inventory_capability_id=None,
                inventory_adapter_type=None,
                inventory_supported_phases=None,
                inventory_measures_power=True,
                inventory_measures_energy=True,
                inventory_vendor=None,
                inventory_model=None,
                inventory_description=None,
                inventory_channel=None,
            ),
            DeviceInventory(),
        )

        self.assertEqual((profile_id, label, phases, kind, capability_id), ("meter_profile", "Meter profile", ("L1",), "meter", "meter"))
        meter_capability = meter_inventory.profiles[0].capabilities[0]
        self.assertEqual(meter_capability.adapter_type, "template_meter")
        self.assertTrue(meter_capability.measures_power)
        self.assertTrue(meter_capability.measures_energy)
        self.assertIsNone(meter_inventory.profiles[0].vendor)
        self.assertIsNone(meter_capability.channel)

        charger_inventory, _, _, _, kind, capability_id = guided_profile_base_update(
            _namespace(
                inventory_kind="charger",
                inventory_profile_id="charger_profile",
                inventory_label="Charger profile",
                inventory_capability_id=None,
                inventory_adapter_type=None,
                inventory_supported_phases=None,
                inventory_vendor=None,
                inventory_model=None,
                inventory_description=None,
                inventory_channel=None,
            ),
            DeviceInventory(),
        )

        charger_capability = charger_inventory.profiles[0].capabilities[0]
        self.assertEqual((kind, capability_id, charger_capability.adapter_type), ("charger", "charger", "template_charger"))
        self.assertEqual(charger_capability.supported_phases, ("L1",))
        self.assertFalse(charger_capability.measures_power)
        self.assertFalse(charger_capability.measures_energy)
        self.assertFalse(charger_capability.supports_feedback)
        self.assertFalse(charger_capability.supports_phase_selection)

    def test_guided_binding_assignment_respects_defaults_and_existing_scope(self) -> None:
        profile = DeviceProfile(
            id="meter_profile",
            label="Meter profile",
            capabilities=(
                DeviceCapability(
                    id="meter",
                    kind="meter",
                    adapter_type="template_meter",
                    supported_phases=("L1", "L2"),
                    measures_power=True,
                    measures_energy=True,
                ),
            ),
        )
        base_inventory = DeviceInventory(profiles=(profile,))
        with_device = maybe_add_guided_device_and_binding(
            _namespace(_inventory_prompt_binding=False),
            base_inventory,
            profile_id="meter_profile",
            label="Meter profile",
            capability_id="meter",
            supported_phases=("L1", "L2"),
            inferred_role="measurement",
        )[0]

        updated, binding_id = guided_binding_assignment(
            _namespace(
                inventory_binding_id=None,
                inventory_binding_label=None,
                inventory_member_phases=None,
            ),
            with_device,
            profile_id="meter_profile",
            device_id="switch_l1",
            capability_id="meter",
            supported_phases=("L1", "L2"),
            inferred_role="measurement",
        )

        self.assertEqual(binding_id, "measurement")
        self.assertEqual(updated.bindings[0].label, "Measurement")
        self.assertEqual(updated.bindings[0].phase_scope, ("L1", "L2"))
        self.assertEqual(updated.bindings[0].members[0].phases, ("L1", "L2"))

        extended, existing_binding_id = guided_binding_assignment(
            _namespace(
                inventory_binding_id="measurement",
                inventory_binding_label=None,
                inventory_member_phases="L1",
                inventory_device_id="meter_l2",
            ),
            updated,
            profile_id="meter_profile",
            device_id="switch_l1",
            capability_id="meter",
            supported_phases=("L1", "L2"),
            inferred_role="measurement",
        )

        self.assertEqual(existing_binding_id, "measurement")
        self.assertEqual(extended.bindings[0].phase_scope, ("L1",))
        self.assertEqual(extended.bindings[0].members[0].phases, ("L1",))

    def test_guided_binding_assignment_calls_field_contracts(self) -> None:
        inventory = DeviceInventory()
        updated_inventory = DeviceInventory()
        with (
            patch(
                "venus_evcharger.bootstrap.wizard_inventory_cli_guided_profile.default_binding_id",
                return_value="measurement",
            ) as default_id,
            patch(
                "venus_evcharger.bootstrap.wizard_inventory_cli_guided_profile.inventory_field_with_default",
                side_effect=["measurement", "Measurement", "L1,L2"],
            ) as field_default,
            patch(
                "venus_evcharger.bootstrap.wizard_inventory_cli_guided_profile.parse_inventory_phases",
                return_value=("L1", "L2"),
            ) as parse_phases,
            patch(
                "venus_evcharger.bootstrap.wizard_inventory_cli_guided_profile.parse_inventory_binding_role",
                return_value="measurement",
            ) as parse_role,
            patch(
                "venus_evcharger.bootstrap.wizard_inventory_cli_guided_profile.set_inventory_binding_member",
                return_value=updated_inventory,
            ) as set_member,
        ):
            result = guided_binding_assignment(
                _namespace(),
                inventory,
                profile_id="meter_profile",
                device_id="meter_l1",
                capability_id="meter",
                supported_phases=("L1", "L2"),
                inferred_role="measurement",
            )

        self.assertEqual(result, (updated_inventory, "measurement"))
        default_id.assert_called_once_with(inventory, "meter_profile", "measurement")
        self.assertEqual(
            [call.args for call in field_default.call_args_list],
            [
                (_namespace(), "inventory_binding_id", "Binding id", "measurement"),
                (_namespace(), "inventory_binding_label", "Binding label", "Measurement"),
                (_namespace(), "inventory_member_phases", "Binding phases", "L1,L2"),
            ],
        )
        parse_phases.assert_called_once_with("L1,L2")
        parse_role.assert_called_once_with("measurement")
        set_member.assert_called_once_with(
            inventory,
            binding_id="measurement",
            device_id="meter_l1",
            capability_id="meter",
            member_phases=("L1", "L2"),
            role="measurement",
            label="Measurement",
            phase_scope=("L1", "L2"),
        )

    def test_guided_binding_assignment_uses_role_label_and_existing_scope_contracts(self) -> None:
        updated_inventory = DeviceInventory()
        role_with_separator = cast(BindingRole, "charge_limit")
        with (
            patch(
                "venus_evcharger.bootstrap.wizard_inventory_cli_guided_profile.parse_inventory_phases",
                return_value=("L1",),
            ),
            patch(
                "venus_evcharger.bootstrap.wizard_inventory_cli_guided_profile.parse_inventory_binding_role",
                return_value="charger",
            ),
            patch(
                "venus_evcharger.bootstrap.wizard_inventory_cli_guided_profile.set_inventory_binding_member",
                return_value=updated_inventory,
            ) as default_label_member,
        ):
            result = guided_binding_assignment(
                _namespace(inventory_binding_id="charge_limit", inventory_binding_label=None, inventory_member_phases="L1"),
                DeviceInventory(),
                profile_id="charger_profile",
                device_id="charger_device",
                capability_id="charger",
                supported_phases=("L1",),
                inferred_role=role_with_separator,
            )

        self.assertEqual(result, (updated_inventory, "charge_limit"))
        self.assertEqual(default_label_member.call_args.kwargs["label"], "Charge Limit")

        with (
            patch(
                "venus_evcharger.bootstrap.wizard_inventory_cli_guided_profile.inventory_field_with_default",
                side_effect=["charge_limit", "Charge Limit", "L1"],
            ),
            patch(
                "venus_evcharger.bootstrap.wizard_inventory_cli_guided_profile.parse_inventory_phases",
                return_value=("L1",),
            ),
            patch(
                "venus_evcharger.bootstrap.wizard_inventory_cli_guided_profile.parse_inventory_binding_role",
                return_value="charger",
            ),
            patch(
                "venus_evcharger.bootstrap.wizard_inventory_cli_guided_profile.set_inventory_binding_member",
                return_value=updated_inventory,
            ) as set_member,
        ):
            result = guided_binding_assignment(
                _namespace(),
                DeviceInventory(),
                profile_id="charger_profile",
                device_id="charger_device",
                capability_id="charger",
                supported_phases=("L1",),
                inferred_role=role_with_separator,
            )

        self.assertEqual(result, (updated_inventory, "charge_limit"))
        self.assertEqual(set_member.call_args.kwargs["label"], "Charge Limit")
        self.assertEqual(set_member.call_args.kwargs["phase_scope"], ("L1",))

        existing_inventory = DeviceInventory(
            bindings=(RoleBinding(id="measurement", role="measurement", label="Measurement", phase_scope=("L1",), members=()),)
        )
        with (
            patch(
                "venus_evcharger.bootstrap.wizard_inventory_cli_guided_profile.inventory_field_with_default",
                side_effect=["measurement", "Measurement", "L1"],
            ),
            patch(
                "venus_evcharger.bootstrap.wizard_inventory_cli_guided_profile.parse_inventory_phases",
                return_value=("L1",),
            ),
            patch(
                "venus_evcharger.bootstrap.wizard_inventory_cli_guided_profile.parse_inventory_binding_role",
                return_value="measurement",
            ),
            patch(
                "venus_evcharger.bootstrap.wizard_inventory_cli_guided_profile.set_inventory_binding_member",
                return_value=updated_inventory,
            ) as set_existing_member,
        ):
            result = guided_binding_assignment(
                _namespace(),
                existing_inventory,
                profile_id="meter_profile",
                device_id="meter_l1",
                capability_id="meter",
                supported_phases=("L1",),
                inferred_role="measurement",
            )

        self.assertEqual(result, (updated_inventory, "measurement"))
        self.assertIsNone(set_existing_member.call_args.kwargs["phase_scope"])

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

    def test_maybe_add_guided_device_and_binding_calls_field_contracts(self) -> None:
        source_inventory = DeviceInventory()
        device_inventory = DeviceInventory()
        bound_inventory = DeviceInventory()
        with (
            patch(
                "venus_evcharger.bootstrap.wizard_inventory_cli_guided_profile.inventory_bool_field",
                side_effect=[True, True],
            ) as bool_field,
            patch(
                "venus_evcharger.bootstrap.wizard_inventory_cli_guided_profile.inventory_field_with_default",
                side_effect=["meter_l1", "Meter L1"],
            ) as field_default,
            patch(
                "venus_evcharger.bootstrap.wizard_inventory_cli_guided_profile.inventory_optional_field",
                return_value="http://meter.local",
            ) as optional_field,
            patch(
                "venus_evcharger.bootstrap.wizard_inventory_cli_guided_profile.add_inventory_device",
                return_value=device_inventory,
            ) as add_device,
            patch(
                "venus_evcharger.bootstrap.wizard_inventory_cli_guided_profile.guided_binding_assignment",
                return_value=(bound_inventory, "measurement"),
            ) as binding_assignment,
        ):
            result = maybe_add_guided_device_and_binding(
                _namespace(),
                source_inventory,
                profile_id="meter_profile",
                label="Meter profile",
                capability_id="meter",
                supported_phases=("L1",),
                inferred_role="measurement",
            )

        self.assertEqual(result, (bound_inventory, "meter_l1", "measurement"))
        self.assertEqual(
            [call.args for call in bool_field.call_args_list],
            [
                (_namespace(), "_inventory_prompt_device", "Add one device instance for this profile now", True),
                (_namespace(), "_inventory_prompt_binding", "Assign this device capability to one role now", True),
            ],
        )
        self.assertEqual(
            [call.args for call in field_default.call_args_list],
            [
                (_namespace(), "inventory_device_id", "Device id", "meter_profile_device"),
                (_namespace(), "inventory_label", "Device label", "Meter profile device"),
            ],
        )
        optional_field.assert_called_once_with(_namespace(), "inventory_endpoint", "Device endpoint")
        add_device.assert_called_once_with(
            source_inventory,
            profile_id="meter_profile",
            device_id="meter_l1",
            label="Meter L1",
            endpoint="http://meter.local",
        )
        binding_assignment.assert_called_once_with(
            _namespace(),
            device_inventory,
            profile_id="meter_profile",
            device_id="meter_l1",
            capability_id="meter",
            supported_phases=("L1",),
            inferred_role="measurement",
        )

    def test_guided_inventory_add_profile_rejects_noninteractive_and_saves_payload(self) -> None:
        with self.assertRaises(ValueError) as error:
            guided_inventory_add_profile(_namespace(non_interactive=True), Path("/tmp/inventory.ini"), DeviceInventory())
        self.assertEqual(str(error.exception), "guided-add-profile requires interactive input")

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
                "venus_evcharger.bootstrap.wizard_inventory_cli_guided_profile.guided_role_for_kind",
                return_value="actuation",
            ) as role_for_kind,
            patch(
                "venus_evcharger.bootstrap.wizard_inventory_cli_guided_profile.save_and_payload",
                return_value={"ok": True},
            ) as save_payload,
        ):
            result = guided_inventory_add_profile(namespace, Path("/tmp/inventory.ini"), DeviceInventory())

        self.assertEqual(result, {"ok": True})
        base_update.assert_called_once_with(namespace, DeviceInventory())
        role_for_kind.assert_called_once_with("switch")
        maybe_add.assert_called_once_with(
            namespace,
            DeviceInventory(),
            profile_id="switch_profile",
            label="Switch profile",
            capability_id="switch",
            supported_phases=("L1",),
            inferred_role="actuation",
        )
        save_payload.assert_called_once_with(
            "guided-add-profile",
            Path("/tmp/inventory.ini"),
            DeviceInventory(),
            profile_id="switch_profile",
            device_id="switch_l1",
            binding_id="actuation",
        )

    def test_guided_inventory_add_profile_allows_missing_noninteractive_attribute(self) -> None:
        namespace = argparse.Namespace()
        with (
            patch(
                "venus_evcharger.bootstrap.wizard_inventory_cli_guided_profile.guided_profile_base_update",
                return_value=(DeviceInventory(), "meter_profile", "Meter profile", ("L1",), "meter", "meter"),
            ),
            patch(
                "venus_evcharger.bootstrap.wizard_inventory_cli_guided_profile.maybe_add_guided_device_and_binding",
                return_value=(DeviceInventory(), "meter_l1", "measurement"),
            ),
            patch(
                "venus_evcharger.bootstrap.wizard_inventory_cli_guided_profile.save_and_payload",
                return_value={"ok": True},
            ),
        ):
            result = guided_inventory_add_profile(namespace, Path("/tmp/inventory.ini"), DeviceInventory())

        self.assertEqual(result, {"ok": True})


if __name__ == "__main__":
    unittest.main()

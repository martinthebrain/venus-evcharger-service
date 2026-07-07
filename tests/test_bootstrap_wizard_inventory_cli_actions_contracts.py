# SPDX-License-Identifier: GPL-3.0-or-later
"""Contracts for simple inventory editor action handlers."""

from __future__ import annotations

import argparse
import unittest
from pathlib import Path
from unittest.mock import sentinel, patch

from venus_evcharger.bootstrap.wizard_inventory_cli_actions import (
    _ALL_FIELD_SPECS,
    _BINDING_LABEL,
    _MEASURES_POWER,
    _PROFILE_ID,
    _VENDOR,
    _bool_field,
    _inventory_capability_fields,
    _optional_field,
    _optional_namespace_text,
    _required_field,
    run_simple_inventory_action,
)
from venus_evcharger.inventory import DeviceInventory


def _namespace(**overrides: object) -> argparse.Namespace:
    values: dict[str, object] = {
        "inventory_action": "add-profile",
        "inventory_profile_id": "profile_1",
        "inventory_label": "Profile 1",
        "inventory_capability_id": "cap_1",
        "inventory_kind": "switch",
        "inventory_adapter_type": "template_switch",
        "inventory_supported_phases": "L1,L2",
        "inventory_channel": "relay_0",
        "inventory_switching_mode": "direct",
        "inventory_measures_power": True,
        "inventory_measures_energy": True,
        "inventory_supports_feedback": True,
        "inventory_supports_phase_selection": True,
        "inventory_vendor": "Vendor",
        "inventory_model": "Model",
        "inventory_description": "Description",
        "inventory_device_id": "device_1",
        "inventory_endpoint": "http://device.local",
        "inventory_binding_id": "binding_1",
        "inventory_binding_role": "measurement",
        "inventory_binding_label": "Binding 1",
        "inventory_binding_phase_scope": "L1,L2",
        "inventory_member_phases": "L2",
        "non_interactive": True,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


class WizardInventoryCliActionsContractTests(unittest.TestCase):
    def test_field_specs_are_the_cli_string_contract(self) -> None:
        self.assertEqual(
            [(spec.attr, spec.prompt) for spec in _ALL_FIELD_SPECS],
            [
                ("inventory_profile_id", "Profile id"),
                ("inventory_label", "Profile label"),
                ("inventory_label", "Device label"),
                ("inventory_capability_id", "Capability id"),
                ("inventory_kind", "Capability kind"),
                ("inventory_adapter_type", "Adapter type"),
                ("inventory_supported_phases", "Supported phases"),
                ("inventory_channel", "Channel"),
                ("inventory_measures_power", "Measures power"),
                ("inventory_measures_energy", "Measures energy"),
                ("inventory_switching_mode", "Switching mode"),
                ("inventory_supports_feedback", "Supports feedback"),
                ("inventory_supports_phase_selection", "Supports phase selection"),
                ("inventory_vendor", "Vendor"),
                ("inventory_model", "Model"),
                ("inventory_description", "Description"),
                ("inventory_device_id", "Device id"),
                ("inventory_endpoint", "Device endpoint"),
                ("inventory_binding_id", "Binding id"),
                ("inventory_member_phases", "Member phases"),
                ("inventory_binding_role", "Binding role"),
                ("inventory_binding_label", "Binding label"),
                ("inventory_binding_phase_scope", "Binding phase scope"),
            ],
        )

    def test_capability_fields_are_normalized_from_namespace(self) -> None:
        self.assertEqual(
            _inventory_capability_fields(_namespace()),
            {
                "channel": "relay_0",
                "measures_power": True,
                "measures_energy": True,
                "switching_mode": "direct",
                "supports_feedback": True,
                "supports_phase_selection": True,
            },
        )
        self.assertEqual(
            _inventory_capability_fields(
                _namespace(
                    inventory_channel=" ",
                    inventory_switching_mode=" ",
                    inventory_measures_power=False,
                    inventory_measures_energy=False,
                    inventory_supports_feedback=False,
                    inventory_supports_phase_selection=False,
                )
            ),
            {
                "channel": None,
                "measures_power": False,
                "measures_energy": False,
                "switching_mode": None,
                "supports_feedback": False,
                "supports_phase_selection": False,
            },
        )

    def test_field_wrappers_forward_exact_attr_and_prompt(self) -> None:
        namespace = _namespace()
        with patch("venus_evcharger.bootstrap.wizard_inventory_cli_actions.inventory_field", return_value="profile") as field:
            self.assertEqual(_required_field(namespace, _PROFILE_ID), "profile")
        field.assert_called_once_with(namespace, "inventory_profile_id", "Profile id")

        with patch("venus_evcharger.bootstrap.wizard_inventory_cli_actions.inventory_optional_field", return_value="Vendor") as optional:
            self.assertEqual(_optional_field(namespace, _VENDOR), "Vendor")
        optional.assert_called_once_with(namespace, "inventory_vendor", "Vendor")

        with patch("venus_evcharger.bootstrap.wizard_inventory_cli_actions.inventory_bool_field", return_value=True) as bool_field:
            self.assertTrue(_bool_field(namespace, _MEASURES_POWER))
        bool_field.assert_called_once_with(namespace, "inventory_measures_power", "Measures power")

        self.assertEqual(_optional_namespace_text(_namespace(inventory_binding_label="  Label  "), _BINDING_LABEL), "Label")
        self.assertIsNone(_optional_namespace_text(_namespace(inventory_binding_label="  "), _BINDING_LABEL))
        self.assertIsNone(_optional_namespace_text(_namespace(inventory_binding_label=None), _BINDING_LABEL))
        self.assertIsNone(_optional_namespace_text(argparse.Namespace(), _BINDING_LABEL))

    def test_add_profile_action_passes_full_profile_contract(self) -> None:
        namespace = _namespace()
        path = Path("/tmp/inventory.ini")
        inventory = DeviceInventory()
        with (
            patch("venus_evcharger.bootstrap.wizard_inventory_cli_actions.add_inventory_profile", return_value=sentinel.updated) as add_profile,
            patch("venus_evcharger.bootstrap.wizard_inventory_cli_actions.save_and_payload", return_value={"ok": True}) as save_payload,
        ):
            result = run_simple_inventory_action("add-profile", namespace, path, inventory)
        self.assertEqual(result, {"ok": True})
        add_profile.assert_called_once_with(
            inventory,
            profile_id="profile_1",
            label="Profile 1",
            capability_id="cap_1",
            kind="switch",
            adapter_type="template_switch",
            supported_phases=("L1", "L2"),
            vendor="Vendor",
            model="Model",
            description="Description",
            channel="relay_0",
            measures_power=True,
            measures_energy=True,
            switching_mode="direct",
            supports_feedback=True,
            supports_phase_selection=True,
        )
        save_payload.assert_called_once_with("add-profile", path, sentinel.updated, profile_id="profile_1")

    def test_add_capability_action_passes_capability_contract(self) -> None:
        namespace = _namespace(inventory_action="add-capability", inventory_kind="meter", inventory_adapter_type="template_meter")
        path = Path("/tmp/inventory.ini")
        inventory = DeviceInventory()
        with (
            patch("venus_evcharger.bootstrap.wizard_inventory_cli_actions.add_inventory_capability", return_value=sentinel.updated) as add_capability,
            patch("venus_evcharger.bootstrap.wizard_inventory_cli_actions.save_and_payload", return_value={"ok": True}) as save_payload,
        ):
            result = run_simple_inventory_action("add-capability", namespace, path, inventory)
        self.assertEqual(result, {"ok": True})
        add_capability.assert_called_once_with(
            inventory,
            profile_id="profile_1",
            capability_id="cap_1",
            kind="meter",
            adapter_type="template_meter",
            supported_phases=("L1", "L2"),
            channel="relay_0",
            measures_power=True,
            measures_energy=True,
            switching_mode="direct",
            supports_feedback=True,
            supports_phase_selection=True,
        )
        save_payload.assert_called_once_with("add-capability", path, sentinel.updated, profile_id="profile_1", capability_id="cap_1")

    def test_device_endpoint_and_remove_actions_delegate_with_ids(self) -> None:
        path = Path("/tmp/inventory.ini")
        inventory = DeviceInventory()
        cases = (
            (
                "add-device",
                "add_inventory_device",
                _namespace(inventory_action="add-device"),
                {
                    "profile_id": "profile_1",
                    "device_id": "device_1",
                    "label": "Profile 1",
                    "endpoint": "http://device.local",
                },
                {"device_id": "device_1"},
            ),
            (
                "remove-device",
                "remove_inventory_device",
                _namespace(inventory_action="remove-device"),
                {"device_id": "device_1"},
                {"device_id": "device_1"},
            ),
            (
                "set-endpoint",
                "set_inventory_device_endpoint",
                _namespace(inventory_action="set-endpoint"),
                {"device_id": "device_1", "endpoint": "http://device.local"},
                {"device_id": "device_1", "endpoint": "http://device.local"},
            ),
        )
        for action, editor_name, namespace, editor_kwargs, payload_kwargs in cases:
            with (
                patch(f"venus_evcharger.bootstrap.wizard_inventory_cli_actions.{editor_name}", return_value=sentinel.updated) as editor,
                patch("venus_evcharger.bootstrap.wizard_inventory_cli_actions.save_and_payload", return_value={"action": action}) as save_payload,
            ):
                result = run_simple_inventory_action(action, namespace, path, inventory)
            self.assertEqual(result, {"action": action})
            editor.assert_called_once_with(inventory, **editor_kwargs)
            save_payload.assert_called_once_with(action, path, sentinel.updated, **payload_kwargs)

    def test_binding_member_actions_delegate_with_binding_contract(self) -> None:
        path = Path("/tmp/inventory.ini")
        inventory = DeviceInventory()
        namespace = _namespace(inventory_action="set-binding-member")
        with (
            patch("venus_evcharger.bootstrap.wizard_inventory_cli_actions.set_inventory_binding_member", return_value=sentinel.updated) as set_member,
            patch("venus_evcharger.bootstrap.wizard_inventory_cli_actions.save_and_payload", return_value={"ok": "set"}) as save_payload,
        ):
            result = run_simple_inventory_action("set-binding-member", namespace, path, inventory)
        self.assertEqual(result, {"ok": "set"})
        set_member.assert_called_once_with(
            inventory,
            binding_id="binding_1",
            device_id="device_1",
            capability_id="cap_1",
            member_phases=("L2",),
            role="measurement",
            label="Binding 1",
            phase_scope=("L1", "L2"),
        )
        save_payload.assert_called_once_with("set-binding-member", path, sentinel.updated, binding_id="binding_1", device_id="device_1")

        clear_namespace = _namespace(
            inventory_action="set-binding-member",
            inventory_binding_role=None,
            inventory_binding_label=" ",
            inventory_binding_phase_scope=None,
        )
        with (
            patch("venus_evcharger.bootstrap.wizard_inventory_cli_actions.set_inventory_binding_member", return_value=sentinel.updated) as set_member_clear,
            patch("venus_evcharger.bootstrap.wizard_inventory_cli_actions.save_and_payload", return_value={"ok": "clear"}),
        ):
            result = run_simple_inventory_action("set-binding-member", clear_namespace, path, inventory)
        self.assertEqual(result, {"ok": "clear"})
        self.assertIsNone(set_member_clear.call_args.kwargs["role"])
        self.assertIsNone(set_member_clear.call_args.kwargs["label"])
        self.assertIsNone(set_member_clear.call_args.kwargs["phase_scope"])

        with (
            patch("venus_evcharger.bootstrap.wizard_inventory_cli_actions.remove_inventory_binding_member", return_value=sentinel.updated) as remove_member,
            patch("venus_evcharger.bootstrap.wizard_inventory_cli_actions.save_and_payload", return_value={"ok": "remove"}) as remove_payload,
        ):
            result = run_simple_inventory_action("remove-binding-member", _namespace(inventory_action="remove-binding-member"), path, inventory)
        self.assertEqual(result, {"ok": "remove"})
        remove_member.assert_called_once_with(inventory, binding_id="binding_1", device_id="device_1")
        remove_payload.assert_called_once_with("remove-binding-member", path, sentinel.updated, binding_id="binding_1", device_id="device_1")

    def test_unsupported_action_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "Unsupported inventory action: unsupported"):
            run_simple_inventory_action("unsupported", _namespace(inventory_action="unsupported"), Path("/tmp/inventory.ini"), DeviceInventory())


if __name__ == "__main__":
    unittest.main()

# SPDX-License-Identifier: GPL-3.0-or-later
"""Contracts for guided inventory role binding edits."""

from __future__ import annotations

import argparse
import unittest
from pathlib import Path
from unittest.mock import patch

from venus_evcharger.bootstrap.wizard_inventory_cli_guided_binding import (
    binding_member_phases,
    extend_guided_binding,
    guided_inventory_edit_binding,
    maybe_replace_binding,
    prompt_binding_choice,
    selected_binding_choice,
    set_guided_binding_member,
    validated_guided_binding,
)
from venus_evcharger.bootstrap.wizard_inventory_types import InventoryCapabilityChoice
from venus_evcharger.inventory import (
    DeviceCapability,
    DeviceInstance,
    DeviceInventory,
    DeviceProfile,
    RoleBinding,
    RoleBindingMember,
)


def _namespace(**overrides: object) -> argparse.Namespace:
    values: dict[str, object] = {
        "inventory_binding_role": "measurement",
        "inventory_binding_id": None,
        "inventory_binding_label": None,
        "inventory_binding_phase_scope": None,
        "inventory_member_phases": None,
        "_inventory_replace_binding": False,
        "_inventory_add_binding_member": False,
        "non_interactive": True,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def _choice(
    *,
    device_id: str = "meter_l1",
    label: str = "Meter L1",
    phases: tuple[str, ...] = ("L1", "L2"),
) -> InventoryCapabilityChoice:
    return {
        "device_id": device_id,
        "device_label": label,
        "profile_id": "meter_profile",
        "profile_label": "Meter profile",
        "capability_id": "meter",
        "adapter_type": "template_meter",
        "supported_phases": phases,
    }


def _inventory() -> DeviceInventory:
    profile = DeviceProfile(
        id="meter_profile",
        label="Meter profile",
        capabilities=(
            DeviceCapability(
                id="meter",
                kind="meter",
                adapter_type="template_meter",
                supported_phases=("L1", "L2", "L3"),
                measures_power=True,
                measures_energy=True,
            ),
        ),
    )
    return DeviceInventory(
        profiles=(profile,),
        devices=(
            DeviceInstance(id="meter_l1", profile_id="meter_profile", label="Meter L1"),
            DeviceInstance(id="meter_l2", profile_id="meter_profile", label="Meter L2"),
        ),
        bindings=(
            RoleBinding(
                id="measurement",
                role="measurement",
                label="Existing measurement",
                phase_scope=("L1", "L2"),
                members=(
                    RoleBindingMember(device_id="meter_l1", capability_id="meter", phases=("L1",)),
                    RoleBindingMember(device_id="meter_l2", capability_id="meter", phases=("L2",)),
                ),
            ),
        ),
    )


class WizardInventoryCliGuidedBindingContractTests(unittest.TestCase):
    def test_prompt_binding_choice_uses_defaults_and_existing_binding(self) -> None:
        binding_id, existing, label, scope = prompt_binding_choice(
            _namespace(inventory_binding_id="measurement"),
            _inventory(),
            "measurement",
        )

        self.assertEqual(binding_id, "measurement")
        self.assertIsNotNone(existing)
        self.assertEqual(label, "Existing measurement")
        self.assertEqual(scope, ("L1", "L2"))

        inventory_with_default_collision = DeviceInventory(
            profiles=_inventory().profiles,
            devices=_inventory().devices,
            bindings=_inventory().bindings
            + (
                RoleBinding(
                    id="measurement_measurement",
                    role="measurement",
                    label="Collision",
                    phase_scope=("L3",),
                    members=(),
                ),
            ),
        )
        binding_id, existing, label, scope = prompt_binding_choice(
            _namespace(inventory_binding_id=None, inventory_binding_label=None, inventory_binding_phase_scope=None),
            inventory_with_default_collision,
            "measurement",
        )

        self.assertEqual(binding_id, "measurement_group")
        self.assertIsNone(existing)
        self.assertEqual(label, "Measurement")
        self.assertEqual(scope, ("L1",))

    def test_prompt_binding_choice_calls_field_contracts(self) -> None:
        inventory = _inventory()
        with (
            patch(
                "venus_evcharger.bootstrap.wizard_inventory_cli_guided_binding.default_binding_id",
                return_value="measurement",
            ) as default_id,
            patch(
                "venus_evcharger.bootstrap.wizard_inventory_cli_guided_binding.inventory_field_with_default",
                side_effect=["measurement", "Measurement label", "L1,L2"],
            ) as field_default,
            patch(
                "venus_evcharger.bootstrap.wizard_inventory_cli_guided_binding.binding_label_default",
                return_value="Existing label",
            ) as label_default,
            patch(
                "venus_evcharger.bootstrap.wizard_inventory_cli_guided_binding.default_binding_scope_text",
                return_value="L1,L2",
            ) as scope_default,
            patch(
                "venus_evcharger.bootstrap.wizard_inventory_cli_guided_binding.parse_inventory_phases",
                return_value=("L1", "L2"),
            ) as parse_phases,
        ):
            result = prompt_binding_choice(_namespace(), inventory, "measurement")

        self.assertEqual(result, ("measurement", inventory.bindings[0], "Measurement label", ("L1", "L2")))
        default_id.assert_called_once_with(inventory, "measurement", "measurement")
        label_default.assert_called_once_with(inventory.bindings[0], "measurement")
        scope_default.assert_called_once_with(inventory.bindings[0])
        parse_phases.assert_called_once_with("L1,L2")
        self.assertEqual(
            [call.args for call in field_default.call_args_list],
            [
                (_namespace(), "inventory_binding_id", "Binding id", "measurement_group"),
                (_namespace(), "inventory_binding_label", "Binding label", "Existing label"),
                (_namespace(), "inventory_binding_phase_scope", "Binding phase scope", "L1,L2"),
            ],
        )

    def test_maybe_replace_binding_contract(self) -> None:
        inventory = _inventory()

        untouched, existing = maybe_replace_binding(
            _namespace(_inventory_replace_binding=False),
            inventory,
            inventory.bindings[0],
            "measurement",
        )
        self.assertEqual(untouched, inventory)
        self.assertEqual(existing, inventory.bindings[0])

        replaced, existing = maybe_replace_binding(
            _namespace(_inventory_replace_binding=True),
            inventory,
            inventory.bindings[0],
            "measurement",
        )
        self.assertEqual(replaced.bindings, ())
        self.assertIsNone(existing)

        created, existing = maybe_replace_binding(_namespace(), inventory, None, "new_measurement")
        self.assertEqual(created, inventory)
        self.assertIsNone(existing)

    def test_maybe_replace_binding_calls_prompt_contract(self) -> None:
        inventory = _inventory()
        with patch(
            "venus_evcharger.bootstrap.wizard_inventory_cli_guided_binding.inventory_bool_field",
            return_value=False,
        ) as bool_field:
            untouched, existing = maybe_replace_binding(_namespace(), inventory, inventory.bindings[0], "measurement")

        self.assertEqual(untouched, inventory)
        self.assertEqual(existing, inventory.bindings[0])
        bool_field.assert_called_once_with(
            _namespace(),
            "_inventory_replace_binding",
            "Replace existing binding members",
            False,
        )

    def test_selected_binding_choice_contract(self) -> None:
        choices = (_choice(device_id="meter_l1"), _choice(device_id="meter_l2", label="Meter L2"))

        with patch("builtins.input", return_value="") as input_prompt:
            self.assertEqual(selected_binding_choice(choices)["device_id"], "meter_l1")
        input_prompt.assert_called_once_with("Select member [1]: ")
        with patch("builtins.input", return_value="2"):
            self.assertEqual(selected_binding_choice(choices)["device_id"], "meter_l2")
        with patch("builtins.input", return_value="0"):
            with self.assertRaises(ValueError) as error:
                selected_binding_choice(choices)
            self.assertEqual(str(error.exception), "Selected binding member is out of range")
        with patch("builtins.input", return_value="3"):
            with self.assertRaises(ValueError) as error:
                selected_binding_choice(choices)
            self.assertEqual(str(error.exception), "Selected binding member is out of range")

    def test_binding_member_phases_contract(self) -> None:
        self.assertEqual(
            binding_member_phases(_namespace(), _choice(phases=("L2", "L3")), ("L1", "L2")),
            ("L2",),
        )
        self.assertEqual(
            binding_member_phases(_namespace(), _choice(phases=("L2", "L3")), ("L1",)),
            ("L2", "L3"),
        )
        self.assertEqual(
            binding_member_phases(_namespace(inventory_member_phases="L3"), _choice(phases=("L1", "L2")), ("L1", "L2")),
            ("L3",),
        )

    def test_binding_member_phases_calls_field_contract(self) -> None:
        selected = _choice(device_id="meter_l2", label="Meter L2", phases=("L2", "L3"))
        with (
            patch(
                "venus_evcharger.bootstrap.wizard_inventory_cli_guided_binding.inventory_field_with_default",
                return_value="L2",
            ) as field_default,
            patch(
                "venus_evcharger.bootstrap.wizard_inventory_cli_guided_binding.parse_inventory_phases",
                return_value=("L2",),
            ) as parse_phases,
        ):
            result = binding_member_phases(_namespace(), selected, ("L1", "L2"))

        self.assertEqual(result, ("L2",))
        field_default.assert_called_once_with(_namespace(), "inventory_member_phases", "Phases for meter_l2", "L2")
        parse_phases.assert_called_once_with("L2")

    def test_set_guided_binding_member_contract(self) -> None:
        created = set_guided_binding_member(
            DeviceInventory(profiles=_inventory().profiles, devices=_inventory().devices),
            binding_id="measurement",
            binding_label="Measurement",
            role="measurement",
            existing_binding=None,
            first_member=True,
            selected=_choice(device_id="meter_l1"),
            member_phases=("L1",),
        )

        self.assertEqual(created.bindings[0].phase_scope, ("L1",))
        self.assertEqual(created.bindings[0].members[0].device_id, "meter_l1")

        extended = set_guided_binding_member(
            _inventory(),
            binding_id="measurement",
            binding_label="Measurement",
            role="measurement",
            existing_binding=_inventory().bindings[0],
            first_member=False,
            selected=_choice(device_id="meter_l2"),
            member_phases=("L2",),
        )

        self.assertEqual(extended.bindings[0].phase_scope, ("L1", "L2"))
        self.assertEqual(tuple(member.device_id for member in extended.bindings[0].members), ("meter_l1", "meter_l2"))

    def test_set_guided_binding_member_calls_editor_contract(self) -> None:
        inventory = _inventory()
        selected = _choice(device_id="meter_l2")
        updated = DeviceInventory()
        with (
            patch(
                "venus_evcharger.bootstrap.wizard_inventory_cli_guided_binding.parse_inventory_binding_role",
                return_value="measurement",
            ) as parse_role,
            patch(
                "venus_evcharger.bootstrap.wizard_inventory_cli_guided_binding.set_inventory_binding_member",
                return_value=updated,
            ) as set_member,
        ):
            result = set_guided_binding_member(
                inventory,
                binding_id="measurement",
                binding_label="Measurement",
                role="measurement",
                existing_binding=None,
                first_member=True,
                selected=selected,
                member_phases=("L1", "L2"),
            )

        self.assertEqual(result, updated)
        parse_role.assert_called_once_with("measurement")
        set_member.assert_called_once_with(
            inventory,
            binding_id="measurement",
            device_id="meter_l2",
            capability_id="meter",
            member_phases=("L1", "L2"),
            role="measurement",
            label="Measurement",
            phase_scope=("L1", "L2"),
        )

    def test_extend_guided_binding_contract(self) -> None:
        with (
            patch(
                "venus_evcharger.bootstrap.wizard_inventory_cli_guided_binding.selected_binding_choice",
                side_effect=[_choice(device_id="meter_l1"), _choice(device_id="meter_l2", label="Meter L2")],
            ),
            patch(
                "venus_evcharger.bootstrap.wizard_inventory_cli_guided_binding.binding_member_phases",
                side_effect=[("L1",), ("L2",)],
            ),
            patch(
                "venus_evcharger.bootstrap.wizard_inventory_cli_guided_binding.inventory_bool_field",
                side_effect=[True, False],
            ),
        ):
            updated = extend_guided_binding(
                _namespace(non_interactive=False),
                DeviceInventory(profiles=_inventory().profiles, devices=_inventory().devices),
                role="measurement",
                binding_id="measurement",
                binding_label="Measurement",
                binding_scope=("L1", "L2"),
                existing_binding=None,
                choices=(_choice(device_id="meter_l1"), _choice(device_id="meter_l2", label="Meter L2")),
            )

        self.assertEqual(updated.bindings[0].phase_scope, ("L1", "L2"))
        self.assertEqual(tuple(member.device_id for member in updated.bindings[0].members), ("meter_l1", "meter_l2"))

    def test_extend_guided_binding_calls_member_contracts(self) -> None:
        inventory = _inventory()
        first_inventory = DeviceInventory()
        second_inventory = DeviceInventory()
        choices = (_choice(device_id="meter_l1"), _choice(device_id="meter_l2", label="Meter L2"))
        with (
            patch(
                "venus_evcharger.bootstrap.wizard_inventory_cli_guided_binding.inventory_bool_field",
                return_value=False,
            ) as bool_field,
            patch(
                "venus_evcharger.bootstrap.wizard_inventory_cli_guided_binding.selected_binding_choice",
                return_value=choices[0],
            ) as select_choice,
            patch(
                "venus_evcharger.bootstrap.wizard_inventory_cli_guided_binding.binding_member_phases",
                return_value=("L1",),
            ) as phases,
            patch(
                "venus_evcharger.bootstrap.wizard_inventory_cli_guided_binding.set_guided_binding_member",
                return_value=first_inventory,
            ) as set_member,
        ):
            result = extend_guided_binding(
                _namespace(),
                inventory,
                role="measurement",
                binding_id="measurement",
                binding_label="Measurement",
                binding_scope=("L1", "L2"),
                existing_binding=inventory.bindings[0],
                choices=choices,
            )

        self.assertEqual(result, first_inventory)
        select_choice.assert_called_once_with(choices)
        phases.assert_called_once_with(_namespace(), choices[0], ("L1", "L2"))
        set_member.assert_called_once_with(
            inventory,
            binding_id="measurement",
            binding_label="Measurement",
            role="measurement",
            existing_binding=inventory.bindings[0],
            first_member=True,
            selected=choices[0],
            member_phases=("L1",),
        )
        bool_field.assert_called_once_with(_namespace(), "_inventory_add_binding_member", "Add another binding member", False)

        with (
            patch(
                "venus_evcharger.bootstrap.wizard_inventory_cli_guided_binding.inventory_bool_field",
                side_effect=[True, False],
            ) as bool_field,
            patch(
                "venus_evcharger.bootstrap.wizard_inventory_cli_guided_binding.selected_binding_choice",
                side_effect=[choices[0], choices[1]],
            ),
            patch(
                "venus_evcharger.bootstrap.wizard_inventory_cli_guided_binding.binding_member_phases",
                side_effect=[("L1",), ("L2",)],
            ),
            patch(
                "venus_evcharger.bootstrap.wizard_inventory_cli_guided_binding.set_guided_binding_member",
                side_effect=[first_inventory, second_inventory],
            ) as set_member,
        ):
            result = extend_guided_binding(
                _namespace(),
                inventory,
                role="measurement",
                binding_id="measurement",
                binding_label="Measurement",
                binding_scope=("L1", "L2"),
                existing_binding=None,
                choices=choices,
            )

        self.assertEqual(result, second_inventory)
        self.assertEqual(
            [call.kwargs["first_member"] for call in set_member.call_args_list],
            [True, False],
        )
        self.assertEqual(
            [call.args for call in bool_field.call_args_list],
            [(_namespace(), "_inventory_add_binding_member", "Add another binding member", False)] * 2,
        )

    def test_guided_inventory_edit_binding_contract(self) -> None:
        namespace = _namespace(
            non_interactive=False,
            inventory_binding_role="measurement",
            inventory_binding_id="measurement_group",
            inventory_binding_label="Measurement group",
            inventory_binding_phase_scope="L1,L2",
        )
        with (
            patch("builtins.input", side_effect=["1", "L1", "2", "L2"]),
            patch("venus_evcharger.bootstrap.wizard_inventory_prompts.prompt_yes_no", side_effect=[True, False]),
            patch(
                "venus_evcharger.bootstrap.wizard_inventory_cli_guided_binding.save_and_payload",
                return_value={"ok": True, "binding_id": "measurement_group"},
            ) as save_payload,
        ):
            result = guided_inventory_edit_binding(namespace, Path("/tmp/inventory.ini"), _inventory())

        self.assertEqual(result, {"ok": True, "binding_id": "measurement_group"})
        saved_inventory = save_payload.call_args.args[2]
        validated_guided_binding(saved_inventory, "measurement_group", ("L1", "L2"))
        self.assertEqual(tuple(member.device_id for member in saved_inventory.bindings[-1].members), ("meter_l1", "meter_l2"))

    def test_guided_inventory_edit_binding_calls_orchestration_contract(self) -> None:
        inventory = _inventory()
        replaced_inventory = DeviceInventory()
        extended_inventory = DeviceInventory()
        choices = (_choice(),)
        with (
            patch(
                "venus_evcharger.bootstrap.wizard_inventory_cli_guided_binding.inventory_choice_field",
                return_value="measurement",
            ) as choice_field,
            patch(
                "venus_evcharger.bootstrap.wizard_inventory_cli_guided_binding.parse_inventory_binding_role",
                return_value="measurement",
            ) as parse_role,
            patch(
                "venus_evcharger.bootstrap.wizard_inventory_cli_guided_binding.inventory_role_capability_choices",
                return_value=choices,
            ) as role_choices,
            patch(
                "venus_evcharger.bootstrap.wizard_inventory_cli_guided_binding.prompt_binding_choice",
                return_value=("measurement", inventory.bindings[0], "Measurement", ("L1",)),
            ) as prompt_choice,
            patch(
                "venus_evcharger.bootstrap.wizard_inventory_cli_guided_binding.maybe_replace_binding",
                return_value=(replaced_inventory, inventory.bindings[0]),
            ) as replace_binding,
            patch(
                "venus_evcharger.bootstrap.wizard_inventory_cli_guided_binding.extend_guided_binding",
                return_value=extended_inventory,
            ) as extend_binding,
            patch("venus_evcharger.bootstrap.wizard_inventory_cli_guided_binding.validated_guided_binding") as validate_binding,
            patch(
                "venus_evcharger.bootstrap.wizard_inventory_cli_guided_binding.save_and_payload",
                return_value={"ok": True},
            ) as save_payload,
        ):
            result = guided_inventory_edit_binding(_namespace(non_interactive=False), Path("/tmp/inventory.ini"), inventory)

        self.assertEqual(result, {"ok": True})
        choice_field.assert_called_once_with(
            _namespace(non_interactive=False),
            "inventory_binding_role",
            "Choose the binding role:",
            ("actuation", "measurement", "charger"),
            "measurement",
        )
        parse_role.assert_called_once_with("measurement")
        role_choices.assert_called_once_with(inventory, role="measurement")
        prompt_choice.assert_called_once_with(_namespace(non_interactive=False), inventory, "measurement")
        replace_binding.assert_called_once_with(_namespace(non_interactive=False), inventory, inventory.bindings[0], "measurement")
        extend_binding.assert_called_once_with(
            _namespace(non_interactive=False),
            replaced_inventory,
            role="measurement",
            binding_id="measurement",
            binding_label="Measurement",
            binding_scope=("L1",),
            existing_binding=inventory.bindings[0],
            choices=choices,
        )
        validate_binding.assert_called_once_with(extended_inventory, "measurement", ("L1",))
        save_payload.assert_called_once_with("guided-edit-binding", Path("/tmp/inventory.ini"), extended_inventory, binding_id="measurement")

    def test_guided_inventory_edit_binding_rejects_noninteractive_and_missing_choices(self) -> None:
        with self.assertRaises(ValueError) as error:
            guided_inventory_edit_binding(_namespace(non_interactive=True), Path("/tmp/inventory.ini"), _inventory())
        self.assertEqual(str(error.exception), "guided-edit-binding requires interactive input")

        empty_inventory = DeviceInventory(profiles=_inventory().profiles)
        with self.assertRaisesRegex(ValueError, "No eligible devices"):
            guided_inventory_edit_binding(_namespace(non_interactive=False), Path("/tmp/inventory.ini"), empty_inventory)

    def test_guided_inventory_edit_binding_allows_missing_noninteractive_attribute(self) -> None:
        namespace = argparse.Namespace(inventory_binding_role="measurement")
        with (
            patch(
                "venus_evcharger.bootstrap.wizard_inventory_cli_guided_binding.inventory_role_capability_choices",
                return_value=(_choice(),),
            ),
            patch(
                "venus_evcharger.bootstrap.wizard_inventory_cli_guided_binding.prompt_binding_choice",
                return_value=("measurement", None, "Measurement", ("L1",)),
            ),
            patch(
                "venus_evcharger.bootstrap.wizard_inventory_cli_guided_binding.maybe_replace_binding",
                return_value=(_inventory(), None),
            ),
            patch(
                "venus_evcharger.bootstrap.wizard_inventory_cli_guided_binding.extend_guided_binding",
                return_value=_inventory(),
            ),
            patch("venus_evcharger.bootstrap.wizard_inventory_cli_guided_binding.validated_guided_binding"),
            patch(
                "venus_evcharger.bootstrap.wizard_inventory_cli_guided_binding.save_and_payload",
                return_value={"ok": True},
            ),
        ):
            self.assertEqual(guided_inventory_edit_binding(namespace, Path("/tmp/inventory.ini"), _inventory()), {"ok": True})

    def test_validated_guided_binding_error_message_contract(self) -> None:
        with self.assertRaises(ValueError) as error:
            validated_guided_binding(_inventory(), "measurement", ("L3",))

        self.assertEqual(
            str(error.exception),
            "Binding member phases do not match the requested binding phase scope (L1,L2 != L3)",
        )

        with self.assertRaises(ValueError) as multi_phase_error:
            validated_guided_binding(_inventory(), "measurement", ("L2", "L3"))

        self.assertEqual(
            str(multi_phase_error.exception),
            "Binding member phases do not match the requested binding phase scope (L1,L2 != L2,L3)",
        )


if __name__ == "__main__":
    unittest.main()

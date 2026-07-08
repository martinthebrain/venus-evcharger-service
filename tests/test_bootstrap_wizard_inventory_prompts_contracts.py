# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import argparse
import io
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from venus_evcharger.bootstrap import wizard_inventory_prompts as prompts


def _namespace(**values: object) -> argparse.Namespace:
    return argparse.Namespace(**values)


class WizardInventoryPromptContractTests(unittest.TestCase):
    def test_namespace_string_trims_text_and_treats_missing_or_empty_as_absent(self) -> None:
        self.assertIsNone(prompts._namespace_string(_namespace(), "inventory_label"))
        self.assertIsNone(prompts._namespace_string(_namespace(inventory_label=None), "inventory_label"))
        self.assertIsNone(prompts._namespace_string(_namespace(inventory_label=17), "inventory_label"))
        self.assertIsNone(prompts._namespace_string(_namespace(inventory_label="   "), "inventory_label"))
        self.assertEqual(prompts._namespace_string(_namespace(inventory_label="  Meter  "), "inventory_label"), "Meter")

    def test_choice_from_raw_accepts_exact_names_and_one_based_numbers_only(self) -> None:
        choices = ("switch", "meter", "charger")
        self.assertEqual(prompts._choice_from_raw("1", choices), "switch")
        self.assertEqual(prompts._choice_from_raw("2", choices), "meter")
        self.assertEqual(prompts._choice_from_raw("3", choices), "charger")
        self.assertEqual(prompts._choice_from_raw("meter", choices), "meter")
        self.assertIsNone(prompts._choice_from_raw("0", choices))
        self.assertIsNone(prompts._choice_from_raw("4", choices))
        self.assertIsNone(prompts._choice_from_raw("Meter", choices))

    def test_interactive_choice_uses_exact_prompt_default_and_retry_message(self) -> None:
        stdout = io.StringIO()
        with (
            patch("builtins.input", side_effect=["bad", "2"]) as input_mock,
            redirect_stdout(stdout),
        ):
            selected = prompts._interactive_choice("switch", ("switch", "meter"))

        self.assertEqual(selected, "meter")
        input_mock.assert_called_with("Select [switch]: ")
        self.assertEqual(input_mock.call_count, 2)
        self.assertEqual(stdout.getvalue(), "Invalid selection, please try again.\n")

    def test_inventory_field_contracts_for_args_interactive_and_non_interactive_errors(self) -> None:
        self.assertEqual(
            prompts.inventory_field(_namespace(inventory_profile_id="  profile_a  "), "inventory_profile_id", "Profile id"),
            "profile_a",
        )
        with self.assertRaisesRegex(ValueError, "--inventory-profile-id is required for --inventory-action add-profile"):
            prompts.inventory_field(
                _namespace(inventory_action="add-profile", non_interactive=True),
                "inventory_profile_id",
                "Profile id",
            )
        with patch("builtins.input", return_value="  profile_b  ") as input_mock:
            self.assertEqual(
                prompts.inventory_field(_namespace(inventory_action="add-profile"), "inventory_profile_id", "Profile id"),
                "profile_b",
            )
        input_mock.assert_called_once_with("Profile id: ")
        with patch("builtins.input", return_value=" "):
            with self.assertRaisesRegex(ValueError, "Profile id must not be empty"):
                prompts.inventory_field(_namespace(inventory_action="add-profile"), "inventory_profile_id", "Profile id")

    def test_inventory_optional_field_contracts_for_clear_absent_and_interactive_values(self) -> None:
        self.assertEqual(
            prompts.inventory_optional_field(_namespace(inventory_endpoint="  http://meter.local  "), "inventory_endpoint", "Endpoint"),
            "http://meter.local",
        )
        with patch("builtins.input") as input_mock:
            self.assertIsNone(prompts.inventory_optional_field(_namespace(inventory_endpoint="   "), "inventory_endpoint", "Endpoint"))
        input_mock.assert_not_called()
        with patch("builtins.input") as input_mock:
            self.assertIsNone(prompts.inventory_optional_field(_namespace(non_interactive=True), "inventory_endpoint", "Endpoint"))
        input_mock.assert_not_called()
        with patch("builtins.input", return_value="  http://meter.local  ") as input_mock:
            self.assertEqual(
                prompts.inventory_optional_field(_namespace(), "inventory_endpoint", "Endpoint"),
                "http://meter.local",
            )
        input_mock.assert_called_once_with("Endpoint [leave blank to clear]: ")
        with patch("builtins.input", return_value="   "):
            self.assertIsNone(prompts.inventory_optional_field(_namespace(), "inventory_endpoint", "Endpoint"))

    def test_inventory_field_with_default_contracts(self) -> None:
        self.assertEqual(
            prompts.inventory_field_with_default(_namespace(inventory_label="  Explicit  "), "inventory_label", "Label", "Default"),
            "Explicit",
        )
        self.assertEqual(
            prompts.inventory_field_with_default(_namespace(non_interactive=True), "inventory_label", "Label", "Default"),
            "Default",
        )
        with patch("builtins.input", return_value="  Custom  ") as input_mock:
            self.assertEqual(
                prompts.inventory_field_with_default(_namespace(), "inventory_label", "Label", "Default"),
                "Custom",
            )
        input_mock.assert_called_once_with("Label [Default]: ")
        with patch("builtins.input", return_value="   "):
            self.assertEqual(prompts.inventory_field_with_default(_namespace(), "inventory_label", "Label", "Default"), "Default")

    def test_inventory_bool_field_contracts_for_explicit_non_interactive_and_prompt(self) -> None:
        self.assertTrue(prompts.inventory_bool_field(_namespace(inventory_measures_power=True), "inventory_measures_power", "Measures power"))
        self.assertFalse(
            prompts.inventory_bool_field(
                _namespace(inventory_measures_power=False, non_interactive=True),
                "inventory_measures_power",
                "Measures power",
                True,
            )
        )
        with patch("venus_evcharger.bootstrap.wizard_inventory_prompts.prompt_yes_no", return_value=True) as prompt_mock:
            self.assertTrue(prompts.inventory_bool_field(_namespace(inventory_measures_power=False), "inventory_measures_power", "Measures power", False))
        prompt_mock.assert_called_once_with("Measures power?", False)
        with patch("venus_evcharger.bootstrap.wizard_inventory_prompts.prompt_yes_no", return_value=True) as prompt_mock:
            self.assertTrue(prompts.inventory_bool_field(_namespace(), "inventory_measures_power", "Measures power"))
        prompt_mock.assert_called_once_with("Measures power?", False)
        with patch("venus_evcharger.bootstrap.wizard_inventory_prompts.prompt_yes_no", return_value=False) as prompt_mock:
            self.assertFalse(prompts.inventory_bool_field(_namespace(), "inventory_measures_power", "Measures power", True))
        prompt_mock.assert_called_once_with("Measures power?", True)

    def test_inventory_choice_field_contracts_for_errors_noninteractive_and_menu_output(self) -> None:
        self.assertEqual(
            prompts.inventory_choice_field(_namespace(inventory_kind="meter"), "inventory_kind", "Choose kind:", ("switch", "meter"), "switch"),
            "meter",
        )
        with self.assertRaisesRegex(ValueError, r"Choose kind: must be one of: switch, meter"):
            prompts.inventory_choice_field(_namespace(inventory_kind="charger"), "inventory_kind", "Choose kind:", ("switch", "meter"), "switch")
        self.assertEqual(
            prompts.inventory_choice_field(_namespace(non_interactive=True), "inventory_kind", "Choose kind:", ("switch", "meter"), "switch"),
            "switch",
        )
        stdout = io.StringIO()
        with (
            patch("venus_evcharger.bootstrap.wizard_inventory_prompts._interactive_choice", return_value="meter") as choice_mock,
            redirect_stdout(stdout),
        ):
            selected = prompts.inventory_choice_field(_namespace(), "inventory_kind", "Choose kind:", ("switch", "meter"), "switch")
        self.assertEqual(selected, "meter")
        choice_mock.assert_called_once_with("switch", ("switch", "meter"))
        self.assertEqual(stdout.getvalue(), "Choose kind:\n  1. switch\n  2. meter\n")


if __name__ == "__main__":
    unittest.main()

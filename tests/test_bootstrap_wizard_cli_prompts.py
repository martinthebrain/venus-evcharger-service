# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import unittest
from unittest.mock import Mock, call, patch

from venus_evcharger.bootstrap import wizard_cli_prompts as prompts


class BootstrapWizardCliPromptTests(unittest.TestCase):
    def test_prompt_text_contract(self) -> None:
        with patch("builtins.input", return_value=" typed ") as input_fn:
            self.assertEqual(prompts._prompt_text("Host", "192.0.2.50"), "typed")
        input_fn.assert_called_once_with("Host [192.0.2.50]: ")

        with patch("builtins.input", return_value="") as input_fn:
            self.assertEqual(prompts._prompt_text("Host", "192.0.2.50"), "192.0.2.50")
        input_fn.assert_called_once_with("Host [192.0.2.50]: ")

    def test_prompt_password_contract(self) -> None:
        with (
            patch("venus_evcharger.bootstrap.wizard_cli_prompts.prompt_yes_no", return_value=True) as yes_no,
            patch("venus_evcharger.bootstrap.wizard_cli_prompts.getpass.getpass") as getpass,
        ):
            self.assertEqual(prompts._prompt_password("secret"), "secret")
        yes_no.assert_called_once_with("Reuse imported password?", True)
        getpass.assert_not_called()

        with (
            patch("venus_evcharger.bootstrap.wizard_cli_prompts.prompt_yes_no", return_value=False) as yes_no,
            patch("venus_evcharger.bootstrap.wizard_cli_prompts.getpass.getpass", return_value="typed") as getpass,
        ):
            self.assertEqual(prompts._prompt_password("secret"), "typed")
        yes_no.assert_called_once_with("Reuse imported password?", True)
        getpass.assert_called_once_with("Password: ")

        with (
            patch("venus_evcharger.bootstrap.wizard_cli_prompts.prompt_yes_no") as yes_no,
            patch("venus_evcharger.bootstrap.wizard_cli_prompts.getpass.getpass", return_value="typed") as getpass,
        ):
            self.assertEqual(prompts._prompt_password(""), "typed")
        yes_no.assert_not_called()
        getpass.assert_called_once_with("Password: ")

    def test_prompt_yes_no_contract(self) -> None:
        for raw_value in ("", "y", "yes", "1", "true", "on"):
            with patch("builtins.input", return_value=raw_value) as input_fn:
                self.assertTrue(prompts.prompt_yes_no("Enable", True))
            input_fn.assert_called_once_with("Enable [Y/n]: ")

        for raw_value in ("n", "no", "0", "false", "off", "anything"):
            with patch("builtins.input", return_value=raw_value) as input_fn:
                self.assertFalse(prompts.prompt_yes_no("Enable", False))
            input_fn.assert_called_once_with("Enable [y/N]: ")

        with patch("builtins.input", return_value="") as input_fn:
            self.assertFalse(prompts.prompt_yes_no("Enable", False))
        input_fn.assert_called_once_with("Enable [y/N]: ")

    def test_choice_resolution_contract(self) -> None:
        choices = ("alpha", "beta", "gamma")
        self.assertEqual(prompts._choice_from_raw("1", choices), "alpha")
        self.assertEqual(prompts._choice_from_raw("2", choices), "beta")
        self.assertEqual(prompts._choice_from_raw("3", choices), "gamma")
        self.assertEqual(prompts._choice_from_raw("beta", choices), "beta")
        self.assertIsNone(prompts._choice_from_raw("0", choices))
        self.assertIsNone(prompts._choice_from_raw("4", choices))
        self.assertIsNone(prompts._choice_from_raw("-1", choices))
        self.assertIsNone(prompts._choice_from_raw("Beta", choices))
        self.assertIsNone(prompts._choice_from_raw("x", choices))

        self.assertEqual(prompts._resolved_choice_input("", choices, "gamma"), "gamma")
        self.assertEqual(prompts._resolved_choice_input("2", choices, None), "beta")
        self.assertIsNone(prompts._resolved_choice_input("", choices, None))
        self.assertIsNone(prompts._resolved_choice_input("missing", choices, "gamma"))

    def test_prompt_choice_input_and_loop_contract(self) -> None:
        with patch("builtins.input", return_value=" 2 ") as input_fn:
            self.assertEqual(prompts._prompt_choice_input(None), "2")
        input_fn.assert_called_once_with("Select [1]: ")

        with patch("builtins.input", return_value=" beta ") as input_fn:
            self.assertEqual(prompts._prompt_choice_input("alpha"), "beta")
        input_fn.assert_called_once_with("Select [alpha]: ")

        prompt_input = Mock(side_effect=["bad", "2"])
        printed = _bounded_print_mock(6)
        with (
            patch("venus_evcharger.bootstrap.wizard_cli_prompts._prompt_choice_input", prompt_input),
            patch("builtins.print", printed),
        ):
            self.assertEqual(
                prompts._prompt_choice(
                    "Pick one",
                    ("alpha", "beta"),
                    {"alpha": "Alpha label", "beta": "Beta label"},
                    "alpha",
                ),
                "beta",
            )
        self.assertEqual(prompt_input.mock_calls, [call("alpha"), call("alpha")])
        printed.assert_has_calls(
            [
                call("Pick one"),
                call("  1. Alpha label"),
                call("  2. Beta label"),
                call("Invalid selection, please try again."),
            ]
        )

        printed = _bounded_print_mock(5)
        default_input = Mock(side_effect=[""])
        with (
            patch("venus_evcharger.bootstrap.wizard_cli_prompts._prompt_choice_input", default_input),
            patch("builtins.print", printed),
        ):
            self.assertEqual(
                prompts._prompt_choice(
                    "Pick default",
                    ("alpha", "beta"),
                    {"alpha": "Alpha label"},
                    "beta",
                ),
                "beta",
            )
        default_input.assert_called_once_with("beta")
        printed.assert_has_calls([call("Pick default"), call("  1. Alpha label"), call("  2. beta")])

    def test_prompt_optional_choice_contract(self) -> None:
        with patch("venus_evcharger.bootstrap.wizard_cli_prompts._prompt_choice", return_value="none") as prompt_choice:
            self.assertIsNone(
                prompts._prompt_optional_choice(
                    "Preset",
                    ("none", "abb"),
                    {"none": "None", "abb": "ABB"},
                    None,
                )
            )
        prompt_choice.assert_called_once_with("Preset", ("none", "abb"), {"none": "None", "abb": "ABB"}, "none")

        with patch("venus_evcharger.bootstrap.wizard_cli_prompts._prompt_choice", return_value="abb") as prompt_choice:
            self.assertEqual(
                prompts._prompt_optional_choice(
                    "Preset",
                    ("none", "abb"),
                    {"none": "None", "abb": "ABB"},
                    "abb",
                ),
                "abb",
            )
        prompt_choice.assert_called_once_with("Preset", ("none", "abb"), {"none": "None", "abb": "ABB"}, "abb")

def _bounded_print_mock(max_calls: int) -> Mock:
    printed = Mock()

    def record_print(*args: object) -> None:
        if printed.call_count >= max_calls:
            raise AssertionError("prompt loop did not resolve")

    printed.side_effect = record_print
    return printed


if __name__ == "__main__":
    unittest.main()

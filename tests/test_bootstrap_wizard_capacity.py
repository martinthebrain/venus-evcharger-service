# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import argparse
import unittest
from unittest.mock import Mock

from venus_evcharger.bootstrap import wizard_capacity


class BootstrapWizardCapacityTests(unittest.TestCase):
    def test_direct_capacity_values_short_circuit_prompts(self) -> None:
        prompt = Mock(return_value=True)
        input_fn = Mock(return_value="999")
        self.assertEqual(
            wizard_capacity.resolved_energy_capacity_wh(
                argparse.Namespace(energy_default_usable_capacity_wh="12000", non_interactive=False),
                ("rec",),
                prompt_yes_no_fn=prompt,
                input_fn=input_fn,
            ),
            12000.0,
        )
        prompt.assert_not_called()
        input_fn.assert_not_called()

        self.assertEqual(
            wizard_capacity.resolved_energy_capacity_wh(
                argparse.Namespace(huawei_usable_capacity_wh="15360", non_interactive=False),
                ("rec",),
                prompt_yes_no_fn=prompt,
                input_fn=input_fn,
            ),
            15360.0,
        )

    def test_non_interactive_and_missing_namespace_attributes_skip_prompt(self) -> None:
        prompt = Mock(return_value=True)
        self.assertIsNone(
            wizard_capacity.resolved_energy_capacity_wh(
                argparse.Namespace(non_interactive=True),
                ("rec",),
                prompt_yes_no_fn=prompt,
            )
        )
        self.assertIsNone(
            wizard_capacity.resolved_energy_capacity_wh(
                argparse.Namespace(),
                tuple(),
                prompt_yes_no_fn=prompt,
            )
        )
        prompt.assert_not_called()

    def test_missing_non_interactive_defaults_to_interactive_prompt(self) -> None:
        prompt = Mock(return_value=True)
        input_fn = Mock(return_value="6400")
        self.assertEqual(
            wizard_capacity.resolved_energy_capacity_wh(
                argparse.Namespace(),
                ("rec",),
                prompt_yes_no_fn=prompt,
                input_fn=input_fn,
            ),
            6400.0,
        )
        prompt.assert_called_once_with("Set usable battery capacity for the suggested energy source now?", False)
        input_fn.assert_called_once_with("Usable battery capacity in Wh [skip]: ")

    def test_interactive_capacity_prompt_contract(self) -> None:
        prompt = Mock(return_value=True)
        input_fn = Mock(return_value=" 20480 ")
        self.assertEqual(
            wizard_capacity.resolved_energy_capacity_wh(
                argparse.Namespace(non_interactive=False),
                ("rec",),
                prompt_yes_no_fn=prompt,
                input_fn=input_fn,
            ),
            20480.0,
        )
        prompt.assert_called_once_with("Set usable battery capacity for the suggested energy source now?", False)
        input_fn.assert_called_once_with("Usable battery capacity in Wh [skip]: ")

        declined_prompt = Mock(return_value=False)
        skipped_input = Mock(return_value="20480")
        self.assertIsNone(
            wizard_capacity.resolved_energy_capacity_wh(
                argparse.Namespace(non_interactive=False),
                ("rec",),
                prompt_yes_no_fn=declined_prompt,
                input_fn=skipped_input,
            )
        )
        declined_prompt.assert_called_once_with("Set usable battery capacity for the suggested energy source now?", False)
        skipped_input.assert_not_called()

    def test_energy_capacity_override_contracts(self) -> None:
        self.assertEqual(wizard_capacity.resolved_energy_capacity_overrides(argparse.Namespace()), {})
        self.assertEqual(
            wizard_capacity.resolved_energy_capacity_overrides(
                argparse.Namespace(energy_usable_capacity_wh=[" hybrid_a = 15360 ", "hybrid_b=7680"])
            ),
            {"hybrid_a": 15360.0, "hybrid_b": 7680.0},
        )
        self.assertEqual(wizard_capacity._parsed_energy_capacity_override("source=1234"), ("source", 1234.0))
        with self.assertRaises(ValueError) as missing_separator:
            wizard_capacity._parsed_energy_capacity_override("broken")
        self.assertEqual(
            str(missing_separator.exception),
            "energy usable capacity overrides must use source_id=Wh, for example huawei_a=15360",
        )

        with self.assertRaises(ValueError) as empty_source:
            wizard_capacity._parsed_energy_capacity_override("=15360")
        self.assertEqual(
            str(empty_source.exception),
            "energy usable capacity overrides must use source_id=Wh with a positive Wh value",
        )

        with self.assertRaises(ValueError) as zero_capacity:
            wizard_capacity._parsed_energy_capacity_override("source=0")
        self.assertEqual(
            str(zero_capacity.exception),
            "energy usable capacity overrides must use source_id=Wh with a positive Wh value",
        )

        with self.assertRaises(ValueError) as malformed_capacity:
            wizard_capacity._parsed_energy_capacity_override("source=a=15360")
        self.assertEqual(
            str(malformed_capacity.exception),
            "energy usable capacity overrides must use source_id=Wh with a positive Wh value",
        )


if __name__ == "__main__":
    unittest.main()

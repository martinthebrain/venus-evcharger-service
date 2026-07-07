# SPDX-License-Identifier: GPL-3.0-or-later
"""Contracts for shared guided inventory CLI helpers."""

from __future__ import annotations

import io
import unittest
from contextlib import redirect_stdout

from venus_evcharger.bootstrap.wizard_inventory_cli_guided_common import (
    _ROLE_LABEL_DEFAULTS,
    binding_label_default,
    binding_scope_default,
    default_binding_id,
    print_capability_choices,
)
from venus_evcharger.inventory import DeviceInventory, RoleBinding


class WizardInventoryCliGuidedCommonContractTests(unittest.TestCase):
    def test_binding_defaults_are_stable(self) -> None:
        binding = RoleBinding(id="measurement", role="measurement", label="Measurement", phase_scope=("L1", "L2"))
        inventory = DeviceInventory(bindings=(binding,))

        self.assertEqual(
            _ROLE_LABEL_DEFAULTS,
            {"actuation": "Actuation", "measurement": "Measurement", "charger": "Charger"},
        )
        self.assertEqual(default_binding_id(DeviceInventory(), "meter_profile", "measurement"), "measurement")
        self.assertEqual(default_binding_id(inventory, "meter_profile", "measurement"), "meter_profile_measurement")
        self.assertEqual(binding_label_default(None, "measurement"), "Measurement")
        self.assertEqual(binding_label_default(binding, "measurement"), "Measurement")
        self.assertEqual(binding_scope_default(None), "L1")
        self.assertEqual(binding_scope_default(binding), "L1,L2")

    def test_print_capability_choices_uses_stable_human_output(self) -> None:
        stdout = io.StringIO()
        choices = (
            {
                "device_id": "meter_l1",
                "device_label": "Meter L1",
                "profile_id": "meter_profile",
                "capability_id": "meter",
                "kind": "meter",
                "adapter_type": "template_meter",
                "supported_phases": ("L1", "L2"),
            },
        )
        with redirect_stdout(stdout):
            print_capability_choices(choices)

        self.assertEqual(
            stdout.getvalue(),
            "Eligible device capabilities:\n"
            "  1. meter_l1 (Meter L1) -> meter/template_meter [L1,L2]\n",
        )


if __name__ == "__main__":
    unittest.main()

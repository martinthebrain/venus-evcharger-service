# SPDX-License-Identifier: GPL-3.0-or-later
import unittest

from venus_evcharger.inventory.config_validation import capability_kind_for_binding_role


class TestInventoryConfigContracts(unittest.TestCase):
    def test_binding_roles_map_to_exact_capability_kinds(self) -> None:
        self.assertEqual(capability_kind_for_binding_role("actuation"), "switch")
        self.assertEqual(capability_kind_for_binding_role("measurement"), "meter")
        self.assertEqual(capability_kind_for_binding_role("charger"), "charger")


if __name__ == "__main__":
    unittest.main()

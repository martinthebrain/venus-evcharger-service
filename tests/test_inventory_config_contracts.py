# SPDX-License-Identifier: GPL-3.0-or-later
import unittest

from venus_evcharger.inventory.config import _binding_capability_kind


class TestInventoryConfigContracts(unittest.TestCase):
    def test_binding_roles_map_to_exact_capability_kinds(self) -> None:
        self.assertEqual(_binding_capability_kind("actuation"), "switch")
        self.assertEqual(_binding_capability_kind("measurement"), "meter")
        self.assertEqual(_binding_capability_kind("charger"), "charger")


if __name__ == "__main__":
    unittest.main()

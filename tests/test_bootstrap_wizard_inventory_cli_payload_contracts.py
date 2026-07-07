# SPDX-License-Identifier: GPL-3.0-or-later
"""Contracts for inventory CLI payload helpers."""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import sentinel, patch

from venus_evcharger.bootstrap.wizard_inventory_cli_payload import action_payload, save_and_payload
from venus_evcharger.inventory import DeviceInventory


class WizardInventoryCliPayloadContractTests(unittest.TestCase):
    def test_action_payload_contains_summary_and_extra_fields(self) -> None:
        path = Path("/tmp/inventory.ini")
        inventory = DeviceInventory()
        with patch(
            "venus_evcharger.bootstrap.wizard_inventory_cli_payload.inventory_summary_payload",
            return_value={"profiles": 0},
        ) as summary:
            payload = action_payload("add-device", path, inventory, device_id="device_1")

        self.assertEqual(
            payload,
            {
                "ok": True,
                "action": "add-device",
                "inventory_path": "/tmp/inventory.ini",
                "inventory": {"profiles": 0},
                "device_id": "device_1",
            },
        )
        summary.assert_called_once_with(path, inventory)

    def test_save_and_payload_persists_before_returning_payload(self) -> None:
        path = Path("/tmp/inventory.ini")
        inventory = DeviceInventory()
        with (
            patch("venus_evcharger.bootstrap.wizard_inventory_cli_payload.save_inventory") as save_inventory,
            patch(
                "venus_evcharger.bootstrap.wizard_inventory_cli_payload.action_payload",
                return_value=sentinel.payload,
            ) as payload_builder,
        ):
            result = save_and_payload("remove-device", path, inventory, device_id="device_1")

        self.assertIs(result, sentinel.payload)
        save_inventory.assert_called_once_with(path, inventory)
        payload_builder.assert_called_once_with("remove-device", path, inventory, device_id="device_1")


if __name__ == "__main__":
    unittest.main()

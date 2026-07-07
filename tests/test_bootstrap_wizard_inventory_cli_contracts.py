# SPDX-License-Identifier: GPL-3.0-or-later
"""Routing contracts for the inventory editor CLI facade."""

from __future__ import annotations

import argparse
import unittest
from pathlib import Path
from unittest.mock import sentinel, patch

from venus_evcharger.bootstrap.wizard_inventory_cli import run_inventory_editor


def _namespace(action: str) -> argparse.Namespace:
    return argparse.Namespace(config_path="/tmp/config.ini", inventory_action=action)


class WizardInventoryCliContractTests(unittest.TestCase):
    def test_show_actions_return_summary_payload(self) -> None:
        for action in ("show", "show-bindings"):
            namespace = _namespace(action)
            path = Path(f"/tmp/{action}.ini")
            with (
                patch("venus_evcharger.bootstrap.wizard_inventory_cli.inventory_action_path", return_value=path) as action_path,
                patch("venus_evcharger.bootstrap.wizard_inventory_cli.load_inventory", return_value=sentinel.inventory) as load_inventory,
                patch(
                    "venus_evcharger.bootstrap.wizard_inventory_cli.inventory_summary_payload",
                    return_value={"action": action},
                ) as summary_payload,
            ):
                result = run_inventory_editor(namespace)
            self.assertEqual(result, {"action": action})
            action_path.assert_called_once_with(namespace)
            load_inventory.assert_called_once_with(path)
            summary_payload.assert_called_once_with(path, sentinel.inventory)

    def test_guided_add_profile_delegates_to_guided_support(self) -> None:
        namespace = _namespace("guided-add-profile")
        path = Path("/tmp/guided-add.ini")
        with (
            patch("venus_evcharger.bootstrap.wizard_inventory_cli.inventory_action_path", return_value=path),
            patch("venus_evcharger.bootstrap.wizard_inventory_cli.load_inventory", return_value=sentinel.inventory),
            patch(
                "venus_evcharger.bootstrap.wizard_inventory_cli.guided_inventory_add_profile",
                return_value={"ok": "add"},
            ) as guided_add,
        ):
            result = run_inventory_editor(namespace)
        self.assertEqual(result, {"ok": "add"})
        guided_add.assert_called_once_with(namespace, path, sentinel.inventory)

    def test_guided_edit_binding_delegates_to_guided_support(self) -> None:
        namespace = _namespace("guided-edit-binding")
        path = Path("/tmp/guided-edit.ini")
        with (
            patch("venus_evcharger.bootstrap.wizard_inventory_cli.inventory_action_path", return_value=path),
            patch("venus_evcharger.bootstrap.wizard_inventory_cli.load_inventory", return_value=sentinel.inventory),
            patch(
                "venus_evcharger.bootstrap.wizard_inventory_cli.guided_inventory_edit_binding",
                return_value={"ok": "edit"},
            ) as guided_edit,
        ):
            result = run_inventory_editor(namespace)
        self.assertEqual(result, {"ok": "edit"})
        guided_edit.assert_called_once_with(namespace, path, sentinel.inventory)

    def test_simple_actions_delegate_to_simple_action_runner(self) -> None:
        namespace = _namespace("add-device")
        path = Path("/tmp/simple.ini")
        with (
            patch("venus_evcharger.bootstrap.wizard_inventory_cli.inventory_action_path", return_value=path),
            patch("venus_evcharger.bootstrap.wizard_inventory_cli.load_inventory", return_value=sentinel.inventory),
            patch(
                "venus_evcharger.bootstrap.wizard_inventory_cli.run_simple_inventory_action",
                return_value={"ok": "simple"},
            ) as simple_action,
        ):
            result = run_inventory_editor(namespace)
        self.assertEqual(result, {"ok": "simple"})
        simple_action.assert_called_once_with("add-device", namespace, path, sentinel.inventory)


if __name__ == "__main__":
    unittest.main()

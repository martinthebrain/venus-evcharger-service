# SPDX-License-Identifier: GPL-3.0-or-later
"""Inventory editor command handling for the setup wizard CLI."""

from __future__ import annotations

import argparse
from venus_evcharger.bootstrap.wizard_inventory_cli_actions import run_simple_inventory_action
from venus_evcharger.bootstrap.wizard_inventory_cli_guided_binding import guided_inventory_edit_binding
from venus_evcharger.bootstrap.wizard_inventory_cli_guided_profile import guided_inventory_add_profile
from venus_evcharger.bootstrap.wizard_inventory_support import (
    inventory_action_path,
    inventory_summary_payload,
    load_inventory,
)


def run_inventory_editor(namespace: argparse.Namespace) -> dict[str, object]:
    """Execute one inventory editor action and return its result payload."""
    inventory_path = inventory_action_path(namespace)
    inventory = load_inventory(inventory_path)
    action = str(namespace.inventory_action)
    if action in {"show", "show-bindings"}:
        return inventory_summary_payload(inventory_path, inventory)
    if action == "guided-add-profile":
        return guided_inventory_add_profile(namespace, inventory_path, inventory)
    if action == "guided-edit-binding":
        return guided_inventory_edit_binding(namespace, inventory_path, inventory)
    return run_simple_inventory_action(action, namespace, inventory_path, inventory)

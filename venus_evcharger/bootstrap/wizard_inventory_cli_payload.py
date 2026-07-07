# SPDX-License-Identifier: GPL-3.0-or-later
"""Payload and persistence helpers for inventory editor actions."""

from __future__ import annotations

from pathlib import Path

from venus_evcharger.bootstrap.wizard_inventory_support import inventory_summary_payload, save_inventory
from venus_evcharger.inventory import DeviceInventory


def action_payload(
    action: str,
    inventory_path: Path,
    inventory: DeviceInventory,
    **extra: object,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "ok": True,
        "action": action,
        "inventory_path": str(inventory_path),
        "inventory": inventory_summary_payload(inventory_path, inventory),
    }
    payload.update(extra)
    return payload


def save_and_payload(
    action: str,
    inventory_path: Path,
    inventory: DeviceInventory,
    **extra: object,
) -> dict[str, object]:
    save_inventory(inventory_path, inventory)
    return action_payload(action, inventory_path, inventory, **extra)

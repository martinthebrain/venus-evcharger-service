# SPDX-License-Identifier: GPL-3.0-or-later
"""Public parsing, validation, and rendering facade for device inventories."""

from __future__ import annotations

import configparser

from .config_contracts import DeviceInventoryConfigError
from .config_parser import parse_inventory_sections
from .config_validation import validate_device_inventory
from .render import render_validated_device_inventory_config
from .schema import DeviceInventory

__all__ = (
    "DeviceInventoryConfigError",
    "parse_device_inventory_config",
    "render_device_inventory_config",
    "validate_device_inventory",
)


def parse_device_inventory_config(config: configparser.ConfigParser) -> DeviceInventory:
    """Parse and validate one normalized device inventory from config sections."""
    inventory = parse_inventory_sections(config)
    return validate_device_inventory(inventory)


def render_device_inventory_config(inventory: DeviceInventory) -> str:
    """Validate and render one normalized device inventory as INI text."""
    validate_device_inventory(inventory)
    return render_validated_device_inventory_config(inventory)

# SPDX-License-Identifier: GPL-3.0-or-later
"""Aggregate the responsibility-specific inventory test cases for audit targets."""

from tests.device_inventory_config_cases_part1 import DeviceInventoryConfigPart1Tests
from tests.device_inventory_config_cases_part2 import DeviceInventoryConfigPart2Tests
from tests.device_inventory_config_cases_part3 import DeviceInventoryConfigPart3Tests
from tests.device_inventory_config_cases_part4 import DeviceInventoryConfigPart4Tests
from tests.device_inventory_config_cases_part5 import DeviceInventoryConfigPart5Tests

__all__ = (
    "DeviceInventoryConfigPart1Tests",
    "DeviceInventoryConfigPart2Tests",
    "DeviceInventoryConfigPart3Tests",
    "DeviceInventoryConfigPart4Tests",
    "DeviceInventoryConfigPart5Tests",
)

# SPDX-License-Identifier: GPL-3.0-or-later
from tests.test_device_inventory_config_support import *  # noqa: F401,F403
from tests.test_device_inventory_config_part1 import _DeviceInventoryConfigTestsPart1
from tests.test_device_inventory_config_part2 import _DeviceInventoryConfigTestsPart2
from tests.test_device_inventory_config_part3 import _DeviceInventoryConfigTestsPart3

class DeviceInventoryConfigTests(_DeviceInventoryConfigTestsPart1, _DeviceInventoryConfigTestsPart2, _DeviceInventoryConfigTestsPart3, _DeviceInventoryConfigTestsHelperRole, unittest.TestCase):
    pass

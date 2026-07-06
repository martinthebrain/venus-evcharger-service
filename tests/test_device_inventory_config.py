# SPDX-License-Identifier: GPL-3.0-or-later
from tests.test_device_inventory_config_support import *  # noqa: F401,F403
from tests.test_device_inventory_config_part1 import _DeviceInventoryConfigTestsPart1
from tests.test_device_inventory_config_part2 import _DeviceInventoryConfigTestsPart2
from tests.test_device_inventory_config_part3 import _DeviceInventoryConfigTestsPart3
from tests.test_device_inventory_config_part4 import _DeviceInventoryConfigTestsPart4
from tests.test_device_inventory_config_part5 import _DeviceInventoryConfigTestsPart5

class DeviceInventoryConfigTests(
    _DeviceInventoryConfigTestsPart1,
    _DeviceInventoryConfigTestsPart2,
    _DeviceInventoryConfigTestsPart3,
    _DeviceInventoryConfigTestsPart4,
    _DeviceInventoryConfigTestsPart5,
    _DeviceInventoryConfigTestsHelperRole,
    unittest.TestCase,
):
    pass

# SPDX-License-Identifier: GPL-3.0-or-later
from tests.test_topology_config_support import *  # noqa: F401,F403
from tests.test_topology_config_part1 import _TopologyConfigTestsPart1
from tests.test_topology_config_part2 import _TopologyConfigTestsPart2
from tests.test_topology_config_part3 import _TopologyConfigTestsPart3
from tests.test_topology_config_part4 import _TopologyConfigTestsPart4
from tests.test_topology_config_part5 import _TopologyConfigTestsPart5

class TopologyConfigTests(
    _TopologyConfigTestsPart1,
    _TopologyConfigTestsPart2,
    _TopologyConfigTestsPart3,
    _TopologyConfigTestsPart4,
    _TopologyConfigTestsPart5,
    unittest.TestCase,
):
    pass

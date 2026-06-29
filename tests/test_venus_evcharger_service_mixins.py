# SPDX-License-Identifier: GPL-3.0-or-later
import unittest

from tests.service_mixins_cases_control import _ServiceRolesControlCases
from tests.service_mixins_cases_runtime_update import _ServiceRolesRuntimeUpdateCases


class TestShellyWallboxServiceRoles(
    _ServiceRolesRuntimeUpdateCases,
    _ServiceRolesControlCases,
    unittest.TestCase,
):
    pass

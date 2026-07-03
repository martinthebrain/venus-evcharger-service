# SPDX-License-Identifier: GPL-3.0-or-later
from tests.auto_controller_primary_cases_battery_balance import _AutoControllerPrimaryBatteryBalanceCases
from tests.auto_controller_primary_cases_common import AutoDecisionControllerTestCase
from tests.auto_controller_primary_cases_core import _AutoControllerPrimaryCoreCases
from tests.auto_controller_primary_cases_learning import _AutoControllerPrimaryLearningCases
from tests.auto_controller_primary_cases_samples import _AutoControllerPrimarySampleCases


class TestAutoDecisionControllerPrimary(
    _AutoControllerPrimaryCoreCases,
    _AutoControllerPrimaryBatteryBalanceCases,
    _AutoControllerPrimaryLearningCases,
    _AutoControllerPrimarySampleCases,
    AutoDecisionControllerTestCase,
):
    pass

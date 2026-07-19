# SPDX-License-Identifier: GPL-3.0-or-later

from tests.energy_aggregate_cases_common import _EnergyAggregateTestBase
from tests.energy_aggregate_cases_learning_forecast import _EnergyAggregateLearningForecastCases
from tests.energy_aggregate_cases_profiles import _EnergyAggregateProfileCases


class TestVenusEvchargerEnergyAggregate(
    _EnergyAggregateProfileCases,
    _EnergyAggregateLearningForecastCases,
    _EnergyAggregateTestBase,
):
    pass

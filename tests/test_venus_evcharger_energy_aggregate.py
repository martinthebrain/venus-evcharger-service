# SPDX-License-Identifier: GPL-3.0-or-later

from tests.energy_aggregate_cases_common import _EnergyAggregateTestBase
from tests.energy_aggregate_cases_learning_forecast import _EnergyAggregateLearningForecastCases
from tests.energy_aggregate_cases_profiles import _EnergyAggregateProfileCases
from venus_evcharger.energy.aggregate import _balance_profile_for_source


class TestVenusEvchargerEnergyAggregate(
    _EnergyAggregateProfileCases,
    _EnergyAggregateLearningForecastCases,
    _EnergyAggregateTestBase,
):
    def test_balance_profile_boundary_ignores_non_string_keys(self) -> None:
        profile = _balance_profile_for_source(
            {
                "battery": {
                    1: "ignored",
                    "reserve_band_floor_soc": 40.0,
                }
            },
            "battery",
        )

        self.assertEqual(profile, {"reserve_band_floor_soc": 40.0})

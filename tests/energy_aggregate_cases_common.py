# SPDX-License-Identifier: GPL-3.0-or-later
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from venus_evcharger.energy import (
    EnergySourceSnapshot,
    EnergyLearningProfile,
    available_energy_source_profiles,
    aggregate_energy_sources,
    derive_discharge_balance_metrics,
    derive_discharge_control_metrics,
    derive_energy_forecast,
    EnergySourceDefinition,
    energy_source_profile_details,
    energy_source_profile_probe_plan,
    load_energy_source_settings,
    resolve_energy_source_profile,
    summarize_energy_learning_profiles,
    update_energy_learning_profiles,
)
from venus_evcharger.energy import aggregate as energy_aggregate_mod
from venus_evcharger.energy import forecast as energy_forecast_mod
from venus_evcharger.energy import learning as energy_learning_mod
from venus_evcharger.energy import learning_update as energy_learning_update_mod
from venus_evcharger.energy import learning_summary as energy_learning_summary_mod
from venus_evcharger.energy.numeric import optional_int


class _EnergyAggregateTestBase(unittest.TestCase):
    pass

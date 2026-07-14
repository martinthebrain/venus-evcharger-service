# SPDX-License-Identifier: GPL-3.0-or-later
"""Energy-source helpers for multi-source battery and inverter integration."""

from .aggregate import aggregate_energy_sources, derive_discharge_balance_metrics, derive_discharge_control_metrics
from .config import load_energy_source_definitions, load_energy_source_settings
from .connectors import read_energy_source_snapshot
from .forecast import derive_energy_forecast
from .grid_fusion import GridMeasurementFusion
from .grid_fusion_contracts import GridFusionConfig, GridFusionResult, GridMeasurement
from .learning import summarize_energy_learning_profiles, update_energy_learning_profiles
from .models import (
    DEFAULT_BATTERY_CHEMISTRY,
    ENERGY_SOURCE_CONNECTOR_TYPES,
    ENERGY_SOURCE_ROLES,
    EnergyClusterSnapshot,
    EnergyLearningProfile,
    EnergySourceDefinition,
    EnergySourceSnapshot,
)
from .profiles import (
    available_energy_source_profiles,
    energy_source_profile_details,
    energy_source_profile_probe_plan,
    resolve_energy_source_profile,
)

__all__ = [
    "ENERGY_SOURCE_CONNECTOR_TYPES",
    "ENERGY_SOURCE_ROLES",
    "DEFAULT_BATTERY_CHEMISTRY",
    "EnergyClusterSnapshot",
    "EnergyLearningProfile",
    "EnergySourceDefinition",
    "EnergySourceSnapshot",
    "aggregate_energy_sources",
    "available_energy_source_profiles",
    "derive_discharge_balance_metrics",
    "derive_discharge_control_metrics",
    "derive_energy_forecast",
    "GridFusionConfig",
    "GridFusionResult",
    "GridMeasurement",
    "GridMeasurementFusion",
    "energy_source_profile_details",
    "energy_source_profile_probe_plan",
    "load_energy_source_definitions",
    "load_energy_source_settings",
    "read_energy_source_snapshot",
    "resolve_energy_source_profile",
    "summarize_energy_learning_profiles",
    "update_energy_learning_profiles",
]

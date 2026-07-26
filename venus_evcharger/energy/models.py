# SPDX-License-Identifier: GPL-3.0-or-later
"""Normalized energy-source data models used by helper and service code."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


ENERGY_SOURCE_ROLES = frozenset({"battery", "hybrid-inverter", "inverter"})
ENERGY_SOURCE_CONNECTOR_TYPES = frozenset(
    {"template_http", "modbus", "command_json", "opendtu_http"}
)
DEFAULT_BATTERY_CHEMISTRY = "lfp"


@dataclass(frozen=True)
class EnergySourceDefinition:
    """Describe one explicitly configured external energy source."""

    source_id: str
    profile_name: str = ""
    role: str = "battery"
    connector_type: str = ""
    config_path: str = ""
    service_name: str = ""
    usable_capacity_wh: float | None = None
    battery_chemistry: str = DEFAULT_BATTERY_CHEMISTRY
    capacity_auto_estimate: bool = True
    capacity_estimate_min_soc: float = 95.0
    capacity_startup_recheck_seconds: float = 300.0
    estimated_capacity_wh: float | None = None
    estimated_capacity_ah: float | None = None
    estimated_capacity_nominal_voltage_v: float | None = None
    estimated_capacity_cell_count: int | None = None
    physical_id: str = ""
    physical_priority: int = 0


@dataclass(frozen=True)
class EnergySourceSnapshot:
    """Runtime snapshot for one normalized energy source."""

    source_id: str
    role: str
    service_name: str
    soc: float | None = None
    usable_capacity_wh: float | None = None
    usable_capacity_source: str = ""
    installed_capacity_ah: float | None = None
    capacity_voltage_v: float | None = None
    capacity_nominal_voltage_v: float | None = None
    capacity_cell_count: int | None = None
    battery_chemistry: str = ""
    net_battery_power_w: float | None = None
    charge_limit_power_w: float | None = None
    discharge_limit_power_w: float | None = None
    ac_power_w: float | None = None
    pv_input_power_w: float | None = None
    grid_interaction_w: float | None = None
    ac_power_scope_key: str = ""
    pv_input_power_scope_key: str = ""
    grid_interaction_scope_key: str = ""
    operating_mode: str = ""
    online: bool = False
    confidence: float = 0.0
    captured_at: float | None = None
    physical_id: str = ""
    physical_priority: int = 0

    @property
    def charge_power_w(self) -> float | None:
        if self.net_battery_power_w is None:
            return None
        return max(0.0, -float(self.net_battery_power_w))

    @property
    def discharge_power_w(self) -> float | None:
        if self.net_battery_power_w is None:
            return None
        return max(0.0, float(self.net_battery_power_w))

    def as_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "physical_id": self.physical_id,
            "physical_priority": self.physical_priority,
            "role": self.role,
            "service_name": self.service_name,
            "soc": self.soc,
            "usable_capacity_wh": self.usable_capacity_wh,
            "usable_capacity_source": self.usable_capacity_source,
            "installed_capacity_ah": self.installed_capacity_ah,
            "capacity_voltage_v": self.capacity_voltage_v,
            "capacity_nominal_voltage_v": self.capacity_nominal_voltage_v,
            "capacity_cell_count": self.capacity_cell_count,
            "battery_chemistry": self.battery_chemistry,
            "net_battery_power_w": self.net_battery_power_w,
            "charge_power_w": self.charge_power_w,
            "discharge_power_w": self.discharge_power_w,
            "charge_limit_power_w": self.charge_limit_power_w,
            "discharge_limit_power_w": self.discharge_limit_power_w,
            "ac_power_w": self.ac_power_w,
            "ac_output_power_w": self.ac_power_w,
            "pv_input_power_w": self.pv_input_power_w,
            "grid_interaction_w": self.grid_interaction_w,
            "ac_power_scope_key": self.ac_power_scope_key,
            "pv_input_power_scope_key": self.pv_input_power_scope_key,
            "grid_interaction_scope_key": self.grid_interaction_scope_key,
            "operating_mode": self.operating_mode,
            "online": self.online,
            "confidence": self.confidence,
            "captured_at": self.captured_at,
        }


@dataclass(frozen=True)
class EnergyClusterSnapshot:
    """Aggregated energy picture computed from normalized source snapshots."""

    effective_soc: float | None = None
    combined_soc: float | None = None
    combined_usable_capacity_wh: float | None = None
    combined_charge_power_w: float | None = None
    combined_discharge_power_w: float | None = None
    combined_charge_limit_power_w: float | None = None
    combined_discharge_limit_power_w: float | None = None
    combined_net_battery_power_w: float | None = None
    combined_ac_power_w: float | None = None
    combined_pv_input_power_w: float | None = None
    combined_grid_interaction_w: float | None = None
    average_confidence: float | None = None
    source_count: int = 0
    online_source_count: int = 0
    valid_soc_source_count: int = 0
    battery_source_count: int = 0
    hybrid_inverter_source_count: int = 0
    inverter_source_count: int = 0
    sources: tuple[EnergySourceSnapshot, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict[str, Any]:
        return {
            "effective_soc": self.effective_soc,
            "combined_soc": self.combined_soc,
            "combined_usable_capacity_wh": self.combined_usable_capacity_wh,
            "combined_charge_power_w": self.combined_charge_power_w,
            "combined_discharge_power_w": self.combined_discharge_power_w,
            "combined_charge_limit_power_w": self.combined_charge_limit_power_w,
            "combined_discharge_limit_power_w": self.combined_discharge_limit_power_w,
            "combined_net_battery_power_w": self.combined_net_battery_power_w,
            "combined_ac_power_w": self.combined_ac_power_w,
            "combined_ac_output_power_w": self.combined_ac_power_w,
            "combined_pv_input_power_w": self.combined_pv_input_power_w,
            "combined_grid_interaction_w": self.combined_grid_interaction_w,
            "average_confidence": self.average_confidence,
            "source_count": self.source_count,
            "online_source_count": self.online_source_count,
            "valid_soc_source_count": self.valid_soc_source_count,
            "battery_source_count": self.battery_source_count,
            "hybrid_inverter_source_count": self.hybrid_inverter_source_count,
            "inverter_source_count": self.inverter_source_count,
            "sources": [source.as_dict() for source in self.sources],
        }


@dataclass(frozen=True)
class EnergyLearningProfile:
    """Simple runtime-learned behaviour metrics for one energy source."""

    source_id: str
    sample_count: int = 0
    active_sample_count: int = 0
    charge_sample_count: int = 0
    discharge_sample_count: int = 0
    import_support_sample_count: int = 0
    import_charge_sample_count: int = 0
    export_charge_sample_count: int = 0
    export_discharge_sample_count: int = 0
    export_idle_sample_count: int = 0
    day_active_sample_count: int = 0
    night_active_sample_count: int = 0
    day_charge_sample_count: int = 0
    night_charge_sample_count: int = 0
    day_discharge_sample_count: int = 0
    night_discharge_sample_count: int = 0
    response_sample_count: int = 0
    smoothing_sample_count: int = 0
    observed_max_charge_power_w: float | None = None
    observed_max_discharge_power_w: float | None = None
    observed_max_ac_power_w: float | None = None
    observed_max_pv_input_power_w: float | None = None
    observed_max_grid_import_w: float | None = None
    observed_max_grid_export_w: float | None = None
    observed_min_discharge_soc: float | None = None
    observed_max_charge_soc: float | None = None
    average_active_charge_power_w: float | None = None
    average_active_discharge_power_w: float | None = None
    average_active_power_delta_w: float | None = None
    typical_response_delay_seconds: float | None = None
    direction_change_count: int = 0
    last_direction: str = "idle"
    last_activity_state: str = "idle"
    last_active_at: float | None = None
    last_inactive_at: float | None = None
    last_change_at: float | None = None

    @property
    def support_bias(self) -> float | None:
        total = int(self.charge_sample_count) + int(self.discharge_sample_count)
        if total <= 0:
            return None
        return (float(self.discharge_sample_count) - float(self.charge_sample_count)) / float(total)

    @property
    def import_support_bias(self) -> float | None:
        total = int(self.import_support_sample_count) + int(self.import_charge_sample_count)
        if total <= 0:
            return None
        return (float(self.import_support_sample_count) - float(self.import_charge_sample_count)) / float(total)

    @property
    def export_bias(self) -> float | None:
        total = int(self.export_charge_sample_count) + int(self.export_discharge_sample_count)
        if total <= 0:
            return None
        return (float(self.export_charge_sample_count) - float(self.export_discharge_sample_count)) / float(total)

    @property
    def battery_first_export_bias(self) -> float | None:
        export_first_count = int(self.export_discharge_sample_count) + int(self.export_idle_sample_count)
        total = int(self.export_charge_sample_count) + export_first_count
        if total <= 0:
            return None
        return (float(self.export_charge_sample_count) - float(export_first_count)) / float(total)

    @property
    def day_support_bias(self) -> float | None:
        total = int(self.day_charge_sample_count) + int(self.day_discharge_sample_count)
        if total <= 0:
            return None
        return (float(self.day_discharge_sample_count) - float(self.day_charge_sample_count)) / float(total)

    @property
    def night_support_bias(self) -> float | None:
        total = int(self.night_charge_sample_count) + int(self.night_discharge_sample_count)
        if total <= 0:
            return None
        return (float(self.night_discharge_sample_count) - float(self.night_charge_sample_count)) / float(total)

    @property
    def reserve_band_floor_soc(self) -> float | None:
        return self.observed_min_discharge_soc

    @property
    def reserve_band_ceiling_soc(self) -> float | None:
        return self.observed_max_charge_soc

    @property
    def reserve_band_width_soc(self) -> float | None:
        if self.reserve_band_floor_soc is None or self.reserve_band_ceiling_soc is None:
            return None
        if self.reserve_band_ceiling_soc < self.reserve_band_floor_soc:
            return None
        return float(self.reserve_band_ceiling_soc) - float(self.reserve_band_floor_soc)

    @property
    def power_smoothing_ratio(self) -> float | None:
        average_delta = self.average_active_power_delta_w
        reference_power = _mean_positive_power(
            self.average_active_charge_power_w,
            self.average_active_discharge_power_w,
        )
        return _normalized_smoothing_ratio(average_delta, reference_power)

    def as_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "sample_count": self.sample_count,
            "active_sample_count": self.active_sample_count,
            "charge_sample_count": self.charge_sample_count,
            "discharge_sample_count": self.discharge_sample_count,
            "import_support_sample_count": self.import_support_sample_count,
            "import_charge_sample_count": self.import_charge_sample_count,
            "export_charge_sample_count": self.export_charge_sample_count,
            "export_discharge_sample_count": self.export_discharge_sample_count,
            "export_idle_sample_count": self.export_idle_sample_count,
            "day_active_sample_count": self.day_active_sample_count,
            "night_active_sample_count": self.night_active_sample_count,
            "day_charge_sample_count": self.day_charge_sample_count,
            "night_charge_sample_count": self.night_charge_sample_count,
            "day_discharge_sample_count": self.day_discharge_sample_count,
            "night_discharge_sample_count": self.night_discharge_sample_count,
            "response_sample_count": self.response_sample_count,
            "smoothing_sample_count": self.smoothing_sample_count,
            "observed_max_charge_power_w": self.observed_max_charge_power_w,
            "observed_max_discharge_power_w": self.observed_max_discharge_power_w,
            "observed_max_ac_power_w": self.observed_max_ac_power_w,
            "observed_max_pv_input_power_w": self.observed_max_pv_input_power_w,
            "observed_max_grid_import_w": self.observed_max_grid_import_w,
            "observed_max_grid_export_w": self.observed_max_grid_export_w,
            "observed_min_discharge_soc": self.observed_min_discharge_soc,
            "observed_max_charge_soc": self.observed_max_charge_soc,
            "average_active_charge_power_w": self.average_active_charge_power_w,
            "average_active_discharge_power_w": self.average_active_discharge_power_w,
            "average_active_power_delta_w": self.average_active_power_delta_w,
            "typical_response_delay_seconds": self.typical_response_delay_seconds,
            "support_bias": self.support_bias,
            "import_support_bias": self.import_support_bias,
            "export_bias": self.export_bias,
            "battery_first_export_bias": self.battery_first_export_bias,
            "day_support_bias": self.day_support_bias,
            "night_support_bias": self.night_support_bias,
            "reserve_band_floor_soc": self.reserve_band_floor_soc,
            "reserve_band_ceiling_soc": self.reserve_band_ceiling_soc,
            "reserve_band_width_soc": self.reserve_band_width_soc,
            "power_smoothing_ratio": self.power_smoothing_ratio,
            "direction_change_count": self.direction_change_count,
            "last_direction": self.last_direction,
            "last_activity_state": self.last_activity_state,
            "last_active_at": self.last_active_at,
            "last_inactive_at": self.last_inactive_at,
            "last_change_at": self.last_change_at,
        }


def _mean_positive_power(*values: float | None) -> float | None:
    normalized = [float(value) for value in values if value is not None and float(value) > 0.0]
    if not normalized:
        return None
    return sum(normalized) / float(len(normalized))


def _normalized_smoothing_ratio(average_delta: float | None, reference_power: float | None) -> float | None:
    if average_delta is None or reference_power is None or reference_power <= 0.0:
        return None
    return max(0.0, min(1.0, 1.0 - (float(average_delta) / float(reference_power))))

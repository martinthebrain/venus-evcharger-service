# SPDX-License-Identifier: GPL-3.0-or-later
"""Primary energy-source catalogue for the auto-input helper."""

from __future__ import annotations

from venus_evcharger.energy import EnergySourceDefinition
from venus_evcharger.inputs.dbus_errors import DBUS_INPUT_READ_ERRORS
from venus_evcharger.inputs.helper.config_runtime import AutoInputHelperSettings
from venus_evcharger.inputs.helper.contracts import GatewayReaderPort


class EnergySourceCatalog:
    """Translate typed helper settings into canonical energy-source definitions."""

    def __init__(self, settings: AutoInputHelperSettings, gateway: GatewayReaderPort) -> None:
        self.settings = settings
        self.gateway = gateway

    def primary_source(self) -> EnergySourceDefinition:
        if self.settings.auto_energy_sources:
            return self.settings.auto_energy_sources[0]
        return EnergySourceDefinition(
            source_id="primary_battery",
            service_name=self.settings.auto_battery_service,
            service_prefix=self.settings.auto_battery_service_prefix,
            soc_path=self.settings.auto_battery_soc_path,
            usable_capacity_wh=_positive_or_none(self.settings.auto_battery_capacity_wh),
            battery_chemistry=self.settings.auto_battery_chemistry,
            capacity_auto_estimate=self.settings.auto_battery_capacity_auto_estimate,
            capacity_wh_path=self.settings.auto_battery_capacity_wh_path,
            capacity_ah_path=self.settings.auto_battery_capacity_ah_path,
            voltage_path=self.settings.auto_battery_voltage_path,
            capacity_estimate_min_soc=self.settings.auto_battery_capacity_estimate_min_soc,
            capacity_startup_recheck_seconds=self.settings.auto_battery_capacity_startup_recheck_seconds,
            estimated_capacity_wh=_positive_or_none(self.settings.auto_battery_capacity_estimated_wh),
            estimated_capacity_ah=_positive_or_none(self.settings.auto_battery_capacity_estimated_ah),
            estimated_capacity_nominal_voltage_v=_positive_or_none(
                self.settings.auto_battery_capacity_estimated_nominal_voltage
            ),
            estimated_capacity_cell_count=_positive_int_or_none(
                self.settings.auto_battery_capacity_estimated_cell_count
            ),
            battery_power_path=self.settings.auto_battery_power_path,
            ac_power_path=self.settings.auto_battery_ac_power_path,
            pv_power_path=self.settings.auto_battery_pv_power_path,
            grid_interaction_path=self.settings.auto_battery_grid_interaction_path,
            operating_mode_path=self.settings.auto_battery_operating_mode_path,
        )

    def primary_service_prefix(self) -> str:
        return self.primary_source().service_prefix or self.settings.auto_battery_service_prefix

    def battery_service_has_soc(self, service_name: str) -> bool:
        try:
            return self.gateway.cached_value(service_name, self.settings.auto_battery_soc_path) is not None
        except DBUS_INPUT_READ_ERRORS:
            return False

    def source_has_readable_data(self, source: EnergySourceDefinition, service_name: str) -> bool:
        paths = (
            source.soc_path,
            source.battery_power_path,
            source.ac_power_path,
            source.pv_power_path,
            source.grid_interaction_path,
            source.operating_mode_path,
        )
        return any(self._field_readable(service_name, path) for path in paths)

    def _field_readable(self, service_name: str, path: str) -> bool:
        if not path:
            return False
        try:
            return self.gateway.cached_value(service_name, path) is not None
        except DBUS_INPUT_READ_ERRORS:
            return False


def _positive_or_none(value: float) -> float | None:
    return value if value > 0.0 else None


def _positive_int_or_none(value: int) -> int | None:
    return value if value > 0 else None

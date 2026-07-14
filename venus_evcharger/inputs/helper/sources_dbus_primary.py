# SPDX-License-Identifier: GPL-3.0-or-later
"""Primary energy-source defaults for auto-input DBus sources."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from venus_evcharger.energy import DEFAULT_BATTERY_CHEMISTRY, EnergySourceDefinition
from venus_evcharger.inputs.helper.sources_dbus_common import DBUS_SOURCE_READ_ERRORS
from venus_evcharger.inputs.helper.sources_dbus_gateway import _AutoInputHelperSourceDbusGateway


def _energy_source_definitions(value: object) -> tuple[EnergySourceDefinition, ...]:
    if not isinstance(value, Iterable) or isinstance(value, (str, bytes, bytearray, dict)):
        return ()
    return tuple(source for source in value if isinstance(source, EnergySourceDefinition))


class _AutoInputHelperSourceDbusPrimary(_AutoInputHelperSourceDbusGateway):
    def _configured_primary_energy_sources(self: Any) -> tuple[EnergySourceDefinition, ...]:
        return _energy_source_definitions(getattr(self, "auto_energy_sources", None))

    @staticmethod
    def _primary_energy_source_id() -> str:
        return "primary_battery"

    @staticmethod
    def _text_attribute(owner: object, name: str, default: str = "") -> str:
        value = getattr(owner, name, None)
        if value is None:
            return default
        text = str(value)
        return text if text else default

    def _primary_energy_service_name(self: Any) -> str:
        return _AutoInputHelperSourceDbusPrimary._text_attribute(self, "auto_battery_service")

    def _primary_energy_service_prefix(self: Any) -> str:
        return _AutoInputHelperSourceDbusPrimary._text_attribute(self, "auto_battery_service_prefix")

    def _primary_energy_soc_path(self: Any) -> str:
        return _AutoInputHelperSourceDbusPrimary._text_attribute(self, "auto_battery_soc_path", "/Soc")

    def _primary_energy_capacity_wh(self: Any) -> float | None:
        value = getattr(self, "auto_battery_capacity_wh", None)
        return float(value) if isinstance(value, (int, float)) else None

    def _primary_energy_chemistry(self: Any) -> str:
        return _AutoInputHelperSourceDbusPrimary._text_attribute(
            self, "auto_battery_chemistry", DEFAULT_BATTERY_CHEMISTRY
        ).strip().lower()

    def _primary_energy_capacity_auto_estimate(self: Any) -> bool:
        return bool(getattr(self, "auto_battery_capacity_auto_estimate", True))

    def _primary_energy_capacity_wh_path(self: Any) -> str:
        return _AutoInputHelperSourceDbusPrimary._text_attribute(self, "auto_battery_capacity_wh_path")

    def _primary_energy_capacity_ah_path(self: Any) -> str:
        return _AutoInputHelperSourceDbusPrimary._text_attribute(
            self, "auto_battery_capacity_ah_path", "/InstalledCapacity"
        )

    def _primary_energy_voltage_path(self: Any) -> str:
        return _AutoInputHelperSourceDbusPrimary._text_attribute(
            self, "auto_battery_voltage_path", "/Dc/0/Voltage"
        )

    def _primary_energy_capacity_estimate_min_soc(self: Any) -> float:
        value = getattr(self, "auto_battery_capacity_estimate_min_soc", None)
        return max(0.0, float(95.0 if value is None else value))

    def _primary_energy_capacity_startup_recheck_seconds(self: Any) -> float:
        value = getattr(self, "auto_battery_capacity_startup_recheck_seconds", None)
        return max(0.0, float(300.0 if value is None else value))

    def _primary_energy_estimated_capacity_wh(self: Any) -> float | None:
        return _AutoInputHelperSourceDbusPrimary._positive_float_or_none(
            getattr(self, "auto_battery_capacity_estimated_wh", None)
        )

    def _primary_energy_estimated_capacity_ah(self: Any) -> float | None:
        return _AutoInputHelperSourceDbusPrimary._positive_float_or_none(
            getattr(self, "auto_battery_capacity_estimated_ah", None)
        )

    def _primary_energy_estimated_capacity_nominal_voltage(self: Any) -> float | None:
        return _AutoInputHelperSourceDbusPrimary._positive_float_or_none(
            getattr(self, "auto_battery_capacity_estimated_nominal_voltage", None)
        )

    def _primary_energy_estimated_capacity_cell_count(self: Any) -> int | None:
        raw_value = getattr(self, "auto_battery_capacity_estimated_cell_count", None)
        if raw_value is None:
            return None
        try:
            value = int(float(raw_value))
        except (TypeError, ValueError):
            return None
        return value if value > 0 else None

    @staticmethod
    def _positive_float_or_none(value: object) -> float | None:
        try:
            numeric = float(str(value))
        except (TypeError, ValueError):
            return None
        return numeric if numeric > 0.0 else None

    def _primary_energy_battery_power_path(self: Any) -> str:
        return _AutoInputHelperSourceDbusPrimary._text_attribute(self, "auto_battery_power_path")

    def _primary_energy_ac_power_path(self: Any) -> str:
        return _AutoInputHelperSourceDbusPrimary._text_attribute(self, "auto_battery_ac_power_path")

    def _primary_energy_pv_power_path(self: Any) -> str:
        return _AutoInputHelperSourceDbusPrimary._text_attribute(self, "auto_battery_pv_power_path")

    def _primary_energy_grid_interaction_path(self: Any) -> str:
        return _AutoInputHelperSourceDbusPrimary._text_attribute(self, "auto_battery_grid_interaction_path")

    def _primary_energy_operating_mode_path(self: Any) -> str:
        return _AutoInputHelperSourceDbusPrimary._text_attribute(self, "auto_battery_operating_mode_path")

    def _default_primary_energy_source(self: Any) -> EnergySourceDefinition:
        return EnergySourceDefinition(
            source_id=self._primary_energy_source_id(),
            service_name=self._primary_energy_service_name(),
            service_prefix=self._primary_energy_service_prefix(),
            soc_path=self._primary_energy_soc_path(),
            usable_capacity_wh=self._primary_energy_capacity_wh(),
            battery_chemistry=self._primary_energy_chemistry(),
            capacity_auto_estimate=self._primary_energy_capacity_auto_estimate(),
            capacity_wh_path=self._primary_energy_capacity_wh_path(),
            capacity_ah_path=self._primary_energy_capacity_ah_path(),
            voltage_path=self._primary_energy_voltage_path(),
            capacity_estimate_min_soc=self._primary_energy_capacity_estimate_min_soc(),
            capacity_startup_recheck_seconds=self._primary_energy_capacity_startup_recheck_seconds(),
            estimated_capacity_wh=self._primary_energy_estimated_capacity_wh(),
            estimated_capacity_ah=self._primary_energy_estimated_capacity_ah(),
            estimated_capacity_nominal_voltage_v=self._primary_energy_estimated_capacity_nominal_voltage(),
            estimated_capacity_cell_count=self._primary_energy_estimated_capacity_cell_count(),
            battery_power_path=self._primary_energy_battery_power_path(),
            ac_power_path=self._primary_energy_ac_power_path(),
            pv_power_path=self._primary_energy_pv_power_path(),
            grid_interaction_path=self._primary_energy_grid_interaction_path(),
            operating_mode_path=self._primary_energy_operating_mode_path(),
        )

    def _primary_energy_source(self: Any) -> EnergySourceDefinition:
        sources = _AutoInputHelperSourceDbusPrimary._configured_primary_energy_sources(self)
        if sources:
            return sources[0]
        return _AutoInputHelperSourceDbusPrimary._default_primary_energy_source(self)

    def _battery_service_has_soc(self: Any, service_name: str) -> bool:
        try:
            return self._get_dbus_value(service_name, self.auto_battery_soc_path) is not None
        except DBUS_SOURCE_READ_ERRORS:
            return False

    def _energy_service_has_readable_field(self: Any, service_name: str, path: str) -> bool:
        if not path:
            return False
        try:
            return self._get_dbus_value(service_name, path) is not None
        except DBUS_SOURCE_READ_ERRORS:
            return False

    def _energy_source_has_readable_data(self: Any, source: EnergySourceDefinition, service_name: str) -> bool:
        paths = (
            source.soc_path,
            source.battery_power_path,
            source.ac_power_path,
            source.pv_power_path,
            source.grid_interaction_path,
            source.operating_mode_path,
        )
        return any(self._energy_service_has_readable_field(service_name, path) for path in paths)

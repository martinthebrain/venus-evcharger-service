# SPDX-License-Identifier: GPL-3.0-or-later
"""Config parsing helpers for normalized energy-source definitions."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .models import ENERGY_SOURCE_CONNECTOR_TYPES, ENERGY_SOURCE_ROLES, EnergySourceDefinition
from .profiles import energy_source_profile_defaults


def _text(value: Any, default: str = "") -> str:
    normalized = "" if value is None else str(value).strip()
    return normalized or default


def _csv_items(value: Any) -> tuple[str, ...]:
    raw = _text(value)
    if not raw:
        return ()
    return tuple(item.strip() for item in raw.split(",") if item.strip())


def _float_or_none(value: Any) -> float | None:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    return numeric if numeric > 0.0 else None


def _float_value(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _int_or_none(value: Any) -> int | None:
    try:
        numeric = int(float(str(value).strip()))
    except (TypeError, ValueError):
        return None
    return numeric if numeric > 0 else None


def _bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def _profile_text(profile_defaults: Mapping[str, Any], key: str, default: str = "") -> str:
    return _text(profile_defaults.get(key), default)


def _legacy_primary_source(defaults: Mapping[str, Any]) -> EnergySourceDefinition:
    return EnergySourceDefinition(
        source_id="primary_battery",
        service_name=_text(defaults.get("AutoBatteryService")),
        service_prefix=_text(defaults.get("AutoBatteryServicePrefix"), "com.victronenergy.battery"),
        soc_path=_text(defaults.get("AutoBatterySocPath"), "/Soc"),
        usable_capacity_wh=_float_or_none(defaults.get("AutoBatteryCapacityWh")),
        battery_chemistry=_text(defaults.get("AutoBatteryChemistry"), EnergySourceDefinition.battery_chemistry).lower(),
        capacity_auto_estimate=_bool(defaults.get("AutoBatteryCapacityAutoEstimate"), True),
        capacity_wh_path=_text(defaults.get("AutoBatteryCapacityWhPath")),
        capacity_ah_path=_text(defaults.get("AutoBatteryCapacityAhPath"), "/InstalledCapacity"),
        voltage_path=_text(defaults.get("AutoBatteryVoltagePath"), "/Dc/0/Voltage"),
        capacity_estimate_min_soc=max(0.0, _float_value(defaults.get("AutoBatteryCapacityEstimateMinSoc"), 95.0)),
        capacity_startup_recheck_seconds=max(
            0.0,
            _float_value(defaults.get("AutoBatteryCapacityStartupRecheckSeconds"), 300.0),
        ),
        estimated_capacity_wh=_float_or_none(defaults.get("AutoBatteryCapacityEstimatedWh")),
        estimated_capacity_ah=_float_or_none(defaults.get("AutoBatteryCapacityEstimatedAh")),
        estimated_capacity_nominal_voltage_v=_float_or_none(
            defaults.get("AutoBatteryCapacityEstimatedNominalVoltage")
        ),
        estimated_capacity_cell_count=_int_or_none(defaults.get("AutoBatteryCapacityEstimatedCellCount")),
        battery_power_path=_text(defaults.get("AutoBatteryPowerPath")),
        ac_power_path=_text(defaults.get("AutoBatteryAcPowerPath")),
        pv_power_path=_text(defaults.get("AutoBatteryPvPowerPath")),
        grid_interaction_path=_text(defaults.get("AutoBatteryGridInteractionPath")),
        operating_mode_path=_text(defaults.get("AutoBatteryOperatingModePath")),
    )


def _configured_source(defaults: Mapping[str, Any], source_id: str) -> EnergySourceDefinition:
    prefix = f"AutoEnergySource.{source_id}."
    profile_name = _text(defaults.get(f"{prefix}Profile")).lower()
    profile_defaults = energy_source_profile_defaults(profile_name)
    role = _text(defaults.get(f"{prefix}Role"), _profile_text(profile_defaults, "Role")).lower()
    if role not in ENERGY_SOURCE_ROLES:
        role = EnergySourceDefinition.role
    connector_type = _text(defaults.get(f"{prefix}Type"), _profile_text(profile_defaults, "Type")).lower()
    if connector_type not in ENERGY_SOURCE_CONNECTOR_TYPES:
        connector_type = EnergySourceDefinition.connector_type
    if connector_type == "template_http_energy":
        connector_type = "template_http"
    return EnergySourceDefinition(
        source_id=source_id,
        profile_name=_profile_text(profile_defaults, "Profile", profile_name),
        role=role,
        connector_type=connector_type,
        config_path=_text(defaults.get(f"{prefix}ConfigPath")),
        service_name=_text(defaults.get(f"{prefix}Service")),
        service_prefix=_text(defaults.get(f"{prefix}ServicePrefix"), _profile_text(profile_defaults, "ServicePrefix")),
        soc_path=_text(defaults.get(f"{prefix}SocPath"), _profile_text(profile_defaults, "SocPath", "/Soc")),
        usable_capacity_wh=_float_or_none(defaults.get(f"{prefix}UsableCapacityWh")),
        battery_chemistry=_text(
            defaults.get(f"{prefix}Chemistry"),
            _text(
                defaults.get("AutoBatteryChemistry"),
                _profile_text(profile_defaults, "BatteryChemistry", EnergySourceDefinition.battery_chemistry),
            ),
        ).lower(),
        capacity_auto_estimate=_bool(
            defaults.get(f"{prefix}CapacityAutoEstimate"),
            _bool(defaults.get("AutoBatteryCapacityAutoEstimate"), bool(profile_defaults.get("CapacityAutoEstimate", True))),
        ),
        capacity_wh_path=_text(
            defaults.get(f"{prefix}CapacityWhPath"),
            _text(defaults.get("AutoBatteryCapacityWhPath"), _profile_text(profile_defaults, "CapacityWhPath")),
        ),
        capacity_ah_path=_text(
            defaults.get(f"{prefix}CapacityAhPath"),
            _text(defaults.get("AutoBatteryCapacityAhPath"), _profile_text(profile_defaults, "CapacityAhPath", "/InstalledCapacity")),
        ),
        voltage_path=_text(
            defaults.get(f"{prefix}VoltagePath"),
            _text(defaults.get("AutoBatteryVoltagePath"), _profile_text(profile_defaults, "VoltagePath", "/Dc/0/Voltage")),
        ),
        capacity_estimate_min_soc=max(
            0.0,
            _float_value(
                defaults.get(f"{prefix}CapacityEstimateMinSoc"),
                _float_value(
                    defaults.get("AutoBatteryCapacityEstimateMinSoc"),
                    float(profile_defaults.get("CapacityEstimateMinSoc", 95.0)),
                ),
            ),
        ),
        capacity_startup_recheck_seconds=max(
            0.0,
            _float_value(
                defaults.get(f"{prefix}CapacityStartupRecheckSeconds"),
                _float_value(
                    defaults.get("AutoBatteryCapacityStartupRecheckSeconds"),
                    float(profile_defaults.get("CapacityStartupRecheckSeconds", 300.0)),
                ),
            ),
        ),
        estimated_capacity_wh=_float_or_none(defaults.get(f"{prefix}CapacityEstimatedWh")),
        estimated_capacity_ah=_float_or_none(defaults.get(f"{prefix}CapacityEstimatedAh")),
        estimated_capacity_nominal_voltage_v=_float_or_none(defaults.get(f"{prefix}CapacityEstimatedNominalVoltage")),
        estimated_capacity_cell_count=_int_or_none(defaults.get(f"{prefix}CapacityEstimatedCellCount")),
        battery_power_path=_text(
            defaults.get(f"{prefix}BatteryPowerPath"),
            _profile_text(profile_defaults, "BatteryPowerPath"),
        ),
        ac_power_path=_text(defaults.get(f"{prefix}AcPowerPath"), _profile_text(profile_defaults, "AcPowerPath")),
        pv_power_path=_text(defaults.get(f"{prefix}PvPowerPath"), _profile_text(profile_defaults, "PvPowerPath")),
        grid_interaction_path=_text(
            defaults.get(f"{prefix}GridInteractionPath"),
            _profile_text(profile_defaults, "GridInteractionPath"),
        ),
        operating_mode_path=_text(
            defaults.get(f"{prefix}OperatingModePath"),
            _profile_text(profile_defaults, "OperatingModePath"),
        ),
    )


def load_energy_source_definitions(defaults: Mapping[str, Any]) -> tuple[EnergySourceDefinition, ...]:
    """Load configured energy sources or fall back to the legacy primary battery."""
    configured_ids = _csv_items(defaults.get("AutoEnergySources"))
    if not configured_ids:
        return (_legacy_primary_source(defaults),)
    return tuple(_configured_source(defaults, source_id) for source_id in configured_ids)


def load_energy_source_settings(defaults: Mapping[str, Any]) -> tuple[tuple[EnergySourceDefinition, ...], bool]:
    """Return normalized energy sources plus whether combined SOC should drive Auto mode."""
    definitions = load_energy_source_definitions(defaults)
    use_combined_soc = _bool(defaults.get("AutoUseCombinedBatterySoc"), True)
    return definitions, use_combined_soc

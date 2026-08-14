# SPDX-License-Identifier: GPL-3.0-or-later
"""Transport-neutral projection of semantic gateway battery measurements."""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import cast

from venus_evcharger.energy import EnergySourceDefinition, EnergySourceSnapshot
from venus_evcharger.inputs.helper.external_contracts import GatewayBatteryMeasurements
from venus_evcharger.ipc.energy import MeasuredValue


@dataclass(frozen=True, slots=True)
class _ResolvedGatewayCapacity:
    usable_capacity_wh: float | None = None
    source: str = ""
    installed_capacity_ah: float | None = None
    voltage_v: float | None = None
    nominal_voltage_v: float | None = None
    cell_count: int | None = None


@dataclass(frozen=True, slots=True)
class _GatewaySourceMetadata:
    source_id: str
    role: str
    configured_service: str
    physical_id: str
    physical_priority: int
    battery_chemistry: str


def gateway_battery_source(
    measurements: GatewayBatteryMeasurements,
    fallback_source_id: str,
    definition: EnergySourceDefinition | None,
) -> EnergySourceSnapshot | None:
    """Build one semantic source without interpreting DBus services or paths."""
    primary = measurements.primary
    if primary is None:
        return None
    metadata = _gateway_source_metadata(fallback_source_id, definition)
    capacity = _resolved_gateway_capacity(measurements, definition)
    return EnergySourceSnapshot(
        source_id=metadata.source_id,
        role=metadata.role,
        service_name=_gateway_service_name(
            _gateway_measurement_source_ids(measurements),
            metadata.configured_service,
        ),
        physical_id=metadata.physical_id,
        physical_priority=metadata.physical_priority,
        soc=_measurement_value(measurements.soc),
        usable_capacity_wh=capacity.usable_capacity_wh,
        usable_capacity_source=capacity.source,
        installed_capacity_ah=capacity.installed_capacity_ah,
        capacity_voltage_v=capacity.voltage_v,
        capacity_nominal_voltage_v=capacity.nominal_voltage_v,
        capacity_cell_count=capacity.cell_count,
        battery_chemistry=metadata.battery_chemistry,
        net_battery_power_w=_measurement_value(measurements.net_power),
        online=True,
        confidence=primary.confidence,
        captured_at=primary.observed_at,
    )


def _gateway_source_metadata(
    fallback_source_id: str,
    definition: EnergySourceDefinition | None,
) -> _GatewaySourceMetadata:
    """Normalize optional source configuration without exposing transport details."""
    if definition is None:
        return _GatewaySourceMetadata(fallback_source_id, "battery", "", "", 0, "")
    return _GatewaySourceMetadata(
        definition.source_id,
        definition.role,
        definition.service_name,
        definition.physical_id,
        definition.physical_priority,
        definition.battery_chemistry,
    )


def _gateway_measurement_source_ids(
    measurements: GatewayBatteryMeasurements,
) -> tuple[str, ...]:
    """Preserve opaque gateway provenance without interpreting DBus identities."""
    return tuple(
        sorted(
            {
                source_id
                for measurement in measurements.available
                for source_id in measurement.source_ids
            }
        )
    )


def _resolved_gateway_capacity(
    measurements: GatewayBatteryMeasurements,
    definition: EnergySourceDefinition | None,
) -> _ResolvedGatewayCapacity:
    """Resolve usable Wh capacity according to the public priority contract."""
    base = _gateway_capacity_metadata(measurements, definition)
    preferred = _preferred_gateway_capacity(measurements, definition)
    if preferred is not None:
        capacity_wh, source = preferred
        return replace(
            base,
            usable_capacity_wh=capacity_wh,
            source=source,
        )
    inferred = _infer_lfp_capacity(
        definition,
        _measurement_value(measurements.soc),
        _positive_measurement_value(measurements.capacity_ah),
        _positive_measurement_value(measurements.voltage_v),
    )
    if inferred is None:
        return base
    capacity_wh, nominal_voltage_v, cell_count = inferred
    return replace(
        base,
        usable_capacity_wh=capacity_wh,
        source="gateway_lfp_inferred",
        nominal_voltage_v=nominal_voltage_v,
        cell_count=cell_count,
    )


def _gateway_capacity_metadata(
    measurements: GatewayBatteryMeasurements,
    definition: EnergySourceDefinition | None,
) -> _ResolvedGatewayCapacity:
    """Collect non-Wh capacity metadata independently from Wh precedence."""
    installed_capacity_ah = _positive_measurement_value(measurements.capacity_ah)
    voltage_v = _positive_measurement_value(measurements.voltage_v)
    if definition is None:
        return _ResolvedGatewayCapacity(
            installed_capacity_ah=installed_capacity_ah,
            voltage_v=voltage_v,
        )
    return _ResolvedGatewayCapacity(
        installed_capacity_ah=(
            installed_capacity_ah
            or _positive_definition_value(definition.estimated_capacity_ah)
        ),
        voltage_v=voltage_v,
        nominal_voltage_v=_positive_definition_value(
            definition.estimated_capacity_nominal_voltage_v
        ),
        cell_count=definition.estimated_capacity_cell_count,
    )


def _preferred_gateway_capacity(
    measurements: GatewayBatteryMeasurements,
    definition: EnergySourceDefinition | None,
) -> tuple[float, str] | None:
    """Apply live, configured, then persisted Wh precedence exactly once."""
    configured_wh, persisted_wh = _configured_capacity_values(definition)
    candidates = (
        (_positive_measurement_value(measurements.capacity_wh), "gateway_capacity_wh"),
        (configured_wh, "configured"),
        (persisted_wh, "config_estimated"),
    )
    return next(
        ((value, source) for value, source in candidates if value is not None),
        None,
    )


def _configured_capacity_values(
    definition: EnergySourceDefinition | None,
) -> tuple[float | None, float | None]:
    """Return configured and persisted Wh values in distinct priority slots."""
    if definition is None:
        return None, None
    return (
        _positive_definition_value(definition.usable_capacity_wh),
        _positive_definition_value(definition.estimated_capacity_wh),
    )


def _infer_lfp_capacity(
    definition: EnergySourceDefinition | None,
    soc: float | None,
    installed_capacity_ah: float | None,
    voltage_v: float | None,
) -> tuple[float, float, int] | None:
    """Infer LFP Wh capacity only from fully validated Ah and voltage inputs."""
    validated = _validated_lfp_inference_inputs(
        definition,
        soc,
        installed_capacity_ah,
        voltage_v,
    )
    if validated is None:
        return None
    installed_capacity_ah, voltage_v = validated
    cell_count = 15 if voltage_v < 52.5 else 16
    nominal_voltage_v = float(cell_count) * 3.2
    capacity_wh = _positive_definition_value(
        installed_capacity_ah * nominal_voltage_v
    )
    if capacity_wh is None:
        return None
    return capacity_wh, nominal_voltage_v, cell_count


def _validated_lfp_inference_inputs(
    definition: EnergySourceDefinition | None,
    soc: float | None,
    installed_capacity_ah: float | None,
    voltage_v: float | None,
) -> tuple[float, float] | None:
    """Narrow validated optional measurements for the capacity calculation."""
    if not _lfp_inference_inputs_valid(
        definition,
        soc,
        installed_capacity_ah,
        voltage_v,
    ):
        return None
    return cast(float, installed_capacity_ah), cast(float, voltage_v)


def _lfp_inference_inputs_valid(
    definition: EnergySourceDefinition | None,
    soc: float | None,
    installed_capacity_ah: float | None,
    voltage_v: float | None,
) -> bool:
    """Validate every prerequisite before deriving capacity from Ah and voltage."""
    if definition is None:
        return False
    return all(
        (
            definition.capacity_auto_estimate,
            definition.battery_chemistry.strip().lower() == "lfp",
            soc is not None,
            soc is not None and soc >= definition.capacity_estimate_min_soc,
            installed_capacity_ah is not None,
            voltage_v is not None,
            voltage_v is not None and 40.0 <= voltage_v <= 60.0,
        )
    )


def _measurement_value(measurement: MeasuredValue | None) -> float | None:
    """Return one finite semantic measurement value."""
    if measurement is None or measurement.value is None:
        return None
    value = float(measurement.value)
    return value if math.isfinite(value) else None


def _positive_measurement_value(
    measurement: MeasuredValue | None,
) -> float | None:
    """Return one finite, positive semantic measurement value."""
    value = _measurement_value(measurement)
    return value if value is not None and value > 0.0 else None


def _positive_definition_value(value: float | None) -> float | None:
    """Return one finite, positive configured value."""
    if value is None:
        return None
    normalized = float(value)
    return normalized if math.isfinite(normalized) and normalized > 0.0 else None


def _gateway_service_name(
    measurement_source_ids: tuple[str, ...],
    configured_service: str,
) -> str:
    """Project opaque provenance into the existing source diagnostics field."""
    measured_service = ",".join(measurement_source_ids)
    if measured_service:
        return measured_service
    if configured_service:
        return configured_service
    return "semantic-gateway"


__all__ = ["gateway_battery_source"]

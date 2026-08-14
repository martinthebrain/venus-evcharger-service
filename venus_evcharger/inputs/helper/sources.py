# SPDX-License-Identifier: GPL-3.0-or-later
"""Semantic energy-source projection for the auto-input helper."""

from __future__ import annotations

import math
import time

from venus_evcharger.energy import read_energy_source_step
from venus_evcharger.energy.read_steps import EnergySourceStepReader
from venus_evcharger.inputs.helper.config_runtime import AutoInputHelperSettings
from venus_evcharger.inputs.helper.contracts import (
    EnergyMeasurementKey,
    EnergySnapshotReaderPort,
    Snapshot,
)
from venus_evcharger.inputs.helper.external_contracts import (
    ExternalEnergyCycle,
    GatewayBatteryMeasurements,
    ProjectedEnergyValue,
    PvProjectionPolicy,
    projection_measurement_status,
)
from venus_evcharger.inputs.helper.external_sources import ConfiguredEnergySources
from venus_evcharger.ipc.energy import EnergyRefreshScope, MeasuredValue


class AutoInputSources:
    """Project one coherent energy snapshot into the core auto-input payload."""

    def __init__(
        self,
        settings: AutoInputHelperSettings,
        gateway: EnergySnapshotReaderPort,
        *,
        connector_session: object | None = None,
        energy_source_reader: EnergySourceStepReader = read_energy_source_step,
    ) -> None:
        self.settings = settings
        self.gateway = gateway
        self._measurements: dict[EnergyMeasurementKey, MeasuredValue | None] = {}
        self._gateway_battery: MeasuredValue | None = None
        self._gateway_battery_power: MeasuredValue | None = None
        self._battery_observed_at: float | None = None
        self._battery_observed_monotonic: float | None = None
        self._cycle_monotonic = 0.0
        self._external_cycle: ExternalEnergyCycle | None = None
        self._pv_projection: ProjectedEnergyValue | None = None
        self.external = ConfiguredEnergySources(
            settings.energy_sources,
            use_combined_soc=settings.use_combined_battery_soc,
            request_timeout_seconds=settings.energy_source_request_timeout_seconds,
            polling_policy=settings.external_polling_policy,
            pv_policy=settings.pv_projection_policy,
            gateway_source_id=settings.grid_fusion_config.backup_source_id,
            gateway_definition=settings.gateway_energy_source,
            session=connector_session,
            reader=energy_source_reader,
        )

    def prepare_cycle(self) -> None:
        self.gateway.refresh_inputs()
        current_epoch = time.time()
        self._cycle_monotonic = time.monotonic()
        scopes: tuple[EnergyMeasurementKey, ...] = (
            "pv",
            "grid",
            "battery",
            "battery_power",
            "battery_capacity_wh",
            "battery_capacity_ah",
            "battery_voltage",
        )
        self._measurements = {
            scope: self.gateway.measurement(scope)
            for scope in scopes
        }
        self._prepare_external_cycle(current_epoch, self._cycle_monotonic)

    def close(self) -> None:
        """Release connector resources owned by this helper."""
        self.external.close()

    def _prepare_external_cycle(
        self,
        current_epoch: float,
        current_monotonic: float,
    ) -> None:
        self._external_cycle = None
        self._gateway_battery = self._valid_battery_measurement(current_monotonic)
        self._gateway_battery_power = self._valid_measurement(
            "battery_power",
            current_monotonic,
        )
        self._battery_observed_at = _oldest_observation(
            self._gateway_battery,
            self._gateway_battery_power,
        )
        self._battery_observed_monotonic = _oldest_monotonic_observation(
            self._gateway_battery,
            self._gateway_battery_power,
        )
        gateway_pv = self._projected_gateway_value("pv", current_monotonic)
        if not self.external.enabled:
            self._pv_projection = gateway_pv
            return
        gateway_measurements = GatewayBatteryMeasurements(
            soc=self._gateway_battery,
            net_power=self._gateway_battery_power,
            capacity_wh=self._valid_positive_measurement(
                "battery_capacity_wh",
                current_monotonic,
            ),
            capacity_ah=self._valid_positive_measurement(
                "battery_capacity_ah",
                current_monotonic,
            ),
            voltage_v=self._valid_positive_measurement(
                "battery_voltage",
                current_monotonic,
            ),
        )
        self._external_cycle = self.external.collect_cycle(
            gateway_measurements,
            current_epoch,
        )
        self._battery_observed_at = self._external_cycle.battery_observed_at
        self._battery_observed_monotonic = (
            self._external_cycle.battery_observed_monotonic
        )
        self._pv_projection = _select_pv_projection(
            gateway_pv,
            self._external_cycle.pv,
            self.settings.pv_projection_policy,
        )

    def observed_at(self, source_name: str) -> float | None:
        if source_name == "pv":
            return _projected_observed_at(self._pv_projection)
        if source_name == "battery":
            return self._battery_observed_at
        if source_name == "grid":
            return _measurement_observed_at(self._measurements.get("grid"))
        return None

    def observed_monotonic(self, source_name: str) -> float | None:
        """Return the source's native monotonic observation timestamp."""
        if source_name == "pv":
            return _projected_observed_monotonic(self._pv_projection)
        if source_name == "battery":
            return self._battery_observed_monotonic
        if source_name == "grid":
            return _measurement_observed_monotonic(
                self._measurements.get("grid")
            )
        return None

    def pv_power(self) -> float | None:
        if self._pv_projection is None:
            if self.settings.pv_projection_policy.name != "external_only":
                self._request_missing("pv")
            return None
        return self._pv_projection.value

    def grid_power(self) -> float | None:
        measurement = self._valid_measurement("grid", self._cycle_monotonic)
        if measurement is None:
            self._request_missing("grid")
            return None
        assert measurement.value is not None
        return float(measurement.value)

    def battery_snapshot(self) -> Snapshot:
        measurement = self._gateway_battery
        if self.external.enabled:
            return self._external_battery_snapshot(measurement)
        if measurement is None:
            self._request_missing("battery")
            return empty_battery_snapshot()
        assert measurement.value is not None
        source_count = max(1, len(measurement.source_ids))
        return gateway_battery_snapshot(
            measurement.value,
            net_power_w=_measurement_value(self._gateway_battery_power),
            confidence=measurement.confidence,
            source_count=source_count,
        )

    def _valid_battery_measurement(self, current: float) -> MeasuredValue | None:
        measurement = self._valid_measurement("battery", current)
        if measurement is None:
            return None
        assert measurement.value is not None
        return measurement if 0.0 <= float(measurement.value) <= 100.0 else None

    def _valid_positive_measurement(
        self,
        key: EnergyMeasurementKey,
        current: float,
    ) -> MeasuredValue | None:
        measurement = self._valid_measurement(key, current)
        if measurement is None or measurement.value is None:
            return None
        return measurement if float(measurement.value) > 0.0 else None

    def _external_battery_snapshot(self, measurement: MeasuredValue | None) -> Snapshot:
        if measurement is None:
            self._request_missing("battery")
        if self._external_cycle is None:
            return empty_battery_snapshot()
        return dict(self._external_cycle.battery)

    def _valid_measurement(
        self,
        key: EnergyMeasurementKey,
        current_monotonic: float,
    ) -> MeasuredValue | None:
        measurement = self._measurements.get(key)
        if measurement is None:
            return None
        if not _contributing_measurement_status(measurement.status):
            return None
        observed_monotonic = float(measurement.observed_monotonic)
        if not _valid_gateway_observation_monotonic(
            observed_monotonic,
            current_monotonic,
        ):
            return None
        age = current_monotonic - observed_monotonic
        if age > self.settings.gateway_max_age_seconds:
            return None
        return measurement

    def _projected_gateway_value(
        self,
        scope: EnergyMeasurementKey,
        current: float,
    ) -> ProjectedEnergyValue | None:
        measurement = self._valid_measurement(scope, current)
        if measurement is None:
            return None
        assert measurement.value is not None
        return ProjectedEnergyValue(
            value=float(measurement.value),
            observed_at=float(measurement.observed_at),
            source_id=self.settings.grid_fusion_config.backup_source_id,
            confidence=float(measurement.confidence),
            measurement_status=projection_measurement_status(
                measurement.status
            ),
            observed_monotonic=float(measurement.observed_monotonic),
        )

    def _request_missing(self, scope: EnergyRefreshScope) -> None:
        self.gateway.request_refresh(scope, reason=f"semantic {scope} measurement unavailable", priority=True)


def gateway_battery_snapshot(
    battery_soc: float,
    *,
    net_power_w: float | None = None,
    confidence: float = 1.0,
    source_count: int = 1,
) -> Snapshot:
    normalized_count = max(1, int(source_count))
    payload = empty_battery_snapshot()
    payload.update(
        {
            "battery_soc": battery_soc,
            "battery_combined_soc": battery_soc,
            "battery_average_confidence": confidence,
            "battery_source_count": normalized_count,
            "battery_online_source_count": normalized_count,
            "battery_valid_soc_source_count": normalized_count,
            "battery_battery_source_count": normalized_count,
            "battery_combined_charge_power_w": _charge_power(net_power_w),
            "battery_combined_discharge_power_w": _discharge_power(net_power_w),
            "battery_combined_net_power_w": net_power_w,
        }
    )
    return payload


def empty_battery_snapshot() -> Snapshot:
    return {
        "battery_soc": None,
        "battery_combined_soc": None,
        "battery_combined_usable_capacity_wh": None,
        "battery_combined_charge_power_w": None,
        "battery_combined_discharge_power_w": None,
        "battery_combined_net_power_w": None,
        "battery_combined_ac_power_w": None,
        "battery_combined_pv_input_power_w": None,
        "battery_combined_grid_interaction_w": None,
        "battery_headroom_charge_w": None,
        "battery_headroom_discharge_w": None,
        "expected_near_term_export_w": None,
        "expected_near_term_import_w": None,
        "battery_discharge_balance_mode": "",
        "battery_discharge_balance_target_distribution_mode": "",
        "battery_discharge_balance_error_w": None,
        "battery_discharge_balance_max_abs_error_w": None,
        "battery_discharge_balance_total_discharge_w": None,
        "battery_discharge_balance_eligible_source_count": 0,
        "battery_discharge_balance_active_source_count": 0,
        "battery_discharge_balance_control_candidate_count": 0,
        "battery_discharge_balance_control_ready_count": 0,
        "battery_discharge_balance_supported_control_source_count": 0,
        "battery_discharge_balance_experimental_control_source_count": 0,
        "battery_average_confidence": None,
        "battery_source_count": 0,
        "battery_online_source_count": 0,
        "battery_valid_soc_source_count": 0,
        "battery_battery_source_count": 0,
        "battery_hybrid_inverter_source_count": 0,
        "battery_inverter_source_count": 0,
        "battery_sources": [],
        "battery_learning_profiles": {},
    }


def _select_pv_projection(
    gateway: ProjectedEnergyValue | None,
    external: ProjectedEnergyValue | None,
    policy: PvProjectionPolicy,
) -> ProjectedEnergyValue | None:
    candidates = {
        "gateway_only": (gateway,),
        "gateway_preferred": (gateway, external),
        "external_preferred": (external, gateway),
        "external_only": (external,),
    }[policy.name]
    available = tuple(candidate for candidate in candidates if candidate is not None)
    if not available:
        return None
    return max(available, key=_projection_quality)


def _projection_quality(projection: ProjectedEnergyValue) -> tuple[int, int]:
    return (
        _projection_status_rank(projection),
        _projection_confidence_rank(projection),
    )


def _projection_status_rank(projection: ProjectedEnergyValue) -> int:
    return 1 if projection.measurement_status == "fresh" else 0


def _projection_confidence_rank(projection: ProjectedEnergyValue) -> int:
    return 1 if projection.confidence >= 0.5 else 0


def _contributing_measurement_status(status: str) -> bool:
    return status in {"fresh", "stale"}


def _valid_gateway_observation_monotonic(
    observed_monotonic: float,
    current_monotonic: float,
) -> bool:
    return (
        math.isfinite(observed_monotonic)
        and 0.0 < observed_monotonic <= current_monotonic
    )


def _projected_observed_at(
    projection: ProjectedEnergyValue | None,
) -> float | None:
    return None if projection is None else projection.observed_at


def _projected_observed_monotonic(
    projection: ProjectedEnergyValue | None,
) -> float | None:
    return None if projection is None else projection.observed_monotonic


def _measurement_observed_at(
    measurement: MeasuredValue | None,
) -> float | None:
    if measurement is None or measurement.observed_at <= 0.0:
        return None
    return float(measurement.observed_at)


def _measurement_observed_monotonic(
    measurement: MeasuredValue | None,
) -> float | None:
    if measurement is None or measurement.observed_monotonic <= 0.0:
        return None
    return float(measurement.observed_monotonic)


def _measurement_value(measurement: MeasuredValue | None) -> float | None:
    if measurement is None or measurement.value is None:
        return None
    return float(measurement.value)


def _oldest_observation(*measurements: MeasuredValue | None) -> float | None:
    timestamps = tuple(
        observed_at
        for measurement in measurements
        if (observed_at := _measurement_observed_at(measurement)) is not None
    )
    return min(timestamps) if timestamps else None


def _oldest_monotonic_observation(
    *measurements: MeasuredValue | None,
) -> float | None:
    timestamps = tuple(
        observed_monotonic
        for measurement in measurements
        if (
            observed_monotonic := _measurement_observed_monotonic(measurement)
        )
        is not None
    )
    return min(timestamps) if timestamps else None


def _charge_power(net_power_w: float | None) -> float | None:
    return None if net_power_w is None else max(0.0, -float(net_power_w))


def _discharge_power(net_power_w: float | None) -> float | None:
    return None if net_power_w is None else max(0.0, float(net_power_w))

# SPDX-License-Identifier: GPL-3.0-or-later
"""Semantic energy-source projection for the auto-input helper."""

from __future__ import annotations

import time

from venus_evcharger.inputs.helper.config_runtime import AutoInputHelperSettings
from venus_evcharger.inputs.helper.contracts import EnergySnapshotReaderPort, Snapshot
from venus_evcharger.ipc.energy import EnergyRefreshScope, MeasuredValue


class AutoInputSources:
    """Project one coherent energy snapshot into the core auto-input payload."""

    def __init__(self, settings: AutoInputHelperSettings, gateway: EnergySnapshotReaderPort) -> None:
        self.settings = settings
        self.gateway = gateway
        self._measurements: dict[EnergyRefreshScope, MeasuredValue | None] = {}

    def prepare_cycle(self) -> None:
        self.gateway.refresh_inputs()
        scopes: tuple[EnergyRefreshScope, ...] = ("pv", "grid", "battery")
        self._measurements = {
            scope: self.gateway.measurement(scope)
            for scope in scopes
        }

    def observed_at(self, source_name: str) -> float | None:
        measurement = self._measurements.get(_source_scope(source_name))
        return measurement.observed_at if measurement is not None and measurement.observed_at > 0.0 else None

    def pv_power(self) -> float | None:
        return self._numeric_value("pv")

    def grid_power(self) -> float | None:
        return self._numeric_value("grid")

    def battery_snapshot(self) -> Snapshot:
        measurement = self._valid_measurement("battery")
        if measurement is None or measurement.value is None or not 0.0 <= measurement.value <= 100.0:
            self._request_missing("battery")
            return empty_battery_snapshot()
        source_count = max(1, len(measurement.source_ids))
        return gateway_battery_snapshot(
            measurement.value,
            confidence=measurement.confidence,
            source_count=source_count,
        )

    def _numeric_value(self, scope: EnergyRefreshScope) -> float | None:
        measurement = self._valid_measurement(scope)
        if measurement is None or measurement.value is None:
            self._request_missing(scope)
            return None
        return measurement.value

    def _valid_measurement(self, scope: EnergyRefreshScope) -> MeasuredValue | None:
        measurement = self._measurements.get(scope)
        if measurement is None or measurement.status not in {"fresh", "stale"}:
            return None
        age = max(0.0, time.time() - measurement.observed_at)
        return measurement if measurement.observed_at > 0.0 and age <= self.settings.gateway_max_age_seconds else None

    def _request_missing(self, scope: EnergyRefreshScope) -> None:
        self.gateway.request_refresh(scope, reason=f"semantic {scope} measurement unavailable", priority=True)


def gateway_battery_snapshot(
    battery_soc: float,
    *,
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


def _source_scope(source_name: str) -> EnergyRefreshScope:
    if source_name == "pv":
        return "pv"
    if source_name == "grid":
        return "grid"
    if source_name == "battery":
        return "battery"
    return "all"

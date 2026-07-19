# SPDX-License-Identifier: GPL-3.0-or-later
"""Canonical semantic source readers for the auto-input helper."""

from __future__ import annotations

from venus_evcharger.dbus_gateway import BATTERY_SOC_READ_KEY
from venus_evcharger.inputs.helper.contracts import (
    BatterySnapshotReaderPort,
    GatewayReaderPort,
    PvGridReaderPort,
    Snapshot,
)


class BatterySourceReader:
    """Read battery SOC from the gateway's semantic cache contract."""

    def __init__(self, gateway: GatewayReaderPort) -> None:
        self.gateway = gateway

    def battery_snapshot(self) -> Snapshot:
        if not self.gateway.source_retry_ready("battery"):
            return {"battery_soc": None}
        value = self.gateway.semantic_value(BATTERY_SOC_READ_KEY, reason="helper semantic battery SOC read")
        if value is None or not 0.0 <= float(value) <= 100.0:
            self.gateway.delay_source_retry("battery")
            return empty_battery_snapshot()
        return gateway_battery_snapshot(float(value))


class AutoInputSources:
    """Small source boundary consumed by snapshot scheduling."""

    def __init__(self, pv_grid: PvGridReaderPort, battery: BatterySnapshotReaderPort) -> None:
        self.pv_grid = pv_grid
        self.battery = battery

    def pv_power(self) -> float | None:
        return self.pv_grid.pv_power()

    def battery_snapshot(self) -> Snapshot:
        return self.battery.battery_snapshot()

    def grid_power(self) -> float | None:
        return self.pv_grid.grid_power()


def gateway_battery_snapshot(battery_soc: float) -> Snapshot:
    payload = empty_battery_snapshot()
    payload.update(
        {
            "battery_soc": battery_soc,
            "battery_combined_soc": battery_soc,
            "battery_average_confidence": 1.0,
            "battery_source_count": 1,
            "battery_online_source_count": 1,
            "battery_valid_soc_source_count": 1,
            "battery_battery_source_count": 1,
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

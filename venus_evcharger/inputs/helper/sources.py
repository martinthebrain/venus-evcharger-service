#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Collect PV, battery, and grid inputs for the Venus EV charger service in a helper process."""

from __future__ import annotations

import time
from typing import Any

from venus_evcharger.core.shared import coerce_dbus_numeric
from venus_evcharger.energy import (
    EnergyClusterSnapshot,
    EnergySourceDefinition,
    aggregate_energy_sources,
    derive_discharge_balance_metrics,
    derive_discharge_control_metrics,
    derive_energy_forecast,
    read_energy_source_snapshot,
    summarize_energy_learning_profiles,
    update_energy_learning_profiles,
)

from ..energy_snapshot_contracts import (
    energy_source_definitions,
    learning_profile_payloads,
    learning_profiles,
    nested_object_mappings,
    object_mapping,
)
from .sources_dbus import _AutoInputHelperSourceDbusMixin
from .sources_pv_grid import _AutoInputHelperSourcePvGridMixin


class _AutoInputHelperSourceMixin(_AutoInputHelperSourceDbusMixin, _AutoInputHelperSourcePvGridMixin):
    def _battery_snapshot_sources(self: Any) -> tuple[EnergySourceDefinition, ...]:
        configured = energy_source_definitions(getattr(self, "auto_energy_sources", ()))
        if configured:
            return configured
        return energy_source_definitions(self._primary_energy_source())

    def _battery_snapshot_cluster(self: Any, now: float) -> tuple[EnergyClusterSnapshot, tuple[EnergySourceDefinition, ...]]:
        sources = self._battery_snapshot_sources()
        source_snapshots = tuple(read_energy_source_snapshot(self, source, now) for source in sources)
        return aggregate_energy_sources(source_snapshots), sources

    def _battery_snapshot_effective_soc(self: Any, cluster: EnergyClusterSnapshot) -> float | None:
        primary_soc = cluster.sources[0].soc if cluster.sources else None
        selected_soc = cluster.effective_soc if bool(getattr(self, "auto_use_combined_battery_soc", True)) else primary_soc
        numeric_soc: object = coerce_dbus_numeric(selected_soc)
        if isinstance(numeric_soc, bool) or not isinstance(numeric_soc, (int, float)):
            return None
        return float(numeric_soc)

    def _battery_snapshot_learning_bundle(
        self: Any,
        cluster: EnergyClusterSnapshot,
        now: float,
    ) -> tuple[dict[str, object], dict[str, object], dict[str, object]]:
        existing_profiles = learning_profiles(getattr(self, "_energy_learning_profiles", {}))
        self._energy_learning_profiles = update_energy_learning_profiles(
            existing_profiles,
            cluster.sources,
            now,
        )
        learning_summary = summarize_energy_learning_profiles(self._energy_learning_profiles)
        discharge_balance = derive_discharge_balance_metrics(
            cluster.sources,
            self._energy_learning_profiles,
        )
        forecast = derive_energy_forecast(
            {
                "battery_combined_charge_power_w": cluster.combined_charge_power_w,
                "battery_combined_discharge_power_w": cluster.combined_discharge_power_w,
                "battery_combined_charge_limit_power_w": cluster.combined_charge_limit_power_w,
                "battery_combined_discharge_limit_power_w": cluster.combined_discharge_limit_power_w,
                "battery_combined_grid_interaction_w": cluster.combined_grid_interaction_w,
            },
            learning_summary,
        )
        return object_mapping(learning_summary), object_mapping(discharge_balance), object_mapping(forecast)

    @staticmethod
    def _battery_snapshot_source_payloads(
        cluster: EnergyClusterSnapshot,
        discharge_balance: dict[str, object],
        discharge_control: dict[str, object],
    ) -> list[dict[str, object]]:
        source_payloads = [object_mapping(source.as_dict()) for source in cluster.sources]
        source_balance = nested_object_mappings(discharge_balance.get("sources", {}))
        source_control = nested_object_mappings(discharge_control.get("sources", {}))
        for source_payload in source_payloads:
            source_id = str(source_payload.get("source_id", ""))
            source_payload.update(source_balance.get(source_id, {}))
            source_payload.update(source_control.get(source_id, {}))
        return source_payloads

    def _get_battery_snapshot(self: Any) -> dict[str, object]:
        """Read combined battery data from one or more energy sources."""
        if not self._source_retry_ready("battery"):
            return {"battery_soc": None}
        try:
            now = self._battery_snapshot_now()
            cluster, sources = self._battery_snapshot_cluster(now)
            effective_soc = self._battery_snapshot_effective_soc(cluster)
            learning_summary, discharge_balance, forecast = self._battery_snapshot_learning_bundle(cluster, now)
            discharge_control = derive_discharge_control_metrics(
                cluster.sources,
                {source.source_id: source for source in sources},
            )
            source_payloads = self._battery_snapshot_source_payloads(cluster, discharge_balance, discharge_control)
            payload = self._battery_snapshot_payload(
                effective_soc,
                cluster,
                forecast,
                discharge_balance,
                discharge_control,
                source_payloads,
            )
            return object_mapping(payload)
        except Exception:
            self._invalidate_auto_battery_service()
            self._delay_source_retry("battery")
            return object_mapping(self._empty_battery_snapshot())

    def _battery_snapshot_now(self: Any) -> float:
        return float(time.time())

    def _battery_snapshot_payload(
        self: Any,
        effective_soc: float | None,
        cluster: EnergyClusterSnapshot,
        forecast: dict[str, object],
        discharge_balance: dict[str, object],
        discharge_control: dict[str, object],
        source_payloads: list[dict[str, object]],
    ) -> dict[str, object]:
        return {
            "battery_soc": effective_soc,
            "battery_combined_soc": cluster.combined_soc,
            "battery_combined_usable_capacity_wh": cluster.combined_usable_capacity_wh,
            "battery_combined_charge_power_w": cluster.combined_charge_power_w,
            "battery_combined_discharge_power_w": cluster.combined_discharge_power_w,
            "battery_combined_net_power_w": cluster.combined_net_battery_power_w,
            "battery_combined_ac_power_w": cluster.combined_ac_power_w,
            "battery_combined_pv_input_power_w": cluster.combined_pv_input_power_w,
            "battery_combined_grid_interaction_w": cluster.combined_grid_interaction_w,
            "battery_headroom_charge_w": forecast["battery_headroom_charge_w"],
            "battery_headroom_discharge_w": forecast["battery_headroom_discharge_w"],
            "expected_near_term_export_w": forecast["expected_near_term_export_w"],
            "expected_near_term_import_w": forecast["expected_near_term_import_w"],
            "battery_discharge_balance_mode": discharge_balance.get("mode"),
            "battery_discharge_balance_target_distribution_mode": discharge_balance.get("target_distribution_mode"),
            "battery_discharge_balance_error_w": discharge_balance.get("error_w"),
            "battery_discharge_balance_max_abs_error_w": discharge_balance.get("max_abs_error_w"),
            "battery_discharge_balance_total_discharge_w": discharge_balance.get("total_discharge_w"),
            "battery_discharge_balance_eligible_source_count": discharge_balance.get("eligible_source_count", 0),
            "battery_discharge_balance_active_source_count": discharge_balance.get("active_source_count", 0),
            "battery_discharge_balance_control_candidate_count": discharge_control.get("control_candidate_count", 0),
            "battery_discharge_balance_control_ready_count": discharge_control.get("control_ready_count", 0),
            "battery_discharge_balance_supported_control_source_count": discharge_control.get(
                "supported_control_source_count", 0
            ),
            "battery_discharge_balance_experimental_control_source_count": discharge_control.get(
                "experimental_control_source_count", 0
            ),
            "battery_average_confidence": cluster.average_confidence,
            "battery_source_count": cluster.source_count,
            "battery_online_source_count": cluster.online_source_count,
            "battery_valid_soc_source_count": cluster.valid_soc_source_count,
            "battery_battery_source_count": cluster.battery_source_count,
            "battery_hybrid_inverter_source_count": cluster.hybrid_inverter_source_count,
            "battery_inverter_source_count": cluster.inverter_source_count,
            "battery_sources": source_payloads,
            "battery_learning_profiles": learning_profile_payloads(getattr(self, "_energy_learning_profiles", {})),
        }

    @staticmethod
    def _empty_battery_snapshot() -> dict[str, object]:
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

    def _get_battery_soc(self: Any) -> float | None:
        """Read battery SOC from the aggregated energy snapshot."""
        battery_soc = self._get_battery_snapshot().get("battery_soc")
        return None if battery_soc is None else float(battery_soc)

    @staticmethod
    def _battery_soc_numeric(value: object) -> float | None:
        """Return one numeric battery SOC value from raw DBus data."""
        numeric_value = coerce_dbus_numeric(value)
        if not isinstance(numeric_value, (int, float)):
            return None
        return float(numeric_value)

    def _validated_battery_soc(self: Any, numeric_value: float, service_name: str) -> float | None:
        """Return battery SOC when in range, otherwise warn and back off briefly."""
        if 0.0 <= numeric_value <= 100.0:
            return numeric_value
        self._warning_throttled(
            "auto-helper-battery-soc-invalid",
            max(5.0, self.auto_battery_scan_interval_seconds or 5.0),
            "Auto input helper ignored out-of-range battery SOC %s from %s %s",
            numeric_value,
            service_name,
            self.auto_battery_soc_path,
        )
        self._delay_source_retry("battery")
        return None

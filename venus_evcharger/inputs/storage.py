# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared DBus input-reading helpers for the Venus EV charger service."""

from __future__ import annotations

import logging
import time
from typing import Any

from venus_evcharger.energy import (
    EnergyClusterSnapshot,
    EnergySourceDefinition,
    EnergySourceSnapshot,
    aggregate_energy_sources,
    derive_discharge_balance_metrics,
    derive_discharge_control_metrics,
    derive_energy_forecast,
    read_energy_source_snapshot,
    summarize_energy_learning_profiles,
    update_energy_learning_profiles,
)
from venus_evcharger.inputs.dbus_errors import DBUS_INPUT_READ_ERRORS, DBUS_INPUT_SNAPSHOT_ERRORS
from .energy_snapshot_contracts import (
    energy_source_definitions,
    learning_profile_payloads,
    learning_profiles,
    nested_object_mappings,
    object_mapping,
)
from .storage_support import _DbusInputStorageSupport


class _DbusInputStorage(_DbusInputStorageSupport):

    def _read_optional_energy_value(self, service_name: str, path: str) -> float | None:
        if not path:
            return None
        if self._introspection_says_skip(service_name, path, priority=85):
            return None
        value = self.service.get_dbus_value(service_name, path)
        return self._battery_soc_numeric(value)

    def _read_optional_energy_text(self, service_name: str, path: str) -> str:
        if not path:
            return ""
        if self._introspection_says_skip(service_name, path, priority=85):
            return ""
        value = self.service.get_dbus_value(service_name, path)
        return "" if value is None else str(value).strip()

    def _dbus_energy_source_snapshot(self, source: EnergySourceDefinition, now: float) -> EnergySourceSnapshot:
        service_name = self._resolve_energy_source_service(source)
        try:
            soc_value = self._read_optional_energy_value(service_name, source.soc_path)
            net_battery_power = self._read_optional_energy_value(service_name, source.battery_power_path)
            ac_power = self._read_optional_energy_value(service_name, source.ac_power_path)
            pv_input_power = self._read_optional_energy_value(service_name, source.pv_power_path)
            grid_interaction = self._read_optional_energy_value(service_name, source.grid_interaction_path)
            operating_mode = self._read_optional_energy_text(service_name, source.operating_mode_path)
        except DBUS_INPUT_READ_ERRORS:
            if source.source_id != self._primary_energy_source().source_id:
                raise
            self.service.invalidate_auto_battery_service()
            service_name = self._resolve_energy_source_service(source)
            soc_value = self._read_optional_energy_value(service_name, source.soc_path)
            net_battery_power = self._read_optional_energy_value(service_name, source.battery_power_path)
            ac_power = self._read_optional_energy_value(service_name, source.ac_power_path)
            pv_input_power = self._read_optional_energy_value(service_name, source.pv_power_path)
            grid_interaction = self._read_optional_energy_value(service_name, source.grid_interaction_path)
            operating_mode = self._read_optional_energy_text(service_name, source.operating_mode_path)
        if soc_value is not None and not 0.0 <= soc_value <= 100.0:
            soc_value = None
        return EnergySourceSnapshot(
            source_id=source.source_id,
            role=source.role,
            service_name=service_name,
            soc=soc_value,
            usable_capacity_wh=source.usable_capacity_wh,
            net_battery_power_w=net_battery_power,
            ac_power_w=ac_power,
            pv_input_power_w=pv_input_power,
            grid_interaction_w=grid_interaction,
            operating_mode=operating_mode,
            online=True,
            confidence=1.0,
            captured_at=now,
        )

    def _battery_snapshot_sources(self) -> tuple[EnergySourceDefinition, ...]:
        configured = energy_source_definitions(getattr(self.service, "auto_energy_sources", ()))
        if configured:
            return configured
        return energy_source_definitions(self._primary_energy_source())

    def _battery_snapshot_cluster(
        self,
        now: float,
    ) -> tuple[EnergyClusterSnapshot, tuple[EnergySourceDefinition, ...], list[EnergySourceSnapshot]]:
        sources = self._battery_snapshot_sources()
        source_snapshots = [read_energy_source_snapshot(self, source, now) for source in sources]
        return aggregate_energy_sources(source_snapshots), sources, source_snapshots

    def _battery_snapshot_effective_soc(self, cluster: EnergyClusterSnapshot) -> float | None:
        primary_soc = cluster.sources[0].soc if cluster.sources else None
        return cluster.effective_soc if bool(getattr(self.service, "auto_use_combined_battery_soc", True)) else primary_soc

    @staticmethod
    def _battery_snapshot_validate_soc(effective_soc: float | None, cluster: EnergyClusterSnapshot) -> None:
        if effective_soc is None and not any(source.soc is not None for source in cluster.sources):
            raise TypeError("Battery SOC is not numeric")

    def _battery_snapshot_cache_owner(self) -> Any:
        svc = self.service
        return getattr(svc, "_service", svc)

    def _battery_snapshot_learning_bundle(
        self,
        cache_owner: Any,
        cluster: EnergyClusterSnapshot,
        now: float,
    ) -> tuple[dict[str, object], dict[str, object], dict[str, object]]:
        existing_profiles = learning_profiles(getattr(cache_owner, "_last_energy_learning_profiles", {}))
        updated_profiles = update_energy_learning_profiles(
            existing_profiles,
            cluster.sources,
            now,
        )
        cache_owner._last_energy_learning_profiles = updated_profiles
        learning_summary = summarize_energy_learning_profiles(
            updated_profiles
        )
        discharge_balance = derive_discharge_balance_metrics(
            cluster.sources,
            updated_profiles,
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
    def _battery_snapshot_discharge_control(
        cluster: EnergyClusterSnapshot,
        sources: tuple[EnergySourceDefinition, ...],
    ) -> dict[str, object]:
        return object_mapping(
            derive_discharge_control_metrics(cluster.sources, {source.source_id: source for source in sources})
        )

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

    def _battery_snapshot_payload(
        self,
        cache_owner: Any,
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
                "supported_control_source_count",
                0,
            ),
            "battery_discharge_balance_experimental_control_source_count": discharge_control.get(
                "experimental_control_source_count",
                0,
            ),
            "battery_average_confidence": cluster.average_confidence,
            "battery_source_count": cluster.source_count,
            "battery_online_source_count": cluster.online_source_count,
            "battery_valid_soc_source_count": cluster.valid_soc_source_count,
            "battery_battery_source_count": cluster.battery_source_count,
            "battery_hybrid_inverter_source_count": cluster.hybrid_inverter_source_count,
            "battery_inverter_source_count": cluster.inverter_source_count,
            "battery_sources": source_payloads,
            "battery_learning_profiles": learning_profile_payloads(
                getattr(cache_owner, "_last_energy_learning_profiles", {})
            ),
        }

    @staticmethod
    def _empty_battery_snapshot_payload(failure: float | None) -> dict[str, object]:
        return {
            "battery_soc": failure,
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

    @staticmethod
    def _failure_soc_value(value: object) -> float | None:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        return float(value)

    def _successful_battery_snapshot_payload(self, now: float) -> dict[str, object]:
        cluster, sources, _ = self._battery_snapshot_cluster(now)
        effective_soc = self._battery_snapshot_effective_soc(cluster)
        self._battery_snapshot_validate_soc(effective_soc, cluster)
        cache_owner = self._battery_snapshot_cache_owner()
        learning_summary, discharge_balance, forecast = self._battery_snapshot_learning_bundle(cache_owner, cluster, now)
        discharge_control = self._battery_snapshot_discharge_control(cluster, sources)
        source_payloads = self._battery_snapshot_source_payloads(cluster, discharge_balance, discharge_control)
        self._mark_source_recovery("battery", "Battery SOC readings recovered")
        battery_payload = self._battery_snapshot_payload(
            cache_owner,
            effective_soc,
            cluster,
            forecast,
            discharge_balance,
            discharge_control,
            source_payloads,
        )
        setattr(cache_owner, "_last_energy_cluster", dict(battery_payload))
        return battery_payload

    def _failed_battery_snapshot_payload(self, now: float, error: Exception) -> dict[str, object]:
        svc = self.service
        battery_service_name = str(getattr(svc, "auto_battery_service", "") or "")
        battery_service_prefix = str(getattr(svc, "auto_battery_service_prefix", "") or "")
        failure = self._handle_source_failure(
            "battery",
            now,
            "battery-missing",
            svc.auto_battery_scan_interval_seconds,
            "Auto mode could not read battery SOC from %s %s: %s",
            battery_service_name or battery_service_prefix,
            svc.auto_battery_soc_path,
            error,
        )
        return self._empty_battery_snapshot_payload(self._failure_soc_value(failure))

    def get_battery_snapshot(self) -> dict[str, object]:
        """Return aggregated battery and inverter source data for Auto mode."""
        now = time.time()
        if not self._source_retry_ready("battery", now):
            return {"battery_soc": None}
        try:
            return self._successful_battery_snapshot_payload(now)
        except DBUS_INPUT_SNAPSHOT_ERRORS as error:
            return self._failed_battery_snapshot_payload(now, error)

    def get_battery_soc(self) -> float | None:
        """Read battery SOC from the resolved battery service."""
        snapshot = self.get_battery_snapshot()
        battery_soc = snapshot.get("battery_soc")
        if isinstance(battery_soc, (int, float)):
            return float(battery_soc)
        return None

    def _read_battery_soc_value(self) -> object:
        """Read one raw battery SOC value, retrying once after invalidating cached service discovery."""
        svc = self.service
        service_name = svc.resolve_auto_battery_service()
        try:
            return svc.get_dbus_value(service_name, svc.auto_battery_soc_path)
        except DBUS_INPUT_READ_ERRORS:
            svc.invalidate_auto_battery_service()
            service_name = svc.resolve_auto_battery_service()
            return svc.get_dbus_value(service_name, svc.auto_battery_soc_path)

    def _battery_soc_numeric(self, value: object) -> float | None:
        """Return one numeric battery SOC value after DBus coercion."""
        coerced_value = self._coerce_dbus_value(value)
        if not isinstance(coerced_value, (int, float)):
            return None
        return float(coerced_value)

    def get_grid_power(self) -> float | None:
        """Read and sum grid power from per-phase paths."""
        now = time.time()
        if not self._source_retry_ready("grid", now):
            return None
        configured_paths = self._configured_grid_paths()
        if not configured_paths:
            return None
        total, seen_value, missing_paths = self._read_grid_phase_values(configured_paths)
        return self._finalize_grid_power(total, seen_value, missing_paths, now)

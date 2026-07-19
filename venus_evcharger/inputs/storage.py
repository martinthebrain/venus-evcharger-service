# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared DBus input-reading helpers for the Venus EV charger service."""

from __future__ import annotations

import time

from venus_evcharger.energy import (
    EnergyClusterSnapshot,
    EnergyLearningProfile,
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
from venus_evcharger.dbus_gateway import BATTERY_SOC_READ_KEY, GRID_POWER_READ_KEY
from venus_evcharger.inputs.gateway_read import GatewayReadPort, SourceHealthPort, numeric_gateway_value
from venus_evcharger.ports.dbus import DbusInputReaderPort
from .energy_snapshot_contracts import (
    energy_source_definitions,
    learning_profile_payloads,
    learning_profiles,
    nested_object_mappings,
    object_mapping,
)
from .storage_support import EnergyServicePort


_EnergySourceValues = tuple[
    float | None,
    float | None,
    float | None,
    float | None,
    float | None,
    str,
]

_EMPTY_ENERGY_SOURCE_VALUES: _EnergySourceValues = (None, None, None, None, None, "")


class StorageInputReader:
    """Read battery, storage, and grid state through gateway contracts."""

    def __init__(
        self,
        port: DbusInputReaderPort,
        gateway: GatewayReadPort,
        health: SourceHealthPort,
        resolver: EnergyServicePort,
    ) -> None:
        self._port = port
        self._gateway = gateway
        self._health = health
        self._resolver = resolver

    def _read_optional_energy_value(self, service_name: str, path: str) -> float | None:
        if not path:
            return None
        if self._resolver.introspection_says_skip(service_name, path, priority=85):
            return None
        value = self._gateway.get_dbus_value(service_name, path)
        return self._battery_soc_numeric(value)

    def _read_optional_energy_text(self, service_name: str, path: str) -> str:
        if not path:
            return ""
        if self._resolver.introspection_says_skip(service_name, path, priority=85):
            return ""
        value = self._gateway.get_dbus_value(service_name, path)
        return "" if value is None else str(value).strip()

    def _read_energy_source_values(
        self,
        service_name: str,
        source: EnergySourceDefinition,
    ) -> _EnergySourceValues:
        return (
            self._read_optional_energy_value(service_name, source.soc_path),
            self._read_optional_energy_value(service_name, source.battery_power_path),
            self._read_optional_energy_value(service_name, source.ac_power_path),
            self._read_optional_energy_value(service_name, source.pv_power_path),
            self._read_optional_energy_value(service_name, source.grid_interaction_path),
            self._read_optional_energy_text(service_name, source.operating_mode_path),
        )

    def _try_read_energy_source_values(
        self,
        service_name: str,
        source: EnergySourceDefinition,
    ) -> _EnergySourceValues | None:
        try:
            return self._read_energy_source_values(service_name, source)
        except DBUS_INPUT_READ_ERRORS:
            return None

    def _usable_energy_source_values(
        self,
        source: EnergySourceDefinition,
        values: _EnergySourceValues | None,
    ) -> _EnergySourceValues | None:
        if values is None:
            return None
        primary_source_id = self._resolver.primary_energy_source().source_id
        if source.source_id == primary_source_id and values == _EMPTY_ENERGY_SOURCE_VALUES:
            return None
        return values

    def _retry_energy_source_values(
        self,
        source: EnergySourceDefinition,
        failed_service_name: str,
    ) -> tuple[str, _EnergySourceValues | None]:
        if source.source_id != self._resolver.primary_energy_source().source_id:
            self._resolver.invalidate_energy_source_service(
                source.source_id,
                expected_service=failed_service_name,
            )
            return failed_service_name, None
        self._resolver.invalidate_auto_battery_service()
        service_name = self._resolver.resolve_energy_source_service(source)
        values = self._try_read_energy_source_values(service_name, source)
        return service_name, self._usable_energy_source_values(source, values)

    def _dbus_energy_source_snapshot(self, source: EnergySourceDefinition, now: float) -> EnergySourceSnapshot:
        service_name = self._resolver.resolve_energy_source_service(source)
        values = self._usable_energy_source_values(
            source,
            self._try_read_energy_source_values(service_name, source),
        )
        if values is None:
            service_name, values = self._retry_energy_source_values(source, service_name)
        if values is None:
            return self._offline_energy_source_snapshot(source, now, service_name)
        return self._online_energy_source_snapshot(source, now, service_name, values)

    @staticmethod
    def _online_energy_source_snapshot(
        source: EnergySourceDefinition,
        now: float,
        service_name: str,
        values: _EnergySourceValues,
    ) -> EnergySourceSnapshot:
        soc_value, net_battery_power, ac_power, pv_input_power, grid_interaction, operating_mode = values
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
        configured = energy_source_definitions(self._port.service.auto_energy_sources)
        if configured:
            return configured
        return energy_source_definitions(self._resolver.primary_energy_source())

    def _battery_snapshot_cluster(
        self,
        now: float,
    ) -> tuple[EnergyClusterSnapshot, tuple[EnergySourceDefinition, ...]]:
        sources = self._battery_snapshot_sources()
        source_snapshots = [self._battery_snapshot_source(source, now) for source in sources]
        return aggregate_energy_sources(source_snapshots), sources

    def _battery_snapshot_source(
        self,
        source: EnergySourceDefinition,
        now: float,
    ) -> EnergySourceSnapshot:
        try:
            return read_energy_source_snapshot(self, source, now)
        except DBUS_INPUT_SNAPSHOT_ERRORS:
            return self._offline_energy_source_snapshot(source, now)

    @staticmethod
    def _offline_energy_source_snapshot(
        source: EnergySourceDefinition,
        now: float,
        service_name: str | None = None,
    ) -> EnergySourceSnapshot:
        return EnergySourceSnapshot(
            source_id=source.source_id,
            role=source.role,
            service_name=service_name or source.service_name or source.service_prefix,
            usable_capacity_wh=source.usable_capacity_wh,
            battery_chemistry=source.battery_chemistry,
            captured_at=now,
        )

    def _battery_snapshot_effective_soc(self, cluster: EnergyClusterSnapshot) -> float | None:
        primary_soc = cluster.sources[0].soc if cluster.sources else None
        return (
            cluster.effective_soc
            if self._port.service.auto_use_combined_battery_soc
            else primary_soc
        )

    @staticmethod
    def _battery_snapshot_validate_soc(effective_soc: float | None) -> None:
        if effective_soc is None:
            raise TypeError("Battery SOC is not numeric")

    def _battery_snapshot_learning_bundle(
        self,
        cluster: EnergyClusterSnapshot,
        now: float,
    ) -> tuple[
        dict[str, EnergyLearningProfile],
        dict[str, object],
        dict[str, object],
        dict[str, object],
    ]:
        existing_profiles = learning_profiles(self._port.energy_learning_profiles())
        updated_profiles = update_energy_learning_profiles(
            existing_profiles,
            cluster.sources,
            now,
        )
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
                "BATTERY_COMBINED_CHARGE_LIMIT_POWER_W": cluster.combined_charge_limit_power_w,
                "battery_combined_discharge_limit_power_w": cluster.combined_discharge_limit_power_w,
                "battery_combined_grid_interaction_w": cluster.combined_grid_interaction_w,
            },
            learning_summary,
        )
        return (
            updated_profiles,
            object_mapping(learning_summary),
            object_mapping(discharge_balance),
            object_mapping(forecast),
        )

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
        source_balance = nested_object_mappings(discharge_balance.get("sources"))
        source_control = nested_object_mappings(discharge_control.get("sources"))
        for source_payload in source_payloads:
            source_id = str(source_payload["source_id"])
            source_payload.update(source_balance.get(source_id) or {})
            source_payload.update(source_control.get(source_id) or {})
        return source_payloads

    def _battery_snapshot_payload(
        self,
        effective_soc: float | None,
        cluster: EnergyClusterSnapshot,
        forecast: dict[str, object],
        discharge_balance: dict[str, object],
        discharge_control: dict[str, object],
        source_payloads: list[dict[str, object]],
        updated_profiles: dict[str, EnergyLearningProfile] | None = None,
    ) -> dict[str, object]:
        profile_state = self._port.energy_learning_profiles() if updated_profiles is None else updated_profiles
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
            "battery_learning_profiles": learning_profile_payloads(profile_state),
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

    def _successful_battery_snapshot_payload(self, now: float) -> dict[str, object]:
        cluster, sources = self._battery_snapshot_cluster(now)
        effective_soc = self._battery_snapshot_effective_soc(cluster)
        self._battery_snapshot_validate_soc(effective_soc)
        updated_profiles, _, discharge_balance, forecast = self._battery_snapshot_learning_bundle(cluster, now)
        discharge_control = self._battery_snapshot_discharge_control(cluster, sources)
        source_payloads = self._battery_snapshot_source_payloads(cluster, discharge_balance, discharge_control)
        battery_payload = self._battery_snapshot_payload(
            effective_soc,
            cluster,
            forecast,
            discharge_balance,
            discharge_control,
            source_payloads,
            updated_profiles,
        )
        self._port.store_energy_learning_profiles(updated_profiles)
        self._port.store_energy_cluster(battery_payload)
        self._health.recovered("battery", "Battery SOC readings recovered")
        return battery_payload

    def _reset_battery_snapshot(self) -> dict[str, object]:
        payload = self._empty_battery_snapshot_payload(None)
        self._port.store_energy_cluster(payload)
        return payload

    def _failed_battery_snapshot_payload(self, now: float, error: Exception) -> dict[str, object]:
        svc = self._port.service
        battery_service_name = svc.auto_battery_service
        battery_service_prefix = svc.auto_battery_service_prefix
        payload = self._reset_battery_snapshot()
        self._health.failed(
            "battery",
            now,
            "battery-missing",
            svc.auto_battery_scan_interval_seconds,
            "Auto mode could not read battery SOC from %s %s: %s",
            battery_service_name or battery_service_prefix,
            svc.auto_battery_soc_path,
            error,
        )
        return payload

    def get_battery_snapshot(self) -> dict[str, object]:
        """Return aggregated battery and inverter source data for Auto mode."""
        now = time.time()
        if not self._health.retry_ready("battery", now):
            return self._reset_battery_snapshot()
        try:
            return self._successful_battery_snapshot_payload(now)
        except DBUS_INPUT_SNAPSHOT_ERRORS as error:
            return self._failed_battery_snapshot_payload(now, error)

    def get_battery_soc(self) -> float | None:
        """Read battery SOC from the gateway read contract."""
        now = time.time()
        if not self._health.retry_ready("battery", now):
            return None
        battery_soc = self._gateway.read_semantic_value(BATTERY_SOC_READ_KEY, reason="main semantic battery SOC read")
        numeric_soc = self._gateway_numeric_value(battery_soc)
        if numeric_soc is not None and 0.0 <= numeric_soc <= 100.0:
            self._health.recovered("battery", "Battery SOC readings recovered")
            return numeric_soc
        self._health.failed(
            "battery",
            now,
            "battery-missing",
            self._port.service.auto_battery_scan_interval_seconds,
            "Auto mode could not read battery SOC from the DBus gateway read contract.",
        )
        return None

    def _battery_soc_numeric(self, value: object) -> float | None:
        """Return one numeric battery SOC value after DBus coercion."""
        return self._gateway_numeric_value(numeric_gateway_value(value))

    @staticmethod
    def _gateway_numeric_value(value: object) -> float | None:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        return float(value)

    def get_grid_power(self) -> float | None:
        """Read grid power from the gateway read contract."""
        now = time.time()
        if not self._health.retry_ready("grid", now):
            return None
        grid_power = self._gateway.read_semantic_value(GRID_POWER_READ_KEY, reason="main semantic grid power read")
        numeric_grid_power = self._gateway_numeric_value(grid_power)
        if numeric_grid_power is not None:
            self._health.recovered("grid", "Grid readings recovered")
            return numeric_grid_power
        self._handle_missing_grid_value(now)
        return None

    def _handle_missing_grid_value(self, now: float) -> None:
        self._health.failed(
            "grid",
            now,
            "grid-missing",
            self._port.service.auto_pv_scan_interval_seconds,
            "Auto mode could not read grid power from %s.",
            self._port.service.auto_grid_service,
        )

# SPDX-License-Identifier: GPL-3.0-or-later
"""Snapshot and capacity helpers for auto-input DBus sources."""

from __future__ import annotations

import logging
import time
from collections.abc import Mapping
from typing import Any, TypeGuard

from venus_evcharger.energy import EnergySourceDefinition, EnergySourceSnapshot
from venus_evcharger.energy.numeric import optional_float, optional_int
from venus_evcharger.inputs.helper.capacity_persistence import (
    configured_estimated_capacity_payload,
    persist_estimated_capacity_if_ah_changed,
)
from venus_evcharger.inputs.helper.sources_dbus_common import DBUS_SOURCE_READ_ERRORS
from venus_evcharger.inputs.helper.sources_dbus_resolve import _AutoInputHelperSourceDbusResolve


def _capacity_payload_mapping(value: object) -> dict[str, object] | None:
    if not isinstance(value, Mapping):
        return None
    return {str(key): item for key, item in value.items()}


def _capacity_payload_float(payload: Mapping[str, object], key: str) -> float | None:
    value = payload.get(key)
    if isinstance(value, bool):
        return None
    return optional_float(value)


def _capacity_payload_int(payload: Mapping[str, object], key: str) -> int | None:
    return optional_int(payload.get(key))


class _AutoInputHelperSourceDbusSnapshot(_AutoInputHelperSourceDbusResolve):
    def _read_dbus_energy_source_fields(
        self: Any,
        source: EnergySourceDefinition,
        service_name: str,
    ) -> tuple[float | None, float | None, float | None, float | None, float | None, str]:
        return (
            self._read_optional_energy_value(service_name, source.soc_path),
            self._read_optional_energy_value(service_name, source.battery_power_path),
            self._read_optional_energy_value(service_name, source.ac_power_path),
            self._read_optional_energy_value(service_name, source.pv_power_path),
            self._read_optional_energy_value(service_name, source.grid_interaction_path),
            self._read_optional_energy_text(service_name, source.operating_mode_path),
        )

    def _read_dbus_energy_source_fields_with_primary_retry(
        self: Any,
        source: EnergySourceDefinition,
    ) -> tuple[str, tuple[float | None, float | None, float | None, float | None, float | None, str]]:
        service_name = self._resolve_energy_source_service(source)
        try:
            return service_name, self._read_dbus_energy_source_fields(source, service_name)
        except DBUS_SOURCE_READ_ERRORS:
            if source.source_id != self._primary_energy_source().source_id:
                raise
            self._invalidate_auto_battery_service()
            service_name = self._resolve_energy_source_service(source)
            return service_name, self._read_dbus_energy_source_fields(source, service_name)

    def _validated_energy_source_soc(
        self: Any,
        source: EnergySourceDefinition,
        service_name: str,
        soc_value: float | None,
    ) -> float | None:
        if soc_value is None or 0.0 <= soc_value <= 100.0:
            return soc_value
        self._warning_throttled(
            "auto-helper-battery-soc-invalid",
            max(5.0, self.auto_battery_scan_interval_seconds or 5.0),
            "Auto input helper ignored out-of-range battery SOC %s from %s %s",
            soc_value,
            service_name,
            source.soc_path,
        )
        self._delay_source_retry("battery")
        return None

    def _dbus_energy_source_snapshot_payload(
        self: Any,
        source: EnergySourceDefinition,
        service_name: str,
        soc_value: float | None,
        net_battery_power: float | None,
        ac_power: float | None,
        pv_input_power: float | None,
        grid_interaction: float | None,
        operating_mode: str,
        now: float,
    ) -> EnergySourceSnapshot:
        capacity_payload = self._dbus_energy_source_capacity_payload(source, service_name, soc_value, now)
        return EnergySourceSnapshot(
            source_id=source.source_id,
            role=source.role,
            service_name=service_name,
            soc=soc_value,
            usable_capacity_wh=_capacity_payload_float(capacity_payload, "usable_capacity_wh"),
            usable_capacity_source=str(capacity_payload.get("usable_capacity_source") or ""),
            installed_capacity_ah=_capacity_payload_float(capacity_payload, "installed_capacity_ah"),
            capacity_voltage_v=_capacity_payload_float(capacity_payload, "capacity_voltage_v"),
            capacity_nominal_voltage_v=_capacity_payload_float(capacity_payload, "capacity_nominal_voltage_v"),
            capacity_cell_count=_capacity_payload_int(capacity_payload, "capacity_cell_count"),
            battery_chemistry=source.battery_chemistry,
            net_battery_power_w=net_battery_power,
            ac_power_w=ac_power,
            pv_input_power_w=pv_input_power,
            grid_interaction_w=grid_interaction,
            operating_mode=operating_mode,
            online=True,
            confidence=1.0,
            captured_at=now,
        )

    def _dbus_energy_source_capacity_payload(
        self: Any,
        source: EnergySourceDefinition,
        service_name: str,
        soc_value: float | None,
        now: float,
    ) -> dict[str, object]:
        configured = _AutoInputHelperSourceDbusSnapshot._configured_dbus_capacity_payload(source)
        if configured is not None:
            return configured
        cached = _AutoInputHelperSourceDbusSnapshot._cached_dbus_capacity_payload(self, source, service_name)
        startup_recheck_due = _AutoInputHelperSourceDbusSnapshot._dbus_capacity_startup_recheck_due(
            self,
            source,
            service_name,
            now,
        )
        if _AutoInputHelperSourceDbusSnapshot._dbus_cached_capacity_usable(cached, startup_recheck_due):
            return cached
        inferred = _AutoInputHelperSourceDbusSnapshot._fresh_dbus_capacity_payload(
            self,
            source,
            service_name,
            soc_value,
            startup_recheck_due,
        )
        return _AutoInputHelperSourceDbusSnapshot._resolved_dbus_capacity_payload(inferred, cached)

    @staticmethod
    def _dbus_cached_capacity_usable(
        cached: dict[str, object] | None,
        startup_recheck_due: bool,
    ) -> TypeGuard[dict[str, object]]:
        return cached is not None and not startup_recheck_due

    @staticmethod
    def _resolved_dbus_capacity_payload(
        inferred: dict[str, object] | None,
        cached: dict[str, object] | None,
    ) -> dict[str, object]:
        if inferred is not None:
            return inferred
        if cached is not None:
            return cached
        return {"usable_capacity_wh": None, "usable_capacity_source": ""}

    def _fresh_dbus_capacity_payload(
        self: Any,
        source: EnergySourceDefinition,
        service_name: str,
        soc_value: float | None,
        startup_recheck_due: bool,
    ) -> dict[str, object] | None:
        inferred = _AutoInputHelperSourceDbusSnapshot._infer_dbus_capacity_payload(
            self,
            source,
            service_name,
            soc_value,
        )
        if inferred is None:
            return None
        self._store_dbus_capacity_payload(source, service_name, inferred, startup_recheck_done=startup_recheck_due)
        self._persist_dbus_capacity_payload_if_needed(source, inferred, startup_recheck_due)
        return inferred

    @staticmethod
    def _configured_dbus_capacity_payload(source: EnergySourceDefinition) -> dict[str, object] | None:
        if source.usable_capacity_wh is None or source.usable_capacity_wh <= 0.0:
            return None
        return {"usable_capacity_wh": source.usable_capacity_wh, "usable_capacity_source": "configured"}

    def _cached_dbus_capacity_payload(
        self: Any,
        source: EnergySourceDefinition,
        service_name: str,
    ) -> dict[str, object] | None:
        key = self._dbus_capacity_cache_key(source, service_name)
        estimates = getattr(self, "_auto_battery_capacity_estimates", None)
        cached = estimates.get(key) if isinstance(estimates, dict) else None
        return _capacity_payload_mapping(cached) or configured_estimated_capacity_payload(source)

    def _persist_dbus_capacity_payload_if_needed(
        self: Any,
        source: EnergySourceDefinition,
        payload: dict[str, object],
        startup_recheck_done: bool,
    ) -> None:
        if not startup_recheck_done:
            return
        try:
            config_path = getattr(self, "config_path", None)
            changed = persist_estimated_capacity_if_ah_changed("" if config_path is None else str(config_path), source, payload)
        except (OSError, RuntimeError, TypeError, ValueError) as error:
            logging.warning("Unable to persist auto-estimated battery capacity: %s", error)
            return
        if changed:
            logging.info("Persisted auto-estimated battery capacity for source %s", source.source_id)

    def _dbus_capacity_startup_recheck_due(
        self: Any,
        source: EnergySourceDefinition,
        service_name: str,
        now: float,
    ) -> bool:
        if not self._dbus_capacity_startup_recheck_time_due(source, now):
            return False
        return not self._dbus_capacity_startup_recheck_seen(source, service_name)

    def _dbus_capacity_startup_recheck_time_due(self: Any, source: EnergySourceDefinition, now: float) -> bool:
        raw_recheck_at = getattr(self, "_auto_battery_capacity_startup_recheck_at", None)
        recheck_at = 0.0 if raw_recheck_at is None else float(raw_recheck_at)
        return bool(source.capacity_startup_recheck_seconds > 0.0 and recheck_at > 0.0 and now >= recheck_at)

    def _dbus_capacity_startup_recheck_seen(self: Any, source: EnergySourceDefinition, service_name: str) -> bool:
        rechecked = getattr(self, "_auto_battery_capacity_startup_rechecked", None)
        key = self._dbus_capacity_cache_key(source, service_name)
        return bool(isinstance(rechecked, dict) and rechecked.get(key))

    def _store_dbus_capacity_payload(
        self: Any,
        source: EnergySourceDefinition,
        service_name: str,
        payload: dict[str, object],
        *,
        startup_recheck_done: bool,
    ) -> None:
        if not isinstance(getattr(self, "_auto_battery_capacity_estimates", None), dict):
            self._auto_battery_capacity_estimates = {}
        key = self._dbus_capacity_cache_key(source, service_name)
        self._auto_battery_capacity_estimates[key] = dict(payload)
        if startup_recheck_done:
            if not isinstance(getattr(self, "_auto_battery_capacity_startup_rechecked", None), dict):
                self._auto_battery_capacity_startup_rechecked = {}
            self._auto_battery_capacity_startup_rechecked[key] = True

    @staticmethod
    def _dbus_capacity_cache_key(source: EnergySourceDefinition, service_name: str) -> str:
        return f"{source.source_id}:{service_name}"

    def _infer_dbus_capacity_payload(
        self: Any,
        source: EnergySourceDefinition,
        service_name: str,
        soc_value: float | None,
    ) -> dict[str, object] | None:
        if not _AutoInputHelperSourceDbusSnapshot._dbus_capacity_inference_allowed(source, soc_value):
            return None
        direct_capacity = self._read_positive_optional_energy_value(service_name, source.capacity_wh_path)
        if direct_capacity is not None:
            return _AutoInputHelperSourceDbusSnapshot._direct_dbus_capacity_payload(direct_capacity)
        installed_capacity_ah = self._read_positive_optional_energy_value(service_name, source.capacity_ah_path)
        voltage = self._read_positive_optional_energy_value(service_name, source.voltage_path)
        return _AutoInputHelperSourceDbusSnapshot._lfp_inferred_dbus_capacity_payload(
            self,
            installed_capacity_ah,
            voltage,
        )

    @staticmethod
    def _dbus_capacity_inference_allowed(source: EnergySourceDefinition, soc_value: float | None) -> bool:
        if not source.capacity_auto_estimate or source.battery_chemistry.strip().lower() != "lfp":
            return False
        return bool(soc_value is not None and float(soc_value) >= float(source.capacity_estimate_min_soc))

    @staticmethod
    def _direct_dbus_capacity_payload(direct_capacity: float) -> dict[str, object]:
        return {
            "usable_capacity_wh": direct_capacity,
            "usable_capacity_source": "dbus_capacity_wh",
        }

    def _lfp_inferred_dbus_capacity_payload(
        self: Any,
        installed_capacity_ah: float | None,
        voltage: float | None,
    ) -> dict[str, object] | None:
        nominal_voltage, cell_count = self._lfp_nominal_voltage_from_full_voltage(voltage)
        if installed_capacity_ah is None or nominal_voltage is None or cell_count is None:
            return None
        return {
            "usable_capacity_wh": installed_capacity_ah * nominal_voltage,
            "usable_capacity_source": "dbus_lfp_inferred",
            "installed_capacity_ah": installed_capacity_ah,
            "capacity_voltage_v": voltage,
            "capacity_nominal_voltage_v": nominal_voltage,
            "capacity_cell_count": cell_count,
        }

    def _read_positive_optional_energy_value(self: Any, service_name: str, path: str) -> float | None:
        value = self._read_optional_energy_value(service_name, path)
        return value if value is not None and value > 0.0 else None

    @staticmethod
    def _lfp_nominal_voltage_from_full_voltage(voltage: float | None) -> tuple[float | None, int | None]:
        if voltage is None or voltage < 40.0 or voltage > 60.0:
            return None, None
        cell_count = 15 if voltage < 52.5 else 16
        return cell_count * 3.2, cell_count

    def _dbus_energy_source_snapshot(self: Any, source: EnergySourceDefinition, now: float) -> EnergySourceSnapshot:
        service_name, fields = self._read_dbus_energy_source_fields_with_primary_retry(source)
        (
            soc_value,
            net_battery_power,
            ac_power,
            pv_input_power,
            grid_interaction,
            operating_mode,
        ) = fields
        validated_soc = self._validated_energy_source_soc(source, service_name, soc_value)
        return _AutoInputHelperSourceDbusSnapshot._dbus_energy_source_snapshot_payload(
            self,
            source,
            service_name,
            validated_soc,
            net_battery_power,
            ac_power,
            pv_input_power,
            grid_interaction,
            operating_mode,
            now,
        )

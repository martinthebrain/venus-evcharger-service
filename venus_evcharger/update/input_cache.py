# SPDX-License-Identifier: GPL-3.0-or-later
"""Cached helper-input resolution for the update cycle."""

from __future__ import annotations

from collections.abc import Mapping
from typing import ClassVar, Protocol

from venus_evcharger.core.contracts import finite_float_or_none, timestamp_not_future
class InputCacheService(Protocol):
    auto_input_cache_seconds: float
    auto_input_validation_poll_seconds: float
    dbus_gateway_max_age_seconds: float
    auto_pv_poll_interval_seconds: float
    auto_grid_poll_interval_seconds: float
    auto_battery_poll_interval_seconds: float
    _auto_cached_inputs_used: bool
    _error_state: dict[str, int]
    _last_energy_cluster: dict[str, object]
    _last_energy_learning_profiles: dict[str, object]
    _last_pv_value: float | None
    _last_pv_at: float | None
    _last_grid_value: float | None
    _last_grid_at: float | None
    _last_battery_soc_value: float | None
    _last_battery_soc_at: float | None
    _last_combined_battery_charge_power_w: float | None
    _last_combined_battery_charge_power_at: float | None
    _last_combined_battery_discharge_power_w: float | None
    _last_combined_battery_discharge_power_at: float | None
    _last_combined_battery_net_power_w: float | None
    _last_combined_battery_net_power_at: float | None
    _last_combined_battery_ac_power_w: float | None
    _last_combined_battery_ac_power_at: float | None


class InputCacheResolver:
    FUTURE_INPUT_TIMESTAMP_TOLERANCE_SECONDS: ClassVar[float] = 1.0

    def __init__(self, service: InputCacheService) -> None:
        self.service = service

    @staticmethod
    def extract_pm_measurements(
        svc: object,
        pm_status: dict[str, object],
    ) -> tuple[bool, float, float, float, float]:
        """Extract normalized relay/power/current/energy values from a Shelly status dict."""
        relay_on = bool(pm_status.get("output"))
        power = InputCacheResolver._float_or_default(pm_status.get("apower", 0.0))
        voltage = InputCacheResolver._float_or_default(pm_status.get("voltage", 0.0))
        current = InputCacheResolver._float_or_default(pm_status.get("current", 0.0))
        raw_energy = pm_status.get("aenergy")
        energy_values = raw_energy if isinstance(raw_energy, Mapping) else {}
        energy_forward = InputCacheResolver._float_or_default(energy_values.get("total", 0.0)) / 1000.0
        return relay_on, power, voltage, current, energy_forward

    @classmethod
    def resolve_cached_input_value(
        cls,
        svc: InputCacheService,
        value: float | None,
        snapshot_at: float | None,
        last_value_attr: str,
        last_at_attr: str,
        now: float,
        max_age_seconds: float | None = None,
    ) -> tuple[float | None, bool]:
        """Use fresh input values immediately and short-lived cached values as fallback."""
        cache_owner = svc
        cache_max_age = float(svc.auto_input_cache_seconds)
        if max_age_seconds is not None:
            cache_max_age = min(cache_max_age, float(max_age_seconds))
        value, snapshot_at = cls._discard_invalid_snapshot_input(
            value,
            snapshot_at,
            now,
            max_age_seconds,
        )
        if value is not None:
            setattr(cache_owner, last_value_attr, value)
            setattr(cache_owner, last_at_attr, now if snapshot_at is None else float(snapshot_at))
            return value, False
        return cls._cached_input_from_service(
            cache_owner,
            last_value_attr,
            last_at_attr,
            now,
            cache_max_age,
        )

    @classmethod
    def _discard_invalid_snapshot_input(
        cls,
        value: float | None,
        snapshot_at: float | None,
        now: float,
        max_age_seconds: float | None,
    ) -> tuple[float | None, float | None]:
        """Drop future or over-age source values before cache fallback is considered."""
        if value is None or snapshot_at is None:
            return value, snapshot_at
        snapshot_time = float(snapshot_at)
        if cls._snapshot_input_from_future(snapshot_time, now):
            return None, None
        if cls._snapshot_input_too_old(snapshot_time, now, max_age_seconds):
            return None, None
        return value, snapshot_time

    @classmethod
    def _snapshot_input_from_future(cls, snapshot_time: float, now: float) -> bool:
        """Return True when one helper-fed source timestamp lies in the future."""
        return not timestamp_not_future(
            snapshot_time,
            now,
            cls.FUTURE_INPUT_TIMESTAMP_TOLERANCE_SECONDS,
        )

    @staticmethod
    def _snapshot_input_too_old(
        snapshot_time: float,
        now: float,
        max_age_seconds: float | None,
    ) -> bool:
        """Return True when one helper-fed source timestamp exceeds its max age."""
        return max_age_seconds is not None and (float(now) - snapshot_time) > float(max_age_seconds)

    @classmethod
    def _cached_input_from_service(
        cls,
        cache_owner: InputCacheService,
        last_value_attr: str,
        last_at_attr: str,
        now: float,
        cache_max_age: float,
    ) -> tuple[float | None, bool]:
        """Return a recent cached helper-fed value when direct input is unavailable."""
        last_value = getattr(cache_owner, last_value_attr, None)
        last_at = getattr(cache_owner, last_at_attr, None)
        if (
            last_value is not None
            and last_at is not None
            and last_at <= (float(now) + cls.FUTURE_INPUT_TIMESTAMP_TOLERANCE_SECONDS)
            and (now - last_at) <= cache_max_age
        ):
            return finite_float_or_none(last_value), True
        return None, False

    @staticmethod
    def _auto_input_source_max_age_seconds(svc: InputCacheService, poll_interval_attr: str) -> float:
        """Return the maximum tolerated age for one helper-fed source value."""
        raw_poll_interval = getattr(svc, poll_interval_attr, None)
        poll_interval_seconds = 0.0 if raw_poll_interval is None else max(0.0, float(raw_poll_interval))
        raw_validation = getattr(svc, "auto_input_validation_poll_seconds", None)
        validation_seconds = 30.0 if raw_validation is None or float(raw_validation) == 0.0 else float(raw_validation)
        raw_gateway_max_age = getattr(svc, "dbus_gateway_max_age_seconds", None)
        gateway_max_age_seconds = 0.0 if raw_gateway_max_age is None else max(0.0, float(raw_gateway_max_age))
        poll_budget_seconds = poll_interval_seconds * 2.0
        source_budget_seconds = max(gateway_max_age_seconds, poll_budget_seconds)
        freshness_limit = validation_seconds if source_budget_seconds <= 0.0 else min(
            validation_seconds,
            source_budget_seconds,
        )
        return max(1.0, freshness_limit)

    def resolve_auto_inputs(
        self,
        worker_snapshot: dict[str, object],
        now: float,
        auto_mode_active: bool,
    ) -> tuple[float | None, float | None, float | None]:
        """Resolve Auto inputs from helper snapshots with short cache fallback."""
        svc = self.service
        if not auto_mode_active:
            svc._auto_cached_inputs_used = False
            return None, None, None
        pv_power, pv_cached = self._resolve_auto_input_metric(
            svc,
            worker_snapshot,
            now,
            value_key="pv_power",
            captured_at_key="pv_captured_at",
            last_value_attr="_last_pv_value",
            last_at_attr="_last_pv_at",
            poll_interval_attr="auto_pv_poll_interval_seconds",
        )
        grid_power, grid_cached = self._resolve_auto_input_metric(
            svc,
            worker_snapshot,
            now,
            value_key="grid_power",
            captured_at_key="grid_captured_at",
            last_value_attr="_last_grid_value",
            last_at_attr="_last_grid_at",
            poll_interval_attr="auto_grid_poll_interval_seconds",
        )
        battery_soc, battery_cached = self._resolve_auto_input_metric(
            svc,
            worker_snapshot,
            now,
            value_key="battery_soc",
            captured_at_key="battery_captured_at",
            last_value_attr="_last_battery_soc_value",
            last_at_attr="_last_battery_soc_at",
            poll_interval_attr="auto_battery_poll_interval_seconds",
        )
        combined_charge_power = self._resolve_battery_cluster_metric(
            svc,
            worker_snapshot,
            now,
            value_key="battery_combined_charge_power_w",
            last_value_attr="_last_combined_battery_charge_power_w",
            last_at_attr="_last_combined_battery_charge_power_at",
        )
        combined_discharge_power = self._resolve_battery_cluster_metric(
            svc,
            worker_snapshot,
            now,
            value_key="battery_combined_discharge_power_w",
            last_value_attr="_last_combined_battery_discharge_power_w",
            last_at_attr="_last_combined_battery_discharge_power_at",
        )
        combined_net_power = self._resolve_battery_cluster_metric(
            svc,
            worker_snapshot,
            now,
            value_key="battery_combined_net_power_w",
            last_value_attr="_last_combined_battery_net_power_w",
            last_at_attr="_last_combined_battery_net_power_at",
        )
        combined_ac_power = self._resolve_battery_cluster_metric(
            svc,
            worker_snapshot,
            now,
            value_key="battery_combined_ac_power_w",
            last_value_attr="_last_combined_battery_ac_power_w",
            last_at_attr="_last_combined_battery_ac_power_at",
        )
        svc._last_energy_cluster = self._energy_cluster_snapshot(
            worker_snapshot,
            combined_charge_power=combined_charge_power,
            combined_discharge_power=combined_discharge_power,
            combined_net_power=combined_net_power,
            combined_ac_power=combined_ac_power,
        )
        self._store_learning_profiles(svc, worker_snapshot)
        svc._auto_cached_inputs_used = pv_cached or grid_cached or battery_cached
        if svc._auto_cached_inputs_used:
            svc._error_state["cache_hits"] += 1
        return pv_power, battery_soc, grid_power

    def _resolve_auto_input_metric(
        self,
        svc: InputCacheService,
        worker_snapshot: dict[str, object],
        now: float,
        *,
        value_key: str,
        captured_at_key: str,
        last_value_attr: str,
        last_at_attr: str,
        poll_interval_attr: str,
    ) -> tuple[float | None, bool]:
        return self.resolve_cached_input_value(
            svc,
            finite_float_or_none(worker_snapshot.get(value_key)),
            finite_float_or_none(worker_snapshot.get(captured_at_key, worker_snapshot.get("captured_at"))),
            last_value_attr,
            last_at_attr,
            now,
            max_age_seconds=self._auto_input_source_max_age_seconds(svc, poll_interval_attr),
        )

    def _resolve_battery_cluster_metric(
        self,
        svc: InputCacheService,
        worker_snapshot: dict[str, object],
        now: float,
        *,
        value_key: str,
        last_value_attr: str,
        last_at_attr: str,
    ) -> float | None:
        value, _ = self._resolve_auto_input_metric(
            svc,
            worker_snapshot,
            now,
            value_key=value_key,
            captured_at_key="battery_captured_at",
            last_value_attr=last_value_attr,
            last_at_attr=last_at_attr,
            poll_interval_attr="auto_battery_poll_interval_seconds",
        )
        return value

    @staticmethod
    def _energy_cluster_snapshot(
        worker_snapshot: dict[str, object],
        *,
        combined_charge_power: float | None,
        combined_discharge_power: float | None,
        combined_net_power: float | None,
        combined_ac_power: float | None,
    ) -> dict[str, object]:
        raw_battery_sources = worker_snapshot.get("battery_sources")
        battery_sources = list(raw_battery_sources) if isinstance(raw_battery_sources, (list, tuple)) else []
        return {
            "battery_combined_soc": worker_snapshot.get("battery_combined_soc"),
            "battery_combined_usable_capacity_wh": worker_snapshot.get("battery_combined_usable_capacity_wh"),
            "battery_combined_charge_power_w": combined_charge_power,
            "battery_combined_discharge_power_w": combined_discharge_power,
            "battery_combined_net_power_w": combined_net_power,
            "battery_combined_ac_power_w": combined_ac_power,
            "battery_combined_pv_input_power_w": worker_snapshot.get("battery_combined_pv_input_power_w"),
            "battery_combined_grid_interaction_w": worker_snapshot.get("battery_combined_grid_interaction_w"),
            "grid_power_w": worker_snapshot.get("grid_power"),
            "grid_captured_at": worker_snapshot.get("grid_captured_at"),
            "grid_gateway_power_w": worker_snapshot.get("grid_gateway_power"),
            "grid_gateway_captured_at": worker_snapshot.get("grid_gateway_captured_at"),
            "grid_primary_power_w": worker_snapshot.get("grid_primary_power"),
            "grid_primary_captured_at": worker_snapshot.get("grid_primary_captured_at"),
            "grid_selected_source_id": worker_snapshot.get("grid_selected_source_id"),
            "grid_fusion_state": worker_snapshot.get("grid_fusion_state"),
            "grid_fusion_confidence": worker_snapshot.get("grid_fusion_confidence"),
            "grid_fusion_primary_valid": worker_snapshot.get("grid_fusion_primary_valid"),
            "grid_fusion_backup_valid": worker_snapshot.get("grid_fusion_backup_valid"),
            "grid_fusion_difference_watts": worker_snapshot.get("grid_fusion_difference_watts"),
            "grid_fusion_tolerance_watts": worker_snapshot.get("grid_fusion_tolerance_watts"),
            "battery_average_confidence": worker_snapshot.get("battery_average_confidence"),
            "battery_source_count": worker_snapshot.get("battery_source_count", 0),
            "battery_online_source_count": worker_snapshot.get("battery_online_source_count", 0),
            "battery_valid_soc_source_count": worker_snapshot.get("battery_valid_soc_source_count", 0),
            "battery_battery_source_count": worker_snapshot.get("battery_battery_source_count", 0),
            "battery_hybrid_inverter_source_count": worker_snapshot.get("battery_hybrid_inverter_source_count", 0),
            "battery_inverter_source_count": worker_snapshot.get("battery_inverter_source_count", 0),
            "battery_sources": battery_sources,
        }

    @staticmethod
    def _store_learning_profiles(cache_owner: InputCacheService, worker_snapshot: dict[str, object]) -> None:
        raw_learning_profiles = worker_snapshot.get("battery_learning_profiles", {})
        if isinstance(raw_learning_profiles, dict):
            cache_owner._last_energy_learning_profiles = dict(raw_learning_profiles)

    @staticmethod
    def _float_or_default(value: object, default: float = 0.0) -> float:
        normalized = finite_float_or_none(value)
        return float(default if normalized is None else normalized)


__all__ = ["InputCacheResolver", "InputCacheService"]

#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Collect PV, battery, and grid inputs for the Venus EV charger service in a helper process.

The helper exists so DBus discovery and polling cannot stall the main wallbox
service. It periodically writes a compact JSON snapshot that the main process
can consume safely, even if DBus becomes slow or temporarily inconsistent.
"""

import os
import time
from typing import Any



class _AutoInputHelperSnapshotMixin:
    @staticmethod
    def _default_source_poll_schedule() -> dict[str, float]:
        return {
            "pv": 0.0,
            "battery": 0.0,
            "grid": 0.0,
        }

    @staticmethod
    def _battery_snapshot_field_names() -> tuple[str, ...]:
        return (
            "battery_soc",
            "battery_combined_soc",
            "battery_combined_usable_capacity_wh",
            "battery_combined_charge_power_w",
            "battery_combined_discharge_power_w",
            "battery_combined_net_power_w",
            "battery_combined_ac_power_w",
            "battery_headroom_charge_w",
            "battery_headroom_discharge_w",
            "expected_near_term_export_w",
            "expected_near_term_import_w",
            "battery_discharge_balance_mode",
            "battery_discharge_balance_target_distribution_mode",
            "battery_discharge_balance_error_w",
            "battery_discharge_balance_max_abs_error_w",
            "battery_discharge_balance_total_discharge_w",
            "battery_discharge_balance_eligible_source_count",
            "battery_discharge_balance_active_source_count",
            "battery_discharge_balance_control_candidate_count",
            "battery_discharge_balance_control_ready_count",
            "battery_discharge_balance_supported_control_source_count",
            "battery_discharge_balance_experimental_control_source_count",
            "battery_source_count",
            "battery_online_source_count",
            "battery_valid_soc_source_count",
            "battery_sources",
            "battery_learning_profiles",
        )

    def _ensure_poll_state(self: Any) -> None:
        """Initialize runtime state for tests or partially constructed instances."""
        self._ensure_source_poll_intervals()
        self._ensure_poll_defaults()

    def _ensure_source_poll_intervals(self: Any) -> None:
        """Populate per-source poll intervals and derive the shared interval when missing."""
        base_poll_interval = max(0.2, getattr(self, "poll_interval_seconds", 1.0))
        for attr_name in self._source_poll_interval_attrs():
            if not hasattr(self, attr_name):
                setattr(self, attr_name, base_poll_interval)
        if not hasattr(self, "poll_interval_seconds"):
            self.poll_interval_seconds = min(
                self.auto_pv_poll_interval_seconds,
                self.auto_grid_poll_interval_seconds,
                self.auto_battery_poll_interval_seconds,
            )

    @staticmethod
    def _source_poll_interval_attrs() -> tuple[str, str, str]:
        """Return attribute names for per-source polling intervals."""
        return (
            "auto_pv_poll_interval_seconds",
            "auto_grid_poll_interval_seconds",
            "auto_battery_poll_interval_seconds",
        )

    def _ensure_poll_defaults(self: Any) -> None:
        """Populate remaining runtime attributes needed by the helper process."""
        default_values = {
            "_last_snapshot_state": self._empty_snapshot,
            "_next_source_poll_at": self._default_source_poll_schedule,
            "_system_bus": None,
            "_dbus_generation": 0,
            "_system_bus_generation": 0,
            "_name_owner_match": None,
            "_dbus_subscription_backoff_until": 0.0,
            "_signal_matches": dict,
            "_monitored_specs": dict,
            "_refresh_scheduled": False,
            "subscription_refresh_seconds": 60.0,
            "validation_poll_seconds": 30.0,
            "_main_loop": None,
            "_stop_requested": False,
        }
        for attr_name, default in default_values.items():
            self._ensure_default_attr(attr_name, default)

    def _ensure_default_attr(self: Any, attr_name: str, default: object) -> None:
        """Populate one runtime attribute when it has not been initialized yet."""
        if hasattr(self, attr_name):
            return
        value = default() if callable(default) else default
        setattr(self, attr_name, value)

    def _collect_snapshot(self: Any, now: float | None = None) -> dict[str, object]:
        """Collect only the due Auto inputs and keep the last snapshot state for the others."""
        self._ensure_poll_state()
        current = time.time() if now is None else float(now)
        snapshot = dict(self._last_snapshot_state)

        source_specs = (
            ("pv", self.auto_pv_poll_interval_seconds, self._get_pv_power, "pv_power", "pv_captured_at"),
            ("battery", self.auto_battery_poll_interval_seconds, self._get_battery_soc, "battery_soc", "battery_captured_at"),
            ("grid", self.auto_grid_poll_interval_seconds, self._get_grid_power, "grid_power", "grid_captured_at"),
        )

        for source_name, interval_seconds, getter, value_key, captured_key in source_specs:
            if current < float(self._next_source_poll_at.get(source_name, 0.0)):
                continue
            value = getter()
            self._apply_source_snapshot_value(snapshot, source_name, value_key, captured_key, value, current)
            self._next_source_poll_at[source_name] = current + float(interval_seconds)

        snapshot["captured_at"] = current
        snapshot["heartbeat_at"] = current
        snapshot["snapshot_version"] = self.SNAPSHOT_SCHEMA_VERSION
        self._stamp_snapshot_metadata(snapshot)
        self._last_snapshot_state = dict(snapshot)
        return snapshot

    def _set_source_value(self: Any, source_name: str, value: object, now: float | None = None) -> None:
        """Update one source in the snapshot and write it to RAM."""
        self._ensure_poll_state()
        current = time.time() if now is None else float(now)
        snapshot = dict(self._last_snapshot_state)
        snapshot_keys = self._source_snapshot_keys(source_name)
        if snapshot_keys is None:
            return
        value_key, captured_key = snapshot_keys
        self._apply_source_snapshot_value(snapshot, source_name, value_key, captured_key, value, current)
        snapshot["captured_at"] = current
        snapshot["heartbeat_at"] = current
        snapshot["snapshot_version"] = self.SNAPSHOT_SCHEMA_VERSION
        self._stamp_snapshot_metadata(snapshot)
        self._last_snapshot_state = snapshot
        self._write_snapshot(snapshot)

    def _apply_source_snapshot_value(
        self: Any,
        snapshot: dict[str, object],
        source_name: str,
        value_key: str,
        captured_key: str,
        value: object,
        current: float,
    ) -> None:
        if source_name == "battery" and isinstance(value, dict):
            self._apply_battery_snapshot_value(snapshot, value, captured_key, current)
            return
        snapshot[value_key] = value
        snapshot[captured_key] = None if value is None else current

    def _apply_battery_snapshot_value(
        self: Any,
        snapshot: dict[str, object],
        value: dict[str, object],
        captured_key: str,
        current: float,
    ) -> None:
        battery_soc = value.get("battery_soc")
        snapshot["battery_soc"] = battery_soc
        snapshot[captured_key] = None if battery_soc is None else current
        for field_name in self._battery_snapshot_field_names():
            if field_name == "battery_soc":
                continue
            snapshot[field_name] = value.get(field_name)

    @staticmethod
    def _source_snapshot_keys(source_name: str) -> tuple[str, str] | None:
        """Return snapshot value/captured-at keys for one logical source."""
        return {
            "pv": ("pv_power", "pv_captured_at"),
            "battery": ("battery_soc", "battery_captured_at"),
            "grid": ("grid_power", "grid_captured_at"),
        }.get(source_name)

    def _heartbeat_snapshot(self: Any) -> bool:
        """Keep the RAM snapshot fresh without re-reading DBus values."""
        self._ensure_poll_state()
        current = time.time()
        snapshot = dict(self._last_snapshot_state)
        snapshot["heartbeat_at"] = current
        snapshot["snapshot_version"] = self.SNAPSHOT_SCHEMA_VERSION
        self._stamp_snapshot_metadata(snapshot)
        self._last_snapshot_state = snapshot
        self._write_snapshot(snapshot)
        return not self._stop_requested

    def _stamp_snapshot_metadata(self: Any, snapshot: dict[str, object]) -> None:
        """Attach helper identity metadata used for supervisor liveness checks."""
        snapshot["writer_pid"] = os.getpid()
        snapshot["helper_generation"] = int(getattr(self, "helper_generation", 0) or 0)

    def _refresh_source(self: Any, source_name: str, now: float | None = None) -> None:
        """Refresh exactly one source on startup or when its DBus signal fires."""
        current = time.time() if now is None else float(now)
        if source_name == "pv":
            value = self._get_pv_power()
        elif source_name == "battery":
            value = self._get_battery_soc()
        elif source_name == "grid":
            value = self._get_grid_power()
        else:
            return
        self._set_source_value(source_name, value, current)

    def _refresh_all_sources(self: Any, now: float | None = None) -> None:
        """Refresh all Auto inputs once, for startup or service topology changes."""
        current = time.time() if now is None else float(now)
        for source_name in ("pv", "battery", "grid"):
            self._refresh_source(source_name, current)

    def _validation_poll(self: Any) -> bool:
        """Fallback poll to recover from silent subscription failures."""
        self._refresh_all_sources()
        return not self._stop_requested

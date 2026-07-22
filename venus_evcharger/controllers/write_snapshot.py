# SPDX-License-Identifier: GPL-3.0-or-later
"""Helpers for capturing and restoring reversible control state."""

from __future__ import annotations

import copy
from collections import deque
from typing import Any


SNAPSHOT_ATTRS = (
    "virtual_mode",
    "virtual_autostart",
    "virtual_startstop",
    "virtual_enable",
    "virtual_set_current",
    "requested_phase_selection",
    "active_phase_selection",
    "supported_phase_selections",
    "_phase_switch_pending_selection",
    "_phase_switch_state",
    "_phase_switch_requested_at",
    "_phase_switch_stable_until",
    "_phase_switch_resume_relay",
    "_phase_switch_mismatch_active",
    "_phase_switch_last_mismatch_selection",
    "_phase_switch_last_mismatch_at",
    "_phase_switch_lockout_selection",
    "_phase_switch_lockout_reason",
    "_phase_switch_lockout_at",
    "_phase_switch_lockout_until",
    "_contactor_fault_active_reason",
    "_contactor_fault_active_since",
    "_contactor_lockout_reason",
    "_contactor_lockout_source",
    "_contactor_lockout_at",
    "_contactor_suspected_open_since",
    "_contactor_suspected_welded_since",
    "min_current",
    "max_current",
    "auto_policy",
    "auto_start_delay_seconds",
    "auto_stop_delay_seconds",
    "auto_scheduled_enabled_days",
    "auto_scheduled_night_start_delay_seconds",
    "auto_scheduled_latest_end_time",
    "auto_scheduled_night_current_amps",
    "auto_dbus_backoff_base_seconds",
    "auto_dbus_backoff_max_seconds",
    "manual_override_until",
    "auto_start_condition_since",
    "auto_stop_condition_since",
    "auto_stop_condition_reason",
    "_auto_mode_cutover_pending",
    "_ignore_min_offtime_once",
    "_pending_relay_state",
    "_pending_relay_requested_at",
    "_relay_sync_expected_state",
    "_relay_sync_requested_at",
    "_relay_sync_deadline_at",
    "_relay_sync_failure_reported",
    "_last_pm_status_at",
    "_last_pm_status_confirmed",
    "_last_confirmed_pm_status_at",
)
SNAPSHOT_DEQUE_ATTRS = ("auto_samples",)
SNAPSHOT_VALUE_ATTRS = (
    "_stop_smoothed_surplus_power",
    "_stop_smoothed_grid_power",
)
SNAPSHOT_MAPPING_ATTRS = (
    "_worker_snapshot",
    "_last_pm_status",
    "_last_confirmed_pm_status",
    "_phase_switch_mismatch_counts",
    "_contactor_fault_counts",
)


def _snapshot_attrs(svc: Any, attr_names: tuple[str, ...]) -> dict[str, Any]:
    """Capture one set of scalar-like attributes."""
    return {
        attr_name: copy.deepcopy(getattr(svc, attr_name))
        for attr_name in attr_names
        if hasattr(svc, attr_name)
    }


def _snapshot_deques(svc: Any, attr_names: tuple[str, ...]) -> dict[str, deque[Any]]:
    """Capture one set of deque attributes."""
    return {
        attr_name: deque(getattr(svc, attr_name))
        for attr_name in attr_names
        if hasattr(svc, attr_name)
    }


def _snapshot_mappings(svc: Any, attr_names: tuple[str, ...]) -> dict[str, dict[str, Any]]:
    """Capture deep-copied dict attributes used by the write path."""
    captured: dict[str, dict[str, Any]] = {}
    for attr_name in attr_names:
        if not hasattr(svc, attr_name):
            continue
        current = getattr(svc, attr_name)
        if isinstance(current, dict):
            captured[attr_name] = copy.deepcopy(current)
    return captured


def capture_write_state(
    svc: Any,
    *,
    attrs: tuple[str, ...] = SNAPSHOT_ATTRS,
    deque_attrs: tuple[str, ...] = SNAPSHOT_DEQUE_ATTRS,
    value_attrs: tuple[str, ...] = SNAPSHOT_VALUE_ATTRS,
    mapping_attrs: tuple[str, ...] = SNAPSHOT_MAPPING_ATTRS,
) -> dict[str, Any]:
    """Capture mutable write-path state so failed writes can be rolled back."""
    return {
        "attrs": _snapshot_attrs(svc, attrs),
        "deques": _snapshot_deques(svc, deque_attrs),
        "values": _snapshot_attrs(svc, value_attrs),
        "mappings": _snapshot_mappings(svc, mapping_attrs),
    }


def _restore_deques(svc: Any, saved_deques: dict[str, deque[Any]]) -> None:
    """Restore previously captured deque attributes."""
    for attr_name, saved in saved_deques.items():
        current = getattr(svc, attr_name, None)
        if isinstance(current, deque):
            current.clear()
            current.extend(saved)
            continue
        setattr(svc, attr_name, deque(saved))


def _restore_mappings(svc: Any, saved_mappings: dict[str, dict[str, Any]]) -> None:
    """Restore previously captured dict-like attributes."""
    for attr_name, saved in saved_mappings.items():
        current = getattr(svc, attr_name, None)
        if isinstance(current, dict):
            current.clear()
            current.update(saved)
            continue
        setattr(svc, attr_name, copy.deepcopy(saved))


def restore_write_state(svc: Any, snapshot: dict[str, Any]) -> None:
    """Restore one previously captured write-path snapshot."""
    for attr_name, value in snapshot["attrs"].items():
        setattr(svc, attr_name, value)
    for attr_name, value in snapshot["values"].items():
        setattr(svc, attr_name, value)
    _restore_deques(svc, snapshot["deques"])
    _restore_mappings(svc, snapshot["mappings"])

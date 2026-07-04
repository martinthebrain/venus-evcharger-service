# SPDX-License-Identifier: GPL-3.0-or-later
"""Operational state payload helpers for the Control API role."""

from __future__ import annotations

from typing import Any

from venus_evcharger.backend.config import backend_mode_for_service, backend_type_for_service
from venus_evcharger.core.common import evse_fault_reason
from venus_evcharger.core.contracts import (
    normalized_fault_state,
    normalized_software_update_state_fields,
)
from venus_evcharger.service.control_state_operational_support import (
    _state_api_operational_auto_decision_state,
    _state_api_operational_balance_state,
    _state_api_operational_energy_state,
    _state_api_operational_victron_bias_state,
    _worker_learning_summary,
    _worker_snapshot,
)
from venus_evcharger.service.control_state_core import _ControlApiStateCore


class _ControlApiStateOperational(_ControlApiStateCore):
    def _state_api_operational_payload(self) -> dict[str, Any]:
        return _state_api_operational_payload(self)


def _state_api_operational_payload(owner: Any) -> dict[str, Any]:
    auto_state = _owner_value_or_default(owner, "_last_auto_state", "idle")
    auto_state_code = _owner_value_or_default(owner, "_last_auto_state_code", 0)
    worker_snapshot = _worker_snapshot(owner)
    learning_summary = _worker_learning_summary(worker_snapshot)
    fault_reason, fault_active = normalized_fault_state(evse_fault_reason(_last_health_reason(owner)))
    software_update_fields = normalized_software_update_state_fields(
        _software_update_state(owner),
        _owner_bool_or_false(owner, "_software_update_available"),
        _owner_bool_or_false(owner, "_software_update_no_update_active"),
    )
    return {
        "ok": True,
        "api_version": "v1",
        "kind": "operational",
        "state": _state_api_operational_state(
            owner,
            worker_snapshot,
            _last_auto_metrics(owner),
            auto_state,
            auto_state_code,
            fault_reason,
            fault_active,
            software_update_fields,
            learning_summary,
        ),
    }


def _owner_value_or_default(owner: Any, name: str, default: object) -> object:
    if not hasattr(owner, name):
        return default
    return getattr(owner, name)


def _last_health_reason(owner: Any) -> object:
    if not hasattr(owner, "_last_health_reason"):
        return ""
    value = getattr(owner, "_last_health_reason")
    return "" if value is None else value


def _software_update_state(owner: Any) -> object:
    if not hasattr(owner, "_software_update_state"):
        return "idle"
    return getattr(owner, "_software_update_state")


def _owner_bool_or_false(owner: Any, name: str) -> bool:
    if not hasattr(owner, name):
        return False
    return bool(getattr(owner, name))


def _last_auto_metrics(owner: Any) -> dict[str, Any]:
    if not hasattr(owner, "_last_auto_metrics"):
        return {}
    raw_metrics = getattr(owner, "_last_auto_metrics")
    return raw_metrics if isinstance(raw_metrics, dict) else {}


def _state_api_operational_state(
    owner: Any,
    worker_snapshot: dict[str, Any],
    last_auto_metrics: dict[str, Any],
    auto_state: object,
    auto_state_code: object,
    fault_reason: str,
    fault_active: int,
    software_update_fields: tuple[str, int, int, int],
    learning_summary: dict[str, Any],
) -> dict[str, Any]:
    (
        software_update_state,
        software_update_state_code,
        software_update_available,
        software_update_no_update_active,
    ) = software_update_fields
    state = _state_api_operational_core_state(
        owner,
        auto_state,
        auto_state_code,
        fault_reason,
        fault_active,
        software_update_state,
        software_update_state_code,
        software_update_available,
        software_update_no_update_active,
    )
    state.update(_state_api_operational_auto_decision_state(owner, last_auto_metrics, auto_state, auto_state_code))
    state.update(_state_api_operational_energy_state(worker_snapshot, learning_summary))
    state.update(_state_api_operational_balance_state(owner, worker_snapshot, last_auto_metrics))
    state.update(_state_api_operational_victron_bias_state(last_auto_metrics))
    return state


def _state_api_operational_core_state(
    owner: Any,
    auto_state: object,
    auto_state_code: object,
    fault_reason: str,
    fault_active: int,
    software_update_state: str,
    software_update_state_code: int,
    software_update_available: int,
    software_update_no_update_active: int,
) -> dict[str, Any]:
    return {
        "mode": _owner_value_or_default(owner, "virtual_mode", 0),
        "enable": _owner_value_or_default(owner, "virtual_enable", 0),
        "startstop": _owner_value_or_default(owner, "virtual_startstop", 0),
        "autostart": _owner_value_or_default(owner, "virtual_autostart", 0),
        "active_phase_selection": _owner_value_or_default(owner, "active_phase_selection", "P1"),
        "requested_phase_selection": _owner_value_or_default(owner, "requested_phase_selection", "P1"),
        "backend_mode": backend_mode_for_service(owner, "combined"),
        "meter_backend": backend_type_for_service(owner, "meter", "na"),
        "switch_backend": backend_type_for_service(owner, "switch", "na"),
        "charger_backend": backend_type_for_service(owner, "charger", "na"),
        "auto_state": auto_state,
        "auto_state_code": auto_state_code,
        "fault_active": fault_active,
        "fault_reason": fault_reason,
        "software_update_state": software_update_state,
        "software_update_state_code": software_update_state_code,
        "software_update_available": software_update_available,
        "software_update_no_update_active": software_update_no_update_active,
        "runtime_overrides_active": _owner_value_or_default(owner, "_runtime_overrides_active", False),
        "runtime_overrides_path": _owner_value_or_default(owner, "runtime_overrides_path", ""),
    }

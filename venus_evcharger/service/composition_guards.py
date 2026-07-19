# SPDX-License-Identifier: GPL-3.0-or-later
"""Runtime guards for dynamic values entering service composition."""

from __future__ import annotations

from typing import Protocol, TypeGuard

from venus_evcharger.backend.base import ChargerBackend, MeterBackend, SwitchBackend
from venus_evcharger.backend.factory import ResolvedBackends
from venus_evcharger.inputs.supervisor_contracts import AutoInputSupervisorService
from venus_evcharger.ports.dbus import DbusInputService
from venus_evcharger.publish.dbus_shared import PublishServicePort
from venus_evcharger.update.runtime_cycle_contracts import UpdateCycleServicePort


class BackendTargetPort(Protocol):
    _backend_bundle: ResolvedBackends
    _meter_backend: MeterBackend | None
    _switch_backend: SwitchBackend | None
    _charger_backend: ChargerBackend | None
    topology_configured: bool
    primary_rpc_configured: bool


def _has_attributes(value: object, names: tuple[str, ...]) -> bool:
    return all(hasattr(value, name) for name in names)


def _has_callable_attributes(value: object, names: tuple[str, ...]) -> bool:
    return all(callable(getattr(value, name, None)) for name in names)


_DBUS_INPUT_SERVICE_STATE_FIELDS = (
    "dbus_gateway_cache_path",
    "dbus_gateway_run_dir",
    "dbus_gateway_max_age_seconds",
    "auto_dbus_backoff_base_seconds",
    "auto_dbus_backoff_max_seconds",
    "auto_pv_scan_interval_seconds",
    "auto_pv_service",
    "auto_pv_service_prefix",
    "auto_pv_max_services",
    "auto_battery_scan_interval_seconds",
    "auto_battery_service",
    "auto_battery_service_prefix",
    "auto_battery_soc_path",
    "auto_battery_capacity_wh",
    "auto_battery_power_path",
    "auto_battery_ac_power_path",
    "auto_battery_pv_power_path",
    "auto_battery_grid_interaction_path",
    "auto_battery_operating_mode_path",
    "auto_energy_sources",
    "auto_use_combined_battery_soc",
    "auto_grid_service",
    "_last_dbus_ok_at",
    "_last_pv_missing_warning",
    "_dbus_list_backoff_until",
    "_dbus_list_failures",
    "_resolved_auto_pv_services",
    "_auto_pv_last_scan",
    "_resolved_auto_battery_service",
    "_auto_battery_last_scan",
    "_resolved_auto_energy_services",
    "_auto_energy_last_scan",
    "_last_energy_learning_profiles",
    "_last_energy_cluster",
)

_DBUS_INPUT_RUNTIME_METHODS = (
    "source_retry_ready",
    "mark_recovery",
    "mark_failure",
    "delay_source_retry",
    "warning_throttled",
)


def is_publish_service(value: object) -> TypeGuard[PublishServicePort]:
    runtime = getattr(value, "runtime", None)
    return _has_attributes(
        value,
        (
            "_dbusservice",
            "_dbus_publish_state",
            "_dbus_live_publish_interval_seconds",
            "_dbus_slow_publish_interval_seconds",
            "_last_health_code",
            "_last_health_reason",
            "last_status",
            "started_at",
            "virtual_set_current",
        ),
    ) and callable(getattr(runtime, "enqueue_dbus_publish_fields", None))


def require_publish_service(value: object) -> PublishServicePort:
    if not is_publish_service(value):
        raise TypeError("wallbox service does not implement PublishServicePort")
    return value


def is_auto_input_service(value: object) -> TypeGuard[AutoInputSupervisorService]:
    runtime = getattr(value, "runtime", None)
    auto = getattr(value, "auto", None)
    return _has_attributes(
        value,
        (
            "auto_input_helper_restart_seconds",
            "auto_input_helper_stale_seconds",
            "auto_input_snapshot_path",
            "virtual_mode",
            "_auto_input_helper_generation",
            "_auto_input_runtime_instance_id",
        ),
    ) and callable(getattr(runtime, "update_worker_snapshot", None)) and callable(
        getattr(auto, "mode_uses_auto_logic", None)
    )


def require_auto_input_service(value: object) -> AutoInputSupervisorService:
    if not is_auto_input_service(value):
        raise TypeError("wallbox service does not implement AutoInputSupervisorService")
    return value


def is_dbus_input_service(value: object) -> TypeGuard[DbusInputService]:
    runtime = getattr(value, "runtime", None)
    return _has_attributes(value, _DBUS_INPUT_SERVICE_STATE_FIELDS) and _has_callable_attributes(
        runtime,
        _DBUS_INPUT_RUNTIME_METHODS,
    )


def require_dbus_input_service(value: object) -> DbusInputService:
    if not is_dbus_input_service(value):
        raise TypeError("wallbox service does not implement DbusInputService")
    return value


def is_update_cycle_service(value: object) -> TypeGuard[UpdateCycleServicePort]:
    runtime = getattr(value, "runtime", None)
    state = getattr(value, "state", None)
    auto = getattr(value, "auto", None)
    return _has_attributes(
        value,
        ("service_name", "_dbusservice", "_readback_store", "time_now"),
    ) and callable(getattr(runtime, "worker_snapshot", None)) and callable(
        getattr(state, "flush_runtime_overrides", None)
    ) and callable(getattr(auto, "decide_relay", None))


def require_update_cycle_service(value: object) -> UpdateCycleServicePort:
    if not is_update_cycle_service(value):
        raise TypeError("wallbox service does not implement UpdateCycleServicePort")
    return value


def is_backend_target(value: object) -> TypeGuard[BackendTargetPort]:
    return hasattr(value, "__dict__")


def require_backend_target(value: object) -> BackendTargetPort:
    if not is_backend_target(value):
        raise TypeError("wallbox service does not expose mutable backend state")
    return value

# SPDX-License-Identifier: GPL-3.0-or-later
"""Runtime guards for dynamic values entering service composition."""

from __future__ import annotations

from typing import Protocol, TypeGuard

from venus_evcharger.auto.policy import AutoPolicy
from venus_evcharger.backend.base import ChargerBackend, MeterBackend, SwitchBackend
from venus_evcharger.backend.factory import ResolvedBackends
from venus_evcharger.inputs.supervisor_contracts import AutoInputSupervisorService
from venus_evcharger.ports.auto import AutoDecisionServicePort
from venus_evcharger.ports.write_runtime import WriteRuntimeServicePort
from venus_evcharger.publish.dbus_shared import PublishServicePort
from venus_evcharger.update.runtime_cycle_contracts import UpdateCycleServicePort


class BackendTargetPort(Protocol):
    _backend_bundle: ResolvedBackends
    _meter_backend: MeterBackend | None
    _switch_backend: SwitchBackend | None
    _charger_backend: ChargerBackend | None
    topology_configured: bool
    primary_rpc_configured: bool


_WRITE_SERVICE_ATTRIBUTES = (
    "auto_policy",
    "virtual_mode",
    "virtual_autostart",
    "virtual_startstop",
    "virtual_enable",
    "auto_manual_override_seconds",
    "auto_start_condition_since",
    "auto_stop_condition_since",
    "virtual_set_current",
    "min_current",
    "max_current",
    "auto_start_delay_seconds",
    "auto_stop_delay_seconds",
    "auto_scheduled_enabled_days",
    "auto_scheduled_night_start_delay_seconds",
    "auto_scheduled_latest_end_time",
    "auto_scheduled_night_current_amps",
    "_software_update_run_requested_at",
    "auto_dbus_backoff_base_seconds",
    "auto_dbus_backoff_max_seconds",
    "supported_phase_selections",
    "requested_phase_selection",
    "active_phase_selection",
    "_phase_switch_lockout_selection",
    "_phase_switch_lockout_until",
    "_auto_mode_cutover_pending",
    "_ignore_min_offtime_once",
    "manual_override_until",
    "time_now",
)
_WRITE_AUTO_METHODS = ("clear_samples", "normalize_mode", "mode_uses_auto_logic")
_WRITE_RUNTIME_METHODS = (
    "queue_relay_command",
    "publish_local_pm_status",
    "worker_snapshot",
    "pending_relay_command",
    "update_worker_snapshot",
    "phase_selection_requires_pause",
    "apply_phase_selection",
)
_WRITE_STATE_METHODS = (
    "publish_field",
    "save_runtime_state",
    "summary",
    "save_runtime_overrides",
    "validate_runtime_config",
)


def _has_attributes(value: object, names: tuple[str, ...]) -> bool:
    return all(hasattr(value, name) for name in names)


def _has_callables(value: object, names: tuple[str, ...]) -> bool:
    return all(callable(getattr(value, name, None)) for name in names)


def _has_write_service_attributes(value: object) -> bool:
    return _has_attributes(value, _WRITE_SERVICE_ATTRIBUTES) and isinstance(
        getattr(value, "auto_policy", None),
        AutoPolicy,
    )


def _has_write_service_collaborators(value: object) -> bool:
    return all(
        (
            _has_callables(value, ("time_now",)),
            _has_callables(getattr(value, "auto", None), _WRITE_AUTO_METHODS),
            _has_callables(getattr(value, "runtime", None), _WRITE_RUNTIME_METHODS),
            _has_callables(getattr(value, "state", None), _WRITE_STATE_METHODS),
        )
    )


def is_publish_service(value: object) -> TypeGuard[PublishServicePort]:
    return _has_attributes(
        value,
        (
            "_dbus_live_publish_interval_seconds",
            "_dbus_slow_publish_interval_seconds",
            "_last_health_code",
            "_last_health_reason",
            "last_status",
            "started_at",
            "virtual_set_current",
            "gateway_publication",
            "runtime",
        ),
    )


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


def is_auto_decision_service(value: object) -> TypeGuard[AutoDecisionServicePort]:
    runtime = getattr(value, "runtime", None)
    state = getattr(value, "state", None)
    return _has_attributes(
        value,
        (
            "virtual_mode",
            "virtual_enable",
            "virtual_autostart",
            "auto_policy",
            "_auto_mode_cutover_pending",
            "_ignore_min_offtime_once",
        ),
    ) and isinstance(
        getattr(value, "auto_policy", None), AutoPolicy
    ) and _has_callables(
        runtime,
        ("write_auto_audit_event", "pending_relay_command"),
    ) and _has_callables(state, ("save_runtime_state", "last_accepted_field"))


def require_auto_decision_service(value: object) -> AutoDecisionServicePort:
    if not is_auto_decision_service(value):
        raise TypeError("wallbox service does not implement AutoDecisionServicePort")
    return value


def is_write_runtime_service(value: object) -> TypeGuard[WriteRuntimeServicePort]:
    return _has_write_service_attributes(
        value,
    ) and _has_write_service_collaborators(
        value,
    )


def require_write_runtime_service(value: object) -> WriteRuntimeServicePort:
    if not is_write_runtime_service(value):
        raise TypeError("wallbox service does not implement WriteRuntimeServicePort")
    return value


def is_update_cycle_service(value: object) -> TypeGuard[UpdateCycleServicePort]:
    runtime = getattr(value, "runtime", None)
    state = getattr(value, "state", None)
    auto = getattr(value, "auto", None)
    return _has_attributes(
        value,
        ("_readback_store", "time_now"),
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

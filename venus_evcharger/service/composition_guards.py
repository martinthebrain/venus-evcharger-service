# SPDX-License-Identifier: GPL-3.0-or-later
"""Runtime guards for dynamic values entering service composition."""

from __future__ import annotations

from typing import Protocol, TypeGuard

from venus_evcharger.backend.base import ChargerBackend, MeterBackend, SwitchBackend
from venus_evcharger.backend.factory import ResolvedBackends
from venus_evcharger.inputs.supervisor_contracts import AutoInputSupervisorService
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

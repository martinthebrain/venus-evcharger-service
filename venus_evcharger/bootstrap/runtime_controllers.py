# SPDX-License-Identifier: GPL-3.0-or-later
"""Controller wiring for service bootstrap runtime initialization."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from venus_evcharger.backend.factory import build_service_backends
from venus_evcharger.backend.shelly_io import ShellyIoController
from venus_evcharger.controllers.auto import AutoDecisionController
from venus_evcharger.controllers.state import ServiceStateController
from venus_evcharger.controllers.write import DbusWriteController
from venus_evcharger.inputs.supervisor import AutoInputSupervisor
from venus_evcharger.ports import AutoDecisionPort, UpdateCyclePort, WriteControllerPort
from venus_evcharger.publish.dbus import DbusPublishController
from venus_evcharger.runtime import RuntimeSupportController
from venus_evcharger.update.controller import UpdateCycleController


def initialize_runtime_controllers(
    svc: Any,
    *,
    age_seconds: Callable[[float | int | None, float | int | None], int],
    health_code: Callable[[str], int],
    mode_uses_auto_logic: Callable[[Any], bool],
    normalize_mode: Callable[[Any], int],
    phase_values: Callable[..., Any],
) -> None:
    """Create the controller objects used by the service runtime."""
    svc._runtime_support_controller = RuntimeSupportController(svc, age_seconds, health_code)
    svc._runtime_support_controller.initialize_runtime_support()
    svc._auto_controller = AutoDecisionController(
        AutoDecisionPort(svc),
        health_code,
        mode_uses_auto_logic,
    )
    svc._dbus_publisher = DbusPublishController(svc, age_seconds)
    svc._shelly_io_controller = ShellyIoController(svc)
    _apply_resolved_backends(svc)
    if not hasattr(svc, "_state_controller") or svc._state_controller is None:
        svc._state_controller = ServiceStateController(svc, normalize_mode)
    svc._write_controller = DbusWriteController(WriteControllerPort(svc))
    svc._auto_input_supervisor = AutoInputSupervisor(svc)
    svc._update_controller = UpdateCycleController(
        UpdateCyclePort(svc),
        phase_values,
        health_code,
    )


def _apply_resolved_backends(svc: Any) -> None:
    """Build and expose resolved backend objects and runtime summary flags on the service."""
    resolved_backends = build_service_backends(svc)
    svc._backend_bundle = resolved_backends
    svc._meter_backend = resolved_backends.meter
    svc._switch_backend = resolved_backends.switch
    svc._charger_backend = resolved_backends.charger
    runtime_backends = resolved_backends.runtime
    svc.topology_configured = runtime_backends.topology_configured
    svc.primary_rpc_configured = runtime_backends.primary_rpc_configured

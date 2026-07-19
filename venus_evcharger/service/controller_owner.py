# SPDX-License-Identifier: GPL-3.0-or-later
"""Explicit controller ownership for the wallbox service composition root."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass

from venus_evcharger.backend.factory import build_service_backends
from venus_evcharger.backend.shelly_io import ShellyIoController
from venus_evcharger.bootstrap.contracts import Formatter, MonthWindow
from venus_evcharger.bootstrap.controller import ServiceBootstrapController
from venus_evcharger.companion import EnergyCompanionDbusBridge
from venus_evcharger.controllers.auto import AutoDecisionController
from venus_evcharger.controllers.state import ServiceStateController
from venus_evcharger.controllers.write import DbusWriteController
from venus_evcharger.inputs.dbus import DbusInputController
from venus_evcharger.inputs.supervisor import AutoInputSupervisor
from venus_evcharger.ports import AutoDecisionPort, DbusInputPort, WriteControllerPort
from venus_evcharger.publish.dbus import DbusPublishController
from venus_evcharger.runtime import RuntimeSupportController
from venus_evcharger.update.controller import UpdateCycleController

from .composition_guards import (
    require_auto_input_service,
    require_backend_target,
    require_dbus_input_service,
    require_publish_service,
    require_update_cycle_service,
)
from .composition_ports import (
    AutoControllerPort,
    AutoInputControllerPort,
    BootstrapControllerPort,
    CompanionControllerPort,
    DbusInputControllerPort,
    PhaseValues,
    PublishControllerPort,
    RuntimeControllerPort,
    ShellyControllerPort,
    StateControllerPort,
    UpdateControllerPort,
)
from .composition_ports import (
    WriteControllerPort as WriteControllerComponentPort,
)


@dataclass(frozen=True)
class ServiceFunctionBundle:
    """Pure functions and platform objects required while assembling controllers."""

    normalize_phase: Callable[[object], str]
    normalize_mode: Callable[[object], int]
    mode_uses_auto_logic: Callable[[object], bool]
    month_window: MonthWindow
    age_seconds: Callable[[float | int | None, float | int | None], int]
    health_code: Callable[[str], int]
    phase_values: PhaseValues
    read_version: Callable[[str], str]
    gobject: object
    script_path: str
    config_path: str
    auto_input_helper_path: str
    formatters: Mapping[str, Formatter]


@dataclass(frozen=True)
class RuntimeControllers:
    """Controllers that exist after configuration has been loaded."""

    runtime: RuntimeControllerPort
    auto: AutoControllerPort
    publisher: PublishControllerPort
    shelly: ShellyControllerPort
    write: WriteControllerComponentPort
    auto_input: AutoInputControllerPort
    dbus_input: DbusInputControllerPort
    update: UpdateControllerPort
    companion: CompanionControllerPort


class ServiceControllerOwner:
    """Own every service controller exactly once and enforce its lifecycle."""

    functions: ServiceFunctionBundle
    state: StateControllerPort
    bootstrap: BootstrapControllerPort

    def __init__(self, service: object, functions: ServiceFunctionBundle) -> None:
        self._service = service
        self.functions = functions
        self.state = ServiceStateController(service, functions.normalize_mode)
        self.bootstrap = ServiceBootstrapController(
            service,
            normalize_phase_func=functions.normalize_phase,
            normalize_mode_func=functions.normalize_mode,
            mode_uses_auto_logic_func=functions.mode_uses_auto_logic,
            month_window_func=functions.month_window,
            read_version_func=functions.read_version,
            gobject_module=functions.gobject,
            script_path=functions.script_path,
            formatters=dict(functions.formatters),
        )
        self._runtime: RuntimeControllers | None = None

    @property
    def runtime(self) -> RuntimeControllers:
        """Return initialized runtime controllers or fail on lifecycle misuse."""
        if self._runtime is None:
            raise RuntimeError("wallbox runtime controllers are not initialized")
        return self._runtime

    def initialize_runtime(self) -> RuntimeControllers:
        """Build the configured runtime object graph exactly once."""
        if self._runtime is not None:
            raise RuntimeError("wallbox runtime controllers are already initialized")

        service = self._service
        runtime = RuntimeSupportController(service, self.functions.age_seconds, self.functions.health_code)
        runtime.initialize_runtime_support()
        auto = AutoDecisionController(
            AutoDecisionPort(service),
            self.functions.health_code,
            self.functions.mode_uses_auto_logic,
        )
        publisher = DbusPublishController(require_publish_service(service), self.functions.age_seconds)
        self._apply_resolved_backends()
        shelly = ShellyIoController(service)
        write = DbusWriteController(WriteControllerPort(service))
        auto_input = AutoInputSupervisor(
            require_auto_input_service(service),
            config_path=self.functions.config_path,
            helper_path=self.functions.auto_input_helper_path,
        )
        dbus_input = DbusInputController(DbusInputPort(require_dbus_input_service(service)))
        update = UpdateCycleController(
            require_update_cycle_service(service),
            self._phase_values,
            self.functions.health_code,
        )
        companion = EnergyCompanionDbusBridge(service, self.functions.script_path)
        self._runtime = RuntimeControllers(
            runtime=runtime,
            auto=auto,
            publisher=publisher,
            shelly=shelly,
            write=write,
            auto_input=auto_input,
            dbus_input=dbus_input,
            update=update,
            companion=companion,
        )
        return self._runtime

    def _phase_values(self, power: float, voltage: float, phase: object, voltage_mode: object) -> object:
        """Adapt validated phase names to the update controller's raw boundary."""
        return self.functions.phase_values(power, voltage, str(phase), str(voltage_mode))

    def _apply_resolved_backends(self) -> None:
        """Expose the resolved hardware topology on the state-owning service."""
        resolved = build_service_backends(self._service)
        target = require_backend_target(self._service)
        target._backend_bundle = resolved
        target._meter_backend = resolved.meter
        target._switch_backend = resolved.switch
        target._charger_backend = resolved.charger
        target.topology_configured = resolved.runtime.topology_configured
        target.primary_rpc_configured = resolved.runtime.primary_rpc_configured

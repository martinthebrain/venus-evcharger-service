# SPDX-License-Identifier: GPL-3.0-or-later
"""Explicit controller ownership for the wallbox service composition root."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from venus_evcharger.backend.factory import build_service_backends
from venus_evcharger.backend.shelly_io import ShellyIoController
from venus_evcharger.bootstrap.contracts import MonthWindow
from venus_evcharger.bootstrap.controller import ServiceBootstrapController
from venus_evcharger.companion import EnergyCompanionPublisher
from venus_evcharger.controllers.auto import AutoDecisionController
from venus_evcharger.controllers.state import ServiceStateController
from venus_evcharger.controllers.write import ControlWriteController
from venus_evcharger.dbus_gateway import GatewayClient, gateway_paths
from venus_evcharger.dbus_gateway_client import GatewayOperationsClient, GatewayPublicationClient
from venus_evcharger.inputs.supervisor import AutoInputSupervisor
from venus_evcharger.ipc.gateway_diagnostics import (
    GatewayDiagnosticsFileReader,
    gateway_diagnostics_path,
)
from venus_evcharger.ports import AutoDecisionPort, WriteControllerPort
from venus_evcharger.ports.gateway_operations import GatewayOperationsPort
from venus_evcharger.publish.dbus import DbusPublishController
from venus_evcharger.runtime import RuntimeSupportController
from venus_evcharger.update.controller import UpdateCycleController

from .composition_guards import (
    require_auto_decision_service,
    require_auto_input_service,
    require_backend_target,
    require_publish_service,
    require_update_cycle_service,
    require_write_runtime_service,
)
from .composition_ports import (
    AutoControllerPort,
    AutoInputControllerPort,
    BootstrapControllerPort,
    CompanionControllerPort,
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


@dataclass(frozen=True)
class RuntimeControllers:
    """Controllers that exist after configuration has been loaded."""

    runtime: RuntimeControllerPort
    auto: AutoControllerPort
    publisher: PublishControllerPort
    shelly: ShellyControllerPort
    write: WriteControllerComponentPort
    auto_input: AutoInputControllerPort
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
        )
        self._runtime: RuntimeControllers | None = None
        self._prepared_runtime: RuntimeSupportController | None = None
        self._gateway_operations: GatewayOperationsPort | None = None

    @property
    def runtime(self) -> RuntimeControllers:
        """Return initialized runtime controllers or fail on lifecycle misuse."""
        if self._runtime is None:
            raise RuntimeError("wallbox runtime controllers are not initialized")
        return self._runtime

    def prepare_runtime_state(self) -> RuntimeSupportController:
        """Initialize shared runtime state and resolve backends before composing controllers."""
        if self._runtime is not None or self._prepared_runtime is not None:
            raise RuntimeError("wallbox runtime state is already prepared")
        runtime = RuntimeSupportController(
            service=self._service,
            age_seconds_func=self.functions.age_seconds,
            health_code_func=self.functions.health_code,
        )
        runtime.initialize_runtime_support()
        runtime.init_worker_state()
        runtime.ensure_observability_state()
        self._gateway_operations = self._build_gateway_operations()
        setattr(self._service, "gateway_operations", self._gateway_operations)
        setattr(self._service, "gateway_publication", self._build_gateway_publication())
        self._apply_resolved_backends()
        self._prepared_runtime = runtime
        return runtime

    def initialize_runtime(self) -> RuntimeControllers:
        """Build the configured runtime object graph exactly once."""
        if self._runtime is not None:
            raise RuntimeError("wallbox runtime controllers are already initialized")

        service = self._service
        runtime = self._prepared_runtime
        if runtime is None:
            runtime = self.prepare_runtime_state()
        auto = AutoDecisionController(
            AutoDecisionPort(require_auto_decision_service(service)),
            self.functions.health_code,
            self.functions.mode_uses_auto_logic,
        )
        publisher = DbusPublishController(
            require_publish_service(service),
            self.functions.age_seconds,
            GatewayDiagnosticsFileReader(gateway_diagnostics_path(self._gateway_run_dir())),
        )
        shelly = ShellyIoController(service)
        write = ControlWriteController(
            WriteControllerPort(require_write_runtime_service(service))
        )
        auto_input = AutoInputSupervisor(
            require_auto_input_service(service),
            config_path=self.functions.config_path,
            helper_path=self.functions.auto_input_helper_path,
        )
        update = UpdateCycleController(
            require_update_cycle_service(service),
            self._phase_values,
            self.functions.health_code,
            gateway_operations=self._required_gateway_operations(),
        )
        companion = EnergyCompanionPublisher(service, self.functions.script_path)
        self._runtime = RuntimeControllers(
            runtime=runtime,
            auto=auto,
            publisher=publisher,
            shelly=shelly,
            write=write,
            auto_input=auto_input,
            update=update,
            companion=companion,
        )
        return self._runtime

    def _phase_values(self, power: float, voltage: float, phase: object, voltage_mode: object) -> object:
        """Adapt validated phase names to the update controller's raw boundary."""
        return self.functions.phase_values(power, voltage, str(phase), str(voltage_mode))

    def _build_gateway_operations(self) -> GatewayOperationsPort:
        return GatewayOperationsClient(GatewayClient(gateway_paths(self._gateway_run_dir())))

    def _build_gateway_publication(self) -> GatewayPublicationClient:
        return GatewayPublicationClient(GatewayClient(gateway_paths(self._gateway_run_dir())))

    def _gateway_run_dir(self) -> str | None:
        run_dir = str(getattr(self._service, "dbus_gateway_run_dir", "") or "").strip()
        return run_dir or None

    def _required_gateway_operations(self) -> GatewayOperationsPort:
        if self._gateway_operations is None:
            raise RuntimeError("semantic gateway operations are not initialized")
        return self._gateway_operations

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

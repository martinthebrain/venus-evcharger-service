# SPDX-License-Identifier: GPL-3.0-or-later
"""Virtual-state publishing and update-cycle helpers for the Venus EV charger service."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass

from venus_evcharger.update.input_cache import InputCacheResolver
from venus_evcharger.update.learning import LearningController
from venus_evcharger.update.offline_publish import OfflinePublisher
from venus_evcharger.update.pm_snapshot import PmSnapshotResolver
from venus_evcharger.update.readback_resolver import ReadbackResolver
from venus_evcharger.update.relay import RelayComponents, build_relay_foundation, complete_relay_components
from venus_evcharger.update.runtime_cycle import RuntimeCycleCoordinator
from venus_evcharger.update.runtime_cycle_contracts import UpdateCycleServicePort
from venus_evcharger.update.software_update_controller import SoftwareUpdateController
from venus_evcharger.update.state import UpdateStateController
from venus_evcharger.update.victron_ess_balance import VictronEssBalanceController
from venus_evcharger.ports.gateway_operations import GatewayOperationsPort, UnavailableGatewayOperations


@dataclass(frozen=True, slots=True)
class UpdateCycleComponents:
    """Named component graph owned by one periodic update controller."""

    readbacks: ReadbackResolver
    state: UpdateStateController
    pm_snapshots: PmSnapshotResolver
    inputs: InputCacheResolver
    offline: OfflinePublisher
    relay: RelayComponents
    learning: LearningController
    victron_ess_balance: VictronEssBalanceController
    runtime_cycle: RuntimeCycleCoordinator
    software_update: SoftwareUpdateController


class UpdateCycleController:
    """Encapsulate the periodic Shelly/Auto update pipeline."""

    def __init__(
        self,
        service: UpdateCycleServicePort,
        phase_values_func: Callable[[float, float, object, object], object],
        health_code_func: Callable[[str], int],
        *,
        gateway_operations: GatewayOperationsPort | None = None,
    ) -> None:
        self.service = service
        readbacks = ReadbackResolver(service._readback_store, service, service.time_now)
        service._readback_resolver = readbacks
        relay_foundation = build_relay_foundation(phase_values_func)
        state = UpdateStateController(
            service,
            relay_foundation.targets,
            relay_foundation.health,
            health_code_func,
        )
        relay = complete_relay_components(relay_foundation, state)
        pm_snapshots = PmSnapshotResolver()
        inputs = InputCacheResolver(service)
        offline = OfflinePublisher(service, relay_foundation.telemetry, state)
        learning = LearningController(service)
        victron_ess_balance = VictronEssBalanceController(gateway_operations or UnavailableGatewayOperations())
        runtime_cycle = RuntimeCycleCoordinator(
            service,
            state,
            pm_snapshots,
            inputs,
            offline,
            relay,
            learning,
            victron_ess_balance,
        )
        self.components = UpdateCycleComponents(
            readbacks=readbacks,
            state=state,
            pm_snapshots=pm_snapshots,
            inputs=inputs,
            offline=offline,
            relay=relay,
            learning=learning,
            victron_ess_balance=victron_ess_balance,
            runtime_cycle=runtime_cycle,
            software_update=SoftwareUpdateController(),
        )

    def sign_of_life(self) -> bool:
        """Periodic heartbeat log for troubleshooting."""
        svc = self.service
        value = svc.state.last_accepted_field("ac_power_w")
        logging.info("Last accepted AC power publication: %s", value)
        return True

    def update(self) -> bool:
        """Periodic update loop: read Shelly, compute auto logic, update DBus."""
        svc = self.service
        try:
            result = self.components.runtime_cycle.run()
            now = svc.time_now()
            svc.state.flush_runtime_overrides(now)
            self.components.software_update.housekeeping(svc, now)
            return result
        except Exception as error:  # pylint: disable=broad-except
            logging.warning(
                "Error updating Venus EV charger data: %s (%s)",
                error,
                svc.state.summary(),
                exc_info=error,
            )
        return True


__all__ = ["UpdateCycleComponents", "UpdateCycleController"]

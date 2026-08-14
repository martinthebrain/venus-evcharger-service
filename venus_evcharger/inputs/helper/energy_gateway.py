# SPDX-License-Identifier: GPL-3.0-or-later
"""Transport-neutral gateway snapshots used by the auto-input helper."""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Callable

from venus_evcharger.dbus_gateway_client import GatewayClient
from venus_evcharger.inputs.helper.config_runtime import AutoInputHelperSettings
from venus_evcharger.inputs.helper.contracts import (
    EnergyGatewayClientPort,
    EnergyMeasurementKey,
    EnergySnapshotReaderPort,
    SnapshotPort,
)
from venus_evcharger.ipc.energy import (
    EnergyInputsSnapshot,
    EnergyRefreshRequest,
    EnergyRefreshScope,
    EnergyTopologySnapshot,
    MeasuredValue,
)


class GatewayEnergySnapshots:
    """Own typed snapshot loading and rate-limited semantic refresh requests."""

    def __init__(
        self,
        settings: AutoInputHelperSettings,
        client: EnergyGatewayClientPort | None = None,
    ) -> None:
        self.settings = settings
        self._client = client or GatewayClient(settings.gateway_paths)
        self._inputs: EnergyInputsSnapshot | None = None
        self._topology: EnergyTopologySnapshot | None = None
        self._request_after: dict[tuple[EnergyRefreshScope, str], float] = {}

    def refresh_inputs(self) -> EnergyInputsSnapshot | None:
        self._inputs = self._client.load_energy_inputs(
            max_age_seconds=self.settings.gateway_max_age_seconds,
        )
        return self._inputs

    def refresh_topology(self) -> EnergyTopologySnapshot | None:
        self._topology = self._client.load_energy_topology(
            max_age_seconds=self.settings.topology_refresh_seconds * 2.0,
        )
        return self._topology

    def measurement(self, key: EnergyMeasurementKey) -> MeasuredValue | None:
        if self._inputs is None:
            return None
        return {
            "pv": self._inputs.pv_power_w,
            "grid": self._inputs.grid_power_w,
            "battery": self._inputs.battery_soc,
            "battery_power": self._inputs.battery_net_power_w,
            "battery_capacity_wh": self._inputs.battery_capacity_wh,
            "battery_capacity_ah": self._inputs.battery_capacity_ah,
            "battery_voltage": self._inputs.battery_voltage_v,
        }.get(key)

    def request_refresh(
        self,
        scope: EnergyRefreshScope,
        *,
        reason: str,
        priority: bool = False,
    ) -> bool:
        request_key = (scope, "")
        now = time.monotonic()
        if now < self._request_after.get(request_key, 0.0):
            return False
        self._request_after[request_key] = now + self.settings.gateway_error_retry_seconds
        request = EnergyRefreshRequest(
            request_id=uuid.uuid4().hex,
            scope=scope,
            max_age_seconds=self._refresh_max_age(scope),
            urgency="priority" if priority else "normal",
            reason=reason,
        )
        try:
            return bool(
                self._client.request_energy_refresh(
                    request,
                    source="auto-input-helper",
                ).accepted
            )
        except (OSError, RuntimeError, TypeError, ValueError) as error:
            logging.debug("Energy refresh request failed scope=%s: %s", scope, error)
            return False

    def reset(self) -> None:
        self._inputs = None
        self._topology = None
        self._request_after.clear()

    def _refresh_max_age(self, scope: EnergyRefreshScope) -> float:
        if scope == "topology":
            return self.settings.topology_refresh_seconds
        return self.settings.gateway_max_age_seconds


class EnergyRefreshCoordinator:
    """Drive semantic startup and topology refreshes without DBus knowledge."""

    def __init__(
        self,
        gateway: EnergySnapshotReaderPort,
        snapshots: SnapshotPort,
        stop_requested: Callable[[], bool],
    ) -> None:
        self.gateway = gateway
        self.snapshots = snapshots
        self._stop_requested = stop_requested

    def refresh(self) -> bool:
        if self._stopping():
            return False
        inputs = self.gateway.refresh_inputs()
        topology = self.gateway.refresh_topology()
        if inputs is None:
            self.gateway.request_refresh("all", reason="initial semantic energy snapshot", priority=True)
        if topology is None:
            self.gateway.request_refresh("topology", reason="initial semantic energy topology")
        self.snapshots.refresh_all()
        return False

    def timer_tick(self) -> bool:
        if self._stopping():
            return False
        self.gateway.refresh_topology()
        self.gateway.request_refresh("topology", reason="periodic semantic topology refresh")
        return True

    def reset(self) -> None:
        self.gateway.reset()

    def _stopping(self) -> bool:
        return bool(self._stop_requested())

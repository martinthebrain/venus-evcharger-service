# SPDX-License-Identifier: GPL-3.0-or-later
"""Gateway-backed input helpers for the Venus EV charger service."""

from __future__ import annotations

from venus_evcharger.inputs.gateway_read import GatewayInputReader, InputSourceHealth
from venus_evcharger.inputs.pv import PvInputReader
from venus_evcharger.inputs.storage import StorageInputReader
from venus_evcharger.inputs.storage_support import EnergyServiceResolver
from venus_evcharger.ports.dbus import DbusInputPort, DbusRawValue

__all__ = ("DbusInputController",)


class DbusInputController:
    """Expose semantic PV, battery, and grid inputs from the DBus gateway."""

    def __init__(self, port: DbusInputPort) -> None:
        self.gateway = GatewayInputReader(port)
        self.source_health = InputSourceHealth(port)
        self.pv = PvInputReader(port, self.gateway, self.source_health)
        self.energy_services = EnergyServiceResolver(port, self.gateway)
        self.storage = StorageInputReader(port, self.gateway, self.source_health, self.energy_services)
        port.bind_controller(self)

    def get_dbus_value(self, service_name: str, path: str) -> DbusRawValue:
        return self.gateway.get_dbus_value(service_name, path)

    def list_dbus_services(self) -> list[str]:
        return self.gateway.list_dbus_services()

    def invalidate_auto_pv_services(self) -> None:
        self.pv.invalidate_auto_pv_services()

    def resolve_auto_pv_services(self) -> list[str]:
        return self.pv.resolve_auto_pv_services()

    def get_pv_power(self) -> float | None:
        return self.pv.get_pv_power()

    def invalidate_auto_battery_service(self) -> None:
        self.energy_services.invalidate_auto_battery_service()

    def invalidate_energy_source_service(
        self,
        source_id: str,
        *,
        expected_service: str | None = None,
    ) -> bool:
        return self.energy_services.invalidate_energy_source_service(
            source_id,
            expected_service=expected_service,
        )

    def resolve_auto_battery_service(self) -> str:
        return self.energy_services.resolve_auto_battery_service()

    def get_battery_snapshot(self) -> dict[str, object]:
        return self.storage.get_battery_snapshot()

    def get_battery_soc(self) -> float | None:
        return self.storage.get_battery_soc()

    def get_grid_power(self) -> float | None:
        return self.storage.get_grid_power()

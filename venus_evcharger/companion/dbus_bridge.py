# SPDX-License-Identifier: GPL-3.0-or-later
"""Optional companion DBus services for aggregated external energy visibility."""

from __future__ import annotations

import platform
import time
from typing import Any, Mapping

from venus_evcharger.dbus_gateway import GatewayDbusServiceProxy, gateway_paths
from .dbus_bridge_services import _EnergyCompanionDbusBridgeServices

_MISSING_VALUE = object()


class EnergyCompanionDbusBridge(_EnergyCompanionDbusBridgeServices):
    """Publish optional aggregated battery, PV, and grid companion services on DBus."""

    def __init__(self, service: Any, script_path: str) -> None:
        self.service = service
        self._script_path = script_path
        self._battery_service: Any = None
        self._pvinverter_service: Any = None
        self._grid_service: Any = None
        self._source_battery_services: dict[str, Any] = {}
        self._source_pvinverter_services: dict[str, Any] = {}
        self._source_grid_services: dict[str, Any] = {}
        self._published_values: dict[str, dict[str, Any]] = {}
        self._grid_hold_state: dict[str, dict[str, Any]] = {}

    def start(self) -> None:
        """Create and register companion services when enabled."""
        svc = self.service
        base_device_instance = int(getattr(svc, "deviceinstance", 0))
        if not self._companion_bridge_enabled(svc):
            return
        self._ensure_battery_service(base_device_instance)
        self._ensure_pvinverter_service(base_device_instance)
        self._ensure_grid_service(base_device_instance)

    def stop(self) -> None:
        """Release companion service references."""
        self._battery_service = None
        self._pvinverter_service = None
        self._grid_service = None
        self._source_battery_services = {}
        self._source_pvinverter_services = {}
        self._source_grid_services = {}
        self._published_values = {}
        self._grid_hold_state = {}

    def publish(self, now: float | None = None) -> bool:
        """Publish the latest worker snapshot to any active companion services."""
        svc = self.service
        if not self._companion_bridge_enabled(svc):
            return False
        if self._companion_publish_should_enqueue(svc):
            return bool(svc.runtime.enqueue_companion_dbus_publish(now))
        current_time = self._companion_publish_time(now)
        normalized_snapshot = self._companion_worker_snapshot(svc)
        return self._publish_companion_snapshot(normalized_snapshot, current_time)

    @staticmethod
    def _companion_bridge_enabled(svc: Any) -> bool:
        """Return whether optional companion DBus publication is enabled."""
        return hasattr(svc, "companion_dbus_bridge_enabled") and bool(getattr(svc, "companion_dbus_bridge_enabled"))

    def _source_services_enabled(self) -> bool:
        """Return whether per-source companion publication is enabled."""
        return not hasattr(self.service, "companion_source_services_enabled") or bool(
            getattr(self.service, "companion_source_services_enabled")
        )

    def _source_grid_services_enabled(self) -> bool:
        """Return whether per-source grid companion publication is enabled."""
        return hasattr(self.service, "companion_source_grid_services_enabled") and bool(
            getattr(self.service, "companion_source_grid_services_enabled")
        )

    @staticmethod
    def _companion_publish_should_enqueue(svc: Any) -> bool:
        """Return whether companion DBus publish must run on the mainloop."""
        return not svc.runtime.dbus_publish_direct_allowed()

    @staticmethod
    def _companion_publish_time(now: float | None) -> float:
        """Return publish timestamp for companion snapshots."""
        return float(now) if isinstance(now, (int, float)) else time.monotonic()

    @staticmethod
    def _companion_worker_snapshot(svc: Any) -> dict[str, Any]:
        """Return a normalized worker snapshot mapping."""
        snapshot = svc.runtime.worker_snapshot()
        return dict(snapshot) if isinstance(snapshot, Mapping) else {}

    def _publish_companion_snapshot(self, snapshot: dict[str, Any], current_time: float) -> bool:
        """Publish one normalized companion snapshot."""
        publish_results = (
            self._publish_battery_snapshot(snapshot),
            self._publish_pvinverter_snapshot(snapshot),
            self._publish_grid_snapshot(snapshot, current_time),
            self._publish_source_snapshots(snapshot, current_time),
        )
        return any(publish_results)

    def _register_service(
        self,
        service_name: str,
        device_instance: int,
        product_label: str,
        specific_paths: Mapping[str, Any],
    ) -> Any:
        run_dir = str(getattr(self.service, "dbus_gateway_run_dir", None) or "")
        service = GatewayDbusServiceProxy(service_name, paths=gateway_paths(run_dir or None))
        self._register_common_paths(service, device_instance, product_label)
        for path, initial in specific_paths.items():
            service.add_path(path, initial)
        service.register()
        return service

    def _register_common_paths(self, dbus_service: Any, device_instance: int, product_label: str) -> None:
        svc = self.service
        dbus_service.add_path("/Mgmt/ProcessName", self._script_path)
        dbus_service.add_path(
            "/Mgmt/ProcessVersion",
            "Unknown version, and running on Python " + platform.python_version(),
        )
        dbus_service.add_path("/Mgmt/Connection", getattr(svc, "connection_name", "External energy companion"))
        dbus_service.add_path("/DeviceInstance", int(device_instance))
        dbus_service.add_path("/ProductId", 0xFFFF)
        dbus_service.add_path("/ProductName", product_label)
        dbus_service.add_path("/CustomName", f"{getattr(svc, 'custom_name', 'Venus EV Charger')} {product_label}")
        dbus_service.add_path("/FirmwareVersion", getattr(svc, "firmware_version", ""))
        dbus_service.add_path("/HardwareVersion", getattr(svc, "hardware_version", ""))
        dbus_service.add_path("/Serial", getattr(svc, "serial", ""))
        dbus_service.add_path("/Connected", 0)
        dbus_service.add_path("/UpdateIndex", 0)

    def _publish_service_values(
        self,
        service_key: str,
        dbus_service: Any,
        values: Mapping[str, Any],
    ) -> bool:
        self.service.runtime.assert_dbus_mainloop_thread(f"companion DBus publish {service_key}")
        previous_values = self._published_values.setdefault(service_key, {})
        changed = False
        for path, value in values.items():
            if previous_values.get(path, _MISSING_VALUE) == value:
                continue
            dbus_service[path] = value
            previous_values[path] = value
            changed = True
        if changed:
            dbus_service["/UpdateIndex"] = int(dbus_service["/UpdateIndex"]) + 1
        return changed

    def _publish_source_snapshots(self, snapshot: Mapping[str, Any], now: float) -> bool:
        if not self._source_services_enabled():
            return False
        source_snapshots = self._normalized_source_snapshots(snapshot)
        changed = False
        for index, source in enumerate(source_snapshots):
            changed = self._publish_one_source_snapshot(source, index, now) or changed
        return changed

    def _publish_one_source_snapshot(self, source: Mapping[str, Any], index: int, now: float) -> bool:
        publish_results = (
            self._publish_battery_source_service(source, index),
            self._publish_pvinverter_source_service(source, index),
            self._publish_grid_source_service(source, index, now),
        )
        return any(publish_results)

    def _publish_battery_source_service(self, source: Mapping[str, Any], index: int) -> bool:
        if not self._source_supports_battery_service(source):
            return False
        battery_service = self._ensure_source_battery_service(source, index)
        return self._publish_service_values(
            f"source-battery:{source['source_id']}",
            battery_service,
            self._battery_source_values(source),
        )

    def _publish_pvinverter_source_service(self, source: Mapping[str, Any], index: int) -> bool:
        if not self._source_supports_pvinverter_service(source):
            return False
        pvinverter_service = self._ensure_source_pvinverter_service(source, index)
        return self._publish_service_values(
            f"source-pvinverter:{source['source_id']}",
            pvinverter_service,
            self._pvinverter_source_values(source),
        )

    def _publish_grid_source_service(self, source: Mapping[str, Any], index: int, now: float) -> bool:
        if not self._source_grid_services_enabled():
            return False
        source_id = str(source.get("source_id", "")).strip()
        grid_service = self._source_grid_services.get(source_id)
        if grid_service is None and not self._source_supports_grid_service(source):
            return False
        if grid_service is None:
            grid_service = self._ensure_source_grid_service(source, index)
        return self._publish_service_values(
            f"source-grid:{source_id}",
            grid_service,
            self._grid_source_values(source, now),
        )

    def _publish_battery_snapshot(self, snapshot: Mapping[str, Any]) -> bool:
        if self._battery_service is None:
            return False
        return self._publish_service_values(
            "battery",
            self._battery_service,
            {
                "/Connected": self._battery_connected(snapshot),
                "/Soc": snapshot.get("battery_combined_soc"),
                "/Dc/0/Power": snapshot.get("battery_combined_net_power_w", 0.0),
                "/Capacity": snapshot.get("battery_combined_usable_capacity_wh"),
            },
        )

    def _publish_pvinverter_snapshot(self, snapshot: Mapping[str, Any]) -> bool:
        if self._pvinverter_service is None:
            return False
        pv_power = self._pvinverter_power_w(snapshot)
        return self._publish_service_values(
            "pvinverter",
            self._pvinverter_service,
            {
                "/Connected": self._pvinverter_connected(snapshot),
                "/Ac/Power": pv_power,
                "/Ac/L1/Power": pv_power,
                "/Ac/L2/Power": 0.0,
                "/Ac/L3/Power": 0.0,
            },
        )

    def _publish_grid_snapshot(self, snapshot: Mapping[str, Any], now: float) -> bool:
        if self._grid_service is None:
            return False
        grid_snapshot = self._grid_snapshot_values(snapshot, now)
        return self._publish_service_values(
            "grid",
            self._grid_service,
            {
                "/Connected": 1 if grid_snapshot["connected"] else 0,
                "/Ac/Power": grid_snapshot["value"],
                "/Ac/L1/Power": grid_snapshot["value"],
                "/Ac/L2/Power": 0.0,
                "/Ac/L3/Power": 0.0,
            },
        )

    @staticmethod
    def _battery_connected(snapshot: Mapping[str, Any]) -> int:
        source_count = int(snapshot.get("battery_source_count") or 0)
        online_count = int(snapshot.get("battery_online_source_count") or 0)
        return 1 if source_count > 0 and online_count > 0 else 0

    @staticmethod
    def _pvinverter_connected(snapshot: Mapping[str, Any]) -> int:
        pv_power = snapshot.get("battery_combined_pv_input_power_w")
        ac_power = snapshot.get("battery_combined_ac_power_w")
        if isinstance(pv_power, (int, float)) and float(pv_power) > 0.0:
            return 1
        if isinstance(ac_power, (int, float)) and float(ac_power) > 0.0:
            return 1
        return EnergyCompanionDbusBridge._battery_connected(snapshot)

    @staticmethod
    def _pvinverter_power_w(snapshot: Mapping[str, Any]) -> float:
        pv_power = snapshot.get("battery_combined_pv_input_power_w")
        if isinstance(pv_power, (int, float)):
            return max(0.0, float(pv_power))
        ac_power = snapshot.get("battery_combined_ac_power_w")
        if isinstance(ac_power, (int, float)):
            return max(0.0, float(ac_power))
        return 0.0

    @staticmethod
    def _battery_source_values(source: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "/Connected": 1 if bool(source.get("online")) else 0,
            "/Soc": source.get("soc"),
            "/Dc/0/Power": source.get("net_battery_power_w", 0.0),
            "/Capacity": source.get("usable_capacity_wh"),
        }

    @staticmethod
    def _pvinverter_source_values(source: Mapping[str, Any]) -> dict[str, Any]:
        pv_power = EnergyCompanionDbusBridge._source_pvinverter_power_w(source)
        return {
            "/Connected": 1 if bool(source.get("online")) else 0,
            "/Ac/Power": pv_power,
            "/Ac/L1/Power": pv_power,
            "/Ac/L2/Power": 0.0,
            "/Ac/L3/Power": 0.0,
        }

    @staticmethod
    def _source_pvinverter_power_w(source: Mapping[str, Any]) -> float:
        pv_power = source.get("pv_input_power_w")
        if isinstance(pv_power, (int, float)):
            return max(0.0, float(pv_power))
        ac_power = source.get("ac_power_w")
        if isinstance(ac_power, (int, float)):
            return max(0.0, float(ac_power))
        return 0.0

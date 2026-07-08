# SPDX-License-Identifier: GPL-3.0-or-later
"""Service registration and source-service helpers for the DBus companion bridge."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any, Mapping

from .dbus_bridge_grid import _EnergyCompanionDbusBridgeGrid

_SOURCE_BASE_ATTRS = {
    "battery": "companion_source_battery_deviceinstance_base",
    "pvinverter": "companion_source_pvinverter_deviceinstance_base",
    "grid": "companion_source_grid_deviceinstance_base",
}

_SOURCE_SERVICE_PREFIX_ATTRS = {
    "battery": "companion_source_battery_service_prefix",
    "pvinverter": "companion_source_pvinverter_service_prefix",
    "grid": "companion_source_grid_service_prefix",
}

_SOURCE_DEFAULT_SERVICE_PREFIXES = {
    "battery": "com.victronenergy.battery.external",
    "pvinverter": "com.victronenergy.pvinverter.external",
    "grid": "com.victronenergy.grid.external",
}


class _EnergyCompanionDbusBridgeServices(_EnergyCompanionDbusBridgeGrid):
    _battery_service: Any
    _pvinverter_service: Any
    _grid_service: Any

    if TYPE_CHECKING:  # pragma: no cover
        service: Any
        _source_battery_services: dict[str, Any]
        _source_pvinverter_services: dict[str, Any]
        _source_grid_services: dict[str, Any]

        def _register_service(
            self,
            service_name: str,
            device_instance: int,
            product_label: str,
            specific_paths: Mapping[str, Any],
        ) -> Any: ...

    def _ensure_battery_service(self, base_device_instance: int) -> None:
        svc = self.service
        if not bool(getattr(svc, "companion_battery_service_enabled", True)) or self._battery_service is not None:
            return
        self._battery_service = self._register_service(
            getattr(
                svc,
                "companion_battery_service_name",
                f"com.victronenergy.battery.external_{base_device_instance}",
            ),
            int(getattr(svc, "companion_battery_deviceinstance", base_device_instance + 40)),
            "External Energy Battery",
            {
                "/Soc": None,
                "/Dc/0/Power": 0.0,
                "/Capacity": None,
            },
        )

    def _ensure_pvinverter_service(self, base_device_instance: int) -> None:
        svc = self.service
        if not bool(getattr(svc, "companion_pvinverter_service_enabled", True)) or self._pvinverter_service is not None:
            return
        self._pvinverter_service = self._register_service(
            getattr(
                svc,
                "companion_pvinverter_service_name",
                f"com.victronenergy.pvinverter.external_{base_device_instance}",
            ),
            int(getattr(svc, "companion_pvinverter_deviceinstance", base_device_instance + 41)),
            "External Energy PV",
            {
                "/Ac/Power": 0.0,
                "/Ac/L1/Power": 0.0,
                "/Ac/L2/Power": 0.0,
                "/Ac/L3/Power": 0.0,
            },
        )

    def _ensure_grid_service(self, base_device_instance: int) -> None:
        svc = self.service
        if (
            not hasattr(svc, "companion_grid_service_enabled")
            or not bool(getattr(svc, "companion_grid_service_enabled"))
            or self._grid_service is not None
        ):
            return
        self._grid_service = self._register_service(
            getattr(
                svc,
                "companion_grid_service_name",
                f"com.victronenergy.grid.external_{base_device_instance}",
            ),
            int(getattr(svc, "companion_grid_deviceinstance", base_device_instance + 42)),
            "External Energy Grid",
            {
                "/Ac/Power": 0.0,
                "/Ac/L1/Power": 0.0,
                "/Ac/L2/Power": 0.0,
                "/Ac/L3/Power": 0.0,
            },
        )

    def _ensure_source_battery_service(self, source: Mapping[str, Any], index: int) -> Any:
        source_id = self._source_id_value(source)
        existing = self._source_battery_services.get(source_id)
        if existing is not None:
            return existing
        device_instance = self._source_device_instance("battery", index)
        service = self._register_service(
            self._source_service_name("battery", source_id, device_instance),
            device_instance,
            self._source_product_label(source, "Battery"),
            {
                "/Soc": None,
                "/Dc/0/Power": 0.0,
                "/Capacity": None,
            },
        )
        self._source_battery_services[source_id] = service
        return service

    def _ensure_source_pvinverter_service(self, source: Mapping[str, Any], index: int) -> Any:
        source_id = self._source_id_value(source)
        existing = self._source_pvinverter_services.get(source_id)
        if existing is not None:
            return existing
        device_instance = self._source_device_instance("pvinverter", index)
        service = self._register_service(
            self._source_service_name("pvinverter", source_id, device_instance),
            device_instance,
            self._source_product_label(source, "PV"),
            {
                "/Ac/Power": 0.0,
                "/Ac/L1/Power": 0.0,
                "/Ac/L2/Power": 0.0,
                "/Ac/L3/Power": 0.0,
            },
        )
        self._source_pvinverter_services[source_id] = service
        return service

    def _ensure_source_grid_service(self, source: Mapping[str, Any], index: int) -> Any:
        source_id = self._source_id_value(source)
        existing = self._source_grid_services.get(source_id)
        if existing is not None:
            return existing
        device_instance = self._source_device_instance("grid", index)
        service = self._register_service(
            self._source_service_name("grid", source_id, device_instance),
            device_instance,
            self._source_product_label(source, "Grid"),
            {
                "/Ac/Power": 0.0,
                "/Ac/L1/Power": 0.0,
                "/Ac/L2/Power": 0.0,
                "/Ac/L3/Power": 0.0,
            },
        )
        self._source_grid_services[source_id] = service
        return service

    def _source_device_instance(self, service_kind: str, index: int) -> int:
        attr_name = self._source_kind_value(_SOURCE_BASE_ATTRS, service_kind)
        raw_base = getattr(self.service, attr_name, None)
        base_device_instance = 0 if raw_base is None else int(raw_base)
        return base_device_instance + int(index)

    @staticmethod
    def _source_default_service_prefix(service_kind: str) -> str:
        return _EnergyCompanionDbusBridgeServices._source_kind_value(_SOURCE_DEFAULT_SERVICE_PREFIXES, service_kind)

    def _source_configured_service_prefix(self, service_kind: str) -> str:
        attr_name = self._source_kind_value(_SOURCE_SERVICE_PREFIX_ATTRS, service_kind)
        raw_prefix = getattr(self.service, attr_name, None)
        return str(raw_prefix).strip() if raw_prefix is not None else self._source_default_service_prefix(service_kind)

    def _source_service_name(self, service_kind: str, source_id: str, device_instance: int) -> str:
        sanitized_source_id = self._sanitize_source_id(source_id or str(device_instance))
        configured_prefix = self._source_configured_service_prefix(service_kind)
        prefix = configured_prefix.rstrip(".") or self._source_default_service_prefix(service_kind)
        return f"{prefix}.{sanitized_source_id}"

    @staticmethod
    def _source_product_label(source: Mapping[str, Any], suffix: str) -> str:
        source_id = _EnergyCompanionDbusBridgeServices._source_id_value(source) or "source"
        return f"External Energy {source_id} {suffix}"

    @staticmethod
    def _sanitize_source_id(source_id: str) -> str:
        normalized = re.sub(r"[^a-zA-Z0-9]+", "_", str(source_id).strip()).strip("_").lower()
        return normalized or "source"

    @classmethod
    def _normalized_source_snapshots(cls, snapshot: Mapping[str, Any]) -> tuple[dict[str, Any], ...]:
        sources = snapshot.get("battery_sources")
        if not isinstance(sources, list):
            return ()
        normalized_sources: list[dict[str, Any]] = []
        for item in sources:
            if not isinstance(item, Mapping):
                continue
            normalized = dict(item)
            if not cls._source_id_value(normalized):
                continue
            normalized_sources.append(normalized)
        return tuple(normalized_sources)

    @classmethod
    def _source_supports_battery_service(cls, source: Mapping[str, Any]) -> bool:
        return cls._source_role(source) in {"battery", "hybrid-inverter"}

    @classmethod
    def _source_supports_pvinverter_service(cls, source: Mapping[str, Any]) -> bool:
        return cls._source_role(source) in {"hybrid-inverter", "inverter"}

    @staticmethod
    def _source_supports_grid_service(source: Mapping[str, Any]) -> bool:
        return isinstance(source.get("grid_interaction_w"), (int, float))

    @staticmethod
    def _source_kind_value(values: Mapping[str, str], service_kind: str) -> str:
        if service_kind not in values:
            raise ValueError(f"Unsupported source service kind: {service_kind}")
        return values[service_kind]

    @classmethod
    def _source_role(cls, source: Mapping[str, Any]) -> str:
        raw_role = source["role"] if "role" in source else ""
        return cls._string_value(raw_role)

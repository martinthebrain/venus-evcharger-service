# SPDX-License-Identifier: GPL-3.0-or-later
"""Semantic publication of aggregate and per-source energy companions."""

from __future__ import annotations

import hashlib
import platform
import time
from collections.abc import Mapping

from venus_evcharger.ports.gateway_publication import (
    CompanionServiceIdentity,
    CompanionServiceKind,
    GatewayPublicationPort,
    require_gateway_publication,
)

from .grid_projection import GridProjectionConfig, GridProjector, aggregate_grid_input

_AGGREGATE_BATTERY_ID = "aggregate-battery"
_AGGREGATE_PV_ID = "aggregate-pv"
_AGGREGATE_GRID_ID = "aggregate-grid"
_MISSING = object()
_AGGREGATE_SERVICE_IDS = (_AGGREGATE_BATTERY_ID, _AGGREGATE_PV_ID, _AGGREGATE_GRID_ID)
_SOURCE_KINDS_BY_ROLE: Mapping[str, tuple[CompanionServiceKind, ...]] = {
    "battery": ("battery",),
    "hybrid-inverter": ("battery", "pv_inverter"),
    "inverter": ("pv_inverter",),
}


class EnergyCompanionPublisher:
    """Translate worker energy snapshots into semantic gateway fields."""

    def __init__(self, service: object, script_path: str) -> None:
        self._service = service
        self._script_path = script_path
        self._publication: GatewayPublicationPort | None = None
        self._registered: set[str] = set()
        self._accepted_fields: dict[str, dict[str, object]] = {}
        self._grid = GridProjector()

    def start(self) -> None:
        """Register enabled aggregate companions through the gateway contract."""
        if not self._enabled:
            return
        self._publication_port()
        if self._flag("companion_battery_service_enabled", True):
            self._register(_AGGREGATE_BATTERY_ID, "battery", "External Energy Battery", _battery_defaults())
        if self._flag("companion_pvinverter_service_enabled", True):
            self._register(_AGGREGATE_PV_ID, "pv_inverter", "External Energy PV", _power_defaults())
        if self._flag("companion_grid_service_enabled", False):
            self._register(_AGGREGATE_GRID_ID, "grid", "External Energy Grid", _power_defaults())

    def stop(self) -> None:
        """Forget producer-side acceptance state without mirroring gateway state."""
        self._publication = None
        self._registered.clear()
        self._accepted_fields.clear()
        self._grid.clear()

    def publish(self, now: float | None = None) -> bool:
        """Enqueue changed semantic fields and return whether any were accepted."""
        if not self._enabled:
            return False
        self._publication_port()
        snapshot = _worker_snapshot(self._service)
        sources = _source_snapshots(snapshot)
        current = _publication_time(now)
        accepted = self._publish_aggregates(snapshot, sources, current)
        return self._publish_enabled_sources(sources, current) or accepted

    @property
    def _enabled(self) -> bool:
        return self._flag("companion_publication_enabled", False)

    def _publish_aggregates(
        self,
        snapshot: Mapping[str, object],
        sources: tuple[Mapping[str, object], ...],
        now: float,
    ) -> bool:
        accepted = False
        for service_id in _AGGREGATE_SERVICE_IDS:
            if service_id in self._registered:
                fields = self._aggregate_fields(service_id, snapshot, sources, now)
                accepted = self._publish(service_id, fields) or accepted
        return accepted

    def _publish_sources(self, sources: tuple[Mapping[str, object], ...], now: float) -> bool:
        accepted = False
        for source in sources:
            accepted = self._publish_source_services(source, now) or accepted
        return accepted

    def _publish_enabled_sources(self, sources: tuple[Mapping[str, object], ...], now: float) -> bool:
        if not self._flag("companion_source_services_enabled", True):
            return False
        return self._publish_sources(sources, now)

    def _aggregate_fields(
        self,
        service_id: str,
        snapshot: Mapping[str, object],
        sources: tuple[Mapping[str, object], ...],
        now: float,
    ) -> Mapping[str, object]:
        if service_id == _AGGREGATE_BATTERY_ID:
            return _battery_fields(snapshot)
        if service_id == _AGGREGATE_PV_ID:
            return _aggregate_pv_fields(snapshot)
        value, online = aggregate_grid_input(
            snapshot,
            sources,
            authoritative_source_id=self._text("companion_grid_authoritative_source"),
        )
        projected = self._grid.project(
            _AGGREGATE_GRID_ID,
            raw_value=value,
            online=online,
            now=now,
            config=self._grid_config("companion_grid"),
        )
        return _power_fields(projected.value_w, projected.connected)

    def _publish_source_services(self, source: Mapping[str, object], now: float) -> bool:
        role = _text_value(source.get("role"))
        source_id = _text_value(source.get("source_id"))
        accepted = False
        for kind in _SOURCE_KINDS_BY_ROLE.get(role, ()):
            accepted = self._publish_source(source_id, kind, source, now) or accepted
        grid_value = _numeric(source.get("grid_interaction_w"))
        return self._publish_source_grid(source_id, source, grid_value, now) or accepted

    def _publish_source_grid(
        self,
        source_id: str,
        source: Mapping[str, object],
        grid_value: float | None,
        now: float,
    ) -> bool:
        if not self._flag("companion_source_grid_services_enabled", False) or grid_value is None:
            return False
        return self._publish_source(source_id, "grid", source, now)

    def _publish_source(
        self,
        source_id: str,
        kind: CompanionServiceKind,
        source: Mapping[str, object],
        now: float,
    ) -> bool:
        service_id = _opaque_source_id(kind, source_id)
        label = {"battery": "Battery", "pv_inverter": "PV", "grid": "Grid"}[kind]
        if kind == "battery":
            fields = _battery_source_fields(source)
        elif kind == "pv_inverter":
            fields = _source_pv_fields(source)
        else:
            projected = self._grid.project(
                service_id,
                raw_value=source.get("grid_interaction_w"),
                online=bool(source.get("online", False)),
                now=now,
                config=self._grid_config("companion_source_grid"),
            )
            fields = _power_fields(projected.value_w, projected.connected)
        if service_id not in self._registered:
            return self._register(service_id, kind, f"External Energy {source_id} {label}", fields)
        return self._publish(service_id, fields)

    def _register(
        self,
        service_id: str,
        kind: CompanionServiceKind,
        product_name: str,
        fields: Mapping[str, object],
    ) -> bool:
        receipt = self._publication_port().register_companion(self._identity(service_id, kind, product_name), fields)
        if not receipt.accepted:
            return False
        self._registered.add(service_id)
        self._accepted_fields[service_id] = dict(fields)
        return True

    def _publish(self, service_id: str, fields: Mapping[str, object]) -> bool:
        previous = self._accepted_fields.get(service_id, {})
        changed = _changed_fields(previous, fields)
        if not changed:
            return False
        publication = self._publication_port()
        receipt = publication.publish_companion_fields(service_id, changed, priority="live")
        if not receipt.accepted:
            return False
        self._remember_accepted(service_id, previous, changed)
        return True

    def _publication_port(self) -> GatewayPublicationPort:
        publication = self._publication
        if publication is None:
            publication = require_gateway_publication(self._service)
            self._publication = publication
        return publication

    def _remember_accepted(
        self,
        service_id: str,
        previous: dict[str, object],
        changed: Mapping[str, object],
    ) -> None:
        previous.update(changed)
        self._accepted_fields[service_id] = previous

    def _identity(
        self,
        service_id: str,
        kind: CompanionServiceKind,
        product_name: str,
    ) -> CompanionServiceIdentity:
        custom_name = self._text("custom_name") or self._text("custom_name_override") or "Venus EV Charger"
        return CompanionServiceIdentity(
            service_id=service_id,
            kind=kind,
            product_name=product_name,
            custom_name=f"{custom_name} {product_name}",
            firmware_version=self._text("firmware_version"),
            hardware_version=self._text("hardware_version"),
            serial=self._text("serial"),
            connection_name=self._text("connection_name") or "External energy companion",
            process_name=self._script_path,
            process_version="Unknown version, and running on Python " + platform.python_version(),
        )

    def _grid_config(self, prefix: str) -> GridProjectionConfig:
        return GridProjectionConfig(
            hold_seconds=self._float(f"{prefix}_hold_seconds", 0.0),
            smoothing_alpha=self._float(f"{prefix}_smoothing_alpha", 1.0),
            smoothing_max_jump_watts=self._float(f"{prefix}_smoothing_max_jump_watts", 0.0),
        )

    def _flag(self, name: str, fallback: bool) -> bool:
        return bool(getattr(self._service, name, fallback))

    def _text(self, name: str) -> str:
        return _text_value(getattr(self._service, name, ""))

    def _float(self, name: str, fallback: float) -> float:
        raw = getattr(self._service, name, fallback)
        try:
            return float(raw)
        except (TypeError, ValueError):
            return fallback


def _worker_snapshot(service: object) -> dict[str, object]:
    runtime = getattr(service, "runtime", None)
    snapshot = runtime.worker_snapshot() if runtime is not None and hasattr(runtime, "worker_snapshot") else {}
    return {str(key): value for key, value in snapshot.items()} if isinstance(snapshot, Mapping) else {}


def _publication_time(now: float | None) -> float:
    return float(now) if isinstance(now, (int, float)) else time.monotonic()


def _changed_fields(
    previous: Mapping[str, object],
    fields: Mapping[str, object],
) -> dict[str, object]:
    return {field: value for field, value in fields.items() if previous.get(field, _MISSING) != value}


def _source_snapshots(snapshot: Mapping[str, object]) -> tuple[Mapping[str, object], ...]:
    sources = snapshot.get("battery_sources")
    if not isinstance(sources, list):
        return ()
    return tuple(item for item in sources if isinstance(item, Mapping) and _text_value(item.get("source_id")))


def _battery_defaults() -> dict[str, object]:
    return {"connected": 0, "soc_percent": None, "dc_power_w": 0.0, "capacity_wh": None}


def _power_defaults() -> dict[str, object]:
    return _power_fields(0.0, False)


def _battery_fields(snapshot: Mapping[str, object]) -> dict[str, object]:
    connected = _positive_int(snapshot.get("battery_source_count")) and _positive_int(
        snapshot.get("battery_online_source_count")
    )
    return {
        "connected": int(connected),
        "soc_percent": snapshot.get("battery_combined_soc"),
        "dc_power_w": snapshot.get("battery_combined_net_power_w", 0.0),
        "capacity_wh": snapshot.get("battery_combined_usable_capacity_wh"),
    }


def _aggregate_pv_fields(snapshot: Mapping[str, object]) -> dict[str, object]:
    power = _non_negative(snapshot.get("battery_combined_pv_input_power_w"))
    if power is None:
        power = _non_negative(snapshot.get("battery_combined_ac_power_w"))
    resolved = 0.0 if power is None else power
    connected = resolved > 0.0 or (
        _positive_int(snapshot.get("battery_source_count"))
        and _positive_int(snapshot.get("battery_online_source_count"))
    )
    return _power_fields(resolved, connected)


def _battery_source_fields(source: Mapping[str, object]) -> dict[str, object]:
    return {
        "connected": int(bool(source.get("online", False))),
        "soc_percent": source.get("soc"),
        "dc_power_w": source.get("net_battery_power_w", 0.0),
        "capacity_wh": source.get("usable_capacity_wh"),
    }


def _source_pv_fields(source: Mapping[str, object]) -> dict[str, object]:
    power = _non_negative(source.get("pv_input_power_w"))
    if power is None:
        power = _non_negative(source.get("ac_power_w"))
    return _power_fields(0.0 if power is None else power, bool(source.get("online", False)))


def _power_fields(power_w: float, connected: bool) -> dict[str, object]:
    return {
        "connected": int(connected),
        "ac_power_w": float(power_w),
        "l1_power_w": float(power_w),
        "l2_power_w": 0.0,
        "l3_power_w": 0.0,
    }


def _opaque_source_id(kind: CompanionServiceKind, source_id: str) -> str:
    digest = hashlib.sha256(f"{kind}:{source_id}".encode()).hexdigest()[:20]
    return f"source-{kind}-{digest}"


def _text_value(value: object) -> str:
    return "" if value is None else str(value).strip()


def _numeric(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _non_negative(value: object) -> float | None:
    numeric = _numeric(value)
    return None if numeric is None else max(0.0, numeric)


def _positive_int(value: object) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float, str)):
        try:
            return int(value or 0) > 0
        except ValueError:
            return False
    return False


__all__ = ["EnergyCompanionPublisher"]

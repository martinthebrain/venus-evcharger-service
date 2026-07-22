# SPDX-License-Identifier: GPL-3.0-or-later
"""Adapter-owned DBus energy-source discovery and binding decisions."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from venus_evcharger.dbus_adapter.read.pv import pv_total_members
from venus_evcharger.dbus_adapter.read.spec import ReadSpec, ReadSpecs
from venus_evcharger.ipc.energy import (
    EnergySourceDescriptor,
    EnergySourceKind,
    EnergySourceState,
    EnergyTopologySnapshot,
)


@dataclass(frozen=True, slots=True)
class IntrospectionTarget:
    """Private DBus target selected by adapter-owned discovery."""

    service: str
    path: str
    priority: int
    source: str
    reason: str


class DbusEnergyDiscoveryManager:
    """Own service selection while publishing only semantic source identities."""

    def __init__(self, specs: ReadSpecs, *, max_prefix_services: int = 10) -> None:
        self._specs = specs
        self._max_prefix_services = max(1, int(max_prefix_services))
        self._service_names: tuple[str, ...] = ()
        self._generation = 0
        self._captured_at = 0.0

    @property
    def generation(self) -> int:
        return self._generation

    def update_services(self, names: Sequence[str], *, now: float) -> None:
        normalized = tuple(sorted({str(name).strip() for name in names if str(name).strip()}))
        if normalized != self._service_names:
            self._service_names = normalized
            self._generation += 1
        self._captured_at = max(0.0, float(now))

    def services_for(self, spec: ReadSpec) -> list[str]:
        explicit = _spec_text(spec, "service")
        if explicit:
            return [explicit]
        prefix = _spec_text(spec, "prefix")
        if not prefix:
            return []
        return [name for name in self._service_names if name.startswith(prefix)][: self._max_prefix_services]

    def first_service(self, spec: ReadSpec) -> str | None:
        services = self.services_for(spec)
        return services[0] if services else None

    def pv_members(
        self,
        spec: ReadSpec,
        cached_values: Mapping[str, Mapping[str, object]],
    ) -> list[tuple[str, str]]:
        return pv_total_members(spec, self.services_for(spec), cached_values)

    def topology_snapshot(self, *, now: float) -> EnergyTopologySnapshot:
        captured_at = max(float(now), self._captured_at)
        return EnergyTopologySnapshot(
            generation=self._generation,
            captured_at=captured_at,
            sources=tuple(self._source_descriptors()),
        )

    def source_ids(self, kind: str) -> tuple[str, ...]:
        return tuple(source.source_id for source in self._source_descriptors() if source.kind == kind)

    def read_keys_for_source(self, source_id: str) -> tuple[str, ...]:
        for source in self._source_descriptors():
            if source.source_id == source_id:
                return _read_keys_for_kind(source.kind)
        return ()

    def introspection_targets(self) -> list[IntrospectionTarget]:
        return [
            *self._grid_introspection_targets(),
            *self._battery_introspection_targets(),
            *self._pv_introspection_targets(),
        ]

    def _source_descriptors(self) -> list[EnergySourceDescriptor]:
        return [
            *self._grid_descriptors(),
            *self._pv_descriptors(),
            *self._battery_descriptors(),
        ]

    def _grid_descriptors(self) -> list[EnergySourceDescriptor]:
        spec = self._specs.get("grid_power_w", {})
        service = _spec_text(spec, "service")
        if not service:
            return []
        return [
            EnergySourceDescriptor(
                source_id="grid-primary",
                kind="grid",
                state=self._service_state(service),
                capabilities=("power",),
            )
        ]

    def _pv_descriptors(self) -> list[EnergySourceDescriptor]:
        spec = self._specs.get("pv_power_w", {})
        descriptors = [
            EnergySourceDescriptor(
                source_id=_opaque_source_id("pv-ac", service),
                kind="pv_ac",
                state=self._service_state(service),
                capabilities=("power",),
            )
            for service in self.services_for(spec)
        ]
        dc_service = _spec_text(spec, "dc_service")
        dc_path = _spec_text(spec, "dc_path")
        if _spec_bool(spec, "use_dc_pv") and dc_service and dc_path:
            descriptors.append(
                EnergySourceDescriptor(
                    source_id=_opaque_source_id("pv-dc", dc_service),
                    kind="pv_dc",
                    state=self._service_state(dc_service),
                    capabilities=("power",),
                )
            )
        return descriptors

    def _battery_descriptors(self) -> list[EnergySourceDescriptor]:
        spec = self._specs.get("battery_soc", {})
        return [
            EnergySourceDescriptor(
                source_id=_opaque_source_id("battery", service),
                kind="battery",
                state=self._service_state(service),
                capabilities=("soc",),
            )
            for service in self.services_for(spec)
        ]

    def _service_state(self, service: str) -> EnergySourceState:
        if not self._service_names:
            return "unknown"
        return "online" if service in self._service_names else "offline"

    def _grid_introspection_targets(self) -> list[IntrospectionTarget]:
        spec = self._specs.get("grid_power_w", {})
        service = _spec_text(spec, "service")
        paths = spec.get("paths", [])
        return [
            IntrospectionTarget(service, str(path), 80, "grid", "configured-grid-field")
            for path in paths
            if service and str(path)
        ]

    def _battery_introspection_targets(self) -> list[IntrospectionTarget]:
        spec = self._specs.get("battery_soc", {})
        path = _spec_text(spec, "path")
        return [
            IntrospectionTarget(service, path, 70, "battery", "discovered-battery-field")
            for service in self.services_for(spec)
            if path
        ]

    def _pv_introspection_targets(self) -> list[IntrospectionTarget]:
        spec = self._specs.get("pv_power_w", {})
        path = _spec_text(spec, "path")
        targets = [
            IntrospectionTarget(service, path, 30, "pv", "discovered-ac-pv-field")
            for service in self.services_for(spec)
            if path
        ]
        dc_service = _spec_text(spec, "dc_service")
        dc_path = _spec_text(spec, "dc_path")
        dc_target = _dc_pv_introspection_target(_spec_bool(spec, "use_dc_pv"), dc_service, dc_path)
        if dc_target is not None:
            targets.append(dc_target)
        return targets


def _dc_pv_introspection_target(enabled: bool, service: str, path: str) -> IntrospectionTarget | None:
    if not enabled:
        return None
    if not service or not path:
        return None
    return IntrospectionTarget(service, path, 30, "pv", "configured-dc-pv-field")


def _opaque_source_id(kind: str, service: str) -> str:
    digest = hashlib.sha256(service.encode()).hexdigest()[:10]
    return f"{kind}-{digest}"


def _spec_text(spec: ReadSpec, key: str) -> str:
    value = spec.get(key)
    return value.strip() if isinstance(value, str) else ""


def _spec_bool(spec: ReadSpec, key: str) -> bool:
    value = spec.get(key)
    return value if isinstance(value, bool) else False


def _read_keys_for_kind(kind: EnergySourceKind) -> tuple[str, ...]:
    return {
        "grid": ("grid_power_w",),
        "pv_ac": ("pv_power_w",),
        "pv_dc": ("pv_power_w",),
        "battery": ("battery_soc",),
    }[kind]

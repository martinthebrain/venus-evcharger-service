# SPDX-License-Identifier: GPL-3.0-or-later
"""Adapter-owned DBus energy-source discovery and binding decisions."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from venus_evcharger.dbus_adapter.read.pv import dc_pv_target, use_dc_pv
from venus_evcharger.dbus_adapter.read.pv_discovery import (
    PvSourceRegistry,
    PvSourceUnavailabilityReason,
    opaque_energy_source_id,
)
from venus_evcharger.dbus_adapter.read.pv_dormancy import (
    DEFAULT_MAX_OBSERVATIONS,
    PvDormancyEvidence,
)
from venus_evcharger.dbus_adapter.read.spec import (
    ReadSpec,
    ReadSpecs,
    read_spec_text,
)
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
    """Own service selection while publishing only validated source identities."""

    def __init__(
        self,
        specs: ReadSpecs,
        *,
        max_prefix_services: int = 10,
        max_pv_observations: int = DEFAULT_MAX_OBSERVATIONS,
        monotonic: Callable[[], float] | None = None,
        wall_clock: Callable[[], float] | None = None,
    ) -> None:
        self._specs = specs
        self._max_prefix_services = max(1, int(max_prefix_services))
        self._service_names: tuple[str, ...] = ()
        self._generation = 0
        self._captured_at = 0.0
        self._pv_sources = PvSourceRegistry(
            specs.get("pv_power_w", {}),
            max_observations=max_pv_observations,
            monotonic=monotonic,
            wall_clock=wall_clock,
        )
        self._pv_revision = self._pv_sources.revision

    @property
    def generation(self) -> int:
        return self._generation

    @property
    def pv_observation_count(self) -> int:
        return self._pv_sources.observation_count

    def update_services(
        self,
        names: Sequence[str],
        *,
        captured_at: float,
    ) -> None:
        normalized = tuple(sorted({str(name).strip() for name in names if str(name).strip()}))
        if normalized != self._service_names:
            self._service_names = normalized
            self._generation += 1
        self._captured_at = max(0.0, float(captured_at))
        self._pv_sources.maintain(self._advertising_services())
        self._sync_pv_revision()

    def services_for(self, spec: ReadSpec) -> list[str]:
        explicit = read_spec_text(spec, "service")
        if explicit:
            return [explicit]
        prefix = read_spec_text(spec, "prefix")
        if not prefix:
            return []
        return [name for name in self._service_names if name.startswith(prefix)][: self._max_prefix_services]

    def first_service(self, spec: ReadSpec) -> str | None:
        services = self.services_for(spec)
        return services[0] if services else None

    def pv_members(self, spec: ReadSpec) -> list[tuple[str, str]]:
        result = self._pv_sources.members(
            self.services_for(spec),
            self._advertising_services(),
        )
        self._sync_pv_revision()
        return result

    def pv_candidates(self, spec: ReadSpec) -> list[tuple[str, str]]:
        """Return advertised PV targets even while their probes back off."""
        result = self._pv_sources.candidates(
            self.services_for(spec),
            self._advertising_services(),
        )
        self._sync_pv_revision()
        return result

    def record_pv_value(
        self,
        service: str,
        path: str,
        value: object,
    ) -> None:
        self._pv_sources.record_value(
            service,
            path,
            value,
            advertising_services=self._advertising_services(),
        )
        self._sync_pv_revision()

    def record_pv_error(
        self,
        service: str,
        path: str,
        error: BaseException | str,
    ) -> None:
        self._pv_sources.record_error(
            service,
            path,
            error,
            advertising_services=self._advertising_services(),
        )
        self._sync_pv_revision()

    def dormant_evidence(self) -> tuple[PvDormancyEvidence, ...]:
        evidence = self._pv_sources.dormant_evidence(self._advertising_services())
        self._sync_pv_revision()
        return evidence

    def dormant_source_ids(self) -> tuple[str, ...]:
        return tuple(item.source_id for item in self.dormant_evidence())

    def source_unavailability_reasons(
        self,
        *,
        dormant_source_ids: frozenset[str] | None = None,
    ) -> dict[str, PvSourceUnavailabilityReason]:
        dormant = frozenset(self.dormant_source_ids()) if dormant_source_ids is None else dormant_source_ids
        result = self._pv_sources.unavailability_reasons(
            self._advertising_services(),
            dormant_source_ids=dormant,
        )
        self._sync_pv_revision()
        return result

    def needs_early_pv_rescan(self) -> bool:
        result = self._pv_sources.needs_early_rescan(self._advertising_services())
        self._sync_pv_revision()
        return result

    def topology_snapshot(
        self,
        *,
        captured_at: float,
    ) -> EnergyTopologySnapshot:
        dormant_source_ids = frozenset(self.dormant_source_ids())
        sources = self._source_descriptors(dormant_source_ids)
        return EnergyTopologySnapshot(
            generation=self._generation,
            captured_at=max(float(captured_at), self._captured_at),
            sources=tuple(sources),
        )

    def source_ids(self, kind: str) -> tuple[str, ...]:
        return tuple(source.source_id for source in self._source_descriptors() if source.kind == kind)

    def read_keys_for_source(self, source_id: str) -> tuple[str, ...]:
        for source in self._source_descriptors():
            if source.source_id == source_id:
                return self._read_keys_for_kind(source.kind)
        return ()

    def introspection_targets(self) -> list[IntrospectionTarget]:
        return [
            *self._grid_introspection_targets(),
            *self._battery_introspection_targets(),
            *self._pv_introspection_targets(),
        ]

    def _source_descriptors(
        self,
        dormant_source_ids: frozenset[str] = frozenset(),
    ) -> list[EnergySourceDescriptor]:
        pv_descriptors = self._pv_sources.descriptors(
            self._advertising_services(),
            dormant_source_ids=dormant_source_ids,
        )
        self._sync_pv_revision()
        return [
            *self._grid_descriptors(),
            *pv_descriptors,
            *self._battery_descriptors(),
        ]

    def _grid_descriptors(self) -> list[EnergySourceDescriptor]:
        spec = self._specs.get("grid_power_w", {})
        service = read_spec_text(spec, "service")
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

    def _battery_descriptors(self) -> list[EnergySourceDescriptor]:
        spec = self._specs.get("battery_soc", {})
        capabilities = tuple(
            capability
            for capability, key in (
                ("soc", "battery_soc"),
                ("net_power", "battery_net_power_w"),
                ("capacity_wh", "battery_capacity_wh"),
                ("capacity_ah", "battery_capacity_ah"),
                ("voltage", "battery_voltage_v"),
            )
            if key in self._specs
        )
        return [
            EnergySourceDescriptor(
                source_id=opaque_energy_source_id("battery", service),
                kind="battery",
                state=self._service_state(service),
                capabilities=capabilities,
            )
            for service in self.services_for(spec)
        ]

    def _read_keys_for_kind(self, kind: EnergySourceKind) -> tuple[str, ...]:
        if kind == "battery":
            return tuple(
                key
                for key in (
                    "battery_soc",
                    "battery_net_power_w",
                    "battery_capacity_wh",
                    "battery_capacity_ah",
                    "battery_voltage_v",
                )
                if key in self._specs
            )
        return _read_keys_for_kind(kind)

    def _service_state(self, service: str) -> EnergySourceState:
        if not self._service_names:
            return "unknown"
        return "online" if service in self._service_names else "offline"

    def _grid_introspection_targets(self) -> list[IntrospectionTarget]:
        spec = self._specs.get("grid_power_w", {})
        service = read_spec_text(spec, "service")
        paths = spec.get("paths", [])
        return [
            IntrospectionTarget(
                service,
                str(path),
                80,
                "grid",
                "configured-grid-field",
            )
            for path in paths
            if service and str(path)
        ]

    def _battery_introspection_targets(self) -> list[IntrospectionTarget]:
        spec = self._specs.get("battery_soc", {})
        targets = self._targets_for_services(
            spec,
            priority=70,
            reason="discovered-battery-field",
        )
        targets.extend(
            self._targets_for_services(
                self._specs.get("battery_net_power_w", {}),
                priority=70,
                reason="configured-battery-power-field",
            )
        )
        for key, reason in (
            ("battery_capacity_wh", "configured-battery-capacity-wh-field"),
            ("battery_capacity_ah", "configured-battery-capacity-ah-field"),
            ("battery_voltage_v", "configured-battery-voltage-field"),
        ):
            targets.extend(
                self._targets_for_services(
                    self._specs.get(key, {}),
                    priority=95,
                    reason=reason,
                )
            )
        return targets

    def _targets_for_services(
        self,
        spec: ReadSpec,
        *,
        priority: int,
        reason: str,
    ) -> list[IntrospectionTarget]:
        """Build private targets for every service selected by one read spec."""
        path = read_spec_text(spec, "path")
        if not path:
            return []
        return [
            IntrospectionTarget(
                service,
                path,
                priority,
                "battery",
                reason,
            )
            for service in self.services_for(spec)
        ]

    def _pv_introspection_targets(self) -> list[IntrospectionTarget]:
        spec = self._specs.get("pv_power_w", {})
        return [
            *_ac_pv_introspection_targets(
                spec,
                self.services_for(spec),
                frozenset(self._service_names),
            ),
            *_advertised_dc_pv_introspection_targets(
                spec,
                frozenset(self._service_names),
            ),
        ]

    def _advertising_services(self) -> frozenset[str]:
        return frozenset(self._service_names)

    def _sync_pv_revision(self) -> None:
        revision = self._pv_sources.revision
        if revision != self._pv_revision:
            self._pv_revision = revision
            self._generation += 1


def _dc_pv_introspection_target(
    spec: ReadSpec,
) -> IntrospectionTarget | None:
    target = dc_pv_target(spec) if use_dc_pv(spec) else None
    if target is None:
        return None
    service, path = target
    return IntrospectionTarget(
        service,
        path,
        30,
        "pv",
        "configured-dc-pv-field",
    )


def _ac_pv_introspection_targets(
    spec: ReadSpec,
    services: Sequence[str],
    advertising_services: frozenset[str],
) -> list[IntrospectionTarget]:
    path = read_spec_text(spec, "path")
    if not path:
        return []
    return [
        IntrospectionTarget(
            service,
            path,
            30,
            "pv",
            "discovered-ac-pv-field",
        )
        for service in services
        if service in advertising_services
    ]


def _advertised_dc_pv_introspection_targets(
    spec: ReadSpec,
    advertising_services: frozenset[str],
) -> list[IntrospectionTarget]:
    target = _dc_pv_introspection_target(spec)
    if target is None or target.service not in advertising_services:
        return []
    return [target]


def _read_keys_for_kind(kind: EnergySourceKind) -> tuple[str, ...]:
    return {
        "grid": ("grid_power_w",),
        "pv_ac": ("pv_power_w",),
        "pv_dc": ("pv_power_w",),
        "battery": ("battery_soc",),
    }[kind]

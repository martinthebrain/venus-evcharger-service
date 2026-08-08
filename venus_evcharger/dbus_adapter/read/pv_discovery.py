# SPDX-License-Identifier: GPL-3.0-or-later
"""Validated PV-source registry owned by adapter discovery."""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Literal

from venus_evcharger.dbus_adapter.read.pv import dc_pv_target, pv_total_members, use_dc_pv
from venus_evcharger.dbus_adapter.read.pv_dormancy import (
    DEFAULT_MAX_OBSERVATIONS,
    PvDormancyEvidence,
    PvDormancyPolicy,
    PvDormancyTracker,
)
from venus_evcharger.dbus_adapter.read.spec import ReadSpec, read_spec_text
from venus_evcharger.ipc.energy import EnergySourceDescriptor, EnergySourceState

PvSourceKind = Literal["pv_ac", "pv_dc"]
PvSourceUnavailabilityReason = Literal[
    "pv-sleep-confirmed",
    "source-not-advertising",
    "source-path-unreadable",
]


@dataclass(frozen=True, slots=True)
class _PvTarget:
    source_id: str
    kind: PvSourceKind
    service: str
    path: str


class PvSourceRegistry:
    """Separate PV candidates from bounded, path-validated source state."""

    def __init__(
        self,
        spec: ReadSpec,
        *,
        max_observations: int = DEFAULT_MAX_OBSERVATIONS,
        monotonic: Callable[[], float] | None = None,
        wall_clock: Callable[[], float] | None = None,
    ) -> None:
        self._spec = spec
        self._tracker = PvDormancyTracker(
            policy=PvDormancyPolicy(max_observations=max_observations),
            monotonic=monotonic,
            wall_clock=wall_clock,
        )
        self._validated_targets: dict[str, _PvTarget] = {}
        self._revision = 0

    @property
    def observation_count(self) -> int:
        return self._tracker.observation_count

    @property
    def revision(self) -> int:
        return self._revision

    def members(
        self,
        ac_services: Sequence[str],
        advertising_services: frozenset[str],
    ) -> list[tuple[str, str]]:
        candidates = self._candidate_targets(ac_services, advertising_services)
        self._maintain_source_ids(frozenset(target.source_id for target in candidates))
        return [(target.service, target.path) for target in candidates if self._tracker.probe_allowed(target.source_id)]

    def record_value(
        self,
        service: str,
        path: str,
        value: object,
        *,
        advertising_services: frozenset[str],
    ) -> None:
        target = self._target_for(service, path, advertising_services)
        if target is None:
            return
        changed = self._tracker.record_value(
            target.source_id,
            value,
            active_source_ids=self._active_source_ids(advertising_services),
        )
        if self._tracker.source_validated(target.source_id):
            changed = self._remember_target(target) or changed
        self._record_change(changed)

    def record_error(
        self,
        service: str,
        path: str,
        error: BaseException | str,
        *,
        advertising_services: frozenset[str],
    ) -> None:
        target = self._target_for(service, path, advertising_services)
        if target is None:
            return
        changed = self._tracker.record_error(
            target.source_id,
            error,
            active_source_ids=self._active_source_ids(advertising_services),
        )
        if self._tracker.source_validated(target.source_id):
            changed = self._remember_target(target) or changed
        self._record_change(changed)

    def maintain(
        self,
        advertising_services: frozenset[str],
    ) -> None:
        self._maintain_source_ids(self._active_source_ids(advertising_services))

    def _maintain_source_ids(self, active_ids: frozenset[str]) -> None:
        changed = self._tracker.maintain(active_ids)
        retained = self._tracker.validated_source_ids()
        stale_ids = set(self._validated_targets).difference(retained)
        for source_id in stale_ids:
            del self._validated_targets[source_id]
        self._record_change(changed)

    def dormant_evidence(
        self,
        advertising_services: frozenset[str],
    ) -> tuple[PvDormancyEvidence, ...]:
        self.maintain(advertising_services)
        return self._tracker.evidence(frozenset(self._validated_targets))

    def unavailability_reasons(
        self,
        advertising_services: frozenset[str],
        *,
        dormant_source_ids: frozenset[str],
    ) -> dict[str, PvSourceUnavailabilityReason]:
        self.maintain(advertising_services)
        return {
            target.source_id: reason
            for target in self._ordered_targets()
            if (
                reason := self._unavailability_reason(
                    target,
                    advertising_services,
                    dormant_source_ids,
                )
            )
            is not None
        }

    def descriptors(
        self,
        advertising_services: frozenset[str],
        *,
        dormant_source_ids: frozenset[str],
    ) -> list[EnergySourceDescriptor]:
        self.maintain(advertising_services)
        return [
            EnergySourceDescriptor(
                source_id=target.source_id,
                kind=target.kind,
                state=self._source_state(
                    target,
                    advertising_services,
                    dormant_source_ids,
                ),
                capabilities=("power",),
            )
            for target in self._ordered_targets()
        ]

    def needs_early_rescan(
        self,
        advertising_services: frozenset[str],
    ) -> bool:
        self.maintain(advertising_services)
        return not any(target.service in advertising_services for target in self._validated_targets.values())

    def _candidate_targets(
        self,
        ac_services: Sequence[str],
        advertising_services: frozenset[str],
    ) -> tuple[_PvTarget, ...]:
        advertised_ac = [service for service in ac_services if service in advertising_services]
        raw_targets = pv_total_members(self._spec, advertised_ac)
        configured_dc = _configured_dc_target(self._spec)
        return tuple(
            self._configured_member_target(service, path, configured_dc)
            for service, path in raw_targets
            if service in advertising_services
        )

    def _active_source_ids(
        self,
        advertising_services: frozenset[str],
    ) -> frozenset[str]:
        return frozenset(
            target.source_id
            for target in self._candidate_targets(
                _ac_services(self._spec, advertising_services),
                advertising_services,
            )
        )

    def _target_for(
        self,
        service: str,
        path: str,
        advertising_services: frozenset[str],
    ) -> _PvTarget | None:
        return next(
            (
                target
                for target in self._candidate_targets(
                    _ac_services(self._spec, advertising_services),
                    advertising_services,
                )
                if (target.service, target.path) == (service, path)
            ),
            None,
        )

    @staticmethod
    def _configured_member_target(
        service: str,
        path: str,
        configured_dc: tuple[str, str] | None,
    ) -> _PvTarget:
        if configured_dc == (service, path):
            return _PvTarget(
                opaque_energy_source_id("pv-dc", service),
                "pv_dc",
                service,
                path,
            )
        return _PvTarget(
            opaque_energy_source_id("pv-ac", service),
            "pv_ac",
            service,
            path,
        )

    def _source_state(
        self,
        target: _PvTarget,
        advertising_services: frozenset[str],
        dormant_source_ids: frozenset[str],
    ) -> EnergySourceState:
        if (
            target.source_id in dormant_source_ids
            or target.service not in advertising_services
            or self._tracker.source_available(target.source_id) is False
        ):
            return "offline"
        return "online"

    def _unavailability_reason(
        self,
        target: _PvTarget,
        advertising_services: frozenset[str],
        dormant_source_ids: frozenset[str],
    ) -> PvSourceUnavailabilityReason | None:
        if target.source_id in dormant_source_ids:
            return "pv-sleep-confirmed"
        if target.service not in advertising_services:
            return "source-not-advertising"
        if self._tracker.source_available(target.source_id) is False:
            return "source-path-unreadable"
        return None

    def _remember_target(self, target: _PvTarget) -> bool:
        previous = self._validated_targets.get(target.source_id)
        self._validated_targets[target.source_id] = target
        return previous != target

    def _ordered_targets(self) -> tuple[_PvTarget, ...]:
        return tuple(
            sorted(
                self._validated_targets.values(),
                key=lambda target: (target.kind, target.service, target.path),
            )
        )

    def _record_change(self, changed: bool) -> None:
        if changed:
            self._revision += 1


def opaque_energy_source_id(kind: str, service: str) -> str:
    digest = hashlib.sha256(service.encode()).hexdigest()[:10]
    return f"{kind}-{digest}"


def _configured_dc_target(spec: ReadSpec) -> tuple[str, str] | None:
    return dc_pv_target(spec) if use_dc_pv(spec) else None


def _ac_services(
    spec: ReadSpec,
    advertising_services: frozenset[str],
) -> tuple[str, ...]:
    explicit = read_spec_text(spec, "service")
    if explicit:
        return (explicit,) if explicit in advertising_services else ()
    prefix = read_spec_text(spec, "prefix")
    return _prefixed_services(prefix, advertising_services)


def _prefixed_services(
    prefix: str,
    advertising_services: frozenset[str],
) -> tuple[str, ...]:
    if not prefix:
        return ()
    return tuple(service for service in sorted(advertising_services) if service.startswith(prefix))


__all__ = [
    "PvSourceRegistry",
    "PvSourceUnavailabilityReason",
    "opaque_energy_source_id",
]

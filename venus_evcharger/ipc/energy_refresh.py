# SPDX-License-Identifier: GPL-3.0-or-later
"""Semantic refresh requests for gateway-owned energy inputs."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from venus_evcharger.ipc.energy_types import (
    ENERGY_IPC_SCHEMA_VERSION,
    ENERGY_REFRESH_COMMAND_KIND,
    EnergyRefreshScope,
    EnergyRefreshUrgency,
)
from venus_evcharger.ipc.energy_validation import (
    mapping,
    non_negative_float,
    optional_text,
    refresh_scope,
    refresh_urgency,
    schema_version,
    text,
)


@dataclass(frozen=True, slots=True)
class _EnergyRefreshFields:
    request_id: str
    scope: EnergyRefreshScope
    source_id: str | None
    max_age_seconds: float
    urgency: EnergyRefreshUrgency
    reason: str


@dataclass(frozen=True, slots=True)
class EnergyRefreshRequest:
    """Semantic request; the adapter chooses DBus discovery and read work."""

    request_id: str
    scope: EnergyRefreshScope
    max_age_seconds: float
    urgency: EnergyRefreshUrgency = "normal"
    source_id: str | None = None
    reason: str = ""
    schema_version: int = ENERGY_IPC_SCHEMA_VERSION

    def __post_init__(self) -> None:
        schema_version(self.schema_version, "energy refresh request")
        text(self.request_id, "energy refresh request_id")
        scope = refresh_scope(self.scope)
        non_negative_float(self.max_age_seconds, "energy refresh max_age_seconds")
        refresh_urgency(self.urgency)
        source_id = optional_text(self.source_id, "energy refresh source_id")
        text(self.reason, "energy refresh reason", allow_empty=True)
        _validate_refresh_source(scope, source_id)

    def to_command(self, *, source: str) -> dict[str, object]:
        producer = text(source, "energy refresh producer")
        return {
            "schema_version": self.schema_version,
            "kind": ENERGY_REFRESH_COMMAND_KIND,
            "request_id": self.request_id,
            "scope": self.scope,
            "source_id": self.source_id,
            "max_age_seconds": self.max_age_seconds,
            "urgency": self.urgency,
            "reason": self.reason,
            "source": producer,
            "priority": "read" if self.urgency == "priority" else "discovery",
            "coalesce_key": f"energy-refresh:{self.scope}:{self.source_id or 'all'}",
        }

    @classmethod
    def from_command(cls, payload: object) -> EnergyRefreshRequest:
        fields = _parse_energy_refresh_fields(payload)
        return cls(
            request_id=fields.request_id,
            scope=fields.scope,
            source_id=fields.source_id,
            max_age_seconds=fields.max_age_seconds,
            urgency=fields.urgency,
            reason=fields.reason,
        )


def _parse_energy_refresh_fields(payload: object) -> _EnergyRefreshFields:
    item = mapping(payload, "energy refresh request")
    _reject_adapter_targets(item)
    _validate_refresh_header(item)
    scope = refresh_scope(item.get("scope"))
    source_id = optional_text(item.get("source_id"), "energy refresh source_id")
    _validate_refresh_source(scope, source_id)
    return _EnergyRefreshFields(
        request_id=text(item.get("request_id"), "energy refresh request_id"),
        scope=scope,
        source_id=source_id,
        max_age_seconds=non_negative_float(item.get("max_age_seconds"), "energy refresh max_age_seconds"),
        urgency=refresh_urgency(item.get("urgency")),
        reason=text(item.get("reason"), "energy refresh reason", allow_empty=True),
    )


def _reject_adapter_targets(item: Mapping[str, object]) -> None:
    if {"service", "path", "key"} & item.keys():
        raise ValueError("energy refresh request must not expose adapter targets")


def _validate_refresh_header(item: Mapping[str, object]) -> None:
    if item.get("kind") != ENERGY_REFRESH_COMMAND_KIND:
        raise ValueError("energy refresh request has an invalid kind")
    if item.get("schema_version") != ENERGY_IPC_SCHEMA_VERSION:
        raise ValueError("energy refresh request has an unsupported schema_version")


def _validate_refresh_source(scope: EnergyRefreshScope, source_id: str | None) -> None:
    if scope == "energy_source" and source_id is None:
        raise ValueError("energy_source refresh requires source_id")
    if scope != "energy_source" and source_id is not None:
        raise ValueError("source_id is only valid for energy_source refresh")


__all__ = ["EnergyRefreshRequest"]

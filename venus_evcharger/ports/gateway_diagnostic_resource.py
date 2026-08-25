# SPDX-License-Identifier: GPL-3.0-or-later
"""Resource-pressure portion of the semantic gateway diagnostics contract."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, TypeGuard

from venus_evcharger.ports.gateway_diagnostics_validation import (
    boolean,
    exact_mapping,
    non_negative_float,
)

GatewayResourceState = Literal["unknown", "ok", "busy", "constrained"]
_RESOURCE_STATES = frozenset({"unknown", "ok", "busy", "constrained"})
_RESOURCE_CAUSES = frozenset({"load", "cpu", "memory"})


@dataclass(frozen=True, slots=True)
class ResourcePressureSummary:
    """Bounded measurements retained from a constrained resource transition."""

    active: bool
    triggered_at: float
    causes: tuple[str, ...]
    load_per_cpu_1m: float | None
    system_cpu_pct: float | None
    mem_available_kb: float | None

    def __post_init__(self) -> None:
        _validate_identity(self)
        _validate_metrics(self)

    def to_payload(self) -> dict[str, object]:
        """Return the strict transport representation."""
        return {
            "active": self.active,
            "triggered_at": self.triggered_at,
            "causes": list(self.causes),
            "load_per_cpu_1m": self.load_per_cpu_1m,
            "system_cpu_pct": self.system_cpu_pct,
            "mem_available_kb": self.mem_available_kb,
        }

    @classmethod
    def from_payload(cls, payload: object) -> ResourcePressureSummary:
        """Validate and decode the strict transport representation."""
        item = exact_mapping(
            payload,
            "gateway resource evidence",
            {
                "active",
                "triggered_at",
                "causes",
                "load_per_cpu_1m",
                "system_cpu_pct",
                "mem_available_kb",
            },
        )
        causes = item["causes"]
        if not isinstance(causes, list) or not all(isinstance(cause, str) for cause in causes):
            raise TypeError("gateway resource evidence causes must be a text list")
        return cls(
            active=boolean(item["active"], "gateway resource evidence active"),
            triggered_at=non_negative_float(
                item["triggered_at"],
                "gateway resource evidence triggered_at",
            ),
            causes=tuple(causes),
            load_per_cpu_1m=optional_metric(item["load_per_cpu_1m"], "load_per_cpu_1m"),
            system_cpu_pct=optional_metric(item["system_cpu_pct"], "system_cpu_pct"),
            mem_available_kb=optional_metric(item["mem_available_kb"], "mem_available_kb"),
        )


def resource_state(value: object) -> GatewayResourceState:
    """Validate one public resource-state value."""
    if not _is_resource_state(value):
        raise ValueError("gateway resource state is invalid")
    return value


def optional_metric(value: object, name: str) -> float | None:
    """Validate one optional non-negative resource metric."""
    return None if value is None else non_negative_float(value, f"gateway resource evidence {name}")


def _validate_identity(value: ResourcePressureSummary) -> None:
    boolean(value.active, "gateway resource evidence active")
    _validate_time(value.triggered_at)
    _validate_causes(value.causes)


def _validate_time(triggered_at: float) -> None:
    if (
        non_negative_float(
            triggered_at,
            "gateway resource evidence triggered_at",
        )
        <= 0.0
    ):
        raise ValueError("gateway resource evidence requires positive triggered_at")


def _validate_causes(causes: tuple[str, ...]) -> None:
    if not causes or len(causes) != len(set(causes)):
        raise ValueError("gateway resource evidence requires unique causes")
    if any(cause not in _RESOURCE_CAUSES for cause in causes):
        raise ValueError("gateway resource evidence cause is invalid")


def _validate_metrics(value: ResourcePressureSummary) -> None:
    metrics = {
        "load": optional_metric(value.load_per_cpu_1m, "load_per_cpu_1m"),
        "cpu": optional_metric(value.system_cpu_pct, "system_cpu_pct"),
        "memory": optional_metric(value.mem_available_kb, "mem_available_kb"),
    }
    if any(metrics[cause] is None for cause in value.causes):
        raise ValueError("gateway resource evidence cause requires its metric")


def _is_resource_state(value: object) -> TypeGuard[GatewayResourceState]:
    return isinstance(value, str) and value in _RESOURCE_STATES


__all__ = ["GatewayResourceState", "ResourcePressureSummary", "resource_state"]

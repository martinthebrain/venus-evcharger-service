# SPDX-License-Identifier: GPL-3.0-or-later
"""Topology and input snapshot contracts for semantic energy IPC."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from venus_evcharger.ipc.energy_types import (
    ENERGY_IPC_SCHEMA_VERSION,
    EnergySourceKind,
    EnergySourceState,
)
from venus_evcharger.ipc.energy_validation import (
    exact_fields,
    mapping,
    non_negative_int,
    positive_float,
    schema_version,
    source_kind,
    source_state,
    text,
    text_tuple,
    unique_text_tuple,
)
from venus_evcharger.ipc.energy_values import MeasuredValue


@dataclass(frozen=True, slots=True)
class EnergySourceDescriptor:
    """Opaque source identity and capabilities discovered by the gateway."""

    source_id: str
    kind: EnergySourceKind
    state: EnergySourceState
    capabilities: tuple[str, ...]

    def __post_init__(self) -> None:
        text(self.source_id, "energy source_id")
        source_kind(self.kind)
        source_state(self.state)
        unique_text_tuple(self.capabilities, "energy source capabilities")

    def to_payload(self) -> dict[str, object]:
        return {
            "source_id": self.source_id,
            "kind": self.kind,
            "state": self.state,
            "capabilities": list(self.capabilities),
        }

    @classmethod
    def from_payload(cls, payload: object) -> EnergySourceDescriptor:
        item = mapping(payload, "energy source descriptor")
        exact_fields(item, required={"source_id", "kind", "state", "capabilities"}, label="energy source descriptor")
        return cls(
            source_id=text(item["source_id"], "energy source_id"),
            kind=source_kind(item["kind"]),
            state=source_state(item["state"]),
            capabilities=text_tuple(item["capabilities"], "energy source capabilities"),
        )


@dataclass(frozen=True, slots=True)
class EnergyTopologySnapshot:
    """Semantic inventory published after gateway-owned auto-discovery."""

    generation: int
    captured_at: float
    sources: tuple[EnergySourceDescriptor, ...]
    schema_version: int = ENERGY_IPC_SCHEMA_VERSION

    def __post_init__(self) -> None:
        schema_version(self.schema_version, "energy topology")
        non_negative_int(self.generation, "energy topology generation")
        positive_float(self.captured_at, "energy topology captured_at")
        _unique_descriptors(self.sources)

    def to_payload(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "generation": self.generation,
            "captured_at": self.captured_at,
            "sources": [source.to_payload() for source in self.sources],
        }

    @classmethod
    def from_payload(cls, payload: object) -> EnergyTopologySnapshot:
        item = _snapshot_mapping(payload, "energy topology")
        return cls(
            generation=non_negative_int(item["generation"], "energy topology generation"),
            captured_at=positive_float(item["captured_at"], "energy topology captured_at"),
            sources=_descriptor_tuple(item["sources"]),
        )


@dataclass(frozen=True, slots=True)
class EnergyInputsSnapshot:
    """Coherent energy inputs consumed by helper and core logic."""

    sequence: int
    captured_at: float
    topology_generation: int
    grid_power_w: MeasuredValue
    pv_power_w: MeasuredValue
    battery_soc: MeasuredValue
    schema_version: int = ENERGY_IPC_SCHEMA_VERSION

    def __post_init__(self) -> None:
        schema_version(self.schema_version, "energy inputs")
        non_negative_int(self.sequence, "energy inputs sequence")
        positive_float(self.captured_at, "energy inputs captured_at")
        non_negative_int(self.topology_generation, "energy inputs topology_generation")
        for name, measurement in (
            ("grid_power_w", self.grid_power_w),
            ("pv_power_w", self.pv_power_w),
            ("battery_soc", self.battery_soc),
        ):
            if not isinstance(measurement, MeasuredValue):
                raise TypeError(f"energy inputs {name} must be a MeasuredValue")

    def to_payload(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "sequence": self.sequence,
            "captured_at": self.captured_at,
            "topology_generation": self.topology_generation,
            "grid_power_w": self.grid_power_w.to_payload(),
            "pv_power_w": self.pv_power_w.to_payload(),
            "battery_soc": self.battery_soc.to_payload(),
        }

    @classmethod
    def from_payload(cls, payload: object) -> EnergyInputsSnapshot:
        item = _snapshot_mapping(payload, "energy inputs")
        return cls(
            sequence=non_negative_int(item["sequence"], "energy inputs sequence"),
            captured_at=positive_float(item["captured_at"], "energy inputs captured_at"),
            topology_generation=non_negative_int(
                item["topology_generation"],
                "energy inputs topology_generation",
            ),
            grid_power_w=MeasuredValue.from_payload(item["grid_power_w"]),
            pv_power_w=MeasuredValue.from_payload(item["pv_power_w"]),
            battery_soc=MeasuredValue.from_payload(item["battery_soc"]),
        )


def _snapshot_mapping(payload: object, label: str) -> Mapping[str, object]:
    item = mapping(payload, label)
    if item.get("schema_version") != ENERGY_IPC_SCHEMA_VERSION:
        raise ValueError(f"{label} has an unsupported schema_version")
    required = (
        {"schema_version", "generation", "captured_at", "sources"}
        if label == "energy topology"
        else {
            "schema_version",
            "sequence",
            "captured_at",
            "topology_generation",
            "grid_power_w",
            "pv_power_w",
            "battery_soc",
        }
    )
    exact_fields(item, required=required, label=label)
    return item


def _descriptor_tuple(value: object) -> tuple[EnergySourceDescriptor, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise TypeError("energy topology sources must be a sequence")
    descriptors = tuple(EnergySourceDescriptor.from_payload(item) for item in value)
    return _unique_descriptors(descriptors)


def _unique_descriptors(value: object) -> tuple[EnergySourceDescriptor, ...]:
    descriptors = _descriptor_values(value)
    source_ids = tuple(item.source_id for item in descriptors)
    if len(source_ids) != len(set(source_ids)):
        raise ValueError("energy topology source_ids must be unique")
    return descriptors


def _descriptor_values(value: object) -> tuple[EnergySourceDescriptor, ...]:
    if not isinstance(value, tuple) or not all(isinstance(item, EnergySourceDescriptor) for item in value):
        raise TypeError("energy topology sources must be EnergySourceDescriptor values")
    return value


__all__ = ["EnergyInputsSnapshot", "EnergySourceDescriptor", "EnergyTopologySnapshot"]

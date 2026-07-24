# SPDX-License-Identifier: GPL-3.0-or-later
"""Atomic file output for DBus gateway cache snapshots."""

from __future__ import annotations

import os
from collections.abc import Mapping

from venus_evcharger.core.shared import write_text_atomically
from venus_evcharger.dbus_gateway_core import (
    DBUS_GATEWAY_SCHEMA_VERSION,
    GatewayPaths,
    write_json_file,
)
from venus_evcharger.ipc.command_types import CommandPayload
from venus_evcharger.ipc.energy import EnergyInputsSnapshot, EnergyTopologySnapshot
from venus_evcharger.ipc.energy_binary import write_energy_inputs_file

__all__ = [
    "write_cache_snapshot",
    "write_energy_inputs_snapshot",
    "write_energy_topology_snapshot",
    "write_health_snapshot",
]


def write_cache_snapshot(
    paths: GatewayPaths,
    snapshot: CommandPayload,
    sequence: int,
) -> None:
    """Atomically publish the canonical cache and its sequence marker."""
    os.makedirs(paths.run_dir, exist_ok=True)
    write_json_file(paths.cache_path, snapshot)
    write_text_atomically(paths.cache_sequence_path, f"{sequence}\n")


def write_health_snapshot(
    paths: GatewayPaths,
    *,
    sequence: int,
    captured_at: float,
    health: Mapping[str, object],
) -> None:
    """Atomically publish the lightweight gateway health snapshot."""
    os.makedirs(paths.run_dir, exist_ok=True)
    write_json_file(
        paths.health_path,
        {
            "schema_version": DBUS_GATEWAY_SCHEMA_VERSION,
            "sequence": sequence,
            "captured_at": captured_at,
            "dbus_health": dict(health),
        },
    )


def write_energy_inputs_snapshot(
    paths: GatewayPaths,
    snapshot: EnergyInputsSnapshot | None,
) -> None:
    """Publish semantic energy inputs when the adapter has produced them."""
    if snapshot is not None:
        write_energy_inputs_file(paths.energy_inputs_path, snapshot)


def write_energy_topology_snapshot(
    paths: GatewayPaths,
    snapshot: EnergyTopologySnapshot | None,
) -> None:
    """Publish semantic energy topology when the adapter has produced it."""
    if snapshot is not None:
        write_json_file(paths.energy_topology_path, snapshot.to_payload())

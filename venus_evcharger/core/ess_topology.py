# SPDX-License-Identifier: GPL-3.0-or-later
"""Transport-neutral identity for persisted ESS learning state."""

from __future__ import annotations

from collections.abc import Iterable

ESS_GRID_SETPOINT_TARGET_ID = "ess-grid-setpoint"
ESS_LEARNING_TOPOLOGY_VERSION = 3


def ess_learning_topology_key(source_id: object, energy_source_ids: Iterable[object]) -> str:
    """Identify the domain topology without exposing a gateway implementation target."""
    normalized_source = str(source_id or "").strip()
    normalized_energy_ids = sorted(
        {str(value).strip() for value in energy_source_ids if str(value).strip()}
    )
    return (
        f"victron-bias-learning/v{ESS_LEARNING_TOPOLOGY_VERSION}"
        f"/source={normalized_source}"
        f"/target={ESS_GRID_SETPOINT_TARGET_ID}"
        f"/energy={','.join(normalized_energy_ids)}"
    )


__all__ = [
    "ESS_GRID_SETPOINT_TARGET_ID",
    "ESS_LEARNING_TOPOLOGY_VERSION",
    "ess_learning_topology_key",
]

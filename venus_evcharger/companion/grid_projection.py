# SPDX-License-Identifier: GPL-3.0-or-later
"""Stateful projection of intermittent grid measurements."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class GridProjectionConfig:
    """Hold and smoothing policy for one grid publication stream."""

    hold_seconds: float = 0.0
    smoothing_alpha: float = 1.0
    smoothing_max_jump_watts: float = 0.0


@dataclass(frozen=True, slots=True)
class GridProjection:
    """One resolved grid value with explicit connectivity semantics."""

    value_w: float
    connected: bool


@dataclass(slots=True)
class _GridState:
    value_w: float
    last_good_at: float


class GridProjector:
    """Resolve missing grid samples without exposing transport details."""

    def __init__(self) -> None:
        self._states: dict[str, _GridState] = {}

    def clear(self) -> None:
        self._states.clear()

    def project(
        self,
        stream_id: str,
        *,
        raw_value: object,
        online: bool,
        now: float,
        config: GridProjectionConfig,
    ) -> GridProjection:
        numeric = _numeric(raw_value)
        previous = self._states.get(stream_id)
        if numeric is not None:
            value = _smoothed_value(numeric, previous, config)
            self._states[stream_id] = _GridState(value_w=value, last_good_at=float(now))
            return GridProjection(value_w=value, connected=bool(online))
        if previous is not None and _within_hold(previous, now, config.hold_seconds):
            return GridProjection(value_w=previous.value_w, connected=True)
        self._states.pop(stream_id, None)
        return GridProjection(value_w=0.0, connected=False)


def aggregate_grid_input(
    snapshot: Mapping[str, object],
    sources: tuple[Mapping[str, object], ...],
    *,
    authoritative_source_id: str,
) -> tuple[object, bool]:
    """Select the configured semantic grid input from one worker snapshot."""
    if bool(snapshot.get("grid_fusion_enabled", False)):
        return _fused_grid_input(snapshot)
    if authoritative_source_id:
        return _authoritative_grid_input(sources, authoritative_source_id)
    return _combined_grid_input(snapshot)


def _fused_grid_input(snapshot: Mapping[str, object]) -> tuple[object, bool]:
    value = snapshot.get("grid_power")
    return value, _numeric(value) is not None


def _authoritative_grid_input(
    sources: tuple[Mapping[str, object], ...],
    source_id: str,
) -> tuple[object, bool]:
    source = next((item for item in sources if _source_id(item) == source_id), None)
    if source is None:
        return None, False
    return source.get("grid_interaction_w"), bool(source.get("online", False))


def _combined_grid_input(snapshot: Mapping[str, object]) -> tuple[object, bool]:
    return (
        snapshot.get("battery_combined_grid_interaction_w"),
        _positive_int(snapshot.get("battery_online_source_count")),
    )


def _smoothed_value(
    value: float,
    previous: _GridState | None,
    config: GridProjectionConfig,
) -> float:
    if previous is None:
        return value
    alpha = min(1.0, max(0.0, float(config.smoothing_alpha)))
    jump = abs(value - previous.value_w)
    jump_limit = float(config.smoothing_max_jump_watts)
    if alpha in (0.0, 1.0) or (jump_limit > 0.0 and jump > jump_limit):
        return value
    return (alpha * value) + ((1.0 - alpha) * previous.value_w)


def _within_hold(state: _GridState, now: float, hold_seconds: float) -> bool:
    return hold_seconds > 0.0 and float(now) - state.last_good_at <= float(hold_seconds)


def _numeric(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _positive_int(value: object) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float, str)):
        try:
            return int(value or 0) > 0
        except ValueError:
            return False
    return False


def _source_id(source: Mapping[str, object]) -> str:
    value = source.get("source_id", "")
    return "" if value is None else str(value).strip()


__all__ = [
    "GridProjection",
    "GridProjectionConfig",
    "GridProjector",
    "aggregate_grid_input",
]

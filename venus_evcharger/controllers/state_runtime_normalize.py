# SPDX-License-Identifier: GPL-3.0-or-later
"""Normalization helpers for runtime-state controller roles."""

from __future__ import annotations

import time
from typing import Protocol, runtime_checkable

from venus_evcharger.backend.models import PhaseSelection, normalize_phase_selection, normalize_phase_selection_tuple
from venus_evcharger.core.contracts import finite_float_or_none, normalize_learning_phase, normalize_learning_state


@runtime_checkable
class RuntimeClock(Protocol):
    """Clock boundary required while normalizing persisted runtime state."""

    def time_now(self) -> float: ...


def require_runtime_clock(value: object) -> RuntimeClock:
    """Narrow the dynamic service boundary to the clock required by state code."""
    if not isinstance(value, RuntimeClock):
        raise AttributeError("state service must expose time_now()")
    if not callable(value.time_now):
        raise AttributeError("state service must expose time_now()")
    return value


class RuntimeStateNormalizer:
    """Normalize untrusted values crossing the runtime-state boundary."""

    @staticmethod
    def coerce_runtime_int(value: object, default: int = 0) -> int:
        if isinstance(value, bool):
            return int(default)
        if not isinstance(value, (str, int, float)):
            return int(default)
        try:
            return int(value)
        except (TypeError, ValueError):
            return int(default)

    @staticmethod
    def coerce_runtime_float(value: object, default: float = 0.0) -> float:
        normalized = finite_float_or_none(value)
        return float(default) if normalized is None else normalized

    @staticmethod
    def optional_float(value: object) -> float | None:
        if value is None:
            return None
        return RuntimeStateNormalizer.coerce_runtime_float(value)

    @staticmethod
    def optional_past_time(value: object, now: float | None = None) -> float | None:
        normalized = RuntimeStateNormalizer.optional_float(value)
        if normalized is None:
            return None
        current = time.time() if now is None else float(now)
        if normalized > (current + 1.0):
            return None
        return normalized

    @staticmethod
    def learned_charge_power_state(value: object) -> str:
        return normalize_learning_state(value)

    @staticmethod
    def learned_charge_power_phase(value: object) -> str | None:
        return normalize_learning_phase(value)

    @staticmethod
    def phase_selection(value: object, default: PhaseSelection = "P1") -> PhaseSelection:
        return normalize_phase_selection(value, default)

    @staticmethod
    def supported_phase_selections(
        value: object,
        default: tuple[PhaseSelection, ...] = ("P1",),
    ) -> tuple[PhaseSelection, ...]:
        normalized: tuple[PhaseSelection, ...] = normalize_phase_selection_tuple(value, default)
        return normalized

    @classmethod
    def optional_phase_selection(
        cls,
        value: object,
        default: PhaseSelection = "P1",
    ) -> PhaseSelection | None:
        if value is None:
            return None
        return cls.phase_selection(value, default)

    @staticmethod
    def optional_text(value: object) -> str | None:
        text = str(value or "").strip()
        return text or None

    @staticmethod
    def phase_switch_state(value: object) -> str | None:
        if value is None:
            return None
        state = str(value).strip().lower()
        if state in {"waiting-relay-off", "stabilizing"}:
            return state
        return None

    @staticmethod
    def load_time(service: RuntimeClock) -> float:
        raw_current_time: object = service.time_now()
        return RuntimeStateNormalizer.coerce_runtime_float(raw_current_time, time.time())

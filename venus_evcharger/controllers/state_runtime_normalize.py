# SPDX-License-Identifier: GPL-3.0-or-later
"""Normalization helpers for runtime-state controller roles."""

from __future__ import annotations

import time
from typing import Any

from venus_evcharger.backend.models import PhaseSelection, normalize_phase_selection, normalize_phase_selection_tuple
from venus_evcharger.core.contracts import finite_float_or_none, normalize_learning_phase, normalize_learning_state
from venus_evcharger.core.controller_contracts import ControllerAssemblyContract


class _StateRuntimeNormalize(ControllerAssemblyContract):
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
    def _coerce_optional_runtime_float(value: object) -> float | None:
        if value is None:
            return None
        return _StateRuntimeNormalize.coerce_runtime_float(value)

    @staticmethod
    def _coerce_optional_runtime_past_time(value: object, now: float | None = None) -> float | None:
        normalized = _StateRuntimeNormalize._coerce_optional_runtime_float(value)
        if normalized is None:
            return None
        current = time.time() if now is None else float(now)
        if normalized > (current + 1.0):
            return None
        return normalized

    @staticmethod
    def _normalize_learned_charge_power_state(value: object) -> str:
        return normalize_learning_state(value)

    @staticmethod
    def _normalize_learned_charge_power_phase(value: object) -> str | None:
        return normalize_learning_phase(value)

    @staticmethod
    def _normalize_runtime_phase_selection(value: object, default: PhaseSelection = "P1") -> PhaseSelection:
        return normalize_phase_selection(value, default)

    @staticmethod
    def _normalize_runtime_supported_phase_selections(
        value: object,
        default: tuple[PhaseSelection, ...] = ("P1",),
    ) -> tuple[PhaseSelection, ...]:
        normalized: tuple[PhaseSelection, ...] = normalize_phase_selection_tuple(value, default)
        return normalized

    @classmethod
    def _normalized_optional_runtime_phase_selection(
        cls,
        value: object,
        default: PhaseSelection = "P1",
    ) -> PhaseSelection | None:
        if value is None:
            return None
        return cls._normalize_runtime_phase_selection(value, default)

    @staticmethod
    def _normalized_optional_runtime_text(value: object) -> str | None:
        text = str(value or "").strip()
        return text or None

    @staticmethod
    def _normalize_phase_switch_state(value: object) -> str | None:
        if value is None:
            return None
        state = str(value).strip().lower()
        if state in {"waiting-relay-off", "stabilizing"}:
            return state
        return None

    @staticmethod
    def _runtime_load_time(svc: Any) -> float:
        time_now = getattr(svc, "_time_now", None)
        raw_current_time: object = time_now() if callable(time_now) else time.time()
        return _StateRuntimeNormalize.coerce_runtime_float(raw_current_time, time.time())

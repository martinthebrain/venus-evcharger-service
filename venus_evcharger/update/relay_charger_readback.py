# SPDX-License-Identifier: GPL-3.0-or-later
"""Fresh charger readback helpers for the update-cycle relay logic."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from venus_evcharger.core.contracts import finite_float_or_none
from venus_evcharger.update.relay_charger_health import _RelayChargerHealth


@runtime_checkable
class ChargerEnableBackend(Protocol):
    """Charger backend surface required for enable/disable writes."""

    def set_enabled(self, enabled: bool) -> None: ...  # pragma: no cover


@runtime_checkable
class ChargerCurrentBackend(Protocol):
    """Charger backend surface required for current writes."""

    def set_current(self, amps: float) -> None: ...  # pragma: no cover


class _RelayChargerReadback(_RelayChargerHealth):
    """Normalize fresh charger and switch readback surfaces for later policy code."""

    if TYPE_CHECKING:  # pragma: no cover
        CHARGER_FAULT_HINT_TOKENS: frozenset[str]

    @staticmethod
    def _charger_enable_backend(svc: Any) -> ChargerEnableBackend | None:
        backend = getattr(svc, "_charger_backend", None)
        return backend if isinstance(backend, ChargerEnableBackend) else None

    @staticmethod
    def _charger_current_backend(svc: Any) -> ChargerCurrentBackend | None:
        backend = getattr(svc, "_charger_backend", None)
        return backend if isinstance(backend, ChargerCurrentBackend) else None

    @staticmethod
    def _charger_state_max_age_seconds(svc: Any) -> float:
        candidates = [2.0]
        worker_poll_interval = finite_float_or_none(getattr(svc, "_worker_poll_interval_seconds", None))
        if worker_poll_interval is not None and worker_poll_interval > 0.0:
            candidates.append(float(worker_poll_interval) * 2.0)
        soft_fail_seconds = finite_float_or_none(getattr(svc, "auto_shelly_soft_fail_seconds", 10.0))
        if soft_fail_seconds is not None and soft_fail_seconds > 0.0:
            candidates.append(float(soft_fail_seconds))
        return max(1.0, min(candidates))

    @staticmethod
    def _charger_readback_now(svc: Any, now: float | None = None) -> float:
        if now is not None:
            return float(now)
        if callable(getattr(svc, "_time_now", None)):
            return float(svc._time_now())
        return time.time()

    @classmethod
    def _fresh_charger_state_timestamp(cls, svc: Any, now: float | None = None) -> float | None:
        if getattr(svc, "_charger_backend", None) is None:
            return None
        state_at = finite_float_or_none(getattr(svc, "_last_charger_state_at", None))
        if state_at is None:
            return None
        current = cls._charger_readback_now(svc, now)
        if abs(current - state_at) > cls._charger_state_max_age_seconds(svc):
            return None
        return float(state_at)

    @classmethod
    def _fresh_switch_feedback_timestamp(cls, svc: Any, now: float | None = None) -> float | None:
        state_at = finite_float_or_none(getattr(svc, "_last_switch_feedback_at", None))
        if state_at is None:
            return None
        current = cls._charger_readback_now(svc, now)
        if abs(current - state_at) > cls._charger_state_max_age_seconds(svc):
            return None
        return float(state_at)

    @classmethod
    def _fresh_switch_feedback_closed(cls, svc: Any, now: float | None = None) -> bool | None:
        if cls._fresh_switch_feedback_timestamp(svc, now) is None:
            return None
        raw_value = getattr(svc, "_last_switch_feedback_closed", None)
        if raw_value is None:
            return None
        return bool(raw_value)

    @classmethod
    def _fresh_switch_interlock_ok(cls, svc: Any, now: float | None = None) -> bool | None:
        if cls._fresh_switch_feedback_timestamp(svc, now) is None:
            return None
        raw_value = getattr(svc, "_last_switch_interlock_ok", None)
        if raw_value is None:
            return None
        return bool(raw_value)

    @classmethod
    def _fresh_charger_enabled_readback(cls, svc: Any, now: float | None = None) -> bool | None:
        if cls._fresh_charger_state_timestamp(svc, now) is None:
            return None
        raw_enabled = getattr(svc, "_last_charger_state_enabled", None)
        if raw_enabled is None:
            return None
        return bool(raw_enabled)

    @classmethod
    def _fresh_charger_float_readback(
        cls,
        svc: Any,
        attribute_name: str,
        now: float | None = None,
    ) -> float | None:
        if cls._fresh_charger_state_timestamp(svc, now) is None:
            return None
        value = finite_float_or_none(getattr(svc, attribute_name, None))
        if value is None:
            return None
        return float(value)

    @classmethod
    def _fresh_charger_power_readback(cls, svc: Any, now: float | None = None) -> float | None:
        power_w = cls._fresh_charger_float_readback(svc, "_last_charger_state_power_w", now)
        return None if power_w is None else max(0.0, float(power_w))

    @classmethod
    def _fresh_charger_actual_current_readback(cls, svc: Any, now: float | None = None) -> float | None:
        current_amps = cls._fresh_charger_float_readback(svc, "_last_charger_state_actual_current_amps", now)
        return None if current_amps is None else max(0.0, float(current_amps))

    @classmethod
    def _fresh_charger_energy_readback(cls, svc: Any, now: float | None = None) -> float | None:
        energy_kwh = cls._fresh_charger_float_readback(svc, "_last_charger_state_energy_kwh", now)
        return None if energy_kwh is None else max(0.0, float(energy_kwh))

    @classmethod
    def _fresh_charger_text_readback(
        cls,
        svc: Any,
        attribute_name: str,
        now: float | None = None,
    ) -> str | None:
        if cls._fresh_charger_state_timestamp(svc, now) is None:
            return None
        raw_value = getattr(svc, attribute_name, None)
        if raw_value is None:
            return None
        text = str(raw_value).strip()
        return text or None

    @classmethod
    def _charger_text_tokens(cls, value: str | None) -> set[str]:
        if value is None:
            return set()
        normalized = str(value).strip().lower()
        for separator in ("-", "_", "/", ".", ",", ";", ":"):
            normalized = normalized.replace(separator, " ")
        return {token for token in normalized.split() if token}

    @classmethod
    def _charger_text_indicates_fault(cls, value: str | None) -> bool:
        tokens = cls._charger_text_tokens(value)
        if not tokens or "no" in tokens:
            return False
        return bool(tokens & set(cls.CHARGER_FAULT_HINT_TOKENS))

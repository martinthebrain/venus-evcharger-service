# SPDX-License-Identifier: GPL-3.0-or-later
"""Charger transport issue and retry bookkeeping component."""

from __future__ import annotations

import time
from typing import Protocol, TypeGuard, runtime_checkable

from venus_evcharger.core.common import _charger_transport_retry_delay_seconds, _fresh_charger_retry_until
from venus_evcharger.core.contracts import exception_detail


@runtime_checkable
class _Clock(Protocol):
    def __call__(self) -> float: ...  # pragma: no cover


@runtime_checkable
class _RetryDelay(Protocol):
    def __call__(self, source: str, captured_at: float, delay_seconds: float) -> object: ...


def _is_object_dict(value: object) -> TypeGuard[dict[str, object]]:
    return isinstance(value, dict) and all(isinstance(key, str) for key in value)


class ChargerTransportTracker:
    """Store transient charger failures on the state-owning service."""

    @staticmethod
    def set_runtime_attr(svc: object, attribute_name: str, value: object) -> None:
        try:
            setattr(svc, attribute_name, value)
        except AttributeError:
            state = getattr(svc, "__dict__", None)
            if _is_object_dict(state):
                state[attribute_name] = value
                return
            raise

    @staticmethod
    def transport_detail(error: BaseException) -> str:
        return exception_detail(error)

    @classmethod
    def remember_issue(
        cls,
        svc: object,
        reason: str,
        source: str,
        error: BaseException,
        now: float | None = None,
    ) -> None:
        captured_at = cls.now(svc, now)
        cls.set_runtime_attr(svc, "_last_charger_transport_reason", str(reason).strip() or None)
        cls.set_runtime_attr(svc, "_last_charger_transport_source", str(source).strip() or None)
        cls.set_runtime_attr(svc, "_last_charger_transport_detail", cls.transport_detail(error))
        cls.set_runtime_attr(svc, "_last_charger_transport_at", captured_at)

    @classmethod
    def clear_issue(cls, svc: object) -> None:
        cls.set_runtime_attr(svc, "_last_charger_transport_reason", None)
        cls.set_runtime_attr(svc, "_last_charger_transport_source", None)
        cls.set_runtime_attr(svc, "_last_charger_transport_detail", None)
        cls.set_runtime_attr(svc, "_last_charger_transport_at", None)

    @classmethod
    def remember_retry(
        cls,
        svc: object,
        reason: str,
        source: str,
        now: float | None = None,
    ) -> None:
        captured_at = cls.now(svc, now)
        delay_seconds = _charger_transport_retry_delay_seconds(svc, reason)
        delay_retry = getattr(svc, "_delay_source_retry", None)
        if isinstance(delay_retry, _RetryDelay):
            delay_retry("charger", captured_at, delay_seconds)
        else:
            retry_after = getattr(svc, "_source_retry_after", None)
            if _is_object_dict(retry_after):
                retry_after["charger"] = captured_at + delay_seconds
        cls.set_runtime_attr(svc, "_charger_retry_reason", str(reason).strip() or None)
        cls.set_runtime_attr(svc, "_charger_retry_source", str(source).strip() or None)
        cls.set_runtime_attr(svc, "_charger_retry_until", captured_at + delay_seconds)

    @classmethod
    def clear_retry(cls, svc: object) -> None:
        cls.set_runtime_attr(svc, "_charger_retry_reason", None)
        cls.set_runtime_attr(svc, "_charger_retry_source", None)
        cls.set_runtime_attr(svc, "_charger_retry_until", None)
        retry_after = getattr(svc, "_source_retry_after", None)
        if _is_object_dict(retry_after):
            retry_after["charger"] = 0.0

    @classmethod
    def retry_active(cls, svc: object, now: float | None = None) -> bool:
        return _fresh_charger_retry_until(svc, cls.now(svc, now)) is not None

    @staticmethod
    def now(svc: object, now: float | None = None) -> float:
        if now is not None:
            return float(now)
        time_now: object = getattr(svc, "time_now", None)
        return float(time_now()) if isinstance(time_now, _Clock) else time.time()


__all__ = ["ChargerTransportTracker"]

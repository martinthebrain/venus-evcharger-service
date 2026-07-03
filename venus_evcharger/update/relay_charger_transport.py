# SPDX-License-Identifier: GPL-3.0-or-later
"""Charger transport issue and retry bookkeeping."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from venus_evcharger.core.common import _charger_transport_retry_delay_seconds, _fresh_charger_retry_until
from venus_evcharger.core.contracts import exception_detail
from venus_evcharger.update.relay_phase_publish import _RelayPhasePublish


class _RelayChargerTransport(_RelayPhasePublish):
    """Store transient charger transport failures and retry windows."""

    if TYPE_CHECKING:  # pragma: no cover

        @classmethod
        def _charger_readback_now(cls, svc: Any, now: float | None = None) -> float: ...

    @staticmethod
    def _set_runtime_attr(svc: Any, attribute_name: str, value: Any) -> None:
        try:
            setattr(svc, attribute_name, value)
        except AttributeError:
            if hasattr(svc, "__dict__"):
                svc.__dict__[attribute_name] = value
                return
            raise

    @staticmethod
    def _charger_transport_detail(error: BaseException) -> str:
        return exception_detail(error)

    @classmethod
    def _remember_charger_transport_issue(
        cls,
        svc: Any,
        reason: str,
        source: str,
        error: BaseException,
        now: float | None = None,
    ) -> None:
        captured_at = cls._charger_readback_now(svc, now)
        cls._set_runtime_attr(svc, "_last_charger_transport_reason", str(reason).strip() or None)
        cls._set_runtime_attr(svc, "_last_charger_transport_source", str(source).strip() or None)
        cls._set_runtime_attr(svc, "_last_charger_transport_detail", cls._charger_transport_detail(error))
        cls._set_runtime_attr(svc, "_last_charger_transport_at", captured_at)

    @classmethod
    def _clear_charger_transport_issue(cls, svc: Any) -> None:
        cls._set_runtime_attr(svc, "_last_charger_transport_reason", None)
        cls._set_runtime_attr(svc, "_last_charger_transport_source", None)
        cls._set_runtime_attr(svc, "_last_charger_transport_detail", None)
        cls._set_runtime_attr(svc, "_last_charger_transport_at", None)

    @classmethod
    def _remember_charger_retry(
        cls,
        svc: Any,
        reason: str,
        source: str,
        now: float | None = None,
    ) -> None:
        captured_at = cls._charger_readback_now(svc, now)
        delay_seconds = _charger_transport_retry_delay_seconds(svc, reason)
        delay_retry = getattr(svc, "_delay_source_retry", None)
        if callable(delay_retry):
            delay_retry("charger", captured_at, delay_seconds)
        elif isinstance(getattr(svc, "_source_retry_after", None), dict):
            svc._source_retry_after["charger"] = captured_at + delay_seconds
        cls._set_runtime_attr(svc, "_charger_retry_reason", str(reason).strip() or None)
        cls._set_runtime_attr(svc, "_charger_retry_source", str(source).strip() or None)
        cls._set_runtime_attr(svc, "_charger_retry_until", captured_at + delay_seconds)

    @classmethod
    def _clear_charger_retry(cls, svc: Any) -> None:
        cls._set_runtime_attr(svc, "_charger_retry_reason", None)
        cls._set_runtime_attr(svc, "_charger_retry_source", None)
        cls._set_runtime_attr(svc, "_charger_retry_until", None)
        if isinstance(getattr(svc, "_source_retry_after", None), dict):
            svc._source_retry_after["charger"] = 0.0

    @classmethod
    def _charger_retry_active(cls, svc: Any, now: float | None = None) -> bool:
        return _fresh_charger_retry_until(svc, cls._charger_readback_now(svc, now)) is not None

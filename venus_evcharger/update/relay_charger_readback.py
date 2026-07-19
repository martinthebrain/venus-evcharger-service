# SPDX-License-Identifier: GPL-3.0-or-later
"""Typed charger-backend capabilities used by relay components."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol, runtime_checkable


@runtime_checkable
class ChargerEnableBackend(Protocol):
    """Charger backend surface required for enable/disable writes."""

    def set_enabled(self, enabled: bool) -> None: ...  # pragma: no cover


@runtime_checkable
class ChargerCurrentBackend(Protocol):
    """Charger backend surface required for current writes."""

    def set_current(self, amps: float) -> None: ...  # pragma: no cover


class ChargerBackendAccess:
    """Resolve optional charger write capabilities without owning runtime state."""

    def __init__(self, fault_hint_tokens: Iterable[str]) -> None:
        self._fault_hint_tokens = frozenset(fault_hint_tokens)

    @staticmethod
    def enable_backend(svc: object) -> ChargerEnableBackend | None:
        backend = getattr(svc, "_charger_backend", None)
        return backend if isinstance(backend, ChargerEnableBackend) else None

    @staticmethod
    def current_backend(svc: object) -> ChargerCurrentBackend | None:
        backend = getattr(svc, "_charger_backend", None)
        return backend if isinstance(backend, ChargerCurrentBackend) else None

    @staticmethod
    def text_tokens(value: str | None) -> set[str]:
        if value is None:
            return set()
        normalized = str(value).strip().lower()
        for separator in ("-", "_", "/", ".", ",", ";", ":"):
            normalized = normalized.replace(separator, " ")
        return {token for token in normalized.split() if token}

    def text_indicates_fault(self, value: str | None) -> bool:
        tokens = self.text_tokens(value)
        if not tokens or "no" in tokens:
            return False
        return bool(tokens & self._fault_hint_tokens)


__all__ = ["ChargerBackendAccess", "ChargerCurrentBackend", "ChargerEnableBackend"]

# SPDX-License-Identifier: GPL-3.0-or-later
"""Structural contracts for the gateway-owned DBus publication service."""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol


class DbusServiceLike(Protocol):  # pragma: no cover - declarative contract
    """Expose only the service operations used by gateway publication."""

    def add_path(
        self,
        path: str,
        value: object,
        *,
        gettextcallback: Callable[[str, object], str] | None = None,
        writeable: bool = False,
        onchangecallback: Callable[[str, object], object] | None = None,
    ) -> object: ...

    def register(self) -> object: ...

    def __setitem__(self, path: str, value: object) -> None: ...


__all__ = ["DbusServiceLike"]

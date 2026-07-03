# SPDX-License-Identifier: GPL-3.0-or-later
"""Minimal protocol for the adapter-owned VeDbusService object."""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol


class DbusServiceLike(Protocol):  # pragma: no cover
    """Only the service operations used by the DBus gateway adapter."""

    def add_path(
        self,
        path: str,
        value: object,
        *,
        writeable: bool = False,
        onchangecallback: Callable[[str, object], object] | None = None,
    ) -> object: ...

    def register(self) -> object: ...

    def __setitem__(self, path: str, value: object) -> None: ...

# SPDX-License-Identifier: GPL-3.0-or-later
"""Typed constructor contracts for the backend composition root."""

from __future__ import annotations

from typing import Protocol, TypeVar

from .base import SwitchBackend

BackendT_co = TypeVar("BackendT_co", covariant=True)


class BackendConstructor(Protocol[BackendT_co]):
    """Construct one role-specific backend from service context and config."""

    def __call__(self, service: object, *, config_path: str = "") -> BackendT_co: ...  # pragma: no cover


class SwitchBackendFactory(Protocol):
    """Resolve and construct one normalized switch backend by registered type."""

    def __call__(
        self,
        backend_type: str,
        service: object,
        config_path: str = "",
    ) -> SwitchBackend: ...  # pragma: no cover

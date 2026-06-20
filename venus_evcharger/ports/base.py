# SPDX-License-Identifier: GPL-3.0-or-later
"""Small controller ports that decouple controllers from the full service object."""

from __future__ import annotations

from typing import Any


class _BaseServicePort:
    """Common forwarding helpers for controller-specific service ports."""

    _ALLOWED_ATTRS: set[str] = set()
    _MUTABLE_ATTRS: set[str] = set()
    _ALLOWED_METHODS: set[str] = set()

    def __init__(self, service: Any) -> None:
        object.__setattr__(self, "_service", service)

    def __getattr__(self, name: str) -> Any:
        if name in self._ALLOWED_ATTRS or name in self._ALLOWED_METHODS:
            return getattr(self._service, name)
        raise AttributeError(name)

    def __setattr__(self, name: str, value: Any) -> None:
        descriptor = getattr(type(self), name, None)
        if isinstance(descriptor, property):
            object.__setattr__(self, name, value)
            return
        if name in self._MUTABLE_ATTRS:
            setattr(self._service, name, value)
            return
        raise AttributeError(name)


class _ControllerBoundPort(_BaseServicePort):
    """Base class for ports that also need controller override callbacks."""

    def __init__(self, service: Any) -> None:
        super().__init__(service)
        object.__setattr__(self, "_controller", None)

    def bind_controller(self, controller: Any) -> None:
        object.__setattr__(self, "_controller", controller)

    def _controller_or_override(self, name: str, controller_method: str) -> Any:
        override = self._service.__dict__.get(name)
        if override is not None:
            return override
        if self._controller is None:
            raise AttributeError(name)
        return getattr(self._controller, controller_method)

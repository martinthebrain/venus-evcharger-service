# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared native Modbus backend instance helpers."""

from __future__ import annotations

import sys
from typing import Any, Callable, TypeVar

from .modbus_client import ModbusClient
from .modbus_transport import ModbusTransport, create_modbus_transport

_SettingsT = TypeVar("_SettingsT")


def initialize_native_modbus_backend(
    backend: Any,
    service: object,
    config_path: str,
    settings_loader: Callable[[object, str], _SettingsT],
) -> None:
    """Initialize common native Modbus backend instance fields."""
    normalized_path = str(config_path).strip()
    backend.service = service
    backend.config_path = normalized_path
    backend.settings = settings_loader(service, normalized_path)
    backend._transport = None
    backend._client_cache = None


def native_modbus_client(backend: Any) -> ModbusClient:
    """Return the lazily created Modbus client for one native backend."""
    if backend._client_cache is None:
        if backend._transport is None:
            backend._transport = _native_modbus_transport_factory(backend)(backend.settings.transport_settings)
        backend._client_cache = ModbusClient(
            backend._transport,
            backend.settings.transport_settings.unit_id,
            backend.settings.transport_settings.timeout_seconds,
        )
    return backend._client_cache


def _native_modbus_transport_factory(backend: Any) -> Callable[[Any], ModbusTransport]:
    """Return the backend module's transport factory, preserving existing test patch points."""
    module = sys.modules.get(type(backend).__module__)
    factory = getattr(module, "create_modbus_transport", create_modbus_transport)
    return factory if callable(factory) else create_modbus_transport


__all__ = ["initialize_native_modbus_backend", "native_modbus_client"]

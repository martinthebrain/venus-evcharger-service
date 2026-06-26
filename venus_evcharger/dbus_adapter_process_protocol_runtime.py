#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Runtime, socket, and identity contracts for DBus adapter process mixins."""

from __future__ import annotations

import configparser
import socket
from collections.abc import Callable
from typing import Any, Protocol

from venus_evcharger.dbus_adapter_write import DbusWriteScheduler
from venus_evcharger.dbus_gateway import DbusCacheStore, DbusCommandInbox, GatewayPaths


class DbusAdapterRuntimeContext(Protocol):  # pragma: no cover
    """Signal/runtime surface required by ``DbusAdapterRuntimeMixin``."""

    _main_loop: Any
    _stop: bool


class DbusAdapterSocketContext(Protocol):  # pragma: no cover
    """Unix-socket IPC surface required by ``DbusAdapterSocketMixin``."""

    paths: GatewayPaths
    cache: DbusCacheStore
    commands: DbusCommandInbox
    _server: socket.socket | None

    def _handle_socket_payload(self, data: str) -> dict[str, Any]: ...
    def _socket_handlers(self) -> dict[str, Callable[[dict[str, Any], str], dict[str, Any]]]: ...
    def _socket_snapshot(self, payload: dict[str, Any], request_type: str) -> dict[str, Any]: ...
    def _socket_health(self, payload: dict[str, Any], request_type: str) -> dict[str, Any]: ...
    def _socket_enqueue(self, payload: dict[str, Any], request_type: str) -> dict[str, Any]: ...
    def _unsupported_socket_request(self, payload: dict[str, Any], request_type: str) -> dict[str, Any]: ...
    def _health_snapshot(self) -> dict[str, Any]: ...


class DbusAdapterIdentityContext(Protocol):  # pragma: no cover
    """EV-charger DBus service identity surface required by ``DbusAdapterIdentityMixin``."""

    config: configparser.ConfigParser
    write_scheduler: DbusWriteScheduler
    service_name: str
    _dbusservice: Any
    _dbusservice_registered: bool

    def _ensure_dbus_service(self) -> None: ...
    def _register_identity_paths(self) -> None: ...
    def _identity_path_values(self, defaults: configparser.SectionProxy) -> dict[str, Any]: ...
    def _add_owned_path(self, path: str, value: Any) -> None: ...
    def _configured_for_identity(self, defaults: configparser.SectionProxy) -> bool: ...

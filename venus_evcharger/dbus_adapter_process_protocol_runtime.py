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

    def install_signal_handlers(self) -> None: ...


class DbusAdapterSocketContext(Protocol):  # pragma: no cover
    """Unix-socket IPC surface required by ``DbusAdapterSocketMixin``."""

    paths: GatewayPaths
    cache: DbusCacheStore
    commands: DbusCommandInbox
    _server: socket.socket | None

    def handle_socket_payload(self, data: str) -> dict[str, Any]: ...
    def socket_handlers(self) -> dict[str, Callable[[dict[str, Any], str], dict[str, Any]]]: ...
    def socket_snapshot(self, payload: dict[str, Any], request_type: str) -> dict[str, Any]: ...
    def socket_health(self, payload: dict[str, Any], request_type: str) -> dict[str, Any]: ...
    def socket_enqueue(self, payload: dict[str, Any], request_type: str) -> dict[str, Any]: ...
    def unsupported_socket_request(self, payload: dict[str, Any], request_type: str) -> dict[str, Any]: ...
    def health_snapshot(self) -> dict[str, Any]: ...


class DbusAdapterIdentityContext(Protocol):  # pragma: no cover
    """EV-charger DBus service identity surface required by ``DbusAdapterIdentityMixin``."""

    config: configparser.ConfigParser
    write_scheduler: DbusWriteScheduler
    service_name: str
    _dbusservice: Any
    _dbusservice_registered: bool

    @property
    def dbus_service(self) -> Any: ...
    @property
    def dbus_service_registered(self) -> bool: ...
    def set_dbus_service(self, service: Any, *, registered: bool = False) -> None: ...
    def ensure_dbus_service(self) -> None: ...
    def register_dbus_service_name(self) -> None: ...
    def register_identity_paths(self) -> None: ...
    def identity_path_values(self, defaults: configparser.SectionProxy) -> dict[str, Any]: ...
    def add_owned_path(self, path: str, value: Any) -> None: ...
    def configured_for_identity(self, defaults: configparser.SectionProxy) -> bool: ...

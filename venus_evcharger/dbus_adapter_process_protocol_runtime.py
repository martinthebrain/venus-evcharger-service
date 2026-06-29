#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Runtime, socket, and identity contracts for DBus adapter process mixins."""

from __future__ import annotations

import configparser
import socket
from collections.abc import Callable
from typing import Protocol

from venus_evcharger.dbus_adapter_write import DbusWriteScheduler
from venus_evcharger.dbus_adapter_service_protocol import DbusServiceLike
from venus_evcharger.dbus_gateway import DbusCacheStore, DbusCommandInbox, GatewayPaths
from venus_evcharger.dbus_gateway_command_types import CommandPayload


class MainLoopLike(Protocol):  # pragma: no cover
    def run(self) -> object: ...
    def quit(self) -> object: ...


class DbusAdapterRuntimeContext(Protocol):  # pragma: no cover
    """Signal/runtime surface required by ``DbusAdapterRuntime``."""

    _main_loop: MainLoopLike | None
    _stop: bool

    def install_signal_handlers(self) -> None: ...


class DbusAdapterSocketContext(Protocol):  # pragma: no cover
    """Unix-socket IPC surface required by ``DbusAdapterSocket``."""

    paths: GatewayPaths
    cache: DbusCacheStore
    commands: DbusCommandInbox
    _server: socket.socket | None

    def handle_socket_payload(self, data: str) -> CommandPayload: ...
    def dispatch_socket_payload(self, payload: CommandPayload) -> CommandPayload: ...
    def socket_handlers(self) -> dict[str, Callable[[CommandPayload, str], CommandPayload]]: ...
    def socket_snapshot(self, payload: CommandPayload, request_type: str) -> CommandPayload: ...
    def socket_health(self, payload: CommandPayload, request_type: str) -> CommandPayload: ...
    def socket_enqueue(self, payload: CommandPayload, request_type: str) -> CommandPayload: ...
    def unsupported_socket_request(self, payload: CommandPayload, request_type: str) -> CommandPayload: ...
    def health_snapshot(self) -> CommandPayload: ...


class DbusAdapterIdentityContext(Protocol):  # pragma: no cover
    """EV-charger DBus service identity surface required by ``DbusAdapterIdentity``."""

    config: configparser.ConfigParser
    write_scheduler: DbusWriteScheduler
    service_name: str
    _dbusservice: DbusServiceLike | None
    _dbusservice_registered: bool

    @property
    def dbus_service(self) -> DbusServiceLike: ...
    @property
    def dbus_service_registered(self) -> bool: ...
    def set_dbus_service(self, service: DbusServiceLike, *, registered: bool = False) -> None: ...
    def ensure_dbus_service(self) -> None: ...
    def register_dbus_service_name(self) -> None: ...
    def register_identity_paths(self) -> None: ...
    def identity_path_values(self, defaults: configparser.SectionProxy) -> CommandPayload: ...
    def add_owned_path(self, path: str, value: object) -> None: ...
    def configured_for_identity(self, defaults: configparser.SectionProxy) -> bool: ...

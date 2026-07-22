#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Runtime, socket, and identity contracts for adapter components."""

from __future__ import annotations

import socket
from collections.abc import Callable
from typing import Protocol

from venus_evcharger.dbus_adapter.publication import GatewayPublicationRegistry
from venus_evcharger.dbus_gateway import DbusCacheStore, DbusGatewayCommandInbox, GatewayPaths
from venus_evcharger.ipc.command_types import CommandPayload


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
    commands: DbusGatewayCommandInbox
    _server: socket.socket | None

    def handle_socket_payload(self, data: str) -> CommandPayload: ...
    def dispatch_socket_payload(self, payload: CommandPayload) -> CommandPayload: ...
    def socket_handlers(self) -> dict[str, Callable[[CommandPayload, str], CommandPayload]]: ...
    def socket_snapshot(self, payload: CommandPayload, request_type: str) -> CommandPayload: ...
    def socket_health(self, payload: CommandPayload, request_type: str) -> CommandPayload: ...
    def socket_enqueue(self, payload: CommandPayload, request_type: str) -> CommandPayload: ...
    def unsupported_socket_request(self, payload: CommandPayload, request_type: str) -> CommandPayload: ...
    def health_snapshot(self) -> CommandPayload: ...


class DbusAdapterPublicationContext(Protocol):  # pragma: no cover
    """Publication registry surface required by ``DbusAdapterPublication``."""

    publication_registry: GatewayPublicationRegistry

    @property
    def evcs_service_registered(self) -> bool: ...

    @property
    def registered_publication_path_count(self) -> int: ...

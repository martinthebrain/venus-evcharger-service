#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Runtime, socket, and identity contracts for adapter components."""

from __future__ import annotations

import socket
from typing import TYPE_CHECKING, Protocol

from venus_evcharger.dbus_adapter.process.protocols.roles import HealthRole
from venus_evcharger.dbus_gateway import DbusCacheStore, DbusGatewayCommandInbox, GatewayPaths
from venus_evcharger.ipc.fast_publication import FastPublicationQueue

if TYPE_CHECKING:
    from venus_evcharger.dbus_adapter.publication import GatewayPublicationRegistry


class MainLoopLike(Protocol):  # pragma: no cover
    def run(self) -> object: ...
    def quit(self) -> object: ...


class DbusAdapterRuntimeContext(Protocol):  # pragma: no cover
    """Signal/runtime surface required by ``DbusAdapterRuntime``."""

    _main_loop: MainLoopLike | None
    _stop: bool


class DbusAdapterSocketContext(Protocol):  # pragma: no cover
    """Unix-socket IPC surface required by ``DbusAdapterSocket``."""

    paths: GatewayPaths
    cache: DbusCacheStore
    commands: DbusGatewayCommandInbox
    fast_publications: FastPublicationQueue
    _server: socket.socket | None

    @property
    def health_role(self) -> HealthRole: ...


class DbusAdapterPublicationContext(Protocol):  # pragma: no cover
    """Publication registry surface required by ``DbusAdapterPublication``."""

    @property
    def publication_registry(self) -> GatewayPublicationRegistry: ...

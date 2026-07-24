#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Narrow context supplied to the gateway write scheduler."""

from __future__ import annotations

import configparser
from collections.abc import Callable
from dataclasses import dataclass
from typing import TypeVar

from venus_evcharger.dbus_adapter.contracts import CommandOutcome
from venus_evcharger.dbus_adapter.process.protocols.roles import (
    IntrospectionRole,
    IoRole,
    PublicationRole,
)
from venus_evcharger.dbus_adapter.rate import DbusCircuitBreaker, DbusConnectionManager
from venus_evcharger.dbus_adapter.scheduling import AtomicJsonWriter
from venus_evcharger.dbus_adapter.write.protocols import PublicationRegistry
from venus_evcharger.dbus_gateway import DbusCacheStore, DbusGatewayCommandInbox
from venus_evcharger.ipc.command_mailbox import CommandMailbox
from venus_evcharger.ipc.command_types import CommandMapping
from venus_evcharger.ipc.fast_publication import FastPublicationQueue

_T = TypeVar("_T")


@dataclass(frozen=True, slots=True)
class DbusAdapterWriteContext:
    """Expose only the state and operations required by DBus writes."""

    cache: DbusCacheStore
    circuit: DbusCircuitBreaker
    commands: DbusGatewayCommandInbox
    command_lifecycle_path: str
    command_lifecycle_max_bytes: int
    config: configparser.ConfigParser
    connection: DbusConnectionManager
    core_command_mailbox: CommandMailbox
    fast_publications: FastPublicationQueue
    json_writer: AtomicJsonWriter
    publication_registry: PublicationRegistry
    service_name: str
    publication_role: PublicationRole
    introspection_role: IntrospectionRole
    io_role: IoRole

    @property
    def evcs_service_registered(self) -> bool:
        return self.publication_role.evcs_service_registered

    def process_non_write_command(self, command: CommandMapping) -> CommandOutcome:
        return self.introspection_role.process_non_write_command(command)

    def timed_dbus_operation(self, kind: str, operation: Callable[[], _T]) -> _T:
        return self.io_role.timed_dbus_operation(kind, operation)

    def timed_local_publish(self, operation: Callable[[], _T]) -> _T:
        return self.io_role.timed_local_publish(operation)


__all__ = ["DbusAdapterWriteContext"]

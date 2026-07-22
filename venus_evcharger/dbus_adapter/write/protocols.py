# SPDX-License-Identifier: GPL-3.0-or-later
"""Contracts for scheduled DBus writes."""

from __future__ import annotations

import configparser
from collections.abc import Callable
from typing import Protocol, TypeVar

from venus_evcharger.dbus_adapter.contracts import CommandOutcome
from venus_evcharger.dbus_adapter.publication import GatewayPublicationRegistry
from venus_evcharger.dbus_adapter.rate import DbusCircuitBreaker, DbusConnectionManager
from venus_evcharger.dbus_adapter.scheduling import AtomicJsonWriter
from venus_evcharger.dbus_gateway import DbusCacheStore, DbusGatewayCommandInbox
from venus_evcharger.ipc.command_mailbox import CommandMailbox
from venus_evcharger.ipc.command_types import CommandMapping

_T = TypeVar("_T")


class DbusWriteSchedulerAdapter(Protocol):  # pragma: no cover
    """Adapter surface required by the DBus write scheduler."""

    cache: DbusCacheStore
    circuit: DbusCircuitBreaker
    commands: DbusGatewayCommandInbox
    command_lifecycle_path: str
    command_lifecycle_max_bytes: int
    config: configparser.ConfigParser
    connection: DbusConnectionManager
    core_command_mailbox: CommandMailbox
    json_writer: AtomicJsonWriter
    publication_registry: GatewayPublicationRegistry
    service_name: str

    @property
    def evcs_service_registered(self) -> bool: ...
    def process_non_write_command(self, command: CommandMapping) -> CommandOutcome: ...
    def timed_dbus_operation(self, kind: str, operation: Callable[[], _T]) -> _T: ...
    def timed_local_publish(self, operation: Callable[[], _T]) -> _T: ...

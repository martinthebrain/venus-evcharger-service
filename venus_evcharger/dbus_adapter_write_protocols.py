# SPDX-License-Identifier: GPL-3.0-or-later
"""Contracts used by the DBus gateway write scheduler."""

from __future__ import annotations

import configparser
from collections.abc import Callable, Mapping
from typing import Protocol, TypeVar

from venus_evcharger.dbus_adapter_components import (
    AtomicJsonWriter,
    CommandOutcome,
    DbusCircuitBreaker,
    DbusConnectionManager,
)
from venus_evcharger.dbus_adapter_service_protocol import DbusServiceLike
from venus_evcharger.dbus_gateway import DbusCacheStore, DbusCommandInbox
from venus_evcharger.dbus_gateway_command_types import CommandFileList, CommandMapping

_T = TypeVar("_T")


class DbusWriteSchedulerAdapter(Protocol):  # pragma: no cover
    """Adapter surface required by the DBus write scheduler."""

    cache: DbusCacheStore
    circuit: DbusCircuitBreaker
    commands: DbusCommandInbox
    command_lifecycle_path: str
    config: configparser.ConfigParser
    connection: DbusConnectionManager
    core_commands: DbusCommandInbox
    json_writer: AtomicJsonWriter
    service_name: str

    @property
    def dbus_service(self) -> DbusServiceLike: ...
    @property
    def dbus_service_registered(self) -> bool: ...
    def process_non_write_command(self, command: CommandMapping) -> CommandOutcome: ...
    def register_dbus_service_name(self) -> None: ...
    def timed_dbus_operation(self, kind: str, operation: Callable[[], _T]) -> _T: ...
    def timed_local_publish(self, operation: Callable[[], _T]) -> _T: ...


class DropStaleCoalescedCommands(Protocol):  # pragma: no cover
    """Callable surface for removing stale coalesced commands."""

    def __call__(
        self,
        processed_path: str,
        processed_command: CommandMapping,
        *,
        pending_commands: CommandFileList | None = None,
    ) -> None: ...


class ProcessLocalPublishBurst(Protocol):  # pragma: no cover
    """Callable surface for flushing local publish commands."""

    def __call__(self, limit: int | None = None) -> int: ...


class PublishCommand(Protocol):  # pragma: no cover
    """Callable surface for publishing one gateway command."""

    def __call__(self, command: CommandMapping, *, command_file: str = "") -> CommandOutcome: ...


class ProcessLoadedCommand(Protocol):  # pragma: no cover
    """Callable surface for applying one loaded command file."""

    def __call__(
        self,
        path: str,
        command: CommandMapping,
        *,
        pending_commands: CommandFileList | None = None,
    ) -> CommandOutcome: ...

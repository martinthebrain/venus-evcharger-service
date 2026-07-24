# SPDX-License-Identifier: GPL-3.0-or-later
"""Contracts for scheduled DBus writes."""

from __future__ import annotations

import configparser
from collections.abc import Callable
from typing import Protocol, TypeVar

from venus_evcharger.dbus_adapter.contracts import CommandOutcome
from venus_evcharger.dbus_adapter.health.slo import GatewayPressureState
from venus_evcharger.dbus_adapter.rate import DbusCircuitBreaker, DbusConnectionManager
from venus_evcharger.dbus_adapter.scheduling import AtomicJsonWriter
from venus_evcharger.dbus_gateway import DbusCacheStore, DbusGatewayCommandInbox
from venus_evcharger.ipc.command_mailbox import CommandMailbox
from venus_evcharger.ipc.command_types import CommandFileList, CommandMapping, CommandPayload
from venus_evcharger.ipc.fast_publication import FastPublicationQueue
from venus_evcharger.ipc.gateway_publication import (
    PublishCompanionFields,
    PublishEvcsFields,
    RegisterCompanionPublication,
    RegisterEvcsPublication,
)

_T = TypeVar("_T")


class PublicationRegistry(Protocol):  # pragma: no cover
    """Apply validated semantic publications to gateway-owned services."""

    def register_evcs(self, publication: RegisterEvcsPublication) -> CommandOutcome: ...
    def publish_evcs(self, publication: PublishEvcsFields) -> CommandOutcome: ...
    def register_companion(self, publication: RegisterCompanionPublication) -> CommandOutcome: ...
    def publish_companion(self, publication: PublishCompanionFields) -> CommandOutcome: ...


class DbusWriteSchedulerAdapter(Protocol):  # pragma: no cover
    """Adapter surface required by the DBus write scheduler."""

    @property
    def cache(self) -> DbusCacheStore: ...
    @property
    def circuit(self) -> DbusCircuitBreaker: ...
    @property
    def commands(self) -> DbusGatewayCommandInbox: ...
    @property
    def command_lifecycle_path(self) -> str: ...
    @property
    def command_lifecycle_max_bytes(self) -> int: ...
    @property
    def config(self) -> configparser.ConfigParser: ...
    @property
    def connection(self) -> DbusConnectionManager: ...
    @property
    def core_command_mailbox(self) -> CommandMailbox: ...
    @property
    def fast_publications(self) -> FastPublicationQueue: ...
    @property
    def json_writer(self) -> AtomicJsonWriter: ...
    @property
    def publication_registry(self) -> PublicationRegistry: ...
    @property
    def service_name(self) -> str: ...
    @property
    def evcs_service_registered(self) -> bool: ...
    def process_non_write_command(self, command: CommandMapping) -> CommandOutcome: ...
    def timed_dbus_operation(self, kind: str, operation: Callable[[], _T]) -> _T: ...
    def timed_local_publish(self, operation: Callable[[], _T]) -> _T: ...


class SemanticWriteAdapter(Protocol):  # pragma: no cover
    """Gateway boundary required by semantic DBus operations."""

    @property
    def cache(self) -> DbusCacheStore: ...
    @property
    def config(self) -> configparser.ConfigParser: ...
    @property
    def connection(self) -> DbusConnectionManager: ...
    @property
    def json_writer(self) -> AtomicJsonWriter: ...
    def timed_dbus_operation(self, kind: str, operation: Callable[[], _T]) -> _T: ...


class PublicationExecutor(Protocol):  # pragma: no cover
    """Apply one typed EVCS or companion publication."""

    def process(self, command: CommandMapping) -> CommandOutcome: ...


class SemanticOperationExecutor(Protocol):  # pragma: no cover
    """Execute one typed adapter-owned system operation."""

    def process_semantic_operation(
        self,
        command: CommandMapping,
        *,
        command_file: str,
    ) -> CommandOutcome: ...


class WriteSchedulerHealth(Protocol):  # pragma: no cover
    """Track queue budgets, lifecycle transitions, and scheduler health."""

    local_publish_burst_limit: int
    local_publish_tick_budget_seconds: float
    dynamic_local_publish_burst_limit: int
    last_processed_at: float

    def health(self, *, now: float | None = None) -> CommandPayload: ...
    def set_dynamic_local_publish_burst(
        self,
        burst: int,
        *,
        pressure_state: GatewayPressureState = "ok",
    ) -> None: ...
    def budget_available(self, command: CommandMapping, now: float) -> bool: ...
    def prune_budget(self, now: float) -> None: ...
    def prune_processed(self, now: float) -> None: ...
    def prioritized_commands(self, commands: CommandFileList) -> CommandFileList: ...
    def record_budget(self, command: CommandMapping) -> None: ...
    def record_lifecycle(self, command: CommandMapping, state: str) -> None: ...
    def record_processed(self) -> None: ...

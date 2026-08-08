# SPDX-License-Identifier: GPL-3.0-or-later
"""Explicit collaborator ports for the composed DBus adapter process."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING, Protocol, TypeVar

from venus_evcharger.dbus_adapter.contracts import (
    CommandCompletion,
    CommandExecution,
)
from venus_evcharger.ipc.command_types import CommandMapping, CommandPayload
from venus_evcharger.ipc.energy import EnergyTopologySnapshot

if TYPE_CHECKING:
    from venus_evcharger.dbus_adapter.process.health import GatewayControlSnapshot

_T = TypeVar("_T")


class RuntimeRole(Protocol):  # pragma: no cover
    def install_signal_handlers(self) -> None: ...


class SocketRole(Protocol):  # pragma: no cover
    def start_socket(self) -> None: ...
    def install_glib_watch(self) -> None: ...
    def close_socket(self) -> None: ...
    def process_socket_once(self) -> bool: ...


class PublicationRole(Protocol):  # pragma: no cover
    @property
    def evcs_service_registered(self) -> bool: ...

    @property
    def registered_publication_path_count(self) -> int: ...


class HealthRole(Protocol):  # pragma: no cover
    def append_health_log(self, health: Mapping[str, object]) -> None: ...
    def health_snapshot(self) -> CommandPayload: ...
    def control_snapshot(self) -> GatewayControlSnapshot: ...
    def apply_slo_regulation(
        self,
        snapshot: GatewayControlSnapshot | None = None,
    ) -> GatewayControlSnapshot: ...


class DiagnosticsRole(Protocol):  # pragma: no cover
    def write_gateway_diagnostics(
        self,
        *,
        health: Mapping[str, object],
        topology: EnergyTopologySnapshot,
        captured_at: float,
        captured_monotonic: float | None = None,
    ) -> None: ...


class IntrospectionSnapshotRole(Protocol):  # pragma: no cover
    def write_introspection_snapshot(self) -> None: ...


class IoRole(Protocol):  # pragma: no cover
    def poll_one_due_read_once(self) -> bool: ...
    def refresh_services_if_due_once(self) -> bool: ...
    def timed_local_publish(self, operation: Callable[[], _T]) -> _T: ...
    def publish_cache(self, control_snapshot: GatewayControlSnapshot | None = None) -> None: ...


class IntrospectionRole(Protocol):  # pragma: no cover
    def enqueue_background_introspection_if_due(self) -> None: ...
    def schedule_non_write_command(
        self,
        command: CommandMapping,
        command_file: str,
        completion: CommandCompletion,
    ) -> CommandExecution: ...


class LoopRole(Protocol):  # pragma: no cover
    def run(self) -> None: ...
    def tick(self) -> bool: ...

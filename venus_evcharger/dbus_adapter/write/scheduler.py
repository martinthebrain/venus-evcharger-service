# SPDX-License-Identifier: GPL-3.0-or-later
"""Write scheduler assembly for DBus gateway commands."""

from __future__ import annotations

from venus_evcharger.dbus_adapter.contracts import CommandOutcome
from venus_evcharger.dbus_adapter.health.slo import GatewayPressureState
from venus_evcharger.dbus_adapter.write.core import WriteCommandQueue
from venus_evcharger.dbus_adapter.write.dispatch import WriteCommandDispatcher
from venus_evcharger.dbus_adapter.write.health import WriteSchedulerHealthTracker
from venus_evcharger.dbus_adapter.write.protocols import DbusWriteSchedulerAdapter
from venus_evcharger.dbus_adapter.write.publish import GatewayPublicationExecutor
from venus_evcharger.dbus_adapter.write.semantic import SemanticWriteExecutor
from venus_evcharger.ipc.command_types import CommandMapping, CommandPayload
from venus_evcharger.ipc.pending_snapshot import PendingCommandSnapshot


class DbusWriteScheduler:
    """Facade assembling independent write-scheduler components."""

    def __init__(self, adapter: DbusWriteSchedulerAdapter) -> None:
        self.publication_executor = GatewayPublicationExecutor(adapter.publication_registry)
        self.semantic_executor = SemanticWriteExecutor(adapter)
        self.health_tracker = WriteSchedulerHealthTracker(adapter)
        self.command_dispatcher = WriteCommandDispatcher(
            adapter,
            publication=self.publication_executor,
            semantic=self.semantic_executor,
        )
        self.command_queue = WriteCommandQueue(
            adapter,
            dispatcher=self.command_dispatcher,
            health=self.health_tracker,
        )

    def health(self, *, now: float | None = None) -> CommandPayload:
        payload = dict(self.health_tracker.health(now=now))
        payload["fast_publication_queue"] = self.command_queue.adapter.fast_publications.snapshot()
        return payload

    def set_dynamic_local_publish_burst(
        self,
        burst: int,
        *,
        pressure_state: GatewayPressureState = "ok",
    ) -> None:
        self.health_tracker.set_dynamic_local_publish_burst(burst, pressure_state=pressure_state)

    def process_one(
        self,
        *,
        include_local_publish: bool = True,
        required_kind: str | None = None,
    ) -> bool:
        return self.command_queue.process_one(
            include_local_publish=include_local_publish,
            required_kind=required_kind,
        )

    def process_urgent_once(self) -> bool:
        return self.command_queue.process_urgent_once()

    def process_local_publish_burst(self, limit: int | None = None) -> int:
        return self.command_queue.process_local_publish_burst(limit)

    def begin_tick(self) -> PendingCommandSnapshot:
        return self.command_queue.begin_tick()

    def end_tick(self) -> None:
        self.command_queue.end_tick()

    def pending_snapshot(self) -> PendingCommandSnapshot:
        return self.command_queue.pending_snapshot()

    def remove_pending(self, path: str, expected: CommandMapping) -> bool:
        return self.command_queue.remove_pending(path, expected)

    def process_command(self, command: CommandMapping, *, command_file: str = "") -> CommandOutcome:
        return self.command_dispatcher.process(command, command_file=command_file)

    def record_lifecycle(self, command: CommandMapping, state: str) -> None:
        self.health_tracker.record_lifecycle(command, state)

    @property
    def local_publish_burst_limit(self) -> int:
        return self.health_tracker.local_publish_burst_limit

    @property
    def last_scheduled_outcome(self) -> CommandOutcome | None:
        return self.command_queue.last_scheduled_outcome

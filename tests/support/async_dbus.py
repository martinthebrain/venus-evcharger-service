# SPDX-License-Identifier: GPL-3.0-or-later
"""Deterministic callback helpers for gateway operation contract tests."""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol
from unittest.mock import MagicMock

from venus_evcharger.dbus_adapter.async_broker import DbusAsyncOperation
from venus_evcharger.dbus_adapter.contracts import CommandExecution, CommandOutcome
from venus_evcharger.dbus_adapter.rate import DbusOperationDeferred
from venus_evcharger.ipc.command_types import CommandMapping


class ReadExecutorLike(Protocol):
    last_operation_performed: bool


class AdapterWithReadExecutor(Protocol):
    read_executor: ReadExecutorLike


class NonWriteScheduler(Protocol):
    def schedule_non_write_command(
        self,
        command: CommandMapping,
        command_file: str,
        completion: Callable[[CommandOutcome], None],
    ) -> CommandExecution: ...


class AdapterWithNonWriteScheduler(Protocol):
    introspection_role: NonWriteScheduler


class SemanticScheduler(Protocol):
    def schedule_semantic_operation(
        self,
        command: CommandMapping,
        *,
        command_file: str,
        completion: Callable[[CommandOutcome], None],
    ) -> CommandExecution: ...


class ImmediateAsyncBroker:
    """Execute a callback-style operation immediately in the calling test."""

    def __init__(self, operation_kinds: list[str] | None = None) -> None:
        self.operations: list[DbusAsyncOperation] = []
        self._operation_kinds = operation_kinds

    @property
    def busy(self) -> bool:
        return False

    def submit(self, operation: DbusAsyncOperation) -> int:
        self.operations.append(operation)
        if self._operation_kinds is not None:
            self._operation_kinds.append(operation.metric_kind)
        try:
            operation.starter(operation.on_success, operation.on_error)
        except Exception as error:
            operation.on_error(error)
        return len(self.operations)


def run_semantic_operation(
    executor: SemanticScheduler,
    command: CommandMapping,
    *,
    command_file: str,
) -> CommandOutcome:
    """Return the immediate callback outcome of one semantic test operation."""
    completed: list[CommandOutcome] = []
    execution = executor.schedule_semantic_operation(
        command,
        command_file=command_file,
        completion=completed.append,
    )
    return completed[-1] if completed else execution.outcome


def completed_execution(
    schedule: Callable[[Callable[[CommandOutcome], None]], CommandExecution],
) -> CommandOutcome:
    """Capture a synchronously completed generic schedule operation."""
    completed: list[CommandOutcome] = []
    execution = schedule(completed.append)
    return completed[-1] if completed else execution.outcome


def run_non_write_command(
    adapter: AdapterWithNonWriteScheduler,
    command: CommandMapping,
) -> CommandOutcome:
    """Return the immediate outcome of one non-write gateway command."""
    return completed_execution(
        lambda completion: adapter.introspection_role.schedule_non_write_command(
            command,
            "command.json",
            completion,
        )
    )


def install_read_responder(
    adapter: AdapterWithReadExecutor,
    responder: Callable[[str, str], object],
) -> MagicMock:
    """Replace only the transport edge while exercising real read semantics."""

    def _submit(
        service: str,
        path: str,
        *,
        on_success: Callable[[object], None],
        on_error: Callable[[BaseException], None],
        optional: bool = False,
    ) -> None:
        del optional
        try:
            value = responder(service, path)
        except DbusOperationDeferred:
            raise
        except Exception as error:
            on_error(error)
        else:
            on_success(value)
        adapter.read_executor.last_operation_performed = True

    mock = MagicMock(side_effect=_submit)
    setattr(adapter.read_executor, "_submit_busitem", mock)
    return mock


__all__ = [
    "ImmediateAsyncBroker",
    "completed_execution",
    "install_read_responder",
    "run_non_write_command",
    "run_semantic_operation",
]

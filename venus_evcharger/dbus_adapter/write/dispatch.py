# SPDX-License-Identifier: GPL-3.0-or-later
"""Policy and routing for one gateway command execution."""

from __future__ import annotations

import logging

from venus_evcharger.dbus_adapter.contracts import (
    CommandCompletion,
    CommandExecution,
    CommandOutcome,
)
from venus_evcharger.dbus_adapter.rate import DbusOperationDeferred
from venus_evcharger.dbus_adapter.write.protocols import (
    DbusWriteSchedulerAdapter,
    PublicationExecutor,
    SemanticOperationExecutor,
)
from venus_evcharger.dbus_adapter.write.support import command_kind
from venus_evcharger.ipc.command_types import CommandMapping
from venus_evcharger.ipc.gateway_operations import SEMANTIC_GATEWAY_KINDS
from venus_evcharger.ipc.gateway_publication import SEMANTIC_PUBLICATION_KINDS
from venus_evcharger.ipc.generic_shelly_configuration import DISABLE_MATCHING_GENERIC_SHELLY_ONCE_KIND

GATEWAY_COMMAND_RETRY_ERRORS = (KeyError, OSError, RuntimeError, TypeError, ValueError)


class WriteCommandDispatcher:
    """Apply circuit policy and route one command to its typed executor."""

    def __init__(
        self,
        adapter: DbusWriteSchedulerAdapter,
        *,
        publication: PublicationExecutor,
        semantic: SemanticOperationExecutor,
    ) -> None:
        self.adapter = adapter
        self.publication = publication
        self.semantic = semantic

    def process(
        self,
        command: CommandMapping,
        *,
        command_file: str = "",
    ) -> CommandOutcome:
        """Process a direct caller command without durable-queue retirement."""
        return self.schedule(
            command,
            command_file=command_file,
            completion=lambda _outcome: None,
        ).outcome

    def schedule(
        self,
        command: CommandMapping,
        *,
        command_file: str,
        completion: CommandCompletion,
    ) -> CommandExecution:
        """Start one permitted command or report immediate backpressure."""
        if self._command_blocked(command):
            return CommandExecution.immediate("deferred")
        return self._dispatch(
            command,
            command_file=command_file,
            completion=completion,
        )

    def outcome(self, path: str, command: CommandMapping) -> CommandOutcome:
        """Return the immediate outcome used by non-durable fast publications."""
        return self.execute(
            path,
            command,
            completion=lambda _outcome: None,
        ).outcome

    def execute(
        self,
        path: str,
        command: CommandMapping,
        *,
        completion: CommandCompletion,
    ) -> CommandExecution:
        """Schedule a durable command and classify transient execution failures."""
        try:
            return self.schedule(
                command,
                command_file=path,
                completion=completion,
            )
        except DbusOperationDeferred:
            return CommandExecution.immediate("deferred")
        except GATEWAY_COMMAND_RETRY_ERRORS as error:
            logging.exception("Gateway command failed; keeping for retry path=%s: %s", path, error)
            return CommandExecution.immediate("deferred")

    def _command_blocked(self, command: CommandMapping) -> bool:
        priority = str(command.get("priority") or "diagnostic")
        return not self.adapter.circuit.allows_priority(priority)

    def _dispatch(
        self,
        command: CommandMapping,
        *,
        command_file: str,
        completion: CommandCompletion,
    ) -> CommandExecution:
        kind = command_kind(command)
        if kind in SEMANTIC_PUBLICATION_KINDS:
            return CommandExecution.immediate(self.publication.process(command))
        if kind in SEMANTIC_GATEWAY_KINDS or kind == DISABLE_MATCHING_GENERIC_SHELLY_ONCE_KIND:
            return self.semantic.schedule_semantic_operation(
                command,
                command_file=command_file,
                completion=completion,
            )
        return self.adapter.schedule_non_write_command(
            command,
            command_file,
            completion,
        )

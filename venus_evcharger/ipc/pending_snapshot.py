# SPDX-License-Identifier: GPL-3.0-or-later
"""Tick-local immutable views of a file command mailbox."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType

from venus_evcharger.ipc.command_mailbox import CommandMailboxReader
from venus_evcharger.ipc.command_types import (
    CommandFile,
    CommandFileList,
    CommandMapping,
)


@dataclass(frozen=True, slots=True)
class PendingCommandSnapshot:
    """One decoded physical/effective mailbox view."""

    physical: tuple[CommandFile, ...]
    effective: tuple[CommandFile, ...]

    @classmethod
    def capture(cls, mailbox: CommandMailboxReader) -> PendingCommandSnapshot:
        loaded = mailbox.load_pending()
        coalesced = mailbox.coalesce(loaded)
        return cls(_freeze_commands(loaded), _freeze_commands(coalesced))

    def physical_list(self) -> CommandFileList:
        return list(self.physical)

    def effective_list(self) -> CommandFileList:
        return list(self.effective)

    def without_paths(self, paths: frozenset[str]) -> PendingCommandSnapshot:
        if not paths:
            return self
        return PendingCommandSnapshot(
            _commands_without_paths(self.physical, paths),
            _commands_without_paths(self.effective, paths),
        )


class TickPendingSnapshotProvider:
    """Reuse one mailbox decode only while an explicitly bounded tick is active."""

    def __init__(self, mailbox: CommandMailboxReader) -> None:
        self._mailbox = mailbox
        self._active = False
        self._snapshot: PendingCommandSnapshot | None = None

    def begin_tick(self) -> PendingCommandSnapshot:
        self._active = True
        self._snapshot = PendingCommandSnapshot.capture(self._mailbox)
        return self._snapshot

    def end_tick(self) -> None:
        self._snapshot = None
        self._active = False

    def snapshot(self) -> PendingCommandSnapshot:
        if self._active and self._snapshot is not None:
            return self._snapshot
        return PendingCommandSnapshot.capture(self._mailbox)

    def remove(self, path: str, expected: CommandMapping) -> bool:
        removed = self._mailbox.remove_if_current(path, expected)
        if self._active and self._snapshot is not None:
            self._snapshot = self._snapshot.without_paths(frozenset((path,)))
        return removed


def _freeze_commands(commands: CommandFileList) -> tuple[CommandFile, ...]:
    return tuple(
        (path, MappingProxyType(dict(command)))
        for path, command in commands
    )


def _commands_without_paths(
    commands: tuple[CommandFile, ...],
    paths: frozenset[str],
) -> tuple[CommandFile, ...]:
    return tuple(item for item in commands if item[0] not in paths)


__all__ = ["PendingCommandSnapshot", "TickPendingSnapshotProvider"]

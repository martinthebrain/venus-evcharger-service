# SPDX-License-Identifier: GPL-3.0-or-later
"""Atomic JSON-file command mailbox shared by independent processes.

The transport owns locking, atomic replacement, conditional retirement, and
RAM-only quarantine. Domain-specific validation and ordering remain in queue
policies, so corruption handling cannot accidentally reinterpret commands.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import logging
import os
import stat
import time
import uuid
from collections import OrderedDict
from collections.abc import Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Generator, Protocol, TypeGuard, TypeVar, runtime_checkable

from venus_evcharger.core.shared import compact_json, write_text_atomically
from venus_evcharger.ipc.command_types import (
    CommandFile,
    CommandFileList,
    CommandMapping,
    CommandOrderKey,
    CommandPayload,
)

COMMAND_PRIORITY_RANKS = {
    "safety": 0,
    "user": 1,
    "publish": 2,
    "read": 3,
    "normal": 4,
    "optional": 5,
    "discovery": 5,
    "diagnostic": 6,
}
MAILBOX_LOCK_TIMEOUT_SECONDS = 0.25
MAILBOX_LOCK_RETRY_SECONDS = 0.005
MAILBOX_REVISION_FIELD = "mailbox_revision"
MAILBOX_ERROR_LOG_INTERVAL_SECONDS = 60.0
MAX_MAILBOX_QUARANTINE_ENTRIES = 128


class MailboxLockTimeout(TimeoutError):
    """Raised when another process holds a mailbox lock for too long."""


class MailboxScanUnavailable(RuntimeError):
    """Raised when mailbox contents cannot be determined reliably."""

    def __init__(self, command_dir: str, error: OSError) -> None:
        super().__init__(f"Command mailbox scan unavailable path={command_dir}: {error}")
        self.command_dir = command_dir
        self.error = error


@dataclass(frozen=True, slots=True)
class _QuarantinedCommandFile:
    signature: tuple[int, int, int] | None
    reason: str


@runtime_checkable
class CommandMailboxReader(Protocol):  # pragma: no cover
    """Consumer surface for a command mailbox."""

    def load_pending(self) -> CommandFileList: ...
    def coalesce(self, commands: CommandFileList) -> CommandFileList: ...
    def remove_if_current(self, path: str, expected: CommandMapping) -> bool: ...


class CommandMailboxWriter(Protocol):  # pragma: no cover
    """Producer surface for a command mailbox."""

    def enqueue(self, command: CommandMapping) -> str: ...


class CommandMailbox(Protocol):  # pragma: no cover
    """Bidirectional command mailbox surface."""

    def enqueue(self, command: CommandMapping) -> str: ...
    def load_pending(self) -> CommandFileList: ...
    def coalesce(self, commands: CommandFileList) -> CommandFileList: ...
    def remove(self, path: str) -> None: ...
    def remove_if_current(self, path: str, expected: CommandMapping) -> bool: ...


class CommandQueuePolicy(Protocol):  # pragma: no cover
    """Semantic policy applied by the neutral file transport."""

    schema_version: int

    def normalize(self, command: CommandMapping) -> CommandPayload: ...
    def queue_class(self, command: CommandMapping) -> str: ...
    def merge_coalesced(self, existing: CommandMapping | None, payload: CommandPayload) -> bool: ...
    def order_key(self, command: CommandMapping) -> CommandOrderKey: ...


class FileCommandMailbox:
    """Atomic command directory with priority-aware latest-wins semantics."""

    def __init__(
        self,
        command_dir: str,
        *,
        policy: CommandQueuePolicy,
        lock_timeout_seconds: float = MAILBOX_LOCK_TIMEOUT_SECONDS,
    ) -> None:
        self.command_dir = command_dir
        self._policy = policy
        self._lock_timeout_seconds = max(0.0, float(lock_timeout_seconds))
        self._lock_path = os.path.join(command_dir, ".mailbox.lock")
        self._quarantined: OrderedDict[str, _QuarantinedCommandFile] = OrderedDict()
        self._last_directory_error_log_at: float | None = None

    def enqueue(self, command: CommandMapping) -> str:
        os.makedirs(self.command_dir, exist_ok=True)
        normalized = self._policy.normalize(command)
        command_id = command_file_id(normalized)
        payload = self._new_payload(command_id, normalized)
        target = os.path.join(self.command_dir, f"{command_id}.json")
        with self._locked():
            if self._keep_existing_coalesced(normalized, target, payload):
                return target
            payload.setdefault("lifecycle_state", "queued")
            write_command_json(target, payload)
        return target

    def _new_payload(self, command_id: str, normalized: CommandMapping) -> CommandPayload:
        payload: CommandPayload = dict(normalized)
        payload.update(
            {
                "schema_version": self._policy.schema_version,
                "id": command_id,
                "created_at": command_float(normalized.get("created_at")) or _now(),
                MAILBOX_REVISION_FIELD: uuid.uuid4().hex,
                "queue_class": self._policy.queue_class(normalized),
            }
        )
        return payload

    def _keep_existing_coalesced(
        self,
        normalized: CommandMapping,
        target: str,
        payload: CommandPayload,
    ) -> bool:
        if not coalesced_target_exists(normalized, target):
            return False
        existing = normalized_mapping(read_command_json(target))
        merged = self._policy.merge_coalesced(existing, payload)
        if not merged and existing is not None and not should_replace_command(existing, payload):
            return True
        mark_coalesced_payload(existing, payload)
        return False

    def load_pending(self) -> CommandFileList:
        try:
            if not self._command_directory_available():
                return []
            with self._locked():
                return self._load_pending_unlocked()
        except (MailboxLockTimeout, MailboxScanUnavailable):
            raise
        except PermissionError:
            # Read-only observers cannot create the advisory lock file. They
            # may still inspect atomically written command files safely.
            return self._load_pending_unlocked()
        except OSError as error:
            raise self._scan_unavailable(error) from error

    def _command_directory_available(self) -> bool:
        try:
            mode = os.stat(self.command_dir).st_mode
        except FileNotFoundError:
            self._quarantined.clear()
            return False
        except OSError as error:
            raise self._scan_unavailable(error) from error
        if stat.S_ISDIR(mode):
            return True
        shape_error = NotADirectoryError(self.command_dir)
        raise self._scan_unavailable(shape_error) from shape_error

    def _load_pending_unlocked(self) -> CommandFileList:
        """Load valid files while unchanged corrupt generations stay isolated."""
        try:
            paths = sorted(Path(self.command_dir).glob("*.json"))
        except OSError as error:
            raise self._scan_unavailable(error) from error
        self._forget_absent_quarantine_entries(paths)
        pending: CommandFileList = []
        for path in paths:
            command = self._load_command_file(path)
            if command is not None:
                pending.append(command)
        return pending

    def _load_command_file(self, path: Path) -> CommandFile | None:
        path_text = str(path)
        signature = _command_file_signature(path_text)
        if self._is_unchanged_quarantined(path_text, signature):
            return None
        try:
            raw_payload = _read_command_json(path_text)
        except (OSError, json.JSONDecodeError) as error:
            reason = f"{type(error).__name__}: {error}"
            self._quarantine(path_text, signature, reason)
            return None
        payload = normalized_mapping(raw_payload)
        if payload is None:
            self._quarantine(path_text, signature, "payload-is-not-object")
            return None
        self._quarantined.pop(path_text, None)
        return path_text, self._policy.normalize(payload)

    def _forget_absent_quarantine_entries(self, paths: list[Path]) -> None:
        present = {str(path) for path in paths}
        for path in tuple(self._quarantined):
            if path not in present:
                self._quarantined.pop(path)

    def _is_unchanged_quarantined(
        self,
        path: str,
        signature: tuple[int, int, int] | None,
    ) -> bool:
        entry = self._quarantined.get(path)
        return entry is not None and entry.signature == signature

    def _quarantine(
        self,
        path: str,
        signature: tuple[int, int, int] | None,
        reason: str,
    ) -> None:
        """Remember only the newest bounded set of invalid file generations."""
        entry = _QuarantinedCommandFile(signature=signature, reason=reason)
        if self._quarantined.get(path) == entry:
            return
        self._quarantined[path] = entry
        self._quarantined.move_to_end(path)
        while len(self._quarantined) > MAX_MAILBOX_QUARANTINE_ENTRIES:
            oldest = next(iter(self._quarantined))
            self._quarantined.pop(oldest)
        logging.warning("Quarantined unreadable command file path=%s reason=%s", path, reason)

    def _record_directory_error(self, error: OSError) -> None:
        """Rate-limit repeated scan failures while retaining no disk state."""
        now = time.monotonic()
        if (
            self._last_directory_error_log_at is not None
            and now - self._last_directory_error_log_at < MAILBOX_ERROR_LOG_INTERVAL_SECONDS
        ):
            return
        self._last_directory_error_log_at = now
        logging.warning("Command mailbox directory scan failed path=%s error=%s", self.command_dir, error)

    def _scan_unavailable(self, error: OSError) -> MailboxScanUnavailable:
        self._record_directory_error(error)
        return MailboxScanUnavailable(self.command_dir, error)

    def remove(self, path: str) -> None:
        os.makedirs(self.command_dir, exist_ok=True)
        with self._locked():
            self._remove_unlocked(path)

    def remove_if_current(self, path: str, expected: CommandMapping) -> bool:
        os.makedirs(self.command_dir, exist_ok=True)
        with self._locked():
            current = normalized_mapping(read_command_json(path))
            if current is None or not _same_mailbox_revision(current, expected):
                return False
            self._remove_unlocked(path)
            return True

    @staticmethod
    def _remove_unlocked(path: str) -> None:
        try:
            os.unlink(path)
        except FileNotFoundError:
            return

    def remove_coalesced(self, coalesce_key: str) -> int:
        normalized_key = coalesce_key.strip()
        if not normalized_key:
            return 0
        os.makedirs(self.command_dir, exist_ok=True)
        with self._locked():
            paths = self._coalesced_paths_unlocked(normalized_key)
            for path in paths:
                self._remove_unlocked(path)
        return len(paths)

    def _coalesced_paths_unlocked(self, coalesce_key: str) -> list[str]:
        return [
            path
            for path, command in self._load_pending_unlocked()
            if str(command.get("coalesce_key") or "") == coalesce_key
        ]

    def coalesce(self, commands: CommandFileList) -> CommandFileList:
        """Return the priority-aware latest command for every coalesce key."""
        selected: OrderedDict[str, CommandFile] = OrderedDict()
        passthrough: CommandFileList = []
        for path, command in commands:
            key = str(command.get("coalesce_key") or "")
            if not key:
                passthrough.append((path, command))
                continue
            selected[key] = select_coalesced_command(selected.get(key), (path, command))
        combined = passthrough + list(selected.values())
        return sorted(combined, key=lambda item: self._policy.order_key(item[1]))

    @contextmanager
    def _locked(self) -> Generator[None, None, None]:
        descriptor = os.open(
            self._lock_path,
            os.O_CREAT | os.O_RDWR | os.O_CLOEXEC,
            0o600,
        )
        try:
            _acquire_lock(descriptor, self._lock_timeout_seconds)
            try:
                yield
            finally:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def command_priority_rank(priority: object) -> int:
    if priority is None:
        return COMMAND_PRIORITY_RANKS["diagnostic"]
    normalized = str(priority).strip().lower()
    if not normalized:
        return COMMAND_PRIORITY_RANKS["diagnostic"]
    return COMMAND_PRIORITY_RANKS.get(normalized, COMMAND_PRIORITY_RANKS["diagnostic"])


def command_float(value: object) -> float:
    if not isinstance(value, (str, bytes, int, float)):
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def command_file_id(command: CommandMapping) -> str:
    coalesce_key = str(command.get("coalesce_key") or "").strip()
    if coalesce_key:
        digest = hashlib.sha256(coalesce_key.encode()).hexdigest()[:24]
        return f"coalesced-{digest}"
    return f"cmd-{time.time_ns()}-{uuid.uuid4().hex[:8]}"


def should_replace_command(existing: CommandMapping, candidate: CommandMapping) -> bool:
    existing_rank = command_priority_rank(existing.get("priority"))
    candidate_rank = command_priority_rank(candidate.get("priority"))
    existing_key = (existing_rank, -command_float(existing.get("created_at")))
    candidate_key = (candidate_rank, -command_float(candidate.get("created_at")))
    return candidate_key <= existing_key


def select_coalesced_command(existing: CommandFile | None, candidate: CommandFile) -> CommandFile:
    if existing is None:
        return candidate
    return candidate if should_replace_command(existing[1], candidate[1]) else existing


def coalesced_target_exists(command: CommandMapping, target: str) -> bool:
    return bool(str(command.get("coalesce_key") or "").strip()) and os.path.exists(target)


def mark_coalesced_payload(existing: CommandMapping | None, payload: CommandPayload) -> None:
    payload["lifecycle_state"] = "coalesced"
    if existing is None or not same_command_priority(existing, payload):
        return
    payload["created_at"] = command_float(existing.get("created_at")) or payload["created_at"]
    payload["updated_at"] = _now()


def same_command_priority(first: CommandMapping, second: CommandMapping) -> bool:
    return command_priority_rank(first.get("priority")) == command_priority_rank(second.get("priority"))


def _same_mailbox_revision(current: CommandMapping, expected: CommandMapping) -> bool:
    expected_revision = expected.get(MAILBOX_REVISION_FIELD)
    current_revision = current.get(MAILBOX_REVISION_FIELD)
    if isinstance(expected_revision, str) and expected_revision:
        return bool(current_revision == expected_revision)
    return bool(current == expected)


def _acquire_lock(descriptor: int, timeout_seconds: float) -> None:
    deadline = time.monotonic() + timeout_seconds
    while True:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return
        except BlockingIOError as error:
            if time.monotonic() >= deadline:
                raise MailboxLockTimeout("Command mailbox lock timed out") from error
            time.sleep(MAILBOX_LOCK_RETRY_SECONDS)


def normalized_mapping(value: object) -> CommandPayload | None:
    if not _is_mapping(value):
        return None
    return {str(key): item for key, item in value.items()}


def read_command_json(path: str) -> object:
    try:
        return _read_command_json(path)
    except (OSError, json.JSONDecodeError):
        return None


def _read_command_json(path: str) -> object:
    with open(path, encoding="utf-8") as handle:
        payload: object = json.load(handle)
    return payload


def _command_file_signature(path: str) -> tuple[int, int, int] | None:
    try:
        status = os.stat(path)
    except OSError:
        return None
    return status.st_ino, status.st_size, status.st_mtime_ns


def write_command_json(path: str, payload: CommandMapping) -> None:
    write_text_atomically(path, compact_json(_json_ready_mapping(payload)) + "\n")


def _now() -> float:
    return time.time()


def _is_mapping(value: object) -> TypeGuard[Mapping[object, object]]:
    return isinstance(value, Mapping)


def _json_ready(value: object) -> object:
    if _is_json_scalar(value):
        return value
    return _json_ready_container(value)


def _json_ready_container(value: object) -> object:
    if _is_mapping(value):
        return _json_ready_mapping(value)
    if _is_object_list(value) or _is_object_tuple(value):
        return [_json_ready(item) for item in value]
    return str(value)


def _is_json_scalar(value: object) -> bool:
    return isinstance(value, (str, int, float, bool)) or value is None


def _is_object_list(value: object) -> TypeGuard[list[object]]:
    return isinstance(value, list)


def _is_object_tuple(value: object) -> TypeGuard[tuple[object, ...]]:
    return isinstance(value, tuple)


_MappingKey = TypeVar("_MappingKey")


def _json_ready_mapping(value: Mapping[_MappingKey, object]) -> CommandPayload:
    return {str(key): _json_ready(item) for key, item in value.items()}

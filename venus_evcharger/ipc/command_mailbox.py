# SPDX-License-Identifier: GPL-3.0-or-later
"""Atomic JSON-file command mailbox shared by independent processes."""

from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from collections import OrderedDict
from collections.abc import Mapping
from pathlib import Path
from typing import Protocol, TypeGuard, TypeVar

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


class CommandMailboxReader(Protocol):  # pragma: no cover
    """Consumer surface for a command mailbox."""

    def load_pending(self) -> CommandFileList: ...
    def coalesce(self, commands: CommandFileList) -> CommandFileList: ...
    def remove(self, path: str) -> None: ...


class CommandMailboxWriter(Protocol):  # pragma: no cover
    """Producer surface for a command mailbox."""

    def enqueue(self, command: CommandMapping) -> str: ...


class CommandMailbox(Protocol):  # pragma: no cover
    """Bidirectional command mailbox surface."""

    def enqueue(self, command: CommandMapping) -> str: ...
    def load_pending(self) -> CommandFileList: ...
    def coalesce(self, commands: CommandFileList) -> CommandFileList: ...
    def remove(self, path: str) -> None: ...


class CommandQueuePolicy(Protocol):  # pragma: no cover
    """Semantic policy applied by the neutral file transport."""

    schema_version: int

    def normalize(self, command: CommandMapping) -> CommandPayload: ...
    def queue_class(self, command: CommandMapping) -> str: ...
    def merge_coalesced(self, existing: CommandMapping | None, payload: CommandPayload) -> None: ...
    def order_key(self, command: CommandMapping) -> CommandOrderKey: ...


class FileCommandMailbox:
    """Atomic command directory with priority-aware latest-wins semantics."""

    def __init__(self, command_dir: str, *, policy: CommandQueuePolicy) -> None:
        self.command_dir = command_dir
        self._policy = policy

    def enqueue(self, command: CommandMapping) -> str:
        os.makedirs(self.command_dir, exist_ok=True)
        normalized = self._policy.normalize(command)
        command_id = command_file_id(normalized)
        payload = self._new_payload(command_id, normalized)
        target = os.path.join(self.command_dir, f"{command_id}.json")
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
        if existing is not None and not should_replace_command(existing, payload):
            return True
        self._policy.merge_coalesced(existing, payload)
        mark_coalesced_payload(existing, payload)
        return False

    def load_pending(self) -> CommandFileList:
        try:
            paths = sorted(Path(self.command_dir).glob("*.json"))
        except OSError:
            return []
        pending: CommandFileList = []
        for path in paths:
            payload = normalized_mapping(read_command_json(str(path)))
            if payload is not None:
                pending.append((str(path), self._policy.normalize(payload)))
        return pending

    @staticmethod
    def remove(path: str) -> None:
        try:
            os.unlink(path)
        except FileNotFoundError:
            return

    def remove_coalesced(self, coalesce_key: str) -> int:
        normalized_key = coalesce_key.strip()
        paths = self._coalesced_paths(normalized_key)
        for path in paths:
            self.remove(path)
        return len(paths)

    def _coalesced_paths(self, coalesce_key: str) -> list[str]:
        if not coalesce_key:
            return []
        return [
            path
            for path, command in self.load_pending()
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


def normalized_mapping(value: object) -> CommandPayload | None:
    if not _is_mapping(value):
        return None
    return {str(key): item for key, item in value.items()}


def read_command_json(path: str) -> object:
    try:
        with open(path, encoding="utf-8") as handle:
            payload: object = json.load(handle)
            return payload
    except (OSError, json.JSONDecodeError):
        return None


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
    if isinstance(value, (list, tuple)):
        return [_json_ready(item) for item in value]
    return str(value)


def _is_json_scalar(value: object) -> bool:
    return isinstance(value, (str, int, float, bool)) or value is None


_MappingKey = TypeVar("_MappingKey")


def _json_ready_mapping(value: Mapping[_MappingKey, object]) -> CommandPayload:
    return {str(key): _json_ready(item) for key, item in value.items()}

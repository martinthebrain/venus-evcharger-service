# SPDX-License-Identifier: GPL-3.0-or-later
"""DBus gateway command inbox and coalescing."""

from __future__ import annotations

import hashlib
import os
import time
import uuid
from collections import OrderedDict
from collections.abc import Mapping
from pathlib import Path

from venus_evcharger.dbus_gateway_command_types import CommandFile, CommandFileList, CommandMapping, CommandPayload
from venus_evcharger.dbus_gateway_core import (
    DBUS_GATEWAY_SCHEMA_VERSION,
    PUBLISH_PATH_RANKS,
    _now,
    float_or_zero,
    priority_rank,
    read_json_file,
    write_json_file,
)
from venus_evcharger.dbus_gateway_policy import command_queue_class


class DbusCommandInbox:
    """Atomic JSON command directory used for writes toward the DBus adapter."""

    def __init__(self, command_dir: str) -> None:
        self.command_dir = command_dir

    def enqueue(self, command: CommandMapping) -> str:
        os.makedirs(self.command_dir, exist_ok=True)
        normalized = self._normalized_command(command)
        command_id = self._command_id(normalized)
        payload = self._new_payload(command_id, normalized)
        target = os.path.join(self.command_dir, f"{command_id}.json")
        if self._merge_existing_coalesced_payload(normalized, target, payload) == "keep-existing":
            return target
        payload.setdefault("lifecycle_state", "queued")
        write_json_file(target, payload)
        return target

    @staticmethod
    def _new_payload(command_id: str, normalized: CommandMapping) -> CommandPayload:
        payload: CommandPayload = dict(normalized)
        payload.update({
            "schema_version": DBUS_GATEWAY_SCHEMA_VERSION,
            "id": command_id,
            "created_at": float_or_zero(normalized.get("created_at")) or _now(),
            "queue_class": command_queue_class(normalized),
        })
        return payload

    def _merge_existing_coalesced_payload(
        self,
        normalized: CommandMapping,
        target: str,
        payload: CommandPayload,
    ) -> str:
        if not _coalesced_target_exists(normalized, target):
            return "write-new"
        existing = read_json_file(target)
        if not _replace_existing_coalesced(existing, payload):
            return "keep-existing"
        _merge_coalesced_publish_payload(existing, payload)
        _mark_coalesced_payload(existing, payload)
        return "write-new"

    @staticmethod
    def _should_replace_existing(path: str, payload: CommandMapping) -> bool:
        existing = read_json_file(path)
        if not isinstance(existing, Mapping):
            return True
        return DbusCommandInbox._should_replace_existing_payload(existing, payload)

    @staticmethod
    def _should_replace_existing_payload(existing: CommandMapping, payload: CommandMapping) -> bool:
        existing_rank = priority_rank(existing.get("priority"))
        new_rank = priority_rank(payload.get("priority"))
        if new_rank < existing_rank:
            return True
        if new_rank > existing_rank:
            return False
        return float_or_zero(payload.get("created_at")) >= float_or_zero(existing.get("created_at"))

    @staticmethod
    def _normalized_command(command: CommandMapping) -> CommandPayload:
        payload = dict(command)
        if _command_kind(payload) == "refresh_services":
            payload["coalesce_key"] = "refresh:services"
        return payload

    @staticmethod
    def _command_id(command: CommandMapping) -> str:
        coalesce_key = str(command.get("coalesce_key") or "").strip()
        if coalesce_key:
            digest = hashlib.sha256(coalesce_key.encode()).hexdigest()[:24]
            return f"coalesced-{digest}"
        return f"cmd-{time.time_ns()}-{uuid.uuid4().hex[:8]}"

    def load_pending(self) -> CommandFileList:
        try:
            paths = sorted(Path(self.command_dir).glob("*.json"))
        except OSError:
            return []
        pending: CommandFileList = []
        for path in paths:
            payload = read_json_file(str(path))
            if isinstance(payload, dict):
                pending.append((str(path), self._normalized_command(payload)))
        return pending

    def remove(self, path: str) -> None:
        try:
            os.unlink(path)
        except FileNotFoundError:
            return

    def remove_coalesced(self, coalesce_key: str) -> int:
        normalized_key = str(coalesce_key or "").strip()
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

    @staticmethod
    def coalesce(commands: CommandFileList) -> CommandFileList:
        """Return commands with latest command per coalesce key, priority-aware."""
        selected: OrderedDict[str, CommandFile] = OrderedDict()
        passthrough: CommandFileList = []
        for path, command in commands:
            key = str(command.get("coalesce_key") or "")
            if not key:
                passthrough.append((path, command))
                continue
            if key not in selected:
                selected[key] = (path, command)
                continue
            selected[key] = _selected_coalesced_command(selected[key], (path, command))
        return sorted(passthrough + list(selected.values()), key=lambda item: _command_order_key(item[1]))


def _selected_coalesced_command(
    existing: CommandFile,
    candidate: CommandFile,
) -> CommandFile:
    old_path, old_command = existing
    path, command = candidate
    old_rank = priority_rank(old_command.get("priority"))
    new_rank = priority_rank(command.get("priority"))
    old_created = float_or_zero(old_command.get("created_at"))
    new_created = float_or_zero(command.get("created_at"))
    if new_rank < old_rank or (new_rank == old_rank and new_created >= old_created):
        return path, command
    return old_path, old_command


def _coalesced_target_exists(normalized: CommandMapping, target: str) -> bool:
    return bool(str(normalized.get("coalesce_key") or "").strip()) and os.path.exists(target)


def _replace_existing_coalesced(existing: object, payload: CommandMapping) -> bool:
    if not isinstance(existing, Mapping):
        return True
    return DbusCommandInbox._should_replace_existing_payload(existing, payload)


def _same_priority(existing: CommandMapping, payload: CommandMapping) -> bool:
    return priority_rank(existing.get("priority")) == priority_rank(payload.get("priority"))


def _same_kind(existing: CommandMapping, payload: CommandMapping) -> bool:
    return _command_kind(existing) == _command_kind(payload)


def _merge_coalesced_publish_payload(existing: object, payload: CommandPayload) -> None:
    _merge_publish_desired_paths(existing, payload)
    _merge_publish_fields(existing, payload)


def _merge_publish_desired_paths(existing: object, payload: CommandPayload) -> None:
    existing_paths, payload_paths = _coalesced_publish_path_maps(existing, payload)
    if existing_paths is None or payload_paths is None:
        return
    payload["paths"] = {**dict(existing_paths), **dict(payload_paths)}


def _coalesced_publish_path_maps(
    existing: object,
    payload: CommandMapping,
) -> tuple[CommandMapping | None, CommandMapping | None]:
    if not _same_publish_desired_payload(existing, payload):
        return None, None
    assert isinstance(existing, Mapping)
    return _path_mapping(existing), _path_mapping(payload)


def _same_publish_desired_payload(existing: object, payload: CommandMapping) -> bool:
    if not isinstance(existing, Mapping):
        return False
    if not (_same_kind(existing, payload) and _same_priority(existing, payload)):
        return False
    return _command_kind(payload) == "publish_desired"


def _merge_publish_fields(existing: object, payload: CommandPayload) -> None:
    existing_fields, payload_fields = _coalesced_publish_field_maps(existing, payload)
    if existing_fields is None or payload_fields is None:
        return
    payload["fields"] = {**dict(existing_fields), **dict(payload_fields)}


def _coalesced_publish_field_maps(
    existing: object,
    payload: CommandMapping,
) -> tuple[CommandMapping | None, CommandMapping | None]:
    if not _same_publish_fields_payload(existing, payload):
        return None, None
    assert isinstance(existing, Mapping)
    return _field_mapping(existing), _field_mapping(payload)


def _same_publish_fields_payload(existing: object, payload: CommandMapping) -> bool:
    if not isinstance(existing, Mapping):
        return False
    if not (_same_kind(existing, payload) and _same_priority(existing, payload)):
        return False
    return _command_kind(payload) == "publish_fields"


def _path_mapping(command: CommandMapping) -> CommandMapping | None:
    paths = command.get("paths")
    return paths if isinstance(paths, Mapping) else None


def _field_mapping(command: CommandMapping) -> CommandMapping | None:
    fields = command.get("fields")
    return fields if isinstance(fields, Mapping) else None


def _mark_coalesced_payload(existing: object, payload: CommandPayload) -> None:
    payload["lifecycle_state"] = "coalesced"
    if isinstance(existing, Mapping) and _same_priority(existing, payload):
        payload["created_at"] = float_or_zero(existing.get("created_at")) or payload["created_at"]
        payload["updated_at"] = _now()


def _command_order_key(command: CommandMapping) -> tuple[int, int, float, int, str]:
    return (
        priority_rank(command.get("priority")),
        _command_kind_rank(command),
        float_or_zero(command.get("created_at")),
        _publish_path_rank(command),
        str(command.get("id") or ""),
    )


def _command_kind_rank(command: CommandMapping) -> int:
    kind = _command_kind(command)
    if kind == "register_service":
        return 0
    if kind == "register_path":
        return 1
    return 2


def _publish_path_rank(command: CommandMapping) -> int:
    if not _ranked_publish_command(command):
        return 0
    return PUBLISH_PATH_RANKS.get(_command_text(command, "path"), 3)


def _ranked_publish_command(command: CommandMapping) -> bool:
    return _publish_priority(command) and _command_kind(command) == "publish_value"


def _publish_priority(command: CommandMapping) -> bool:
    return _command_text(command, "priority").strip().lower() == "publish"


def _command_kind(command: CommandMapping) -> str:
    return _command_text(command, "kind") or _command_text(command, "type")


def _command_text(command: CommandMapping, key: str) -> str:
    if key not in command:
        return ""
    return str(command[key] or "")

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
from typing import Any

from venus_evcharger.dbus_gateway_core import (
    DBUS_GATEWAY_SCHEMA_VERSION,
    PUBLISH_PATH_RANKS,
    _float_or_zero,
    _now,
    _priority_rank,
    read_json_file,
    write_json_file,
)
from venus_evcharger.dbus_gateway_policy import command_queue_class


class DbusCommandInbox:
    """Atomic JSON command directory used for writes toward the DBus adapter."""

    def __init__(self, command_dir: str) -> None:
        self.command_dir = command_dir

    def enqueue(self, command: Mapping[str, Any]) -> str:
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
    def _new_payload(command_id: str, normalized: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "schema_version": DBUS_GATEWAY_SCHEMA_VERSION,
            "id": command_id,
            "created_at": float(normalized.get("created_at", _now())),
            **dict(normalized),
            "queue_class": command_queue_class(normalized),
        }

    def _merge_existing_coalesced_payload(
        self,
        normalized: Mapping[str, Any],
        target: str,
        payload: dict[str, Any],
    ) -> str:
        if not _coalesced_target_exists(normalized, target):
            return "write-new"
        existing = read_json_file(target, {})
        if not _replace_existing_coalesced(existing, payload):
            return "keep-existing"
        _mark_coalesced_payload(existing, payload)
        return "write-new"

    @staticmethod
    def _should_replace_existing(path: str, payload: Mapping[str, Any]) -> bool:
        existing = read_json_file(path, {})
        if not isinstance(existing, Mapping):
            return True
        return DbusCommandInbox._should_replace_existing_payload(existing, payload)

    @staticmethod
    def _should_replace_existing_payload(existing: Mapping[str, Any], payload: Mapping[str, Any]) -> bool:
        existing_rank = _priority_rank(existing.get("priority"))
        new_rank = _priority_rank(payload.get("priority"))
        if new_rank < existing_rank:
            return True
        if new_rank > existing_rank:
            return False
        return _float_or_zero(payload.get("created_at")) >= _float_or_zero(existing.get("created_at"))

    @staticmethod
    def _normalized_command(command: Mapping[str, Any]) -> dict[str, Any]:
        payload = dict(command)
        kind = str(payload.get("kind") or payload.get("type") or "")
        if kind == "refresh_services":
            payload["coalesce_key"] = "refresh:services"
        return payload

    @staticmethod
    def _command_id(command: Mapping[str, Any]) -> str:
        coalesce_key = str(command.get("coalesce_key") or "").strip()
        if coalesce_key:
            digest = hashlib.sha256(coalesce_key.encode("utf-8")).hexdigest()[:24]
            return f"coalesced-{digest}"
        return f"cmd-{time.time_ns()}-{uuid.uuid4().hex[:8]}"

    def load_pending(self) -> list[tuple[str, dict[str, Any]]]:
        try:
            paths = sorted(Path(self.command_dir).glob("*.json"))
        except OSError:
            return []
        pending: list[tuple[str, dict[str, Any]]] = []
        for path in paths:
            payload = read_json_file(str(path), {})
            if isinstance(payload, dict):
                pending.append((str(path), self._normalized_command(payload)))
        return pending

    def remove(self, path: str) -> None:
        try:
            os.unlink(path)
        except FileNotFoundError:
            return

    def remove_coalesced(self, coalesce_key: str) -> int:
        removed = 0
        normalized_key = str(coalesce_key or "").strip()
        if not normalized_key:
            return removed
        for path, command in self.load_pending():
            if str(command.get("coalesce_key") or "") != normalized_key:
                continue
            self.remove(path)
            removed += 1
        return removed

    @staticmethod
    def coalesce(commands: list[tuple[str, dict[str, Any]]]) -> list[tuple[str, dict[str, Any]]]:
        """Return commands with latest command per coalesce key, priority-aware."""
        selected: OrderedDict[str, tuple[str, dict[str, Any]]] = OrderedDict()
        passthrough: list[tuple[str, dict[str, Any]]] = []
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
    existing: tuple[str, dict[str, Any]],
    candidate: tuple[str, dict[str, Any]],
) -> tuple[str, dict[str, Any]]:
    old_path, old_command = existing
    path, command = candidate
    old_rank = _priority_rank(old_command.get("priority"))
    new_rank = _priority_rank(command.get("priority"))
    old_created = _float_or_zero(old_command.get("created_at"))
    new_created = _float_or_zero(command.get("created_at"))
    if new_rank < old_rank or (new_rank == old_rank and new_created >= old_created):
        return path, command
    return old_path, old_command


def _coalesced_target_exists(normalized: Mapping[str, Any], target: str) -> bool:
    return bool(str(normalized.get("coalesce_key") or "").strip()) and os.path.exists(target)


def _replace_existing_coalesced(existing: object, payload: Mapping[str, Any]) -> bool:
    if not isinstance(existing, Mapping):
        return True
    return DbusCommandInbox._should_replace_existing_payload(existing, payload)


def _same_priority(existing: Mapping[str, Any], payload: Mapping[str, Any]) -> bool:
    return _priority_rank(existing.get("priority")) == _priority_rank(payload.get("priority"))


def _mark_coalesced_payload(existing: object, payload: dict[str, Any]) -> None:
    payload["lifecycle_state"] = "coalesced"
    if isinstance(existing, Mapping) and _same_priority(existing, payload):
        payload["created_at"] = _float_or_zero(existing.get("created_at")) or payload["created_at"]
        payload["updated_at"] = _now()


def _command_order_key(command: Mapping[str, Any]) -> tuple[int, int, float, int, str]:
    return (
        _priority_rank(command.get("priority")),
        _command_kind_rank(command),
        _float_or_zero(command.get("created_at")),
        _publish_path_rank(command),
        str(command.get("id") or ""),
    )


def _command_kind_rank(command: Mapping[str, Any]) -> int:
    kind = str(command.get("kind") or command.get("type") or "")
    if kind == "register_service":
        return 0
    if kind == "register_path":
        return 1
    return 2


def _publish_path_rank(command: Mapping[str, Any]) -> int:
    if not _ranked_publish_command(command):
        return 0
    return PUBLISH_PATH_RANKS.get(str(command.get("path") or ""), 3)


def _ranked_publish_command(command: Mapping[str, Any]) -> bool:
    return _publish_priority(command) and _command_kind(command) == "publish_value"


def _publish_priority(command: Mapping[str, Any]) -> bool:
    return str(command.get("priority") or "").strip().lower() == "publish"


def _command_kind(command: Mapping[str, Any]) -> str:
    return str(command.get("kind") or command.get("type") or "")

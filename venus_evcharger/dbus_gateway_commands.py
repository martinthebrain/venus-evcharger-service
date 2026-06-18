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
        normalized = self._normalized_command(command)  # pragma: no mutate
        command_id = self._command_id(normalized)  # pragma: no mutate
        payload = self._new_payload(command_id, normalized)  # pragma: no mutate
        target = os.path.join(self.command_dir, f"{command_id}.json")  # pragma: no mutate
        if self._merge_existing_coalesced_payload(normalized, target, payload) == "keep-existing":
            return target
        payload.setdefault("lifecycle_state", "queued")  # pragma: no mutate
        write_json_file(target, payload)
        return target

    @staticmethod
    def _new_payload(command_id: str, normalized: Mapping[str, Any]) -> dict[str, Any]:  # pragma: no mutate block
        return {
            "schema_version": DBUS_GATEWAY_SCHEMA_VERSION,  # pragma: no mutate
            "id": command_id,  # pragma: no mutate
            "created_at": float(normalized.get("created_at", _now())),  # pragma: no mutate
            **dict(normalized),
            "queue_class": command_queue_class(normalized),  # pragma: no mutate
        }

    def _merge_existing_coalesced_payload(
        self,
        normalized: Mapping[str, Any],
        target: str,
        payload: dict[str, Any],
    ) -> str:
        if not _coalesced_target_exists(normalized, target):
            return "write-new"  # pragma: no mutate
        existing = read_json_file(target, {})  # pragma: no mutate
        if not _replace_existing_coalesced(existing, payload):
            return "keep-existing"  # pragma: no mutate
        _mark_coalesced_payload(existing, payload)
        return "write-new"  # pragma: no mutate

    @staticmethod
    def _should_replace_existing(path: str, payload: Mapping[str, Any]) -> bool:
        existing = read_json_file(path, {})  # pragma: no mutate
        if not isinstance(existing, Mapping):
            return True
        return DbusCommandInbox._should_replace_existing_payload(existing, payload)  # pragma: no mutate

    @staticmethod
    def _should_replace_existing_payload(existing: Mapping[str, Any], payload: Mapping[str, Any]) -> bool:
        existing_rank = _priority_rank(existing.get("priority"))
        new_rank = _priority_rank(payload.get("priority"))
        if new_rank < existing_rank:
            return True  # pragma: no mutate
        if new_rank > existing_rank:
            return False  # pragma: no mutate
        return _float_or_zero(payload.get("created_at")) >= _float_or_zero(existing.get("created_at"))  # pragma: no mutate

    @staticmethod
    def _normalized_command(command: Mapping[str, Any]) -> dict[str, Any]:
        payload = dict(command)  # pragma: no mutate
        kind = str(payload.get("kind") or payload.get("type") or "")  # pragma: no mutate
        if kind == "refresh_services":
            payload["coalesce_key"] = "refresh:services"  # pragma: no mutate
        return payload  # pragma: no mutate

    @staticmethod
    def _command_id(command: Mapping[str, Any]) -> str:
        coalesce_key = str(command.get("coalesce_key") or "").strip()  # pragma: no mutate
        if coalesce_key:
            digest = hashlib.sha256(coalesce_key.encode("utf-8")).hexdigest()[:24]  # pragma: no mutate
            return f"coalesced-{digest}"  # pragma: no mutate
        return f"cmd-{time.time_ns()}-{uuid.uuid4().hex[:8]}"  # pragma: no mutate

    def load_pending(self) -> list[tuple[str, dict[str, Any]]]:  # pragma: no mutate block
        try:
            paths = sorted(Path(self.command_dir).glob("*.json"))  # pragma: no mutate
        except OSError:
            return []
        pending: list[tuple[str, dict[str, Any]]] = []
        for path in paths:
            payload = read_json_file(str(path), {})
            if isinstance(payload, dict):
                pending.append((str(path), self._normalized_command(payload)))  # pragma: no mutate
        return pending

    def remove(self, path: str) -> None:
        try:
            os.unlink(path)
        except FileNotFoundError:
            return

    def remove_coalesced(self, coalesce_key: str) -> int:
        normalized_key = str(coalesce_key or "").strip()  # pragma: no mutate
        paths = self._coalesced_paths(normalized_key)  # pragma: no mutate
        for path in paths:
            self.remove(path)
        return len(paths)  # pragma: no mutate

    def _coalesced_paths(self, coalesce_key: str) -> list[str]:  # pragma: no mutate block
        if not coalesce_key:
            return []  # pragma: no mutate
        return [
            path
            for path, command in self.load_pending()
            if str(command.get("coalesce_key") or "") == coalesce_key  # pragma: no mutate
        ]

    @staticmethod
    def coalesce(commands: list[tuple[str, dict[str, Any]]]) -> list[tuple[str, dict[str, Any]]]:
        """Return commands with latest command per coalesce key, priority-aware."""
        selected: OrderedDict[str, tuple[str, dict[str, Any]]] = OrderedDict()
        passthrough: list[tuple[str, dict[str, Any]]] = []
        for path, command in commands:
            key = str(command.get("coalesce_key") or "")  # pragma: no mutate
            if not key:
                passthrough.append((path, command))
                continue
            if key not in selected:
                selected[key] = (path, command)
                continue
            selected[key] = _selected_coalesced_command(selected[key], (path, command))
        return sorted(passthrough + list(selected.values()), key=lambda item: _command_order_key(item[1]))  # pragma: no mutate


def _selected_coalesced_command(
    existing: tuple[str, dict[str, Any]],
    candidate: tuple[str, dict[str, Any]],
) -> tuple[str, dict[str, Any]]:  # pragma: no mutate block
    old_path, old_command = existing  # pragma: no mutate
    path, command = candidate  # pragma: no mutate
    old_rank = _priority_rank(old_command.get("priority"))  # pragma: no mutate
    new_rank = _priority_rank(command.get("priority"))  # pragma: no mutate
    old_created = _float_or_zero(old_command.get("created_at"))  # pragma: no mutate
    new_created = _float_or_zero(command.get("created_at"))  # pragma: no mutate
    if new_rank < old_rank or (new_rank == old_rank and new_created >= old_created):
        return path, command  # pragma: no mutate
    return old_path, old_command  # pragma: no mutate


def _coalesced_target_exists(normalized: Mapping[str, Any], target: str) -> bool:
    return bool(str(normalized.get("coalesce_key") or "").strip()) and os.path.exists(target)  # pragma: no mutate


def _replace_existing_coalesced(existing: object, payload: Mapping[str, Any]) -> bool:
    if not isinstance(existing, Mapping):
        return True  # pragma: no mutate
    return DbusCommandInbox._should_replace_existing_payload(existing, payload)  # pragma: no mutate


def _same_priority(existing: Mapping[str, Any], payload: Mapping[str, Any]) -> bool:
    return _priority_rank(existing.get("priority")) == _priority_rank(payload.get("priority"))  # pragma: no mutate


def _mark_coalesced_payload(existing: object, payload: dict[str, Any]) -> None:
    payload["lifecycle_state"] = "coalesced"  # pragma: no mutate
    if isinstance(existing, Mapping) and _same_priority(existing, payload):
        payload["created_at"] = _float_or_zero(existing.get("created_at")) or payload["created_at"]  # pragma: no mutate
        payload["updated_at"] = _now()  # pragma: no mutate


def _command_order_key(command: Mapping[str, Any]) -> tuple[int, int, float, int, str]:  # pragma: no mutate block
    return (
        _priority_rank(command.get("priority")),  # pragma: no mutate
        _command_kind_rank(command),  # pragma: no mutate
        _float_or_zero(command.get("created_at")),  # pragma: no mutate
        _publish_path_rank(command),  # pragma: no mutate
        str(command.get("id") or ""),  # pragma: no mutate
    )


def _command_kind_rank(command: Mapping[str, Any]) -> int:  # pragma: no mutate block
    kind = str(command.get("kind") or command.get("type") or "")  # pragma: no mutate
    if kind == "register_service":
        return 0  # pragma: no mutate
    if kind == "register_path":
        return 1  # pragma: no mutate
    return 2  # pragma: no mutate


def _publish_path_rank(command: Mapping[str, Any]) -> int:  # pragma: no mutate block
    if not _ranked_publish_command(command):
        return 0  # pragma: no mutate
    return PUBLISH_PATH_RANKS.get(str(command.get("path") or ""), 3)  # pragma: no mutate


def _ranked_publish_command(command: Mapping[str, Any]) -> bool:
    return _publish_priority(command) and _command_kind(command) == "publish_value"  # pragma: no mutate


def _publish_priority(command: Mapping[str, Any]) -> bool:
    return str(command.get("priority") or "").strip().lower() == "publish"  # pragma: no mutate


def _command_kind(command: Mapping[str, Any]) -> str:
    return str(command.get("kind") or command.get("type") or "")  # pragma: no mutate

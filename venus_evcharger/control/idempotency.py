# SPDX-License-Identifier: GPL-3.0-or-later
"""Runtime-only idempotency storage for the local Control API."""

from __future__ import annotations

import json
import logging
import os
import threading
from collections import OrderedDict
from typing import Any

from .runtime_paths import is_runtime_path


class ControlApiIdempotencyStore:
    """Persist recent idempotency responses in memory and under /run when configured."""

    def __init__(self, *, history_limit: int = 200, path: str = "") -> None:
        self._history_limit = max(1, int(history_limit))
        self._path = path.strip()
        self._entries: OrderedDict[str, tuple[str, int, dict[str, Any]]] = OrderedDict()
        self._lock = threading.Lock()
        self._load_runtime_entries()

    @property
    def path(self) -> str:
        return self._path

    def get(self, key: str) -> tuple[str, int, dict[str, Any]] | None:
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            fingerprint, status, response = entry
            self._entries.move_to_end(key)
            return fingerprint, status, dict(response)

    def put(self, key: str, fingerprint: str, status: int, response: dict[str, Any]) -> None:
        with self._lock:
            self._entries[key] = (fingerprint, int(status), dict(response))
            self._entries.move_to_end(key)
            while len(self._entries) > self._history_limit:
                self._entries.popitem(last=False)
            self._persist_runtime_entries()

    def count(self) -> int:
        with self._lock:
            return len(self._entries)

    def _load_runtime_entries(self) -> None:
        path = self._path
        if not self._should_load_path(path):
            return
        payload = self._loaded_payload(path)
        if payload is None:
            return
        for key, entry in self._loaded_entries(payload).items():
            self._entries[key] = entry
        while len(self._entries) > self._history_limit:
            self._entries.popitem(last=False)

    def _should_load_path(self, path: str) -> bool:
        return bool(path) and self._is_runtime_path(path) and os.path.exists(path)

    @staticmethod
    def _loaded_payload(path: str) -> dict[str, Any] | None:
        try:
            with open(path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, json.JSONDecodeError) as error:
            logging.debug("Unable to load Control API idempotency store %s: %s", path, error)
            return None
        return payload if isinstance(payload, dict) else None

    @classmethod
    def _loaded_entries(cls, payload: dict[str, Any]) -> dict[str, tuple[str, int, dict[str, Any]]]:
        entries: dict[str, tuple[str, int, dict[str, Any]]] = {}
        for key, item in payload.items():
            loaded = cls._loaded_entry(item)
            if loaded is not None:
                entries[str(key)] = loaded
        return entries

    @staticmethod
    def _loaded_entry(value: Any) -> tuple[str, int, dict[str, Any]] | None:
        if not isinstance(value, dict):
            return None
        fingerprint = str(value.get("fingerprint", "")).strip()
        status = int(value.get("status", 0) or 0)
        response = value.get("response")
        if not fingerprint or not isinstance(response, dict):
            return None
        return fingerprint, status, dict(response)

    def _persist_runtime_entries(self) -> None:
        path = self._path
        if not path or not self._is_runtime_path(path):
            return
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(self._serialized_entries(), handle, sort_keys=True)
        except OSError as error:
            logging.debug("Unable to persist Control API idempotency store %s: %s", path, error)

    def _serialized_entries(self) -> dict[str, dict[str, Any]]:
        return {
            key: {
                "fingerprint": fingerprint,
                "status": status,
                "response": response,
            }
            for key, (fingerprint, status, response) in self._entries.items()
        }

    @staticmethod
    def _is_runtime_path(path: str) -> bool:
        return is_runtime_path(path)

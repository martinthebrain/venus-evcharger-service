# SPDX-License-Identifier: GPL-3.0-or-later
"""Runtime-only audit helpers for local Control API command activity."""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from collections import deque
from typing import Any, Mapping

from .runtime_paths import is_runtime_path


class ControlApiAuditTrail:
    """Keep recent API audit entries in memory and optionally mirror them to /run."""

    def __init__(self, *, history_limit: int = 200, path: str = "") -> None:
        self._history_limit = max(1, int(history_limit))
        self._path = path.strip()
        self._entries: deque[dict[str, Any]] = deque(maxlen=self._history_limit)
        self._next_seq = 1
        self._lock = threading.Lock()

    @property
    def path(self) -> str:
        return self._path

    def append(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        """Store one normalized runtime audit entry and mirror it to /run when configured."""
        with self._lock:
            entry = self._normalized_entry(payload)
            self._next_seq += 1
            self._entries.append(entry)
            self._append_runtime_log(entry)
            return dict(entry)

    def _normalized_entry(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "seq": self._next_seq,
            "timestamp": float(payload.get("timestamp", time.time())),
            "transport": str(payload.get("transport", "http") or "http"),
            "scope": str(payload.get("scope", "control") or "control"),
            "client_host": str(payload.get("client_host", "") or ""),
            "status_code": int(payload.get("status_code", 0) or 0),
            "replayed": bool(payload.get("replayed", False)),
            "command": self._mapping_payload(payload.get("command")),
            "result": self._mapping_payload(payload.get("result")),
            "error": self._mapping_payload(payload.get("error")),
        }

    @staticmethod
    def _mapping_payload(value: Any) -> dict[str, Any]:
        return dict(value) if isinstance(value, Mapping) else {}

    def recent(self, *, limit: int = 20) -> list[dict[str, Any]]:
        """Return the most recent audit entries."""
        capped_limit = max(0, int(limit))
        with self._lock:
            entries = [dict(entry) for entry in self._entries]
        return entries[-capped_limit:] if capped_limit else []

    def count(self) -> int:
        """Return the current in-memory entry count."""
        with self._lock:
            return len(self._entries)

    def _append_runtime_log(self, entry: dict[str, Any]) -> None:
        path = self._path
        if not path or not self._is_runtime_path(path):
            return
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry, sort_keys=True) + "\n")
        except OSError as error:
            logging.debug("Unable to append Control API audit log %s: %s", path, error)

    @staticmethod
    def _is_runtime_path(path: str) -> bool:
        return is_runtime_path(path)

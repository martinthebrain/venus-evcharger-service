# SPDX-License-Identifier: GPL-3.0-or-later
"""Persistence coordinator for volatile runtime state."""

from __future__ import annotations

import logging
import math
import os
import time
from typing import TYPE_CHECKING

from venus_evcharger.controllers.errors import RUNTIME_PERSISTENCE_WRITE_ERRORS
from venus_evcharger.controllers.state_json import read_json_object_file
from venus_evcharger.controllers.state_runtime_normalize import RuntimeStateNormalizer, require_runtime_clock
from venus_evcharger.core.shared import compact_json, write_text_atomically

if TYPE_CHECKING:
    from venus_evcharger.controllers.state_contracts import RuntimeRestorePort, RuntimeSnapshotPort, StateSummaryPort


RUNTIME_STATE_CHANGED_AT_FIELD = "state_changed_at"


def _finite_number(value: object) -> float | None:
    """Return a finite non-boolean number."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    numeric = float(value)
    return numeric if math.isfinite(numeric) else None


def _valid_state_changed_at(value: object, current_time: float) -> float | None:
    """Return a bounded epoch timestamp from persisted status metadata."""
    changed_at = _finite_number(value)
    if changed_at is None or changed_at < 0.0 or changed_at > current_time + 1.0:
        return None
    return min(changed_at, current_time)


def _legacy_state_changed_at(path: str, current_time: float) -> float:
    """Use the old event file's mtime as the best migration timestamp."""
    try:
        modified_at = os.path.getmtime(path)
    except OSError:
        return current_time
    normalized = _valid_state_changed_at(modified_at, current_time)
    return current_time if normalized is None else normalized


def _semantic_state(state: dict[str, object]) -> dict[str, object]:
    """Remove persistence metadata before restoring domain state."""
    semantic = dict(state)
    semantic.pop(RUNTIME_STATE_CHANGED_AT_FIELD, None)
    return semantic


def _status_document(state: dict[str, object], changed_at: float) -> dict[str, object]:
    """Attach event-time metadata without changing the semantic snapshot."""
    document = dict(state)
    document[RUNTIME_STATE_CHANGED_AT_FIELD] = changed_at
    return document


def _loaded_status(path: str, document: dict[str, object], current_time: float) -> tuple[float, bool]:
    """Return the event timestamp and whether the legacy file needs migration."""
    persisted = _valid_state_changed_at(document.get(RUNTIME_STATE_CHANGED_AT_FIELD), current_time)
    if persisted is not None:
        return persisted, False
    return _legacy_state_changed_at(path, current_time), True


class RuntimeStatePersistence:
    """Load and save snapshots without owning normalization or restore rules."""

    def __init__(
        self,
        service: object,
        normalizer: RuntimeStateNormalizer,
        snapshot: RuntimeSnapshotPort,
        restorer: RuntimeRestorePort,
        summary: StateSummaryPort,
    ) -> None:
        self.service = service
        self.normalizer = normalizer
        self.snapshot = snapshot
        self.restorer = restorer
        self.summary = summary

    @staticmethod
    def _write(path: str, state: dict[str, object], changed_at: float) -> bool:
        try:
            write_text_atomically(path, compact_json(_status_document(state, changed_at)))
            return True
        except RUNTIME_PERSISTENCE_WRITE_ERRORS as error:
            logging.warning("Unable to write runtime state to %s: %s", path, error)
            return False

    def load(self) -> None:
        path = str(getattr(self.service, "runtime_state_path", "")).strip()
        if not path:
            return
        document = read_json_object_file(path)
        if document is None:
            return
        current_time = max(0.0, self.normalizer.load_time(require_runtime_clock(self.service)))
        changed_at, needs_migration = _loaded_status(path, document, current_time)
        self.restorer.restore(_semantic_state(document), current_time)
        restored_state = self.snapshot.build()
        serialized_state = compact_json(restored_state)
        if needs_migration:
            self._write(path, restored_state, changed_at)
        setattr(self.service, "_runtime_state_serialized", serialized_state)
        setattr(self.service, "_runtime_state_changed_at", changed_at)
        logging.info("Restored runtime state from %s: %s", path, self.summary.build())

    def save(self) -> None:
        path = str(getattr(self.service, "runtime_state_path", "")).strip()
        if not path:
            return
        state = self.snapshot.build()
        serialized_state = compact_json(state)
        if serialized_state == getattr(self.service, "_runtime_state_serialized", None):
            return
        changed_at = max(0.0, time.time())
        if not self._write(path, state, changed_at):
            return
        setattr(self.service, "_runtime_state_serialized", serialized_state)
        setattr(self.service, "_runtime_state_changed_at", changed_at)
        logging.debug("Saved runtime state to %s: %s", path, self.summary.build())

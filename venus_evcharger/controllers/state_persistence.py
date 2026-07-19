# SPDX-License-Identifier: GPL-3.0-or-later
"""Persistence coordinator for volatile runtime state."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from venus_evcharger.controllers.errors import RUNTIME_PERSISTENCE_WRITE_ERRORS
from venus_evcharger.controllers.state_json import read_json_object_file
from venus_evcharger.controllers.state_runtime_normalize import RuntimeStateNormalizer, require_runtime_clock
from venus_evcharger.core.shared import compact_json, write_text_atomically

if TYPE_CHECKING:
    from venus_evcharger.controllers.state_contracts import RuntimeRestorePort, RuntimeSnapshotPort, StateSummaryPort


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

    def serialized(self) -> str:
        return compact_json(self.snapshot.build())

    def load(self) -> None:
        path = str(getattr(self.service, "runtime_state_path", "")).strip()
        if not path:
            return
        state = read_json_object_file(path)
        if state is None:
            return
        self.restorer.restore(state, self.normalizer.load_time(require_runtime_clock(self.service)))
        setattr(self.service, "_runtime_state_serialized", self.serialized())
        logging.info("Restored runtime state from %s: %s", path, self.summary.build())

    def save(self) -> None:
        path = str(getattr(self.service, "runtime_state_path", "")).strip()
        if not path:
            return
        payload = self.serialized()
        if payload == getattr(self.service, "_runtime_state_serialized", None):
            return
        try:
            write_text_atomically(path, payload)
            setattr(self.service, "_runtime_state_serialized", payload)
            logging.debug("Saved runtime state to %s: %s", path, self.summary.build())
        except RUNTIME_PERSISTENCE_WRITE_ERRORS as error:
            logging.warning("Unable to write runtime state to %s: %s", path, error)

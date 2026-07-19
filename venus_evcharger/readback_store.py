# SPDX-License-Identifier: GPL-3.0-or-later
"""Thread-safe in-memory ownership of backend readback snapshots."""

from __future__ import annotations

import threading

from venus_evcharger.ports.readback import ReadbackSnapshots, TimedChargerState, TimedSwitchState


class InMemoryReadbackStore:
    """Replace and read complete immutable snapshots under one small lock."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._charger: TimedChargerState | None = None
        self._switch: TimedSwitchState | None = None

    def snapshot(self) -> ReadbackSnapshots:
        """Return charger and switch snapshots from one consistent store view."""
        with self._lock:
            return ReadbackSnapshots(charger=self._charger, switch=self._switch)

    def replace_charger(self, snapshot: TimedChargerState | None) -> None:
        """Atomically replace or clear the complete charger snapshot."""
        with self._lock:
            self._charger = snapshot

    def replace_switch(self, snapshot: TimedSwitchState | None) -> None:
        """Atomically replace or clear the complete switch snapshot."""
        with self._lock:
            self._switch = snapshot


__all__ = ["InMemoryReadbackStore"]

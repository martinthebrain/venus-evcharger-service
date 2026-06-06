# SPDX-License-Identifier: GPL-3.0-or-later
"""Auto-input helper process supervision and snapshot refresh helpers."""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from typing import Any

from venus_evcharger.core.shared import AUTO_INPUT_SNAPSHOT_SCHEMA_VERSION
from venus_evcharger.inputs.supervisor_process import _AutoInputSupervisorProcessMixin
from venus_evcharger.inputs.supervisor_snapshot import _AutoInputSupervisorSnapshotMixin


class AutoInputSupervisor(
    _AutoInputSupervisorSnapshotMixin,
    _AutoInputSupervisorProcessMixin,
):
    """Supervise the external auto-input helper and ingest its RAM snapshot."""

    SNAPSHOT_SCHEMA_VERSION = AUTO_INPUT_SNAPSHOT_SCHEMA_VERSION
    SNAPSHOT_SOURCE_KEYS = ("pv", "battery", "grid")
    FUTURE_TIMESTAMP_TOLERANCE_SECONDS = 1.0
    SNAPSHOT_REQUIRED_KEYS = frozenset(
        {
            "snapshot_version",
            "captured_at",
            "heartbeat_at",
            "pv_captured_at",
            "pv_power",
            "battery_captured_at",
            "battery_soc",
            "grid_captured_at",
            "grid_power",
            "writer_pid",
            "helper_generation",
        }
    )

    def __init__(self, service: Any) -> None:
        self.service = service


_PATCH_EXPORTS = (logging, os, subprocess, sys)

# SPDX-License-Identifier: GPL-3.0-or-later
"""Snapshot ingestion helpers for the Auto input supervisor."""

from __future__ import annotations

from venus_evcharger.inputs.supervisor_snapshot_runtime import _AutoInputSupervisorSnapshotRuntime
from venus_evcharger.inputs.supervisor_snapshot_validation import _AutoInputSupervisorSnapshotValidation


class _AutoInputSupervisorSnapshot(
    _AutoInputSupervisorSnapshotValidation,
    _AutoInputSupervisorSnapshotRuntime,
):
    pass

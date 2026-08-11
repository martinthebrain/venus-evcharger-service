# SPDX-License-Identifier: GPL-3.0-or-later
"""Auto-input helper process supervision and snapshot refresh helpers."""

from __future__ import annotations

from venus_evcharger.core.shared import AUTO_INPUT_SNAPSHOT_SCHEMA_VERSION
from venus_evcharger.inputs.supervisor_contracts import AutoInputSupervisorService, SnapshotSchema
from venus_evcharger.inputs.supervisor_process import AutoInputProcessLifecycle
from venus_evcharger.inputs.supervisor_snapshot_runtime import AutoInputSnapshotRuntime
from venus_evcharger.inputs.supervisor_snapshot_validation import AutoInputSnapshotValidator

__all__ = ("AutoInputSupervisor",)


class AutoInputSupervisor:
    """Supervise the external auto-input helper and ingest its RAM snapshot."""

    SCHEMA = SnapshotSchema(
        version=AUTO_INPUT_SNAPSHOT_SCHEMA_VERSION,
        source_keys=("pv", "battery", "grid"),
        future_timestamp_tolerance_seconds=1.0,
        required_keys=frozenset(
            {
                "snapshot_version",
                "snapshot_sequence",
                "captured_at",
                "captured_monotonic",
                "heartbeat_at",
                "heartbeat_monotonic",
                "pv_captured_at",
                "pv_observed_monotonic",
                "pv_power",
                "battery_captured_at",
                "battery_observed_monotonic",
                "battery_soc",
                "grid_captured_at",
                "grid_observed_monotonic",
                "grid_power",
                "writer_pid",
                "helper_generation",
                "runtime_instance_id",
            }
        ),
    )

    def __init__(
        self,
        service: AutoInputSupervisorService,
        *,
        config_path: str,
        helper_path: str,
    ) -> None:
        self.validator = AutoInputSnapshotValidator(service, self.SCHEMA)
        self.snapshot_runtime = AutoInputSnapshotRuntime(service, self.SCHEMA, self.validator)
        self.process_lifecycle = AutoInputProcessLifecycle(
            service,
            self.snapshot_runtime,
            config_path=config_path,
            helper_path=helper_path,
        )

    def stop_helper(self, force: bool = False) -> None:
        self.process_lifecycle.stop_helper(force)

    def spawn_helper(self, now: float | None = None) -> None:
        self.process_lifecycle.spawn_helper(now)

    def ensure_helper_process(self, now: float | None = None) -> None:
        self.process_lifecycle.ensure_helper_process(now)

    def refresh_snapshot(self, monotonic_at: float | None = None) -> None:
        self.snapshot_runtime.refresh_snapshot(monotonic_at)

# SPDX-License-Identifier: GPL-3.0-or-later
"""Contracts for the bounded auto-input snapshot parity checker."""

from __future__ import annotations

import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path

from scripts.dev.compare_auto_input_helper_snapshots import (
    MAX_REPORTED_DIFFERENCES,
    MAX_SNAPSHOT_BYTES,
    SnapshotParityError,
    compare_snapshots,
    load_snapshot,
    main,
)


def snapshot(**changes: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "snapshot_version": 2,
        "snapshot_sequence": 3,
        "captured_at": 1_000.0,
        "captured_monotonic": 100.0,
        "heartbeat_at": 1_001.0,
        "heartbeat_monotonic": 101.0,
        "writer_pid": 111,
        "helper_generation": 4,
        "runtime_instance_id": "python-runtime",
        "helper_state": "running",
        "helper_status": "running",
        "pv_status": "ok",
        "pv_captured_at": 999.0,
        "pv_observed_monotonic": 99.0,
        "pv_power": 2_300.0,
        "battery_status": "ok",
        "battery_captured_at": 998.0,
        "battery_observed_monotonic": 98.0,
        "battery_soc": 57.5,
        "battery_source_count": 1,
        "battery_sources": [{"source_id": "battery", "captured_at": 998.0}],
        "grid_status": "ok",
        "grid_captured_at": 997.0,
        "grid_observed_monotonic": 97.0,
        "grid_power": -125.0,
        "grid_fusion_backup_age_seconds": 4.0,
        "grid_selected_source_id": "victron",
    }
    payload.update(changes)
    return payload


class AutoInputSnapshotParityTests(unittest.TestCase):
    def test_volatile_identity_time_age_and_sequence_values_are_normalized(self) -> None:
        left = snapshot()
        right = snapshot(
            snapshot_sequence=900,
            captured_at=2_000.0,
            captured_monotonic=200.0,
            heartbeat_at=2_001.0,
            heartbeat_monotonic=201.0,
            writer_pid=222,
            helper_generation=8,
            runtime_instance_id="rust-runtime",
            pv_captured_at=1_999.0,
            pv_observed_monotonic=199.0,
            battery_captured_at=1_998.0,
            battery_observed_monotonic=198.0,
            grid_captured_at=1_997.0,
            grid_observed_monotonic=197.0,
            grid_fusion_backup_age_seconds=0.25,
            battery_sources=[{"source_id": "battery", "captured_at": 1_998.0}],
        )

        self.assertTrue(compare_snapshots(left, right).equal)

    def test_nested_poll_timestamps_are_normalized_but_presence_remains_semantic(self) -> None:
        left = snapshot(
            battery_sources=[
                {
                    "source_id": "battery",
                    "attempted_at": 1000.0,
                    "observed_at": 999.0,
                    "observed_monotonic": 99.0,
                    "next_poll_at": 101.0,
                }
            ]
        )
        right = snapshot(
            battery_sources=[
                {
                    "source_id": "battery",
                    "attempted_at": 2000.0,
                    "observed_at": 1999.0,
                    "observed_monotonic": 199.0,
                    "next_poll_at": 201.0,
                }
            ]
        )
        missing = snapshot(
            battery_sources=[
                {
                    "source_id": "battery",
                    "attempted_at": None,
                    "observed_at": 1999.0,
                    "observed_monotonic": 199.0,
                    "next_poll_at": 201.0,
                }
            ]
        )

        self.assertTrue(compare_snapshots(left, right).equal)
        self.assertFalse(compare_snapshots(left, missing).equal)

    def test_time_presence_and_semantic_values_remain_part_of_parity(self) -> None:
        missing_timestamp = snapshot(pv_captured_at=None)
        changed_state = snapshot(helper_state="starting")
        changed_source = snapshot(battery_sources=[{"source_id": "other", "captured_at": 998.0}])

        self.assertFalse(compare_snapshots(snapshot(), missing_timestamp).equal)
        self.assertFalse(compare_snapshots(snapshot(), changed_state).equal)
        self.assertFalse(compare_snapshots(snapshot(), changed_source).equal)

    def test_floats_are_tolerant_but_integer_contracts_are_exact(self) -> None:
        close = snapshot(pv_power=2_300.000_000_5)
        far = snapshot(pv_power=2_300.01)
        different_count = snapshot(battery_source_count=2)

        self.assertTrue(compare_snapshots(snapshot(), close).equal)
        self.assertFalse(compare_snapshots(snapshot(), far).equal)
        self.assertFalse(compare_snapshots(snapshot(), different_count).equal)

    def test_shape_differences_are_reported_and_bounded(self) -> None:
        left = snapshot(**{f"left_{index}": index for index in range(50)})
        right = snapshot(**{f"right_{index}": index for index in range(50)})

        result = compare_snapshots(left, right)

        self.assertFalse(result.equal)
        self.assertTrue(result.truncated)
        self.assertEqual(len(result.differences), MAX_REPORTED_DIFFERENCES)

    def test_loader_rejects_invalid_contract_duplicate_fields_and_large_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "snapshot.json"
            path.write_text(json.dumps(snapshot(snapshot_version=1)), encoding="utf-8")
            with self.assertRaises(SnapshotParityError):
                load_snapshot(path)

            path.write_text('{"snapshot_version":2,"snapshot_version":2}', encoding="utf-8")
            with self.assertRaises(SnapshotParityError):
                load_snapshot(path)

            path.write_bytes(b" " * (MAX_SNAPSHOT_BYTES + 1))
            with self.assertRaises(SnapshotParityError):
                load_snapshot(path)

    def test_loader_rejects_invalid_identity_and_nonfinite_time(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "snapshot.json"
            path.write_text(json.dumps(snapshot(writer_pid=0)), encoding="utf-8")
            with self.assertRaises(SnapshotParityError):
                load_snapshot(path)

        with self.assertRaises(SnapshotParityError):
            compare_snapshots(snapshot(captured_at=float("inf")), snapshot())
        with self.assertRaises(SnapshotParityError):
            compare_snapshots(snapshot(writer_pid=0), snapshot())

    def test_boolean_and_numeric_values_are_not_interchangeable(self) -> None:
        self.assertFalse(compare_snapshots(snapshot(grid_fusion_enabled=False), snapshot(grid_fusion_enabled=0)).equal)

    def test_cli_exit_codes_distinguish_parity_mismatch_and_invalid_input(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            left = Path(temp_dir) / "left.json"
            right = Path(temp_dir) / "right.json"
            left.write_text(json.dumps(snapshot()), encoding="utf-8")
            right.write_text(json.dumps(snapshot(writer_pid=999)), encoding="utf-8")
            output = StringIO()
            with redirect_stdout(output), redirect_stderr(output):
                self.assertEqual(main([str(left), str(right)]), 0)

            right.write_text(json.dumps(snapshot(pv_power=2_400.0)), encoding="utf-8")
            with redirect_stdout(output), redirect_stderr(output):
                self.assertEqual(main([str(left), str(right)]), 1)

            right.write_text("not-json", encoding="utf-8")
            with redirect_stdout(output), redirect_stderr(output):
                self.assertEqual(main([str(left), str(right)]), 2)


if __name__ == "__main__":
    unittest.main()

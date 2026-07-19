# SPDX-License-Identifier: GPL-3.0-or-later
"""Public snapshot-runtime scenarios for the auto-input supervisor."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.support.auto_input_supervisor import (
    AutoInputSupervisorServiceFake,
    HelperProcessFake,
    valid_snapshot,
)
from venus_evcharger.inputs.supervisor import AutoInputSupervisor


def write_snapshot(path: Path, payload: object, mtime_ns: int) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")
    os.utime(path, ns=(mtime_ns, mtime_ns))


class TestAutoInputSupervisorSnapshotRuntimeContracts(unittest.TestCase):
    def supervisor(self, service: AutoInputSupervisorServiceFake) -> AutoInputSupervisor:
        return AutoInputSupervisor(service, config_path="/config.ini", helper_path="/helper.py")

    def test_refresh_applies_fresh_snapshot_and_all_identity_diagnostics(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "snapshot.json"
            payload = valid_snapshot(
                heartbeat_at=105.0,
                pv_status="ok",
                battery_status="ok",
                grid_status="ok",
                helper_state="running",
                helper_status="healthy",
            )
            write_snapshot(path, payload, 10)
            process = HelperProcessFake(pid=4321)
            service = AutoInputSupervisorServiceFake(
                auto_input_snapshot_path=str(path),
                now=106.0,
                _auto_input_helper_process=process,
                _auto_input_helper_generation=1,
                _auto_input_helper_last_start_at=99.0,
            )

            self.supervisor(service).refresh_snapshot()

        self.assertEqual(service._auto_input_snapshot_mtime_ns, 10)
        self.assertEqual(service._auto_input_snapshot_last_seen, 105.0)
        self.assertTrue(service._auto_input_snapshot_seen_for_current_helper)
        self.assertEqual(service._auto_input_snapshot_last_captured_at, 100.0)
        self.assertEqual(service._auto_input_snapshot_version, AutoInputSupervisor.SCHEMA.version)
        self.assertEqual(service._auto_input_snapshot_writer_pid, 4321)
        self.assertEqual(service._auto_input_snapshot_generation, 1)
        fields = service.runtime.snapshots[-1]
        self.assertEqual(fields["pv_power"], 2300.0)
        self.assertEqual(fields["helper_status"], "healthy")
        self.assertTrue(fields["auto_mode_active"])

    def test_stale_snapshot_updates_identity_but_clears_source_values(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "snapshot.json"
            write_snapshot(path, valid_snapshot(), 20)
            service = AutoInputSupervisorServiceFake(
                auto_input_snapshot_path=str(path),
                now=130.0,
                _auto_input_snapshot_last_seen=90.0,
                _auto_input_snapshot_seen_for_current_helper=False,
                virtual_mode=0,
            )
            self.supervisor(service).refresh_snapshot()

        fields = service.runtime.snapshots[-1]
        self.assertIsNone(fields["pv_power"])
        self.assertIsNone(fields["battery_soc"])
        self.assertIsNone(fields["grid_power"])
        self.assertFalse(fields["auto_mode_active"])
        self.assertFalse(service._auto_input_snapshot_seen_for_current_helper)
        self.assertIsNone(service._auto_input_snapshot_last_seen)

    def test_previous_current_helper_liveness_is_preserved_on_foreign_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "snapshot.json"
            write_snapshot(path, valid_snapshot(runtime_instance_id="foreign"), 30)
            service = AutoInputSupervisorServiceFake(
                auto_input_snapshot_path=str(path),
                now=101.0,
                _auto_input_snapshot_last_seen=99.0,
                _auto_input_snapshot_seen_for_current_helper=True,
            )
            self.supervisor(service).refresh_snapshot()

        self.assertEqual(service._auto_input_snapshot_last_seen, 99.0)
        self.assertFalse(service._auto_input_snapshot_seen_for_current_helper)

    def test_each_helper_identity_dimension_controls_liveness(self) -> None:
        scenarios: tuple[tuple[dict[str, object], dict[str, object], bool], ...] = (
            ({"runtime_instance_id": "foreign"}, {}, False),
            ({"helper_generation": 2}, {"_auto_input_helper_generation": 1}, False),
            ({"writer_pid": 1111}, {"_auto_input_helper_process": HelperProcessFake(pid=4321)}, False),
            ({}, {"_auto_input_helper_last_start_at": 101.0}, False),
            ({}, {"_auto_input_helper_generation": 0}, True),
            ({}, {"_auto_input_helper_process": None}, True),
        )
        for index, (snapshot_changes, service_changes, expected) in enumerate(scenarios, start=1):
            with self.subTest(index=index), tempfile.TemporaryDirectory() as temp_dir:
                path = Path(temp_dir) / "snapshot.json"
                write_snapshot(path, valid_snapshot(**snapshot_changes), index * 100)
                service = AutoInputSupervisorServiceFake(auto_input_snapshot_path=str(path), now=100.0)
                for key, value in service_changes.items():
                    setattr(service, key, value)
                self.supervisor(service).refresh_snapshot()
                self.assertEqual(service._auto_input_snapshot_seen_for_current_helper, expected)

    def test_unchanged_missing_and_empty_paths_are_noops(self) -> None:
        service = AutoInputSupervisorServiceFake(auto_input_snapshot_path="", now=100.0)
        supervisor = self.supervisor(service)
        supervisor.refresh_snapshot()
        self.assertEqual(service.runtime.snapshots, [])

        service.auto_input_snapshot_path = "/missing/snapshot.json"
        supervisor.refresh_snapshot()
        self.assertEqual(service.runtime.snapshots, [])

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "snapshot.json"
            write_snapshot(path, valid_snapshot(), 40)
            service.auto_input_snapshot_path = str(path)
            service._auto_input_snapshot_mtime_ns = 40
            supervisor.refresh_snapshot()
        self.assertEqual(service.runtime.snapshots, [])

    def test_invalid_json_and_schema_failures_are_warned_without_apply(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "snapshot.json"
            path.write_text("{", encoding="utf-8")
            service = AutoInputSupervisorServiceFake(auto_input_snapshot_path=str(path))
            supervisor = self.supervisor(service)
            supervisor.refresh_snapshot()
            self.assertEqual(service.runtime.warnings[-1][0], "auto-input-helper-read-failed")
            self.assertEqual(service.runtime.snapshots, [])

            write_snapshot(path, {"snapshot_version": 1}, 50)
            supervisor.refresh_snapshot()
            self.assertEqual(service.runtime.warnings[-1][0], "auto-input-helper-schema-invalid")
            self.assertEqual(service.runtime.snapshots, [])

    def test_read_errors_are_throttled_for_each_supported_boundary_exception(self) -> None:
        service = AutoInputSupervisorServiceFake(auto_input_snapshot_path="/tmp/snapshot.json")
        supervisor = self.supervisor(service)
        for error in (OSError("io"), UnicodeDecodeError("utf-8", b"x", 0, 1, "bad"), RuntimeError("io")):
            with patch("pathlib.Path.read_text", side_effect=error), patch(
                "venus_evcharger.inputs.supervisor_snapshot_runtime.os.stat"
            ) as stat:
                stat.return_value.st_mtime_ns = len(service.runtime.warnings) + 100
                supervisor.refresh_snapshot()
            self.assertEqual(service.runtime.warnings[-1][0], "auto-input-helper-read-failed")

    def test_regressed_and_future_timestamps_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "snapshot.json"
            service = AutoInputSupervisorServiceFake(
                auto_input_snapshot_path=str(path),
                now=100.0,
                _auto_input_snapshot_last_captured_at=101.0,
            )
            supervisor = self.supervisor(service)
            write_snapshot(path, valid_snapshot(captured_at=100.0), 60)
            supervisor.refresh_snapshot()
            self.assertEqual(service.runtime.warnings[-1][0], "auto-input-helper-captured-at-regressed")

            service._auto_input_snapshot_last_captured_at = None
            write_snapshot(path, valid_snapshot(captured_at=101.1, heartbeat_at=101.1, pv_captured_at=101.1, battery_captured_at=101.1, grid_captured_at=101.1), 61)
            supervisor.refresh_snapshot()
            self.assertEqual(service.runtime.warnings[-1][0], "auto-input-helper-future-timestamp")
        self.assertEqual(service.runtime.snapshots, [])

    def test_timestamp_tolerance_and_equal_capture_are_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "snapshot.json"
            payload = valid_snapshot(
                captured_at=101.0,
                heartbeat_at=101.0,
                pv_captured_at=101.0,
                battery_captured_at=101.0,
                grid_captured_at=101.0,
            )
            write_snapshot(path, payload, 70)
            service = AutoInputSupervisorServiceFake(
                auto_input_snapshot_path=str(path),
                now=100.0,
                _auto_input_snapshot_last_captured_at=101.0,
            )
            self.supervisor(service).refresh_snapshot()
        self.assertEqual(len(service.runtime.snapshots), 1)


if __name__ == "__main__":
    unittest.main()

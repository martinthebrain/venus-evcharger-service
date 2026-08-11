# SPDX-License-Identifier: GPL-3.0-or-later
"""Public snapshot-runtime scenarios for the auto-input supervisor."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from tests.support.auto_input_supervisor import (
    AutoInputSupervisorServiceFake,
    HelperProcessFake,
    valid_snapshot,
)
from venus_evcharger.inputs.supervisor import AutoInputSupervisor
from venus_evcharger.inputs.supervisor_snapshot_runtime import _sequence_advances


def write_snapshot(path: Path, payload: object, mtime_ns: int) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")
    os.utime(path, ns=(mtime_ns, mtime_ns))


class TestAutoInputSupervisorSnapshotRuntimeContracts(unittest.TestCase):
    def supervisor(self, service: AutoInputSupervisorServiceFake) -> AutoInputSupervisor:
        return AutoInputSupervisor(service, config_path="/config.ini", helper_path="/helper.py")

    def test_public_process_commands_delegate_without_transport_knowledge(self) -> None:
        supervisor = self.supervisor(AutoInputSupervisorServiceFake())
        supervisor.process_lifecycle.stop_helper = MagicMock()
        supervisor.process_lifecycle.spawn_helper = MagicMock()
        supervisor.process_lifecycle.ensure_helper_process = MagicMock()

        supervisor.stop_helper(force=True)
        supervisor.spawn_helper(123.0)
        supervisor.ensure_helper_process(124.0)

        supervisor.process_lifecycle.stop_helper.assert_called_once_with(True)
        supervisor.process_lifecycle.spawn_helper.assert_called_once_with(123.0)
        supervisor.process_lifecycle.ensure_helper_process.assert_called_once_with(124.0)

    def test_apply_snapshot_without_file_signature_preserves_last_signature(self) -> None:
        service = AutoInputSupervisorServiceFake()
        runtime = self.supervisor(service).snapshot_runtime
        signature = (11, 12, 13, 14)
        runtime._last_snapshot_signature = signature

        runtime._apply_snapshot(None, 100.0, valid_snapshot(), True)

        self.assertEqual(runtime._last_snapshot_signature, signature)
        self.assertEqual(service.runtime.snapshots[-1]["captured_at"], 100.0)

    def test_sequence_contract_distinguishes_restart_from_regression(self) -> None:
        scenarios = (
            (None, None, 1, None, "instance-1", None, False),
            (1, None, 1, None, "instance-1", None, True),
            (2, 1, 1, 1, "instance-1", "instance-1", True),
            (1, 1, 1, 1, "instance-1", "instance-1", False),
            (1, 2, 1, 1, "instance-1", "instance-1", False),
            (1, 99, 1, 1, "instance-2", "instance-1", True),
            (1, 99, 2, 1, "instance-1", "instance-1", True),
        )
        for scenario in scenarios:
            (
                sequence,
                previous_sequence,
                generation,
                previous_generation,
                runtime_instance,
                previous_runtime_instance,
                expected,
            ) = scenario
            with self.subTest(scenario=scenario):
                self.assertIs(
                    _sequence_advances(
                        sequence,
                        previous_sequence,
                        generation,
                        previous_generation,
                        runtime_instance,
                        previous_runtime_instance,
                    ),
                    expected,
                )

    def test_helper_start_boundary_includes_an_equal_monotonic_timestamp(self) -> None:
        service = AutoInputSupervisorServiceFake(_auto_input_helper_last_start_at=0.0)
        runtime = self.supervisor(service).snapshot_runtime
        self.assertTrue(runtime._snapshot_after_current_helper_start(service, 0.0))

        service._auto_input_helper_last_start_at = 10.0
        self.assertFalse(runtime._snapshot_after_current_helper_start(service, 9.999))
        self.assertTrue(runtime._snapshot_after_current_helper_start(service, 10.0))

        service._auto_input_helper_last_start_at = 0.5
        self.assertFalse(runtime._snapshot_after_current_helper_start(service, 0.0))

    def test_timestamp_and_sequence_warning_throttles_have_a_one_second_floor(self) -> None:
        service = AutoInputSupervisorServiceFake(
            auto_input_helper_restart_seconds=0.5,
            _auto_input_snapshot_last_sequence=1,
            _auto_input_snapshot_generation=1,
            _auto_input_snapshot_runtime_instance_id="instance-1",
        )
        runtime = self.supervisor(service).snapshot_runtime

        self.assertFalse(
            runtime._snapshot_timestamps_valid(
                "/run/auto-input.json",
                102.0,
                100.0,
            )
        )
        self.assertEqual(service.runtime.warnings[-1][1], 1.0)
        self.assertFalse(
            runtime._snapshot_sequence_valid(
                "/run/auto-input.json",
                valid_snapshot(snapshot_sequence=1),
            )
        )
        self.assertEqual(service.runtime.warnings[-1][1], 1.0)

    def test_missing_monotonic_freshness_is_not_in_the_future(self) -> None:
        supervisor = self.supervisor(AutoInputSupervisorServiceFake())

        self.assertTrue(
            supervisor.snapshot_runtime._snapshot_timestamps_valid(
                "/run/auto-input.json",
                None,
                100.0,
            )
        )

    def test_refresh_applies_fresh_snapshot_and_all_identity_diagnostics(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "snapshot.json"
            payload = valid_snapshot(
                heartbeat_at=105.0,
                heartbeat_monotonic=105.0,
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

    def test_missing_gateway_health_tolerates_snapshot_until_slow_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "snapshot.json"
            write_snapshot(path, valid_snapshot(), 21)
            service = AutoInputSupervisorServiceFake(
                auto_input_snapshot_path=str(path),
                gateway_pressure_policy=None,
                now=145.0,
            )
            self.supervisor(service).refresh_snapshot()
            self.assertEqual(service.runtime.snapshots[-1]["pv_power"], 2300.0)

            write_snapshot(path, valid_snapshot(snapshot_sequence=2), 22)
            service.now = 146.0
            self.supervisor(service).refresh_snapshot()

        fields = service.runtime.snapshots[-1]
        self.assertIsNone(fields["pv_power"])
        self.assertIsNone(fields["battery_soc"])
        self.assertIsNone(fields["grid_power"])

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

    def test_atomic_replacement_with_same_mtime_is_applied(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "snapshot.json"
            replacement = Path(temp_dir) / "replacement.json"
            write_snapshot(path, valid_snapshot(pv_power=900.0), 40)
            service = AutoInputSupervisorServiceFake(auto_input_snapshot_path=str(path), now=100.0)
            supervisor = self.supervisor(service)
            supervisor.refresh_snapshot()

            write_snapshot(
                replacement,
                valid_snapshot(snapshot_sequence=2, pv_power=1200.0),
                40,
            )
            replacement.replace(path)
            supervisor.refresh_snapshot()

        self.assertEqual([snapshot["pv_power"] for snapshot in service.runtime.snapshots], [900.0, 1200.0])

    def test_sequence_must_increase_within_one_helper_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "snapshot.json"
            service = AutoInputSupervisorServiceFake(
                auto_input_snapshot_path=str(path),
                now=100.0,
            )
            supervisor = self.supervisor(service)

            write_snapshot(path, valid_snapshot(snapshot_sequence=2), 80)
            supervisor.refresh_snapshot()
            write_snapshot(path, valid_snapshot(snapshot_sequence=2), 81)
            supervisor.refresh_snapshot()
            write_snapshot(path, valid_snapshot(snapshot_sequence=1), 82)
            supervisor.refresh_snapshot()

            self.assertEqual(len(service.runtime.snapshots), 1)
            self.assertEqual(
                service.runtime.warnings[-2:],
                [
                    (
                        "auto-input-helper-sequence-regressed",
                        5.0,
                        "Auto input helper snapshot %s has non-increasing sequence %s after %s",
                        (str(path), 2, 2),
                        None,
                    ),
                    (
                        "auto-input-helper-sequence-regressed",
                        5.0,
                        "Auto input helper snapshot %s has non-increasing sequence %s after %s",
                        (str(path), 1, 2),
                        None,
                    ),
                ],
            )

            service._auto_input_runtime_instance_id = "instance-2"
            write_snapshot(
                path,
                valid_snapshot(
                    snapshot_sequence=1,
                    runtime_instance_id="instance-2",
                ),
                83,
            )
            supervisor.refresh_snapshot()

        self.assertEqual(len(service.runtime.snapshots), 2)
        self.assertEqual(service._auto_input_snapshot_last_sequence, 1)
        self.assertEqual(
            service._auto_input_snapshot_runtime_instance_id,
            "instance-2",
        )

    def test_consumer_monotonic_time_is_sampled_after_the_file_read(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "snapshot.json"
            write_snapshot(path, valid_snapshot(), 84)
            service = AutoInputSupervisorServiceFake(
                auto_input_snapshot_path=str(path),
                now=99.0,
            )
            supervisor = self.supervisor(service)

            def complete_read(*_args: object, **_kwargs: object) -> str:
                service.now = 100.0
                return json.dumps(valid_snapshot())

            with patch.object(Path, "read_text", side_effect=complete_read):
                supervisor.refresh_snapshot()

        self.assertEqual(len(service.runtime.snapshots), 1)
        self.assertEqual(service._auto_input_snapshot_last_seen, 100.0)

    def test_new_helper_generation_may_restart_its_snapshot_sequence(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "snapshot.json"
            write_snapshot(
                path,
                valid_snapshot(
                    snapshot_sequence=1,
                    helper_generation=2,
                ),
                85,
            )
            service = AutoInputSupervisorServiceFake(
                auto_input_snapshot_path=str(path),
                now=100.0,
                _auto_input_helper_generation=2,
                _auto_input_snapshot_last_sequence=99,
                _auto_input_snapshot_generation=1,
                _auto_input_snapshot_runtime_instance_id="instance-1",
            )

            self.supervisor(service).refresh_snapshot()

        self.assertEqual(len(service.runtime.snapshots), 1)
        self.assertEqual(service._auto_input_snapshot_last_sequence, 1)
        self.assertEqual(service._auto_input_snapshot_generation, 2)

    def test_snapshot_file_signature_and_change_detection_are_exact(self) -> None:
        service = AutoInputSupervisorServiceFake(_auto_input_snapshot_mtime_ns=10)
        runtime = self.supervisor(service).snapshot_runtime
        stat_result = SimpleNamespace(st_mtime_ns=10, st_ino=20, st_size=30, st_ctime_ns=40)
        with patch("venus_evcharger.inputs.supervisor_snapshot_runtime.os.stat", return_value=stat_result):
            metadata = runtime._snapshot_file_metadata("/run/snapshot.json")
        self.assertEqual(metadata, (10, (10, 20, 30, 40)))
        assert metadata is not None

        self.assertFalse(runtime._snapshot_file_changed("/run/snapshot.json", metadata))
        runtime._last_snapshot_signature = metadata[1]
        self.assertFalse(runtime._snapshot_file_changed("/run/snapshot.json", metadata))
        self.assertTrue(runtime._snapshot_file_changed("/run/snapshot.json", (10, (10, 21, 30, 40))))
        self.assertTrue(runtime._snapshot_file_changed("/run/snapshot.json", (11, (11, 20, 30, 40))))

        with patch("venus_evcharger.inputs.supervisor_snapshot_runtime.os.stat", side_effect=OSError("missing")):
            self.assertIsNone(runtime._snapshot_file_metadata("/missing/snapshot.json"))

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

    def test_epoch_regression_is_accepted_but_future_monotonic_time_is_rejected(self) -> None:
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
            self.assertEqual(service.runtime.snapshots[-1]["captured_at"], 100.0)

            write_snapshot(
                path,
                valid_snapshot(
                    snapshot_sequence=2,
                    captured_at=50.0,
                    heartbeat_at=50.0,
                    captured_monotonic=101.1,
                    heartbeat_monotonic=101.1,
                    pv_captured_at=50.0,
                    pv_observed_monotonic=101.1,
                    battery_captured_at=50.0,
                    battery_observed_monotonic=101.1,
                    grid_captured_at=50.0,
                    grid_observed_monotonic=101.1,
                ),
                61,
            )
            supervisor.refresh_snapshot()
            self.assertEqual(
                service.runtime.warnings[-1],
                (
                    "auto-input-helper-future-timestamp",
                    5.0,
                    "Auto input helper snapshot %s moved monotonic freshness into the future: %.3f > %.3f",
                    (str(path), 101.1, 100.0),
                    None,
                ),
            )
        self.assertEqual(len(service.runtime.snapshots), 1)

    def test_timestamp_tolerance_and_equal_capture_are_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "snapshot.json"
            payload = valid_snapshot(
                captured_at=101.0,
                captured_monotonic=101.0,
                heartbeat_at=101.0,
                heartbeat_monotonic=101.0,
                pv_captured_at=101.0,
                pv_observed_monotonic=101.0,
                battery_captured_at=101.0,
                battery_observed_monotonic=101.0,
                grid_captured_at=101.0,
                grid_observed_monotonic=101.0,
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

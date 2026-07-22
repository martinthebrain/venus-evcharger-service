# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import MagicMock, patch

from venus_evcharger.inputs.helper.liveness import HelperLiveness, WarningThrottle
from venus_evcharger.inputs.helper.snapshot import AtomicSnapshotWriter, SnapshotStore, empty_snapshot
from venus_evcharger.inputs.helper.sources import AutoInputSources
from venus_evcharger.ipc.energy import MeasuredValue
from tests.support.auto_input_helper import (
    FakeEnergyGateway,
    FakeLoop,
    FakeSnapshots,
    FakeSources,
    MemoryWriter,
    helper_settings,
    run_callback,
)


class AutoInputHelperSnapshotContracts(unittest.TestCase):
    def test_collect_reads_due_sources_and_stamps_identity(self) -> None:
        settings = helper_settings()
        sources = FakeSources()
        writer = MemoryWriter()
        store = SnapshotStore(settings, sources, writer, lambda: False)
        snapshot = store.collect(100.0)
        self.assertEqual(snapshot["pv_power"], 100.0)
        self.assertEqual(snapshot["battery_soc"], 50.0)
        self.assertEqual(snapshot["grid_gateway_power"], -20.0)
        self.assertEqual(snapshot["heartbeat_at"], 100.0)
        self.assertEqual(snapshot["runtime_instance_id"], "test-instance")
        self.assertEqual(writer.payloads, [])

    def test_collect_keeps_not_yet_due_source(self) -> None:
        settings = helper_settings()
        sources = FakeSources()
        store = SnapshotStore(settings, sources, MemoryWriter(), lambda: False)
        first = store.collect(100.0)
        sources.pv = 200.0
        second = store.collect(100.1)
        self.assertEqual(first["pv_power"], second["pv_power"])

    def test_poll_publishes_due_sources_and_stops_without_more_work(self) -> None:
        writer = MemoryWriter()
        stop_requested = MagicMock(side_effect=[False, True])
        store = SnapshotStore(helper_settings(), FakeSources(), writer, stop_requested)
        with patch("venus_evcharger.inputs.helper.snapshot.time.time", return_value=100.0):
            self.assertTrue(store.poll())
            self.assertFalse(store.poll())
        self.assertEqual(len(writer.payloads), 1)
        self.assertEqual(writer.payloads[0]["battery_soc"], 50.0)
        self.assertEqual(writer.payloads[0]["battery_captured_at"], 100.0)

    def test_refresh_source_writes_and_updates_battery_fields(self) -> None:
        sources = FakeSources()
        sources.battery = {"battery_soc": 66.0, "battery_source_count": 2}
        writer = MemoryWriter()
        store = SnapshotStore(helper_settings(), sources, writer, lambda: False)
        store.refresh_source("battery", 10.0)
        self.assertEqual(writer.payloads[-1]["battery_soc"], 66.0)
        self.assertEqual(writer.payloads[-1]["battery_source_count"], 2)
        self.assertEqual(writer.payloads[-1]["battery_status"], "ok")
        store.refresh_source("unknown", 11.0)
        self.assertEqual(len(writer.payloads), 1)

    def test_missing_source_marks_status_and_capture_time(self) -> None:
        sources = FakeSources()
        sources.pv = None
        writer = MemoryWriter()
        store = SnapshotStore(helper_settings(), sources, writer, lambda: False)
        store.refresh_source("pv", 10.0)
        self.assertEqual(writer.payloads[-1]["pv_status"], "missing")
        self.assertIsNone(writer.payloads[-1]["pv_captured_at"])

    def test_refresh_all_validation_and_current_are_public_snapshot_contracts(self) -> None:
        writer = MemoryWriter()
        stopped = True
        store = SnapshotStore(helper_settings(), FakeSources(), writer, lambda: stopped)
        with patch("venus_evcharger.inputs.helper.snapshot.time.time", return_value=12.0):
            self.assertFalse(store.validation_poll())
        self.assertEqual(len(writer.payloads), 3)
        self.assertEqual(store.current()["captured_at"], 12.0)

    def test_heartbeat_and_lifecycle_preserve_values(self) -> None:
        writer = MemoryWriter()
        stopped = False
        store = SnapshotStore(helper_settings(), FakeSources(), writer, lambda: stopped)
        store.refresh_source("pv", 10.0)
        with patch("venus_evcharger.inputs.helper.snapshot.time.time", return_value=20.0):
            self.assertTrue(store.heartbeat())
        store.write_lifecycle("initializing", 30.0)
        self.assertEqual(writer.payloads[-1]["helper_state"], "initializing")
        self.assertEqual(writer.payloads[-1]["pv_power"], 100.0)

    def test_atomic_writer_deduplicates_identical_payload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "snapshot.json"
            settings = replace(helper_settings(), snapshot_path=str(path))
            writer = AtomicSnapshotWriter(settings)
            with patch("venus_evcharger.inputs.helper.snapshot.write_text_atomically") as write:
                writer.write({"captured_at": 1.0})
                writer.write({"captured_at": 1.0})
        write.assert_called_once()
        payload = json.loads(write.call_args.args[1])
        self.assertEqual(payload["helper_generation"], 3)

    def test_empty_snapshot_has_supervisor_liveness_contract(self) -> None:
        snapshot = empty_snapshot(5.0)
        self.assertEqual(snapshot["snapshot_version"], 1)
        self.assertEqual(snapshot["captured_at"], 5.0)
        self.assertEqual(snapshot["helper_status"], "starting")

    def test_composed_source_boundary_uses_only_semantic_measurements(self) -> None:
        gateway = FakeEnergyGateway()
        gateway.measurements = {
            "pv": MeasuredValue(50.0, 99.0, "fresh", 1.0),
            "grid": MeasuredValue(-10.0, 99.0, "fresh", 1.0),
            "battery": MeasuredValue(60.0, 99.0, "fresh", 1.0),
        }
        sources = AutoInputSources(helper_settings(), gateway)
        with patch("venus_evcharger.inputs.helper.sources.time.time", return_value=100.0):
            sources.prepare_cycle()
            self.assertEqual(sources.pv_power(), 50.0)
            self.assertEqual(sources.grid_power(), -10.0)
            self.assertEqual(sources.battery_snapshot()["battery_soc"], 60.0)


class AutoInputHelperLivenessContracts(unittest.TestCase):
    def test_start_requires_bound_components(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "must be bound"):
            HelperLiveness(helper_settings()).start()

    def test_request_stop_quits_bound_loop_via_glib(self) -> None:
        liveness = HelperLiveness(helper_settings())
        loop = FakeLoop()
        liveness.bind(FakeSnapshots(), loop)
        with patch("venus_evcharger.inputs.helper.liveness.GLIB_RUNTIME.idle_add", side_effect=run_callback):
            liveness.request_stop()
        self.assertTrue(liveness.stop_requested())
        self.assertEqual(loop.quit_calls, 1)

    def test_request_stop_without_loop_is_still_idempotent(self) -> None:
        liveness = HelperLiveness(helper_settings())
        liveness.request_stop()
        self.assertTrue(liveness.stop_requested())

    def test_start_and_stop_own_both_threads(self) -> None:
        liveness = HelperLiveness(helper_settings())
        liveness.bind(FakeSnapshots(), FakeLoop())
        first = MagicMock()
        second = MagicMock()
        first.is_alive.return_value = True
        second.is_alive.return_value = False
        with patch("venus_evcharger.inputs.helper.liveness.threading.Thread", side_effect=[first, second]):
            liveness.start()
        first.start.assert_called_once_with()
        second.start.assert_called_once_with()
        liveness.stop()
        first.join.assert_called_once_with(timeout=1.0)
        second.join.assert_not_called()

    def test_thread_loops_stop_refresh_and_request_parent_shutdown(self) -> None:
        liveness = HelperLiveness(helper_settings(parent_pid=7))
        snapshots = FakeSnapshots()
        loop = FakeLoop()
        liveness.bind(snapshots, loop)
        event = MagicMock()
        event.wait.side_effect = [False, True]
        liveness._thread_stop = event
        liveness._heartbeat_loop()
        self.assertEqual(snapshots.heartbeat_calls, 1)
        event.wait.side_effect = [False]
        with patch.object(liveness, "_parent_alive", return_value=False):
            liveness._parent_watchdog_loop()
        self.assertTrue(liveness.stop_requested())

    def test_thread_loop_guard_branches_are_deterministic(self) -> None:
        liveness = HelperLiveness(helper_settings(parent_pid=7))
        event = MagicMock()
        liveness._thread_stop = event
        liveness._stop_requested = True
        event.wait.side_effect = [False]
        liveness._heartbeat_loop()
        event.wait.side_effect = [False]
        liveness._parent_watchdog_loop()
        self.assertFalse(liveness.parent_watchdog_tick())
        liveness._stop_requested = False
        event.wait.side_effect = [True]
        liveness._parent_watchdog_loop()
        event.wait.side_effect = [False, True]
        with patch.object(liveness, "_parent_alive", return_value=True):
            liveness._parent_watchdog_loop()
            self.assertTrue(liveness.parent_watchdog_tick())

    def test_unbound_heartbeat_is_contained(self) -> None:
        liveness = HelperLiveness(helper_settings())
        with patch("venus_evcharger.inputs.helper.liveness.logging.debug") as debug:
            liveness._write_heartbeat_once()
        debug.assert_called_once()

    def test_heartbeat_write_failure_is_contained(self) -> None:
        liveness = HelperLiveness(helper_settings())
        snapshots = FakeSnapshots()
        snapshots.heartbeat_error = RuntimeError("disk")
        liveness.snapshots = snapshots
        with patch("venus_evcharger.inputs.helper.liveness.logging.debug") as debug:
            liveness._write_heartbeat_once()
        debug.assert_called_once()

    def test_parent_alive_accepts_unmanaged_process_and_detects_parent(self) -> None:
        self.assertTrue(HelperLiveness(helper_settings(parent_pid=None))._parent_alive())
        liveness = HelperLiveness(helper_settings(parent_pid=7))
        with patch("venus_evcharger.inputs.helper.liveness.os.getppid", return_value=7):
            self.assertTrue(liveness._parent_alive())
        with patch("venus_evcharger.inputs.helper.liveness.os.getppid", side_effect=OSError("proc")):
            self.assertFalse(liveness._parent_alive())
        liveness.main_loop = None
        with patch("venus_evcharger.inputs.helper.liveness.os.getppid", return_value=8):
            self.assertFalse(liveness.parent_watchdog_tick())

    def test_warning_throttle_logs_once_per_window(self) -> None:
        throttle = WarningThrottle()
        with patch("venus_evcharger.inputs.helper.liveness.time.time", side_effect=[10.0, 11.0, 20.1]), patch(
            "venus_evcharger.inputs.helper.liveness.logging.warning"
        ) as warning:
            throttle("key", 5.0, "problem %s", 1)
            throttle("key", 5.0, "problem %s", 1)
            throttle("key", 5.0, "problem %s", 1)
        self.assertEqual(warning.call_count, 2)


if __name__ == "__main__":
    unittest.main()

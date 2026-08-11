# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import MagicMock, patch

from venus_evcharger.core.shared import AUTO_INPUT_SNAPSHOT_SCHEMA_VERSION
from venus_evcharger.inputs.helper import snapshot as snapshot_module
from venus_evcharger.inputs.helper.liveness import HelperLiveness, WarningThrottle
from venus_evcharger.inputs.helper.snapshot import (
    AtomicSnapshotWriter,
    SnapshotStore,
    _SourceTarget,
)
from venus_evcharger.inputs.helper.snapshot_defaults import (
    BATTERY_SNAPSHOT_FIELDS,
    empty_snapshot,
)
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
    def test_store_initialization_preserves_owned_collaborators_and_schedule(self) -> None:
        settings = helper_settings()
        sources = FakeSources()
        writer = MemoryWriter()
        stop_requested = MagicMock(return_value=False)
        store = SnapshotStore(settings, sources, writer, stop_requested)

        self.assertIs(store.settings, settings)
        self.assertIs(store.sources, sources)
        self.assertIs(store.writer, writer)
        self.assertIs(store.stop_requested, stop_requested)
        self.assertEqual(store._state, empty_snapshot())
        self.assertEqual(store._next_poll_at, {"pv": 0.0, "battery": 0.0, "grid": 0.0})
        self.assertEqual(store._grid_fusion.config, settings.grid_fusion_config)
        atomic_writer = AtomicSnapshotWriter(settings)
        self.assertIsNone(atomic_writer._last_payload)

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
        self.assertEqual(sources.prepared, 1)

    def test_collect_uses_post_read_times_for_freshness_and_next_schedule(self) -> None:
        settings = replace(
            helper_settings(),
            auto_pv_poll_interval_seconds=2.0,
            auto_battery_poll_interval_seconds=3.0,
            auto_grid_poll_interval_seconds=4.0,
        )
        sources = FakeSources()
        sources.observed["battery"] = 7.0
        sources.observed_monotonic_values = {
            "pv": 101.0,
            "battery": 102.0,
            "grid": 103.0,
        }
        store = SnapshotStore(settings, sources, MemoryWriter(), lambda: False)

        with patch(
            "venus_evcharger.inputs.helper.snapshot.time.time",
            side_effect=(10.0, 11.0, 12.0, 13.0, 14.0),
        ), patch(
            "venus_evcharger.inputs.helper.snapshot.time.monotonic",
            side_effect=(110.0, 111.0, 112.0, 113.0, 114.0),
        ):
            snapshot = store.collect()

        self.assertEqual(snapshot["pv_captured_at"], 11.0)
        self.assertEqual(snapshot["pv_observed_monotonic"], 101.0)
        self.assertEqual(snapshot["battery_captured_at"], 7.0)
        self.assertEqual(snapshot["battery_observed_monotonic"], 102.0)
        self.assertEqual(snapshot["grid_gateway_captured_at"], 13.0)
        self.assertEqual(snapshot["grid_observed_monotonic"], 103.0)
        self.assertEqual(snapshot["captured_at"], 14.0)
        self.assertEqual(snapshot["captured_monotonic"], 114.0)
        self.assertEqual(snapshot["heartbeat_at"], 14.0)
        self.assertEqual(snapshot["heartbeat_monotonic"], 114.0)
        self.assertEqual(
            store._next_poll_at,
            {"pv": 113.0, "battery": 115.0, "grid": 117.0},
        )

    def test_collect_keeps_not_yet_due_source(self) -> None:
        settings = helper_settings()
        sources = FakeSources()
        store = SnapshotStore(settings, sources, MemoryWriter(), lambda: False)
        first = store.collect(100.0)
        sources.pv = 200.0
        second = store.collect(100.1)
        self.assertEqual(first["pv_power"], second["pv_power"])

    def test_collection_and_refresh_all_use_cycle_monotonic_fallbacks(self) -> None:
        collected = SnapshotStore(
            helper_settings(),
            FakeSources(),
            MemoryWriter(),
            lambda: False,
        ).collect(25.0)
        self.assertEqual(collected["pv_observed_monotonic"], 25.0)
        self.assertEqual(collected["battery_observed_monotonic"], 25.0)
        self.assertEqual(collected["grid_observed_monotonic"], 25.0)

        writer = MemoryWriter()
        refreshed = SnapshotStore(
            helper_settings(),
            FakeSources(),
            writer,
            lambda: False,
        )
        refreshed.refresh_all(30.0)
        payload = writer.payloads[-1]
        self.assertEqual(payload["pv_observed_monotonic"], 30.0)
        self.assertEqual(payload["battery_observed_monotonic"], 30.0)
        self.assertEqual(payload["grid_observed_monotonic"], 30.0)

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
        self.assertEqual(sources.prepared, 2)

    def test_refresh_source_contracts_cover_each_source_and_observation_time(self) -> None:
        sources = FakeSources()
        sources.observed = {"pv": 8.0, "grid": 9.0}
        sources.observed_monotonic_values = {"pv": 18.0, "grid": 19.0}
        writer = MemoryWriter()
        store = SnapshotStore(helper_settings(), sources, writer, lambda: False)

        store.refresh_source("pv", 10.0)
        store.refresh_source("grid", 11.0)

        self.assertEqual(sources.prepared, 2)
        self.assertEqual(writer.payloads[0]["pv_power"], 100.0)
        self.assertEqual(writer.payloads[0]["pv_captured_at"], 8.0)
        self.assertEqual(writer.payloads[0]["pv_observed_monotonic"], 18.0)
        self.assertEqual(writer.payloads[0]["captured_at"], 10.0)
        self.assertEqual(writer.payloads[0]["captured_monotonic"], 10.0)
        self.assertEqual(writer.payloads[0]["heartbeat_monotonic"], 10.0)
        self.assertEqual(writer.payloads[1]["grid_gateway_power"], -20.0)
        self.assertEqual(writer.payloads[1]["grid_gateway_captured_at"], 9.0)
        self.assertEqual(writer.payloads[1]["grid_observed_monotonic"], 19.0)
        self.assertEqual(writer.payloads[1]["captured_at"], 11.0)
        self.assertEqual(writer.payloads[1]["captured_monotonic"], 11.0)
        self.assertEqual(writer.payloads[1]["heartbeat_monotonic"], 11.0)

    def test_missing_source_marks_status_and_capture_time(self) -> None:
        sources = FakeSources()
        sources.pv = None
        writer = MemoryWriter()
        store = SnapshotStore(helper_settings(), sources, writer, lambda: False)
        store.refresh_source("pv", 10.0)
        self.assertEqual(writer.payloads[-1]["pv_status"], "missing")
        self.assertIsNone(writer.payloads[-1]["pv_captured_at"])
        self.assertIsNone(writer.payloads[-1]["pv_observed_monotonic"])

    def test_refresh_all_validation_and_current_are_public_snapshot_contracts(self) -> None:
        writer = MemoryWriter()
        stopped = True
        sources = FakeSources()
        store = SnapshotStore(helper_settings(), sources, writer, lambda: stopped)
        with patch("venus_evcharger.inputs.helper.snapshot.time.time", return_value=12.0):
            self.assertFalse(store.validation_poll())
        self.assertEqual(len(writer.payloads), 1)
        self.assertEqual(writer.payloads[0]["pv_power"], 100.0)
        self.assertEqual(writer.payloads[0]["pv_captured_at"], 12.0)
        self.assertEqual(writer.payloads[0]["battery_soc"], 50.0)
        self.assertEqual(writer.payloads[0]["battery_captured_at"], 12.0)
        self.assertEqual(writer.payloads[0]["grid_gateway_power"], -20.0)
        self.assertEqual(writer.payloads[0]["grid_gateway_captured_at"], 12.0)
        self.assertEqual(store.current()["captured_at"], 12.0)
        self.assertIsNone(store._prepared_source_sample("unknown", 12.0, 12.0))
        self.assertEqual(sources.prepared, 1)

    def test_refresh_all_uses_one_prepared_cycle_and_source_observation_times(self) -> None:
        sources = FakeSources()
        sources.observed = {"pv": 1.0, "battery": 2.0, "grid": 3.0}
        sources.observed_monotonic_values = {
            "pv": 11.0,
            "battery": 12.0,
            "grid": 13.0,
        }
        writer = MemoryWriter()
        store = SnapshotStore(helper_settings(), sources, writer, lambda: False)

        store.refresh_all(10.0)

        payload = writer.payloads[-1]
        self.assertEqual(sources.prepared, 1)
        self.assertEqual(payload["pv_captured_at"], 1.0)
        self.assertEqual(payload["pv_observed_monotonic"], 11.0)
        self.assertEqual(payload["battery_captured_at"], 2.0)
        self.assertEqual(payload["battery_observed_monotonic"], 12.0)
        self.assertEqual(payload["grid_gateway_captured_at"], 3.0)
        self.assertEqual(payload["grid_observed_monotonic"], 13.0)
        self.assertEqual(payload["captured_at"], 10.0)
        self.assertEqual(payload["captured_monotonic"], 10.0)
        self.assertEqual(payload["heartbeat_at"], 10.0)
        self.assertEqual(payload["heartbeat_monotonic"], 10.0)

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

    def test_heartbeat_and_lifecycle_have_exact_time_and_identity_semantics(self) -> None:
        writer = MemoryWriter()
        store = SnapshotStore(helper_settings(), FakeSources(), writer, lambda: True)

        with (
            patch("venus_evcharger.inputs.helper.snapshot.time.time", return_value=20.0),
            patch(
                "venus_evcharger.inputs.helper.snapshot.time.monotonic",
                return_value=120.0,
            ),
            patch("venus_evcharger.inputs.helper.snapshot.os.getpid", return_value=41),
        ):
            self.assertFalse(store.heartbeat())
        heartbeat = writer.payloads[-1]
        self.assertIsNone(heartbeat["captured_at"])
        self.assertEqual(heartbeat["heartbeat_at"], 20.0)
        self.assertEqual(heartbeat["heartbeat_monotonic"], 120.0)
        self.assertEqual(heartbeat["helper_state"], "starting")
        self.assertEqual(heartbeat["helper_status"], "starting")
        self.assertEqual(heartbeat["writer_pid"], 41)
        self.assertEqual(heartbeat["helper_generation"], 3)
        self.assertEqual(heartbeat["runtime_instance_id"], "test-instance")

        with patch("venus_evcharger.inputs.helper.snapshot.os.getpid", return_value=42):
            store.write_lifecycle("stopping", 30.0)
        lifecycle = writer.payloads[-1]
        self.assertEqual(lifecycle["captured_at"], 30.0)
        self.assertEqual(lifecycle["captured_monotonic"], 30.0)
        self.assertEqual(lifecycle["heartbeat_at"], 30.0)
        self.assertEqual(lifecycle["heartbeat_monotonic"], 30.0)
        self.assertEqual(lifecycle["helper_state"], "stopping")
        self.assertEqual(lifecycle["helper_status"], "stopping")
        self.assertEqual(lifecycle["writer_pid"], 42)
        self.assertEqual(store.current(), lifecycle)

    def test_heartbeat_initializes_absent_lifecycle_labels(self) -> None:
        writer = MemoryWriter()
        store = SnapshotStore(helper_settings(), FakeSources(), writer, lambda: False)
        del store._state["helper_state"]
        del store._state["helper_status"]
        with patch("venus_evcharger.inputs.helper.snapshot.time.time", return_value=20.0):
            self.assertTrue(store.heartbeat())
        self.assertEqual(writer.payloads[-1]["helper_state"], "running")
        self.assertEqual(writer.payloads[-1]["helper_status"], "running")
        self.assertNotIn(None, writer.payloads[-1])

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

    def test_atomic_writer_normalizes_exact_identity_without_mutating_input(self) -> None:
        settings = replace(helper_settings(), snapshot_path="/run/exact.json")
        writer = AtomicSnapshotWriter(settings)
        original = {
            "captured_at": 1.0,
            "snapshot_version": 9,
            "writer_pid": 10,
            "helper_generation": 11,
            "runtime_instance_id": "old",
        }
        with (
            patch("venus_evcharger.inputs.helper.snapshot.os.getpid", return_value=77),
            patch("venus_evcharger.inputs.helper.snapshot.write_text_atomically") as write,
        ):
            writer.write(original)
            writer.write({"captured_at": 2.0})

        self.assertEqual(
            original,
            {
                "captured_at": 1.0,
                "snapshot_version": 9,
                "writer_pid": 10,
                "helper_generation": 11,
                "runtime_instance_id": "old",
            },
        )
        self.assertEqual(write.call_count, 2)
        self.assertEqual(write.call_args_list[0].args[0], "/run/exact.json")
        self.assertEqual(
            json.loads(write.call_args_list[0].args[1]),
            {
                "captured_at": 1.0,
                "snapshot_version": 9,
                "snapshot_sequence": 1,
                "writer_pid": 77,
                "helper_generation": 3,
                "runtime_instance_id": "test-instance",
            },
        )
        self.assertEqual(
            json.loads(write.call_args_list[1].args[1]),
            {
                "captured_at": 2.0,
                "snapshot_version": AUTO_INPUT_SNAPSHOT_SCHEMA_VERSION,
                "snapshot_sequence": 2,
                "writer_pid": 77,
                "helper_generation": 3,
                "runtime_instance_id": "test-instance",
            },
        )

    def test_empty_snapshot_has_supervisor_liveness_contract(self) -> None:
        snapshot = empty_snapshot(5.0)
        self.assertEqual(
            snapshot["snapshot_version"],
            AUTO_INPUT_SNAPSHOT_SCHEMA_VERSION,
        )
        self.assertEqual(snapshot["captured_at"], 5.0)
        self.assertEqual(snapshot["helper_status"], "starting")

    def test_empty_snapshot_is_one_exact_complete_boundary_contract(self) -> None:
        with patch("venus_evcharger.inputs.helper.snapshot.os.getpid", return_value=81):
            snapshot = empty_snapshot(5.0)
        self.assertEqual(
            snapshot,
            {
                "snapshot_version": AUTO_INPUT_SNAPSHOT_SCHEMA_VERSION,
                "snapshot_sequence": 0,
                "captured_at": 5.0,
                "captured_monotonic": 5.0,
                "heartbeat_at": 5.0,
                "heartbeat_monotonic": 5.0,
                "writer_pid": 81,
                "helper_state": "starting",
                "helper_status": "starting",
                "pv_status": "missing",
                "pv_captured_at": None,
                "pv_observed_monotonic": None,
                "pv_power": None,
                "battery_status": "missing",
                "battery_captured_at": None,
                "battery_observed_monotonic": None,
                "battery_soc": None,
                "battery_combined_soc": None,
                "battery_combined_usable_capacity_wh": None,
                "battery_combined_charge_power_w": None,
                "battery_combined_discharge_power_w": None,
                "battery_combined_net_power_w": None,
                "battery_combined_ac_power_w": None,
                "battery_source_count": 0,
                "battery_online_source_count": 0,
                "battery_valid_soc_source_count": 0,
                "battery_sources": [],
                "battery_learning_profiles": {},
                "grid_status": "missing",
                "grid_captured_at": None,
                "grid_observed_monotonic": None,
                "grid_power": None,
                "grid_gateway_captured_at": None,
                "grid_gateway_power": None,
                "grid_primary_captured_at": None,
                "grid_primary_power": None,
                "grid_fusion_enabled": False,
                "grid_fusion_primary_source_id": "",
                "grid_fusion_backup_source_id": "victron",
                "grid_selected_source_id": "",
                "grid_fusion_state": "unavailable",
                "grid_fusion_confidence": 0.0,
                "grid_fusion_primary_valid": False,
                "grid_fusion_backup_valid": False,
                "grid_fusion_primary_age_seconds": None,
                "grid_fusion_backup_age_seconds": None,
                "grid_fusion_difference_watts": None,
                "grid_fusion_tolerance_watts": None,
                "grid_fusion_primary_invalid_samples": 0,
                "grid_fusion_primary_recovery_samples": 0,
                "grid_fusion_mismatch_samples": 0,
            },
        )

    def test_private_snapshot_primitives_have_exact_contracts(self) -> None:
        explicit, explicit_clock, explicit_monotonic, explicit_monotonic_clock = (
            snapshot_module._collection_clock(3.5)
        )
        self.assertEqual(
            (
                explicit,
                explicit_clock(),
                explicit_monotonic,
                explicit_monotonic_clock(),
            ),
            (3.5, 3.5, 3.5, 3.5),
        )
        with patch(
            "venus_evcharger.inputs.helper.snapshot.time.time",
            side_effect=(4.0, 5.0),
        ), patch(
            "venus_evcharger.inputs.helper.snapshot.time.monotonic",
            side_effect=(14.0, 15.0),
        ):
            dynamic, dynamic_clock, dynamic_monotonic, dynamic_monotonic_clock = (
                snapshot_module._collection_clock(None)
            )
            self.assertEqual(
                (
                    dynamic,
                    dynamic_clock(),
                    dynamic_monotonic,
                    dynamic_monotonic_clock(),
                ),
                (4.0, 5.0, 14.0, 15.0),
            )

        sources = FakeSources()
        sources.observed["pv"] = 6.0
        self.assertEqual(snapshot_module._source_observed_at(sources, "pv", 7.0), 6.0)
        sources.observed["pv"] = 0.0
        self.assertEqual(snapshot_module._source_observed_at(sources, "pv", 7.0), 7.0)

        with patch.object(
            sources,
            "observed_monotonic",
            return_value=16.0,
        ) as observed_monotonic:
            self.assertEqual(
                snapshot_module._source_observed_monotonic(sources, "pv", 17.0),
                16.0,
            )
        observed_monotonic.assert_called_once_with("pv")
        with patch.object(sources, "observed_monotonic", return_value=None):
            self.assertEqual(
                snapshot_module._source_observed_monotonic(sources, "grid", 18.0),
                18.0,
            )

        store = SnapshotStore(helper_settings(), sources, MemoryWriter(), lambda: False)
        stamped: dict[str, object] = {}
        store._stamp(stamped, 8.0, 18.0)
        self.assertEqual(stamped["captured_at"], 8.0)
        self.assertEqual(stamped["captured_monotonic"], 18.0)
        self.assertEqual(stamped["heartbeat_at"], 8.0)
        self.assertEqual(stamped["heartbeat_monotonic"], 18.0)

        finalized: dict[str, object] = {}
        with patch(
            "venus_evcharger.inputs.helper.snapshot.apply_grid_fusion"
        ) as apply_fusion:
            store._finalize(finalized, 9.0, 19.0)
        apply_fusion.assert_called_once_with(store._grid_fusion, finalized, 9.0)
        self.assertEqual(finalized["captured_at"], 9.0)
        self.assertEqual(finalized["captured_monotonic"], 19.0)
        self.assertEqual(finalized["heartbeat_at"], 9.0)
        self.assertEqual(finalized["heartbeat_monotonic"], 19.0)

        sources.observed = {"pv": 10.0}
        sources.observed_monotonic_values = {"pv": 20.0}
        self.assertEqual(
            store._prepared_source_sample("pv", 11.0, 21.0),
            (
                100.0,
                _SourceTarget(
                    "pv",
                    "pv_power",
                    "pv_captured_at",
                    "pv_observed_monotonic",
                ),
                10.0,
                20.0,
            ),
        )

        target = _SourceTarget(
            "pv",
            "pv_power",
            "pv_captured_at",
            "pv_observed_monotonic",
        )
        state = empty_snapshot()
        SnapshotStore._apply_source(state, target, 123.0, 8.0, 18.0)
        self.assertEqual(state["pv_power"], 123.0)
        self.assertEqual(state["pv_captured_at"], 8.0)
        self.assertEqual(state["pv_observed_monotonic"], 18.0)
        self.assertEqual(state["pv_status"], "ok")
        self.assertEqual(state["helper_state"], "running")
        self.assertEqual(state["helper_status"], "running")

    def test_battery_application_projects_every_declared_field(self) -> None:
        value = {
            field_name: index + 0.5
            for index, field_name in enumerate(BATTERY_SNAPSHOT_FIELDS)
        }
        state = empty_snapshot()
        SnapshotStore._apply_source(
            state,
            _SourceTarget(
                "battery",
                "battery_soc",
                "battery_captured_at",
                "battery_observed_monotonic",
            ),
            value,
            9.0,
            19.0,
        )
        self.assertEqual(state["battery_captured_at"], 9.0)
        self.assertEqual(state["battery_observed_monotonic"], 19.0)
        self.assertEqual(state["battery_status"], "ok")
        for field_name, expected in value.items():
            self.assertEqual(state[field_name], expected)

    def test_due_source_and_direct_read_contracts_are_independent(self) -> None:
        settings = replace(
            helper_settings(),
            auto_pv_poll_interval_seconds=1.0,
            auto_battery_poll_interval_seconds=2.0,
            auto_grid_poll_interval_seconds=3.0,
        )
        sources = FakeSources()
        store = SnapshotStore(settings, sources, MemoryWriter(), lambda: False)
        store._next_poll_at = {"pv": 5.0, "battery": 6.0, "grid": 7.0}

        due = store._due_sources(7.0)
        self.assertEqual(
            tuple((spec.target, spec.interval) for spec in due),
            (
                (
                    _SourceTarget(
                        "pv",
                        "pv_power",
                        "pv_captured_at",
                        "pv_observed_monotonic",
                    ),
                    1.0,
                ),
                (
                    _SourceTarget(
                        "battery",
                        "battery_soc",
                        "battery_captured_at",
                        "battery_observed_monotonic",
                    ),
                    2.0,
                ),
                (
                    _SourceTarget(
                        "grid",
                        "grid_gateway_power",
                        "grid_gateway_captured_at",
                        "grid_observed_monotonic",
                    ),
                    3.0,
                ),
            ),
        )
        self.assertEqual(due[0].getter(), 100.0)
        self.assertEqual(due[1].getter(), {"battery_soc": 50.0})
        self.assertEqual(due[2].getter(), -20.0)
        self.assertEqual(
            store._source_read("pv"),
            (
                100.0,
                _SourceTarget(
                    "pv",
                    "pv_power",
                    "pv_captured_at",
                    "pv_observed_monotonic",
                ),
            ),
        )
        self.assertEqual(
            store._source_read("battery"),
            (
                {"battery_soc": 50.0},
                _SourceTarget(
                    "battery",
                    "battery_soc",
                    "battery_captured_at",
                    "battery_observed_monotonic",
                ),
            ),
        )
        self.assertEqual(
            store._source_read("grid"),
            (
                -20.0,
                _SourceTarget(
                    "grid",
                    "grid_gateway_power",
                    "grid_gateway_captured_at",
                    "grid_observed_monotonic",
                ),
            ),
        )
        self.assertIsNone(store._source_read("unknown"))

    def test_grid_fusion_is_applied_only_to_battery_and_grid_direct_refreshes(self) -> None:
        store = SnapshotStore(helper_settings(), FakeSources(), MemoryWriter(), lambda: False)
        with patch("venus_evcharger.inputs.helper.snapshot.apply_grid_fusion") as apply:
            store.refresh_source("pv", 1.0)
            store.refresh_source("battery", 2.0)
            store.refresh_source("grid", 3.0)
        self.assertEqual(apply.call_count, 2)
        self.assertEqual(apply.call_args_list[0].args[2], 2.0)
        self.assertEqual(apply.call_args_list[1].args[2], 3.0)

    def test_identity_stamp_replaces_an_empty_mapping_exactly(self) -> None:
        store = SnapshotStore(helper_settings(), FakeSources(), MemoryWriter(), lambda: False)
        state: dict[str, object] = {}
        with patch("venus_evcharger.inputs.helper.snapshot.os.getpid", return_value=91):
            store._stamp_identity(state)
        self.assertEqual(
            state,
            {
                "snapshot_version": AUTO_INPUT_SNAPSHOT_SCHEMA_VERSION,
                "writer_pid": 91,
                "helper_generation": 3,
                "runtime_instance_id": "test-instance",
            },
        )

    def test_non_battery_mapping_remains_an_ordinary_source_value(self) -> None:
        state = empty_snapshot()
        value = {"raw": 1}
        SnapshotStore._apply_source(
            state,
            _SourceTarget(
                "pv",
                "pv_power",
                "pv_captured_at",
                "pv_observed_monotonic",
            ),
            value,
            9.0,
            19.0,
        )
        self.assertEqual(state["pv_power"], value)
        self.assertEqual(state["pv_captured_at"], 9.0)
        self.assertEqual(state["pv_status"], "ok")

    def test_composed_source_boundary_uses_only_semantic_measurements(self) -> None:
        gateway = FakeEnergyGateway()
        gateway.measurements = {
            "pv": MeasuredValue(50.0, 99.0, "fresh", 1.0, observed_monotonic=99.0),
            "grid": MeasuredValue(-10.0, 99.0, "fresh", 1.0, observed_monotonic=99.0),
            "battery": MeasuredValue(60.0, 99.0, "fresh", 1.0, observed_monotonic=99.0),
        }
        sources = AutoInputSources(helper_settings(), gateway)
        with patch("venus_evcharger.inputs.helper.sources.time.time", return_value=100.0), patch(
            "venus_evcharger.inputs.helper.sources.time.monotonic",
            return_value=100.0,
        ):
            sources.prepare_cycle()
            self.assertEqual(sources.pv_power(), 50.0)
            self.assertEqual(sources.grid_power(), -10.0)
            self.assertEqual(sources.battery_snapshot()["battery_soc"], 60.0)
        sources.close()


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

# SPDX-License-Identifier: GPL-3.0-or-later
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

from venus_evcharger.inputs.supervisor_snapshot_runtime import _AutoInputSupervisorSnapshotRuntime


class _RuntimeHarness(_AutoInputSupervisorSnapshotRuntime):
    SNAPSHOT_SOURCE_KEYS = ("pv", "battery", "grid")
    FUTURE_TIMESTAMP_TOLERANCE_SECONDS = 1.0

    def __init__(self, service: object) -> None:
        self.service = service


class _Policy:
    @staticmethod
    def liveness_timeout_seconds(value: float) -> float:
        return float(value)


class TestAutoInputSupervisorSnapshotRuntimeContracts(unittest.TestCase):
    def test_snapshot_io_contracts_cover_native_and_fallback_mtime_and_read_errors(self) -> None:
        service = SimpleNamespace(
            _stat_path=MagicMock(return_value=SimpleNamespace(st_mtime_ns=123, st_mtime=9.0)),
            _load_json_file=MagicMock(return_value={"value": 1}),
            auto_input_helper_restart_seconds=0.5,
            _warning_throttled=MagicMock(),
        )
        harness = _RuntimeHarness(service)
        harness._validate_snapshot_dict = MagicMock(return_value={"normalized": True})
        self.assertEqual(harness._snapshot_mtime_ns("path"), 123)
        self.assertEqual(harness._load_snapshot_dict("path"), {"normalized": True})
        service._load_json_file.assert_called_once_with("path")
        harness._validate_snapshot_dict.assert_called_once_with("path", {"value": 1})

        service._stat_path.return_value = SimpleNamespace(st_mtime=2.5)
        self.assertEqual(harness._snapshot_mtime_ns("fallback"), 2_500_000_000)
        service._stat_path.side_effect = OSError("missing")
        self.assertIsNone(harness._snapshot_mtime_ns("missing"))

        default_stat_service = SimpleNamespace()
        default_stat_harness = _RuntimeHarness(default_stat_service)
        with patch(
            "venus_evcharger.inputs.supervisor_snapshot_runtime.os.stat",
            return_value=SimpleNamespace(st_mtime_ns=456, st_mtime=4.0),
        ) as stat_path:
            self.assertEqual(default_stat_harness._snapshot_mtime_ns("default"), 456)
        stat_path.assert_called_once_with("default")

        error = RuntimeError("broken")
        service._load_json_file.side_effect = error
        self.assertIsNone(harness._load_snapshot_dict("bad"))
        service._warning_throttled.assert_called_once_with(
            "auto-input-helper-read-failed",
            1.0,
            "Unable to read auto input helper snapshot %s: %s",
            "bad",
            error,
            exc_info=error,
        )

    def test_source_field_builders_and_timestamp_normalization_are_exact(self) -> None:
        self.assertEqual(
            _RuntimeHarness._empty_snapshot_fields(),
            {
                "pv_captured_at": None,
                "pv_power": None,
                "battery_captured_at": None,
                "battery_soc": None,
                "grid_captured_at": None,
                "grid_power": None,
            },
        )
        snapshot = {
            "pv_captured_at": "1",
            "pv_power": 2,
            "battery_captured_at": "3",
            "battery_soc": 4,
            "grid_captured_at": "5",
            "grid_power": 6,
        }
        fields = _RuntimeHarness._snapshot_value_fields(snapshot)
        self.assertEqual(fields, snapshot)
        self.assertEqual(
            _RuntimeHarness._normalize_source_timestamps(fields),
            {**snapshot, "pv_captured_at": 1.0, "battery_captured_at": 3.0, "grid_captured_at": 5.0},
        )

    def test_snapshot_freshness_uses_heartbeat_age_and_policy_threshold(self) -> None:
        service = SimpleNamespace(auto_input_helper_stale_seconds=10.0)
        harness = _RuntimeHarness(service)
        with patch(
            "venus_evcharger.inputs.supervisor_snapshot_runtime.service_dbus_backpressure_policy",
            return_value=_Policy(),
        ) as policy:
            self.assertEqual(
                harness._snapshot_freshness({"captured_at": 80, "heartbeat_at": 90}, 100.0),
                (80.0, 90.0, False),
            )
            self.assertEqual(
                harness._snapshot_freshness({"captured_at": 80, "heartbeat_at": 89.999}, 100.0),
                (80.0, 89.999, True),
            )
        self.assertEqual(policy.call_args_list, [call(service), call(service)])

        service.auto_input_helper_stale_seconds = 0.5
        with patch(
            "venus_evcharger.inputs.supervisor_snapshot_runtime.service_dbus_backpressure_policy",
            return_value=_Policy(),
        ):
            self.assertEqual(
                harness._snapshot_freshness({"captured_at": 101, "heartbeat_at": 101}, 100.0),
                (101.0, 101.0, False),
            )

    def test_build_and_apply_snapshot_fields_update_every_runtime_attribute(self) -> None:
        service = SimpleNamespace(
            virtual_mode=2,
            _mode_uses_auto_logic=MagicMock(return_value=True),
            _auto_input_snapshot_seen_for_current_helper=False,
            _update_worker_snapshot=MagicMock(),
        )
        harness = _RuntimeHarness(service)
        snapshot = {
            "pv_captured_at": 1.0,
            "pv_power": 2.0,
            "battery_captured_at": 1.0,
            "battery_soc": 50.0,
            "grid_captured_at": 1.0,
            "grid_power": -3.0,
        }
        fields = harness._build_snapshot_fields(snapshot, 10.0, 1.0, False)
        self.assertEqual(fields, {"captured_at": 1.0, "auto_mode_active": True, **snapshot})
        service._mode_uses_auto_logic.assert_called_once_with(2)
        fields.update(
            snapshot_version=1,
            writer_pid=2,
            helper_generation=3,
            runtime_instance_id="instance",
        )
        harness._apply_snapshot(4, 5.0, 10.0, fields, True)
        self.assertEqual(service._auto_input_snapshot_mtime_ns, 4)
        self.assertEqual(service._auto_input_snapshot_last_seen, 5.0)
        self.assertIs(service._auto_input_snapshot_seen_for_current_helper, True)
        self.assertEqual(service._auto_input_snapshot_last_captured_at, 1.0)
        self.assertEqual(service._auto_input_snapshot_version, 1)
        self.assertEqual(service._auto_input_snapshot_writer_pid, 2)
        self.assertEqual(service._auto_input_snapshot_generation, 3)
        self.assertEqual(service._auto_input_snapshot_runtime_instance_id, "instance")
        service._update_worker_snapshot.assert_called_once_with(**fields)

        service._auto_input_snapshot_last_seen = 9.0
        service._auto_input_snapshot_seen_for_current_helper = True
        harness._apply_snapshot(5, None, 11.0, fields, False)
        self.assertEqual(service._auto_input_snapshot_last_seen, 9.0)

        service._auto_input_snapshot_seen_for_current_helper = False
        harness._apply_snapshot(5, None, 11.0, fields, False)
        self.assertIsNone(service._auto_input_snapshot_last_seen)

        service_without_seen_state = SimpleNamespace(
            _auto_input_snapshot_last_seen=9.0,
            _update_worker_snapshot=MagicMock(),
        )
        _RuntimeHarness(service_without_seen_state)._apply_snapshot(5, None, 11.0, fields, False)
        self.assertIsNone(service_without_seen_state._auto_input_snapshot_last_seen)

        stale_fields = harness._build_snapshot_fields(snapshot, 12.0, None, True)
        self.assertEqual(stale_fields["captured_at"], 12.0)
        for key in ("pv_power", "battery_soc", "grid_power"):
            self.assertIsNone(stale_fields[key])

        service_without_mode = SimpleNamespace(
            _mode_uses_auto_logic=MagicMock(side_effect=lambda value: value == 0),
        )
        mode_harness = _RuntimeHarness(service_without_mode)
        self.assertTrue(mode_harness._build_snapshot_fields(snapshot, 12.0, 1.0, False)["auto_mode_active"])
        service_without_mode._mode_uses_auto_logic.assert_called_once_with(0)

    def test_helper_identity_matchers_cover_exact_boundaries(self) -> None:
        process = SimpleNamespace(pid=20)
        service = SimpleNamespace(
            _auto_input_helper_last_start_at=10.0,
            _auto_input_helper_generation=3,
            _auto_input_runtime_instance_id=" instance ",
            _auto_input_helper_process=process,
        )
        harness = _RuntimeHarness(service)
        snapshot = {"helper_generation": 3, "runtime_instance_id": "instance", "writer_pid": 20}
        self.assertTrue(harness._snapshot_after_current_helper_start(service, 10.0))
        self.assertFalse(harness._snapshot_after_current_helper_start(service, 9.999))
        self.assertTrue(harness._snapshot_generation_matches_current_helper(service, snapshot))
        self.assertTrue(harness._snapshot_runtime_instance_matches_current_service(service, snapshot))
        self.assertTrue(harness._snapshot_pid_matches_current_helper(service, snapshot))
        self.assertTrue(harness._snapshot_matches_current_helper(snapshot, 10.0))
        snapshot["writer_pid"] = 21
        self.assertFalse(harness._snapshot_matches_current_helper(snapshot, 10.0))

        service._auto_input_helper_generation = 0
        self.assertTrue(harness._snapshot_generation_matches_current_helper(service, {}))
        service._auto_input_runtime_instance_id = ""
        self.assertFalse(harness._snapshot_runtime_instance_matches_current_service(service, snapshot))
        service._auto_input_helper_process = None
        self.assertTrue(harness._snapshot_pid_matches_current_helper(service, {}))
        service._auto_input_helper_last_start_at = 0.0
        self.assertTrue(harness._snapshot_after_current_helper_start(service, -1.0))

        service._auto_input_helper_last_start_at = None
        self.assertTrue(harness._snapshot_after_current_helper_start(service, -1.0))
        self.assertTrue(harness._snapshot_after_current_helper_start(SimpleNamespace(), -1.0))
        service._auto_input_helper_last_start_at = 0.5
        self.assertFalse(harness._snapshot_after_current_helper_start(service, 0.25))
        service._auto_input_helper_generation = 1
        self.assertFalse(harness._snapshot_generation_matches_current_helper(service, {"helper_generation": 2}))
        self.assertFalse(
            harness._snapshot_runtime_instance_matches_current_service(
                SimpleNamespace(_auto_input_runtime_instance_id="expected"),
                {"runtime_instance_id": "other"},
            )
        )
        self.assertFalse(harness._snapshot_runtime_instance_matches_current_service(SimpleNamespace(), snapshot))
        self.assertFalse(
            harness._snapshot_runtime_instance_matches_current_service(
                SimpleNamespace(),
                {"runtime_instance_id": "XXXX"},
            )
        )
        self.assertFalse(
            harness._snapshot_runtime_instance_matches_current_service(
                SimpleNamespace(_auto_input_runtime_instance_id=None),
                {"runtime_instance_id": None},
            )
        )
        self.assertFalse(
            harness._snapshot_runtime_instance_matches_current_service(
                SimpleNamespace(_auto_input_runtime_instance_id="XXXX"),
                {},
            )
        )

        harness._snapshot_after_current_helper_start = MagicMock(return_value=True)
        harness._snapshot_runtime_instance_matches_current_service = MagicMock(return_value=True)
        harness._snapshot_generation_matches_current_helper = MagicMock(return_value=True)
        harness._snapshot_pid_matches_current_helper = MagicMock(return_value=True)
        self.assertTrue(harness._snapshot_matches_current_helper(snapshot, 20.0))
        harness._snapshot_after_current_helper_start.assert_called_once_with(service, 20.0)
        harness._snapshot_runtime_instance_matches_current_service.assert_called_once_with(service, snapshot)
        harness._snapshot_generation_matches_current_helper.assert_called_once_with(service, snapshot)
        harness._snapshot_pid_matches_current_helper.assert_called_once_with(service, snapshot)

    def test_timestamp_guards_emit_exact_warnings(self) -> None:
        service = SimpleNamespace(
            _auto_input_snapshot_last_captured_at=10.0,
            auto_input_helper_restart_seconds=0.5,
            _warning_throttled=MagicMock(),
        )
        harness = _RuntimeHarness(service)
        self.assertTrue(harness._snapshot_captured_at_monotonic("path", 10.0))
        self.assertFalse(harness._snapshot_captured_at_monotonic("path", 9.0))
        service._warning_throttled.assert_called_once_with(
            "auto-input-helper-captured-at-regressed",
            1.0,
            "Auto input helper snapshot %s moved captured_at backwards from %.3f to %.3f",
            "path",
            10.0,
            9.0,
        )
        service._warning_throttled.reset_mock()
        with patch(
            "venus_evcharger.inputs.supervisor_snapshot_runtime.timestamp_not_future",
            side_effect=[True, False],
        ) as not_future:
            self.assertTrue(harness._snapshot_freshness_not_future("path", 101.0, 100.0))
            self.assertFalse(harness._snapshot_freshness_not_future("path", 101.001, 100.0))
        self.assertEqual(
            not_future.call_args_list,
            [call(101.0, 100.0, 1.0), call(101.001, 100.0, 1.0)],
        )
        service._warning_throttled.assert_called_once_with(
            "auto-input-helper-future-timestamp",
            1.0,
            "Auto input helper snapshot %s moved freshness timestamp into the future: %.3f > %.3f",
            "path",
            101.001,
            100.0,
        )

    def test_identity_and_diagnostic_copy_contracts_are_complete(self) -> None:
        harness = _RuntimeHarness(SimpleNamespace())
        snapshot = {
            "snapshot_version": 2,
            "writer_pid": "3",
            "helper_generation": "4",
            "runtime_instance_id": " id ",
            "pv_status": "ok",
            "battery_status": "missing",
            "grid_status": "ok",
            "helper_state": "running",
            "helper_status": "healthy",
        }
        fields: dict[str, object] = {}
        harness._copy_snapshot_identity_fields(fields, snapshot)
        harness._copy_snapshot_diagnostic_fields(fields, snapshot)
        self.assertEqual(
            fields,
            {
                "snapshot_version": 2,
                "writer_pid": 3,
                "helper_generation": 4,
                "runtime_instance_id": " id ",
                "pv_status": "ok",
                "battery_status": "missing",
                "grid_status": "ok",
                "helper_state": "running",
                "helper_status": "healthy",
            },
        )
        empty_fields: dict[str, object] = {}
        harness._copy_snapshot_identity_fields(
            empty_fields,
            {"snapshot_version": 1, "runtime_instance_id": None},
        )
        self.assertEqual(empty_fields["runtime_instance_id"], "")

    def test_refresh_payload_and_public_refresh_orchestrate_exactly(self) -> None:
        service = SimpleNamespace(_auto_input_snapshot_mtime_ns=1)
        harness = _RuntimeHarness(service)
        harness._snapshot_mtime_ns = MagicMock(return_value=2)
        harness._load_snapshot_dict = MagicMock(return_value={"snapshot_version": 1})
        harness._snapshot_freshness = MagicMock(return_value=(10.0, 11.0, False))
        harness._snapshot_timestamps_valid = MagicMock(return_value=True)
        harness._build_snapshot_fields = MagicMock(return_value={"captured_at": 10.0})
        harness._copy_snapshot_identity_fields = MagicMock()
        harness._copy_snapshot_diagnostic_fields = MagicMock()
        harness._snapshot_seen_for_current_helper = MagicMock(return_value=True)
        payload = harness._refresh_snapshot_payload("path", 12.0)
        self.assertEqual(payload, (2, 11.0, True, {"captured_at": 10.0}))
        harness._snapshot_mtime_ns.assert_called_once_with("path")
        harness._load_snapshot_dict.assert_called_once_with("path")
        harness._snapshot_freshness.assert_called_once_with({"snapshot_version": 1}, 12.0)
        harness._snapshot_timestamps_valid.assert_called_once_with("path", 10.0, 11.0, 12.0)
        harness._build_snapshot_fields.assert_called_once_with(
            {"snapshot_version": 1},
            12.0,
            10.0,
            False,
        )
        harness._snapshot_seen_for_current_helper.assert_called_once_with(
            {"snapshot_version": 1},
            11.0,
            False,
        )

        service = SimpleNamespace(
            _ensure_worker_state=MagicMock(),
            _time_now=MagicMock(return_value=20.0),
            auto_input_snapshot_path=" path ",
        )
        harness = _RuntimeHarness(service)
        harness._refresh_snapshot_payload = MagicMock(return_value=(2, 19.0, True, {"captured_at": 18.0}))
        harness._apply_snapshot = MagicMock()
        harness.refresh_snapshot()
        service._ensure_worker_state.assert_called_once_with()
        harness._refresh_snapshot_payload.assert_called_once_with("path", 20.0)
        harness._apply_snapshot.assert_called_once_with(2, 19.0, 20.0, {"captured_at": 18.0}, True)

        harness._refresh_snapshot_payload.reset_mock()
        harness._apply_snapshot.reset_mock()
        harness.refresh_snapshot(now=21)
        harness._refresh_snapshot_payload.assert_called_once_with("path", 21.0)

        missing_path_service = SimpleNamespace(
            _ensure_worker_state=MagicMock(),
            _time_now=MagicMock(return_value=30.0),
        )
        missing_path_harness = _RuntimeHarness(missing_path_service)
        missing_path_harness._refresh_snapshot_payload = MagicMock(return_value=None)
        missing_path_harness.refresh_snapshot()
        missing_path_harness._refresh_snapshot_payload.assert_called_once_with("", 30.0)

    def test_timestamp_and_seen_orchestration_short_circuit_contracts(self) -> None:
        harness = _RuntimeHarness(SimpleNamespace())
        harness._snapshot_captured_at_monotonic = MagicMock(return_value=True)
        harness._snapshot_freshness_not_future = MagicMock(return_value=True)
        self.assertTrue(harness._snapshot_timestamps_valid("path", 1.0, 2.0, 3.0))
        harness._snapshot_captured_at_monotonic.assert_called_once_with("path", 1.0)
        harness._snapshot_freshness_not_future.assert_called_once_with("path", 2.0, 3.0)

        harness._snapshot_matches_current_helper = MagicMock(return_value=True)
        self.assertFalse(harness._snapshot_seen_for_current_helper({}, 1.0, True))
        self.assertFalse(harness._snapshot_seen_for_current_helper({}, None, False))
        harness._snapshot_matches_current_helper.assert_not_called()
        self.assertTrue(harness._snapshot_seen_for_current_helper({"id": 1}, 2.0, False))
        harness._snapshot_matches_current_helper.assert_called_once_with({"id": 1}, 2.0)


if __name__ == "__main__":
    unittest.main()

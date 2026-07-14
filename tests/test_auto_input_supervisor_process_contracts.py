# SPDX-License-Identifier: GPL-3.0-or-later
import os
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, call, mock_open, patch

from venus_evcharger.inputs.supervisor_process import _AutoInputSupervisorProcess


class _Harness(_AutoInputSupervisorProcess):
    def __init__(self, service: object) -> None:
        self.service = service

    def refresh_snapshot(self, now: float | None = None) -> None:
        self.refreshed_at = now


class _Policy:
    @staticmethod
    def liveness_timeout_seconds(value: float) -> float:
        return float(value)


class TestAutoInputSupervisorProcessContracts(unittest.TestCase):
    def test_stop_helper_distinguishes_absent_exited_terminate_and_kill(self) -> None:
        ensure = MagicMock()
        service = SimpleNamespace(_ensure_worker_state=ensure, _auto_input_helper_process=None)
        _Harness(service).stop_helper()
        ensure.assert_called_once_with()

        exited = MagicMock(pid=10)
        exited.poll.return_value = 0
        service._auto_input_helper_process = exited
        service._auto_input_helper_restart_requested_at = 1.0
        _Harness(service).stop_helper()
        self.assertIsNone(service._auto_input_helper_process)
        self.assertIsNone(service._auto_input_helper_restart_requested_at)

        running = MagicMock(pid=11)
        running.poll.return_value = None
        service._auto_input_helper_process = running
        _Harness(service).stop_helper(force=False)
        running.terminate.assert_called_once_with()
        running.kill.assert_not_called()

        running.reset_mock()
        service._auto_input_helper_process = running
        _Harness(service).stop_helper(force=True)
        running.kill.assert_called_once_with()
        running.terminate.assert_not_called()

        broken = MagicMock(pid=12)
        broken.poll.return_value = None
        broken.terminate.side_effect = OSError("stuck")
        service._auto_input_helper_process = broken
        with patch("venus_evcharger.inputs.supervisor_process.logging.debug") as debug:
            _Harness(service).stop_helper()
        debug.assert_called_once_with("Unable to stop auto input helper pid=%s: %s", 12, broken.terminate.side_effect)

        missing_pid = MagicMock(spec=["poll", "terminate"])
        missing_pid.poll.return_value = None
        missing_pid.terminate.side_effect = RuntimeError("stuck")
        service._auto_input_helper_process = missing_pid
        with patch("venus_evcharger.inputs.supervisor_process.logging.debug") as debug:
            _Harness(service).stop_helper()
        debug.assert_called_once_with("Unable to stop auto input helper pid=%s: %s", "na", missing_pid.terminate.side_effect)

    def test_helper_command_is_an_exact_process_boundary(self) -> None:
        service = SimpleNamespace(
            _auto_input_helper_path=MagicMock(return_value="/helper.py"),
            _config_path=MagicMock(return_value="/config.ini"),
            auto_input_snapshot_path="/tmp/snapshot.json",
            _auto_input_runtime_instance_id="instance",
        )
        with patch("venus_evcharger.inputs.supervisor_process.os.getpid", return_value=123):
            command = _Harness(service)._helper_command(7)
        self.assertEqual(
            command,
            [
                os.sys.executable,
                "-u",
                "/helper.py",
                "/config.ini",
                "/tmp/snapshot.json",
                "123",
                "7",
                "instance",
            ],
        )

    def test_spawn_helper_performs_lifecycle_steps_and_records_process(self) -> None:
        service = SimpleNamespace(
            _ensure_worker_state=MagicMock(),
            _time_now=MagicMock(return_value=50.0),
            _auto_input_helper_generation=2,
            _auto_input_runtime_instance_id="instance",
            _auto_input_helper_process=None,
            _auto_input_helper_restart_requested_at=1.0,
            auto_input_snapshot_path="/tmp/snapshot.json",
        )
        harness = _Harness(service)
        harness._ensure_runtime_instance_id = MagicMock()
        harness._reset_snapshot_liveness_for_new_helper = MagicMock()
        harness._remove_stale_snapshot_file = MagicMock()
        harness._terminate_orphaned_helpers = MagicMock()
        harness._helper_command = MagicMock(return_value=["helper", "args"])
        process = MagicMock(pid=321)
        with (
            patch("venus_evcharger.inputs.supervisor_process.subprocess.Popen", return_value=process) as popen,
            patch("venus_evcharger.inputs.supervisor_process.logging.info") as info,
        ):
            harness.spawn_helper(now=50)
        harness._ensure_runtime_instance_id.assert_called_once_with()
        harness._reset_snapshot_liveness_for_new_helper.assert_called_once_with()
        harness._remove_stale_snapshot_file.assert_called_once_with()
        harness._terminate_orphaned_helpers.assert_called_once_with()
        harness._helper_command.assert_called_once_with(3)
        popen.assert_called_once_with(["helper", "args"])
        self.assertIs(service._auto_input_helper_process, process)
        self.assertEqual(service._auto_input_helper_generation, 3)
        self.assertEqual(service._auto_input_helper_last_start_at, 50.0)
        self.assertIsNone(service._auto_input_helper_restart_requested_at)
        info.assert_called_once_with(
            "Started auto input helper pid=%s snapshot=%s instance=%s",
            321,
            "/tmp/snapshot.json",
            "instance",
        )

        service._auto_input_helper_generation = None
        with patch("venus_evcharger.inputs.supervisor_process.subprocess.Popen", return_value=process):
            harness.spawn_helper(now=51.5)
        self.assertEqual(service._auto_input_helper_generation, 1)
        self.assertEqual(service._auto_input_helper_last_start_at, 51.5)

    def test_runtime_identity_and_snapshot_liveness_reset_are_exact(self) -> None:
        service = SimpleNamespace(_auto_input_runtime_instance_id="  ")
        harness = _Harness(service)
        with patch("venus_evcharger.inputs.supervisor_process.uuid.uuid4") as uuid4:
            uuid4.return_value.hex = "generated"
            harness._ensure_runtime_instance_id()
        self.assertEqual(service._auto_input_runtime_instance_id, "generated")

        service._auto_input_runtime_instance_id = "existing"
        with patch("venus_evcharger.inputs.supervisor_process.uuid.uuid4") as uuid4:
            harness._ensure_runtime_instance_id()
        uuid4.assert_not_called()

        service._auto_input_snapshot_last_seen = 1
        service._auto_input_snapshot_seen_for_current_helper = True
        service._auto_input_snapshot_writer_pid = 2
        service._auto_input_snapshot_generation = 3
        service._auto_input_snapshot_runtime_instance_id = "old"
        harness._reset_snapshot_liveness_for_new_helper()
        self.assertIsNone(service._auto_input_snapshot_last_seen)
        self.assertFalse(service._auto_input_snapshot_seen_for_current_helper)
        self.assertIsNone(service._auto_input_snapshot_writer_pid)
        self.assertIsNone(service._auto_input_snapshot_generation)
        self.assertIsNone(service._auto_input_snapshot_runtime_instance_id)
        self.assertIs(service._auto_input_snapshot_seen_for_current_helper, False)

    def test_stale_snapshot_removal_is_limited_to_volatile_paths(self) -> None:
        service = SimpleNamespace(auto_input_snapshot_path="/data/snapshot.json", _auto_input_snapshot_mtime_ns=1)
        harness = _Harness(service)
        with patch("venus_evcharger.inputs.supervisor_process.os.unlink") as unlink:
            harness._remove_stale_snapshot_file()
        unlink.assert_not_called()

        service.auto_input_snapshot_path = "/tmp/snapshot.json"
        with patch("venus_evcharger.inputs.supervisor_process.os.unlink") as unlink:
            harness._remove_stale_snapshot_file()
        unlink.assert_called_once_with("/tmp/snapshot.json")
        self.assertIsNone(service._auto_input_snapshot_mtime_ns)
        self.assertTrue(harness._snapshot_path_is_volatile("/run/a"))
        self.assertTrue(harness._snapshot_path_is_volatile("/tmp/a"))
        self.assertTrue(harness._snapshot_path_is_volatile("/var/volatile/a"))
        self.assertFalse(harness._snapshot_path_is_volatile("/data/a"))
        self.assertFalse(harness._stale_snapshot_path_removable(""))

        service._auto_input_snapshot_mtime_ns = 2
        with patch("venus_evcharger.inputs.supervisor_process.os.unlink", side_effect=FileNotFoundError):
            harness._remove_stale_snapshot_file()
        self.assertIsNone(service._auto_input_snapshot_mtime_ns)

        service._auto_input_snapshot_mtime_ns = 3
        error = OSError("denied")
        with (
            patch("venus_evcharger.inputs.supervisor_process.os.unlink", side_effect=error),
            patch("venus_evcharger.inputs.supervisor_process.logging.debug") as debug,
        ):
            harness._remove_stale_snapshot_file()
        debug.assert_called_once_with("Unable to remove stale auto input snapshot %s: %s", "/tmp/snapshot.json", error)

    def test_proc_candidates_and_cmdline_matching_are_exact(self) -> None:
        self.assertIsNone(_Harness._orphan_candidate_pid("self", 10))
        self.assertIsNone(_Harness._orphan_candidate_pid("10", 10))
        self.assertEqual(_Harness._orphan_candidate_pid("11", 10), 11)
        harness = _Harness(SimpleNamespace())
        harness._proc_entries = MagicMock(return_value=["self", "10", "11", "12"])
        self.assertEqual(harness._orphan_candidate_pids(10), [11, 12])

        opener = mock_open(read_data=b"python\x00venus_evcharger_auto_input_helper.py\x00/tmp/snapshot.json\x00")
        with patch("builtins.open", opener):
            self.assertTrue(_Harness._helper_cmdline_matches(11, "/tmp/snapshot.json"))
        opener.assert_called_once_with("/proc/11/cmdline", "rb")
        with patch("builtins.open", mock_open(read_data=b"python\x00other.py\x00")):
            self.assertFalse(_Harness._helper_cmdline_matches(12, "/tmp/snapshot.json"))
        with patch("builtins.open", side_effect=OSError("gone")):
            self.assertFalse(_Harness._helper_cmdline_matches(13, "/tmp/snapshot.json"))
        self.assertEqual(
            _Harness._normalized_helper_cmdline(b"python\x00bad-\xff\x00"),
            "python bad-\ufffd ",
        )
        self.assertEqual(_Harness._process_pid(SimpleNamespace(pid=12)), 12)
        self.assertEqual(_Harness._process_pid(SimpleNamespace()), "na")
        with patch("builtins.open", mock_open(read_data=b"venus_evcharger_auto_input_helper.py\x00")):
            self.assertFalse(_Harness._helper_cmdline_matches(14, "/tmp/snapshot.json"))
        with patch("builtins.open", mock_open(read_data=b"python\x00/tmp/snapshot.json\x00")):
            self.assertFalse(_Harness._helper_cmdline_matches(15, "/tmp/snapshot.json"))
        with patch("venus_evcharger.inputs.supervisor_process.os.listdir", side_effect=OSError("no proc")):
            self.assertEqual(_Harness._proc_entries(), [])
        with patch("venus_evcharger.inputs.supervisor_process.os.listdir", return_value=["1", "self"]) as listdir:
            self.assertEqual(_Harness._proc_entries(), ["1", "self"])
        listdir.assert_called_once_with("/proc")

    def test_orphan_discovery_and_termination_use_exact_pid_contract(self) -> None:
        service = SimpleNamespace(auto_input_snapshot_path=" /tmp/snapshot.json ")
        harness = _Harness(service)
        harness._orphan_candidate_pids = MagicMock(return_value=[10, 11, 12])
        harness._helper_cmdline_matches = MagicMock(side_effect=[False, True, True])
        with patch("venus_evcharger.inputs.supervisor_process.os.getpid", return_value=9):
            self.assertEqual(harness._orphaned_helper_pids(), [11, 12])
        harness._helper_cmdline_matches.assert_has_calls(
            [call(10, "/tmp/snapshot.json"), call(11, "/tmp/snapshot.json"), call(12, "/tmp/snapshot.json")]
        )
        harness._orphan_candidate_pids.assert_called_once_with(9)

        harness._orphaned_helper_pids = MagicMock(return_value=[11, 12])
        with (
            patch("venus_evcharger.inputs.supervisor_process.os.kill", side_effect=[None, ProcessLookupError]) as kill,
            patch("venus_evcharger.inputs.supervisor_process.logging.info") as info,
        ):
            harness._terminate_orphaned_helpers()
        self.assertEqual(kill.call_args_list, [call(11, 15), call(12, 15)])
        info.assert_called_once_with("Stopped orphaned auto input helper pid=%s", 11)

        error = OSError("denied")
        harness._orphaned_helper_pids = MagicMock(return_value=[13])
        with (
            patch("venus_evcharger.inputs.supervisor_process.os.kill", side_effect=error),
            patch("venus_evcharger.inputs.supervisor_process.logging.debug") as debug,
        ):
            harness._terminate_orphaned_helpers()
        debug.assert_called_once_with("Unable to stop orphaned auto input helper pid=%s: %s", 13, error)

        harness._orphaned_helper_pids = MagicMock(return_value=[20, 21, 22])
        with patch(
            "venus_evcharger.inputs.supervisor_process.os.kill",
            side_effect=[None, ProcessLookupError, None],
        ) as kill:
            harness._terminate_orphaned_helpers()
        self.assertEqual(kill.call_args_list, [call(20, 15), call(21, 15), call(22, 15)])

    def test_liveness_age_refresh_and_stale_restart_boundaries(self) -> None:
        service = SimpleNamespace(
            _auto_input_snapshot_last_seen=90.0,
            _auto_input_snapshot_seen_for_current_helper=True,
            _auto_input_helper_last_start_at=80.0,
            auto_input_snapshot_path="/tmp/snapshot.json",
            auto_input_helper_stale_seconds=10.0,
            auto_input_helper_restart_seconds=3.0,
            _auto_input_helper_restart_requested_at=None,
            _stop_auto_input_helper=MagicMock(),
        )
        harness = _Harness(service)
        self.assertEqual(harness._helper_snapshot_age(100.0), 10.0)
        harness._refresh_snapshot_for_liveness_check(100.0)
        self.assertEqual(harness.refreshed_at, 100.0)

        process = MagicMock(pid=77)
        with (
            patch("venus_evcharger.inputs.supervisor_process.service_dbus_backpressure_policy", return_value=_Policy()),
            patch("venus_evcharger.inputs.supervisor_process.logging.warning") as warning,
        ):
            self.assertFalse(harness._handle_stale_running_helper(process, 100.0, 10.0))
            self.assertTrue(harness._handle_stale_running_helper(process, 100.001, 10.001))
            service._stop_auto_input_helper.assert_called_once_with(force=False)
            self.assertEqual(service._auto_input_helper_restart_requested_at, 100.001)
            self.assertTrue(harness._handle_stale_running_helper(process, 103.001, 20.0))
            service._stop_auto_input_helper.assert_called_once_with(force=False)
            self.assertTrue(harness._handle_stale_running_helper(process, 103.002, 20.0))
            service._stop_auto_input_helper.assert_called_with(force=True)
        warning.assert_called_once_with("Auto input helper pid=%s stale for %.0fs, restarting", 77, 10.001)

        missing_pid = MagicMock(spec=[])
        service._auto_input_helper_restart_requested_at = None
        with (
            patch("venus_evcharger.inputs.supervisor_process.service_dbus_backpressure_policy", return_value=_Policy()),
            patch("venus_evcharger.inputs.supervisor_process.logging.warning") as warning,
        ):
            harness._handle_stale_running_helper(missing_pid, 110.0, 20.0)
        warning.assert_called_once_with("Auto input helper pid=%s stale for %.0fs, restarting", "na", 20.0)

        service._auto_input_helper_restart_requested_at = 100.0
        service.auto_input_helper_restart_seconds = 1.0
        service._stop_auto_input_helper.reset_mock()
        with patch("venus_evcharger.inputs.supervisor_process.service_dbus_backpressure_policy", return_value=_Policy()):
            harness._handle_stale_running_helper(process, 102.001, 20.0)
        service._stop_auto_input_helper.assert_called_once_with(force=True)

    def test_existing_process_cooldown_and_spawn_failure_contracts(self) -> None:
        process = MagicMock(pid=88)
        process.poll.return_value = 1
        service = SimpleNamespace(
            _auto_input_helper_process=process,
            _auto_input_helper_restart_requested_at=2.0,
            _auto_input_helper_last_start_at=95.0,
            auto_input_helper_restart_seconds=5.0,
            _warning_throttled=MagicMock(),
            _spawn_auto_input_helper=MagicMock(side_effect=RuntimeError("failed")),
        )
        harness = _Harness(service)
        self.assertFalse(harness._handle_existing_helper_process(process, 100.0))
        self.assertIsNone(service._auto_input_helper_process)
        self.assertIsNone(service._auto_input_helper_restart_requested_at)
        with patch("venus_evcharger.inputs.supervisor_process.service_dbus_backpressure_policy", return_value=_Policy()):
            self.assertFalse(harness._helper_restart_cooldown_active(100.0))
            service._auto_input_helper_last_start_at = 96.0
            self.assertTrue(harness._helper_restart_cooldown_active(100.0))
            harness._spawn_helper_with_warning(100.0)
        service._warning_throttled.assert_called_once()
        self.assertEqual(service._warning_throttled.call_args.args[:3], (
            "auto-input-helper-start-failed",
            5.0,
            "Unable to start auto input helper: %s",
        ))
        self.assertEqual(str(service._warning_throttled.call_args.args[3]), "failed")
        self.assertIs(service._warning_throttled.call_args.kwargs["exc_info"], service._spawn_auto_input_helper.side_effect)
        service._spawn_auto_input_helper.assert_called_once_with(100.0)

        service.auto_input_helper_restart_seconds = 0.5
        service._warning_throttled.reset_mock()
        with patch(
            "venus_evcharger.inputs.supervisor_process.service_dbus_backpressure_policy", return_value=_Policy()
        ) as policy_for:
            harness._spawn_helper_with_warning(101.0)
        policy_for.assert_called_once_with(service)
        self.assertEqual(service._warning_throttled.call_args.args[1], 1.0)

    def test_spawn_log_uses_normalized_pid_when_process_has_no_pid(self) -> None:
        service = SimpleNamespace(
            _ensure_worker_state=MagicMock(),
            _auto_input_helper_generation=0,
            _auto_input_runtime_instance_id="instance",
            _auto_input_helper_restart_requested_at=None,
            auto_input_snapshot_path="/tmp/snapshot.json",
        )
        harness = _Harness(service)
        harness._reset_snapshot_liveness_for_new_helper = MagicMock()
        harness._remove_stale_snapshot_file = MagicMock()
        harness._terminate_orphaned_helpers = MagicMock()
        harness._helper_command = MagicMock(return_value=["helper"])
        process = MagicMock(spec=[])
        with (
            patch("venus_evcharger.inputs.supervisor_process.subprocess.Popen", return_value=process),
            patch("venus_evcharger.inputs.supervisor_process.logging.info") as info,
        ):
            harness.spawn_helper(now=1.0)
        info.assert_called_once_with(
            "Started auto input helper pid=%s snapshot=%s instance=%s",
            "na",
            "/tmp/snapshot.json",
            "instance",
        )

    def test_existing_running_process_and_ensure_process_branch_contracts(self) -> None:
        process = MagicMock(pid=90)
        process.poll.return_value = None
        service = SimpleNamespace(
            _ensure_worker_state=MagicMock(),
            _time_now=MagicMock(return_value=100.0),
            _auto_input_helper_process=process,
        )
        harness = _Harness(service)
        harness._refresh_snapshot_for_liveness_check = MagicMock()
        harness._helper_snapshot_age = MagicMock(return_value=2.0)
        harness._handle_stale_running_helper = MagicMock(return_value=False)
        self.assertTrue(harness._handle_existing_helper_process(process, 100.0))
        harness._refresh_snapshot_for_liveness_check.assert_called_once_with(100.0)
        harness._helper_snapshot_age.assert_called_once_with(100.0)
        harness._handle_stale_running_helper.assert_called_once_with(process, 100.0, 2.0)

        harness._handle_existing_helper_process = MagicMock(return_value=True)
        harness._helper_restart_cooldown_active = MagicMock()
        harness._spawn_helper_with_warning = MagicMock()
        harness.ensure_helper_process(now=101)
        service._ensure_worker_state.assert_called_once_with()
        harness._handle_existing_helper_process.assert_called_once_with(process, 101.0)
        harness._helper_restart_cooldown_active.assert_not_called()
        harness._spawn_helper_with_warning.assert_not_called()

        service._auto_input_helper_process = None
        harness._helper_restart_cooldown_active = MagicMock(return_value=False)
        harness._spawn_helper_with_warning.reset_mock()
        harness.ensure_helper_process(now=102)
        harness._helper_restart_cooldown_active.assert_called_once_with(102.0)
        harness._spawn_helper_with_warning.assert_called_once_with(102.0)

    def test_process_edge_boundaries_cover_missing_paths_start_age_and_exit_log(self) -> None:
        missing = SimpleNamespace(_auto_input_snapshot_mtime_ns=1)
        harness = _Harness(missing)
        self.assertEqual(harness._orphan_snapshot_path(), "")
        with patch("venus_evcharger.inputs.supervisor_process.os.unlink") as unlink:
            harness._remove_stale_snapshot_file()
        unlink.assert_not_called()
        harness._refresh_snapshot_for_liveness_check(1.0)
        self.assertFalse(hasattr(harness, "refreshed_at"))

        service = SimpleNamespace(
            _auto_input_snapshot_last_seen=None,
            _auto_input_helper_last_start_at=0.5,
        )
        harness = _Harness(service)
        self.assertEqual(harness._helper_snapshot_age(1.0), 0.5)

        process = MagicMock(pid=99)
        process.poll.return_value = 7
        service._auto_input_helper_process = process
        service._auto_input_helper_restart_requested_at = 1.0
        with patch("venus_evcharger.inputs.supervisor_process.logging.warning") as warning:
            self.assertFalse(harness._handle_existing_helper_process(process, 2.0))
        warning.assert_called_once_with("Auto input helper exited with rc=%s pid=%s", 7, 99)

        missing_pid = MagicMock(spec=["poll"])
        missing_pid.poll.return_value = 8
        service._auto_input_helper_process = missing_pid
        with patch("venus_evcharger.inputs.supervisor_process.logging.warning") as warning:
            harness._handle_existing_helper_process(missing_pid, 3.0)
        warning.assert_called_once_with("Auto input helper exited with rc=%s pid=%s", 8, "na")

    def test_cooldown_boundaries_use_policy_and_service_identity(self) -> None:
        service = SimpleNamespace(_auto_input_helper_last_start_at=5.0, auto_input_helper_restart_seconds=5.0)
        harness = _Harness(service)
        policy = _Policy()
        with patch(
            "venus_evcharger.inputs.supervisor_process.service_dbus_backpressure_policy", return_value=policy
        ) as policy_for:
            self.assertTrue(harness._helper_restart_cooldown_active(9.999))
            self.assertFalse(harness._helper_restart_cooldown_active(10.0))
        self.assertEqual(policy_for.call_args_list, [call(service), call(service)])

        service._auto_input_helper_last_start_at = 0.0
        with patch("venus_evcharger.inputs.supervisor_process.service_dbus_backpressure_policy", return_value=policy):
            self.assertFalse(harness._helper_restart_cooldown_active(1.0))
        service._auto_input_helper_last_start_at = 0.5
        with patch("venus_evcharger.inputs.supervisor_process.service_dbus_backpressure_policy", return_value=policy):
            self.assertTrue(harness._helper_restart_cooldown_active(1.0))


if __name__ == "__main__":
    unittest.main()

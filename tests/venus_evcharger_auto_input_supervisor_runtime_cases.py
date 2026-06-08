# SPDX-License-Identifier: GPL-3.0-or-later
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from venus_evcharger.inputs.supervisor import AutoInputSupervisor


class TestAutoInputSupervisorRuntime(unittest.TestCase):
    def test_coerce_snapshot_timestamp_and_snapshot_mtime_cover_invalid_inputs(self):
        self.assertIsNone(AutoInputSupervisor._coerce_snapshot_timestamp(None))
        self.assertIsNone(AutoInputSupervisor._coerce_snapshot_timestamp(True))
        self.assertIsNone(AutoInputSupervisor._coerce_snapshot_timestamp(False))
        self.assertIsNone(AutoInputSupervisor._coerce_snapshot_timestamp("nope"))
        self.assertIsNone(AutoInputSupervisor._coerce_snapshot_timestamp(float("nan")))
        self.assertIsNone(AutoInputSupervisor._coerce_snapshot_timestamp(float("inf")))
        self.assertEqual(AutoInputSupervisor._coerce_snapshot_timestamp("12.5"), 12.5)

        service = SimpleNamespace(_stat_path=MagicMock(side_effect=OSError("missing")))
        controller = AutoInputSupervisor(service)
        self.assertIsNone(controller._snapshot_mtime_ns("/tmp/missing.json"))

    def test_helper_snapshot_age_falls_back_to_helper_start_time(self):
        service = SimpleNamespace(_auto_input_snapshot_last_seen=None, _auto_input_helper_last_start_at=90.0)
        controller = AutoInputSupervisor(service)
        self.assertEqual(controller._helper_snapshot_age(100.0), 10.0)

    def test_helper_snapshot_age_ignores_previous_helper_snapshot_liveness(self):
        service = SimpleNamespace(
            _auto_input_snapshot_last_seen=10.0,
            _auto_input_snapshot_seen_for_current_helper=False,
            _auto_input_helper_last_start_at=90.0,
        )
        controller = AutoInputSupervisor(service)
        self.assertEqual(controller._helper_snapshot_age(100.0), 10.0)

    def test_snapshot_freshness_not_future_accepts_missing_timestamp(self):
        controller = AutoInputSupervisor(SimpleNamespace())
        self.assertTrue(controller._snapshot_freshness_not_future("/tmp/auto.json", None, 100.0))

    def test_snapshot_path_is_volatile_only_for_ram_paths(self):
        self.assertTrue(AutoInputSupervisor._snapshot_path_is_volatile("/run/dbus-venus-evcharger-auto-60.json"))
        self.assertTrue(AutoInputSupervisor._snapshot_path_is_volatile("/tmp/auto.json"))
        self.assertTrue(AutoInputSupervisor._snapshot_path_is_volatile("/var/volatile/log/auto.json"))
        self.assertFalse(AutoInputSupervisor._snapshot_path_is_volatile("/data/auto.json"))

    def test_ensure_helper_process_restarts_stale_helper(self):
        process = MagicMock()
        process.poll.return_value = None
        process.pid = 4321
        service = SimpleNamespace(
            _ensure_worker_state=MagicMock(),
            _auto_input_helper_process=process,
            _auto_input_helper_last_start_at=10.0,
            _auto_input_helper_restart_requested_at=None,
            _auto_input_snapshot_last_seen=70.0,
            auto_input_helper_stale_seconds=15.0,
            auto_input_helper_restart_seconds=5.0,
            _time_now=MagicMock(return_value=100.0),
            _stop_auto_input_helper=MagicMock(),
            _spawn_auto_input_helper=MagicMock(),
            _warning_throttled=MagicMock(),
        )
        controller = AutoInputSupervisor(service)
        controller.ensure_helper_process()
        service._stop_auto_input_helper.assert_called_once_with(force=False)
        self.assertEqual(service._auto_input_helper_restart_requested_at, 100.0)
        service._spawn_auto_input_helper.assert_not_called()

    def test_ensure_helper_process_refreshes_snapshot_before_stale_decision(self):
        process = MagicMock()
        process.poll.return_value = None
        process.pid = 4321
        service = SimpleNamespace(
            _ensure_worker_state=MagicMock(),
            _auto_input_helper_process=process,
            _auto_input_helper_last_start_at=10.0,
            _auto_input_helper_restart_requested_at=None,
            _auto_input_snapshot_last_seen=70.0,
            _auto_input_snapshot_seen_for_current_helper=True,
            auto_input_snapshot_path="/tmp/auto-helper.json",
            auto_input_helper_stale_seconds=15.0,
            auto_input_helper_restart_seconds=5.0,
            _time_now=MagicMock(return_value=100.0),
            _stop_auto_input_helper=MagicMock(),
            _spawn_auto_input_helper=MagicMock(),
            _warning_throttled=MagicMock(),
        )
        controller = AutoInputSupervisor(service)

        def refresh_snapshot(_current):
            service._auto_input_snapshot_last_seen = 100.0

        with patch.object(controller, "refresh_snapshot", side_effect=refresh_snapshot) as refresh_mock:
            controller.ensure_helper_process()

        refresh_mock.assert_called_once_with(100.0)
        service._stop_auto_input_helper.assert_not_called()
        self.assertIsNone(service._auto_input_helper_restart_requested_at)
        service._spawn_auto_input_helper.assert_not_called()

    def test_stop_helper_covers_none_exited_running_and_force_paths(self):
        service = SimpleNamespace(_ensure_worker_state=MagicMock(), _auto_input_helper_process=None, _auto_input_helper_restart_requested_at="pending")
        controller = AutoInputSupervisor(service)
        controller.stop_helper()
        service._ensure_worker_state.assert_called_once_with()

        exited_process = MagicMock()
        exited_process.poll.return_value = 0
        service = SimpleNamespace(_ensure_worker_state=MagicMock(), _auto_input_helper_process=exited_process, _auto_input_helper_restart_requested_at="pending")
        controller = AutoInputSupervisor(service)
        controller.stop_helper()
        self.assertIsNone(service._auto_input_helper_process)
        self.assertIsNone(service._auto_input_helper_restart_requested_at)

        running_process = MagicMock()
        running_process.poll.return_value = None
        service = SimpleNamespace(_ensure_worker_state=MagicMock(), _auto_input_helper_process=running_process, _auto_input_helper_restart_requested_at=None)
        controller = AutoInputSupervisor(service)
        controller.stop_helper()
        running_process.terminate.assert_called_once_with()

        running_process = MagicMock()
        running_process.poll.return_value = None
        service._auto_input_helper_process = running_process
        controller = AutoInputSupervisor(service)
        controller.stop_helper(force=True)
        running_process.kill.assert_called_once_with()

        broken_process = MagicMock()
        broken_process.poll.return_value = None
        broken_process.terminate.side_effect = RuntimeError("stuck")
        service._auto_input_helper_process = broken_process
        controller = AutoInputSupervisor(service)
        with patch("venus_evcharger.inputs.supervisor.logging.debug") as debug_mock:
            controller.stop_helper()
        debug_mock.assert_called_once()

    def test_ensure_helper_process_handles_exited_process_cooldown_and_spawn_failure(self):
        exited_process = MagicMock()
        exited_process.poll.return_value = 1
        exited_process.pid = 4444
        service = SimpleNamespace(
            _ensure_worker_state=MagicMock(),
            _auto_input_helper_process=exited_process,
            _auto_input_helper_last_start_at=98.0,
            _auto_input_helper_restart_requested_at="pending",
            _auto_input_snapshot_last_seen=None,
            auto_input_helper_stale_seconds=15.0,
            auto_input_helper_restart_seconds=5.0,
            _time_now=MagicMock(return_value=100.0),
            _stop_auto_input_helper=MagicMock(),
            _spawn_auto_input_helper=MagicMock(),
            _warning_throttled=MagicMock(),
        )
        controller = AutoInputSupervisor(service)
        with patch("venus_evcharger.inputs.supervisor.logging.warning") as warning_mock:
            controller.ensure_helper_process()
        warning_mock.assert_called_once()
        self.assertIsNone(service._auto_input_helper_process)
        self.assertIsNone(service._auto_input_helper_restart_requested_at)
        service._spawn_auto_input_helper.assert_not_called()

        service = SimpleNamespace(
            _ensure_worker_state=MagicMock(),
            _auto_input_helper_process=None,
            _auto_input_helper_last_start_at=0.0,
            _auto_input_helper_restart_requested_at=None,
            auto_input_helper_restart_seconds=5.0,
            _time_now=MagicMock(return_value=100.0),
            _spawn_auto_input_helper=MagicMock(side_effect=RuntimeError("spawn failed")),
            _warning_throttled=MagicMock(),
        )
        controller = AutoInputSupervisor(service)
        controller.ensure_helper_process()
        service._warning_throttled.assert_called_once()

    def test_spawn_helper_and_stale_helper_edge_paths(self):
        service = SimpleNamespace(
            _ensure_worker_state=MagicMock(),
            _time_now=MagicMock(return_value=100.0),
            _auto_input_helper_path=MagicMock(return_value="/tmp/helper.py"),
            _config_path=MagicMock(return_value="/tmp/config.ini"),
            auto_input_snapshot_path="/tmp/snapshot.json",
            _auto_input_helper_process=None,
            _auto_input_helper_generation=7,
            _auto_input_runtime_instance_id="instance-1",
            _auto_input_helper_last_start_at=0.0,
            _auto_input_helper_restart_requested_at="pending",
            _auto_input_snapshot_last_seen=75.0,
            _auto_input_snapshot_seen_for_current_helper=True,
            _auto_input_snapshot_mtime_ns=9,
            _auto_input_snapshot_writer_pid=2222,
            _auto_input_snapshot_generation=7,
            _auto_input_snapshot_runtime_instance_id="old-instance",
            _stop_auto_input_helper=MagicMock(),
        )
        process = MagicMock(pid=5555)
        controller = AutoInputSupervisor(service)
        with patch("venus_evcharger.inputs.supervisor.os.getpid", return_value=1234):
            with patch("venus_evcharger.inputs.supervisor.subprocess.Popen", return_value=process) as popen:
                controller.spawn_helper()
        popen.assert_called_once()
        self.assertEqual(popen.call_args.args[0][-2], "8")
        self.assertEqual(popen.call_args.args[0][-1], "instance-1")
        self.assertEqual(service._auto_input_helper_process, process)
        self.assertEqual(service._auto_input_helper_generation, 8)
        self.assertEqual(service._auto_input_helper_last_start_at, 100.0)
        self.assertIsNone(service._auto_input_helper_restart_requested_at)
        self.assertIsNone(service._auto_input_snapshot_last_seen)
        self.assertFalse(service._auto_input_snapshot_seen_for_current_helper)
        self.assertIsNone(service._auto_input_snapshot_writer_pid)
        self.assertIsNone(service._auto_input_snapshot_generation)
        self.assertIsNone(service._auto_input_snapshot_runtime_instance_id)

        process = MagicMock(pid=4444)
        service = SimpleNamespace(
            _auto_input_snapshot_last_seen=None,
            _auto_input_helper_last_start_at=0.0,
            auto_input_helper_stale_seconds=15.0,
            _auto_input_helper_restart_requested_at=None,
            auto_input_helper_restart_seconds=5.0,
            _stop_auto_input_helper=MagicMock(),
        )
        controller = AutoInputSupervisor(service)
        self.assertIsNone(controller._helper_snapshot_age(100.0))
        self.assertFalse(controller._handle_stale_running_helper(process, 100.0, 10.0))
        service._auto_input_helper_restart_requested_at = 90.0
        self.assertTrue(controller._handle_stale_running_helper(process, 100.0, 20.0))
        service._stop_auto_input_helper.assert_called_once_with(force=True)

    def test_handle_existing_helper_and_refresh_snapshot_early_return_paths(self):
        process = MagicMock()
        process.poll.return_value = None
        service = SimpleNamespace(_helper_snapshot_age=MagicMock(), _handle_stale_running_helper=MagicMock(return_value=False))
        controller = AutoInputSupervisor(service)
        with patch.object(controller, "_helper_snapshot_age", return_value=1.0):
            with patch.object(controller, "_handle_stale_running_helper", return_value=False):
                self.assertTrue(controller._handle_existing_helper_process(process, 100.0))

        service = SimpleNamespace(_ensure_worker_state=MagicMock(), auto_input_snapshot_path="", _time_now=MagicMock(return_value=100.0), _auto_input_snapshot_mtime_ns=None)
        controller = AutoInputSupervisor(service)
        controller.refresh_snapshot()
        service._ensure_worker_state.assert_called_once_with()

        service = SimpleNamespace(_ensure_worker_state=MagicMock(), auto_input_snapshot_path="/tmp/snapshot.json", _time_now=MagicMock(return_value=100.0), _snapshot_mtime_ns=MagicMock(return_value=None), _auto_input_snapshot_mtime_ns=None)
        controller = AutoInputSupervisor(service)
        with patch.object(controller, "_snapshot_mtime_ns", return_value=None):
            controller.refresh_snapshot()

        service = SimpleNamespace(_ensure_worker_state=MagicMock(), auto_input_snapshot_path="/tmp/snapshot.json", _time_now=MagicMock(return_value=100.0), _stat_path=MagicMock(return_value=SimpleNamespace(st_mtime_ns=3, st_mtime=0.0)), _auto_input_snapshot_mtime_ns=3)
        controller = AutoInputSupervisor(service)
        controller.refresh_snapshot()

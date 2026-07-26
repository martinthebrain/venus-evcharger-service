# SPDX-License-Identifier: GPL-3.0-or-later
"""Public lifecycle scenarios for the auto-input helper process."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import mock_open, patch

from tests.support.auto_input_supervisor import (
    AutoInputSupervisorServiceFake,
    HelperProcessFake,
    SnapshotRefreshFake,
)
from venus_evcharger.inputs.supervisor_process import AutoInputProcessLifecycle


def lifecycle(
    service: AutoInputSupervisorServiceFake,
    snapshot_runtime: SnapshotRefreshFake | None = None,
) -> AutoInputProcessLifecycle:
    return AutoInputProcessLifecycle(
        service,
        snapshot_runtime or SnapshotRefreshFake(),
        config_path="/config.ini",
        helper_path="/helper.py",
    )


class TestAutoInputSupervisorProcessContracts(unittest.TestCase):
    def test_helper_snapshot_age_is_unknown_before_start_or_snapshot(self) -> None:
        manager = lifecycle(AutoInputSupervisorServiceFake())

        self.assertIsNone(manager._helper_snapshot_age(100.0))

    def test_stop_helper_handles_absent_exited_running_and_forced_processes(self) -> None:
        service = AutoInputSupervisorServiceFake()
        manager = lifecycle(service)
        manager.stop_helper()

        exited = HelperProcessFake(return_code=0)
        service._auto_input_helper_process = exited
        service._auto_input_helper_restart_requested_at = 10.0
        manager.stop_helper()
        self.assertIsNone(service._auto_input_helper_process)
        self.assertIsNone(service._auto_input_helper_restart_requested_at)

        running = HelperProcessFake()
        service._auto_input_helper_process = running
        manager.stop_helper()
        manager.stop_helper(force=True)
        self.assertEqual((running.terminate_calls, running.kill_calls), (1, 1))

        failing = HelperProcessFake(terminate_error=OSError("gone"), kill_error=RuntimeError("gone"))
        service._auto_input_helper_process = failing
        manager.stop_helper()
        manager.stop_helper(force=True)
        self.assertEqual((failing.terminate_calls, failing.kill_calls), (1, 1))

    def test_spawn_builds_exact_command_resets_liveness_and_removes_ram_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            snapshot_path = Path(temp_dir) / "auto.json"
            snapshot_path.write_text("stale", encoding="utf-8")
            service = AutoInputSupervisorServiceFake(
                auto_input_snapshot_path=str(snapshot_path),
                _auto_input_runtime_instance_id="",
                _auto_input_snapshot_last_seen=50.0,
                _auto_input_snapshot_seen_for_current_helper=True,
                _auto_input_snapshot_writer_pid=1,
                _auto_input_snapshot_generation=2,
                _auto_input_snapshot_runtime_instance_id="old",
            )
            process = HelperProcessFake(pid=7654)
            manager = lifecycle(service)
            with patch("venus_evcharger.inputs.supervisor_process.subprocess.Popen", return_value=process) as popen:
                manager.spawn_helper(123.0)

            command = popen.call_args.args[0]
            self.assertEqual(command[2:5], ["/helper.py", "/config.ini", str(snapshot_path)])
            self.assertEqual(command[6], "1")
            self.assertEqual(command[7], service._auto_input_runtime_instance_id)
            self.assertFalse(snapshot_path.exists())
            self.assertIs(service._auto_input_helper_process, process)
            self.assertEqual(service._auto_input_helper_last_start_at, 123.0)
            self.assertEqual(service._auto_input_helper_generation, 1)
            self.assertIsNone(service._auto_input_snapshot_last_seen)
            self.assertFalse(service._auto_input_snapshot_seen_for_current_helper)

    def test_spawn_preserves_nonvolatile_snapshot_and_handles_unlink_errors(self) -> None:
        process = HelperProcessFake()
        service = AutoInputSupervisorServiceFake(auto_input_snapshot_path="/data/auto.json")
        manager = lifecycle(service)
        with patch("venus_evcharger.inputs.supervisor_process.os.unlink") as unlink, patch(
            "venus_evcharger.inputs.supervisor_process.subprocess.Popen",
            return_value=process,
        ):
            manager.spawn_helper(1.0)
        unlink.assert_not_called()

        service.auto_input_snapshot_path = "/tmp/auto.json"
        for error in (FileNotFoundError(), OSError("busy"), RuntimeError("busy")):
            with patch("venus_evcharger.inputs.supervisor_process.os.unlink", side_effect=error), patch(
                "venus_evcharger.inputs.supervisor_process.subprocess.Popen",
                return_value=process,
            ):
                manager.spawn_helper(2.0)
            self.assertIsNone(service._auto_input_snapshot_mtime_ns)

    def test_spawn_discovers_only_matching_orphan_helpers(self) -> None:
        service = AutoInputSupervisorServiceFake(auto_input_snapshot_path="/tmp/current.json")
        process = HelperProcessFake()
        command_line = b"python\x00venus_evcharger_auto_input_helper.py\x00/tmp/current.json\x00"
        with patch("venus_evcharger.inputs.supervisor_process.os.listdir", return_value=["word", "9998", "9999"]), patch(
            "builtins.open",
            mock_open(read_data=command_line),
        ), patch("venus_evcharger.inputs.supervisor_process.os.getpid", return_value=9998), patch(
            "venus_evcharger.inputs.supervisor_process.os.kill"
        ) as kill, patch(
            "venus_evcharger.inputs.supervisor_process.os.unlink",
            side_effect=FileNotFoundError,
        ), patch(
            "venus_evcharger.inputs.supervisor_process.subprocess.Popen",
            return_value=process,
        ):
            lifecycle(service).spawn_helper(3.0)
        kill.assert_called_once_with(9999, 15)

    def test_spawn_tolerates_proc_and_orphan_termination_races(self) -> None:
        service = AutoInputSupervisorServiceFake(auto_input_snapshot_path="/tmp/current.json")
        process = HelperProcessFake()
        with patch("venus_evcharger.inputs.supervisor_process.os.listdir", side_effect=OSError("no proc")), patch(
            "venus_evcharger.inputs.supervisor_process.os.unlink",
            side_effect=FileNotFoundError,
        ), patch(
            "venus_evcharger.inputs.supervisor_process.subprocess.Popen",
            return_value=process,
        ):
            lifecycle(service).spawn_helper(4.0)

        command_line = b"venus_evcharger_auto_input_helper.py\x00/tmp/current.json"
        for error in (ProcessLookupError(), OSError("denied"), RuntimeError("denied")):
            with patch("venus_evcharger.inputs.supervisor_process.os.listdir", return_value=["9999"]), patch(
                "builtins.open",
                mock_open(read_data=command_line),
            ), patch("venus_evcharger.inputs.supervisor_process.os.kill", side_effect=error), patch(
                "venus_evcharger.inputs.supervisor_process.os.unlink",
                side_effect=FileNotFoundError,
            ), patch(
                "venus_evcharger.inputs.supervisor_process.subprocess.Popen",
                return_value=process,
            ):
                lifecycle(service).spawn_helper(5.0)

    def test_spawn_ignores_unreadable_and_nonmatching_process_cmdlines(self) -> None:
        service = AutoInputSupervisorServiceFake(auto_input_snapshot_path="/tmp/current.json")
        process = HelperProcessFake()
        for stream in (OSError("gone"), b"python\x00unrelated.py\x00"):
            opener = patch("builtins.open", side_effect=stream) if isinstance(stream, OSError) else patch(
                "builtins.open",
                mock_open(read_data=stream),
            )
            with patch("venus_evcharger.inputs.supervisor_process.os.listdir", return_value=["9999"]), opener, patch(
                "venus_evcharger.inputs.supervisor_process.os.kill"
            ) as kill, patch(
                "venus_evcharger.inputs.supervisor_process.os.unlink",
                side_effect=FileNotFoundError,
            ), patch(
                "venus_evcharger.inputs.supervisor_process.subprocess.Popen",
                return_value=process,
            ):
                lifecycle(service).spawn_helper(6.0)
            kill.assert_not_called()

    def test_running_helper_refreshes_snapshot_and_remains_alive_when_fresh(self) -> None:
        process = HelperProcessFake()
        snapshot_runtime = SnapshotRefreshFake()
        service = AutoInputSupervisorServiceFake(
            now=100.0,
            _auto_input_helper_process=process,
            _auto_input_helper_last_start_at=90.0,
            _auto_input_snapshot_last_seen=99.0,
            _auto_input_snapshot_seen_for_current_helper=True,
        )
        lifecycle(service, snapshot_runtime).ensure_helper_process()
        self.assertEqual(snapshot_runtime.calls, [100.0])
        self.assertEqual(process.terminate_calls, 0)

    def test_stale_running_helper_terminates_then_forces_after_grace(self) -> None:
        process = HelperProcessFake()
        service = AutoInputSupervisorServiceFake(
            now=100.0,
            _auto_input_helper_process=process,
            _auto_input_helper_last_start_at=50.0,
            _auto_input_snapshot_last_seen=70.0,
            _auto_input_snapshot_seen_for_current_helper=True,
        )
        manager = lifecycle(service)
        manager.ensure_helper_process()
        self.assertEqual(process.terminate_calls, 1)
        self.assertEqual(service._auto_input_helper_restart_requested_at, 100.0)

        service.now = 102.0
        manager.ensure_helper_process()
        self.assertEqual(process.kill_calls, 0)

        service.now = 106.0
        manager.ensure_helper_process()
        self.assertEqual(process.kill_calls, 1)

    def test_missing_gateway_health_extends_stale_and_restart_grace(self) -> None:
        process = HelperProcessFake()
        service = AutoInputSupervisorServiceFake(
            gateway_pressure_policy=None,
            now=100.0,
            _auto_input_helper_process=process,
            _auto_input_helper_last_start_at=50.0,
            _auto_input_snapshot_last_seen=70.0,
            _auto_input_snapshot_seen_for_current_helper=True,
        )
        manager = lifecycle(service)

        manager.ensure_helper_process()
        self.assertEqual(process.terminate_calls, 0)

        service.now = 116.0
        manager.ensure_helper_process()
        self.assertEqual(process.terminate_calls, 1)
        self.assertEqual(service._auto_input_helper_restart_requested_at, 116.0)

        service.now = 131.0
        manager.ensure_helper_process()
        self.assertEqual(process.kill_calls, 0)

        service.now = 132.0
        manager.ensure_helper_process()
        self.assertEqual(process.kill_calls, 1)

    def test_helper_start_time_is_liveness_fallback_until_first_snapshot(self) -> None:
        process = HelperProcessFake()
        service = AutoInputSupervisorServiceFake(
            now=100.0,
            _auto_input_helper_process=process,
            _auto_input_helper_last_start_at=90.0,
            _auto_input_snapshot_last_seen=20.0,
            _auto_input_snapshot_seen_for_current_helper=False,
        )
        lifecycle(service).ensure_helper_process()
        self.assertEqual(process.terminate_calls, 0)

    def test_exited_helper_obeys_restart_cooldown_then_respawns(self) -> None:
        exited = HelperProcessFake(return_code=9)
        service = AutoInputSupervisorServiceFake(
            now=100.0,
            _auto_input_helper_process=exited,
            _auto_input_helper_last_start_at=98.0,
            _auto_input_helper_restart_requested_at=99.0,
        )
        manager = lifecycle(service)
        with patch("venus_evcharger.inputs.supervisor_process.subprocess.Popen") as popen:
            manager.ensure_helper_process()
        popen.assert_not_called()
        self.assertIsNone(service._auto_input_helper_process)

        service.now = 104.0
        spawned = HelperProcessFake(pid=8888)
        with patch("venus_evcharger.inputs.supervisor_process.os.unlink", side_effect=FileNotFoundError), patch(
            "venus_evcharger.inputs.supervisor_process.subprocess.Popen",
            return_value=spawned,
        ):
            manager.ensure_helper_process()
        self.assertIs(service._auto_input_helper_process, spawned)

    def test_missing_gateway_health_extends_exited_helper_restart_cooldown(self) -> None:
        service = AutoInputSupervisorServiceFake(
            gateway_pressure_policy=None,
            now=110.0,
            _auto_input_helper_process=HelperProcessFake(return_code=9),
            _auto_input_helper_last_start_at=98.0,
        )
        manager = lifecycle(service)
        with patch("venus_evcharger.inputs.supervisor_process.subprocess.Popen") as popen:
            manager.ensure_helper_process()
        popen.assert_not_called()

        service.now = 114.0
        spawned = HelperProcessFake(pid=8888)
        with patch(
            "venus_evcharger.inputs.supervisor_process.os.unlink",
            side_effect=FileNotFoundError,
        ), patch(
            "venus_evcharger.inputs.supervisor_process.subprocess.Popen",
            return_value=spawned,
        ):
            manager.ensure_helper_process()
        self.assertIs(service._auto_input_helper_process, spawned)

    def test_spawn_failure_is_reported_and_empty_snapshot_path_skips_refresh(self) -> None:
        service = AutoInputSupervisorServiceFake(
            auto_input_snapshot_path="",
            now=100.0,
            _auto_input_helper_process=HelperProcessFake(),
            _auto_input_helper_last_start_at=50.0,
        )
        snapshot_runtime = SnapshotRefreshFake()
        lifecycle(service, snapshot_runtime).ensure_helper_process()
        self.assertEqual(snapshot_runtime.calls, [])

        service._auto_input_helper_process = None
        with patch("venus_evcharger.inputs.supervisor_process.subprocess.Popen", side_effect=OSError("boom")):
            lifecycle(service).ensure_helper_process()
        self.assertEqual(service.runtime.warnings[-1][0], "auto-input-helper-start-failed")
        self.assertIsInstance(service.runtime.warnings[-1][4], OSError)


if __name__ == "__main__":
    unittest.main()

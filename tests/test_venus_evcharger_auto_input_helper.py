# SPDX-License-Identifier: GPL-3.0-or-later
"""Process-level contracts for the composed auto-input helper."""

from __future__ import annotations

import runpy
import signal
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from dataclasses import replace
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock, patch

from venus_evcharger.inputs.helper.liveness import HelperLiveness
from venus_evcharger_auto_input_helper import AutoInputHelper, _main_args, _signal_values, main
from tests.support.auto_input_helper import FakeLoop, FakeSnapshots, helper_settings, run_callback


class AutoInputHelperProcessTests(unittest.TestCase):
    def test_helper_is_a_plain_composition_root(self) -> None:
        self.assertEqual(AutoInputHelper.__bases__, (object,))
        self.assertNotIn("__getattr__", AutoInputHelper.__dict__)

    def test_main_builds_runs_and_identifies_helper(self) -> None:
        with patch("venus_evcharger_auto_input_helper.AutoInputHelper") as helper_class:
            self.assertEqual(main(["/config.ini", "/run/input.json", "4", "7", "instance"]), 0)
        helper_class.assert_called_once_with("/config.ini", "/run/input.json", "4", "7", "instance")
        helper_class.return_value.run.assert_called_once_with()

    def test_script_entrypoint_exposes_help_without_starting_helper(self) -> None:
        with (
            patch.object(
                sys,
                "argv",
                ["venus_evcharger_auto_input_helper.py", "--help"],
            ),
            redirect_stdout(StringIO()),
            self.assertRaises(SystemExit) as raised,
        ):
            runpy.run_path(
                "venus_evcharger_auto_input_helper.py",
                run_name="__main__",
            )
        self.assertEqual(raised.exception.code, 0)

    def test_argument_parser_defaults_optional_values(self) -> None:
        args = _main_args(["/config.ini"])
        self.assertEqual(args.config_path, "/config.ini")
        self.assertIsNone(args.snapshot_path)
        self.assertIsNone(args.runtime_instance_id)

    def test_supported_signals_are_concrete(self) -> None:
        self.assertIn(signal.SIGTERM, _signal_values())
        self.assertIn(signal.SIGINT, _signal_values())

    def test_liveness_stops_when_parent_disappears(self) -> None:
        liveness = HelperLiveness(helper_settings(parent_pid=123))
        snapshots = FakeSnapshots()
        loop = FakeLoop()
        liveness.bind(snapshots, loop)
        with patch("venus_evcharger.inputs.helper.liveness.os.getppid", return_value=999):
            self.assertFalse(liveness.parent_watchdog_tick())
        self.assertEqual(loop.quit_calls, 1)

    def test_helper_composes_all_runtime_roles_from_real_config(self) -> None:
        source = Path("deploy/venus/config.venus_evcharger.ini").read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory() as tmp_dir:
            config_path = Path(tmp_dir) / "config.ini"
            config_path.write_text(source, encoding="utf-8")
            helper = AutoInputHelper(str(config_path), str(Path(tmp_dir) / "snapshot.json"), None, 1, "runtime")
        self.assertIs(helper.sources.gateway, helper.gateway)
        self.assertIsNotNone(helper.sources.external)
        self.assertIs(helper.refresh_coordinator.gateway, helper.gateway)
        self.assertIs(helper.refresh_coordinator.snapshots, helper.snapshots)

    def test_signal_handler_requests_component_stop(self) -> None:
        helper = object.__new__(AutoInputHelper)
        helper.liveness = MagicMock()
        helper._handle_signal(signal.SIGTERM, None)
        helper.liveness.request_stop.assert_called_once_with()

    def test_run_orchestrates_components_and_always_stops(self) -> None:
        helper = object.__new__(AutoInputHelper)
        helper.settings = helper_settings()
        helper.liveness = MagicMock()
        helper.snapshots = MagicMock()
        helper.refresh_coordinator = MagicMock()
        helper.sources = MagicMock()
        loop = FakeLoop()
        with patch.object(helper, "_install_signal_handlers"), patch.object(helper, "_install_timers"), patch.object(
            helper, "_schedule_initial_refresh"
        ), patch("venus_evcharger_auto_input_helper.GLIB_RUNTIME.create_main_loop", return_value=loop):
            helper.run()
        helper.liveness.bind.assert_called_once_with(helper.snapshots, loop)
        helper.snapshots.write_lifecycle.assert_called_once_with("starting")
        helper.liveness.start.assert_called_once_with()
        helper.liveness.stop.assert_called_once_with()
        helper.refresh_coordinator.reset.assert_called_once_with()
        helper.sources.close.assert_called_once_with()

    def test_signal_installation_contains_foreign_runtime_errors(self) -> None:
        helper = object.__new__(AutoInputHelper)
        helper.liveness = MagicMock()
        with patch("venus_evcharger_auto_input_helper.signal.signal", side_effect=RuntimeError("not main thread")), patch(
            "venus_evcharger_auto_input_helper.logging.debug"
        ) as debug:
            helper._install_signal_handlers()
        self.assertEqual(debug.call_count, len(_signal_values()))

    def test_timers_are_bound_to_component_methods(self) -> None:
        helper = object.__new__(AutoInputHelper)
        helper.settings = helper_settings()
        helper.snapshots = MagicMock()
        helper.refresh_coordinator = MagicMock()
        helper.liveness = MagicMock()
        with patch("venus_evcharger_auto_input_helper.GLIB_RUNTIME.timeout_add") as timeout_add:
            helper._install_timers()
        self.assertEqual(timeout_add.call_count, 4)
        timeout_add.assert_any_call(
            max(200, int(helper.settings.poll_interval_seconds * 1000)),
            helper.snapshots.poll,
        )

    def test_snapshot_poll_timer_has_a_200_millisecond_lower_bound(self) -> None:
        helper = object.__new__(AutoInputHelper)
        helper.settings = replace(helper_settings(), poll_interval_seconds=0.001)
        helper.snapshots = MagicMock()
        helper.refresh_coordinator = MagicMock()
        helper.liveness = MagicMock()
        with patch("venus_evcharger_auto_input_helper.GLIB_RUNTIME.timeout_add") as timeout_add:
            helper._install_timers()
        timeout_add.assert_any_call(200, helper.snapshots.poll)

    def test_initial_refresh_honours_stop_and_initializes_when_running(self) -> None:
        helper = object.__new__(AutoInputHelper)
        helper.liveness = MagicMock()
        helper.snapshots = MagicMock()
        helper.refresh_coordinator = MagicMock()
        with patch("venus_evcharger_auto_input_helper.GLIB_RUNTIME.idle_add", side_effect=run_callback):
            helper.liveness.stop_requested.return_value = True
            helper._schedule_initial_refresh()
            helper.snapshots.write_lifecycle.assert_not_called()
            helper.liveness.stop_requested.return_value = False
            helper.refresh_coordinator.refresh.return_value = False
            helper._schedule_initial_refresh()
        helper.snapshots.write_lifecycle.assert_called_once_with("initializing")


if __name__ == "__main__":
    unittest.main()

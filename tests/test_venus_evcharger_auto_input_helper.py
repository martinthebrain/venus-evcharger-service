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
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

from venus_evcharger.inputs.helper.liveness import HelperLiveness
from venus_evcharger_auto_input_helper import (
    AutoInputHelper,
    _default_config_path,
    _main_args,
    _signal_values,
    main,
)
from tests.support.auto_input_helper import FakeLoop, FakeSnapshots, helper_settings, run_callback


class AutoInputHelperProcessTests(unittest.TestCase):
    def test_helper_is_a_plain_composition_root(self) -> None:
        self.assertEqual(AutoInputHelper.__bases__, (object,))
        self.assertNotIn("__getattr__", AutoInputHelper.__dict__)

    def test_main_builds_runs_and_identifies_helper(self) -> None:
        with (
            patch("venus_evcharger_auto_input_helper.AutoInputHelper") as helper_class,
            patch("venus_evcharger_auto_input_helper.logging.basicConfig") as basic_config,
        ):
            self.assertEqual(main(["/config.ini", "/run/input.json", "4", "7", "instance"]), 0)
        helper_class.assert_called_once_with("/config.ini", "/run/input.json", "4", "7", "instance")
        helper_class.return_value.run.assert_called_once_with()
        helper_class.return_value.run_once.assert_not_called()
        basic_config.assert_called_once_with(
            format="%(levelname)s [pid=%(process)d %(threadName)s] %(message)s",
            level=20,
        )

    def test_script_entrypoint_exposes_help_without_starting_helper(self) -> None:
        output = StringIO()
        with (
            patch.object(
                sys,
                "argv",
                ["venus_evcharger_auto_input_helper.py", "--help"],
            ),
            redirect_stdout(output),
            self.assertRaises(SystemExit) as raised,
        ):
            runpy.run_path(
                "venus_evcharger_auto_input_helper.py",
                run_name="__main__",
            )
        self.assertEqual(raised.exception.code, 0)
        self.assertIn(
            "Run the Venus EV charger Auto input helper.",
            output.getvalue().splitlines(),
        )

    def test_argument_parser_defaults_optional_values(self) -> None:
        args = _main_args(["/config.ini"])
        self.assertEqual(args.config_path, "/config.ini")
        self.assertIsNone(args.snapshot_path)
        self.assertIsNone(args.parent_pid)
        self.assertIsNone(args.helper_generation)
        self.assertIsNone(args.runtime_instance_id)
        self.assertFalse(args.once)

    def test_once_mode_uses_the_threadless_single_collection(self) -> None:
        with patch("venus_evcharger_auto_input_helper.AutoInputHelper") as helper_class:
            self.assertEqual(main(["--once", "/config.ini"]), 0)
        helper_class.return_value.run_once.assert_called_once_with()
        helper_class.return_value.run.assert_not_called()

    def test_argument_parser_uses_the_deployment_config_by_default(self) -> None:
        args = _main_args([])
        self.assertEqual(args.config_path, _default_config_path())
        self.assertTrue(args.config_path.endswith("/deploy/venus/config.venus_evcharger.ini"))

    def test_argument_parser_exposes_the_exact_process_description(self) -> None:
        output = StringIO()
        with redirect_stdout(output), self.assertRaises(SystemExit) as raised:
            _main_args(["--help"])
        self.assertEqual(raised.exception.code, 0)
        self.assertIn(
            "Run the Venus EV charger Auto input helper.",
            output.getvalue().splitlines(),
        )

    def test_supported_signals_are_concrete(self) -> None:
        expected = tuple(
            signum
            for signum in (
                getattr(signal, "SIGTERM", None),
                getattr(signal, "SIGINT", None),
                getattr(signal, "SIGHUP", None),
            )
            if signum is not None
        )
        self.assertEqual(_signal_values(), expected)

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

    def test_helper_constructor_has_one_exact_component_wiring_contract(self) -> None:
        settings = helper_settings()
        with (
            patch(
                "venus_evcharger_auto_input_helper.load_auto_input_helper_settings",
                return_value=settings,
            ) as load,
            patch("venus_evcharger_auto_input_helper.GatewayEnergySnapshots") as gateway_type,
            patch("venus_evcharger_auto_input_helper.AutoInputSources") as sources_type,
            patch("venus_evcharger_auto_input_helper.HelperLiveness") as liveness_type,
            patch("venus_evcharger_auto_input_helper.AtomicSnapshotWriter") as writer_type,
            patch("venus_evcharger_auto_input_helper.SnapshotStore") as snapshot_type,
            patch("venus_evcharger_auto_input_helper.EnergyRefreshCoordinator") as refresh_type,
        ):
            helper = AutoInputHelper("/config.ini", "/snapshot.json", "4", "7", "runtime")

        load.assert_called_once_with("/config.ini", "/snapshot.json", "4", "7", "runtime")
        gateway_type.assert_called_once_with(settings)
        sources_type.assert_called_once_with(settings, gateway_type.return_value)
        liveness_type.assert_called_once_with(settings)
        writer_type.assert_called_once_with(settings)
        snapshot_type.assert_called_once_with(
            settings,
            sources_type.return_value,
            writer_type.return_value,
            liveness_type.return_value.stop_requested,
        )
        refresh_type.assert_called_once_with(
            gateway_type.return_value,
            snapshot_type.return_value,
            liveness_type.return_value.stop_requested,
        )
        self.assertIs(helper.settings, settings)
        self.assertIs(helper.gateway, gateway_type.return_value)
        self.assertIs(helper.sources, sources_type.return_value)
        self.assertIs(helper.liveness, liveness_type.return_value)
        self.assertIs(helper.snapshots, snapshot_type.return_value)
        self.assertIs(helper.refresh_coordinator, refresh_type.return_value)
        self.assertIsNone(helper._main_loop)

    def test_signal_handler_requests_component_stop(self) -> None:
        helper = object.__new__(AutoInputHelper)
        helper.liveness = MagicMock()
        with patch("venus_evcharger_auto_input_helper.logging.info") as info:
            helper._handle_signal(signal.SIGTERM, object())
        info.assert_called_once_with(
            "Auto input helper received signal %s",
            signal.SIGTERM,
        )
        helper.liveness.request_stop.assert_called_once_with()

    def test_run_orchestrates_components_and_always_stops(self) -> None:
        helper = object.__new__(AutoInputHelper)
        helper.settings = helper_settings(parent_pid=731)
        helper.liveness = MagicMock()
        helper.snapshots = MagicMock()
        helper.refresh_coordinator = MagicMock()
        helper.sources = MagicMock()
        loop = FakeLoop()
        with (
            patch.object(helper, "_install_signal_handlers") as install_signals,
            patch.object(helper, "_install_timers") as install_timers,
            patch.object(helper, "_schedule_initial_refresh") as initial_refresh,
            patch(
                "venus_evcharger_auto_input_helper.GLIB_RUNTIME.create_main_loop",
                return_value=loop,
            ) as create_loop,
            patch(
                "venus_evcharger_auto_input_helper.os.getpid",
                side_effect=(51, 52),
            ),
            patch("venus_evcharger_auto_input_helper.logging.info") as info,
        ):
            helper.run()
        install_signals.assert_called_once_with()
        install_timers.assert_called_once_with()
        initial_refresh.assert_called_once_with()
        create_loop.assert_called_once_with()
        helper.liveness.bind.assert_called_once_with(helper.snapshots, loop)
        helper.snapshots.write_lifecycle.assert_called_once_with("starting")
        helper.liveness.start.assert_called_once_with()
        helper.liveness.stop.assert_called_once_with()
        helper.refresh_coordinator.reset.assert_called_once_with()
        helper.sources.close.assert_called_once_with()
        self.assertIs(helper._main_loop, loop)
        self.assertEqual(loop.run_calls, 1)
        self.assertEqual(
            info.call_args_list,
            [
                call(
                    "Start auto input helper pid=%s parent=%s snapshot=%s",
                    51,
                    helper.settings.parent_pid,
                    helper.settings.snapshot_path,
                ),
                call("Auto input helper stopping pid=%s", 52),
            ],
        )

    def test_run_once_publishes_one_complete_snapshot_without_liveness_threads(self) -> None:
        helper = object.__new__(AutoInputHelper)
        helper.snapshots = MagicMock()
        helper.sources = MagicMock()

        helper.run_once()

        helper.snapshots.write_lifecycle.assert_called_once_with("initializing")
        helper.snapshots.refresh_all.assert_called_once_with()
        helper.sources.close.assert_called_once_with()

    def test_run_once_cleanup_is_unconditional_when_collection_fails(self) -> None:
        helper = object.__new__(AutoInputHelper)
        helper.snapshots = MagicMock()
        helper.snapshots.refresh_all.side_effect = RuntimeError("collection failed")
        helper.sources = MagicMock()

        with self.assertRaisesRegex(RuntimeError, "collection failed"):
            helper.run_once()

        helper.sources.close.assert_called_once_with()

    def test_run_cleanup_is_unconditional_when_the_main_loop_fails(self) -> None:
        helper = object.__new__(AutoInputHelper)
        helper.settings = helper_settings()
        helper.liveness = MagicMock()
        helper.snapshots = MagicMock()
        helper.refresh_coordinator = MagicMock()
        helper.sources = MagicMock()
        loop = MagicMock()
        loop.run.side_effect = RuntimeError("loop failed")
        with (
            patch.object(helper, "_install_signal_handlers"),
            patch.object(helper, "_install_timers"),
            patch.object(helper, "_schedule_initial_refresh"),
            patch(
                "venus_evcharger_auto_input_helper.GLIB_RUNTIME.create_main_loop",
                return_value=loop,
            ),
            self.assertRaisesRegex(RuntimeError, "loop failed"),
        ):
            helper.run()
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

    def test_signal_installation_contains_each_declared_foreign_error_exactly(self) -> None:
        helper = object.__new__(AutoInputHelper)
        for error in (
            OSError("os"),
            RuntimeError("runtime"),
            ValueError("value"),
        ):
            with (
                self.subTest(error=type(error).__name__),
                patch(
                    "venus_evcharger_auto_input_helper.signal.signal",
                    side_effect=error,
                ),
                patch("venus_evcharger_auto_input_helper.logging.debug") as debug,
            ):
                helper._install_signal_handlers()
            self.assertEqual(
                debug.call_args_list,
                [
                    call(
                        "Unable to install auto-input-helper signal handler signal=%s: %s",
                        signum,
                        error,
                    )
                    for signum in _signal_values()
                ],
            )

    def test_signal_installation_binds_every_supported_signal_to_one_handler(self) -> None:
        helper = object.__new__(AutoInputHelper)
        with patch("venus_evcharger_auto_input_helper.signal.signal") as install:
            helper._install_signal_handlers()
        self.assertEqual(
            install.call_args_list,
            [
                call(signum, helper._handle_signal)
                for signum in _signal_values()
            ],
        )

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
        self.assertEqual(
            timeout_add.call_args_list,
            [
                call(int(helper.settings.poll_interval_seconds * 1000), helper.snapshots.poll),
                call(
                    int(helper.settings.validation_poll_seconds * 1000),
                    helper.snapshots.validation_poll,
                ),
                call(
                    int(helper.settings.topology_refresh_seconds * 1000),
                    helper.refresh_coordinator.timer_tick,
                ),
                call(1000, helper.liveness.parent_watchdog_tick),
            ],
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

    def test_slow_timers_have_independent_five_second_lower_bounds(self) -> None:
        helper = object.__new__(AutoInputHelper)
        helper.settings = replace(
            helper_settings(),
            poll_interval_seconds=0.25,
            validation_poll_seconds=0.1,
            topology_refresh_seconds=0.2,
        )
        helper.snapshots = MagicMock()
        helper.refresh_coordinator = MagicMock()
        helper.liveness = MagicMock()
        with patch("venus_evcharger_auto_input_helper.GLIB_RUNTIME.timeout_add") as timeout_add:
            helper._install_timers()
        self.assertEqual(
            timeout_add.call_args_list,
            [
                call(250, helper.snapshots.poll),
                call(5000, helper.snapshots.validation_poll),
                call(5000, helper.refresh_coordinator.timer_tick),
                call(1000, helper.liveness.parent_watchdog_tick),
            ],
        )

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
        helper.refresh_coordinator.refresh.assert_called_once_with()

    def test_initial_refresh_registers_one_callback_with_exact_return_semantics(self) -> None:
        helper = object.__new__(AutoInputHelper)
        helper.liveness = MagicMock()
        helper.snapshots = MagicMock()
        helper.refresh_coordinator = MagicMock(return_value=False)
        helper.refresh_coordinator.refresh.return_value = False
        with patch("venus_evcharger_auto_input_helper.GLIB_RUNTIME.idle_add") as idle_add:
            helper._schedule_initial_refresh()
        idle_add.assert_called_once()
        callback = idle_add.call_args.args[0]
        helper.liveness.stop_requested.return_value = True
        self.assertFalse(callback())
        helper.liveness.stop_requested.return_value = False
        self.assertFalse(callback())
        helper.snapshots.write_lifecycle.assert_called_once_with("initializing")
        helper.refresh_coordinator.refresh.assert_called_once_with()

    def test_main_uses_process_arguments_when_no_explicit_argv_is_supplied(self) -> None:
        with (
            patch.object(
                sys,
                "argv",
                ["helper.py", "/config.ini", "/snapshot.json", "9", "2", "instance"],
            ),
            patch("venus_evcharger_auto_input_helper.AutoInputHelper") as helper_type,
        ):
            self.assertEqual(main(), 0)
        helper_type.assert_called_once_with(
            "/config.ini",
            "/snapshot.json",
            "9",
            "2",
            "instance",
        )

    def test_argument_parser_preserves_all_five_positional_values(self) -> None:
        args = _main_args(["config", "snapshot", "parent", "generation", "instance"])
        self.assertEqual(
            vars(args),
            {
                "once": False,
                "config_path": "config",
                "snapshot_path": "snapshot",
                "parent_pid": "parent",
                "helper_generation": "generation",
                "runtime_instance_id": "instance",
            },
        )

    def test_signal_values_exclude_all_missing_runtime_constants(self) -> None:
        with patch(
            "venus_evcharger_auto_input_helper.signal",
            SimpleNamespace(),
        ):
            self.assertEqual(_signal_values(), ())


if __name__ == "__main__":
    unittest.main()

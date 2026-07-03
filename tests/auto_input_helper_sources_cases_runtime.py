# SPDX-License-Identifier: GPL-3.0-or-later
from tests.auto_input_helper_sources_cases_common import *
from venus_evcharger.dbus_gateway import GRID_POWER_READ_KEY


class _AutoInputHelperSourcesRuntimeCases:
    def test_get_grid_power_returns_total_and_handles_no_paths(self):
        helper = self._make_helper()

        self._write_gateway_cache(helper, key_values={GRID_POWER_READ_KEY: -800.0})
        self.assertEqual(helper._get_grid_power(), -800.0)

        helper = self._make_helper()
        self.assertIsNone(helper._get_grid_power())

    def test_get_grid_power_covers_retry_guard_and_partial_failures(self):
        helper = self._make_helper()
        helper._source_retry_after["grid"] = 200.0
        with patch("venus_evcharger_auto_input_helper.time.time", return_value=100.0):
            self.assertIsNone(helper._get_grid_power())

        helper = self._make_helper()

        self._write_gateway_cache(helper, key_values={GRID_POWER_READ_KEY: 200.0})
        self.assertEqual(helper._get_grid_power(), 200.0)

        helper = self._make_helper()
        helper._delay_source_retry = MagicMock()
        self.assertIsNone(helper._get_grid_power())
        helper._delay_source_retry.assert_called_once_with("grid")

    def test_write_snapshot_deduplicates_identical_payload(self):
        helper = self._make_helper()
        helper.snapshot_path = "/tmp/auto.json"
        with patch("venus_evcharger_auto_input_helper.write_text_atomically") as write_mock:
            helper._write_snapshot({"captured_at": 100.0})
            helper._write_snapshot({"captured_at": 100.0})

        write_mock.assert_called_once()

    def test_run_sets_up_mainloop_subscriptions_and_timers(self):
        helper = self._make_helper()
        helper.snapshot_path = "/tmp/auto.json"
        helper.parent_pid = 1234
        helper.validation_poll_seconds = 30.0
        helper.subscription_refresh_seconds = 60.0
        helper._refresh_subscriptions = MagicMock()
        helper._start_liveness_threads = MagicMock()
        helper._stop_liveness_threads = MagicMock()
        mainloop = MagicMock()

        with patch("venus_evcharger_auto_input_helper.GLib.MainLoop", return_value=mainloop):
            with patch("venus_evcharger_auto_input_helper.GLib.timeout_add") as timeout_add:
                with patch("venus_evcharger_auto_input_helper.GLib.idle_add", side_effect=lambda callback: callback()):
                    with patch("venus_evcharger_auto_input_helper.signal.signal") as signal_mock:
                        helper.run()

        self.assertTrue(signal_mock.called)
        helper._refresh_subscriptions.assert_called_once_with()
        self.assertEqual(timeout_add.call_count, 3)
        mainloop.run.assert_called_once_with()

    def test_run_ignores_signal_registration_failures_and_missing_signals(self):
        helper = self._make_helper()
        helper.snapshot_path = "/tmp/auto.json"
        helper.parent_pid = 1234
        helper.validation_poll_seconds = 30.0
        helper.subscription_refresh_seconds = 60.0
        helper._refresh_subscriptions = MagicMock()
        helper._start_liveness_threads = MagicMock()
        helper._stop_liveness_threads = MagicMock()
        mainloop = MagicMock()

        fake_signal = MagicMock(SIGTERM=15, SIGINT=None, SIGHUP=1)
        fake_signal.signal.side_effect = [RuntimeError("no handler"), RuntimeError("no handler")]
        with patch("venus_evcharger_auto_input_helper.GLib.MainLoop", return_value=mainloop):
            with patch("venus_evcharger_auto_input_helper.GLib.timeout_add"):
                with patch("venus_evcharger_auto_input_helper.GLib.idle_add", side_effect=lambda callback: callback()):
                    with patch.object(venus_evcharger_auto_input_helper, "signal", fake_signal):
                        helper.run()

        helper._refresh_subscriptions.assert_called_once_with()

    def test_run_cleans_up_dbus_connection_after_mainloop_exits(self):
        helper = self._make_helper()
        helper.snapshot_path = "/tmp/auto.json"
        helper.parent_pid = 1234
        helper.validation_poll_seconds = 30.0
        helper.subscription_refresh_seconds = 60.0
        helper._refresh_subscriptions = MagicMock()
        helper._reset_system_bus = MagicMock()
        helper._start_liveness_threads = MagicMock()
        helper._stop_liveness_threads = MagicMock()
        mainloop = MagicMock()

        with patch("venus_evcharger_auto_input_helper.GLib.MainLoop", return_value=mainloop):
            with patch("venus_evcharger_auto_input_helper.GLib.timeout_add"):
                with patch("venus_evcharger_auto_input_helper.GLib.idle_add", side_effect=lambda callback: callback()):
                    with patch("venus_evcharger_auto_input_helper.signal.signal"):
                        helper.run()

        mainloop.run.assert_called_once_with()
        helper._reset_system_bus.assert_called_once_with()

    def test_run_uses_gateway_mainloop_and_main_invokes_helper(self):
        helper = self._make_helper()
        helper.snapshot_path = "/tmp/auto.json"
        helper.parent_pid = 1234
        helper.validation_poll_seconds = 30.0
        helper.subscription_refresh_seconds = 60.0
        helper._refresh_subscriptions = MagicMock()
        helper._start_liveness_threads = MagicMock()
        helper._stop_liveness_threads = MagicMock()
        with patch("venus_evcharger_auto_input_helper.GLib.MainLoop") as mainloop_cls:
            with patch("venus_evcharger_auto_input_helper.GLib.timeout_add"):
                with patch("venus_evcharger_auto_input_helper.GLib.idle_add", side_effect=lambda callback: callback()):
                    with patch("venus_evcharger_auto_input_helper.signal.signal"):
                        helper.run()
        mainloop_cls.return_value.run.assert_called_once_with()

        fake_helper = MagicMock()
        with patch("venus_evcharger_auto_input_helper.AutoInputHelper", return_value=fake_helper) as helper_cls:
            self.assertEqual(
                venus_evcharger_auto_input_helper.main(["/tmp/config.ini", "/tmp/snapshot.json", "1234"]),
                0,
            )
        helper_cls.assert_called_once_with("/tmp/config.ini", "/tmp/snapshot.json", "1234", None)
        fake_helper.run.assert_called_once_with()

    def test_main_uses_default_config_path_when_argv_missing(self):
        fake_helper = MagicMock()
        with patch("venus_evcharger_auto_input_helper.AutoInputHelper", return_value=fake_helper) as helper_cls:
            with patch("venus_evcharger_auto_input_helper.os.path.abspath", return_value="/repo/venus_evcharger_auto_input_helper.py"):
                self.assertEqual(venus_evcharger_auto_input_helper.main([]), 0)

        helper_cls.assert_called_once_with("/repo/deploy/venus/config.venus_evcharger.ini", None, None, None)

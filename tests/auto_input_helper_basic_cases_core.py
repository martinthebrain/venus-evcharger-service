# SPDX-License-Identifier: GPL-3.0-or-later
from tests.auto_input_helper_basic_cases_common import (
    AutoInputHelper,
    ModuleType,
    _as_bool,
    os,
    patch,
    runpy,
    sys,
    tempfile,
    venus_evcharger_auto_input_helper,
)


class _AutoInputHelperBasicCoreCases:
    def test_as_bool_parses_truthy_and_defaults(self):
        self.assertTrue(_as_bool("yes"))
        self.assertFalse(_as_bool("0"))
        self.assertTrue(_as_bool(None, default=True))

    def test_init_raises_for_missing_or_invalid_config(self):
        temp_dir = tempfile.mkdtemp()
        self.addCleanup(lambda: os.path.isdir(temp_dir) and os.rmdir(temp_dir))
        config_path = os.path.join(temp_dir, "missing.ini")
        with self.assertRaises(ValueError):
            AutoInputHelper(config_path)

    def test_init_uses_dedicated_auto_input_poll_interval_when_configured(self):
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as handle:
            handle.write(
                "[DEFAULT]\n"
                "PollIntervalMs=1000\n"
                "AutoInputPollIntervalMs=2000\n"
                "AutoPvPollIntervalMs=2000\n"
                "AutoGridPollIntervalMs=2000\n"
                "AutoBatteryPollIntervalMs=10000\n"
                "AutoInputSnapshotPath=/tmp/helper.json\n"
            )
            config_path = handle.name

        self.addCleanup(lambda: os.path.exists(config_path) and os.unlink(config_path))
        helper = AutoInputHelper(config_path)
        self.assertEqual(helper.poll_interval_seconds, 2.0)
        self.assertEqual(helper.auto_pv_poll_interval_seconds, 2.0)
        self.assertEqual(helper.auto_grid_poll_interval_seconds, 2.0)
        self.assertEqual(helper.auto_battery_poll_interval_seconds, 10.0)

    def test_derive_subscription_refresh_seconds_uses_smallest_positive_scan_interval(self):
        helper = AutoInputHelper.__new__(AutoInputHelper)
        helper.config = {"AutoPvScanIntervalSeconds": "45", "AutoBatteryScanIntervalSeconds": "15"}
        self.assertEqual(helper._derive_subscription_refresh_seconds(), 15.0)

    def test_derive_subscription_refresh_seconds_ignores_non_positive_candidates(self):
        helper = AutoInputHelper.__new__(AutoInputHelper)
        helper.config = {"AutoPvScanIntervalSeconds": "0", "AutoBatteryScanIntervalSeconds": "-5"}
        self.assertEqual(helper._derive_subscription_refresh_seconds(), 60.0)

    def test_handle_signal_sets_stop_flag_and_requests_idle_quit(self):
        helper = self._make_helper()
        helper._main_loop = __import__("unittest.mock").mock.MagicMock()

        with patch("venus_evcharger_auto_input_helper.GLib.idle_add") as idle_add:
            helper._handle_signal(15, None)

        self.assertTrue(helper._stop_requested)
        idle_add.assert_called_once_with(helper._main_loop.quit)

    def test_handle_signal_sets_stop_flag_without_idle_quit_when_main_loop_is_missing(self):
        helper = self._make_helper()
        helper._main_loop = None

        with patch("venus_evcharger_auto_input_helper.GLib.idle_add") as idle_add:
            helper._handle_signal(15, None)

        self.assertTrue(helper._stop_requested)
        idle_add.assert_not_called()

    def test_warning_throttled_logs_once_per_interval(self):
        helper = self._make_helper()

        with patch("venus_evcharger_auto_input_helper.time.time", side_effect=[100.0, 105.0, 131.0]):
            with patch("venus_evcharger_auto_input_helper.logging.warning") as warning_mock:
                helper._warning_throttled("dbus", 30.0, "failed %s", "x")
                helper._warning_throttled("dbus", 30.0, "failed %s", "x")
                helper._warning_throttled("dbus", 30.0, "failed %s", "x")

        self.assertEqual(warning_mock.call_count, 2)

    def test_parent_alive_uses_parent_pid_and_handles_errors(self):
        helper = self._make_helper()
        helper.parent_pid = 1234

        helper.parent_pid = None
        self.assertTrue(helper._parent_alive())
        helper.parent_pid = 1234

        with __import__("unittest.mock").mock.patch("venus_evcharger_auto_input_helper.os.getppid", return_value=1234):
            self.assertTrue(helper._parent_alive())
        with __import__("unittest.mock").mock.patch("venus_evcharger_auto_input_helper.os.getppid", return_value=9999):
            self.assertFalse(helper._parent_alive())
        with __import__("unittest.mock").mock.patch("venus_evcharger_auto_input_helper.os.getppid", side_effect=RuntimeError("boom")):
            self.assertFalse(helper._parent_alive())

    def test_helper_module_import_fallback_sets_dbus_glib_mainloop_to_none(self):
        helper_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "venus_evcharger_auto_input_helper.py")
        fake_dbus = ModuleType("dbus")
        fake_dbus_mainloop = ModuleType("dbus.mainloop")
        fake_dbus_glib_mainloop = ModuleType("dbus.mainloop.glib")
        fake_glib = __import__("unittest.mock").mock.MagicMock()
        fake_gi = ModuleType("gi")
        fake_repository = ModuleType("gi.repository")
        fake_repository.GLib = fake_glib
        fake_gi.repository = fake_repository

        with patch.dict(
            sys.modules,
            {
                "dbus": fake_dbus,
                "dbus.mainloop": fake_dbus_mainloop,
                "dbus.mainloop.glib": fake_dbus_glib_mainloop,
                "gi": fake_gi,
                "gi.repository": fake_repository,
                "gi.repository.GLib": fake_glib,
            },
            clear=False,
        ):
            module_globals = runpy.run_path(helper_path, run_name="venus_evcharger_auto_input_helper_import_test")

        self.assertIsNone(module_globals["dbus_glib_mainloop"])

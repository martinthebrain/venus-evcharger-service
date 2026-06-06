# SPDX-License-Identifier: GPL-3.0-or-later
from tests.venus_evcharger_helpers_support import *


class TestShellyWallboxHelpersSecondary(ShellyWallboxHelpersTestBase):
    def test_get_dbus_value_retries_once_after_error(self):
        service = ShellyWallboxService.__new__(ShellyWallboxService)
        service.dbus_method_timeout_seconds = 1.25
        first_interface = MagicMock()
        second_interface = MagicMock()
        first_interface.GetValue.side_effect = RuntimeError("temporary dbus error")
        second_interface.GetValue.return_value = 42.0
        first_bus = MagicMock()
        second_bus = MagicMock()
        first_bus.get_object.return_value = object()
        second_bus.get_object.return_value = object()
        service._get_system_bus = MagicMock(side_effect=[first_bus, second_bus])
        service._reset_system_bus = MagicMock()

        original_interface = venus_evcharger_service.dbus.Interface
        venus_evcharger_service.dbus.Interface = MagicMock(side_effect=[first_interface, second_interface])
        try:
            self.assertEqual(service._get_dbus_value("com.victronenergy.system", "/Dc/Pv/Power"), 42.0)
        finally:
            venus_evcharger_service.dbus.Interface = original_interface

        self.assertEqual(service._get_system_bus.call_count, 2)
        second_interface.GetValue.assert_called_once_with(timeout=1.25)
        service._reset_system_bus.assert_called()

    def test_system_bus_reset_recreates_cached_connection(self):
        service = ShellyWallboxService.__new__(ShellyWallboxService)
        first_bus = object()
        second_bus = object()

        original_system_bus = runtime_support_module.dbus.SystemBus
        original_session_bus = runtime_support_module.dbus.SessionBus
        runtime_support_module.dbus.SystemBus = MagicMock(side_effect=[first_bus, second_bus])
        runtime_support_module.dbus.SessionBus = MagicMock(
            side_effect=AssertionError("session bus not expected")
        )
        try:
            with unittest.mock.patch.dict(venus_evcharger_service.os.environ, {}, clear=True):
                self.assertIs(service._get_system_bus(), first_bus)
                self.assertIs(service._get_system_bus(), first_bus)
                service._reset_system_bus()
                self.assertIs(service._get_system_bus(), second_bus)
        finally:
            runtime_support_module.dbus.SystemBus = original_system_bus
            runtime_support_module.dbus.SessionBus = original_session_bus

    def test_request_uses_configured_shelly_timeout(self):
        service = ShellyWallboxService.__new__(ShellyWallboxService)
        service.shelly_request_timeout_seconds = 1.5
        service.use_digest_auth = False
        service.username = ""
        service.password = ""
        response = MagicMock()
        response.json.return_value = {"ok": True}
        service.session = MagicMock()
        service.session.get.return_value = response

        self.assertEqual(service._request("http://example.invalid"), {"ok": True})
        service.session.get.assert_called_once_with(url="http://example.invalid", timeout=1.5)

    def test_runtime_state_can_be_loaded_from_ram_file(self):
        service = ShellyWallboxService.__new__(ShellyWallboxService)
        service.virtual_mode = 0
        service.virtual_autostart = 1
        service.virtual_enable = 1
        service.virtual_startstop = 1
        service.manual_override_until = 0.0
        service._auto_mode_cutover_pending = False
        service._ignore_min_offtime_once = False
        service.relay_last_changed_at = None
        service.relay_last_off_at = None

        with tempfile.TemporaryDirectory() as temp_dir:
            service.runtime_state_path = f"{temp_dir}/state.json"
            with open(service.runtime_state_path, "w", encoding="utf-8") as handle:
                handle.write(
                    '{"autostart":0,"auto_mode_cutover_pending":1,"enable":1,'
                    '"ignore_min_offtime_once":1,"manual_override_until":123.5,'
                    '"mode":1,"relay_last_changed_at":111.0,"relay_last_off_at":112.0,"startstop":0}'
                )

            service._load_runtime_state()

        self.assertEqual(service.virtual_mode, 1)
        self.assertEqual(service.virtual_autostart, 0)
        self.assertEqual(service.virtual_enable, 1)
        self.assertEqual(service.virtual_startstop, 0)
        self.assertEqual(service.manual_override_until, 123.5)
        self.assertTrue(service._auto_mode_cutover_pending)
        self.assertFalse(service._ignore_min_offtime_once)
        self.assertEqual(service.relay_last_changed_at, 111.0)
        self.assertEqual(service.relay_last_off_at, 112.0)

    def test_runtime_state_is_written_atomically_to_ram_file(self):
        service = ShellyWallboxService.__new__(ShellyWallboxService)
        service.virtual_mode = 1
        service.virtual_autostart = 1
        service.virtual_enable = 1
        service.virtual_startstop = 0
        service.manual_override_until = 0.0
        service._auto_mode_cutover_pending = True
        service._ignore_min_offtime_once = False
        service.relay_last_changed_at = 100.0
        service.relay_last_off_at = 101.0
        service._runtime_state_serialized = None

        with tempfile.TemporaryDirectory() as temp_dir:
            service.runtime_state_path = f"{temp_dir}/state.json"
            service._save_runtime_state()

            with open(service.runtime_state_path, "r", encoding="utf-8") as handle:
                saved = handle.read()

        self.assertIn('"mode":1', saved)
        self.assertIn('"enable":1', saved)
        self.assertIn('"auto_mode_cutover_pending":1', saved)
        self.assertNotIn('"ignore_min_offtime_once"', saved)

    def test_io_worker_once_collects_snapshot_in_ram(self):
        service = ShellyWallboxService.__new__(ShellyWallboxService)
        service.poll_interval_ms = 1000
        service.virtual_mode = 1
        service.auto_shelly_soft_fail_seconds = 10
        service.auto_dbus_backoff_base_seconds = 5
        service._warning_state = {}
        service._error_state = {
            "dbus": 0,
            "shelly": 0,
            "pv": 0,
            "battery": 0,
            "grid": 0,
            "cache_hits": 0,
        }
        service._failure_active = {
            "dbus": False,
            "shelly": False,
            "pv": False,
            "battery": False,
            "grid": False,
        }
        service._source_retry_after = {}
        service._worker_fetch_pm_status = MagicMock(return_value={"output": True})

        with unittest.mock.patch("venus_evcharger_service.time.time", return_value=100.0):
            service._io_worker_once()

        snapshot = service._get_worker_snapshot()
        self.assertEqual(snapshot["captured_at"], 100.0)
        self.assertEqual(snapshot["pm_captured_at"], 100.0)
        self.assertEqual(snapshot["pm_status"], {"output": True})
        self.assertTrue(snapshot["auto_mode_active"])

    def test_io_auto_worker_once_collects_auto_inputs_in_ram(self):
        service = ShellyWallboxService.__new__(ShellyWallboxService)
        service.poll_interval_ms = 1000
        service.virtual_mode = 1
        service.auto_shelly_soft_fail_seconds = 10
        service.auto_dbus_backoff_base_seconds = 5
        service._warning_state = {}
        service._error_state = {
            "dbus": 0,
            "shelly": 0,
            "pv": 0,
            "battery": 0,
            "grid": 0,
            "cache_hits": 0,
        }
        service._failure_active = {
            "dbus": False,
            "shelly": False,
            "pv": False,
            "battery": False,
            "grid": False,
        }
        service._source_retry_after = {}
        service.auto_input_snapshot_path = "/tmp/auto-helper.json"
        service.auto_input_helper_stale_seconds = 15
        service.auto_input_helper_restart_seconds = 5
        helper_snapshot = {
            "snapshot_version": 1,
            "captured_at": 100.0,
            "heartbeat_at": 100.0,
            "pv_captured_at": 100.0,
            "pv_power": 2300.0,
            "battery_captured_at": 100.0,
            "battery_soc": 57.0,
            "grid_captured_at": 100.0,
            "grid_power": -2100.0,
            "writer_pid": 4321,
            "helper_generation": 0,
        }
        stat_result = MagicMock()
        stat_result.st_mtime_ns = 1

        with unittest.mock.patch("venus_evcharger_service.time.time", return_value=100.0):
            with unittest.mock.patch("venus_evcharger_service.os.stat", return_value=stat_result):
                with unittest.mock.patch("venus_evcharger_service.open", unittest.mock.mock_open(read_data=json.dumps(helper_snapshot))):
                    service._refresh_auto_input_snapshot()

        snapshot = service._get_worker_snapshot()
        self.assertEqual(snapshot["pv_power"], 2300.0)
        self.assertEqual(snapshot["battery_soc"], 57.0)
        self.assertEqual(snapshot["grid_power"], -2100.0)
        self.assertEqual(snapshot["pv_captured_at"], 100.0)
        self.assertEqual(snapshot["battery_captured_at"], 100.0)
        self.assertEqual(snapshot["grid_captured_at"], 100.0)
        self.assertTrue(snapshot["auto_mode_active"])

    def test_refresh_auto_input_snapshot_uses_heartbeat_for_helper_staleness(self):
        service = ShellyWallboxService.__new__(ShellyWallboxService)
        service.poll_interval_ms = 1000
        service.virtual_mode = 1
        service.auto_shelly_soft_fail_seconds = 10
        service.auto_dbus_backoff_base_seconds = 5
        service._warning_state = {}
        service._error_state = {
            "dbus": 0,
            "shelly": 0,
            "pv": 0,
            "battery": 0,
            "grid": 0,
            "cache_hits": 0,
        }
        service._failure_active = {
            "dbus": False,
            "shelly": False,
            "pv": False,
            "battery": False,
            "grid": False,
        }
        service._source_retry_after = {}
        service.auto_input_snapshot_path = "/tmp/auto-helper.json"
        service.auto_input_helper_stale_seconds = 15
        service.auto_input_helper_restart_seconds = 5
        helper_snapshot = {
            "snapshot_version": 1,
            "captured_at": 100.0,
            "heartbeat_at": 130.0,
            "pv_captured_at": 100.0,
            "pv_power": 2300.0,
            "battery_captured_at": 100.0,
            "battery_soc": 57.0,
            "grid_captured_at": 100.0,
            "grid_power": -2100.0,
            "writer_pid": 4321,
            "helper_generation": 0,
        }
        stat_result = MagicMock()
        stat_result.st_mtime_ns = 3

        with unittest.mock.patch("venus_evcharger_service.time.time", return_value=130.0):
            with unittest.mock.patch("venus_evcharger_service.os.stat", return_value=stat_result):
                with unittest.mock.patch("venus_evcharger_service.open", unittest.mock.mock_open(read_data=json.dumps(helper_snapshot))):
                    service._refresh_auto_input_snapshot()

        snapshot = service._get_worker_snapshot()
        self.assertEqual(snapshot["pv_power"], 2300.0)
        self.assertEqual(snapshot["pv_captured_at"], 100.0)
        self.assertEqual(service._auto_input_snapshot_last_seen, 130.0)

    def test_io_auto_worker_does_not_delay_published_pm_status(self):
        service = ShellyWallboxService.__new__(ShellyWallboxService)
        service.poll_interval_ms = 1000
        service.virtual_mode = 1
        service.auto_shelly_soft_fail_seconds = 10
        service.auto_dbus_backoff_base_seconds = 5
        service._warning_state = {}
        service._error_state = {
            "dbus": 0,
            "shelly": 0,
            "pv": 0,
            "battery": 0,
            "grid": 0,
            "cache_hits": 0,
        }
        service._failure_active = {
            "dbus": False,
            "shelly": False,
            "pv": False,
            "battery": False,
            "grid": False,
        }
        service._source_retry_after = {}
        self._set_worker_snapshot(
            service,
            captured_at=99.0,
            pm_captured_at=99.0,
            pm_status={"output": True, "apower": 1800.0},
        )

        service.auto_input_snapshot_path = "/tmp/auto-helper.json"
        service.auto_input_helper_stale_seconds = 15
        service.auto_input_helper_restart_seconds = 5
        helper_snapshot = {
            "snapshot_version": 1,
            "captured_at": 100.0,
            "heartbeat_at": 100.0,
            "pv_captured_at": 100.0,
            "pv_power": 2300.0,
            "battery_captured_at": 100.0,
            "battery_soc": 57.0,
            "grid_captured_at": 100.0,
            "grid_power": -2100.0,
            "writer_pid": 4321,
            "helper_generation": 0,
        }
        stat_result = MagicMock()
        stat_result.st_mtime_ns = 2

        with unittest.mock.patch("venus_evcharger_service.time.time", return_value=100.0):
            with unittest.mock.patch("venus_evcharger_service.os.stat", return_value=stat_result):
                with unittest.mock.patch("venus_evcharger_service.open", unittest.mock.mock_open(read_data=json.dumps(helper_snapshot))):
                    service._refresh_auto_input_snapshot()

        snapshot = service._get_worker_snapshot()
        self.assertEqual(snapshot["pm_status"], {"output": True, "apower": 1800.0})
        self.assertEqual(snapshot["pv_power"], 2300.0)
        self.assertEqual(snapshot["pm_captured_at"], 99.0)

    def test_start_io_worker_restarts_helper_when_process_missing(self):
        service = ShellyWallboxService.__new__(ShellyWallboxService)
        service._ensure_worker_state = MagicMock()
        alive_worker = MagicMock()
        alive_worker.is_alive.return_value = True
        service._worker_thread = alive_worker
        service._auto_input_helper_process = None
        service._auto_input_helper_last_start_at = 0.0
        service._auto_input_helper_restart_requested_at = None
        service.auto_input_helper_restart_seconds = 5
        service._spawn_auto_input_helper = MagicMock()

        with unittest.mock.patch("venus_evcharger_service.time.time", return_value=100.0):
            service._start_io_worker()

        service._spawn_auto_input_helper.assert_called_once()

    def test_ensure_auto_input_helper_process_restarts_stale_helper(self):
        service = ShellyWallboxService.__new__(ShellyWallboxService)
        service._ensure_worker_state = MagicMock()
        process = MagicMock()
        process.poll.return_value = None
        process.pid = 4321
        service._auto_input_helper_process = process
        service._auto_input_helper_last_start_at = 10.0
        service._auto_input_helper_restart_requested_at = None
        service._auto_input_snapshot_last_seen = 70.0
        service.auto_input_helper_stale_seconds = 15
        service.auto_input_helper_restart_seconds = 5
        service._stop_auto_input_helper = MagicMock()

        service._ensure_auto_input_helper_process(100.0)

        service._stop_auto_input_helper.assert_called_once_with(force=False)
        self.assertEqual(service._auto_input_helper_restart_requested_at, 100.0)

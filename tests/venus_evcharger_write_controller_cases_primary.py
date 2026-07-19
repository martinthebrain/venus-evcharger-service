# SPDX-License-Identifier: GPL-3.0-or-later
from tests.venus_evcharger_write_controller_support import *


class TestDbusWriteControllerPrimary(DbusWriteControllerTestBase):
    def test_snapshot_dbus_paths_returns_empty_mapping_without_dbusservice(self) -> None:
        self.assertEqual(_snapshot_dbus_paths(SimpleNamespace(), ("/Mode",)), {})

    def test_snapshot_dbus_paths_uses_publish_state_without_touching_dbusservice(self) -> None:
        class FailingDbusService(dict[str, object]):
            def __getitem__(self, key: str) -> object:
                raise RuntimeError("must not touch dbus")

        service = SimpleNamespace(
            _dbusservice=FailingDbusService({"/Mode": 99}),
            _dbus_publish_state={"/Mode": {"value": 1, "updated_at": 100.0}},
        )

        self.assertEqual(_snapshot_dbus_paths(service, ("/Mode",)), {"/Mode": 1})

    def test_handle_mode_write_manual_to_auto_queues_clean_cutover(self) -> None:
        service = SimpleNamespace(
            virtual_mode=0,
            virtual_autostart=1,
            virtual_startstop=1,
            virtual_enable=0,
            auto_start_condition_since=100.0,
            auto_stop_condition_since=200.0,
            auto_samples=deque([(205.0, 2200.0, -2200.0)]),
            manual_override_until=500.0,
            _auto_mode_cutover_pending=False,
            _ignore_min_offtime_once=False,
            _dbusservice={"/Mode": 0, "/StartStop": 1, "/Enable": 0},
            time_now=MagicMock(return_value=200.0),
            _normalize_mode=self._normalize_mode,
            _mode_uses_auto_logic=self._mode_uses_auto_logic,
            _clear_auto_samples=MagicMock(),
            _queue_relay_command=MagicMock(),
            _publish_local_pm_status=MagicMock(),
            _peek_pending_relay_command=MagicMock(return_value=(None, None)),
            _last_pm_status={"output": True},
            _get_worker_snapshot=MagicMock(return_value={"pv_power": 10, "battery_soc": 50, "grid_power": -10}),
            _update_worker_snapshot=MagicMock(),
            _publish_dbus_field=MagicMock(),
            _state_summary=self._state_summary,
            _save_runtime_state=MagicMock(),
        )
        service._clear_auto_samples = partial(self._clear_auto_samples, service)
        service._publish_dbus_field.side_effect = self._publish_field_side_effect(service)

        controller = write_controller(service)
        result = controller.handle_write("/Mode", 1)

        self.assertTrue(result)
        self.assertEqual(service.virtual_mode, 1)
        self.assertEqual(service.virtual_enable, 1)
        self.assertEqual(service.virtual_startstop, 0)
        self.assertEqual(service.manual_override_until, 0.0)
        self.assertTrue(service._auto_mode_cutover_pending)
        service._queue_relay_command.assert_called_once_with(False, 200.0)
        service._publish_local_pm_status.assert_called_once_with(False, 200.0)
        service._update_worker_snapshot.assert_called_once()
        self.assertEqual(service._dbusservice["/Mode"], 1)
        self.assertEqual(service._dbusservice["/StartStop"], 1)
        self.assertEqual(service._dbusservice["/Enable"], 1)
        service._save_runtime_state.assert_called_once()

    def test_handle_mode_write_manual_to_auto_uses_best_known_relay_state_for_cutover(self) -> None:
        service = SimpleNamespace(
            virtual_mode=0,
            virtual_autostart=1,
            virtual_startstop=0,
            virtual_enable=0,
            auto_start_condition_since=None,
            auto_stop_condition_since=None,
            auto_samples=deque(),
            manual_override_until=10.0,
            _auto_mode_cutover_pending=False,
            _ignore_min_offtime_once=False,
            _dbusservice={"/Mode": 0, "/StartStop": 0, "/Enable": 0},
            time_now=MagicMock(return_value=200.0),
            _normalize_mode=self._normalize_mode,
            _mode_uses_auto_logic=self._mode_uses_auto_logic,
            _clear_auto_samples=MagicMock(),
            _queue_relay_command=MagicMock(),
            _publish_local_pm_status=MagicMock(),
            _peek_pending_relay_command=MagicMock(return_value=(None, None)),
            _last_pm_status={"output": True},
            _last_pm_status_confirmed=True,
            _get_worker_snapshot=MagicMock(return_value={"pm_status": {"output": True}, "pm_confirmed": True}),
            _update_worker_snapshot=MagicMock(),
            _publish_dbus_field=MagicMock(),
            _state_summary=self._state_summary,
            _save_runtime_state=MagicMock(),
        )
        service._clear_auto_samples = partial(self._clear_auto_samples, service)
        service._publish_dbus_field.side_effect = self._publish_field_side_effect(service)

        controller = write_controller(service)

        self.assertTrue(controller.handle_write("/Mode", 1))

        self.assertTrue(service._auto_mode_cutover_pending)
        service._queue_relay_command.assert_called_once_with(False, 200.0)
        service._publish_local_pm_status.assert_called_once_with(False, 200.0)

    def test_handle_mode_write_manual_to_auto_honors_pending_relay_command_for_cutover(self) -> None:
        service = SimpleNamespace(
            virtual_mode=0,
            virtual_autostart=1,
            virtual_startstop=0,
            virtual_enable=0,
            auto_start_condition_since=None,
            auto_stop_condition_since=None,
            auto_samples=deque(),
            manual_override_until=10.0,
            _auto_mode_cutover_pending=False,
            _ignore_min_offtime_once=False,
            _dbusservice={"/Mode": 0, "/StartStop": 0, "/Enable": 0},
            time_now=MagicMock(return_value=200.0),
            _normalize_mode=self._normalize_mode,
            _mode_uses_auto_logic=self._mode_uses_auto_logic,
            _clear_auto_samples=MagicMock(),
            _queue_relay_command=MagicMock(),
            _publish_local_pm_status=MagicMock(),
            _peek_pending_relay_command=MagicMock(return_value=(True, 150.0)),
            _last_pm_status={"output": False},
            _get_worker_snapshot=MagicMock(return_value={"pm_status": {"output": False}}),
            _update_worker_snapshot=MagicMock(),
            _publish_dbus_field=MagicMock(),
            _state_summary=self._state_summary,
            _save_runtime_state=MagicMock(),
        )
        service._clear_auto_samples = partial(self._clear_auto_samples, service)
        service._publish_dbus_field.side_effect = self._publish_field_side_effect(service)

        controller = write_controller(service)

        self.assertTrue(controller.handle_write("/Mode", 1))

        self.assertTrue(service._auto_mode_cutover_pending)
        service._queue_relay_command.assert_called_once_with(False, 200.0)
        service._publish_local_pm_status.assert_called_once_with(False, 200.0)

    def test_handle_mode_write_manual_to_auto_queues_cutover_when_relay_state_is_unknown(self) -> None:
        service = SimpleNamespace(
            virtual_mode=0,
            virtual_autostart=1,
            virtual_startstop=0,
            virtual_enable=0,
            auto_start_condition_since=None,
            auto_stop_condition_since=None,
            auto_samples=deque(),
            manual_override_until=10.0,
            _auto_mode_cutover_pending=False,
            _ignore_min_offtime_once=False,
            _dbusservice={"/Mode": 0, "/StartStop": 0, "/Enable": 0},
            time_now=MagicMock(return_value=200.0),
            _normalize_mode=self._normalize_mode,
            _mode_uses_auto_logic=self._mode_uses_auto_logic,
            _clear_auto_samples=MagicMock(),
            _queue_relay_command=MagicMock(),
            _publish_local_pm_status=MagicMock(),
            _peek_pending_relay_command=MagicMock(return_value=(None, None)),
            _last_pm_status=None,
            _last_pm_status_confirmed=False,
            _last_pm_status_at=None,
            _get_worker_snapshot=MagicMock(return_value={"pm_status": None, "pm_confirmed": False}),
            _update_worker_snapshot=MagicMock(),
            _publish_dbus_field=MagicMock(),
            _state_summary=self._state_summary,
            _save_runtime_state=MagicMock(),
        )
        service._clear_auto_samples = partial(self._clear_auto_samples, service)
        service._publish_dbus_field.side_effect = self._publish_field_side_effect(service)

        controller = write_controller(service)

        self.assertTrue(controller.handle_write("/Mode", 1))

        self.assertTrue(service._auto_mode_cutover_pending)
        service._queue_relay_command.assert_called_once_with(False, 200.0)
        service._publish_local_pm_status.assert_called_once_with(False, 200.0)

    def test_handle_mode_write_manual_to_auto_skips_cutover_when_relay_is_confirmed_off(self) -> None:
        service = SimpleNamespace(
            virtual_mode=0,
            virtual_autostart=1,
            virtual_startstop=1,
            virtual_enable=0,
            auto_start_condition_since=None,
            auto_stop_condition_since=None,
            auto_samples=deque(),
            manual_override_until=10.0,
            _auto_mode_cutover_pending=True,
            _ignore_min_offtime_once=True,
            _dbusservice={"/Mode": 0, "/StartStop": 1, "/Enable": 0},
            time_now=MagicMock(return_value=200.0),
            _normalize_mode=self._normalize_mode,
            _mode_uses_auto_logic=self._mode_uses_auto_logic,
            _clear_auto_samples=MagicMock(),
            _queue_relay_command=MagicMock(),
            _publish_local_pm_status=MagicMock(),
            _peek_pending_relay_command=MagicMock(return_value=(None, None)),
            _last_pm_status={"output": False},
            _last_pm_status_confirmed=True,
            _last_pm_status_at=199.5,
            _worker_poll_interval_seconds=1.0,
            relay_sync_timeout_seconds=2.0,
            _get_worker_snapshot=MagicMock(
                return_value={
                    "pm_status": {"output": False},
                    "pm_confirmed": True,
                    "pm_captured_at": 199.5,
                    "pv_power": 10,
                    "battery_soc": 50,
                    "grid_power": -10,
                }
            ),
            _update_worker_snapshot=MagicMock(),
            _publish_dbus_field=MagicMock(),
            _state_summary=self._state_summary,
            _save_runtime_state=MagicMock(),
        )
        service._clear_auto_samples = partial(self._clear_auto_samples, service)
        service._publish_dbus_field.side_effect = self._publish_field_side_effect(service)

        controller = write_controller(service)

        self.assertTrue(controller.handle_write("/Mode", 1))

        self.assertFalse(service._auto_mode_cutover_pending)
        self.assertFalse(service._ignore_min_offtime_once)
        self.assertEqual(service.virtual_enable, 0)
        service._queue_relay_command.assert_not_called()
        service._publish_local_pm_status.assert_not_called()

    def test_handle_software_update_run_write_queues_request_and_resets_dbus_path(self) -> None:
        service = SimpleNamespace(
            _dbusservice={"/Auto/SoftwareUpdateRun": 0},
            time_now=MagicMock(return_value=200.0),
            _publish_dbus_field=MagicMock(),
            _state_summary=self._state_summary,
            _save_runtime_state=MagicMock(),
            _save_runtime_overrides=MagicMock(),
            _software_update_run_requested_at=None,
        )
        service._publish_dbus_field.side_effect = self._publish_field_side_effect(service)

        controller = write_controller(service)

        self.assertTrue(controller.handle_write("/Auto/SoftwareUpdateRun", 1))

        self.assertEqual(service._software_update_run_requested_at, 200.0)
        self.assertEqual(service._dbusservice["/Auto/SoftwareUpdateRun"], 0)
        service._save_runtime_state.assert_called_once()
        service._save_runtime_overrides.assert_called_once()

    def test_snapshot_write_state_skips_non_mapping_dbusservice_objects(self) -> None:
        service = SimpleNamespace(
            _dbusservice=object(),
            _dbus_publish_state={"/Mode": {"value": 0}},
            _worker_snapshot={"captured_at": 1.0},
        )

        snapshot = DbusWriteController._snapshot_write_state(service)

        self.assertNotIn("_dbusservice", snapshot["mappings"])
        self.assertIn("_dbus_publish_state", snapshot["mappings"])

    def test_handle_enable_write_in_manual_mode_switches_relay(self) -> None:
        service = SimpleNamespace(
            virtual_mode=0,
            virtual_startstop=0,
            virtual_enable=0,
            auto_manual_override_seconds=300,
            manual_override_until=0.0,
            _dbusservice={"/StartStop": 0, "/Enable": 0},
            time_now=MagicMock(return_value=100.0),
            _mode_uses_auto_logic=self._mode_uses_auto_logic,
            _queue_relay_command=MagicMock(),
            _publish_local_pm_status=MagicMock(),
            _publish_dbus_field=MagicMock(),
            _state_summary=self._state_summary,
            _save_runtime_state=MagicMock(),
        )
        service._publish_dbus_field.side_effect = self._publish_field_side_effect(service)

        controller = write_controller(service)
        result = controller.handle_write("/Enable", 1)

        self.assertTrue(result)
        self.assertEqual(service.virtual_enable, 1)
        self.assertEqual(service.virtual_startstop, 1)
        self.assertEqual(service.manual_override_until, 400.0)
        service._queue_relay_command.assert_called_once_with(True, 100.0)
        service._publish_local_pm_status.assert_called_once_with(True, 100.0)
        self.assertEqual(service._dbusservice["/StartStop"], 1)
        self.assertEqual(service._dbusservice["/Enable"], 1)
        service._save_runtime_state.assert_called_once()

    def test_handle_enable_write_in_manual_mode_uses_charger_backend_when_available(self) -> None:
        charger_backend = SimpleNamespace(set_enabled=MagicMock())
        service = SimpleNamespace(
            virtual_mode=0,
            virtual_startstop=0,
            virtual_enable=0,
            auto_manual_override_seconds=300,
            manual_override_until=0.0,
            _charger_backend=charger_backend,
            _dbusservice={"/StartStop": 0, "/Enable": 0},
            time_now=MagicMock(return_value=100.0),
            _mode_uses_auto_logic=self._mode_uses_auto_logic,
            _queue_relay_command=MagicMock(),
            _publish_local_pm_status=MagicMock(),
            _publish_dbus_field=MagicMock(),
            _state_summary=self._state_summary,
            _save_runtime_state=MagicMock(),
        )
        service._publish_dbus_field.side_effect = self._publish_field_side_effect(service)

        controller = write_controller(service)

        self.assertTrue(controller.handle_write("/Enable", 1))

        charger_backend.set_enabled.assert_called_once_with(True)
        service._queue_relay_command.assert_not_called()
        service._publish_local_pm_status.assert_not_called()
        self.assertEqual(service.virtual_enable, 1)
        self.assertEqual(service.virtual_startstop, 1)
        self.assertEqual(service.manual_override_until, 400.0)
        self.assertEqual(service._dbusservice["/StartStop"], 1)
        self.assertEqual(service._dbusservice["/Enable"], 1)

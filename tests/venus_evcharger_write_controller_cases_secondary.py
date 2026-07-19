# SPDX-License-Identifier: GPL-3.0-or-later
from tests.venus_evcharger_write_controller_support import *


class TestDbusWriteControllerSecondary(DbusWriteControllerTestBase):
    def test_handle_mode_write_keeps_in_flight_cutover_state_after_relay_side_effects_start(self) -> None:
        service = SimpleNamespace(
            virtual_mode=0,
            virtual_autostart=1,
            virtual_startstop=1,
            virtual_enable=0,
            auto_start_condition_since=100.0,
            auto_stop_condition_since=200.0,
            auto_stop_condition_reason=None,
            auto_samples=deque([(205.0, 2200.0, -2200.0)]),
            _stop_smoothed_surplus_power=123.0,
            _stop_smoothed_grid_power=45.0,
            manual_override_until=500.0,
            _auto_mode_cutover_pending=False,
            _ignore_min_offtime_once=False,
            _pending_relay_state=None,
            _pending_relay_requested_at=None,
            _relay_sync_expected_state=None,
            _relay_sync_requested_at=None,
            _relay_sync_deadline_at=None,
            _relay_sync_failure_reported=False,
            _last_pm_status={"output": True},
            _last_pm_status_at=150.0,
            _last_pm_status_confirmed=True,
            _last_confirmed_pm_status={"output": True},
            _last_confirmed_pm_status_at=150.0,
            _worker_snapshot={"captured_at": 150.0},
            _dbusservice={"/Mode": 0, "/StartStop": 1, "/Enable": 0},
            _dbus_publish_state={"/Mode": {"value": 0, "updated_at": 150.0}},
            time_now=MagicMock(return_value=200.0),
            _normalize_mode=self._normalize_mode,
            _mode_uses_auto_logic=self._mode_uses_auto_logic,
            _clear_auto_samples=MagicMock(),
            _publish_local_pm_status=MagicMock(side_effect=RuntimeError("publish failed")),
            _peek_pending_relay_command=MagicMock(return_value=(None, None)),
            _get_worker_snapshot=MagicMock(return_value={"pv_power": 10, "battery_soc": 50, "grid_power": -10}),
            _update_worker_snapshot=MagicMock(),
            _publish_dbus_field=MagicMock(),
            _state_summary=self._state_summary,
            _save_runtime_state=MagicMock(),
        )

        def _queue_side_effect(relay_on: bool, current_time: float) -> None:
            service._pending_relay_state = bool(relay_on)
            service._pending_relay_requested_at = current_time
            service._relay_sync_expected_state = bool(relay_on)
            service._relay_sync_requested_at = current_time
            service._relay_sync_deadline_at = current_time + 2.0
            service._relay_sync_failure_reported = False

        service._queue_relay_command = MagicMock(side_effect=_queue_side_effect)
        service._clear_auto_samples = partial(self._clear_auto_samples, service)
        service._publish_dbus_field.side_effect = self._publish_field_side_effect(service)

        controller = write_controller(service)

        self.assertTrue(controller.handle_write("/Mode", 1))
        self.assertEqual(service.virtual_mode, 1)
        self.assertEqual(service.virtual_enable, 1)
        self.assertEqual(service.virtual_startstop, 0)
        self.assertEqual(service.manual_override_until, 0.0)
        self.assertTrue(service._auto_mode_cutover_pending)
        self.assertFalse(service._ignore_min_offtime_once)
        self.assertEqual(list(service.auto_samples), [])
        self.assertEqual(service._stop_smoothed_surplus_power, 123.0)
        self.assertEqual(service._stop_smoothed_grid_power, 45.0)
        self.assertFalse(service._pending_relay_state)
        self.assertEqual(service._pending_relay_requested_at, 200.0)
        self.assertFalse(service._relay_sync_expected_state)
        self.assertEqual(service._relay_sync_requested_at, 200.0)
        self.assertEqual(service._relay_sync_deadline_at, 202.0)
        self.assertFalse(service._relay_sync_failure_reported)
        self.assertEqual(service._dbusservice["/Mode"], 1)
        self.assertEqual(service._dbusservice["/StartStop"], 1)
        self.assertEqual(service._dbusservice["/Enable"], 1)
        service._save_runtime_state.assert_called_once()

    def test_handle_write_rolls_back_when_failure_happens_before_relay_side_effects(self) -> None:
        service = SimpleNamespace(
            virtual_mode=0,
            virtual_autostart=0,
            virtual_startstop=0,
            virtual_enable=0,
            auto_start_condition_since=None,
            auto_stop_condition_since=None,
            auto_samples=deque(),
            manual_override_until=0.0,
            _auto_mode_cutover_pending=False,
            _ignore_min_offtime_once=False,
            _dbusservice={"/AutoStart": 0},
            time_now=MagicMock(return_value=100.0),
            _publish_dbus_field=MagicMock(side_effect=RuntimeError("fail")),
            _state_summary=self._state_summary,
            _save_runtime_state=MagicMock(),
        )

        controller = write_controller(service)

        self.assertFalse(controller.handle_write("/AutoStart", 1))
        self.assertEqual(service.virtual_autostart, 0)

    def test_restore_write_state_recovers_values_deques_and_mappings_from_non_container_targets(self) -> None:
        service = SimpleNamespace(
            scalar_value=0.0,
            sample_buffer=None,
            mapping_state=None,
        )
        snapshot = {
            "attrs": {},
            "deques": {"sample_buffer": deque([(1.0, 2.0, 3.0)])},
            "values": {"scalar_value": 12.5},
            "mappings": {"mapping_state": {"mode": 1}},
        }

        DbusWriteController._restore_write_state(service, snapshot)

        self.assertEqual(service.scalar_value, 12.5)
        self.assertIsInstance(service.sample_buffer, deque)
        self.assertEqual(list(service.sample_buffer), [(1.0, 2.0, 3.0)])
        self.assertEqual(service.mapping_state, {"mode": 1})

    def test_restore_write_state_ignores_dbus_restore_errors(self) -> None:
        class FailingDbusService(dict[str, object]):
            def __setitem__(self, key: str, value: object) -> None:
                raise RuntimeError("dbus write failed")

        service = SimpleNamespace(_dbusservice=FailingDbusService())
        snapshot = {
            "attrs": {},
            "deques": {},
            "values": {},
            "mappings": {},
            "dbus_paths": {"/Mode": 0},
        }

        DbusWriteController._restore_write_state(service, snapshot)

    def test_restore_write_state_queues_dbus_restore_when_async_publish_is_available(self) -> None:
        service = SimpleNamespace(
            time_now=MagicMock(return_value=123.0),
            _enqueue_dbus_publish_values=MagicMock(),
        )
        snapshot = {
            "attrs": {},
            "deques": {},
            "values": {},
            "mappings": {},
            "dbus_paths": {"/Mode": 0},
        }

        DbusWriteController._restore_write_state(service, snapshot)

        service._enqueue_dbus_publish_values.assert_called_once_with([("/Mode", 0)], 123.0)

    def test_handle_mode_write_returns_true_when_save_fails_after_relay_side_effects_started(self) -> None:
        service = SimpleNamespace(
            virtual_mode=0,
            virtual_autostart=1,
            virtual_startstop=1,
            virtual_enable=0,
            auto_start_condition_since=None,
            auto_stop_condition_since=None,
            auto_samples=deque(),
            manual_override_until=500.0,
            _auto_mode_cutover_pending=False,
            _ignore_min_offtime_once=False,
            _pending_relay_state=None,
            _pending_relay_requested_at=None,
            _relay_sync_expected_state=None,
            _relay_sync_requested_at=None,
            _relay_sync_deadline_at=None,
            _relay_sync_failure_reported=False,
            _dbusservice={"/Mode": 0, "/StartStop": 1, "/Enable": 0},
            time_now=MagicMock(return_value=200.0),
            _normalize_mode=self._normalize_mode,
            _mode_uses_auto_logic=self._mode_uses_auto_logic,
            _clear_auto_samples=MagicMock(),
            _queue_relay_command=MagicMock(),
            _publish_local_pm_status=MagicMock(return_value={"output": False}),
            _peek_pending_relay_command=MagicMock(return_value=(None, None)),
            _get_worker_snapshot=MagicMock(return_value={"pv_power": 10, "battery_soc": 50, "grid_power": -10}),
            _update_worker_snapshot=MagicMock(),
            _publish_dbus_field=MagicMock(),
            _state_summary=self._state_summary,
            _save_runtime_state=MagicMock(side_effect=RuntimeError("save failed")),
        )
        service._clear_auto_samples = partial(self._clear_auto_samples, service)
        service._publish_dbus_field.side_effect = self._publish_field_side_effect(service)
        controller = write_controller(service)

        self.assertTrue(controller.handle_write("/Mode", 1))
        self.assertEqual(service.virtual_mode, 1)
        self.assertTrue(service._auto_mode_cutover_pending)
        service._queue_relay_command.assert_called_once_with(False, 200.0)
        service._publish_local_pm_status.assert_called_once_with(False, 200.0)

    def test_handle_mode_write_scheduled_to_manual_under_load_keeps_live_session_and_skips_cutover(self) -> None:
        service = SimpleNamespace(
            virtual_mode=2,
            virtual_autostart=1,
            virtual_startstop=1,
            virtual_enable=1,
            auto_start_condition_since=100.0,
            auto_stop_condition_since=200.0,
            auto_stop_condition_reason="scheduled-night-charge",
            auto_samples=deque([(205.0, 500.0, 300.0)]),
            manual_override_until=0.0,
            _auto_mode_cutover_pending=False,
            _ignore_min_offtime_once=False,
            _dbusservice={"/Mode": 2, "/StartStop": 1, "/Enable": 1},
            time_now=MagicMock(return_value=200.0),
            _normalize_mode=self._normalize_mode,
            _mode_uses_auto_logic=self._mode_uses_auto_logic,
            _clear_auto_samples=MagicMock(),
            _queue_relay_command=MagicMock(),
            _publish_local_pm_status=MagicMock(),
            _peek_pending_relay_command=MagicMock(return_value=(None, None)),
            _last_pm_status={"output": True},
            _last_pm_status_confirmed=True,
            _last_pm_status_at=199.5,
            _get_worker_snapshot=MagicMock(return_value={"pm_status": {"output": True}, "pm_confirmed": True}),
            _update_worker_snapshot=MagicMock(),
            _publish_dbus_field=MagicMock(),
            _state_summary=self._state_summary,
            _save_runtime_state=MagicMock(),
        )
        service._clear_auto_samples = partial(self._clear_auto_samples, service)
        service._publish_dbus_field.side_effect = self._publish_field_side_effect(service)

        controller = write_controller(service)

        self.assertTrue(controller.handle_write("/Mode", 0))

        self.assertEqual(service.virtual_mode, 0)
        self.assertEqual(service.virtual_enable, 1)
        self.assertEqual(service.virtual_startstop, 1)
        self.assertFalse(service._auto_mode_cutover_pending)
        service._queue_relay_command.assert_not_called()
        service._publish_local_pm_status.assert_not_called()
        self.assertEqual(list(service.auto_samples), [])
        service._save_runtime_state.assert_called_once()

    def test_handle_mode_write_manual_to_auto_still_queues_cutover_when_charger_transport_error_is_latched(self) -> None:
        service = SimpleNamespace(
            virtual_mode=0,
            virtual_autostart=1,
            virtual_startstop=1,
            virtual_enable=0,
            auto_start_condition_since=None,
            auto_stop_condition_since=None,
            auto_samples=deque(),
            manual_override_until=500.0,
            _auto_mode_cutover_pending=False,
            _ignore_min_offtime_once=False,
            _last_charger_transport_reason="offline",
            _last_charger_transport_source="enable",
            _last_charger_transport_at=150.0,
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

        self.assertTrue(controller.handle_write("/Mode", 1))

        self.assertEqual(service.virtual_mode, 1)
        self.assertTrue(service._auto_mode_cutover_pending)
        self.assertEqual(service.manual_override_until, 0.0)
        service._queue_relay_command.assert_called_once_with(False, 200.0)
        service._publish_local_pm_status.assert_called_once_with(False, 200.0)

    def test_handle_autostart_write_restores_dbus_path_when_save_fails_before_relay_side_effects(self) -> None:
        service = SimpleNamespace(
            virtual_mode=0,
            virtual_autostart=0,
            virtual_startstop=0,
            virtual_enable=0,
            auto_start_condition_since=None,
            auto_stop_condition_since=None,
            auto_samples=deque(),
            manual_override_until=0.0,
            _auto_mode_cutover_pending=False,
            _ignore_min_offtime_once=False,
            _dbusservice={"/AutoStart": 0},
            _dbus_publish_state={"/AutoStart": {"value": 0, "updated_at": 10.0}},
            time_now=MagicMock(return_value=200.0),
            _publish_dbus_field=MagicMock(),
            _state_summary=self._state_summary,
            _save_runtime_state=MagicMock(side_effect=RuntimeError("save failed")),
        )
        service._publish_dbus_field.side_effect = self._publish_field_side_effect(service)

        controller = write_controller(service)

        self.assertFalse(controller.handle_write("/AutoStart", 1))
        self.assertEqual(service.virtual_autostart, 0)
        self.assertEqual(service._dbusservice["/AutoStart"], 0)
        self.assertEqual(service._dbus_publish_state["/AutoStart"]["value"], 0)

    def test_mode_autostart_current_and_error_paths(self) -> None:
        service = SimpleNamespace(
            virtual_mode=5,
            virtual_autostart=0,
            virtual_startstop=0,
            virtual_enable=0,
            auto_start_condition_since=1.0,
            auto_stop_condition_since=2.0,
            auto_samples=deque([(1.0, 2.0, 3.0)]),
            manual_override_until=50.0,
            _auto_mode_cutover_pending=True,
            _ignore_min_offtime_once=True,
            max_current=16.0,
            min_current=6.0,
            virtual_set_current=10.0,
            _dbusservice={"/Mode": 5, "/StartStop": 0, "/Enable": 0, "/AutoStart": 0, "/SetCurrent": 10.0},
            time_now=MagicMock(return_value=42.0),
            _normalize_mode=self._normalize_mode_5_to_2,
            _mode_uses_auto_logic=self._mode_uses_auto_logic,
            _clear_auto_samples=MagicMock(),
            _queue_relay_command=MagicMock(),
            _publish_local_pm_status=MagicMock(),
            _get_worker_snapshot=MagicMock(return_value={"pv_power": 10, "battery_soc": 50, "grid_power": -10}),
            _update_worker_snapshot=MagicMock(),
            _publish_dbus_field=MagicMock(),
            _state_summary=self._state_summary,
            _save_runtime_state=MagicMock(),
        )
        service._clear_auto_samples = partial(self._clear_auto_samples, service)
        service._publish_dbus_field.side_effect = self._publish_field_side_effect(service)
        controller = write_controller(service)

        self.assertTrue(controller.handle_write("/Mode", 5))
        self.assertEqual(service.virtual_mode, 2)
        self.assertEqual(service.manual_override_until, 0.0)
        self.assertTrue(service._auto_mode_cutover_pending)
        service._queue_relay_command.assert_called_once_with(False, 42.0)

        self.assertTrue(controller.handle_write("/AutoStart", 1))
        self.assertEqual(service.virtual_autostart, 1)
        self.assertEqual(service._dbusservice["/AutoStart"], 1)

        self.assertTrue(controller.handle_write("/SetCurrent", 12.5))
        self.assertTrue(controller.handle_write("/MaxCurrent", 20))
        self.assertTrue(controller.handle_write("/MinCurrent", 4))
        self.assertEqual(service.virtual_set_current, 12.5)
        self.assertEqual(service.max_current, 20.0)
        self.assertEqual(service.min_current, 4.0)

        service._save_runtime_state.reset_mock()
        service._publish_dbus_field.side_effect = RuntimeError("fail")
        self.assertFalse(controller.handle_write("/AutoStart", 1))
        self.assertEqual(service.virtual_autostart, 1)
        service._save_runtime_state.assert_not_called()

# SPDX-License-Identifier: GPL-3.0-or-later
from tests.venus_evcharger_publisher_support import (
    DbusPublishController,
    DbusPublishControllerTestCase,
    MagicMock,
    SimpleNamespace,
    patch,
)


class TestDbusPublishControllerPublish(DbusPublishControllerTestCase):
    def test_publish_path_handles_change_and_interval_throttling(self) -> None:
        service = SimpleNamespace(
            _dbusservice={},
            _dbus_publish_state={},
            _dbus_live_publish_interval_seconds=1.0,
            _dbus_slow_publish_interval_seconds=5.0,
        )
        controller = DbusPublishController(service, self._age_seconds)

        self.assertTrue(controller.publish_path("/Path", 1, now=100.0))
        self.assertFalse(controller.publish_path("/Path", 1, now=101.0))

        service._dbus_publish_state["/IntervalMissing"] = {"value": 5}
        self.assertTrue(controller.publish_path("/IntervalMissing", 5, now=100.0, interval_seconds=5.0))

        self.assertFalse(controller.publish_path("/IntervalMissing", 7, now=103.0, interval_seconds=5.0))
        self.assertTrue(controller.publish_path("/IntervalMissing", 7, now=106.0, interval_seconds=5.0))

    def test_publish_live_measurements_rolls_back_publish_state_and_marks_failure(self) -> None:
        class FlakyDbusService(dict[str, float]):
            def __init__(self) -> None:
                super().__init__({"/Ac/Power": 10.0})
                self.writes: list[tuple[str, float]] = []

            def __setitem__(self, key: str, value: float) -> None:
                self.writes.append((key, value))
                if key == "/Ac/Voltage":
                    raise RuntimeError("dbus write failed")
                super().__setitem__(key, value)

        service = SimpleNamespace(
            _dbusservice=FlakyDbusService(),
            _dbus_publish_state={
                "/Ac/Power": {"value": 10.0, "updated_at": 90.0},
            },
            _dbus_live_publish_interval_seconds=1.0,
            _dbus_slow_publish_interval_seconds=5.0,
            _mark_failure=MagicMock(),
            _warning_throttled=MagicMock(),
        )
        controller = DbusPublishController(service, self._age_seconds)

        changed = controller.publish_live_measurements(
            1000.0,
            230.0,
            4.3,
            {
                "L1": {"power": 1000.0, "current": 4.3, "voltage": 230.0},
                "L2": {"power": 0.0, "current": 0.0, "voltage": 0.0},
                "L3": {"power": 0.0, "current": 0.0, "voltage": 0.0},
            },
            100.0,
        )

        self.assertFalse(changed)
        self.assertEqual(service._dbusservice["/Ac/Power"], 10.0)
        self.assertEqual(service._dbus_publish_state["/Ac/Power"], {"value": 10.0, "updated_at": 90.0})
        service._mark_failure.assert_called_once_with("dbus")
        service._warning_throttled.assert_called_once()

    def test_publish_config_paths_is_all_or_nothing_for_publish_state(self) -> None:
        class FlakyDbusService(dict[str, object]):
            def __setitem__(self, key: str, value: object) -> None:
                if key == "/Enable":
                    raise RuntimeError("dbus write failed")
                super().__setitem__(key, value)

        service = SimpleNamespace(
            _dbusservice=FlakyDbusService(),
            _dbus_publish_state={},
            _dbus_live_publish_interval_seconds=1.0,
            _dbus_slow_publish_interval_seconds=5.0,
            _mark_failure=MagicMock(),
            _warning_throttled=MagicMock(),
            virtual_mode=1,
            virtual_autostart=1,
            virtual_enable=1,
            virtual_set_current=16.0,
            requested_phase_selection="P1",
            active_phase_selection="P1",
            supported_phase_selections=("P1", "P1_P2"),
            min_current=6.0,
            max_current=16.0,
        )
        controller = DbusPublishController(service, self._age_seconds)

        changed = controller.publish_config_paths(1, 100.0)

        self.assertFalse(changed)
        self.assertNotIn("/Mode", service._dbus_publish_state)
        self.assertNotIn("/Enable", service._dbus_publish_state)
        service._mark_failure.assert_called_once_with("dbus")

    def test_learned_display_helpers_cover_empty_scalar_and_fault_paths(self) -> None:
        service = SimpleNamespace(
            _charger_backend=object(),
            learned_charge_power_state="stable",
            learned_charge_power_watts=None,
            learned_charge_power_updated_at=None,
            learned_charge_power_phase="L1",
            learned_charge_power_voltage=230.0,
            auto_learn_charge_power_max_age_seconds=0.0,
            phase="L1",
            voltage_mode="phase",
            _last_charger_state_current_amps=0.0,
            _last_health_reason="contactor-lockout-open",
            _last_charger_state_fault="contactor-lockout-open",
            _last_charger_state_at=100.0,
        )
        controller = DbusPublishController(service, self._age_seconds)

        self.assertIsNone(controller._charger_current_readback(100.0))
        self.assertFalse(controller._learned_charge_power_expired_for_display(100.0))
        service.auto_learn_charge_power_max_age_seconds = 60.0
        self.assertTrue(controller._learned_charge_power_expired_for_display(100.0))
        self.assertIsNone(controller._validated_learned_display_scalars(None, 230.0))
        self.assertIsNone(controller._validated_learned_display_scalars(2000.0, None))
        self.assertIsNone(controller._raw_learned_display_values())
        self.assertIsNone(controller._stable_learned_display_inputs(100.0))
        self.assertIsNone(controller._rounded_display_current(0.4))
        self.assertIsNone(controller._derived_learned_set_current(100.0))
        self.assertEqual(controller._fault_active(service), 1)

    def test_publish_helpers_cover_delete_failure_and_display_fallback_edges(self) -> None:
        class _DeleteFailingDbusService(dict[str, object]):
            def __delitem__(self, key: str) -> None:
                raise RuntimeError("cannot delete")

        service = SimpleNamespace(
            _dbusservice=_DeleteFailingDbusService({"/Ghost": 1}),
            _dbus_publish_state={"/Ghost": {"value": 1, "updated_at": 1.0}},
            _dbus_live_publish_interval_seconds=1.0,
            _dbus_slow_publish_interval_seconds=5.0,
            _charger_backend=None,
            virtual_set_current=16.0,
            learned_charge_power_state="stable",
            learned_charge_power_watts=230.0,
            learned_charge_power_updated_at=100.0,
            learned_charge_power_phase="L1",
            learned_charge_power_voltage=230.0,
            auto_learn_charge_power_max_age_seconds=21600.0,
            min_current=6.0,
            max_current=16.0,
            voltage_mode="phase",
            phase="L1",
        )
        controller = DbusPublishController(service, self._age_seconds)

        self.assertTrue(controller.publish_path("/Ghost", None, now=10.0, force=True))
        self.assertEqual(controller._display_set_current(100.0), 6.0)

    def test_publish_helpers_cover_remaining_restore_and_learned_display_edges(self) -> None:
        class _DeleteFailingDbusService(dict[str, object]):
            def __delitem__(self, key: str) -> None:
                raise RuntimeError("cannot delete")

        service = SimpleNamespace(
            _dbusservice=_DeleteFailingDbusService({"/Ghost": 1}),
            _dbus_publish_state={},
            _dbus_live_publish_interval_seconds=1.0,
            _dbus_slow_publish_interval_seconds=5.0,
            min_current=6.0,
            max_current=16.0,
            voltage_mode="phase",
        )
        controller = DbusPublishController(service, self._age_seconds)

        controller._restore_service_values(["/Ghost"], {})

        with patch.object(controller, "_learned_display_current_allowed", return_value=True):
            with patch.object(controller, "_raw_learned_display_values", return_value=None):
                self.assertIsNone(controller._stable_learned_display_inputs(100.0))
            with (
                patch.object(controller, "_raw_learned_display_values", return_value=(2300.0, 230.0, "L1")),
                patch.object(controller, "_phase_voltage_for_display_current", return_value=None),
            ):
                self.assertIsNone(controller._stable_learned_display_inputs(100.0))
            with (
                patch.object(
                    controller,
                    "_stable_learned_display_inputs",
                    return_value=SimpleNamespace(power_w=1.0, phase_voltage_v=230.0, phase_count=1.0),
                ),
                patch.object(controller, "_rounded_display_current", return_value=None),
            ):
                self.assertIsNone(controller._derived_learned_set_current(100.0))

    def test_learned_display_helpers_cover_minimal_candidate_and_unbounded_current_edges(self) -> None:
        service = SimpleNamespace(
            _charger_backend=object(),
            _dbus_live_publish_interval_seconds=0.0,
            auto_shelly_soft_fail_seconds=0.0,
            min_current=None,
            max_current=0.0,
        )
        controller = DbusPublishController(service, self._age_seconds)

        self.assertEqual(controller._charger_state_max_age_seconds(), 2.0)
        self.assertEqual(controller._clamped_display_current(12.5), 12.5)

    def test_publish_transactional_removes_new_paths_when_group_write_fails(self) -> None:
        class FlakyDbusService(dict[str, int]):
            def __setitem__(self, key: str, value: int) -> None:
                if key == "/B":
                    raise RuntimeError("group write failed")
                super().__setitem__(key, value)

        service = SimpleNamespace(
            _dbusservice=FlakyDbusService(),
            _dbus_publish_state={},
            _dbus_live_publish_interval_seconds=1.0,
            _dbus_slow_publish_interval_seconds=5.0,
            _mark_failure=MagicMock(),
            _warning_throttled=MagicMock(),
        )
        controller = DbusPublishController(service, self._age_seconds)

        changed = controller._publish_values_transactional(
            "generic",
            {"/A": 1, "/B": 2},
            100.0,
            force=True,
        )

        self.assertFalse(changed)
        self.assertNotIn("/A", service._dbusservice)
        self.assertNotIn("/B", service._dbusservice)
        self.assertEqual(service._dbus_publish_state, {})
        service._mark_failure.assert_called_once_with("dbus")

    def test_publish_group_failure_falls_back_to_logging_without_warning_helper(self) -> None:
        service = SimpleNamespace(
            _dbusservice={},
            _dbus_publish_state={},
            _dbus_live_publish_interval_seconds=1.0,
            _dbus_slow_publish_interval_seconds=5.0,
            _mark_failure=MagicMock(),
        )
        controller = DbusPublishController(service, self._age_seconds)

        with patch("logging.warning") as warning:
            controller._publish_group_failure("diagnostic", ["/Path"], 123.0)

        service._mark_failure.assert_called_once_with("dbus")
        warning.assert_called_once()

    def test_publish_values_returns_false_when_group_is_fully_throttled(self) -> None:
        service = SimpleNamespace(
            _dbusservice={"/Path": 5},
            _dbus_publish_state={
                "/Path": {"value": 5, "updated_at": 95.0},
            },
            _dbus_live_publish_interval_seconds=1.0,
            _dbus_slow_publish_interval_seconds=5.0,
        )
        controller = DbusPublishController(service, self._age_seconds)

        changed = controller._publish_values({"/Path": 5}, 100.0, interval_seconds=10.0)

        self.assertFalse(changed)
        self.assertEqual(service._dbusservice["/Path"], 5)
        self.assertEqual(service._dbus_publish_state["/Path"], {"value": 5, "updated_at": 95.0})

    def test_off_mainloop_publish_is_queued_without_touching_dbus_service(self) -> None:
        service = SimpleNamespace(
            _dbusservice={"/Path": 1},
            _dbus_publish_state={},
            _dbus_live_publish_interval_seconds=1.0,
            _dbus_slow_publish_interval_seconds=5.0,
            _dbus_publish_direct_allowed=MagicMock(return_value=False),
            _enqueue_dbus_publish_values=MagicMock(return_value=True),
        )
        controller = DbusPublishController(service, self._age_seconds)

        changed = controller._publish_values({"/Path": 2, "/Other": 3}, 100.0, force=True)

        self.assertTrue(changed)
        self.assertEqual(service._dbusservice["/Path"], 1)
        service._enqueue_dbus_publish_values.assert_called_once_with(
            [("/Path", 2), ("/Other", 3)],
            100.0,
        )

    def test_off_mainloop_update_index_bump_is_queued(self) -> None:
        service = SimpleNamespace(
            _dbusservice={"/UpdateIndex": 7},
            _dbus_publish_state={},
            _dbus_live_publish_interval_seconds=1.0,
            _dbus_slow_publish_interval_seconds=5.0,
            _dbus_publish_direct_allowed=MagicMock(return_value=False),
            _enqueue_dbus_publish_values=MagicMock(return_value=True),
            _enqueue_dbus_update_index_bump=MagicMock(),
        )
        controller = DbusPublishController(service, self._age_seconds)

        controller.bump_update_index(100.0)

        self.assertEqual(service._dbusservice["/UpdateIndex"], 7)
        service._enqueue_dbus_update_index_bump.assert_called_once_with(100.0)

    def test_direct_dbus_publish_guard_fails_when_wrong_thread_touches_service(self) -> None:
        service = SimpleNamespace(
            _dbusservice={"/Path": 1},
            _dbus_publish_state={},
            _dbus_live_publish_interval_seconds=1.0,
            _dbus_slow_publish_interval_seconds=5.0,
            _dbus_publish_direct_allowed=MagicMock(return_value=True),
            _assert_dbus_mainloop_thread=MagicMock(side_effect=RuntimeError("wrong thread")),
        )
        controller = DbusPublishController(service, self._age_seconds)

        with self.assertRaisesRegex(RuntimeError, "wrong thread"):
            controller.publish_path("/Path", 2, now=100.0, force=True)

    def test_publish_values_ignores_restore_failure_after_group_write_error(self) -> None:
        class FlakyRestoreDbusService(dict[str, int]):
            def __init__(self) -> None:
                super().__init__({"/A": 1})
                self.restore_attempts = 0

            def __setitem__(self, key: str, value: int) -> None:
                if key == "/A" and value == 1:
                    self.restore_attempts += 1
                    raise RuntimeError("restore failed")
                if key == "/B":
                    raise RuntimeError("group write failed")
                super().__setitem__(key, value)

        service = SimpleNamespace(
            _dbusservice=FlakyRestoreDbusService(),
            _dbus_publish_state={},
            _dbus_live_publish_interval_seconds=1.0,
            _dbus_slow_publish_interval_seconds=5.0,
            _mark_failure=MagicMock(),
            _warning_throttled=MagicMock(),
        )
        controller = DbusPublishController(service, self._age_seconds)

        changed = controller._publish_values_transactional(
            "generic",
            {"/A": 2, "/B": 3},
            100.0,
            force=True,
        )

        self.assertFalse(changed)
        self.assertEqual(service._dbusservice["/A"], 2)
        self.assertEqual(service._dbus_publish_state, {})
        self.assertEqual(service._dbusservice.restore_attempts, 1)
        service._mark_failure.assert_called_once_with("dbus")
        service._warning_throttled.assert_called_once()

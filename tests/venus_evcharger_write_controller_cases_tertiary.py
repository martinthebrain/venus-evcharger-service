# SPDX-License-Identifier: GPL-3.0-or-later
from tests.venus_evcharger_write_controller_support import *


class TestDbusWriteControllerTertiary(DbusWriteControllerTestBase):
    def test_write_port_relay_payload_helpers_validate_untrusted_shapes(self) -> None:
        self.assertIsNone(WriteControllerPort._relay_output_value(None))
        self.assertIsNone(WriteControllerPort._relay_output_value({}))
        self.assertFalse(WriteControllerPort._relay_output_value({"output": 0}))
        self.assertTrue(WriteControllerPort._relay_output_value({"output": 1}))
        self.assertFalse(WriteControllerPort._pending_relay_state_on([True, 100.0]))
        self.assertFalse(WriteControllerPort._pending_relay_state_on(()))
        self.assertFalse(WriteControllerPort._pending_relay_state_on((None, 100.0)))
        self.assertTrue(WriteControllerPort._pending_relay_state_on((True, 100.0)))

    def test_write_port_rejects_non_string_contract_results(self) -> None:
        service = SimpleNamespace(
            _apply_phase_selection=MagicMock(return_value={"phase": "bad"}),
            _state_summary=MagicMock(return_value={"state": "bad"}),
        )
        port = WriteControllerPort(service)

        with self.assertRaisesRegex(TypeError, "_apply_phase_selection must return str"):
            port.apply_phase_selection("P1")
        with self.assertRaisesRegex(TypeError, "_state_summary must return str"):
            port.state_summary()

    def test_auto_runtime_setting_write_publishes_and_persists_overrides(self) -> None:
        service = SimpleNamespace(
            virtual_mode=1,
            virtual_autostart=1,
            virtual_startstop=0,
            virtual_enable=1,
            auto_start_surplus_watts=1500.0,
            auto_stop_surplus_watts=1200.0,
            auto_min_soc=40.0,
            auto_resume_soc=50.0,
            auto_start_delay_seconds=10.0,
            auto_stop_delay_seconds=30.0,
            auto_phase_switching_enabled=True,
            auto_policy=SimpleNamespace(),
            _dbusservice={"/Auto/StartSurplusWatts": 1500.0},
            _time_now=MagicMock(return_value=42.0),
            _publish_dbus_path=MagicMock(),
            _state_summary=self._state_summary,
            _save_runtime_state=MagicMock(),
            _save_runtime_overrides=MagicMock(),
            _validate_runtime_config=MagicMock(),
        )
        service._publish_dbus_path.side_effect = self._publish_side_effect(service)
        controller = DbusWriteController(WriteControllerPort(service))

        with patch("venus_evcharger.controllers.write.AutoPolicy.from_service", return_value=service.auto_policy), patch(
            "venus_evcharger.controllers.write.validate_auto_policy"
        ) as validate_policy:
            self.assertTrue(controller.handle_write("/Auto/StartSurplusWatts", 1825.0))

        self.assertEqual(service.auto_start_surplus_watts, 1825.0)
        self.assertEqual(service._dbusservice["/Auto/StartSurplusWatts"], 1825.0)
        validate_policy.assert_called_once_with(service.auto_policy, service)
        service._validate_runtime_config.assert_called_once()
        service._save_runtime_state.assert_called_once()
        service._save_runtime_overrides.assert_called_once()

    def test_auto_runtime_setting_write_updates_dbus_backoff_without_policy_rebuild(self) -> None:
        service = SimpleNamespace(
            virtual_mode=1,
            virtual_autostart=1,
            virtual_startstop=0,
            virtual_enable=1,
            auto_dbus_backoff_base_seconds=5.0,
            _dbusservice={"/Auto/DbusBackoffBaseSeconds": 5.0},
            _time_now=MagicMock(return_value=42.0),
            _publish_dbus_path=MagicMock(),
            _state_summary=self._state_summary,
            _save_runtime_state=MagicMock(),
            _save_runtime_overrides=MagicMock(),
            _validate_runtime_config=MagicMock(),
        )
        service._publish_dbus_path.side_effect = self._publish_side_effect(service)
        controller = DbusWriteController(WriteControllerPort(service))

        with patch("venus_evcharger.controllers.write.validate_auto_policy") as validate_policy:
            self.assertTrue(controller.handle_write("/Auto/DbusBackoffBaseSeconds", 7.5))

        self.assertEqual(service.auto_dbus_backoff_base_seconds, 7.5)
        self.assertEqual(service._dbusservice["/Auto/DbusBackoffBaseSeconds"], 7.5)
        validate_policy.assert_not_called()
        service._validate_runtime_config.assert_called_once()
        service._save_runtime_state.assert_called_once()
        service._save_runtime_overrides.assert_called_once()

    def test_auto_runtime_setting_write_updates_scheduled_v2_settings_without_policy_rebuild(self) -> None:
        service = SimpleNamespace(
            virtual_mode=2,
            virtual_autostart=1,
            virtual_startstop=0,
            virtual_enable=1,
            auto_scheduled_enabled_days="Mon,Tue,Wed,Thu,Fri",
            auto_scheduled_night_start_delay_seconds=3600.0,
            auto_scheduled_latest_end_time="06:30",
            auto_scheduled_night_current_amps=0.0,
            _dbusservice={
                "/Auto/ScheduledEnabledDays": "Mon,Tue,Wed,Thu,Fri",
                "/Auto/ScheduledFallbackDelaySeconds": 3600.0,
                "/Auto/ScheduledLatestEndTime": "06:30",
                "/Auto/ScheduledNightCurrent": 0.0,
            },
            _time_now=MagicMock(return_value=42.0),
            _publish_dbus_path=MagicMock(),
            _state_summary=self._state_summary,
            _save_runtime_state=MagicMock(),
            _save_runtime_overrides=MagicMock(),
            _validate_runtime_config=MagicMock(),
        )
        service._publish_dbus_path.side_effect = self._publish_side_effect(service)
        controller = DbusWriteController(WriteControllerPort(service))

        with patch("venus_evcharger.controllers.write.validate_auto_policy") as validate_policy:
            self.assertTrue(controller.handle_write("/Auto/ScheduledEnabledDays", "Mon,Wed,Fri"))
            self.assertTrue(controller.handle_write("/Auto/ScheduledFallbackDelaySeconds", 1800.0))
            self.assertTrue(controller.handle_write("/Auto/ScheduledLatestEndTime", "07:15"))
            self.assertTrue(controller.handle_write("/Auto/ScheduledNightCurrent", 11.0))

        self.assertEqual(service.auto_scheduled_enabled_days, "Mon,Wed,Fri")
        self.assertEqual(service.auto_scheduled_night_start_delay_seconds, 1800.0)
        self.assertEqual(service.auto_scheduled_latest_end_time, "07:15")
        self.assertEqual(service.auto_scheduled_night_current_amps, 11.0)
        self.assertEqual(service._dbusservice["/Auto/ScheduledEnabledDays"], "Mon,Wed,Fri")
        self.assertEqual(service._dbusservice["/Auto/ScheduledFallbackDelaySeconds"], 1800.0)
        self.assertEqual(service._dbusservice["/Auto/ScheduledLatestEndTime"], "07:15")
        self.assertEqual(service._dbusservice["/Auto/ScheduledNightCurrent"], 11.0)
        validate_policy.assert_not_called()
        self.assertEqual(service._validate_runtime_config.call_count, 4)
        self.assertEqual(service._save_runtime_state.call_count, 4)
        self.assertEqual(service._save_runtime_overrides.call_count, 4)

    def test_auto_runtime_setting_write_updates_learn_charge_flag_with_policy_rebuild(self) -> None:
        service = SimpleNamespace(
            virtual_mode=1,
            virtual_autostart=1,
            virtual_startstop=0,
            virtual_enable=1,
            auto_learn_charge_power_enabled=True,
            auto_policy=SimpleNamespace(),
            _dbusservice={"/Auto/LearnChargePowerEnabled": 1},
            _time_now=MagicMock(return_value=42.0),
            _publish_dbus_path=MagicMock(),
            _state_summary=self._state_summary,
            _save_runtime_state=MagicMock(),
            _save_runtime_overrides=MagicMock(),
            _validate_runtime_config=MagicMock(),
        )
        service._publish_dbus_path.side_effect = self._publish_side_effect(service)
        controller = DbusWriteController(WriteControllerPort(service))

        with patch("venus_evcharger.controllers.write.AutoPolicy.from_service", return_value=service.auto_policy), patch(
            "venus_evcharger.controllers.write.validate_auto_policy"
        ) as validate_policy:
            self.assertTrue(controller.handle_write("/Auto/LearnChargePowerEnabled", 0))

        self.assertFalse(service.auto_learn_charge_power_enabled)
        self.assertEqual(service._dbusservice["/Auto/LearnChargePowerEnabled"], 0)
        validate_policy.assert_called_once_with(service.auto_policy, service)
        service._validate_runtime_config.assert_called_once()
        service._save_runtime_state.assert_called_once()
        service._save_runtime_overrides.assert_called_once()

    def test_manual_startstop_and_setcurrent_use_charger_backend_when_available(self) -> None:
        charger_backend = SimpleNamespace(
            set_enabled=MagicMock(),
            set_current=MagicMock(),
        )
        service = SimpleNamespace(
            virtual_mode=0,
            virtual_autostart=0,
            virtual_startstop=0,
            virtual_enable=0,
            auto_manual_override_seconds=300,
            manual_override_until=0.0,
            max_current=16.0,
            min_current=6.0,
            virtual_set_current=10.0,
            _charger_backend=charger_backend,
            _dbusservice={"/StartStop": 0, "/Enable": 0, "/SetCurrent": 10.0},
            _time_now=MagicMock(return_value=100.0),
            _normalize_mode=self._normalize_mode,
            _mode_uses_auto_logic=self._mode_uses_auto_logic,
            _queue_relay_command=MagicMock(),
            _publish_local_pm_status=MagicMock(),
            _publish_dbus_path=MagicMock(),
            _state_summary=self._state_summary,
            _save_runtime_state=MagicMock(),
        )
        service._publish_dbus_path.side_effect = self._publish_side_effect(service)
        controller = DbusWriteController(WriteControllerPort(service))

        self.assertTrue(controller.handle_write("/StartStop", 1))
        charger_backend.set_enabled.assert_called_once_with(True)
        service._queue_relay_command.assert_not_called()
        service._publish_local_pm_status.assert_not_called()
        self.assertEqual(service.virtual_startstop, 1)
        self.assertEqual(service.virtual_enable, 1)
        self.assertEqual(service.manual_override_until, 400.0)

        self.assertTrue(controller.handle_write("/SetCurrent", 12.5))
        charger_backend.set_current.assert_called_once_with(12.5)
        self.assertEqual(service.virtual_set_current, 12.5)
        self.assertEqual(service._dbusservice["/SetCurrent"], 12.5)

    def test_handle_setcurrent_returns_true_when_save_fails_after_charger_side_effects_started(self) -> None:
        charger_backend = SimpleNamespace(set_current=MagicMock())
        service = SimpleNamespace(
            virtual_mode=0,
            virtual_autostart=0,
            virtual_startstop=0,
            virtual_enable=0,
            max_current=16.0,
            min_current=6.0,
            virtual_set_current=10.0,
            _charger_backend=charger_backend,
            _dbusservice={"/SetCurrent": 10.0},
            _time_now=MagicMock(return_value=100.0),
            _mode_uses_auto_logic=self._mode_uses_auto_logic,
            _publish_dbus_path=MagicMock(),
            _state_summary=self._state_summary,
            _save_runtime_state=MagicMock(side_effect=RuntimeError("save failed")),
        )
        service._publish_dbus_path.side_effect = self._publish_side_effect(service)
        controller = DbusWriteController(WriteControllerPort(service))

        self.assertTrue(controller.handle_write("/SetCurrent", 12.5))

        charger_backend.set_current.assert_called_once_with(12.5)
        self.assertEqual(service.virtual_set_current, 12.5)
        self.assertEqual(service._dbusservice["/SetCurrent"], 12.5)

    def test_startstop_and_enable_in_auto_mode_cover_off_and_on_paths(self) -> None:
        service = SimpleNamespace(
            virtual_mode=1,
            virtual_startstop=0,
            virtual_enable=0,
            auto_manual_override_seconds=300,
            manual_override_until=0.0,
            _dbusservice={"/StartStop": 0, "/Enable": 0},
            _time_now=MagicMock(return_value=100.0),
            _mode_uses_auto_logic=self._mode_uses_auto_logic,
            _queue_relay_command=MagicMock(),
            _publish_local_pm_status=MagicMock(),
            _publish_dbus_path=MagicMock(),
            _state_summary=self._state_summary,
            _save_runtime_state=MagicMock(),
        )
        service._publish_dbus_path.side_effect = self._publish_side_effect(service)
        controller = DbusWriteController(WriteControllerPort(service))

        self.assertTrue(controller.handle_write("/StartStop", 1))
        self.assertEqual(service.virtual_enable, 1)
        service._queue_relay_command.assert_not_called()

        self.assertTrue(controller.handle_write("/StartStop", 0))
        service._queue_relay_command.assert_called_once_with(False, 100.0)
        service._publish_local_pm_status.assert_called_once_with(False, 100.0)

        service._queue_relay_command.reset_mock()
        service._publish_local_pm_status.reset_mock()
        self.assertTrue(controller.handle_write("/Enable", 1))
        service._queue_relay_command.assert_not_called()
        self.assertTrue(controller.handle_write("/Enable", 0))
        service._queue_relay_command.assert_called_once_with(False, 100.0)

    def test_mode_transition_and_publish_helpers_cover_remaining_branches(self) -> None:
        service = SimpleNamespace(
            virtual_mode=1,
            virtual_startstop=0,
            virtual_enable=1,
            _mode_uses_auto_logic=self._mode_uses_auto_logic,
            _publish_dbus_path=MagicMock(),
        )
        controller = DbusWriteController(WriteControllerPort(service))

        controller._handle_mode_transition_to_auto(1, 100.0)
        self.assertFalse(hasattr(service, "manual_override_until"))

        controller._publish_startstop_enable(controller.port, 100.0)
        service._publish_dbus_path.assert_any_call("/StartStop", 1, 100.0, force=True)
        service._publish_dbus_path.assert_any_call("/Enable", 1, 100.0, force=True)

    def test_handle_phase_selection_write_applies_supported_selection_when_relay_is_off(self) -> None:
        service = SimpleNamespace(
            virtual_mode=0,
            virtual_autostart=1,
            virtual_startstop=0,
            virtual_enable=0,
            requested_phase_selection="P1",
            active_phase_selection="P1",
            supported_phase_selections=("P1", "P1_P2"),
            auto_start_condition_since=None,
            auto_stop_condition_since=None,
            manual_override_until=0.0,
            _auto_mode_cutover_pending=False,
            _ignore_min_offtime_once=False,
            _dbusservice={
                "/PhaseSelection": "P1",
                "/PhaseSelectionActive": "P1",
                "/SupportedPhaseSelections": "P1,P1_P2",
            },
            _time_now=MagicMock(return_value=100.0),
            _normalize_mode=self._normalize_mode,
            _mode_uses_auto_logic=self._mode_uses_auto_logic,
            _phase_selection_requires_pause=MagicMock(return_value=True),
            _apply_phase_selection=MagicMock(),
            _peek_pending_relay_command=MagicMock(return_value=(None, None)),
            _last_pm_status={"output": False},
            _last_pm_status_confirmed=True,
            _get_worker_snapshot=MagicMock(return_value={"pm_status": {"output": False}, "pm_confirmed": True}),
            _publish_dbus_path=MagicMock(),
            _state_summary=self._state_summary,
            _save_runtime_state=MagicMock(),
        )
        service._apply_phase_selection.side_effect = partial(self._apply_phase_selection, service)
        service._publish_dbus_path.side_effect = self._publish_side_effect(service)

        controller = DbusWriteController(WriteControllerPort(service))

        self.assertTrue(controller.handle_write("/PhaseSelection", "P1_P2"))

        service._apply_phase_selection.assert_called_once_with("P1_P2")
        self.assertEqual(service.requested_phase_selection, "P1_P2")
        self.assertEqual(service.active_phase_selection, "P1_P2")
        self.assertEqual(service._dbusservice["/PhaseSelection"], "P1_P2")
        self.assertEqual(service._dbusservice["/PhaseSelectionActive"], "P1_P2")
        self.assertEqual(service._dbusservice["/SupportedPhaseSelections"], "P1,P1_P2")

    def test_handle_phase_selection_write_stages_live_change_when_backend_requires_pause(self) -> None:
        service = SimpleNamespace(
            virtual_mode=0,
            virtual_autostart=1,
            virtual_startstop=1,
            virtual_enable=1,
            requested_phase_selection="P1",
            active_phase_selection="P1",
            supported_phase_selections=("P1", "P1_P2"),
            _phase_switch_pending_selection=None,
            _phase_switch_state=None,
            _phase_switch_requested_at=None,
            _phase_switch_stable_until=None,
            _phase_switch_resume_relay=False,
            auto_start_condition_since=None,
            auto_stop_condition_since=None,
            manual_override_until=0.0,
            _auto_mode_cutover_pending=False,
            _ignore_min_offtime_once=False,
            _dbusservice={
                "/PhaseSelection": "P1",
                "/PhaseSelectionActive": "P1",
                "/SupportedPhaseSelections": "P1,P1_P2",
            },
            _time_now=MagicMock(return_value=100.0),
            _normalize_mode=self._normalize_mode,
            _mode_uses_auto_logic=self._mode_uses_auto_logic,
            _phase_selection_requires_pause=MagicMock(return_value=True),
            _apply_phase_selection=MagicMock(),
            _queue_relay_command=MagicMock(),
            _publish_local_pm_status=MagicMock(),
            _peek_pending_relay_command=MagicMock(return_value=(None, None)),
            _last_pm_status={"output": True},
            _last_pm_status_confirmed=True,
            _get_worker_snapshot=MagicMock(return_value={"pm_status": {"output": True}, "pm_confirmed": True}),
            _publish_dbus_path=MagicMock(),
            _state_summary=self._state_summary,
            _save_runtime_state=MagicMock(),
        )
        service._publish_dbus_path.side_effect = self._publish_side_effect(service)

        controller = DbusWriteController(WriteControllerPort(service))

        self.assertTrue(controller.handle_write("/PhaseSelection", "P1_P2"))
        service._queue_relay_command.assert_called_once_with(False, 100.0)
        service._publish_local_pm_status.assert_called_once_with(False, 100.0)
        service._apply_phase_selection.assert_not_called()
        self.assertEqual(service.requested_phase_selection, "P1_P2")
        self.assertEqual(service.active_phase_selection, "P1")
        self.assertEqual(service._phase_switch_pending_selection, "P1_P2")
        self.assertEqual(service._phase_switch_state, "waiting-relay-off")
        self.assertEqual(service._phase_switch_requested_at, 100.0)
        self.assertIsNone(service._phase_switch_stable_until)
        self.assertTrue(service._phase_switch_resume_relay)
        self.assertEqual(service._dbusservice["/PhaseSelection"], "P1_P2")
        self.assertEqual(service._dbusservice["/PhaseSelectionActive"], "P1")
        self.assertEqual(service._dbusservice["/SupportedPhaseSelections"], "P1,P1_P2")

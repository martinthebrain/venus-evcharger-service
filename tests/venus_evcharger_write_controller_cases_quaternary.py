# SPDX-License-Identifier: GPL-3.0-or-later
from tests.venus_evcharger_write_controller_support import *


class TestDbusWriteControllerQuaternary(DbusWriteControllerTestBase):
    def test_handle_phase_selection_write_rejects_locked_out_phase_from_effective_supported_set(self) -> None:
        service = SimpleNamespace(
            virtual_mode=0,
            virtual_autostart=1,
            virtual_startstop=0,
            virtual_enable=0,
            requested_phase_selection="P1",
            active_phase_selection="P1",
            supported_phase_selections=("P1", "P1_P2", "P1_P2_P3"),
            _phase_switch_lockout_selection="P1_P2_P3",
            _phase_switch_lockout_until=150.0,
            auto_start_condition_since=None,
            auto_stop_condition_since=None,
            manual_override_until=0.0,
            _auto_mode_cutover_pending=False,
            _ignore_min_offtime_once=False,
            _dbusservice={
                "/PhaseSelection": "P1",
                "/PhaseSelectionActive": "P1",
                "/SupportedPhaseSelections": "P1,P1_P2,P1_P2_P3",
            },
            time_now=MagicMock(return_value=100.0),
            _normalize_mode=self._normalize_mode,
            _mode_uses_auto_logic=self._mode_uses_auto_logic,
            _phase_selection_requires_pause=MagicMock(return_value=False),
            _apply_phase_selection=MagicMock(),
            _peek_pending_relay_command=MagicMock(return_value=(None, None)),
            _last_pm_status={"output": False},
            _last_pm_status_confirmed=True,
            _get_worker_snapshot=MagicMock(return_value={"pm_status": {"output": False}, "pm_confirmed": True}),
            _publish_dbus_field=MagicMock(),
            _state_summary=self._state_summary,
            _save_runtime_state=MagicMock(),
        )
        service._publish_dbus_field.side_effect = self._publish_field_side_effect(service)

        controller = write_controller(service)

        self.assertFalse(controller.handle_write("/PhaseSelection", "P1_P2_P3"))
        service._apply_phase_selection.assert_not_called()
        self.assertEqual(service._dbusservice["/SupportedPhaseSelections"], "P1,P1_P2,P1_P2_P3")

    def test_handle_phase_lockout_reset_write_clears_degradation_and_publishes_reset_state(self) -> None:
        service = SimpleNamespace(
            virtual_mode=1,
            virtual_autostart=1,
            virtual_startstop=0,
            virtual_enable=1,
            requested_phase_selection="P1",
            active_phase_selection="P1",
            supported_phase_selections=("P1", "P1_P2", "P1_P2_P3"),
            _phase_switch_mismatch_active=True,
            _phase_switch_mismatch_counts={"P1_P2_P3": 3},
            _phase_switch_last_mismatch_selection="P1_P2_P3",
            _phase_switch_last_mismatch_at=95.0,
            _phase_switch_lockout_selection="P1_P2_P3",
            _phase_switch_lockout_reason="mismatch-threshold",
            _phase_switch_lockout_at=96.0,
            _phase_switch_lockout_until=160.0,
            auto_start_condition_since=None,
            auto_stop_condition_since=None,
            manual_override_until=0.0,
            _auto_mode_cutover_pending=False,
            _ignore_min_offtime_once=False,
            _dbusservice={
                "/PhaseSelection": "P1",
                "/PhaseSelectionActive": "P1",
                "/SupportedPhaseSelections": "P1,P1_P2",
                "/Auto/PhaseLockoutActive": 1,
                "/Auto/PhaseLockoutTarget": "P1_P2_P3",
                "/Auto/PhaseLockoutReason": "mismatch-threshold",
                "/Auto/PhaseSupportedConfigured": "P1,P1_P2,P1_P2_P3",
                "/Auto/PhaseSupportedEffective": "P1,P1_P2",
                "/Auto/PhaseDegradedActive": 1,
                "/Auto/PhaseLockoutAge": 4,
                "/Auto/PhaseLockoutReset": 0,
            },
            time_now=MagicMock(return_value=100.0),
            _normalize_mode=self._normalize_mode,
            _mode_uses_auto_logic=self._mode_uses_auto_logic,
            _publish_dbus_field=MagicMock(),
            _state_summary=self._state_summary,
            _save_runtime_state=MagicMock(),
        )
        service._publish_dbus_field.side_effect = self._publish_field_side_effect(service)

        controller = write_controller(service)

        self.assertTrue(controller.handle_write("/Auto/PhaseLockoutReset", 1))
        self.assertFalse(service._phase_switch_mismatch_active)
        self.assertEqual(service._phase_switch_mismatch_counts, {})
        self.assertIsNone(service._phase_switch_lockout_selection)
        self.assertEqual(service._dbusservice["/SupportedPhaseSelections"], "P1,P1_P2,P1_P2_P3")
        self.assertEqual(service._dbusservice["/Auto/PhaseLockoutActive"], 0)
        self.assertEqual(service._dbusservice["/Auto/PhaseLockoutTarget"], "")
        self.assertEqual(service._dbusservice["/Auto/PhaseLockoutReason"], "")
        self.assertEqual(service._dbusservice["/Auto/PhaseSupportedEffective"], "P1,P1_P2,P1_P2_P3")
        self.assertEqual(service._dbusservice["/Auto/PhaseDegradedActive"], 0)
        self.assertEqual(service._dbusservice["/Auto/PhaseLockoutReset"], 0)

    def test_handle_contactor_lockout_reset_write_clears_latched_fault_state(self) -> None:
        service = SimpleNamespace(
            virtual_mode=1,
            virtual_autostart=1,
            virtual_startstop=0,
            virtual_enable=1,
            requested_phase_selection="P1",
            active_phase_selection="P1",
            supported_phase_selections=("P1", "P1_P2"),
            _contactor_fault_counts={"contactor-suspected-open": 3},
            _contactor_fault_active_reason="contactor-suspected-open",
            _contactor_fault_active_since=95.0,
            _contactor_lockout_reason="contactor-suspected-open",
            _contactor_lockout_source="count-threshold",
            _contactor_lockout_at=96.0,
            _contactor_suspected_open_since=94.0,
            _contactor_suspected_welded_since=None,
            auto_start_condition_since=None,
            auto_stop_condition_since=None,
            manual_override_until=0.0,
            _auto_mode_cutover_pending=False,
            _ignore_min_offtime_once=False,
            _dbusservice={
                "/Auto/ContactorFaultCount": 3,
                "/Auto/ContactorLockoutActive": 1,
                "/Auto/ContactorLockoutReason": "contactor-suspected-open",
                "/Auto/ContactorLockoutSource": "count-threshold",
                "/Auto/ContactorLockoutAge": 4,
                "/Auto/ContactorLockoutReset": 0,
            },
            time_now=MagicMock(return_value=100.0),
            _normalize_mode=self._normalize_mode,
            _mode_uses_auto_logic=self._mode_uses_auto_logic,
            _publish_dbus_field=MagicMock(),
            _state_summary=self._state_summary,
            _save_runtime_state=MagicMock(),
        )
        service._publish_dbus_field.side_effect = self._publish_field_side_effect(service)

        controller = write_controller(service)

        self.assertTrue(controller.handle_write("/Auto/ContactorLockoutReset", 1))
        self.assertEqual(service._contactor_fault_counts, {})
        self.assertIsNone(service._contactor_fault_active_reason)
        self.assertIsNone(service._contactor_fault_active_since)
        self.assertEqual(service._contactor_lockout_reason, "")
        self.assertEqual(service._contactor_lockout_source, "")
        self.assertIsNone(service._contactor_lockout_at)
        self.assertIsNone(service._contactor_suspected_open_since)
        self.assertEqual(service._dbusservice["/Auto/ContactorFaultCount"], 0)
        self.assertEqual(service._dbusservice["/Auto/ContactorLockoutActive"], 0)
        self.assertEqual(service._dbusservice["/Auto/ContactorLockoutReason"], "")
        self.assertEqual(service._dbusservice["/Auto/ContactorLockoutSource"], "")
        self.assertEqual(service._dbusservice["/Auto/ContactorLockoutAge"], -1)
        self.assertEqual(service._dbusservice["/Auto/ContactorLockoutReset"], 0)

    def test_reset_and_software_update_paths_ignore_zero_writes(self) -> None:
        service = SimpleNamespace(
            _dbusservice={
                "/Auto/PhaseLockoutReset": 1,
                "/Auto/ContactorLockoutReset": 1,
                "/Auto/SoftwareUpdateRun": 1,
            },
            time_now=MagicMock(return_value=100.0),
            _publish_dbus_field=MagicMock(),
            _state_summary=self._state_summary,
            _save_runtime_state=MagicMock(),
            _save_runtime_overrides=MagicMock(),
            _software_update_run_requested_at=None,
        )
        service._publish_dbus_field.side_effect = self._publish_field_side_effect(service)

        controller = write_controller(service)

        self.assertTrue(controller.handle_write("/Auto/PhaseLockoutReset", 0))
        self.assertTrue(controller.handle_write("/Auto/ContactorLockoutReset", 0))
        self.assertTrue(controller.handle_write("/Auto/SoftwareUpdateRun", 0))

        self.assertEqual(service._dbusservice["/Auto/PhaseLockoutReset"], 0)
        self.assertEqual(service._dbusservice["/Auto/ContactorLockoutReset"], 0)
        self.assertEqual(service._dbusservice["/Auto/SoftwareUpdateRun"], 0)
        self.assertIsNone(service._software_update_run_requested_at)

    def test_handle_current_setting_write_covers_set_min_and_max_paths(self) -> None:
        def build_service() -> SimpleNamespace:
            service = SimpleNamespace(
                virtual_set_current=8.0,
                min_current=6.0,
                max_current=16.0,
                _dbusservice={"/SetCurrent": 8.0, "/MinCurrent": 6.0, "/MaxCurrent": 16.0},
                time_now=MagicMock(return_value=42.0),
                _publish_dbus_field=MagicMock(),
                _save_runtime_state=MagicMock(),
                _save_runtime_overrides=MagicMock(),
                _state_summary=self._state_summary,
            )
            service._publish_dbus_field.side_effect = self._publish_field_side_effect(service)
            return service

        service = build_service()
        controller = write_controller(service)
        self.assertTrue(controller.handle_write("/SetCurrent", 12.5))
        self.assertEqual(service.virtual_set_current, 12.5)
        self.assertEqual(service._dbusservice["/SetCurrent"], 12.5)

        service = build_service()
        controller = write_controller(service)
        self.assertTrue(controller.handle_write("/MinCurrent", 7.0))
        self.assertEqual(service.min_current, 7.0)
        self.assertEqual(service._dbusservice["/MinCurrent"], 7.0)

        service = build_service()
        controller = write_controller(service)
        self.assertTrue(controller.handle_write("/MaxCurrent", 20.0))
        self.assertEqual(service.max_current, 20.0)
        self.assertEqual(service._dbusservice["/MaxCurrent"], 20.0)

    def test_handle_current_setting_write_uses_native_charger_current_when_available(self) -> None:
        backend = SimpleNamespace(set_current=MagicMock())
        service = SimpleNamespace(
            virtual_set_current=8.0,
            min_current=6.0,
            max_current=16.0,
            _charger_backend=backend,
            _dbusservice={"/SetCurrent": 8.0},
            time_now=MagicMock(return_value=42.0),
            _publish_dbus_field=MagicMock(),
            _save_runtime_state=MagicMock(),
            _save_runtime_overrides=MagicMock(),
            _state_summary=self._state_summary,
        )
        service._publish_dbus_field.side_effect = self._publish_field_side_effect(service)

        controller = write_controller(service)

        self.assertTrue(controller.handle_write("/SetCurrent", 10.0))

        backend.set_current.assert_called_once_with(10.0)
        self.assertEqual(service.virtual_set_current, 10.0)
        self.assertEqual(service._dbusservice["/SetCurrent"], 10.0)

    def test_auto_runtime_setting_write_covers_phase_tuning_paths(self) -> None:
        runtime_cases = (
            ("/Auto/PhaseSwitching", 0, 0),
            ("/Auto/PhasePreferLowestWhenIdle", 0, 0),
            ("/Auto/PhaseUpshiftDelaySeconds", 12.0, 12.0),
            ("/Auto/PhaseDownshiftDelaySeconds", 14.0, 14.0),
            ("/Auto/PhaseUpshiftHeadroomWatts", 900.0, 900.0),
            ("/Auto/PhaseDownshiftMarginWatts", 450.0, 450.0),
            ("/Auto/PhaseMismatchRetrySeconds", 30.0, 30.0),
            ("/Auto/PhaseMismatchLockoutCount", 4, 4),
            ("/Auto/PhaseMismatchLockoutSeconds", 600.0, 600.0),
        )

        for path, new_value, expected_dbus in runtime_cases:
            with self.subTest(path=path):
                service = SimpleNamespace(
                    virtual_mode=1,
                    virtual_autostart=1,
                    virtual_startstop=0,
                    virtual_enable=1,
                    _dbusservice={path: 0},
                    time_now=MagicMock(return_value=42.0),
                    _publish_dbus_field=MagicMock(),
                    _state_summary=self._state_summary,
                    _save_runtime_state=MagicMock(),
                    _save_runtime_overrides=MagicMock(),
                    _validate_runtime_config=MagicMock(),
                    auto_policy=AutoPolicy(),
                )
                service._publish_dbus_field.side_effect = self._publish_field_side_effect(service)
                controller = write_controller(service)

                self.assertTrue(controller.handle_write(path, new_value))

                self.assertEqual(
                    AUTO_POLICY_SETTING_BY_PATH[path].read(service.auto_policy),
                    expected_dbus,
                )
                self.assertEqual(service._dbusservice[path], expected_dbus)
                service._validate_runtime_config.assert_called_once()

    def test_auto_runtime_setting_write_covers_remaining_policy_and_delay_paths(self) -> None:
        runtime_cases = (
            ("/Auto/StartSurplusWatts", "auto_start_surplus_watts", 2000.0),
            ("/Auto/StopSurplusWatts", "auto_stop_surplus_watts", 1500.0),
            ("/Auto/MinSoc", "auto_min_soc", 45.0),
            ("/Auto/ResumeSoc", "auto_resume_soc", 55.0),
            ("/Auto/StartDelaySeconds", "auto_start_delay_seconds", 20.0),
            ("/Auto/StopDelaySeconds", "auto_stop_delay_seconds", 30.0),
            ("/Auto/DbusBackoffMaxSeconds", "auto_dbus_backoff_max_seconds", 12.0),
            ("/Auto/GridRecoveryStartSeconds", "auto_grid_recovery_start_seconds", 25.0),
            ("/Auto/StopSurplusDelaySeconds", "auto_stop_surplus_delay_seconds", 35.0),
            ("/Auto/StopSurplusVolatilityLowWatts", "auto_stop_surplus_volatility_low_watts", 120.0),
            ("/Auto/StopSurplusVolatilityHighWatts", "auto_stop_surplus_volatility_high_watts", 240.0),
            ("/Auto/ReferenceChargePowerWatts", "auto_reference_charge_power_watts", 2100.0),
            ("/Auto/LearnChargePowerMinWatts", "auto_learn_charge_power_min_watts", 700.0),
            ("/Auto/LearnChargePowerAlpha", "auto_learn_charge_power_alpha", 0.3),
            ("/Auto/LearnChargePowerStartDelaySeconds", "auto_learn_charge_power_start_delay_seconds", 40.0),
            ("/Auto/LearnChargePowerWindowSeconds", "auto_learn_charge_power_window_seconds", 120.0),
            ("/Auto/LearnChargePowerMaxAgeSeconds", "auto_learn_charge_power_max_age_seconds", 1800.0),
        )

        for path, attr, new_value in runtime_cases:
            with self.subTest(path=path):
                service = SimpleNamespace(
                    virtual_mode=1,
                    virtual_autostart=1,
                    virtual_startstop=0,
                    virtual_enable=1,
                    _dbusservice={path: 0},
                    time_now=MagicMock(return_value=42.0),
                    _publish_dbus_field=MagicMock(),
                    _state_summary=self._state_summary,
                    _save_runtime_state=MagicMock(),
                    _save_runtime_overrides=MagicMock(),
                    _validate_runtime_config=MagicMock(),
                    auto_policy=AutoPolicy(),
                    auto_start_delay_seconds=1.0,
                    auto_stop_delay_seconds=1.0,
                    auto_dbus_backoff_max_seconds=1.0,
                )
                service._publish_dbus_field.side_effect = self._publish_field_side_effect(service)
                controller = write_controller(service)

                self.assertTrue(controller.handle_write(path, new_value))

                policy_setting = AUTO_POLICY_SETTING_BY_PATH.get(path)
                if policy_setting is None:
                    self.assertEqual(getattr(service, attr), new_value)
                else:
                    self.assertEqual(policy_setting.read(service.auto_policy), new_value)
                    self.assertFalse(hasattr(service, attr))
                self.assertEqual(service._dbusservice[path], new_value)
                service._validate_runtime_config.assert_called_once()

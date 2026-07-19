# SPDX-License-Identifier: GPL-3.0-or-later
from tests.venus_evcharger_bootstrap_controller_path_cases_support import (
    MagicMock,
    ServiceBootstrapControllerTestCase,
    SimpleNamespace,
    _FakeDbusService,
    _backend_runtime_summary_fixture,
    _path_auto_policy,
    datetime,
    patch,
)


class TestServiceBootstrapPathRegistration(ServiceBootstrapControllerTestCase):
    def test_register_paths_uses_main_script_path_without_publishing_service_yet(self):
        service = SimpleNamespace(
            _dbusservice=_FakeDbusService(),
            connection_name="Shelly RPC",
            deviceinstance=60,
            product_name="Venus EV Charger Service",
            custom_name="Wallbox",
            firmware_version="1.0",
            hardware_version="Shelly 1PM Gen4",
            serial="ABC123",
            position=1,
            min_current=6.0,
            max_current=16.0,
            virtual_set_current=16.0,
            virtual_autostart=1,
            virtual_mode=0,
            virtual_startstop=1,
            virtual_enable=1,
            requested_phase_selection="P1",
            active_phase_selection="P1",
            supported_phase_selections=("P1",),
            auto_policy=_path_auto_policy(),
            auto_start_delay_seconds=10.0,
            auto_stop_delay_seconds=30.0,
            auto_month_windows={4: ((7, 30), (19, 30))},
            auto_scheduled_enabled_days="Mon,Tue,Wed,Thu,Fri",
            auto_scheduled_night_start_delay_seconds=3600.0,
            auto_scheduled_latest_end_time="06:30",
            auto_scheduled_night_current_amps=13.0,
            auto_dbus_backoff_base_seconds=5.0,
            auto_dbus_backoff_max_seconds=60.0,
            runtime_overrides_path="/run/wallbox-overrides.ini",
            _runtime_overrides_active=True,
            _backend_runtime_summary=_backend_runtime_summary_fixture(
                meter_type="shelly_meter",
                switch_type="template_switch",
                charger_type="template_charger",
            ),
            _last_health_reason="init",
            _last_health_code=0,
            _last_auto_state="idle",
            _last_auto_state_code=0,
            auto=SimpleNamespace(handle_dbus_write=MagicMock()),
        )

        controller = self._controller(service)
        controller.register_paths()

        self.assertEqual(service._dbusservice.paths["/Mgmt/ProcessName"]["value"], "/tmp/venus_evcharger_service.py")
        self.assertEqual(service._dbusservice.paths["/Mode"]["value"], 0)
        self.assertEqual(service._dbusservice.paths["/PhaseSelection"]["value"], "P1")
        self.assertEqual(service._dbusservice.paths["/Auto/StartSurplusWatts"]["value"], 1850.0)
        self.assertEqual(service._dbusservice.paths["/Auto/ScheduledEnabledDays"]["value"], "Mon,Tue,Wed,Thu,Fri")
        self.assertEqual(service._dbusservice.paths["/Auto/ScheduledFallbackDelaySeconds"]["value"], 3600.0)
        self.assertEqual(service._dbusservice.paths["/Auto/ScheduledLatestEndTime"]["value"], "06:30")
        self.assertEqual(service._dbusservice.paths["/Auto/ScheduledNightCurrent"]["value"], 13.0)
        self.assertEqual(service._dbusservice.paths["/Auto/DbusBackoffBaseSeconds"]["value"], 5.0)
        self.assertEqual(service._dbusservice.paths["/Auto/GridRecoveryStartSeconds"]["value"], 14.0)
        self.assertEqual(service._dbusservice.paths["/Auto/StopSurplusVolatilityLowWatts"]["value"], 80.0)
        self.assertEqual(service._dbusservice.paths["/Auto/ReferenceChargePowerWatts"]["value"], 2100.0)
        self.assertEqual(service._dbusservice.paths["/Auto/LearnChargePowerEnabled"]["value"], 1)
        self.assertEqual(service._dbusservice.paths["/Auto/LearnChargePowerWindowSeconds"]["value"], 180.0)
        self.assertEqual(service._dbusservice.paths["/Auto/PhaseSwitching"]["value"], 1)
        self.assertEqual(service._dbusservice.paths["/Auto/PhasePreferLowestWhenIdle"]["value"], 0)
        self.assertEqual(service._dbusservice.paths["/Auto/PhaseUpshiftDelaySeconds"]["value"], 120.0)
        self.assertEqual(service._dbusservice.paths["/Auto/PhaseMismatchLockoutCount"]["value"], 3)
        self.assertEqual(service._dbusservice.paths["/Auto/State"]["value"], "idle")
        self.assertEqual(service._dbusservice.paths["/Auto/StateCode"]["value"], 0)
        self.assertEqual(service._dbusservice.paths["/Auto/DecisionReason"]["value"], "init")
        self.assertEqual(service._dbusservice.paths["/Auto/DecisionState"]["value"], "idle")
        self.assertEqual(service._dbusservice.paths["/Auto/DecisionStateCode"]["value"], 0)
        self.assertEqual(service._dbusservice.paths["/Auto/DecisionRelayIntent"]["value"], -1)
        self.assertEqual(service._dbusservice.paths["/Auto/DecisionSurplusWatts"]["value"], -1.0)
        self.assertEqual(service._dbusservice.paths["/Auto/DecisionGridWatts"]["value"], -1.0)
        self.assertEqual(service._dbusservice.paths["/Auto/DecisionSocPercent"]["value"], -1.0)
        self.assertEqual(service._dbusservice.paths["/Auto/DecisionStartThresholdWatts"]["value"], -1.0)
        self.assertEqual(service._dbusservice.paths["/Auto/DecisionStopThresholdWatts"]["value"], -1.0)
        self.assertEqual(service._dbusservice.paths["/Auto/DecisionProfile"]["value"], "")
        self.assertEqual(service._dbusservice.paths["/Auto/DecisionThresholdMode"]["value"], "")
        self.assertEqual(service._dbusservice.paths["/Auto/ScheduledState"]["value"], "disabled")
        self.assertEqual(service._dbusservice.paths["/Auto/ScheduledStateCode"]["value"], 0)
        self.assertEqual(service._dbusservice.paths["/Auto/ScheduledReason"]["value"], "disabled")
        self.assertEqual(service._dbusservice.paths["/Auto/ScheduledReasonCode"]["value"], 0)
        self.assertEqual(service._dbusservice.paths["/Auto/ScheduledNightBoostActive"]["value"], 0)
        self.assertEqual(service._dbusservice.paths["/Auto/ScheduledTargetDayEnabled"]["value"], 0)
        self.assertEqual(service._dbusservice.paths["/Auto/ScheduledTargetDay"]["value"], "")
        self.assertEqual(service._dbusservice.paths["/Auto/ScheduledTargetDate"]["value"], "")
        self.assertEqual(service._dbusservice.paths["/Auto/ScheduledFallbackStart"]["value"], "")
        self.assertEqual(service._dbusservice.paths["/Auto/ScheduledBoostUntil"]["value"], "")
        self.assertEqual(service._dbusservice.paths["/Auto/RecoveryActive"]["value"], 0)
        self.assertEqual(service._dbusservice.paths["/Auto/StatusSource"]["value"], "unknown")
        self.assertEqual(service._dbusservice.paths["/Auto/FaultActive"]["value"], 0)
        self.assertEqual(service._dbusservice.paths["/Auto/FaultReason"]["value"], "")
        self.assertEqual(service._dbusservice.paths["/Auto/BackendMode"]["value"], "split")
        self.assertEqual(service._dbusservice.paths["/Auto/MeterBackend"]["value"], "shelly_meter")
        self.assertEqual(service._dbusservice.paths["/Auto/SwitchBackend"]["value"], "template_switch")
        self.assertEqual(service._dbusservice.paths["/Auto/ChargerBackend"]["value"], "template_charger")
        self.assertEqual(service._dbusservice.paths["/Auto/ChargerFaultActive"]["value"], 0)
        self.assertEqual(service._dbusservice.paths["/Auto/ChargerTransportActive"]["value"], 0)
        self.assertEqual(service._dbusservice.paths["/Auto/ChargerTransportReason"]["value"], "")
        self.assertEqual(service._dbusservice.paths["/Auto/ChargerTransportSource"]["value"], "")
        self.assertEqual(service._dbusservice.paths["/Auto/ChargerTransportDetail"]["value"], "")
        self.assertEqual(service._dbusservice.paths["/Auto/RuntimeOverridesActive"]["value"], 1)
        self.assertEqual(service._dbusservice.paths["/Auto/RuntimeOverridesPath"]["value"], "/run/wallbox-overrides.ini")
        self.assertEqual(service._dbusservice.paths["/Auto/ChargerRetryActive"]["value"], 0)
        self.assertEqual(service._dbusservice.paths["/Auto/ChargerRetryReason"]["value"], "")
        self.assertEqual(service._dbusservice.paths["/Auto/ChargerRetrySource"]["value"], "")
        self.assertEqual(service._dbusservice.paths["/Auto/ChargerCurrentTarget"]["value"], -1.0)
        self.assertEqual(service._dbusservice.paths["/Auto/PhaseCurrent"]["value"], "")
        self.assertEqual(service._dbusservice.paths["/Auto/PhaseObserved"]["value"], "")
        self.assertEqual(service._dbusservice.paths["/Auto/PhaseTarget"]["value"], "")
        self.assertEqual(service._dbusservice.paths["/Auto/PhaseReason"]["value"], "")
        self.assertEqual(service._dbusservice.paths["/Auto/PhaseMismatchActive"]["value"], 0)
        self.assertEqual(service._dbusservice.paths["/Auto/PhaseLockoutActive"]["value"], 0)
        self.assertEqual(service._dbusservice.paths["/Auto/PhaseLockoutTarget"]["value"], "")
        self.assertEqual(service._dbusservice.paths["/Auto/PhaseLockoutReason"]["value"], "")
        self.assertEqual(service._dbusservice.paths["/Auto/PhaseSupportedConfigured"]["value"], "P1")
        self.assertEqual(service._dbusservice.paths["/Auto/PhaseSupportedEffective"]["value"], "P1")
        self.assertEqual(service._dbusservice.paths["/Auto/PhaseDegradedActive"]["value"], 0)
        self.assertEqual(service._dbusservice.paths["/Auto/SwitchFeedbackClosed"]["value"], -1)
        self.assertEqual(service._dbusservice.paths["/Auto/SwitchInterlockOk"]["value"], -1)
        self.assertEqual(service._dbusservice.paths["/Auto/SwitchFeedbackMismatch"]["value"], 0)
        self.assertEqual(service._dbusservice.paths["/Auto/ContactorSuspectedOpen"]["value"], 0)
        self.assertEqual(service._dbusservice.paths["/Auto/ContactorSuspectedWelded"]["value"], 0)
        self.assertEqual(service._dbusservice.paths["/Auto/ContactorFaultCount"]["value"], 0)
        self.assertEqual(service._dbusservice.paths["/Auto/ContactorLockoutActive"]["value"], 0)
        self.assertEqual(service._dbusservice.paths["/Auto/ContactorLockoutReason"]["value"], "")
        self.assertEqual(service._dbusservice.paths["/Auto/ContactorLockoutSource"]["value"], "")
        self.assertEqual(service._dbusservice.paths["/Auto/ContactorLockoutReset"]["value"], 0)
        self.assertEqual(service._dbusservice.paths["/Auto/PhaseLockoutReset"]["value"], 0)
        self.assertEqual(service._dbusservice.paths["/Auto/PhaseThresholdWatts"]["value"], -1.0)
        self.assertEqual(service._dbusservice.paths["/Auto/PhaseCandidate"]["value"], "")
        self.assertEqual(service._dbusservice.paths["/Auto/PhaseCandidateAge"]["value"], -1)
        self.assertEqual(service._dbusservice.paths["/Auto/PhaseLockoutAge"]["value"], -1)

    def test_register_paths_initializes_scheduled_snapshot_when_mode_is_scheduled(self):
        service = SimpleNamespace(
            _dbusservice=_FakeDbusService(),
            connection_name="Shelly RPC",
            deviceinstance=60,
            product_name="Venus EV Charger Service",
            custom_name="Wallbox",
            firmware_version="1.0",
            hardware_version="Shelly 1PM Gen4",
            serial="ABC123",
            position=1,
            min_current=6.0,
            max_current=16.0,
            virtual_set_current=16.0,
            virtual_autostart=1,
            virtual_mode=2,
            virtual_startstop=1,
            virtual_enable=1,
            requested_phase_selection="P1",
            active_phase_selection="P1",
            supported_phase_selections=("P1",),
            auto_policy=_path_auto_policy(),
            auto_start_delay_seconds=10.0,
            auto_stop_delay_seconds=30.0,
            auto_month_windows={4: ((7, 30), (19, 30))},
            auto_scheduled_enabled_days="Mon,Tue,Wed,Thu,Fri",
            auto_scheduled_night_start_delay_seconds=3600.0,
            auto_scheduled_latest_end_time="06:30",
            auto_scheduled_night_current_amps=13.0,
            auto_dbus_backoff_base_seconds=5.0,
            auto_dbus_backoff_max_seconds=60.0,
            runtime_overrides_path="/run/wallbox-overrides.ini",
            _runtime_overrides_active=True,
            backend_mode="combined",
            meter_backend_type="shelly_combined",
            switch_backend_type="shelly_combined",
            charger_backend_type=None,
            _last_health_reason="init",
            _last_health_code=0,
            _last_auto_state="idle",
            _last_auto_state_code=0,
            auto=SimpleNamespace(handle_dbus_write=MagicMock()),
        )
        controller = self._controller(service)

        with patch("venus_evcharger.bootstrap.paths.time.time", return_value=datetime(2026, 4, 20, 21, 0).timestamp()):
            controller.register_paths()

        self.assertEqual(service._dbusservice.paths["/Auto/ScheduledState"]["value"], "night-boost")
        self.assertEqual(service._dbusservice.paths["/Auto/ScheduledReason"]["value"], "night-boost-window")
        self.assertEqual(service._dbusservice.paths["/Auto/ContactorLockoutAge"]["value"], -1)
        self.assertEqual(service._dbusservice.paths["/Auto/LastSwitchFeedbackAge"]["value"], -1)
        self.assertEqual(service._dbusservice.paths["/Auto/LastChargerTransportAge"]["value"], -1)
        self.assertEqual(service._dbusservice.paths["/Auto/ChargerRetryRemaining"]["value"], -1)
        self.assertTrue(service._dbusservice.paths["/Mode"]["writeable"])
        self.assertTrue(service._dbusservice.paths["/PhaseSelection"]["writeable"])
        self.assertTrue(service._dbusservice.paths["/Auto/StartSurplusWatts"]["writeable"])
        self.assertTrue(service._dbusservice.paths["/Auto/ScheduledEnabledDays"]["writeable"])
        self.assertTrue(service._dbusservice.paths["/Auto/ScheduledFallbackDelaySeconds"]["writeable"])
        self.assertTrue(service._dbusservice.paths["/Auto/ScheduledLatestEndTime"]["writeable"])
        self.assertTrue(service._dbusservice.paths["/Auto/ScheduledNightCurrent"]["writeable"])
        self.assertTrue(service._dbusservice.paths["/Auto/DbusBackoffBaseSeconds"]["writeable"])
        self.assertTrue(service._dbusservice.paths["/Auto/LearnChargePowerEnabled"]["writeable"])
        self.assertTrue(service._dbusservice.paths["/Auto/PhasePreferLowestWhenIdle"]["writeable"])
        self.assertTrue(service._dbusservice.paths["/Auto/PhaseSwitching"]["writeable"])
        self.assertTrue(service._dbusservice.paths["/Auto/PhaseMismatchLockoutCount"]["writeable"])
        self.assertTrue(service._dbusservice.paths["/Auto/PhaseLockoutReset"]["writeable"])
        self.assertTrue(service._dbusservice.paths["/Auto/ContactorLockoutReset"]["writeable"])
        self.assertFalse(service._dbusservice.register_called)

    def test_publish_dbus_service_registers_service_after_paths_exist(self):
        service = SimpleNamespace(_dbusservice=_FakeDbusService())

        controller = self._controller(service)
        controller.publish_dbus_service()

        self.assertTrue(service._dbusservice.register_called)

    def test_register_paths_marks_service_disconnected_when_host_is_not_configured(self):
        service = SimpleNamespace(
            _dbusservice=_FakeDbusService(),
            connection_name="Not configured",
            deviceinstance=60,
            product_name="Venus EV Charger Service",
            custom_name="Wallbox",
            firmware_version="1.0",
            hardware_version="Not configured",
            serial="unconfigured-60",
            position=1,
            min_current=6.0,
            max_current=16.0,
            virtual_set_current=16.0,
            virtual_autostart=1,
            virtual_mode=0,
            virtual_startstop=1,
            virtual_enable=1,
            requested_phase_selection="P1",
            active_phase_selection="P1",
            supported_phase_selections=("P1",),
            auto_policy=_path_auto_policy(),
            auto_start_delay_seconds=10.0,
            auto_stop_delay_seconds=30.0,
            auto_month_windows={},
            auto_scheduled_enabled_days="Mon,Tue,Wed,Thu,Fri",
            auto_scheduled_night_start_delay_seconds=3600.0,
            auto_scheduled_latest_end_time="06:30",
            auto_scheduled_night_current_amps=13.0,
            auto_dbus_backoff_base_seconds=5.0,
            auto_dbus_backoff_max_seconds=60.0,
            runtime_overrides_path="/run/wallbox-overrides.ini",
            _runtime_overrides_active=False,
            backend_mode="combined",
            meter_backend_type="shelly_combined",
            switch_backend_type="shelly_combined",
            charger_backend_type=None,
            topology_configured=False,
            host_configured=False,
            _last_health_reason="not-configured",
            _last_health_code=41,
            _last_auto_state="idle",
            _last_auto_state_code=0,
            auto=SimpleNamespace(handle_dbus_write=MagicMock()),
        )

        controller = self._controller(service)
        controller.register_paths()

        self.assertEqual(service._dbusservice.paths["/Connected"]["value"], 0)

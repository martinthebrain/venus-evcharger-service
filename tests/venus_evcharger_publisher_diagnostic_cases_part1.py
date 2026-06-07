# SPDX-License-Identifier: GPL-3.0-or-later
from tests.venus_evcharger_publisher_diagnostic_cases_support import *  # noqa: F401,F403

class _TestDbusPublishControllerDiagnosticsPart1:
    def test_diagnostic_values_include_backend_and_charger_visibility(self) -> None:
        current_time = 1776718800.0
        service = _with_backends_config(SimpleNamespace(
            _error_state={
                "dbus": 1,
                "shelly": 0,
                "charger": 2,
                "pv": 0,
                "battery": 0,
                "grid": 1,
                "cache_hits": 3,
            },
            last_status=2,
            virtual_mode=2,
            _last_health_reason="running",
            _last_health_code=5,
            _last_auto_state="charging",
            _last_auto_state_code=2,
            _last_status_source="charger-fault",
            auto_month_windows={4: ((7, 30), (19, 30))},
            auto_scheduled_enabled_days="Mon,Tue,Wed,Thu,Fri",
            auto_scheduled_night_start_delay_seconds=3600.0,
            auto_scheduled_latest_end_time="06:30",
            _charger_backend=SimpleNamespace(set_current=MagicMock()),
            _charger_target_current_amps=13.0,
            _charger_target_current_applied_at=current_time - 4.0,
            _last_charger_state_status="charging",
            _last_charger_state_fault="",
            _last_charger_fault_active=1,
            _last_charger_state_at=current_time - 3.0,
            _last_charger_estimate_source="current-voltage-phase",
            _last_charger_estimate_at=current_time - 1.0,
            _runtime_overrides_active=True,
            runtime_overrides_path="/run/wallbox-overrides.ini",
            _last_charger_transport_reason="offline",
            _last_charger_transport_source="read",
            _last_charger_transport_detail="Modbus slave 1 on /dev/ttyS7 did not respond",
            _last_charger_transport_at=current_time - 2.0,
            _charger_retry_reason="offline",
            _charger_retry_source="read",
            _charger_retry_until=current_time + 5.0,
            _shelly_state="offline",
            _shelly_last_error_reason="no-route",
            _shelly_consecutive_errors=3,
            _shelly_retry_after=current_time + 30.0,
            _shelly_last_ok_at=current_time - 11.0,
            _pending_relay_requested_at=current_time - 9.0,
            _last_confirmed_pm_status={"_phase_selection": "P1"},
            _last_switch_feedback_closed=False,
            _last_switch_interlock_ok=True,
            _last_switch_feedback_at=current_time - 4.0,
            _contactor_fault_counts={},
            _contactor_lockout_reason="",
            _contactor_lockout_source="",
            _contactor_lockout_at=None,
            _contactor_fault_active_reason=None,
            _phase_switch_mismatch_active=True,
            supported_phase_selections=("P1", "P1_P2_P3"),
            _phase_switch_lockout_selection="P1_P2",
            _phase_switch_lockout_reason="mismatch-threshold",
            _phase_switch_lockout_at=current_time - 9.0,
            _phase_switch_lockout_until=current_time + 50.0,
            _last_auto_metrics={
                "surplus": 1840.0,
                "grid": -120.0,
                "soc": 61.0,
                "profile": "normal",
                "start_threshold": 1850.0,
                "stop_threshold": 1350.0,
                "threshold_mode": "adaptive",
                "relay_intent": 1,
                "phase_current": "P1",
                "phase_target": "P1_P2",
                "phase_reason": "phase-upshift-pending",
                "phase_threshold_watts": 3010.0,
                "phase_candidate": "P1_P2",
            },
            _auto_phase_target_since=current_time - 8.0,
            _is_update_stale=self._never_stale,
            _recovery_attempts=4,
            _last_confirmed_pm_status_at=current_time - 5.0,
            _last_pm_status_at=current_time - 5.0,
            _last_pm_status_confirmed=True,
            _last_pv_at=current_time - 2.0,
            _last_battery_soc_at=current_time - 3.0,
            _last_grid_at=current_time - 6.0,
            _last_dbus_ok_at=current_time - 1.0,
            _last_successful_update_at=current_time - 7.0,
            _software_update_available=True,
            _software_update_state="available",
            _software_update_detail="manifest",
            _software_update_current_version="1.2.3",
            _software_update_available_version="1.2.4",
            _software_update_no_update_active=True,
            _software_update_last_check_at=current_time - 60.0,
            _software_update_last_run_at=current_time - 3600.0,
            started_at=current_time - 10.0,
        ), mode="split", meter_type="template_meter", switch_type="template_switch", charger_type="smartevse_charger")
        controller = DbusPublishController(service, self._real_age_seconds)

        counter_values = controller._diagnostic_counter_values(current_time)
        age_values = controller._diagnostic_age_values(current_time)

        self.assertEqual(counter_values["/Auto/ScheduledState"], "night-boost")
        self.assertEqual(counter_values["/Auto/ScheduledStateCode"], 4)
        self.assertEqual(counter_values["/Auto/ScheduledReason"], "night-boost-window")
        self.assertEqual(counter_values["/Auto/ScheduledReasonCode"], 4)
        self.assertEqual(counter_values["/Auto/ScheduledNightBoostActive"], 1)
        self.assertEqual(counter_values["/Auto/ScheduledTargetDayEnabled"], 1)
        self.assertEqual(counter_values["/Auto/ScheduledTargetDay"], "Tue")
        self.assertEqual(counter_values["/Auto/ScheduledTargetDate"], "2026-04-21")
        self.assertEqual(counter_values["/Auto/ScheduledFallbackStart"], "2026-04-20 20:30")
        self.assertEqual(counter_values["/Auto/ScheduledBoostUntil"], "2026-04-21 06:30")
        self.assertEqual(counter_values["/Auto/BackendMode"], "split")
        self.assertEqual(counter_values["/Auto/MeterBackend"], "template_meter")
        self.assertEqual(counter_values["/Auto/SwitchBackend"], "template_switch")
        self.assertEqual(counter_values["/Auto/ChargerBackend"], "smartevse_charger")
        self.assertEqual(counter_values["/Auto/RecoveryActive"], 0)
        self.assertEqual(counter_values["/Auto/StatusSource"], "charger-fault")
        self.assertEqual(counter_values["/Auto/FaultActive"], 0)
        self.assertEqual(counter_values["/Auto/FaultReason"], "")
        self.assertEqual(counter_values["/Auto/DecisionReason"], "running")
        self.assertEqual(counter_values["/Auto/DecisionState"], "charging")
        self.assertEqual(counter_values["/Auto/DecisionStateCode"], 3)
        self.assertEqual(counter_values["/Auto/DecisionRelayIntent"], 1)
        self.assertEqual(counter_values["/Auto/DecisionSurplusWatts"], 1840.0)
        self.assertEqual(counter_values["/Auto/DecisionGridWatts"], -120.0)
        self.assertEqual(counter_values["/Auto/DecisionSocPercent"], 61.0)
        self.assertEqual(counter_values["/Auto/DecisionStartThresholdWatts"], 1850.0)
        self.assertEqual(counter_values["/Auto/DecisionStopThresholdWatts"], 1350.0)
        self.assertEqual(counter_values["/Auto/DecisionProfile"], "normal")
        self.assertEqual(counter_values["/Auto/DecisionThresholdMode"], "adaptive")
        self.assertEqual(counter_values["/Auto/ChargerStatus"], "charging")
        self.assertEqual(counter_values["/Auto/ChargerFault"], "")
        self.assertEqual(counter_values["/Auto/ChargerFaultActive"], 1)
        self.assertEqual(counter_values["/Auto/ChargerEstimateActive"], 1)
        self.assertEqual(counter_values["/Auto/ChargerEstimateSource"], "current-voltage-phase")
        self.assertEqual(counter_values["/Auto/RuntimeOverridesActive"], 1)
        self.assertEqual(counter_values["/Auto/RuntimeOverridesPath"], "/run/wallbox-overrides.ini")
        self.assertEqual(counter_values["/Auto/SoftwareUpdateAvailable"], 1)
        self.assertEqual(counter_values["/Auto/SoftwareUpdateState"], "available-blocked")
        self.assertEqual(counter_values["/Auto/SoftwareUpdateStateCode"], 4)
        self.assertEqual(counter_values["/Auto/SoftwareUpdateDetail"], "manifest")
        self.assertEqual(counter_values["/Auto/SoftwareUpdateCurrentVersion"], "1.2.3")
        self.assertEqual(counter_values["/Auto/SoftwareUpdateAvailableVersion"], "1.2.4")
        self.assertEqual(counter_values["/Auto/SoftwareUpdateNoUpdateActive"], 1)
        self.assertEqual(counter_values["/Auto/ChargerTransportActive"], 1)
        self.assertEqual(counter_values["/Auto/ChargerTransportReason"], "offline")
        self.assertEqual(counter_values["/Auto/ChargerTransportSource"], "read")
        self.assertEqual(
            counter_values["/Auto/ChargerTransportDetail"],
            "Modbus slave 1 on /dev/ttyS7 did not respond",
        )
        self.assertEqual(counter_values["/Auto/ChargerRetryActive"], 1)
        self.assertEqual(counter_values["/Auto/ChargerRetryReason"], "offline")
        self.assertEqual(counter_values["/Auto/ChargerRetrySource"], "read")
        self.assertEqual(counter_values["/Auto/ChargerWriteErrors"], 2)
        self.assertEqual(counter_values["/Auto/ErrorCount"], 4)
        self.assertEqual(counter_values["/Auto/ChargerCurrentTarget"], 13.0)
        self.assertEqual(counter_values["/Auto/ShellyState"], "offline")
        self.assertEqual(counter_values["/Auto/ShellyLastError"], "no-route")
        self.assertEqual(counter_values["/Auto/ShellyRetryRemaining"], 30)
        self.assertEqual(counter_values["/Auto/ShellyConsecutiveErrors"], 3)
        self.assertEqual(counter_values["/Auto/PhaseCurrent"], "P1")
        self.assertEqual(counter_values["/Auto/PhaseObserved"], "P1")
        self.assertEqual(counter_values["/Auto/PhaseTarget"], "P1_P2")
        self.assertEqual(counter_values["/Auto/PhaseReason"], "phase-upshift-pending")
        self.assertEqual(counter_values["/Auto/PhaseMismatchActive"], 1)
        self.assertEqual(counter_values["/Auto/PhaseLockoutActive"], 1)
        self.assertEqual(counter_values["/Auto/PhaseLockoutTarget"], "P1_P2")
        self.assertEqual(counter_values["/Auto/PhaseLockoutReason"], "mismatch-threshold")
        self.assertEqual(counter_values["/Auto/PhaseSupportedConfigured"], "P1,P1_P2_P3")
        self.assertEqual(counter_values["/Auto/PhaseSupportedEffective"], "P1")
        self.assertEqual(counter_values["/Auto/PhaseDegradedActive"], 1)
        self.assertEqual(counter_values["/Auto/SwitchFeedbackClosed"], 0)
        self.assertEqual(counter_values["/Auto/SwitchInterlockOk"], 1)
        self.assertEqual(counter_values["/Auto/SwitchFeedbackMismatch"], 0)
        self.assertEqual(counter_values["/Auto/ContactorSuspectedOpen"], 0)
        self.assertEqual(counter_values["/Auto/ContactorSuspectedWelded"], 0)
        self.assertEqual(counter_values["/Auto/ContactorFaultCount"], 0)
        self.assertEqual(counter_values["/Auto/ContactorLockoutActive"], 0)
        self.assertEqual(counter_values["/Auto/ContactorLockoutReason"], "")
        self.assertEqual(counter_values["/Auto/ContactorLockoutSource"], "")
        self.assertEqual(counter_values["/Auto/PhaseThresholdWatts"], 3010.0)
        self.assertEqual(counter_values["/Auto/PhaseCandidate"], "P1_P2")
        self.assertEqual(age_values["/Auto/ChargerCurrentTargetAge"], 4.0)
        self.assertEqual(age_values["/Auto/PhaseCandidateAge"], 8.0)
        self.assertEqual(age_values["/Auto/PhaseLockoutAge"], 9.0)
        self.assertEqual(age_values["/Auto/ContactorLockoutAge"], -1.0)
        self.assertEqual(age_values["/Auto/LastSwitchFeedbackAge"], 4.0)
        self.assertEqual(age_values["/Auto/LastChargerReadAge"], 3.0)
        self.assertEqual(age_values["/Auto/LastChargerEstimateAge"], 1.0)
        self.assertEqual(age_values["/Auto/LastChargerTransportAge"], 2.0)
        self.assertEqual(age_values["/Auto/ChargerRetryRemaining"], 5.0)
        self.assertEqual(age_values["/Auto/ShellyLastOkAge"], 11.0)
        self.assertEqual(age_values["/Auto/PendingRelayAge"], 9.0)
        self.assertEqual(age_values["/Auto/SoftwareUpdateLastCheckAge"], 60.0)
        self.assertEqual(age_values["/Auto/SoftwareUpdateLastRunAge"], 3600.0)

        service._last_health_reason = "contactor-suspected-welded"
        welded_counter_values = controller._diagnostic_counter_values(current_time)
        self.assertEqual(welded_counter_values["/Auto/ContactorSuspectedOpen"], 0)
        self.assertEqual(welded_counter_values["/Auto/ContactorSuspectedWelded"], 1)

        service._last_health_reason = "contactor-suspected-open"
        open_counter_values = controller._diagnostic_counter_values(current_time)
        self.assertEqual(open_counter_values["/Auto/ContactorSuspectedOpen"], 1)
        self.assertEqual(open_counter_values["/Auto/ContactorSuspectedWelded"], 0)

        service._last_health_reason = "contactor-lockout-open"
        service._last_auto_state = "recovery"
        service._last_auto_state_code = 5
        service._contactor_fault_counts = {"contactor-suspected-open": 3}
        service._contactor_lockout_reason = "contactor-suspected-open"
        service._contactor_lockout_source = "count-threshold"
        service._contactor_lockout_at = current_time - 6.0
        lockout_counter_values = controller._diagnostic_counter_values(current_time)
        lockout_age_values = controller._diagnostic_age_values(current_time)
        self.assertEqual(lockout_counter_values["/Auto/RecoveryActive"], 1)
        self.assertEqual(lockout_counter_values["/Auto/FaultActive"], 1)
        self.assertEqual(lockout_counter_values["/Auto/FaultReason"], "contactor-lockout-open")
        self.assertEqual(lockout_counter_values["/Auto/ContactorFaultCount"], 3)
        self.assertEqual(lockout_counter_values["/Auto/ContactorLockoutActive"], 1)
        self.assertEqual(lockout_counter_values["/Auto/ContactorLockoutReason"], "contactor-suspected-open")
        self.assertEqual(lockout_counter_values["/Auto/ContactorLockoutSource"], "count-threshold")
        self.assertEqual(lockout_age_values["/Auto/ContactorLockoutAge"], 6.0)

    def test_diagnostic_values_prefer_resolved_backend_selection_over_legacy_attrs(self) -> None:
        current_time = 1776718800.0
        service = SimpleNamespace(
            _dbusservice={},
            _dbus_publish_state={},
            _dbus_live_publish_interval_seconds=1.0,
            _dbus_slow_publish_interval_seconds=5.0,
            _error_state={"dbus": 0, "shelly": 0, "charger": 0, "pv": 0, "battery": 0, "grid": 0, "cache_hits": 0},
            last_status=0,
            virtual_mode=0,
            _last_health_reason="init",
            _last_health_code=0,
            _last_auto_state="idle",
            _last_auto_state_code=0,
            _last_status_source="unknown",
            auto_month_windows={},
            auto_scheduled_enabled_days="Mon,Tue,Wed,Thu,Fri,Sat,Sun",
            auto_scheduled_night_start_delay_seconds=3600.0,
            auto_scheduled_latest_end_time="06:30",
            backend_mode="combined",
            meter_backend_type="shelly_combined",
            switch_backend_type="shelly_combined",
            charger_backend_type=None,
            _backend_bundle=SimpleNamespace(
                runtime=SimpleNamespace(
                    backend_mode="split",
                    meter_type="template_meter",
                    switch_type="switch_group",
                    charger_type="smartevse_charger",
                    meter_config_path=None,
                    switch_config_path=None,
                    charger_config_path=None,
                )
            ),
            _is_update_stale=self._never_stale,
            _recovery_attempts=0,
            started_at=current_time - 10.0,
        )

        counter_values = DbusPublishController(service, self._real_age_seconds)._diagnostic_counter_values(current_time)

        self.assertEqual(counter_values["/Auto/BackendMode"], "split")
        self.assertEqual(counter_values["/Auto/MeterBackend"], "template_meter")
        self.assertEqual(counter_values["/Auto/SwitchBackend"], "switch_group")
        self.assertEqual(counter_values["/Auto/ChargerBackend"], "smartevse_charger")

    def test_software_update_age_values_are_negative_one_before_any_check_or_run(self) -> None:
        service = SimpleNamespace(
            _dbusservice={},
            _dbus_publish_state={},
            _dbus_live_publish_interval_seconds=1.0,
            _dbus_slow_publish_interval_seconds=5.0,
            _error_state={"dbus": 0, "shelly": 0, "charger": 0, "pv": 0, "battery": 0, "grid": 0, "cache_hits": 0},
            last_status=0,
            virtual_mode=1,
            _last_health_reason="init",
            _last_health_code=0,
            _last_auto_state="idle",
            _last_auto_state_code=0,
            _last_status_source="unknown",
            auto_month_windows={},
            auto_scheduled_enabled_days="Mon,Tue,Wed,Thu,Fri",
            auto_scheduled_night_start_delay_seconds=3600.0,
            auto_scheduled_latest_end_time="06:30",
            _last_confirmed_pm_status_at=None,
            _last_pm_status_at=None,
            _last_pm_status_confirmed=False,
            _last_pv_at=None,
            _last_battery_soc_at=None,
            _last_grid_at=None,
            _last_dbus_ok_at=None,
            _software_update_last_check_at=None,
            _software_update_last_run_at=None,
            _is_update_stale=self._never_stale,
            _last_successful_update_at=90.0,
            started_at=90.0,
        )
        controller = DbusPublishController(service, self._real_age_seconds)

        age_values = controller._diagnostic_age_values(100.0)

        self.assertEqual(age_values["/Auto/SoftwareUpdateLastCheckAge"], -1.0)
        self.assertEqual(age_values["/Auto/SoftwareUpdateLastRunAge"], -1.0)


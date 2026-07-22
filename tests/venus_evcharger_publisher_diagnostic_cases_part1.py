# SPDX-License-Identifier: GPL-3.0-or-later
from venus_evcharger.backend.models import BackendRuntimeSummary

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
            _source_retry_after={"shelly": current_time + 30.0},
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
            _runtime_update_is_stale=self._never_stale,
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
        controller = build_publish_controller(service, self._real_age_seconds)

        counter_values = controller.diagnostics.counter_values(current_time)
        age_values = controller.diagnostics.age_values(current_time)

        self.assertEqual(counter_values["auto_scheduled_state"], "night-boost")
        self.assertEqual(counter_values["auto_scheduled_state_code"], 4)
        self.assertEqual(counter_values["auto_scheduled_reason"], "night-boost-window")
        self.assertEqual(counter_values["auto_scheduled_reason_code"], 4)
        self.assertEqual(counter_values["auto_scheduled_night_boost_active"], 1)
        self.assertEqual(counter_values["auto_scheduled_target_day_enabled"], 1)
        self.assertEqual(counter_values["auto_scheduled_target_day"], "Tue")
        self.assertEqual(counter_values["auto_scheduled_target_date"], "2026-04-21")
        self.assertEqual(counter_values["auto_scheduled_fallback_start"], "2026-04-20 20:30")
        self.assertEqual(counter_values["auto_scheduled_boost_until"], "2026-04-21 06:30")
        self.assertEqual(counter_values["auto_backend_mode"], "split")
        self.assertEqual(counter_values["auto_meter_backend"], "template_meter")
        self.assertEqual(counter_values["auto_switch_backend"], "template_switch")
        self.assertEqual(counter_values["auto_charger_backend"], "smartevse_charger")
        self.assertEqual(counter_values["auto_recovery_active"], 0)
        self.assertEqual(counter_values["auto_status_source"], "charger-fault")
        self.assertEqual(counter_values["auto_fault_active"], 0)
        self.assertEqual(counter_values["auto_fault_reason"], "")
        self.assertEqual(counter_values["auto_decision_reason"], "running")
        self.assertEqual(counter_values["auto_decision_state"], "charging")
        self.assertEqual(counter_values["auto_decision_state_code"], 3)
        self.assertEqual(counter_values["auto_decision_relay_intent"], 1)
        self.assertEqual(counter_values["auto_decision_surplus_watts"], 1840.0)
        self.assertEqual(counter_values["auto_decision_grid_watts"], -120.0)
        self.assertEqual(counter_values["auto_decision_soc_percent"], 61.0)
        self.assertEqual(counter_values["auto_decision_start_threshold_watts"], 1850.0)
        self.assertEqual(counter_values["auto_decision_stop_threshold_watts"], 1350.0)
        self.assertEqual(counter_values["auto_decision_profile"], "normal")
        self.assertEqual(counter_values["auto_decision_threshold_mode"], "adaptive")
        self.assertEqual(counter_values["auto_charger_status"], "charging")
        self.assertEqual(counter_values["auto_charger_fault"], "")
        self.assertEqual(counter_values["auto_charger_fault_active"], 1)
        self.assertEqual(counter_values["auto_charger_estimate_active"], 1)
        self.assertEqual(counter_values["auto_charger_estimate_source"], "current-voltage-phase")
        self.assertEqual(counter_values["auto_runtime_overrides_active"], 1)
        self.assertEqual(counter_values["auto_runtime_overrides_path"], "/run/wallbox-overrides.ini")
        self.assertEqual(counter_values["auto_software_update_available"], 1)
        self.assertEqual(counter_values["auto_software_update_state"], "available-blocked")
        self.assertEqual(counter_values["auto_software_update_state_code"], 4)
        self.assertEqual(counter_values["auto_software_update_detail"], "manifest")
        self.assertEqual(counter_values["auto_software_update_current_version"], "1.2.3")
        self.assertEqual(counter_values["auto_software_update_available_version"], "1.2.4")
        self.assertEqual(counter_values["auto_software_update_no_update_active"], 1)
        self.assertEqual(counter_values["auto_charger_transport_active"], 1)
        self.assertEqual(counter_values["auto_charger_transport_reason"], "offline")
        self.assertEqual(counter_values["auto_charger_transport_source"], "read")
        self.assertEqual(
            counter_values["auto_charger_transport_detail"],
            "Modbus slave 1 on /dev/ttyS7 did not respond",
        )
        self.assertEqual(counter_values["auto_charger_retry_active"], 1)
        self.assertEqual(counter_values["auto_charger_retry_reason"], "offline")
        self.assertEqual(counter_values["auto_charger_retry_source"], "read")
        self.assertEqual(counter_values["auto_charger_write_errors"], 2)
        self.assertEqual(counter_values["auto_error_count"], 4)
        self.assertEqual(counter_values["auto_charger_current_target"], 13.0)
        self.assertEqual(counter_values["auto_shelly_state"], "offline")
        self.assertEqual(counter_values["auto_shelly_last_error"], "no-route")
        self.assertEqual(counter_values["auto_shelly_retry_remaining"], 30)
        self.assertEqual(counter_values["auto_shelly_consecutive_errors"], 3)
        self.assertEqual(counter_values["auto_phase_current"], "P1")
        self.assertEqual(counter_values["auto_phase_observed"], "P1")
        self.assertEqual(counter_values["auto_phase_target"], "P1_P2")
        self.assertEqual(counter_values["auto_phase_reason"], "phase-upshift-pending")
        self.assertEqual(counter_values["auto_phase_mismatch_active"], 1)
        self.assertEqual(counter_values["auto_phase_lockout_active"], 1)
        self.assertEqual(counter_values["auto_phase_lockout_target"], "P1_P2")
        self.assertEqual(counter_values["auto_phase_lockout_reason"], "mismatch-threshold")
        self.assertEqual(counter_values["auto_phase_supported_configured"], "P1,P1_P2_P3")
        self.assertEqual(counter_values["auto_phase_supported_effective"], "P1")
        self.assertEqual(counter_values["auto_phase_degraded_active"], 1)
        self.assertEqual(counter_values["auto_switch_feedback_closed"], 0)
        self.assertEqual(counter_values["auto_switch_interlock_ok"], 1)
        self.assertEqual(counter_values["auto_switch_feedback_mismatch"], 0)
        self.assertEqual(counter_values["auto_contactor_suspected_open"], 0)
        self.assertEqual(counter_values["auto_contactor_suspected_welded"], 0)
        self.assertEqual(counter_values["auto_contactor_fault_count"], 0)
        self.assertEqual(counter_values["auto_contactor_lockout_active"], 0)
        self.assertEqual(counter_values["auto_contactor_lockout_reason"], "")
        self.assertEqual(counter_values["auto_contactor_lockout_source"], "")
        self.assertEqual(counter_values["auto_phase_threshold_watts"], 3010.0)
        self.assertEqual(counter_values["auto_phase_candidate"], "P1_P2")
        self.assertEqual(age_values["auto_charger_current_target_age"], 4.0)
        self.assertEqual(age_values["auto_phase_candidate_age"], 8.0)
        self.assertEqual(age_values["auto_phase_lockout_age"], 9.0)
        self.assertEqual(age_values["auto_contactor_lockout_age"], -1.0)
        self.assertEqual(age_values["auto_last_switch_feedback_age"], 4.0)
        self.assertEqual(age_values["auto_last_charger_read_age"], 3.0)
        self.assertEqual(age_values["auto_last_charger_estimate_age"], 1.0)
        self.assertEqual(age_values["auto_last_charger_transport_age"], 2.0)
        self.assertEqual(age_values["auto_charger_retry_remaining"], 5.0)
        self.assertEqual(age_values["auto_shelly_last_ok_age"], 11.0)
        self.assertEqual(age_values["auto_pending_relay_age"], 9.0)
        self.assertEqual(age_values["auto_software_update_last_check_age"], 60.0)
        self.assertEqual(age_values["auto_software_update_last_run_age"], 3600.0)

        service._last_health_reason = "contactor-suspected-welded"
        welded_counter_values = controller.diagnostics.counter_values(current_time)
        self.assertEqual(welded_counter_values["auto_contactor_suspected_open"], 0)
        self.assertEqual(welded_counter_values["auto_contactor_suspected_welded"], 1)

        service._last_health_reason = "contactor-suspected-open"
        open_counter_values = controller.diagnostics.counter_values(current_time)
        self.assertEqual(open_counter_values["auto_contactor_suspected_open"], 1)
        self.assertEqual(open_counter_values["auto_contactor_suspected_welded"], 0)

        service._last_health_reason = "contactor-lockout-open"
        service._last_auto_state = "recovery"
        service._last_auto_state_code = 5
        service._contactor_fault_counts = {"contactor-suspected-open": 3}
        service._contactor_lockout_reason = "contactor-suspected-open"
        service._contactor_lockout_source = "count-threshold"
        service._contactor_lockout_at = current_time - 6.0
        lockout_counter_values = controller.diagnostics.counter_values(current_time)
        lockout_age_values = controller.diagnostics.age_values(current_time)
        self.assertEqual(lockout_counter_values["auto_recovery_active"], 1)
        self.assertEqual(lockout_counter_values["auto_fault_active"], 1)
        self.assertEqual(lockout_counter_values["auto_fault_reason"], "contactor-lockout-open")
        self.assertEqual(lockout_counter_values["auto_contactor_fault_count"], 3)
        self.assertEqual(lockout_counter_values["auto_contactor_lockout_active"], 1)
        self.assertEqual(lockout_counter_values["auto_contactor_lockout_reason"], "contactor-suspected-open")
        self.assertEqual(lockout_counter_values["auto_contactor_lockout_source"], "count-threshold")
        self.assertEqual(lockout_age_values["auto_contactor_lockout_age"], 6.0)

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
                runtime=BackendRuntimeSummary(
                    backend_mode="split",
                    meter_type="template_meter",
                    switch_type="switch_group",
                    charger_type="smartevse_charger",
                    meter_config_path=None,
                    switch_config_path=None,
                    charger_config_path=None,
                    topology_configured=True,
                    primary_rpc_configured=False,
                )
            ),
            _runtime_update_is_stale=self._never_stale,
            _recovery_attempts=0,
            started_at=current_time - 10.0,
        )

        counter_values = build_publish_controller(service, self._real_age_seconds).diagnostics.counter_values(current_time)

        self.assertEqual(counter_values["auto_backend_mode"], "split")
        self.assertEqual(counter_values["auto_meter_backend"], "template_meter")
        self.assertEqual(counter_values["auto_switch_backend"], "switch_group")
        self.assertEqual(counter_values["auto_charger_backend"], "smartevse_charger")

        service._error_state = "bad"
        counter_values = build_publish_controller(service, self._real_age_seconds).diagnostics.counter_values(current_time)
        self.assertEqual(counter_values["auto_error_count"], 0)
        self.assertEqual(counter_values["auto_dbus_read_errors"], 0)

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
            _runtime_update_is_stale=self._never_stale,
            _last_successful_update_at=90.0,
            started_at=90.0,
        )
        controller = build_publish_controller(service, self._real_age_seconds)

        age_values = controller.diagnostics.age_values(100.0)

        self.assertEqual(age_values["auto_software_update_last_check_age"], -1.0)
        self.assertEqual(age_values["auto_software_update_last_run_age"], -1.0)

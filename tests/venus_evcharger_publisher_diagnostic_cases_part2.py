# SPDX-License-Identifier: GPL-3.0-or-later
from tests.venus_evcharger_publisher_diagnostic_cases_support import *  # noqa: F401,F403
from unittest.mock import patch

from venus_evcharger.publish.dbus_diagnostics_introspection import _introspection_finding_unusable


def _diagnostic_contract_service(test_case, current_time: float):
    return _with_backends_config(SimpleNamespace(
        _error_state={"dbus": 1, "shelly": 2, "charger": 3, "pv": 4, "battery": 5, "grid": 6, "cache_hits": 7},
        last_status=2,
        virtual_mode=2,
        _last_health_reason="running",
        _last_health_code=9,
        _last_auto_state="running",
        _last_auto_state_code=2,
        _last_status_source="auto-policy",
        auto_month_windows={4: ((7, 30), (19, 30))},
        auto_scheduled_enabled_days="Mon,Tue,Wed,Thu,Fri",
        auto_scheduled_night_start_delay_seconds=3600.0,
        auto_scheduled_latest_end_time="06:30",
        _runtime_overrides_active=True,
        runtime_overrides_path="/run/override.json",
        _last_charger_state_status="charging",
        _last_charger_state_fault="",
        _last_charger_fault_active=0,
        _last_charger_state_at=current_time - 17.0,
        _last_charger_estimate_source="meterless",
        _last_charger_estimate_at=current_time - 18.0,
        _last_charger_transport_reason="offline",
        _last_charger_transport_source="read",
        _last_charger_transport_detail="timeout",
        _last_charger_transport_at=current_time - 1.0,
        auto_dbus_backoff_max_seconds=30.0,
        _charger_retry_reason="offline",
        _charger_retry_source="write",
        _charger_retry_until=current_time + 20.0,
        _last_confirmed_pm_status={"_phase_selection": "P1_P2", "output": True},
        _phase_switch_mismatch_active=True,
        supported_phase_selections=("P1", "P1_P2"),
        _phase_switch_lockout_selection="P1_P2",
        _phase_switch_lockout_reason="mismatch-threshold",
        _phase_switch_lockout_at=current_time - 21.0,
        _phase_switch_lockout_until=current_time + 50.0,
        _last_switch_feedback_closed=True,
        _last_switch_interlock_ok=False,
        _last_switch_feedback_at=current_time - 22.0,
        _contactor_fault_counts={"contactor-suspected-open": 8, "contactor-suspected-welded": 9},
        _contactor_lockout_reason="contactor-suspected-open",
        _contactor_lockout_source="count-threshold",
        _contactor_lockout_at=current_time - 23.0,
        _is_update_stale=test_case._never_stale,
        _recovery_attempts=4,
        _last_confirmed_pm_status_at=current_time - 10.0,
        _last_pm_status_at=current_time - 11.0,
        _last_pm_status_confirmed=True,
        _last_pv_at=current_time - 12.0,
        _last_battery_soc_at=current_time - 13.0,
        _last_grid_at=current_time - 14.0,
        _last_dbus_ok_at=current_time - 15.0,
        _charger_target_current_applied_at=current_time - 16.0,
        _auto_phase_target_since=current_time - 24.0,
        _last_successful_update_at=current_time - 25.0,
        started_at=current_time - 100.0,
        _software_update_state="available",
        _software_update_available=True,
        _software_update_no_update_active=False,
        _software_update_detail="ready",
        _software_update_current_version="1.0.0",
        _software_update_available_version="1.1.0",
        _software_update_last_check_at=current_time - 26.0,
        _software_update_last_run_at=current_time - 27.0,
        _last_update_cycle_duration_seconds=0.31,
        _update_worker_pending=True,
        _update_worker_skipped_count=5,
        _last_publish_flush_duration_seconds=0.41,
        _last_dbus_publish_queue_lag_seconds=0.51,
        _dbus_publish_dropped_count=6,
        _last_write_command_duration_seconds=0.61,
        _last_write_command_queue_lag_seconds=0.71,
        _mainloop_heartbeat_at=current_time - 28.0,
    ), mode="split", meter_type="template_meter", switch_type="switch_group", charger_type="smartevse_charger")


class _TestDbusPublishControllerDiagnosticsPart2:
    def test_diagnostic_values_include_dbus_introspection_map_counts(self) -> None:
        service = SimpleNamespace()
        controller = DbusPublishController(service, self._real_age_seconds)
        snapshot = {
            "worker_state": "running",
            "queue_depth": 3,
            "services": {
                "svc.good": {"paths": {"/A": {"status": "fresh"}}},
                "svc.bad": {
                    "paths": {
                        "/Missing": {"status": "known-missing"},
                        "/Slow": {"status": "unresponsive-backoff"},
                        "/Odd": [],
                    }
                },
                "svc.odd": {"paths": []},
                "svc.list": [],
            },
        }

        with patch("venus_evcharger.publish.dbus_diagnostics_introspection.load_owner_introspection_snapshot", return_value=snapshot):
            values = controller._dbus_introspection_counter_values(100.0)

        self.assertEqual(values["auto_dbus_introspection_state"], "running")
        self.assertEqual(values["auto_dbus_introspection_queue_depth"], 3)
        self.assertEqual(values["auto_dbus_introspection_service_count"], 4)
        self.assertEqual(values["auto_dbus_introspection_unusable_path_count"], 2)

        with patch("venus_evcharger.publish.dbus_diagnostics_introspection.load_owner_introspection_snapshot", return_value={"services": []}):
            odd_values = controller._dbus_introspection_counter_values(101.0)

        self.assertEqual(odd_values["auto_dbus_introspection_service_count"], 0)
        self.assertEqual(odd_values["auto_dbus_introspection_unusable_path_count"], 0)

    def test_dbus_introspection_counter_helpers_reject_malformed_payload_shapes(self) -> None:
        controller = DbusPublishController(SimpleNamespace(), self._real_age_seconds)

        self.assertEqual(controller._dbus_introspection_state([]), "missing")
        self.assertEqual(controller._dbus_introspection_state({}), "missing")
        self.assertEqual(controller._dbus_introspection_state({"worker_state": ""}), "missing")
        self.assertEqual(controller._dbus_introspection_state({"worker_state": "running"}), "running")
        self.assertEqual(controller._dbus_introspection_queue_depth([]), 0)
        self.assertEqual(controller._dbus_introspection_queue_depth({"queue_depth": None}), 0)
        self.assertEqual(controller._dbus_introspection_queue_depth({"queue_depth": -2}), 0)
        self.assertEqual(controller._dbus_introspection_queue_depth({"queue_depth": "bad"}), 0)
        self.assertEqual(controller._dbus_introspection_queue_depth({"queue_depth": "4"}), 4)
        self.assertEqual(controller._dbus_introspection_service_count([]), 0)
        self.assertEqual(controller._dbus_introspection_service_count({"svc": {}}), 1)
        self.assertEqual(controller._dbus_introspection_unusable_count([]), 0)
        self.assertEqual(controller._dbus_introspection_unusable_paths([]), 0)
        self.assertEqual(controller._dbus_introspection_unusable_paths({"paths": []}), 0)
        self.assertEqual(
            controller._dbus_introspection_unusable_paths(
                {
                    "paths": {
                        "/Missing": {"status": "known-missing"},
                        "/Slow": {"status": "unresponsive-backoff"},
                        "/Fresh": {"status": "fresh"},
                        "/Odd": object(),
                    }
                }
            ),
            2,
        )
        self.assertTrue(_introspection_finding_unusable({"status": "known-missing"}))
        self.assertTrue(_introspection_finding_unusable({"status": "unresponsive-backoff"}))
        self.assertFalse(_introspection_finding_unusable({"status": "fresh"}))
        self.assertFalse(_introspection_finding_unusable({"status": ""}))
        self.assertFalse(_introspection_finding_unusable({"status": None}))
        self.assertFalse(_introspection_finding_unusable({}))
        self.assertFalse(_introspection_finding_unusable([]))

        snapshot_loader = MagicMock(return_value={"heartbeat_at": 90.0})
        with patch("venus_evcharger.publish.dbus_diagnostics_introspection.load_owner_introspection_snapshot", snapshot_loader):
            self.assertEqual(controller._dbus_introspection_snapshot_age(100.0), 10.0)
        snapshot_loader.assert_called_once_with(controller.service, now=100.0)

        with patch(
            "venus_evcharger.publish.dbus_diagnostics_introspection.load_owner_introspection_snapshot",
            return_value={},
        ):
            self.assertEqual(controller._dbus_introspection_snapshot_age(100.0), -1.0)

    def test_diagnostic_values_keep_fault_and_recovery_visible_while_scheduled_and_retry_are_also_active(self) -> None:
        current_time = 1776718800.0
        service = _with_backends_config(SimpleNamespace(
            _error_state={"dbus": 0, "shelly": 0, "charger": 1, "pv": 0, "battery": 0, "grid": 0, "cache_hits": 0},
            last_status=0,
            virtual_mode=2,
            _last_health_reason="contactor-lockout-open",
            _last_health_code=32,
            _last_auto_state="recovery",
            _last_auto_state_code=5,
            _last_status_source="contactor-lockout-open",
            auto_month_windows={4: ((7, 30), (19, 30))},
            auto_scheduled_enabled_days="Mon,Tue,Wed,Thu,Fri",
            auto_scheduled_night_start_delay_seconds=3600.0,
            auto_scheduled_latest_end_time="06:30",
            _charger_backend=SimpleNamespace(set_current=MagicMock()),
            _last_charger_state_status="charging",
            _last_charger_state_fault="overcurrent error",
            _last_charger_fault_active=1,
            _last_charger_state_at=current_time - 1.0,
            _last_charger_transport_reason="offline",
            _last_charger_transport_source="read",
            _last_charger_transport_detail="timeout",
            _last_charger_transport_at=current_time - 1.0,
            _charger_retry_reason="offline",
            _charger_retry_source="read",
            _charger_retry_until=current_time + 12.0,
            _last_confirmed_pm_status={"_phase_selection": "P1", "output": False},
            _phase_switch_mismatch_active=False,
            supported_phase_selections=("P1", "P1_P2"),
            _phase_switch_lockout_selection="P1_P2",
            _phase_switch_lockout_reason="mismatch-threshold",
            _phase_switch_lockout_at=current_time - 5.0,
            _phase_switch_lockout_until=current_time + 50.0,
            _last_switch_feedback_closed=False,
            _last_switch_interlock_ok=True,
            _last_switch_feedback_at=current_time - 1.0,
            _contactor_fault_counts={"contactor-suspected-open": 3},
            _contactor_lockout_reason="contactor-suspected-open",
            _contactor_lockout_source="count-threshold",
            _contactor_lockout_at=current_time - 4.0,
            _is_update_stale=self._never_stale,
            _recovery_attempts=1,
            _last_confirmed_pm_status_at=current_time - 2.0,
            _last_pm_status_at=current_time - 2.0,
            _last_pm_status_confirmed=True,
            _last_pv_at=current_time - 2.0,
            _last_battery_soc_at=current_time - 2.0,
            _last_grid_at=current_time - 2.0,
            _last_dbus_ok_at=current_time - 1.0,
            _last_successful_update_at=current_time - 3.0,
            started_at=current_time - 10.0,
        ), mode="split", meter_type="template_meter", switch_type="switch_group", charger_type="simpleevse_charger")
        controller = DbusPublishController(service, self._real_age_seconds)

        counter_values = controller._diagnostic_counter_values(current_time)

        self.assertEqual(counter_values["status"], 0)
        self.assertEqual(counter_values["auto_recovery_active"], 1)
        self.assertEqual(counter_values["auto_fault_active"], 1)
        self.assertEqual(counter_values["auto_fault_reason"], "contactor-lockout-open")
        self.assertEqual(counter_values["auto_status_source"], "contactor-lockout-open")
        self.assertEqual(counter_values["auto_scheduled_state"], "night-boost")
        self.assertEqual(counter_values["auto_scheduled_reason"], "night-boost-window")
        self.assertEqual(counter_values["auto_charger_transport_active"], 1)
        self.assertEqual(counter_values["auto_charger_retry_active"], 1)
        self.assertEqual(counter_values["auto_contactor_lockout_active"], 1)
        self.assertEqual(counter_values["auto_contactor_lockout_reason"], "contactor-suspected-open")

    def test_diagnostic_values_keep_retry_visible_after_transport_detail_has_gone_stale(self) -> None:
        current_time = 200.0
        service = _with_backends_config(SimpleNamespace(
            _error_state={"dbus": 0, "shelly": 0, "charger": 1, "pv": 0, "battery": 0, "grid": 0, "cache_hits": 0},
            last_status=6,
            virtual_mode=1,
            _last_health_reason="charger-transport-offline",
            _last_health_code=37,
            _last_auto_state="blocked",
            _last_auto_state_code=4,
            _last_status_source="charger-status-ready",
            auto_month_windows={},
            auto_scheduled_enabled_days="Mon,Tue,Wed,Thu,Fri",
            auto_scheduled_night_start_delay_seconds=3600.0,
            auto_scheduled_latest_end_time="06:30",
            _last_charger_state_status="ready",
            _last_charger_state_fault="",
            _last_charger_fault_active=0,
            _last_charger_state_at=199.0,
            auto_shelly_soft_fail_seconds=10.0,
            _last_charger_transport_reason="offline",
            _last_charger_transport_source="read",
            _last_charger_transport_detail="timeout",
            _last_charger_transport_at=150.0,
            _charger_retry_reason="offline",
            _charger_retry_source="read",
            _charger_retry_until=210.0,
            _last_confirmed_pm_status={"_phase_selection": "P1", "output": False},
            _phase_switch_mismatch_active=False,
            supported_phase_selections=("P1",),
            _phase_switch_lockout_selection=None,
            _phase_switch_lockout_reason="",
            _phase_switch_lockout_at=None,
            _phase_switch_lockout_until=None,
            _last_switch_feedback_closed=None,
            _last_switch_interlock_ok=None,
            _last_switch_feedback_at=None,
            _contactor_fault_counts={},
            _contactor_lockout_reason="",
            _contactor_lockout_source="",
            _contactor_lockout_at=None,
            _is_update_stale=self._never_stale,
            _recovery_attempts=0,
            _last_confirmed_pm_status_at=199.0,
            _last_pm_status_at=199.0,
            _last_pm_status_confirmed=True,
            _last_pv_at=199.0,
            _last_battery_soc_at=199.0,
            _last_grid_at=199.0,
            _last_dbus_ok_at=199.0,
            _last_successful_update_at=199.0,
            started_at=100.0,
        ), mode="split", meter_type="template_meter", switch_type="switch_group", charger_type="smartevse_charger")
        controller = DbusPublishController(service, self._real_age_seconds)

        counter_values = controller._diagnostic_counter_values(current_time)
        age_values = controller._diagnostic_age_values(current_time)

        self.assertEqual(counter_values["status"], 6)
        self.assertEqual(counter_values["auto_fault_active"], 0)
        self.assertEqual(counter_values["auto_fault_reason"], "")
        self.assertEqual(counter_values["auto_charger_transport_active"], 0)
        self.assertEqual(counter_values["auto_charger_transport_reason"], "")
        self.assertEqual(counter_values["auto_charger_retry_active"], 1)
        self.assertEqual(counter_values["auto_charger_retry_reason"], "offline")
        self.assertEqual(counter_values["auto_status_source"], "charger-status-ready")
        self.assertEqual(age_values["auto_last_charger_transport_age"], -1.0)
        self.assertEqual(age_values["auto_charger_retry_remaining"], 10.0)

    def test_diagnostic_values_prefer_confirmed_switch_group_phase_over_native_charger_phase(self) -> None:
        current_time = 200.0
        service = _with_backends_config(SimpleNamespace(
            _error_state={"dbus": 0, "shelly": 0, "charger": 0, "pv": 0, "battery": 0, "grid": 0, "cache_hits": 0},
            last_status=2,
            virtual_mode=1,
            _last_health_reason="",
            _last_health_code=0,
            _last_auto_state="running",
            _last_auto_state_code=2,
            _last_status_source="charger-status-charging",
            auto_month_windows={},
            auto_scheduled_enabled_days="Mon,Tue,Wed,Thu,Fri",
            auto_scheduled_night_start_delay_seconds=3600.0,
            auto_scheduled_latest_end_time="06:30",
            _last_charger_state_status="charging",
            _last_charger_state_fault="",
            _last_charger_fault_active=0,
            _last_charger_state_phase_selection="P1",
            _last_charger_state_at=199.0,
            _last_confirmed_pm_status={"_phase_selection": "P1_P2", "output": True},
            _last_confirmed_pm_status_at=199.0,
            _last_pm_status_at=199.0,
            _last_pm_status_confirmed=True,
            _phase_switch_mismatch_active=True,
            supported_phase_selections=("P1", "P1_P2"),
            _phase_switch_lockout_selection=None,
            _phase_switch_lockout_reason="",
            _phase_switch_lockout_at=None,
            _phase_switch_lockout_until=None,
            _last_switch_feedback_closed=None,
            _last_switch_interlock_ok=None,
            _last_switch_feedback_at=None,
            _contactor_fault_counts={},
            _contactor_lockout_reason="",
            _contactor_lockout_source="",
            _contactor_lockout_at=None,
            _is_update_stale=self._never_stale,
            _recovery_attempts=0,
            _last_pv_at=199.0,
            _last_battery_soc_at=199.0,
            _last_grid_at=199.0,
            _last_dbus_ok_at=199.0,
            _last_successful_update_at=199.0,
            started_at=100.0,
        ), mode="split", meter_type="template_meter", switch_type="switch_group", charger_type="smartevse_charger")
        controller = DbusPublishController(service, self._real_age_seconds)

        counter_values = controller._diagnostic_counter_values(current_time)

        self.assertEqual(counter_values["status"], 2)
        self.assertEqual(counter_values["auto_status_source"], "charger-status-charging")
        self.assertEqual(counter_values["auto_phase_observed"], "P1_P2")
        self.assertEqual(counter_values["auto_phase_mismatch_active"], 1)

    def test_diagnostic_counter_and_age_contracts_publish_complete_semantic_surface(self) -> None:
        current_time = 500.0
        service = _diagnostic_contract_service(self, current_time)
        controller = DbusPublishController(service, self._real_age_seconds)

        with patch("venus_evcharger.publish.dbus_diagnostics_introspection.load_owner_introspection_snapshot", return_value={
            "worker_state": "running",
            "queue_depth": 2,
            "heartbeat_at": current_time - 29.0,
            "services": {"svc": {"paths": {"/Missing": {"status": "known-missing"}}}},
        }):
            counter_values = controller._diagnostic_counter_values(current_time)
            age_values = controller._diagnostic_age_values(current_time)

        self.assertEqual(
            set(counter_values),
            {
                "status",
                "auto_health",
                "auto_health_code",
                "auto_state",
                "auto_state_code",
                "auto_recovery_active",
                "auto_status_source",
                "auto_fault_active",
                "auto_fault_reason",
                "auto_stale",
                "auto_recovery_attempts",
                "auto_decision_reason",
                "auto_decision_state",
                "auto_decision_state_code",
                "auto_decision_relay_intent",
                "auto_decision_surplus_watts",
                "auto_decision_grid_watts",
                "auto_decision_soc_percent",
                "auto_decision_start_threshold_watts",
                "auto_decision_stop_threshold_watts",
                "auto_decision_profile",
                "auto_decision_threshold_mode",
                "auto_scheduled_state",
                "auto_scheduled_state_code",
                "auto_scheduled_reason",
                "auto_scheduled_reason_code",
                "auto_scheduled_night_boost_active",
                "auto_scheduled_target_day_enabled",
                "auto_scheduled_target_day",
                "auto_scheduled_target_date",
                "auto_scheduled_fallback_start",
                "auto_scheduled_boost_until",
                "auto_backend_mode",
                "auto_meter_backend",
                "auto_switch_backend",
                "auto_charger_backend",
                "auto_runtime_overrides_active",
                "auto_runtime_overrides_path",
                "auto_software_update_available",
                "auto_software_update_state",
                "auto_software_update_state_code",
                "auto_software_update_detail",
                "auto_software_update_current_version",
                "auto_software_update_available_version",
                "auto_software_update_no_update_active",
                "auto_charger_status",
                "auto_charger_fault",
                "auto_charger_fault_active",
                "auto_charger_estimate_active",
                "auto_charger_estimate_source",
                "auto_charger_transport_active",
                "auto_charger_transport_reason",
                "auto_charger_transport_source",
                "auto_charger_transport_detail",
                "auto_charger_retry_active",
                "auto_charger_retry_reason",
                "auto_charger_retry_source",
                "auto_charger_current_target",
                "auto_error_count",
                "auto_dbus_read_errors",
                "auto_shelly_read_errors",
                "auto_charger_write_errors",
                "auto_pv_read_errors",
                "auto_battery_read_errors",
                "auto_grid_read_errors",
                "auto_input_cache_hits",
                "auto_shelly_state",
                "auto_shelly_last_error",
                "auto_shelly_retry_remaining",
                "auto_shelly_consecutive_errors",
                "auto_phase_current",
                "auto_phase_observed",
                "auto_phase_target",
                "auto_phase_reason",
                "auto_phase_mismatch_active",
                "auto_phase_lockout_active",
                "auto_phase_lockout_target",
                "auto_phase_lockout_reason",
                "auto_phase_supported_configured",
                "auto_phase_supported_effective",
                "auto_phase_degraded_active",
                "auto_phase_threshold_watts",
                "auto_phase_candidate",
                "auto_switch_feedback_closed",
                "auto_switch_interlock_ok",
                "auto_switch_feedback_mismatch",
                "auto_contactor_suspected_open",
                "auto_contactor_suspected_welded",
                "auto_contactor_fault_count",
                "auto_contactor_lockout_active",
                "auto_contactor_lockout_reason",
                "auto_contactor_lockout_source",
                "auto_update_worker_duration_seconds",
                "auto_update_worker_pending",
                "auto_update_worker_skipped",
                "auto_publish_flush_duration_seconds",
                "auto_publish_queue_lag_seconds",
                "auto_publish_queue_dropped",
                "auto_write_command_duration_seconds",
                "auto_write_command_queue_lag_seconds",
                "auto_mainloop_heartbeat_age",
                "auto_dbus_introspection_state",
                "auto_dbus_introspection_queue_depth",
                "auto_dbus_introspection_service_count",
                "auto_dbus_introspection_unusable_path_count",
            },
        )
        self.assertEqual(counter_values["auto_recovery_attempts"], 4)
        self.assertEqual(counter_values["auto_error_count"], 21)
        self.assertEqual(counter_values["auto_input_cache_hits"], 7)
        self.assertEqual(counter_values["auto_charger_transport_detail"], "timeout")
        self.assertEqual(counter_values["auto_charger_retry_source"], "write")
        self.assertEqual(counter_values["auto_runtime_overrides_path"], "/run/override.json")
        self.assertEqual(counter_values["auto_dbus_introspection_unusable_path_count"], 1)
        self.assertEqual(counter_values["auto_health"], "running")
        self.assertEqual(counter_values["auto_stale"], 0)
        self.assertEqual(
            controller._runtime_timing_values(current_time),
            {
                "auto_update_worker_duration_seconds": 0.31,
                "auto_update_worker_pending": 1,
                "auto_update_worker_skipped": 5,
                "auto_publish_flush_duration_seconds": 0.41,
                "auto_publish_queue_lag_seconds": 0.51,
                "auto_publish_queue_dropped": 6,
                "auto_write_command_duration_seconds": 0.61,
                "auto_write_command_queue_lag_seconds": 0.71,
                "auto_mainloop_heartbeat_age": 28.0,
            },
        )
        self.assertEqual(counter_values["auto_phase_lockout_active"], 1)
        self.assertEqual(counter_values["auto_phase_lockout_target"], "P1_P2")
        self.assertEqual(counter_values["auto_phase_lockout_reason"], "mismatch-threshold")
        self.assertEqual(counter_values["auto_phase_supported_configured"], "P1,P1_P2")
        self.assertEqual(counter_values["auto_phase_supported_effective"], "P1")
        self.assertEqual(counter_values["auto_phase_degraded_active"], 1)
        self.assertEqual(counter_values["auto_switch_feedback_closed"], 1)
        self.assertEqual(counter_values["auto_switch_interlock_ok"], 0)
        self.assertEqual(counter_values["auto_contactor_fault_count"], 8)
        self.assertEqual(counter_values["auto_contactor_lockout_active"], 1)
        self.assertEqual(counter_values["auto_contactor_lockout_reason"], "contactor-suspected-open")
        self.assertEqual(counter_values["auto_contactor_lockout_source"], "count-threshold")
        self.assertEqual(
            controller._phase_counter_values(current_time),
            {
                "auto_phase_current": "",
                "auto_phase_observed": "P1_P2",
                "auto_phase_target": "",
                "auto_phase_reason": "",
                "auto_phase_mismatch_active": 1,
                "auto_phase_lockout_active": 1,
                "auto_phase_lockout_target": "P1_P2",
                "auto_phase_lockout_reason": "mismatch-threshold",
                "auto_phase_supported_configured": "P1,P1_P2",
                "auto_phase_supported_effective": "P1",
                "auto_phase_degraded_active": 1,
                "auto_phase_threshold_watts": -1.0,
                "auto_phase_candidate": "",
            },
        )
        service._phase_switch_lockout_until = current_time - 1.0
        self.assertEqual(
            controller._phase_counter_values(current_time)["auto_phase_supported_effective"],
            "P1,P1_P2",
        )
        service._phase_switch_lockout_until = current_time + 50.0
        self.assertEqual(
            controller._contactor_counter_values(),
            {
                "auto_switch_feedback_closed": 1,
                "auto_switch_interlock_ok": 0,
                "auto_switch_feedback_mismatch": 0,
                "auto_contactor_suspected_open": 0,
                "auto_contactor_suspected_welded": 0,
                "auto_contactor_fault_count": 8,
                "auto_contactor_lockout_active": 1,
                "auto_contactor_lockout_reason": "contactor-suspected-open",
                "auto_contactor_lockout_source": "count-threshold",
            },
        )
        service._last_switch_feedback_closed = False
        service._last_confirmed_pm_status = {"output": True}
        self.assertEqual(controller._contactor_counter_values()["auto_switch_feedback_mismatch"], 1)

        self.assertEqual(
            set(age_values),
            {
                "auto_last_shelly_read_age",
                "auto_shelly_last_ok_age",
                "auto_pending_relay_age",
                "auto_last_pv_read_age",
                "auto_last_battery_read_age",
                "auto_last_grid_read_age",
                "auto_last_dbus_read_age",
                "auto_charger_current_target_age",
                "auto_phase_candidate_age",
                "auto_phase_lockout_age",
                "auto_contactor_lockout_age",
                "auto_last_switch_feedback_age",
                "auto_last_charger_read_age",
                "auto_last_charger_estimate_age",
                "auto_last_charger_transport_age",
                "auto_charger_retry_remaining",
                "auto_last_successful_update_age",
                "auto_software_update_last_check_age",
                "auto_software_update_last_run_age",
                "auto_stale_seconds",
                "auto_dbus_introspection_snapshot_age",
            },
        )
        self.assertEqual(age_values["auto_last_shelly_read_age"], 10.0)
        self.assertEqual(age_values["auto_last_pv_read_age"], 12.0)
        self.assertEqual(age_values["auto_last_battery_read_age"], 13.0)
        self.assertEqual(age_values["auto_last_grid_read_age"], 14.0)
        self.assertEqual(age_values["auto_last_dbus_read_age"], 15.0)
        self.assertEqual(age_values["auto_charger_current_target_age"], 16.0)
        self.assertEqual(age_values["auto_last_charger_transport_age"], 1.0)
        self.assertEqual(age_values["auto_charger_retry_remaining"], 20.0)
        self.assertEqual(age_values["auto_last_successful_update_age"], 25.0)
        self.assertEqual(age_values["auto_stale_seconds"], 25.0)
        self.assertEqual(age_values["auto_dbus_introspection_snapshot_age"], 29.0)

    def test_diagnostic_runtime_error_state_accepts_only_mappings(self) -> None:
        controller = DbusPublishController(SimpleNamespace(), self._real_age_seconds)

        self.assertEqual(controller._runtime_error_state(SimpleNamespace(_error_state={"dbus": 1})), {"dbus": 1})
        self.assertEqual(controller._runtime_error_state(SimpleNamespace(_error_state=[])), {})
        self.assertEqual(controller._runtime_error_state(SimpleNamespace()), {})

    def test_diagnostic_error_counter_values_normalize_runtime_error_state(self) -> None:
        values = DbusPublishController._error_counter_values(
            {
                "dbus": "2",
                "shelly": None,
                "charger": True,
                "pv": -5,
                "battery": "bad",
                "grid": 3.9,
                "cache_hits": "7",
            }
        )

        self.assertEqual(
            values,
            {
                "auto_error_count": 5,
                "auto_dbus_read_errors": 2,
                "auto_shelly_read_errors": 0,
                "auto_charger_write_errors": 0,
                "auto_pv_read_errors": 0,
                "auto_battery_read_errors": 0,
                "auto_grid_read_errors": 3,
                "auto_input_cache_hits": 7,
            },
        )
        self.assertEqual(
            DbusPublishController._error_counter_values({}),
            {
                "auto_error_count": 0,
                "auto_dbus_read_errors": 0,
                "auto_shelly_read_errors": 0,
                "auto_charger_write_errors": 0,
                "auto_pv_read_errors": 0,
                "auto_battery_read_errors": 0,
                "auto_grid_read_errors": 0,
                "auto_input_cache_hits": 0,
            },
        )

    def test_diagnostic_backend_counter_values_cover_defaults_and_split_topology(self) -> None:
        default_controller = DbusPublishController(SimpleNamespace(), self._real_age_seconds)
        self.assertEqual(
            default_controller._backend_counter_values(),
            {
                "auto_backend_mode": "combined",
                "auto_meter_backend": "shelly_meter",
                "auto_switch_backend": "shelly_contactor_switch",
                "auto_charger_backend": "",
                "auto_runtime_overrides_active": 0,
                "auto_runtime_overrides_path": "",
            },
        )

        split_service = _with_backends_config(
            SimpleNamespace(_runtime_overrides_active=True, runtime_overrides_path="/run/runtime.json"),
            mode="split",
            meter_type="template_meter",
            switch_type="switch_group",
            charger_type="smartevse_charger",
        )
        split_controller = DbusPublishController(split_service, self._real_age_seconds)
        self.assertEqual(
            split_controller._backend_counter_values(),
            {
                "auto_backend_mode": "split",
                "auto_meter_backend": "template_meter",
                "auto_switch_backend": "switch_group",
                "auto_charger_backend": "smartevse_charger",
                "auto_runtime_overrides_active": 1,
                "auto_runtime_overrides_path": "/run/runtime.json",
            },
        )

    def test_diagnostic_charger_counter_values_publish_each_transport_field(self) -> None:
        service = SimpleNamespace(
            _last_charger_state_status=None,
            _last_charger_state_fault=" fault ",
            _last_charger_fault_active=True,
            _last_charger_estimate_source="estimated",
            _last_charger_estimate_at=95.0,
            _last_charger_transport_reason="offline",
            _last_charger_transport_source="read",
            _last_charger_transport_detail="timeout",
            _last_charger_transport_at=99.0,
            auto_dbus_backoff_max_seconds=30.0,
            _charger_retry_reason="busy",
            _charger_retry_source="write",
            _charger_retry_until=105.0,
            _charger_target_current_amps="12.5",
        )
        controller = DbusPublishController(service, self._real_age_seconds)

        self.assertEqual(
            controller._charger_counter_values(100.0),
            {
                "auto_charger_status": "",
                "auto_charger_fault": "fault",
                "auto_charger_fault_active": 1,
                "auto_charger_estimate_active": 1,
                "auto_charger_estimate_source": "estimated",
                "auto_charger_transport_active": 1,
                "auto_charger_transport_reason": "offline",
                "auto_charger_transport_source": "read",
                "auto_charger_transport_detail": "timeout",
                "auto_charger_retry_active": 1,
                "auto_charger_retry_reason": "busy",
                "auto_charger_retry_source": "write",
                "auto_charger_current_target": 12.5,
            },
        )

        service._last_charger_transport_at = 1.0
        service._charger_retry_until = 1.0
        stale_values = controller._charger_counter_values(100.0)
        self.assertEqual(stale_values["auto_charger_transport_active"], 0)
        self.assertEqual(stale_values["auto_charger_transport_reason"], "")
        self.assertEqual(stale_values["auto_charger_transport_source"], "")
        self.assertEqual(stale_values["auto_charger_transport_detail"], "")
        self.assertEqual(stale_values["auto_charger_retry_active"], 0)
        self.assertEqual(stale_values["auto_charger_retry_reason"], "")
        self.assertEqual(stale_values["auto_charger_retry_source"], "")

        missing_values = DbusPublishController(SimpleNamespace(), self._real_age_seconds)._charger_counter_values(100.0)
        self.assertEqual(missing_values["auto_charger_status"], "")
        self.assertEqual(missing_values["auto_charger_fault"], "")
        self.assertEqual(missing_values["auto_charger_fault_active"], 0)

    def test_diagnostic_shelly_counter_values_normalize_retry_and_error_edges(self) -> None:
        controller = DbusPublishController(
            SimpleNamespace(
                _shelly_state="offline",
                _shelly_last_error_reason="timeout",
                _source_retry_remaining=MagicMock(return_value="-4"),
                _shelly_consecutive_errors=-3,
            ),
            self._real_age_seconds,
        )

        self.assertEqual(
            controller._shelly_counter_values(50.0),
            {
                "auto_shelly_state": "offline",
                "auto_shelly_last_error": "timeout",
                "auto_shelly_retry_remaining": 0,
                "auto_shelly_consecutive_errors": 0,
            },
        )
        controller.service._source_retry_remaining.assert_called_once_with("shelly", 50.0)

        fallback = DbusPublishController(
            SimpleNamespace(
                _shelly_state="",
                _shelly_last_error_reason=None,
                _shelly_retry_after=61.9,
                _shelly_consecutive_errors="4",
            ),
            self._real_age_seconds,
        )
        self.assertEqual(fallback._shelly_counter_values(50.0)["auto_shelly_retry_remaining"], 11)
        self.assertEqual(fallback._shelly_counter_values(50.0)["auto_shelly_consecutive_errors"], 4)
        self.assertEqual(fallback._shelly_counter_values(50.0)["auto_shelly_state"], "unknown")
        self.assertEqual(fallback._shelly_counter_values(50.0)["auto_shelly_last_error"], "")

        missing_values = DbusPublishController(SimpleNamespace(), self._real_age_seconds)._shelly_counter_values(0.0)
        self.assertEqual(missing_values["auto_shelly_retry_remaining"], 0)
        self.assertEqual(missing_values["auto_shelly_consecutive_errors"], 0)
        self.assertEqual(missing_values["auto_shelly_state"], "unknown")
        self.assertEqual(missing_values["auto_shelly_last_error"], "")

        self.assertEqual(
            DbusPublishController._shelly_retry_remaining_value(SimpleNamespace(_shelly_retry_after=True), 50.0),
            0,
        )
        self.assertEqual(
            DbusPublishController._shelly_retry_remaining_value(SimpleNamespace(_shelly_retry_after="bad"), 50.0),
            0,
        )
        self.assertEqual(
            DbusPublishController._shelly_retry_remaining_value(SimpleNamespace(_shelly_retry_after=49.0), 50.0),
            0,
        )
        self.assertEqual(DbusPublishController._shelly_retry_remaining_value(SimpleNamespace(), 0.0), 0)

    def test_diagnostic_auto_decision_helper_contracts(self) -> None:
        metrics = {"good": "12.5", "bad": "x", "text": "  profile  ", "relay_intent": False}

        self.assertEqual(DbusPublishController._auto_decision_metric_float(metrics, "good"), 12.5)
        self.assertEqual(DbusPublishController._auto_decision_metric_float(metrics, "bad"), -1.0)
        self.assertEqual(DbusPublishController._auto_decision_metric_float(metrics, "missing"), -1.0)
        self.assertEqual(DbusPublishController._auto_decision_metric_text(metrics, "text"), "profile")
        self.assertEqual(DbusPublishController._auto_decision_metric_text(metrics, "missing"), "")
        self.assertEqual(DbusPublishController._auto_decision_relay_intent(metrics), 0)
        self.assertEqual(DbusPublishController._auto_decision_relay_intent({"relay_intent": True}), 1)
        self.assertEqual(DbusPublishController._auto_decision_relay_intent({}), -1)

    def test_diagnostic_runtime_timing_values_default_to_neutral_when_missing(self) -> None:
        controller = DbusPublishController(SimpleNamespace(), self._real_age_seconds)

        self.assertEqual(
            controller._runtime_timing_values(500.0),
            {
                "auto_update_worker_duration_seconds": 0.0,
                "auto_update_worker_pending": 0,
                "auto_update_worker_skipped": 0,
                "auto_publish_flush_duration_seconds": 0.0,
                "auto_publish_queue_lag_seconds": 0.0,
                "auto_publish_queue_dropped": 0,
                "auto_write_command_duration_seconds": 0.0,
                "auto_write_command_queue_lag_seconds": 0.0,
                "auto_mainloop_heartbeat_age": -1.0,
            },
        )
        controller = DbusPublishController(
            SimpleNamespace(_mainloop_heartbeat_at=501.0, _update_worker_pending=False),
            self._real_age_seconds,
        )
        self.assertEqual(controller._runtime_timing_values(500.0)["auto_mainloop_heartbeat_age"], 0.0)
        self.assertEqual(controller._runtime_timing_values(500.0)["auto_update_worker_pending"], 0)

    def test_diagnostic_last_shelly_read_age_uses_confirmed_then_confirmed_pm_fallback(self) -> None:
        current_time = 500.0
        service = _diagnostic_contract_service(self, current_time)
        controller = DbusPublishController(service, self._real_age_seconds)

        with patch("venus_evcharger.publish.dbus_diagnostics_introspection.load_owner_introspection_snapshot", return_value={}):
            service._last_confirmed_pm_status_at = current_time - 30.0
            service._last_pm_status_at = current_time - 11.0
            service._last_pm_status_confirmed = True
            self.assertEqual(controller._diagnostic_age_values(current_time)["auto_last_shelly_read_age"], 30.0)

            service._last_confirmed_pm_status_at = None
            self.assertEqual(controller._diagnostic_age_values(current_time)["auto_last_shelly_read_age"], 11.0)

            service._last_pm_status_confirmed = False
            self.assertEqual(controller._diagnostic_age_values(current_time)["auto_last_shelly_read_age"], -1.0)

            delattr(service, "_last_pm_status_confirmed")
            self.assertEqual(controller._diagnostic_age_values(current_time)["auto_last_shelly_read_age"], -1.0)

            service._last_confirmed_pm_status_at = current_time + 10.0
            service._last_pm_status_at = current_time - 11.0
            service._last_pm_status_confirmed = False
            self.assertEqual(controller._diagnostic_age_values(current_time)["auto_last_shelly_read_age"], -1.0)

    def test_diagnostic_counter_values_use_normalized_missing_state_and_current_stale_time(self) -> None:
        current_time = 500.0
        service = _diagnostic_contract_service(self, current_time)
        delattr(service, "_last_auto_state")
        delattr(service, "_last_status_source")
        service._is_update_stale = MagicMock(side_effect=lambda timestamp: timestamp == current_time)
        controller = DbusPublishController(service, self._real_age_seconds)
        snapshot_loader = MagicMock(return_value={})

        with patch("venus_evcharger.publish.dbus_diagnostics_introspection.load_owner_introspection_snapshot", snapshot_loader):
            values = controller._diagnostic_counter_values(current_time)

        self.assertEqual(values["auto_state"], "idle")
        self.assertEqual(values["auto_state_code"], 0)
        self.assertEqual(values["auto_status_source"], "unknown")
        self.assertEqual(values["auto_stale"], 1)
        service._is_update_stale.assert_called_once_with(current_time)
        snapshot_loader.assert_called_with(service, now=current_time)

    def test_scheduled_counter_values_normalize_disabled_and_active_snapshots(self) -> None:
        controller = DbusPublishController(SimpleNamespace(), self._real_age_seconds)

        self.assertEqual(
            controller._scheduled_counter_values_from_snapshot(None),
            {
                "auto_scheduled_state": "disabled",
                "auto_scheduled_state_code": 0,
                "auto_scheduled_reason": "disabled",
                "auto_scheduled_reason_code": 0,
                "auto_scheduled_night_boost_active": 0,
                "auto_scheduled_target_day_enabled": 0,
                "auto_scheduled_target_day": "",
                "auto_scheduled_target_date": "",
                "auto_scheduled_fallback_start": "",
                "auto_scheduled_boost_until": "",
            },
        )

        active = SimpleNamespace(
            state="night-boost",
            state_code=999,
            reason="night-boost-window",
            reason_code=999,
            night_boost_active=True,
            target_day_enabled=True,
            target_day_label="Mon",
            target_date_text="2026-07-06",
            fallback_start_text="22:00",
            boost_until_text="06:30",
        )
        self.assertEqual(
            controller._scheduled_counter_values_from_snapshot(active),
            {
                "auto_scheduled_state": "night-boost",
                "auto_scheduled_state_code": 4,
                "auto_scheduled_reason": "night-boost-window",
                "auto_scheduled_reason_code": 4,
                "auto_scheduled_night_boost_active": 1,
                "auto_scheduled_target_day_enabled": 1,
                "auto_scheduled_target_day": "Mon",
                "auto_scheduled_target_date": "2026-07-06",
                "auto_scheduled_fallback_start": "22:00",
                "auto_scheduled_boost_until": "06:30",
            },
        )

        invalid = SimpleNamespace(
            state="bad",
            state_code=4,
            reason="bad",
            reason_code=4,
            night_boost_active=True,
            target_day_enabled=False,
            target_day_label="",
            target_date_text="",
            fallback_start_text="",
            boost_until_text="",
        )
        self.assertEqual(controller._scheduled_counter_values_from_snapshot(invalid)["auto_scheduled_state"], "disabled")
        self.assertEqual(
            controller._scheduled_counter_values_from_snapshot(invalid)["auto_scheduled_night_boost_active"],
            0,
        )

        missing_text = SimpleNamespace(
            state="auto-window",
            reason="daytime-auto",
            night_boost_active=False,
            target_day_enabled=False,
        )
        missing_values = controller._scheduled_counter_values_from_snapshot(missing_text)
        self.assertEqual(missing_values["auto_scheduled_target_day_enabled"], 0)
        self.assertEqual(missing_values["auto_scheduled_target_day"], "")
        self.assertEqual(missing_values["auto_scheduled_target_date"], "")
        self.assertEqual(missing_values["auto_scheduled_fallback_start"], "")
        self.assertEqual(missing_values["auto_scheduled_boost_until"], "")

    def test_software_update_counter_values_normalize_blocked_and_missing_state(self) -> None:
        controller = DbusPublishController(
            SimpleNamespace(
                _software_update_state="available",
                _software_update_available=True,
                _software_update_no_update_active=True,
                _software_update_detail=None,
                _software_update_current_version=" 1.0 ",
                _software_update_available_version="1.1",
            ),
            self._real_age_seconds,
        )

        self.assertEqual(
            controller._software_update_counter_values(),
            {
                "auto_software_update_available": 1,
                "auto_software_update_state": "available-blocked",
                "auto_software_update_state_code": 4,
                "auto_software_update_detail": "",
                "auto_software_update_current_version": " 1.0 ",
                "auto_software_update_available_version": "1.1",
                "auto_software_update_no_update_active": 1,
            },
        )

        fallback = DbusPublishController(SimpleNamespace(_software_update_state="nonsense"), self._real_age_seconds)
        self.assertEqual(fallback._software_update_counter_values()["auto_software_update_state"], "idle")
        self.assertEqual(fallback._software_update_counter_values()["auto_software_update_available"], 0)

        missing = DbusPublishController(SimpleNamespace(), self._real_age_seconds)
        self.assertEqual(
            missing._software_update_counter_values(),
            {
                "auto_software_update_available": 0,
                "auto_software_update_state": "idle",
                "auto_software_update_state_code": 0,
                "auto_software_update_detail": "",
                "auto_software_update_current_version": "",
                "auto_software_update_available_version": "",
                "auto_software_update_no_update_active": 0,
            },
        )

        unblocked = DbusPublishController(
            SimpleNamespace(
                _software_update_state="available-blocked",
                _software_update_available=False,
                _software_update_no_update_active=False,
            ),
            self._real_age_seconds,
        )
        self.assertEqual(unblocked._software_update_counter_values()["auto_software_update_state"], "up-to-date")
        self.assertEqual(unblocked._software_update_counter_values()["auto_software_update_state_code"], 2)

    def test_diagnostic_age_values_tolerate_missing_optional_timestamps(self) -> None:
        current_time = 500.0
        service = _diagnostic_contract_service(self, current_time)
        for attribute_name in (
            "_last_confirmed_pm_status_at",
            "_last_pm_status_at",
            "_last_pm_status_confirmed",
            "_phase_switch_lockout_at",
            "_contactor_lockout_at",
            "_last_charger_estimate_at",
        ):
            delattr(service, attribute_name)
        controller = DbusPublishController(service, self._real_age_seconds)

        with patch("venus_evcharger.publish.dbus_diagnostics_introspection.load_owner_introspection_snapshot", return_value={}):
            age_values = controller._diagnostic_age_values(current_time)

        self.assertEqual(age_values["auto_last_shelly_read_age"], -1.0)
        self.assertEqual(age_values["auto_phase_lockout_age"], -1.0)
        self.assertEqual(age_values["auto_contactor_lockout_age"], -1.0)
        self.assertEqual(age_values["auto_last_charger_estimate_age"], -1.0)

    def test_publish_diagnostic_paths_uses_distinct_transactions_and_or_result(self) -> None:
        service = SimpleNamespace(_dbus_slow_publish_interval_seconds=7.5)
        controller = DbusPublishController(service, self._real_age_seconds)
        controller.ensure_state = MagicMock()
        controller._diagnostic_counter_values = MagicMock(return_value={"status": 2})
        controller._diagnostic_age_values = MagicMock(return_value={"auto_stale_seconds": 3.0})
        controller._publish_fields_transactional = MagicMock(side_effect=[False, True])

        self.assertTrue(controller.publish_diagnostic_paths(500.0))

        controller.ensure_state.assert_called_once_with()
        controller._diagnostic_counter_values.assert_called_once_with(500.0)
        controller._diagnostic_age_values.assert_called_once_with(500.0)
        self.assertEqual(controller._publish_fields_transactional.call_count, 2)
        controller._publish_fields_transactional.assert_any_call("diagnostic-counters", {"status": 2}, 500.0)
        controller._publish_fields_transactional.assert_any_call(
            "diagnostic-ages",
            {"auto_stale_seconds": 3.0},
            500.0,
            interval_seconds=7.5,
        )

        controller._publish_fields_transactional = MagicMock(side_effect=[True, False])
        self.assertTrue(controller.publish_diagnostic_paths(501.0))
        controller._publish_fields_transactional = MagicMock(side_effect=[False, False])
        self.assertFalse(controller.publish_diagnostic_paths(502.0))

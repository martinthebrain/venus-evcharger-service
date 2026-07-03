# SPDX-License-Identifier: GPL-3.0-or-later
from tests.venus_evcharger_publisher_diagnostic_cases_support import *  # noqa: F401,F403
from unittest.mock import patch

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

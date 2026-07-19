# SPDX-License-Identifier: GPL-3.0-or-later
from tests.venus_evcharger_update_cycle_controller_support import *


class TestUpdateCycleControllerQuindenary(UpdateCycleControllerTestBase):
    def test_update_forces_charger_off_when_native_charger_fault_is_fresh(self):
        charger_backend = SimpleNamespace(
            set_enabled=MagicMock(),
            set_current=MagicMock(),
        )
        service = SimpleNamespace(
            time_now=MagicMock(return_value=105.0),
            _state_summary=lambda: "state",
            _watchdog_recover=MagicMock(),
            _ensure_auto_input_helper_process=MagicMock(),
            _refresh_auto_input_snapshot=MagicMock(),
            _get_worker_snapshot=MagicMock(
                return_value={
                    "pm_status": {
                        "output": True,
                        "apower": 1200.0,
                        "voltage": 230.0,
                        "current": 5.2,
                        "aenergy": {"total": 1.0},
                    },
                    "pm_confirmed": True,
                    "pm_captured_at": 105.0,
                }
            ),
            _last_pm_status=None,
            _last_pm_status_at=None,
            _last_pm_status_confirmed=False,
            _safe_float=lambda value, default=0.0: float(value) if value is not None else default,
            virtual_mode=1,
            phase="L1",
            voltage_mode="phase",
            _mode_uses_auto_logic=lambda mode: int(mode) in (1, 2),
            _auto_decide_relay=MagicMock(return_value=True),
            _charger_backend=charger_backend,
            _last_charger_state_status="fault",
            _last_charger_state_fault="overcurrent error",
            _last_charger_state_at=105.0,
            _publish_live_measurements=MagicMock(return_value=False),
            _publish_energy_time_measurements=MagicMock(return_value=False),
            _publish_config_paths=MagicMock(return_value=False),
            _publish_diagnostic_paths=MagicMock(return_value=False),
            _save_runtime_state=MagicMock(),
            _ensure_observability_state=MagicMock(),
            _set_health=MagicMock(),
            _last_health_reason="running",
            _last_health_code=5,
            charging_started_at=None,
            energy_at_start=0.0,
            last_status=0,
            virtual_enable=1,
            virtual_set_current=11.0,
            _dbusservice={"/Ac/Power": 0.0},
            service_name="com.victronenergy.evcharger.http_60",
            last_update=0.0,
            _dbus_publish_state={},
            _dbus_live_publish_interval_seconds=1.0,
            _dbus_slow_publish_interval_seconds=5.0,
            _last_voltage=230.0,
            virtual_startstop=0,
            charging_threshold_watts=100.0,
            idle_status=1,
            _last_successful_update_at=None,
            _last_recovery_attempt_at=None,
            _error_state={"cache_hits": 0},
            _last_pv_value=None,
            _last_pv_at=None,
            _last_grid_value=None,
            _last_grid_at=None,
            _last_battery_soc_value=None,
            _last_battery_soc_at=None,
            auto_input_cache_seconds=0.0,
            auto_shelly_soft_fail_seconds=10.0,
            auto_audit_log=False,
            _last_auto_metrics={"surplus": None, "grid": None, "soc": None},
            learned_charge_power_watts=None,
            learned_charge_power_updated_at=None,
            learned_charge_power_state="unknown",
            learned_charge_power_learning_since=None,
            learned_charge_power_sample_count=0,
            learned_charge_power_phase=None,
            learned_charge_power_voltage=None,
            learned_charge_power_signature_mismatch_sessions=0,
            learned_charge_power_signature_checked_session_started_at=None,
            auto_policy=_learning_policy(enabled=False),
            min_current=6.0,
            max_current=16.0,
            _relay_sync_expected_state=None,
            _relay_sync_requested_at=None,
            _relay_sync_deadline_at=None,
            _relay_sync_failure_reported=False,
            _charger_target_current_amps=None,
            _charger_target_current_applied_at=None,
            _mark_failure=MagicMock(),
            _mark_recovery=MagicMock(),
            _warning_throttled=MagicMock(),
            _queue_relay_command=MagicMock(),
            _publish_local_pm_status=MagicMock(),
            _bump_update_index=MagicMock(),
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0, "charger-fault": 26}.get(reason, 99))

        self.assertTrue(controller.update())

        charger_backend.set_current.assert_not_called()
        charger_backend.set_enabled.assert_called_once_with(False)
        service._set_health.assert_any_call("charger-fault", cached=False)
        service._warning_throttled.assert_called_once()
        self.assertEqual(service.last_status, 0)
        self.assertEqual(service._last_status_source, "charger-fault")
        self.assertEqual(service._last_charger_fault_active, 1)

    def test_update_saves_runtime_state_when_charge_power_learning_updates(self):
        service = SimpleNamespace(
            time_now=MagicMock(return_value=100.0),
            _state_summary=lambda: "state",
            _watchdog_recover=MagicMock(),
            _ensure_auto_input_helper_process=MagicMock(),
            _refresh_auto_input_snapshot=MagicMock(),
            _get_worker_snapshot=MagicMock(
                return_value={
                    "pm_status": {"output": True, "apower": 1900.0, "voltage": 230.0, "current": 8.3, "aenergy": {"total": 1.0}},
                    "pm_confirmed": True,
                    "pm_captured_at": 100.0,
                }
            ),
            _last_pm_status=None,
            _last_pm_status_at=None,
            _last_pm_status_confirmed=False,
            _safe_float=lambda value, default=0.0: float(value) if value is not None else default,
            virtual_mode=1,
            phase="L1",
            voltage_mode="phase",
            _mode_uses_auto_logic=lambda mode: int(mode) in (1, 2),
            _auto_decide_relay=MagicMock(return_value=True),
            _publish_live_measurements=MagicMock(return_value=False),
            _publish_energy_time_measurements=MagicMock(return_value=False),
            _publish_config_paths=MagicMock(return_value=False),
            _publish_diagnostic_paths=MagicMock(return_value=False),
            _save_runtime_state=MagicMock(),
            _ensure_observability_state=MagicMock(),
            _last_health_reason="init",
            _last_health_code=0,
            charging_started_at=50.0,
            energy_at_start=0.0,
            last_status=0,
            virtual_enable=1,
            _dbusservice={"/Ac/Power": 0.0},
            service_name="com.victronenergy.evcharger.http_60",
            last_update=0.0,
            _dbus_publish_state={},
            _dbus_live_publish_interval_seconds=1.0,
            _dbus_slow_publish_interval_seconds=5.0,
            _last_voltage=230.0,
            virtual_startstop=1,
            charging_threshold_watts=100.0,
            idle_status=1,
            _last_successful_update_at=None,
            _last_recovery_attempt_at=None,
            _error_state={"cache_hits": 0},
            _last_pv_value=None,
            _last_pv_at=None,
            _last_grid_value=None,
            _last_grid_at=None,
            _last_battery_soc_value=None,
            _last_battery_soc_at=None,
            auto_input_cache_seconds=0.0,
            auto_policy=_learning_policy(),
            learned_charge_power_watts=None,
            learned_charge_power_updated_at=None,
            learned_charge_power_state="unknown",
            learned_charge_power_learning_since=None,
            learned_charge_power_sample_count=0,
            learned_charge_power_phase=None,
            learned_charge_power_voltage=None,
            learned_charge_power_signature_mismatch_sessions=0,
            learned_charge_power_signature_checked_session_started_at=None,
            _bump_update_index=MagicMock(),
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        self.assertTrue(controller.update())

        self.assertGreaterEqual(service._save_runtime_state.call_count, 1)
        self.assertEqual(service.learned_charge_power_watts, 1900.0)
        self.assertEqual(service.learned_charge_power_updated_at, 100.0)

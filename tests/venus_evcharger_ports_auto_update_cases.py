# SPDX-License-Identifier: GPL-3.0-or-later
import unittest
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import MagicMock

from venus_evcharger.ports import AutoDecisionPort, DbusInputPort, UpdateCyclePort


class TestWallboxPortsAutoUpdate(unittest.TestCase):
    def test_dbus_input_port_uses_service_override_before_controller(self) -> None:
        service = SimpleNamespace(auto_pv_service="", auto_pv_service_prefix="com.victronenergy.pvinverter", _resolved_auto_pv_services=[], _auto_pv_last_scan=0.0, auto_pv_scan_interval_seconds=60.0, auto_pv_max_services=2, auto_pv_path="/Ac/Power", auto_use_dc_pv=False, auto_dc_pv_service="com.victronenergy.system", auto_dc_pv_path="/Dc/Pv/Power", _last_pv_missing_warning=None, auto_battery_service="", auto_battery_service_prefix="com.victronenergy.battery", auto_battery_soc_path="/Soc", _resolved_auto_battery_service=None, _auto_battery_last_scan=0.0, auto_battery_scan_interval_seconds=60.0, auto_grid_l1_path="/Ac/Grid/L1/Power", auto_grid_l2_path="/Ac/Grid/L2/Power", auto_grid_l3_path="/Ac/Grid/L3/Power", auto_grid_require_all_phases=True, auto_grid_service="com.victronenergy.system", _dbus_list_backoff_until=0.0, _dbus_list_failures=0, auto_dbus_backoff_base_seconds=5.0, auto_dbus_backoff_max_seconds=60.0, dbus_method_timeout_seconds=1.0, _last_dbus_ok_at=None, _source_retry_ready=MagicMock(return_value=True), _mark_recovery=MagicMock(), _mark_failure=MagicMock(), _delay_source_retry=MagicMock(), _warning_throttled=MagicMock(), _get_system_bus=MagicMock(), _reset_system_bus=MagicMock(), _get_dbus_value=MagicMock(return_value=42.0))
        port = DbusInputPort(service)
        class DummyController:
            def get_dbus_value(self, *_args: Any, **_kwargs: Any) -> Any:
                raise AssertionError("service override should win")
        port.bind_controller(DummyController())
        self.assertEqual(port.get_dbus_value("svc", "/Path"), 42.0)

    def test_dbus_input_port_validates_override_return_contracts(self) -> None:
        service = SimpleNamespace(
            _get_dbus_value=MagicMock(return_value=["raw", 1]),
            _list_dbus_services=MagicMock(return_value=["svc", 1]),
            _resolve_auto_pv_services=MagicMock(return_value="svc"),
            _resolve_auto_battery_service=MagicMock(return_value=None),
        )
        port = DbusInputPort(service)

        self.assertEqual(port.get_dbus_value("svc", "/Path"), ["raw", 1])
        with self.assertRaisesRegex(TypeError, "list_dbus_services must return list\\[str\\]"):
            port.list_dbus_services()
        with self.assertRaisesRegex(TypeError, "resolve_auto_pv_services must return list, got str"):
            port.resolve_auto_pv_services()
        self.assertIsNone(port.resolve_auto_battery_service())

    def test_auto_decision_port_falls_back_to_bound_controller(self) -> None:
        service = SimpleNamespace(auto_samples=[], auto_average_window_seconds=60.0, relay_last_changed_at=None, relay_last_off_at=None, auto_start_condition_since=None, auto_stop_condition_since=None, _last_health_reason="init", _last_health_code=0, auto_min_runtime_seconds=0.0, auto_min_offtime_seconds=0.0, _last_grid_at=None, auto_grid_missing_stop_seconds=60.0, virtual_mode=1, _auto_mode_cutover_pending=False, _ignore_min_offtime_once=False, _last_battery_allow_warning=None, auto_allow_without_battery_soc=False, auto_battery_scan_interval_seconds=60.0, auto_resume_soc=50.0, auto_min_soc=30.0, auto_stop_delay_seconds=30.0, auto_stop_grid_import_watts=200.0, auto_night_lock_stop=False, _last_auto_metrics={}, started_at=0.0, auto_startup_warmup_seconds=0.0, manual_override_until=0.0, virtual_autostart=1, auto_start_delay_seconds=30.0, auto_start_max_grid_import_watts=50.0, auto_start_surplus_watts=2000.0, auto_stop_surplus_watts=1500.0, _auto_cached_inputs_used=False, virtual_enable=1, auto_daytime_only=False, auto_month_windows={}, _save_runtime_state=MagicMock(), _peek_pending_relay_command=MagicMock(return_value=(None, None)))
        port = AutoDecisionPort(service)
        class DummyController:
            def clear_auto_samples(self) -> str:
                return "cleared"
            def is_within_auto_daytime_window(self) -> bool:
                return True
            def get_available_surplus_watts(self, pv_power: float, grid_power: float) -> float:
                return pv_power - grid_power
            def add_auto_sample(self, now: float, surplus_power: float, grid_power: float) -> tuple[float, float, float]:
                return now, surplus_power, grid_power
            def average_auto_metric(self, index: int) -> float:
                return float(index + 10)
        port.bind_controller(DummyController())
        self.assertEqual(port.clear_auto_samples(), "cleared")
        self.assertTrue(port.is_within_auto_daytime_window())
        self.assertEqual(port.get_available_surplus_watts(20.0, -5.0), 25.0)
        self.assertEqual(port.add_auto_sample(1.0, 2.0, 3.0), (1.0, 2.0, 3.0))
        self.assertEqual(port.average_auto_metric(4), 14.0)

    def test_auto_decision_port_missing_controller_override_raises_for_bound_methods(self) -> None:
        service = SimpleNamespace(
            auto_samples=[],
            auto_average_window_seconds=60.0,
            relay_last_changed_at=None,
            relay_last_off_at=None,
            auto_start_condition_since=None,
            auto_stop_condition_since=None,
            _last_health_reason="init",
            _last_health_code=0,
            auto_min_runtime_seconds=0.0,
            auto_min_offtime_seconds=0.0,
            _last_grid_at=None,
            auto_grid_missing_stop_seconds=60.0,
            virtual_mode=1,
            _auto_mode_cutover_pending=False,
            _ignore_min_offtime_once=False,
            _last_battery_allow_warning=None,
            auto_allow_without_battery_soc=False,
            auto_battery_scan_interval_seconds=60.0,
            auto_resume_soc=50.0,
            auto_min_soc=30.0,
            auto_stop_delay_seconds=30.0,
            auto_stop_grid_import_watts=200.0,
            auto_night_lock_stop=False,
            _last_auto_metrics={},
            started_at=0.0,
            auto_startup_warmup_seconds=0.0,
            manual_override_until=0.0,
            virtual_autostart=1,
            auto_start_delay_seconds=30.0,
            auto_start_max_grid_import_watts=50.0,
            auto_start_surplus_watts=2000.0,
            auto_stop_surplus_watts=1500.0,
            _auto_cached_inputs_used=False,
            virtual_enable=1,
            auto_daytime_only=False,
            auto_month_windows={},
            _save_runtime_state=MagicMock(),
            _peek_pending_relay_command=MagicMock(return_value=(None, None)),
        )
        port = AutoDecisionPort(service)
        with self.assertRaises(AttributeError):
            port.clear_auto_samples()

    def test_auto_decision_port_forwards_audit_and_pending_helpers(self) -> None:
        service = SimpleNamespace(auto_samples=[], auto_average_window_seconds=60.0, relay_last_changed_at=None, relay_last_off_at=None, auto_start_condition_since=None, auto_stop_condition_since=None, _last_health_reason="init", _last_health_code=0, auto_min_runtime_seconds=0.0, auto_min_offtime_seconds=0.0, _last_grid_at=None, auto_grid_missing_stop_seconds=60.0, virtual_mode=1, _auto_mode_cutover_pending=False, _ignore_min_offtime_once=False, _last_battery_allow_warning=None, auto_allow_without_battery_soc=False, auto_battery_scan_interval_seconds=60.0, auto_resume_soc=50.0, auto_min_soc=30.0, auto_stop_delay_seconds=30.0, auto_stop_grid_import_watts=200.0, auto_night_lock_stop=False, _last_auto_metrics={}, started_at=0.0, auto_startup_warmup_seconds=0.0, manual_override_until=0.0, virtual_autostart=1, auto_start_delay_seconds=30.0, auto_start_max_grid_import_watts=50.0, auto_start_surplus_watts=2000.0, auto_stop_surplus_watts=1500.0, _auto_cached_inputs_used=False, virtual_enable=1, virtual_startstop=0, auto_daytime_only=False, auto_month_windows={}, auto_audit_log=True, _save_runtime_state=MagicMock(return_value="saved"), _write_auto_audit_event=MagicMock(return_value="audited"), _peek_pending_relay_command=MagicMock(return_value=(True, 123.0)))
        port = AutoDecisionPort(service)
        self.assertEqual(port.save_runtime_state(), "saved")
        self.assertEqual(port.write_auto_audit_event("running", False), "audited")
        self.assertEqual(port.peek_pending_relay_command(), (True, 123.0))

    def test_auto_decision_port_validates_pending_relay_command_contract(self) -> None:
        service = SimpleNamespace(_peek_pending_relay_command=MagicMock(return_value=[True, 1.0]))
        port = AutoDecisionPort(service)
        with self.assertRaisesRegex(TypeError, "peek_pending_relay_command must return tuple"):
            port.peek_pending_relay_command()
        service._peek_pending_relay_command.return_value = (True,)
        with self.assertRaisesRegex(TypeError, "tuple length 2"):
            port.peek_pending_relay_command()
        service._peek_pending_relay_command.return_value = (1, 1.0)
        with self.assertRaisesRegex(TypeError, "state must be bool\\|None"):
            port.peek_pending_relay_command()
        service._peek_pending_relay_command.return_value = (True, True)
        with self.assertRaisesRegex(TypeError, "timestamp must be int\\|float\\|None"):
            port.peek_pending_relay_command()
        service._peek_pending_relay_command.return_value = (False, 2)
        self.assertEqual(port.peek_pending_relay_command(), (False, 2.0))

    def test_auto_decision_port_exposes_confirmed_pm_state_and_service_time_source(self) -> None:
        service = SimpleNamespace(auto_samples=[], auto_average_window_seconds=60.0, relay_last_changed_at=None, relay_last_off_at=None, auto_start_condition_since=None, auto_stop_condition_since=None, _last_health_reason="init", _last_health_code=0, _last_auto_state="weird", _last_auto_state_code=99, _last_pm_status_confirmed=True, _last_confirmed_pm_status={"output": True}, _last_confirmed_pm_status_at=123.0, auto_min_runtime_seconds=0.0, auto_min_offtime_seconds=0.0, _last_grid_at=None, _grid_recovery_required=False, _grid_recovery_since=None, auto_grid_missing_stop_seconds=60.0, auto_grid_recovery_start_seconds=30.0, virtual_mode=1, _auto_mode_cutover_pending=False, _ignore_min_offtime_once=False, _last_battery_allow_warning=None, auto_allow_without_battery_soc=False, auto_battery_scan_interval_seconds=60.0, auto_resume_soc=50.0, auto_min_soc=30.0, auto_high_soc_threshold=55.0, auto_high_soc_release_threshold=50.0, auto_high_soc_start_surplus_watts=1800.0, auto_high_soc_stop_surplus_watts=1200.0, auto_stop_delay_seconds=30.0, auto_stop_grid_import_watts=200.0, auto_stop_surplus_delay_seconds=30.0, auto_stop_ewma_alpha=0.35, auto_stop_ewma_alpha_stable=0.55, auto_stop_ewma_alpha_volatile=0.15, auto_stop_surplus_volatility_low_watts=150.0, auto_stop_surplus_volatility_high_watts=400.0, auto_policy=None, auto_night_lock_stop=False, _last_auto_metrics={}, _auto_high_soc_profile_active=False, _stop_smoothed_surplus_power=None, _stop_smoothed_grid_power=None, started_at=0.0, auto_startup_warmup_seconds=0.0, manual_override_until=0.0, virtual_autostart=1, auto_start_delay_seconds=30.0, auto_start_max_grid_import_watts=50.0, auto_start_surplus_watts=2000.0, auto_stop_surplus_watts=1500.0, _auto_cached_inputs_used=False, virtual_enable=1, virtual_startstop=0, auto_daytime_only=False, auto_month_windows={}, auto_audit_log=False, _save_runtime_state=MagicMock(), _set_health=MagicMock(return_value="health-set"), _peek_pending_relay_command=MagicMock(return_value=(None, None)), _time_now=MagicMock(return_value=456.0), _normalize_mode=MagicMock(return_value=2))
        port = AutoDecisionPort(service)
        self.assertEqual(port._last_confirmed_pm_status, {"output": True})
        self.assertEqual(port._time_now(), 456.0)
        self.assertEqual(port.set_health("running", False, relay_intent=True), "health-set")
        port.virtual_mode = cast(Any, "5")
        port.virtual_autostart = cast(Any, "0")
        port.virtual_enable = 7
        port.virtual_startstop = cast(Any, "bad")
        self.assertEqual(service.virtual_mode, 2)
        self.assertEqual(service.virtual_autostart, 0)
        self.assertEqual(service.virtual_enable, 1)
        self.assertEqual(service.virtual_startstop, 0)

    def test_update_cycle_port_forwards_mutable_runtime_fields(self) -> None:
        service = SimpleNamespace(_startup_manual_target=None, virtual_mode=1, auto_shelly_soft_fail_seconds=10.0, _last_health_reason="init", _last_health_code=0, _last_auto_state="weird", _last_auto_state_code=99, charging_started_at=None, energy_at_start=0.0, virtual_startstop=0, virtual_enable=1, phase="L1", voltage_mode="phase", last_status=0, _last_pm_status=None, _last_pm_status_at=None, _last_voltage=None, auto_input_cache_seconds=120.0, _auto_cached_inputs_used=False, _error_state={"cache_hits": 0}, _last_pv_value=None, _last_pv_at=None, _last_grid_value=None, _last_grid_at=None, _last_battery_soc_value=None, _last_battery_soc_at=None, auto_audit_log=False, _last_auto_metrics={}, charging_threshold_watts=100.0, idle_status=6, _last_successful_update_at=None, _last_recovery_attempt_at=None, last_update=0.0, service_name="svc", _dbusservice={"/Ac/Power": 0.0}, _mode_uses_auto_logic=MagicMock(return_value=True), _normalize_mode=MagicMock(return_value=2), _last_pm_status_confirmed=False, learned_charge_power_state="mystery")
        port = UpdateCyclePort(service)
        port.last_status = 2
        port._startup_manual_target = cast(Any, 1)
        port.virtual_mode = cast(Any, "5")
        port.virtual_startstop = cast(Any, "0")
        port.virtual_enable = 7
        port._last_pm_status_confirmed = cast(Any, 1)
        port.learned_charge_power_state = "stable"
        self.assertEqual(service.last_status, 2)
        self.assertTrue(service._startup_manual_target)
        self.assertEqual(service.virtual_mode, 2)
        self.assertEqual(service.virtual_startstop, 0)
        self.assertEqual(service.virtual_enable, 1)
        self.assertTrue(service._last_pm_status_confirmed)
        self.assertEqual(service.learned_charge_power_state, "stable")

    def test_update_cycle_port_property_normalizers_cover_remaining_paths(self) -> None:
        service = SimpleNamespace(
            _startup_manual_target="bad",
            virtual_mode="bad",
            virtual_startstop="bad",
            virtual_enable="bad",
            _last_auto_state="weird",
            _last_auto_state_code=99,
            _last_pm_status_confirmed=0,
            learned_charge_power_state="weird",
        )
        port = UpdateCyclePort(service)

        self.assertFalse(port._startup_manual_target)
        self.assertEqual(port.virtual_mode, 0)
        self.assertEqual(port.virtual_startstop, 1)
        self.assertEqual(port.virtual_enable, 1)
        self.assertEqual(port._last_auto_state, "idle")
        self.assertEqual(port._last_auto_state_code, 0)
        self.assertFalse(port._last_pm_status_confirmed)
        self.assertEqual(port.learned_charge_power_state, "unknown")

        port._last_auto_state = "waiting-surplus"
        port._last_auto_state_code = 5
        self.assertEqual(service._last_auto_state, "idle")
        self.assertEqual(service._last_auto_state_code, 0)

    def test_update_cycle_port_property_getters_cover_defaults_and_valid_pairs(self) -> None:
        service = SimpleNamespace(
            _startup_manual_target=1,
            virtual_mode=1,
            virtual_startstop=None,
            virtual_enable=None,
            _last_auto_state="waiting",
            _last_auto_state_code=4,
            _last_pm_status_confirmed=True,
            learned_charge_power_state="learning",
        )
        port = UpdateCyclePort(service)

        self.assertTrue(port._startup_manual_target)
        self.assertEqual(port.virtual_mode, 1)
        self.assertEqual(port.virtual_startstop, 1)
        self.assertEqual(port.virtual_enable, 1)
        self.assertEqual(port._last_auto_state, "waiting")
        self.assertEqual(port._last_auto_state_code, 1)
        self.assertTrue(port._last_pm_status_confirmed)
        self.assertEqual(port.learned_charge_power_state, "learning")

    def test_auto_decision_port_property_getters_cover_defaults_and_valid_state_pairs(self) -> None:
        service = SimpleNamespace(
            auto_samples=[],
            auto_average_window_seconds=60.0,
            relay_last_changed_at=None,
            relay_last_off_at=None,
            auto_start_condition_since=None,
            auto_stop_condition_since=None,
            _last_health_reason="init",
            _last_health_code=0,
            auto_min_runtime_seconds=0.0,
            auto_min_offtime_seconds=0.0,
            _last_grid_at=None,
            auto_grid_missing_stop_seconds=60.0,
            virtual_mode=1,
            _auto_mode_cutover_pending=False,
            _ignore_min_offtime_once=False,
            _last_battery_allow_warning=None,
            auto_allow_without_battery_soc=False,
            auto_battery_scan_interval_seconds=60.0,
            auto_resume_soc=50.0,
            auto_min_soc=30.0,
            auto_stop_delay_seconds=30.0,
            auto_stop_grid_import_watts=200.0,
            auto_night_lock_stop=False,
            _last_auto_metrics={},
            started_at=0.0,
            auto_startup_warmup_seconds=0.0,
            manual_override_until=0.0,
            virtual_autostart=1,
            auto_start_delay_seconds=30.0,
            auto_start_max_grid_import_watts=50.0,
            auto_start_surplus_watts=2000.0,
            auto_stop_surplus_watts=1500.0,
            _auto_cached_inputs_used=False,
            virtual_enable=1,
            virtual_startstop=None,
            auto_daytime_only=False,
            auto_month_windows={},
            _save_runtime_state=MagicMock(),
            _peek_pending_relay_command=MagicMock(return_value=(None, None)),
            _last_auto_state="waiting",
            _last_auto_state_code=4,
        )
        port = AutoDecisionPort(service)
        self.assertEqual(port.virtual_startstop, 1)
        self.assertEqual(port._last_auto_state, "waiting")
        self.assertEqual(port._last_auto_state_code, 1)

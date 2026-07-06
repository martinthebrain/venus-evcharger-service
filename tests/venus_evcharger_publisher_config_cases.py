# SPDX-License-Identifier: GPL-3.0-or-later
import configparser
from unittest.mock import patch

from venus_evcharger.core.common import DEFAULT_SCHEDULED_ENABLED_DAYS

from tests.venus_evcharger_publisher_support import (
    DbusPublishController,
    DbusPublishControllerTestCase,
    MagicMock,
    SimpleNamespace,
)


class TestDbusPublishControllerConfig(DbusPublishControllerTestCase):
    def test_config_values_use_stable_learned_current_by_default(self) -> None:
        service = SimpleNamespace(
            virtual_mode=1,
            virtual_autostart=1,
            virtual_enable=1,
            virtual_set_current=16.0,
            requested_phase_selection="P1_P2",
            active_phase_selection="P1",
            supported_phase_selections=("P1", "P1_P2"),
            min_current=6.0,
            max_current=16.0,
            auto_start_surplus_watts=1850.0,
            auto_stop_surplus_watts=1350.0,
            auto_min_soc=40.0,
            auto_resume_soc=50.0,
            auto_start_delay_seconds=10.0,
            auto_stop_delay_seconds=30.0,
            auto_scheduled_enabled_days="Mon,Tue,Wed,Thu,Fri",
            auto_scheduled_night_start_delay_seconds=3600.0,
            auto_scheduled_latest_end_time="06:30",
            auto_scheduled_night_current_amps=13.0,
            auto_dbus_backoff_base_seconds=5.0,
            auto_dbus_backoff_max_seconds=60.0,
            auto_grid_recovery_start_seconds=14.0,
            auto_stop_surplus_delay_seconds=45.0,
            auto_stop_surplus_volatility_low_watts=80.0,
            auto_stop_surplus_volatility_high_watts=240.0,
            auto_reference_charge_power_watts=2100.0,
            auto_learn_charge_power_enabled=True,
            auto_learn_charge_power_min_watts=1400.0,
            auto_learn_charge_power_alpha=0.25,
            auto_learn_charge_power_start_delay_seconds=12.0,
            auto_learn_charge_power_window_seconds=180.0,
            auto_learn_charge_power_max_age_seconds=21600.0,
            auto_phase_switching_enabled=True,
            auto_phase_prefer_lowest_when_idle=False,
            auto_phase_upshift_delay_seconds=120.0,
            auto_phase_downshift_delay_seconds=30.0,
            auto_phase_upshift_headroom_watts=250.0,
            auto_phase_downshift_margin_watts=150.0,
            auto_phase_mismatch_retry_seconds=300.0,
            auto_phase_mismatch_lockout_count=3,
            auto_phase_mismatch_lockout_seconds=1800.0,
            learned_charge_power_state="stable",
            learned_charge_power_watts=2990.0,
            learned_charge_power_updated_at=95.0,
            learned_charge_power_phase="L1",
            learned_charge_power_voltage=230.0,
            phase="L1",
            voltage_mode="phase",
            last_status=6,
        )
        controller = DbusPublishController(service, self._age_seconds)

        values = controller._config_values(1, now=100.0)

        self.assertEqual(
            values,
            {
                "connected": 1,
                "status": 6,
                "mode": 1,
                "auto_start": 1,
                "start_stop": 1,
                "enable": 1,
                "phase_selection": "P1_P2",
                "phase_selection_active": "P1",
                "supported_phase_selections": "P1,P1_P2",
                "set_current": 13.0,
                "min_current": 6.0,
                "max_current": 16.0,
                "auto_start_surplus_watts": 1850.0,
                "auto_stop_surplus_watts": 1350.0,
                "auto_min_soc": 40.0,
                "auto_resume_soc": 50.0,
                "auto_start_delay_seconds": 10.0,
                "auto_stop_delay_seconds": 30.0,
                "auto_scheduled_enabled_days": "Mon,Tue,Wed,Thu,Fri",
                "auto_scheduled_fallback_delay_seconds": 3600.0,
                "auto_scheduled_latest_end_time": "06:30",
                "auto_scheduled_night_current": 13.0,
                "auto_dbus_backoff_base_seconds": 5.0,
                "auto_dbus_backoff_max_seconds": 60.0,
                "auto_grid_recovery_start_seconds": 14.0,
                "auto_stop_surplus_delay_seconds": 45.0,
                "auto_stop_surplus_volatility_low_watts": 80.0,
                "auto_stop_surplus_volatility_high_watts": 240.0,
                "auto_reference_charge_power_watts": 2100.0,
                "auto_learn_charge_power_enabled": 1,
                "auto_learn_charge_power_min_watts": 1400.0,
                "auto_learn_charge_power_alpha": 0.25,
                "auto_learn_charge_power_start_delay_seconds": 12.0,
                "auto_learn_charge_power_window_seconds": 180.0,
                "auto_learn_charge_power_max_age_seconds": 21600.0,
                "auto_phase_switching": 1,
                "auto_phase_prefer_lowest_when_idle": 0,
                "auto_phase_upshift_delay_seconds": 120.0,
                "auto_phase_downshift_delay_seconds": 30.0,
                "auto_phase_upshift_headroom_watts": 250.0,
                "auto_phase_downshift_margin_watts": 150.0,
                "auto_phase_mismatch_retry_seconds": 300.0,
                "auto_phase_mismatch_lockout_count": 3,
                "auto_phase_mismatch_lockout_seconds": 1800.0,
            },
        )

    def test_config_values_can_disable_learned_current_display(self) -> None:
        service = SimpleNamespace(
            virtual_mode=1,
            virtual_autostart=1,
            virtual_enable=1,
            virtual_set_current=16.0,
            requested_phase_selection="P1",
            active_phase_selection="P1",
            supported_phase_selections=("P1",),
            min_current=6.0,
            max_current=16.0,
            display_learned_set_current=0,
            learned_charge_power_state="stable",
            learned_charge_power_watts=2990.0,
            learned_charge_power_updated_at=95.0,
            learned_charge_power_phase="L1",
            learned_charge_power_voltage=230.0,
            phase="L1",
            voltage_mode="phase",
            auto_learn_charge_power_max_age_seconds=21600.0,
        )
        controller = DbusPublishController(service, self._age_seconds)

        values = controller._config_values(1, now=100.0)

        self.assertEqual(values["set_current"], 16.0)

    def test_config_values_publish_exact_default_surface_for_minimal_service(self) -> None:
        service = SimpleNamespace(virtual_set_current=11.0)
        controller = DbusPublishController(service, self._age_seconds)

        values = controller._config_values(0, now=100.0)

        self.assertEqual(
            values,
            {
                "connected": 1,
                "status": 0,
                "mode": 0,
                "auto_start": 1,
                "start_stop": 0,
                "enable": 1,
                "phase_selection": "P1",
                "phase_selection_active": "P1",
                "supported_phase_selections": "P1",
                "set_current": 11.0,
                "min_current": 0.0,
                "max_current": 0.0,
                "auto_start_surplus_watts": 0.0,
                "auto_stop_surplus_watts": 0.0,
                "auto_min_soc": 0.0,
                "auto_resume_soc": 0.0,
                "auto_start_delay_seconds": 0.0,
                "auto_stop_delay_seconds": 0.0,
                "auto_scheduled_enabled_days": "Mon,Tue,Wed,Thu,Fri",
                "auto_scheduled_fallback_delay_seconds": 0.0,
                "auto_scheduled_latest_end_time": "06:30",
                "auto_scheduled_night_current": 0.0,
                "auto_dbus_backoff_base_seconds": 0.0,
                "auto_dbus_backoff_max_seconds": 0.0,
                "auto_grid_recovery_start_seconds": 0.0,
                "auto_stop_surplus_delay_seconds": 0.0,
                "auto_stop_surplus_volatility_low_watts": 0.0,
                "auto_stop_surplus_volatility_high_watts": 0.0,
                "auto_reference_charge_power_watts": 0.0,
                "auto_learn_charge_power_enabled": 1,
                "auto_learn_charge_power_min_watts": 0.0,
                "auto_learn_charge_power_alpha": 0.0,
                "auto_learn_charge_power_start_delay_seconds": 0.0,
                "auto_learn_charge_power_window_seconds": 0.0,
                "auto_learn_charge_power_max_age_seconds": 0.0,
                "auto_phase_switching": 1,
                "auto_phase_prefer_lowest_when_idle": 1,
                "auto_phase_upshift_delay_seconds": 0.0,
                "auto_phase_downshift_delay_seconds": 0.0,
                "auto_phase_upshift_headroom_watts": 0.0,
                "auto_phase_downshift_margin_watts": 0.0,
                "auto_phase_mismatch_retry_seconds": 0.0,
                "auto_phase_mismatch_lockout_count": 0,
                "auto_phase_mismatch_lockout_seconds": 0.0,
            },
        )

    def test_config_values_keep_actual_set_current_when_native_charger_backend_is_present(self) -> None:
        service = SimpleNamespace(
            virtual_mode=1,
            virtual_autostart=1,
            virtual_enable=1,
            virtual_set_current=11.0,
            requested_phase_selection="P1",
            active_phase_selection="P1",
            supported_phase_selections=("P1",),
            min_current=6.0,
            max_current=16.0,
            learned_charge_power_state="stable",
            learned_charge_power_watts=2990.0,
            learned_charge_power_updated_at=95.0,
            learned_charge_power_phase="L1",
            learned_charge_power_voltage=230.0,
            phase="L1",
            voltage_mode="phase",
            auto_learn_charge_power_max_age_seconds=21600.0,
            _charger_backend=SimpleNamespace(set_current=MagicMock()),
        )
        controller = DbusPublishController(service, self._age_seconds)

        values = controller._config_values(1, now=100.0)

        self.assertEqual(values["set_current"], 11.0)

    def test_config_values_degrade_supported_phase_selections_while_lockout_is_active(self) -> None:
        service = SimpleNamespace(
            virtual_mode=1,
            virtual_autostart=1,
            virtual_enable=1,
            virtual_set_current=16.0,
            requested_phase_selection="P1",
            active_phase_selection="P1",
            supported_phase_selections=("P1", "P1_P2", "P1_P2_P3"),
            min_current=6.0,
            max_current=16.0,
            _phase_switch_lockout_selection="P1_P2_P3",
            _phase_switch_lockout_until=140.0,
        )
        controller = DbusPublishController(service, self._age_seconds)

        values = controller._config_values(1, now=100.0)

        self.assertEqual(values["supported_phase_selections"], "P1,P1_P2")

    def test_config_values_prefer_fresh_native_charger_readback_for_gui_state(self) -> None:
        service = SimpleNamespace(
            virtual_mode=1,
            virtual_autostart=1,
            virtual_enable=1,
            virtual_set_current=16.0,
            requested_phase_selection="P1",
            active_phase_selection="P1",
            supported_phase_selections=("P1",),
            min_current=6.0,
            max_current=16.0,
            learned_charge_power_state="stable",
            learned_charge_power_watts=2990.0,
            learned_charge_power_updated_at=95.0,
            learned_charge_power_phase="L1",
            learned_charge_power_voltage=230.0,
            phase="L1",
            voltage_mode="phase",
            auto_learn_charge_power_max_age_seconds=21600.0,
            auto_shelly_soft_fail_seconds=10.0,
            _charger_backend=SimpleNamespace(set_current=MagicMock()),
            _last_charger_state_enabled=False,
            _last_charger_state_current_amps=12.5,
            _last_charger_state_status="paused",
            _last_charger_state_fault="vehicle-sleeping",
            _last_charger_state_at=99.5,
        )
        controller = DbusPublishController(service, self._age_seconds)

        values = controller._config_values(1, now=100.0)

        self.assertEqual(values["enable"], 0)
        self.assertEqual(values["start_stop"], 0)
        self.assertEqual(values["set_current"], 12.5)

    def test_config_values_publish_native_enabled_and_non_default_control_surface(self) -> None:
        service = SimpleNamespace(
            virtual_mode=1,
            virtual_autostart=0,
            virtual_enable=0,
            virtual_set_current=10.0,
            requested_phase_selection="P1",
            active_phase_selection="P1_P2",
            supported_phase_selections=("P1", "P1_P2"),
            auto_scheduled_enabled_days="Sat,Sun",
            auto_scheduled_latest_end_time="05:15",
            auto_learn_charge_power_enabled=False,
            auto_phase_switching_enabled=False,
            _charger_backend=SimpleNamespace(set_current=MagicMock()),
            _last_charger_state_enabled=True,
            _last_charger_state_current_amps=14.0,
            _last_charger_state_at=99.5,
        )
        controller = DbusPublishController(service, self._age_seconds)

        values = controller._config_values(0, now=100.0)

        self.assertEqual(values["auto_start"], 0)
        self.assertEqual(values["enable"], 1)
        self.assertEqual(values["start_stop"], 1)
        self.assertEqual(values["set_current"], 14.0)
        self.assertEqual(values["phase_selection_active"], "P1_P2")
        self.assertEqual(values["auto_scheduled_enabled_days"], "Sat,Sun")
        self.assertEqual(values["auto_scheduled_latest_end_time"], "05:15")
        self.assertEqual(values["auto_learn_charge_power_enabled"], 0)
        self.assertEqual(values["auto_phase_switching"], 0)

    def test_config_values_publish_live_connected_display(self) -> None:
        service = SimpleNamespace(
            virtual_mode=1,
            virtual_autostart=1,
            virtual_enable=1,
            virtual_set_current=16.0,
            requested_phase_selection="P1",
            active_phase_selection="P1",
            supported_phase_selections=("P1",),
            topology_configured=True,
            _shelly_state="online",
            _last_pm_status_at=90.0,
            auto_shelly_soft_fail_seconds=10.0,
        )
        controller = DbusPublishController(service, self._age_seconds)

        self.assertEqual(controller._config_values(1, now=100.0)["connected"], 1)
        service._shelly_state = "offline"
        self.assertEqual(controller._config_values(1, now=100.0)["connected"], 0)
        service._shelly_state = ""
        self.assertEqual(controller._config_values(1, now=109.0)["connected"], 1)
        self.assertEqual(controller._config_values(1, now=120.1)["connected"], 0)
        service.topology_configured = False
        self.assertEqual(controller._config_values(1, now=100.0)["connected"], 0)

    def test_config_values_publish_connected_from_native_readback_and_transport_errors(self) -> None:
        service = SimpleNamespace(
            virtual_mode=1,
            virtual_autostart=1,
            virtual_enable=1,
            virtual_set_current=16.0,
            requested_phase_selection="P1",
            active_phase_selection="P1",
            supported_phase_selections=("P1",),
            topology_configured=True,
            _shelly_state="unknown",
            _last_pm_status_at=None,
            _last_charger_state_at=98.0,
            _last_charger_transport_reason=None,
            _last_charger_transport_at=None,
            auto_shelly_soft_fail_seconds=10.0,
        )
        controller = DbusPublishController(service, self._age_seconds)

        self.assertEqual(controller._config_values(1, now=100.0)["connected"], 1)
        service._last_charger_state_at = 70.0
        service._last_charger_transport_reason = "offline"
        service._last_charger_transport_at = 99.0
        self.assertEqual(controller._config_values(1, now=100.0)["connected"], 0)
        service._last_charger_transport_at = 70.0
        self.assertEqual(controller._config_values(1, now=100.0)["connected"], 1)

    def test_config_helpers_cover_fault_and_contactor_count_fallbacks(self) -> None:
        service = SimpleNamespace(
            backend_mode="",
            meter_backend_type=None,
            _last_health_reason="contactor-lockout-open",
            _contactor_lockout_reason="",
            _contactor_fault_active_reason="contactor-suspected-open",
            _contactor_fault_counts=[],
        )
        controller = DbusPublishController(service, self._age_seconds)

        self.assertEqual(controller._backend_mode_value(service), "combined")
        self.assertEqual(controller._backend_type_value(service, "meter_backend_type", "meter"), "meter")
        self.assertEqual(controller._fault_reason(service), "contactor-lockout-open")
        self.assertEqual(controller._contactor_fault_count(service), 0)

    def test_backend_helpers_prefer_config_over_conflicting_legacy_attributes(self) -> None:
        config = configparser.ConfigParser()
        config["Backends"] = {
            "Mode": "split",
            "MeterType": "template_meter",
            "SwitchType": "switch_group",
            "ChargerType": "goe_charger",
        }
        service = SimpleNamespace(
            config=config,
            backend_mode="combined",
            meter_backend_type="shelly_combined",
            switch_backend_type="shelly_combined",
            charger_backend_type=None,
        )
        controller = DbusPublishController(service, self._age_seconds)

        self.assertEqual(controller._backend_mode_value(service), "split")
        self.assertEqual(controller._backend_type_value(service, "meter_backend_type", "meter"), "template_meter")
        self.assertEqual(controller._backend_type_value(service, "switch_backend_type", "switch"), "switch_group")
        self.assertEqual(controller._backend_type_value(service, "charger_backend_type", "na"), "goe_charger")

    def test_config_values_convert_stable_three_phase_line_voltage_to_display_current(self) -> None:
        service = SimpleNamespace(
            virtual_mode=1,
            virtual_autostart=1,
            virtual_enable=1,
            virtual_set_current=16.0,
            requested_phase_selection="P1_P2_P3",
            active_phase_selection="P1_P2_P3",
            supported_phase_selections=("P1", "P1_P2_P3"),
            min_current=6.0,
            max_current=16.0,
            learned_charge_power_state="stable",
            learned_charge_power_watts=10400.0,
            learned_charge_power_updated_at=95.0,
            learned_charge_power_phase="3P",
            learned_charge_power_voltage=400.0,
            phase="3P",
            voltage_mode="line_to_line",
            auto_learn_charge_power_max_age_seconds=21600.0,
        )
        controller = DbusPublishController(service, self._age_seconds)

        values = controller._config_values(1, now=100.0)

        self.assertEqual(values["set_current"], 15.0)

    def test_config_helper_contracts_cover_backend_phase_contactor_and_schedule_edges(self) -> None:
        controller = DbusPublishController(SimpleNamespace(), self._age_seconds)
        service = SimpleNamespace(
            topology_configured=True,
            host_configured=False,
            auto_shelly_soft_fail_seconds=4.0,
            _shelly_state=" degraded ",
            _last_confirmed_pm_status_at=None,
            _last_pm_status_at=92.5,
            _last_charger_state_at=80.0,
            _shelly_last_ok_at=None,
            _last_charger_transport_reason="timeout",
            _last_charger_transport_at=97.0,
            backend_mode="",
            meter_backend_type=" custom-meter ",
            _charger_target_current_amps=None,
            _last_auto_metrics={
                "phase_target": " P1_P2 ",
                "phase_threshold_watts": 1750.5,
            },
            _last_health_reason="phase-switch-mismatch",
            virtual_mode=2,
            auto_month_windows={},
            auto_scheduled_enabled_days="Mon,Tue,Wed,Thu,Fri",
            auto_scheduled_night_start_delay_seconds=3600.0,
            auto_scheduled_latest_end_time="06:30",
            _last_auto_state="recovery",
            _last_auto_state_code=0,
            _last_confirmed_pm_status={"_phase_selection": " P1_P2 ", "output": True},
            _last_charger_state_phase_selection="P1",
            _phase_switch_mismatch_active=False,
            _phase_switch_lockout_selection="P1_P2",
            _phase_switch_lockout_reason=" mismatch-threshold ",
            _phase_switch_lockout_until=150.0,
            supported_phase_selections=("P1", "P1_P2"),
            _last_switch_feedback_closed=False,
            _last_switch_interlock_ok=None,
            _contactor_fault_counts={
                "contactor-suspected-open": 7,
                "phase-switch-mismatch": 5,
            },
            _contactor_lockout_reason=" contactor-suspected-open ",
            _contactor_lockout_source=" count-threshold ",
            _contactor_fault_active_reason="phase-switch-mismatch",
        )

        self.assertTrue(controller._service_configured_for_connected(service))
        self.assertFalse(controller._service_configured_for_connected(SimpleNamespace(topology_configured=False)))
        self.assertFalse(controller._service_configured_for_connected(SimpleNamespace(host_configured=False)))
        self.assertTrue(controller._service_configured_for_connected(SimpleNamespace(host_configured=True)))
        self.assertTrue(controller._service_configured_for_connected(SimpleNamespace()))
        self.assertTrue(controller._connected_state_is_live("online"))
        self.assertTrue(controller._connected_state_is_live("degraded"))
        self.assertFalse(controller._connected_state_is_live("offline"))
        self.assertFalse(controller._connected_state_is_live("unknown"))
        self.assertEqual(controller._explicit_connected_state_display("degraded"), 1)
        self.assertIsNone(controller._explicit_connected_state_display("unknown"))
        self.assertEqual(controller._backend_reachable_display(service, 100.0), 1)
        service._shelly_state = "offline"
        self.assertEqual(controller._backend_reachable_display(service, 100.0), 0)
        service._shelly_state = ""
        self.assertEqual(controller._implicit_connected_display(service, 100.0), 1)
        self.assertTrue(controller._fresh_backend_transport_problem(service, 100.0))
        for timestamp_name in (
            "_last_confirmed_pm_status_at",
            "_last_pm_status_at",
            "_last_charger_state_at",
            "_shelly_last_ok_at",
        ):
            timestamp_service = SimpleNamespace(auto_shelly_soft_fail_seconds=4.0)
            setattr(timestamp_service, timestamp_name, 99.0)
            self.assertTrue(controller._fresh_backend_readback_present(timestamp_service, 100.0))
        self.assertFalse(controller._fresh_backend_readback_present(SimpleNamespace(auto_shelly_soft_fail_seconds=4.0), 100.0))
        self.assertTrue(controller._connected_timestamp_fresh(SimpleNamespace(auto_shelly_soft_fail_seconds=4.0, marker=92.0), "marker", 100.0))
        self.assertFalse(controller._connected_timestamp_fresh(SimpleNamespace(auto_shelly_soft_fail_seconds=4.0, marker=91.9), "marker", 100.0))
        self.assertEqual(controller._implicit_connected_display(SimpleNamespace(_last_pm_status_at=None), 100.0), 1)
        self.assertEqual(
            controller._implicit_connected_display(
                SimpleNamespace(_last_pm_status_at=92.0, auto_shelly_soft_fail_seconds=4.0),
                100.0,
            ),
            1,
        )
        self.assertEqual(
            controller._implicit_connected_display(
                SimpleNamespace(_last_pm_status_at=91.9, auto_shelly_soft_fail_seconds=4.0),
                100.0,
            ),
            0,
        )
        self.assertEqual(controller._connected_stale_after_seconds(service), 8.0)
        self.assertEqual(controller._connected_stale_after_seconds(SimpleNamespace()), 20.0)
        service.auto_shelly_soft_fail_seconds = 0.2
        self.assertEqual(controller._connected_stale_after_seconds(service), 1.0)
        service.auto_shelly_soft_fail_seconds = 4.0

        self.assertEqual(controller._backend_mode_value(service), "combined")
        self.assertEqual(controller._backend_type_value(service, "meter_backend_type", "meter"), "custom-meter")
        self.assertEqual(controller._backend_type_value(service, "unknown_backend_type", "fallback"), "fallback")
        self.assertEqual(controller._backend_type_value(SimpleNamespace(), "unknown_backend_type"), "")
        self.assertEqual(controller._charger_current_target_value(service), -1.0)
        service._charger_target_current_amps = 13.5
        self.assertEqual(controller._charger_current_target_value(service), 13.5)
        self.assertEqual(controller._auto_phase_metric_text(service, "phase_target"), "P1_P2")
        self.assertEqual(controller._auto_phase_metric_float(service, "phase_threshold_watts"), 1750.5)
        service._last_health_reason = "contactor-lockout-open"
        self.assertEqual(controller._fault_reason(service), "contactor-lockout-open")
        self.assertEqual(controller._fault_active(service), 1)

        snapshot = controller._scheduled_snapshot(service, 1719817200.0)
        self.assertIsNotNone(snapshot)
        self.assertEqual(snapshot.state, "after-latest-end")
        self.assertEqual(snapshot.reason, "latest-end-reached")
        self.assertEqual(snapshot.target_day_label, "Mon")
        service.auto_month_windows = {7: ((9, 15), (17, 45))}
        service.auto_scheduled_enabled_days = "Tue"
        service.auto_scheduled_night_start_delay_seconds = 5400.0
        service.auto_scheduled_latest_end_time = "05:15"
        non_default_snapshot = controller._scheduled_snapshot(service, 1782932400.0)
        self.assertIsNotNone(non_default_snapshot)
        self.assertEqual(non_default_snapshot.target_day_label, "Thu")
        self.assertFalse(non_default_snapshot.target_day_enabled)
        self.assertEqual(non_default_snapshot.fallback_start_text, "2026-07-01 19:15")
        self.assertEqual(non_default_snapshot.boost_until_text, "2026-07-02 05:15")
        service.auto_month_windows = {}
        service.auto_scheduled_enabled_days = "Mon,Tue,Wed,Thu,Fri"
        service.auto_scheduled_night_start_delay_seconds = 3600.0
        service.auto_scheduled_latest_end_time = "06:30"
        service.virtual_mode = 1
        self.assertIsNone(controller._scheduled_snapshot(service, 1719817200.0))
        service.virtual_mode = 2

        self.assertEqual(controller._recovery_active(service), 1)
        service._last_auto_state = "idle"
        self.assertEqual(controller._recovery_active(service), 0)
        service._last_auto_state = "recovery"
        self.assertEqual(controller._observed_phase_value(service), "P1_P2")
        service._last_confirmed_pm_status = {}
        self.assertEqual(controller._observed_phase_value(service), "P1")
        service._last_health_reason = "phase-switch-mismatch"
        self.assertEqual(controller._phase_switch_mismatch_active(service), 1)
        self.assertEqual(controller._phase_switch_lockout_active(service, 100.0), 1)
        self.assertEqual(controller._phase_switch_lockout_target(service, 100.0), "P1_P2")
        self.assertEqual(controller._phase_switch_lockout_reason(service, 100.0), "mismatch-threshold")
        self.assertEqual(
            controller._phase_switch_lockout_active(
                SimpleNamespace(_phase_switch_lockout_selection=None, _phase_switch_lockout_until=150.0),
                100.0,
            ),
            0,
        )
        self.assertEqual(
            controller._phase_switch_lockout_active(
                SimpleNamespace(_phase_switch_lockout_selection="P1_P2", _phase_switch_lockout_until=None),
                100.0,
            ),
            0,
        )
        self.assertEqual(controller._phase_supported_configured(service), "P1,P1_P2")
        self.assertEqual(controller._phase_supported_effective(service, 100.0), "P1")
        self.assertEqual(controller._phase_degraded_active(service, 100.0), 1)
        service._phase_switch_lockout_until = 90.0
        self.assertEqual(controller._phase_switch_lockout_active(service, 100.0), 0)
        self.assertEqual(controller._phase_supported_effective(service, 100.0), "P1,P1_P2")

        service._last_confirmed_pm_status = {"output": True}
        self.assertEqual(controller._switch_feedback_closed(service), 0)
        self.assertEqual(controller._switch_interlock_ok(service), -1)
        self.assertEqual(controller._switch_feedback_mismatch(service), 1)
        self.assertEqual(
            controller._switch_feedback_mismatch(SimpleNamespace(_last_switch_feedback_closed=True)),
            1,
        )
        service._last_switch_feedback_closed = True
        service._last_confirmed_pm_status = {"output": True}
        self.assertEqual(controller._switch_feedback_mismatch(service), 0)
        service._last_confirmed_pm_status = "not-a-mapping"
        self.assertEqual(controller._switch_feedback_mismatch(service), 1)
        service._last_switch_feedback_closed = None
        self.assertEqual(controller._switch_feedback_mismatch(service), 0)
        service._last_health_reason = "contactor-feedback-mismatch"
        self.assertEqual(controller._switch_feedback_mismatch(service), 1)
        self.assertEqual(controller._contactor_suspected_open(service), 0)
        service._last_health_reason = "contactor-suspected-open"
        self.assertEqual(controller._contactor_suspected_open(service), 1)
        self.assertEqual(controller._contactor_suspected_welded(service), 0)
        service._last_health_reason = "contactor-suspected-welded"
        self.assertEqual(controller._contactor_suspected_welded(service), 1)
        self.assertEqual(controller._contactor_lockout_reason(service), "contactor-suspected-open")
        self.assertEqual(controller._contactor_lockout_active(service), 1)
        self.assertEqual(controller._contactor_lockout_source(service), "count-threshold")
        self.assertEqual(controller._contactor_fault_count(service), 7)
        service._contactor_lockout_reason = ""
        self.assertEqual(controller._contactor_fault_count(service), 5)
        service._contactor_fault_counts = {}
        self.assertEqual(controller._contactor_fault_count(service), 0)

    def test_config_helper_contracts_cover_missing_runtime_defaults(self) -> None:
        controller = DbusPublishController(SimpleNamespace(), self._age_seconds)

        snapshot = controller._scheduled_snapshot(SimpleNamespace(virtual_mode=2), 1719817200.0)
        self.assertIsNotNone(snapshot)
        self.assertEqual(snapshot.state, "after-latest-end")
        self.assertEqual(snapshot.reason, "latest-end-reached")
        self.assertFalse(snapshot.night_boost_active)
        self.assertEqual(snapshot.target_day_index, 0)
        self.assertEqual(snapshot.target_day_label, "Mon")
        self.assertEqual(snapshot.target_date_text, "2024-07-01")
        self.assertTrue(snapshot.target_day_enabled)
        self.assertEqual(snapshot.fallback_start_text, "2024-06-30 19:00")
        self.assertEqual(snapshot.boost_until_text, "2024-07-01 06:30")
        boundary_snapshot = controller._scheduled_snapshot(SimpleNamespace(virtual_mode=2), 1719774000.0)
        self.assertIsNotNone(boundary_snapshot)
        self.assertEqual(boundary_snapshot.state, "night-boost")
        self.assertEqual(boundary_snapshot.reason, "night-boost-window")
        self.assertTrue(boundary_snapshot.night_boost_active)

        default_service = SimpleNamespace()
        self.assertIsNone(controller._scheduled_snapshot(default_service, 1719817200.0))
        self.assertEqual(controller._recovery_active(default_service), 0)
        self.assertEqual(controller._phase_switch_mismatch_active(default_service), 0)
        self.assertEqual(controller._phase_switch_lockout_active(default_service, 100.0), 0)
        self.assertEqual(controller._phase_switch_lockout_target(default_service, 100.0), "")
        self.assertEqual(controller._phase_switch_lockout_reason(default_service, 100.0), "")
        self.assertEqual(controller._phase_supported_configured(default_service), "P1")
        self.assertEqual(controller._phase_supported_effective(default_service, 100.0), "P1")
        self.assertEqual(controller._phase_degraded_active(default_service, 100.0), 0)
        self.assertEqual(controller._switch_feedback_closed(default_service), -1)
        self.assertEqual(controller._switch_interlock_ok(default_service), -1)
        self.assertEqual(controller._switch_feedback_mismatch(default_service), 0)
        self.assertEqual(controller._contactor_suspected_open(default_service), 0)
        self.assertEqual(controller._contactor_suspected_welded(default_service), 0)
        self.assertEqual(controller._contactor_lockout_reason(default_service), "")
        self.assertEqual(controller._contactor_lockout_active(default_service), 0)
        self.assertEqual(controller._contactor_lockout_source(default_service), "")
        self.assertEqual(controller._contactor_fault_count(default_service), 0)

        expired_lockout = SimpleNamespace(
            _phase_switch_lockout_selection="P1_P2",
            _phase_switch_lockout_reason="late",
            _phase_switch_lockout_until=100.0,
            supported_phase_selections=("P1", "P1_P2"),
        )
        self.assertEqual(controller._phase_switch_lockout_active(expired_lockout, 100.0), 0)
        self.assertEqual(controller._phase_switch_lockout_target(expired_lockout, 100.0), "")
        self.assertEqual(controller._phase_switch_lockout_reason(expired_lockout, 100.0), "")
        self.assertEqual(controller._phase_supported_effective(expired_lockout, 100.0), "P1,P1_P2")
        self.assertEqual(controller._phase_degraded_active(expired_lockout, 100.0), 0)

    def test_scheduled_snapshot_passes_explicit_schedule_boundary_contract(self) -> None:
        controller = DbusPublishController(SimpleNamespace(), self._age_seconds)
        service = SimpleNamespace(
            virtual_mode=2,
            auto_schedule_timezone="Europe/Berlin",
            auto_month_windows={7: ((9, 15), (17, 45))},
            auto_scheduled_enabled_days="Sat,Sun",
            auto_scheduled_night_start_delay_seconds=4500.0,
            auto_scheduled_latest_end_time="05:45",
        )
        scheduled_when = object()
        expected_snapshot = object()

        with (
            patch(
                "venus_evcharger.publish.dbus_config.local_datetime_from_timestamp",
                return_value=scheduled_when,
            ) as local_datetime,
            patch(
                "venus_evcharger.publish.dbus_config.scheduled_mode_snapshot",
                return_value=expected_snapshot,
            ) as scheduled_snapshot,
        ):
            snapshot = controller._scheduled_snapshot(service, 1782932400.0)

        self.assertIs(snapshot, expected_snapshot)
        local_datetime.assert_called_once_with(1782932400.0, "Europe/Berlin")
        scheduled_snapshot.assert_called_once_with(
            scheduled_when,
            {7: ((9, 15), (17, 45))},
            "Sat,Sun",
            delay_seconds=4500.0,
            latest_end_time="05:45",
        )

    def test_scheduled_snapshot_passes_default_schedule_boundary_contract(self) -> None:
        controller = DbusPublishController(SimpleNamespace(), self._age_seconds)
        service = SimpleNamespace(virtual_mode=2)
        scheduled_when = object()
        expected_snapshot = object()

        with (
            patch(
                "venus_evcharger.publish.dbus_config.local_datetime_from_timestamp",
                return_value=scheduled_when,
            ) as local_datetime,
            patch(
                "venus_evcharger.publish.dbus_config.scheduled_mode_snapshot",
                return_value=expected_snapshot,
            ) as scheduled_snapshot,
        ):
            snapshot = controller._scheduled_snapshot(service, 1719817200.0)

        self.assertIs(snapshot, expected_snapshot)
        local_datetime.assert_called_once_with(1719817200.0, "UTC")
        scheduled_snapshot.assert_called_once_with(
            scheduled_when,
            None,
            DEFAULT_SCHEDULED_ENABLED_DAYS,
            delay_seconds=3600.0,
            latest_end_time=None,
        )

    def test_publish_config_paths_uses_config_transaction_contract(self) -> None:
        service = SimpleNamespace(_dbus_slow_publish_interval_seconds=12.5)
        controller = DbusPublishController(service, self._age_seconds)
        controller.ensure_state = MagicMock()
        controller._config_values = MagicMock(return_value={"mode": 2})
        controller._publish_fields_transactional = MagicMock(return_value=True)

        self.assertTrue(controller.publish_config_paths(1, 100.0))

        controller.ensure_state.assert_called_once_with()
        controller._config_values.assert_called_once_with(1, 100.0)
        controller._publish_fields_transactional.assert_called_once_with(
            "config",
            {"mode": 2},
            100.0,
            interval_seconds=12.5,
        )

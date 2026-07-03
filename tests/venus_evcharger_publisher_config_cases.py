# SPDX-License-Identifier: GPL-3.0-or-later
import configparser

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

        self.assertEqual(values["connected"], 1)
        self.assertEqual(values["status"], 6)
        self.assertEqual(values["set_current"], 13.0)
        self.assertEqual(values["phase_selection"], "P1_P2")
        self.assertEqual(values["phase_selection_active"], "P1")
        self.assertEqual(values["supported_phase_selections"], "P1,P1_P2")
        self.assertEqual(values["auto_start_surplus_watts"], 1850.0)
        self.assertEqual(values["auto_stop_surplus_watts"], 1350.0)
        self.assertEqual(values["auto_min_soc"], 40.0)
        self.assertEqual(values["auto_resume_soc"], 50.0)
        self.assertEqual(values["auto_start_delay_seconds"], 10.0)
        self.assertEqual(values["auto_stop_delay_seconds"], 30.0)
        self.assertEqual(values["auto_scheduled_enabled_days"], "Mon,Tue,Wed,Thu,Fri")
        self.assertEqual(values["auto_scheduled_fallback_delay_seconds"], 3600.0)
        self.assertEqual(values["auto_scheduled_latest_end_time"], "06:30")
        self.assertEqual(values["auto_scheduled_night_current"], 13.0)
        self.assertEqual(values["auto_dbus_backoff_base_seconds"], 5.0)
        self.assertEqual(values["auto_dbus_backoff_max_seconds"], 60.0)
        self.assertEqual(values["auto_grid_recovery_start_seconds"], 14.0)
        self.assertEqual(values["auto_stop_surplus_delay_seconds"], 45.0)
        self.assertEqual(values["auto_stop_surplus_volatility_low_watts"], 80.0)
        self.assertEqual(values["auto_stop_surplus_volatility_high_watts"], 240.0)
        self.assertEqual(values["auto_reference_charge_power_watts"], 2100.0)
        self.assertEqual(values["auto_learn_charge_power_enabled"], 1)
        self.assertEqual(values["auto_learn_charge_power_min_watts"], 1400.0)
        self.assertEqual(values["auto_learn_charge_power_alpha"], 0.25)
        self.assertEqual(values["auto_learn_charge_power_start_delay_seconds"], 12.0)
        self.assertEqual(values["auto_learn_charge_power_window_seconds"], 180.0)
        self.assertEqual(values["auto_learn_charge_power_max_age_seconds"], 21600.0)
        self.assertEqual(values["auto_phase_switching"], 1)
        self.assertEqual(values["auto_phase_prefer_lowest_when_idle"], 0)
        self.assertEqual(values["auto_phase_upshift_delay_seconds"], 120.0)
        self.assertEqual(values["auto_phase_downshift_delay_seconds"], 30.0)
        self.assertEqual(values["auto_phase_upshift_headroom_watts"], 250.0)
        self.assertEqual(values["auto_phase_downshift_margin_watts"], 150.0)
        self.assertEqual(values["auto_phase_mismatch_retry_seconds"], 300.0)
        self.assertEqual(values["auto_phase_mismatch_lockout_count"], 3)
        self.assertEqual(values["auto_phase_mismatch_lockout_seconds"], 1800.0)

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

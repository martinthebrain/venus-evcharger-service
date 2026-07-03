# SPDX-License-Identifier: GPL-3.0-or-later
from tests.venus_evcharger_bootstrap_controller_path_cases_support import *  # noqa: F401,F403
from venus_evcharger.dbus_gateway import (
    missing_required_venus_paths,
    mismatched_venus_writeability,
)

class _TestServiceBootstrapControllerPathsPart2:
    def test_register_paths_marks_configured_split_topology_connected_without_legacy_host(self):
        service = SimpleNamespace(
            _dbusservice=_FakeDbusService(),
            connection_name="Adapter topology",
            deviceinstance=60,
            product_name="Venus EV Charger Service",
            custom_name="Wallbox",
            firmware_version="1.0",
            hardware_version="External adapter topology",
            serial="topology-60",
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
            auto_start_surplus_watts=1850.0,
            auto_stop_surplus_watts=1350.0,
            auto_min_soc=40.0,
            auto_resume_soc=50.0,
            auto_start_delay_seconds=10.0,
            auto_stop_delay_seconds=30.0,
            auto_month_windows={},
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
            runtime_overrides_path="/run/wallbox-overrides.ini",
            _runtime_overrides_active=False,
            backend_mode="split",
            meter_backend_type="template_meter",
            switch_backend_type="template_switch",
            charger_backend_type=None,
            topology_configured=True,
            host_configured=False,
            _last_health_reason="init",
            _last_health_code=0,
            _last_auto_state="idle",
            _last_auto_state_code=0,
            _handle_write=MagicMock(),
        )

        controller = self._controller(service)
        controller.register_paths()

        self.assertEqual(service._dbusservice.paths["/Connected"]["value"], 1)
        registered_paths = set(service._dbusservice.paths)
        self.assertEqual(missing_required_venus_paths(registered_paths), ())
        for path, spec in service._dbusservice.paths.items():
            with self.subTest(path=path):
                self.assertFalse(mismatched_venus_writeability(path, bool(spec.get("writeable", False))))

    def test_initialize_controllers_uses_port_wrappers_for_bound_controllers(self):
        service = SimpleNamespace(
            backend_mode="combined",
            meter_backend_type="shelly_combined",
            switch_backend_type="shelly_combined",
            charger_backend_type=None,
            meter_backend_config_path="",
            switch_backend_config_path="",
            charger_backend_config_path="",
            phase="L1",
            pm_component="Switch",
            pm_id=0,
            max_current=16.0,
        )
        controller = self._controller(service)

        controller.initialize_controllers()

        self.assertIsInstance(service._auto_controller.service, AutoDecisionPort)
        self.assertIsInstance(service._write_controller.port, WriteControllerPort)
        self.assertIsInstance(service._update_controller.service, UpdateCyclePort)
        self.assertEqual(service._backend_bundle.runtime.backend_mode, "combined")
        self.assertEqual(service._backend_bundle.runtime.meter_type, "shelly_meter")
        self.assertEqual(service._backend_bundle.runtime.switch_type, "shelly_contactor_switch")
        self.assertIsNotNone(service._meter_backend)
        self.assertIsNotNone(service._switch_backend)
        self.assertIsNone(service._charger_backend)

    def test_initialize_controllers_supports_meterless_split_charger_setup(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            charger_path = Path(temp_dir) / "charger.ini"
            charger_path.write_text(
                "[Adapter]\nType=template_charger\nBaseUrl=http://adapter.local\n"
                "[EnableRequest]\nUrl=/charger/enable\n"
                "[CurrentRequest]\nUrl=/charger/current\n",
                encoding="utf-8",
            )
            service = SimpleNamespace(
                backend_mode="split",
                meter_backend_type="none",
                switch_backend_type="shelly_combined",
                charger_backend_type="template_charger",
                meter_backend_config_path="",
                switch_backend_config_path="",
                charger_backend_config_path=str(charger_path),
                phase="L1",
                pm_component="Switch",
                pm_id=0,
                max_current=16.0,
                host="192.168.1.20",
                session=MagicMock(),
            )
            controller = self._controller(service)

            controller.initialize_controllers()

            self.assertEqual(service._backend_bundle.runtime.backend_mode, "split")
            self.assertEqual(service._backend_bundle.runtime.meter_type, None)
            self.assertIsNone(service._meter_backend)
            self.assertIsNotNone(service._switch_backend)
            self.assertIsNotNone(service._charger_backend)

    def test_initialize_controllers_supports_switchless_split_charger_setup(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            charger_path = Path(temp_dir) / "charger.ini"
            charger_path.write_text(
                "[Adapter]\nType=template_charger\nBaseUrl=http://adapter.local\n"
                "[Capabilities]\nSupportedPhaseSelections=P1,P1_P2\n"
                "[EnableRequest]\nUrl=/charger/enable\n"
                "[CurrentRequest]\nUrl=/charger/current\n"
                "[PhaseRequest]\nUrl=/charger/phase\n",
                encoding="utf-8",
            )
            service = SimpleNamespace(
                backend_mode="split",
                meter_backend_type="none",
                switch_backend_type="none",
                charger_backend_type="template_charger",
                meter_backend_config_path="",
                switch_backend_config_path="",
                charger_backend_config_path=str(charger_path),
                phase="L1",
                pm_component="Switch",
                pm_id=0,
                max_current=16.0,
                host="192.168.1.20",
                session=MagicMock(),
                config={"DEFAULT": {}},
            )
            controller = self._controller(service)

            controller.initialize_controllers()
            controller.initialize_virtual_state()

            self.assertEqual(service._backend_bundle.runtime.switch_type, None)
            self.assertIsNone(service._switch_backend)
            self.assertIsNotNone(service._charger_backend)
            self.assertEqual(service.supported_phase_selections, ("P1", "P1_P2"))

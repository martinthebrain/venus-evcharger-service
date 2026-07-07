# SPDX-License-Identifier: GPL-3.0-or-later
from unittest import mock

from tests.venus_evcharger_bootstrap_controller_path_cases_support import *  # noqa: F401,F403
from venus_evcharger.dbus_gateway import (
    missing_required_venus_paths,
    mismatched_venus_writeability,
)

class _TestServiceBootstrapControllerPathsPart2:
    def test_register_paths_wires_every_dynamic_path_with_formatter_and_write_handler(self):
        write_handler = MagicMock()
        service = SimpleNamespace(_dbusservice=MagicMock(), _handle_write=write_handler)
        controller = self._controller(service)
        controller._register_management_paths = MagicMock()
        formatter = MagicMock()
        controller._all_service_paths = MagicMock(
            return_value={
                "/Path/One": (1, formatter),
                "/Path/Two": ("two", None),
            }
        )

        with patch("venus_evcharger.bootstrap.paths.logging.debug") as debug:
            controller.register_paths()

        controller._register_management_paths.assert_called_once_with()
        controller._all_service_paths.assert_called_once_with()
        self.assertEqual(
            service._dbusservice.add_path.call_args_list,
            [
                mock.call(
                    "/Path/One",
                    1,
                    gettextcallback=formatter,
                    onchangecallback=write_handler,
                ),
                mock.call(
                    "/Path/Two",
                    "two",
                    gettextcallback=None,
                    onchangecallback=write_handler,
                ),
            ],
        )
        self.assertEqual(
            debug.call_args_list,
            [
                mock.call("Registering path: %s initial=%r formatter=%r", "/Path/One", 1, formatter),
                mock.call("Registering path: %s initial=%r formatter=%r", "/Path/Two", "two", None),
            ],
        )

    def test_register_paths_logs_and_reraises_dbus_registration_errors(self):
        write_handler = MagicMock()
        service = SimpleNamespace(_dbusservice=MagicMock(), _handle_write=write_handler)
        controller = self._controller(service)
        controller._register_management_paths = MagicMock()
        controller._all_service_paths = MagicMock(return_value={"/Broken": (1, None)})
        failure = RuntimeError("dbus rejected path")
        service._dbusservice.add_path.side_effect = failure

        with patch("venus_evcharger.bootstrap.paths.logging.error") as error, self.assertRaises(RuntimeError) as raised:
            controller.register_paths()

        self.assertIs(raised.exception, failure)
        error.assert_called_once_with("Failed to register path %s: %s", "/Broken", failure, exc_info=failure)

    def test_diagnostic_paths_are_composed_from_state_and_diagnostic_default_helpers(self):
        service = SimpleNamespace(
            _last_health_reason="grid-ok",
            _last_health_code=12,
            _last_auto_state="charging",
            _last_auto_state_code=4,
            _last_status_source=99,
        )
        controller = self._controller(service)
        scheduled_snapshot = object()
        controller._scheduled_snapshot = MagicMock(return_value=scheduled_snapshot)
        helper_patches = (
            patch("venus_evcharger.bootstrap.paths.scheduled_diagnostic_defaults", return_value={"/Scheduled": (1, None)}),
            patch("venus_evcharger.bootstrap.paths.backend_diagnostic_defaults", return_value={"/Backend": (2, None)}),
            patch("venus_evcharger.bootstrap.paths.decision_diagnostic_defaults", return_value={"/Decision": (3, None)}),
            patch(
                "venus_evcharger.bootstrap.paths.software_update_diagnostic_defaults",
                return_value={"/Software": (4, None)},
            ),
            patch("venus_evcharger.bootstrap.paths.phase_diagnostic_defaults", return_value={"/Phase": (5, None)}),
            patch("venus_evcharger.bootstrap.paths.age_counter_diagnostic_defaults", return_value={"/Age": (6, None)}),
            patch("venus_evcharger.bootstrap.paths.runtime_timing_diagnostic_defaults", return_value={"/Timing": (7, None)}),
        )

        with helper_patches[0] as scheduled, helper_patches[1] as backend, helper_patches[2] as decision, helper_patches[
            3
        ] as software, helper_patches[4] as phase, helper_patches[5] as age, helper_patches[6] as timing:
            self.assertEqual(
                controller._diagnostic_paths(),
                {
                    "/Auto/Health": ("grid-ok", None),
                    "/Auto/HealthCode": (12, None),
                    "/Auto/State": ("charging", None),
                    "/Auto/StateCode": (4, None),
                    "/Auto/RecoveryActive": (0, None),
                    "/Auto/StatusSource": ("99", None),
                    "/Auto/FaultActive": (0, None),
                    "/Auto/FaultReason": ("", None),
                    "/Scheduled": (1, None),
                    "/Backend": (2, None),
                    "/Decision": (3, None),
                    "/Software": (4, None),
                    "/Phase": (5, None),
                    "/Age": (6, None),
                    "/Timing": (7, None),
                },
            )

        controller._scheduled_snapshot.assert_called_once_with()
        scheduled.assert_called_once_with(scheduled_snapshot)
        for helper in (backend, decision, software, phase):
            helper.assert_called_once_with(service)
        age.assert_called_once_with()
        timing.assert_called_once_with()

    def test_diagnostic_paths_use_default_auto_state_when_state_attributes_are_missing(self):
        service = SimpleNamespace(_last_health_reason="init", _last_health_code=0)
        controller = self._controller(service)
        controller._scheduled_snapshot = MagicMock(return_value=None)
        empty_helper = MagicMock(return_value={})

        with patch("venus_evcharger.bootstrap.paths.scheduled_diagnostic_defaults", empty_helper), patch(
            "venus_evcharger.bootstrap.paths.backend_diagnostic_defaults",
            empty_helper,
        ), patch("venus_evcharger.bootstrap.paths.decision_diagnostic_defaults", empty_helper), patch(
            "venus_evcharger.bootstrap.paths.software_update_diagnostic_defaults",
            empty_helper,
        ), patch("venus_evcharger.bootstrap.paths.phase_diagnostic_defaults", empty_helper), patch(
            "venus_evcharger.bootstrap.paths.age_counter_diagnostic_defaults",
            empty_helper,
        ), patch("venus_evcharger.bootstrap.paths.runtime_timing_diagnostic_defaults", empty_helper):
            defaults = controller._diagnostic_paths()

        self.assertEqual(defaults["/Auto/State"], ("idle", None))
        self.assertEqual(defaults["/Auto/StateCode"], (0, None))
        self.assertEqual(defaults["/Auto/StatusSource"], ("unknown", None))

    def test_scheduled_snapshot_wires_configured_values_and_missing_optional_defaults(self):
        service = SimpleNamespace(
            virtual_mode=2,
            auto_month_windows={7: ((8, 15), (20, 45))},
            auto_scheduled_enabled_days="Tue,Thu",
            auto_scheduled_night_start_delay_seconds=5400.0,
            auto_scheduled_latest_end_time="07:45",
        )
        controller = self._controller(service)
        snapshot = object()

        with patch("venus_evcharger.bootstrap.paths.time.time", return_value=1783429200.0), patch(
            "venus_evcharger.bootstrap.paths.scheduled_mode_snapshot",
            return_value=snapshot,
        ) as scheduled:
            self.assertIs(controller._scheduled_snapshot(), snapshot)

        expected_now = datetime.fromtimestamp(1783429200.0)
        scheduled.assert_called_once_with(
            expected_now,
            {7: ((8, 15), (20, 45))},
            "Tue,Thu",
            delay_seconds=5400.0,
            latest_end_time="07:45",
        )

        default_service = SimpleNamespace(virtual_mode=2)
        default_controller = self._controller(default_service)
        with patch("venus_evcharger.bootstrap.paths.time.time", return_value=1783429200.0), patch(
            "venus_evcharger.bootstrap.paths.scheduled_mode_snapshot",
            return_value=snapshot,
        ) as scheduled_with_defaults:
            self.assertIs(default_controller._scheduled_snapshot(), snapshot)

        scheduled_with_defaults.assert_called_once_with(
            expected_now,
            {},
            (0, 1, 2, 3, 4),
            delay_seconds=3600.0,
            latest_end_time="06:30",
        )

        idle_controller = self._controller(SimpleNamespace(virtual_mode=0))
        with patch("venus_evcharger.bootstrap.paths.scheduled_mode_snapshot") as scheduled_idle:
            self.assertIsNone(idle_controller._scheduled_snapshot())
        scheduled_idle.assert_not_called()

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

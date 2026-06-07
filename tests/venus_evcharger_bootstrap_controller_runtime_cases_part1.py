# SPDX-License-Identifier: GPL-3.0-or-later
from tests.venus_evcharger_bootstrap_controller_runtime_cases_support import *  # noqa: F401,F403

class _TestServiceBootstrapControllerRuntimePart1:
    def test_initialize_controllers_keeps_legacy_backend_attrs_unset_after_resolution(self):
        parser = configparser.ConfigParser()
        parser.read_dict(
            {
                "DEFAULT": {"Host": "192.168.1.20"},
                "Backends": {
                    "Mode": "split",
                    "MeterType": "template_meter",
                    "SwitchType": "template_switch",
                    "ChargerType": "goe_charger",
                    "MeterConfigPath": "/data/meter.ini",
                    "SwitchConfigPath": "/data/switch.ini",
                    "ChargerConfigPath": "/data/charger.ini",
                },
            }
        )
        resolved = SimpleNamespace(
            runtime=SimpleNamespace(
                backend_mode="split",
                meter_type="template_meter",
                switch_type="template_switch",
                charger_type="goe_charger",
                meter_config_path=None,
                switch_config_path=None,
                charger_config_path=None,
                topology_configured=True,
                primary_rpc_configured=False,
            ),
            meter="meter",
            switch="switch",
            charger="charger",
        )
        service = SimpleNamespace(
            config=parser,
            host="192.168.1.20",
        )
        controller = self._controller(service)

        self.assertFalse(hasattr(service, "backend_mode"))

        with (
            patch("venus_evcharger.bootstrap.runtime.RuntimeSupportController") as runtime_controller,
            patch("venus_evcharger.bootstrap.runtime.AutoDecisionController"),
            patch("venus_evcharger.bootstrap.runtime.DbusPublishController"),
            patch("venus_evcharger.bootstrap.runtime.ShellyIoController"),
            patch("venus_evcharger.bootstrap.runtime.build_service_backends", return_value=resolved),
            patch("venus_evcharger.bootstrap.runtime.ServiceStateController"),
            patch("venus_evcharger.bootstrap.runtime.DbusWriteController"),
            patch("venus_evcharger.bootstrap.runtime.AutoInputSupervisor"),
            patch("venus_evcharger.bootstrap.runtime.UpdateCycleController"),
        ):
            runtime_controller.return_value.initialize_runtime_support = MagicMock()
            controller.initialize_controllers()

        self.assertFalse(hasattr(service, "backend_mode"))
        self.assertFalse(hasattr(service, "meter_backend_type"))
        self.assertFalse(hasattr(service, "switch_backend_type"))
        self.assertFalse(hasattr(service, "charger_backend_type"))
        self.assertFalse(hasattr(service, "_backend_selection"))
        self.assertEqual(service._meter_backend, "meter")
        self.assertEqual(service._switch_backend, "switch")
        self.assertEqual(service._charger_backend, "charger")
        self.assertTrue(service.topology_configured)
        self.assertFalse(service.primary_rpc_configured)

    def test_initialize_virtual_state_uses_config_defaults(self):
        service = SimpleNamespace(
            config={
                "DEFAULT": {
                    "Mode": "1",
                    "AutoStart": "0",
                    "StartStop": "1",
                    "Enable": "0",
                    "SetCurrent": "12.5",
                    "PhaseSelection": "P1_P2",
                }
            },
            max_current=16.0,
            _switch_backend=SimpleNamespace(
                capabilities=MagicMock(return_value=SimpleNamespace(supported_phase_selections=("P1", "P1_P2")))
            ),
        )
        controller = self._controller(service)

        controller.initialize_virtual_state()

        self.assertEqual(service.virtual_mode, 1)
        self.assertEqual(service.virtual_autostart, 0)
        self.assertEqual(service.virtual_startstop, 1)
        self.assertEqual(service.virtual_enable, 0)
        self.assertEqual(service.virtual_set_current, 12.5)
        self.assertEqual(list(service.auto_samples), [])
        self.assertIsNone(service.learned_charge_power_watts)
        self.assertIsNone(service.learned_charge_power_updated_at)
        self.assertEqual(service.learned_charge_power_state, "unknown")
        self.assertIsNone(service.learned_charge_power_learning_since)
        self.assertEqual(service.learned_charge_power_sample_count, 0)
        self.assertIsNone(service.learned_charge_power_phase)
        self.assertIsNone(service.learned_charge_power_voltage)
        self.assertEqual(service.learned_charge_power_signature_mismatch_sessions, 0)
        self.assertIsNone(service.learned_charge_power_signature_checked_session_started_at)
        self.assertIsNone(service.relay_last_changed_at)
        self.assertEqual(service.supported_phase_selections, ("P1", "P1_P2"))
        self.assertEqual(service.requested_phase_selection, "P1_P2")
        self.assertEqual(service.active_phase_selection, "P1_P2")
        self.assertFalse(service._auto_mode_cutover_pending)

    def test_initialize_virtual_state_falls_back_when_phase_selection_is_not_supported(self):
        service = SimpleNamespace(
            config={
                "DEFAULT": {
                    "PhaseSelection": "P1_P2_P3",
                }
            },
            max_current=16.0,
            _switch_backend=SimpleNamespace(
                capabilities=MagicMock(return_value=SimpleNamespace(supported_phase_selections=("P1", "P1_P2")))
            ),
        )
        controller = self._controller(service)

        controller.initialize_virtual_state()

        self.assertEqual(service.requested_phase_selection, "P1")
        self.assertEqual(service.active_phase_selection, "P1")

    def test_switch_backend_supported_phase_selections_falls_back_after_capability_error(self):
        service = SimpleNamespace(
            _switch_backend=SimpleNamespace(capabilities=MagicMock(side_effect=RuntimeError("boom"))),
        )
        controller = self._controller(service)

        self.assertEqual(controller._switch_backend_supported_phase_selections(service), ("P1",))

    def test_restore_runtime_state_sets_manual_startup_target_only_outside_auto_mode(self):
        manual_service = SimpleNamespace(
            virtual_mode=0,
            virtual_enable=0,
            virtual_startstop=1,
            _load_runtime_state=MagicMock(),
            _init_worker_state=MagicMock(),
        )
        auto_service = SimpleNamespace(
            virtual_mode=1,
            virtual_enable=1,
            virtual_startstop=1,
            _load_runtime_state=MagicMock(),
            _init_worker_state=MagicMock(),
        )

        self._controller(manual_service).restore_runtime_state()
        self._controller(auto_service).restore_runtime_state()

        self.assertTrue(manual_service._startup_manual_target)
        self.assertIsNone(auto_service._startup_manual_target)
        manual_service._load_runtime_state.assert_called_once_with()
        manual_service._init_worker_state.assert_called_once_with()

    def test_apply_device_metadata_prefers_custom_override_and_defaults(self):
        service = SimpleNamespace(
            config={"DEFAULT": {"ProductName": "Configured Product"}},
            custom_name_override="My Wallbox",
            host="192.168.1.20",
            host_configured=True,
        )
        controller = self._controller(service)
        controller.fetch_device_info_with_fallback = MagicMock(return_value={})

        controller.apply_device_metadata()

        self.assertEqual(service.product_name, "Configured Product")
        self.assertEqual(service.custom_name, "My Wallbox")
        self.assertEqual(service.serial, "192168120")
        self.assertEqual(service.firmware_version, "1.0")
        self.assertEqual(service.hardware_version, "Shelly 1PM Gen4")

    def test_apply_device_metadata_uses_device_info_when_available(self):
        service = SimpleNamespace(
            config={"DEFAULT": {}},
            custom_name_override="",
            host="192.168.1.20",
            host_configured=True,
        )
        controller = self._controller(service)
        controller.fetch_device_info_with_fallback = MagicMock(
            return_value={
                "name": "Shelly Garage",
                "mac": "ABCDEF",
                "fw_id": "fw-123",
                "model": "Shelly Plus",
            }
        )

        controller.apply_device_metadata()

        self.assertEqual(service.product_name, "Venus EV Charger Service")
        self.assertEqual(service.custom_name, "Shelly Garage")
        self.assertEqual(service.serial, "ABCDEF")
        self.assertEqual(service.firmware_version, "fw-123")
        self.assertEqual(service.hardware_version, "Shelly Plus")

    def test_apply_device_metadata_skips_device_lookup_when_host_is_not_configured(self):
        service = SimpleNamespace(
            config={"DEFAULT": {"ProductName": "Configured Product"}},
            custom_name_override="",
            host="",
            topology_configured=False,
            host_configured=False,
            deviceinstance=60,
        )
        controller = self._controller(service)
        controller.fetch_device_info_with_fallback = MagicMock(return_value={"name": "should-not-be-used"})

        controller.apply_device_metadata()

        controller.fetch_device_info_with_fallback.assert_not_called()
        self.assertEqual(service.product_name, "Configured Product")
        self.assertEqual(service.custom_name, "Venus EV Charger Service")
        self.assertEqual(service.serial, "unconfigured-60")
        self.assertEqual(service.firmware_version, "1.0")
        self.assertEqual(service.hardware_version, "Not configured")

    def test_apply_device_metadata_uses_generic_metadata_for_split_topology_without_legacy_rpc(self):
        service = SimpleNamespace(
            config={"DEFAULT": {"ProductName": "Configured Product"}},
            custom_name_override="",
            host="",
            topology_configured=True,
            primary_rpc_configured=False,
            deviceinstance=60,
        )
        controller = self._controller(service)
        controller.fetch_device_info_with_fallback = MagicMock(return_value={"name": "should-not-be-used"})

        controller.apply_device_metadata()

        controller.fetch_device_info_with_fallback.assert_not_called()
        self.assertEqual(service.product_name, "Configured Product")
        self.assertEqual(service.custom_name, "Venus EV Charger Service")
        self.assertEqual(service.serial, "topology-60")
        self.assertEqual(service.firmware_version, "1.0")
        self.assertEqual(service.hardware_version, "External adapter topology")

    def test_start_runtime_loops_starts_worker_and_schedules_timers(self):
        gobject_module = MagicMock()
        service = SimpleNamespace(
            _start_io_worker=MagicMock(),
            _start_control_api_server=MagicMock(),
            _start_companion_dbus_bridge=MagicMock(),
            host_configured=True,
            runtime_state_path="/run/state.json",
            _state_summary=MagicMock(return_value="mode=1"),
            poll_interval_ms=1000,
            sign_of_life_minutes=10,
            _update=MagicMock(),
            _sign_of_life=MagicMock(),
        )
        controller = ServiceBootstrapController(
            service,
            normalize_phase_func=lambda value: value,
            normalize_mode_func=lambda value: int(value),
            mode_uses_auto_logic_func=lambda mode: int(mode) in (1, 2),
            month_window_func=lambda *_args, **_kwargs: ((8, 0), (18, 0)),
            age_seconds_func=lambda *_args, **_kwargs: 0,
            health_code_func=lambda reason: {"init": 0}.get(reason, 99),
            phase_values_func=lambda *_args, **_kwargs: {},
            read_version_func=lambda _name: "1.0",
            gobject_module=gobject_module,
            script_path="/tmp/venus_evcharger_service.py",
            formatters={"kwh": None, "a": None, "w": None, "v": None, "status": None},
        )

        controller.start_runtime_loops()

        service._start_io_worker.assert_called_once_with()
        service._start_control_api_server.assert_called_once_with()
        service._start_companion_dbus_bridge.assert_called_once_with()
        gobject_module.timeout_add.assert_any_call(1000, service._update)
        gobject_module.timeout_add.assert_any_call(600000, service._sign_of_life)

    def test_start_runtime_loops_skips_companion_bridge_when_hook_is_not_callable(self):
        gobject_module = MagicMock()
        service = SimpleNamespace(
            _start_io_worker=MagicMock(),
            _start_control_api_server=MagicMock(),
            _start_companion_dbus_bridge=None,
            host_configured=True,
            runtime_state_path="/run/state.json",
            _state_summary=MagicMock(return_value="mode=1"),
            poll_interval_ms=1000,
            sign_of_life_minutes=10,
            _update=MagicMock(),
            _sign_of_life=MagicMock(),
        )
        controller = ServiceBootstrapController(
            service,
            normalize_phase_func=lambda value: value,
            normalize_mode_func=lambda value: int(value),
            mode_uses_auto_logic_func=lambda mode: int(mode) in (1, 2),
            month_window_func=lambda *_args, **_kwargs: ((8, 0), (18, 0)),
            age_seconds_func=lambda *_args, **_kwargs: 0,
            health_code_func=lambda reason: {"init": 0}.get(reason, 99),
            phase_values_func=lambda *_args, **_kwargs: {},
            read_version_func=lambda _name: "1.0",
            gobject_module=gobject_module,
            script_path="/tmp/venus_evcharger_service.py",
            formatters={"kwh": None, "a": None, "w": None, "v": None, "status": None},
        )

        controller.start_runtime_loops()

        service._start_io_worker.assert_called_once_with()
        service._start_control_api_server.assert_called_once_with()
        gobject_module.timeout_add.assert_any_call(1000, service._update)

    def test_start_runtime_loops_uses_async_runtime_hooks_when_available(self):
        gobject_module = MagicMock()
        service = SimpleNamespace(
            _mark_mainloop_thread=MagicMock(),
            _start_io_worker=MagicMock(),
            _start_control_api_server=MagicMock(),
            _start_update_worker=MagicMock(),
            _start_control_command_worker=MagicMock(),
            _start_mainloop_watchdog=MagicMock(),
            _start_companion_dbus_bridge=MagicMock(),
            _schedule_update_cycle=MagicMock(),
            _flush_dbus_publish_queue=MagicMock(),
            _mainloop_heartbeat_tick=MagicMock(),
            _dbus_publish_flush_interval_ms=123,
            topology_configured=True,
            runtime_state_path="/run/state.json",
            _state_summary=MagicMock(return_value="mode=1"),
            poll_interval_ms=1000,
            sign_of_life_minutes=10,
            _update=MagicMock(),
            _sign_of_life=MagicMock(),
        )
        controller = ServiceBootstrapController(
            service,
            normalize_phase_func=lambda value: value,
            normalize_mode_func=lambda value: int(value),
            mode_uses_auto_logic_func=lambda mode: int(mode) in (1, 2),
            month_window_func=lambda *_args, **_kwargs: ((8, 0), (18, 0)),
            age_seconds_func=lambda *_args, **_kwargs: 0,
            health_code_func=lambda reason: {"init": 0}.get(reason, 99),
            phase_values_func=lambda *_args, **_kwargs: {},
            read_version_func=lambda _name: "1.0",
            gobject_module=gobject_module,
            script_path="/tmp/venus_evcharger_service.py",
            formatters={"kwh": None, "a": None, "w": None, "v": None, "status": None},
        )

        controller.start_runtime_loops()

        service._mark_mainloop_thread.assert_called_once_with()
        service._start_update_worker.assert_called_once_with()
        service._start_control_command_worker.assert_called_once_with()
        service._start_mainloop_watchdog.assert_called_once_with()
        gobject_module.timeout_add.assert_any_call(1000, service._schedule_update_cycle)
        gobject_module.timeout_add.assert_any_call(123, service._flush_dbus_publish_queue)
        gobject_module.timeout_add.assert_any_call(1000, service._mainloop_heartbeat_tick)
        self.assertFalse(any(call.args == (1000, service._update) for call in gobject_module.timeout_add.call_args_list))


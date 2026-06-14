# SPDX-License-Identifier: GPL-3.0-or-later
from tests.venus_evcharger_bootstrap_controller_runtime_cases_support import *  # noqa: F401,F403

class _TestServiceBootstrapControllerRuntimePart2:
    def test_start_runtime_loops_skips_worker_when_host_is_not_configured(self):
        gobject_module = MagicMock()
        service = SimpleNamespace(
            _start_io_worker=MagicMock(),
            _ensure_dbus_introspection_worker_process=MagicMock(),
            _time_now=MagicMock(return_value=123.0),
            _start_control_api_server=MagicMock(),
            _start_companion_dbus_bridge=MagicMock(),
            topology_configured=False,
            host_configured=False,
            runtime_state_path="/run/state.json",
            _state_summary=MagicMock(return_value="mode=0"),
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
            health_code_func=lambda reason: {"init": 0, "not-configured": 41}.get(reason, 99),
            phase_values_func=lambda *_args, **_kwargs: {},
            read_version_func=lambda _name: "1.0",
            gobject_module=gobject_module,
            script_path="/tmp/venus_evcharger_service.py",
            formatters={"kwh": None, "a": None, "w": None, "v": None, "status": None},
        )

        controller.start_runtime_loops()

        service._start_io_worker.assert_not_called()
        service._ensure_dbus_introspection_worker_process.assert_called_once_with(123.0)
        service._start_control_api_server.assert_called_once_with()
        service._start_companion_dbus_bridge.assert_called_once_with()
        gobject_module.timeout_add.assert_any_call(1000, service._update)
        gobject_module.timeout_add.assert_any_call(600000, service._sign_of_life)

    def test_start_runtime_loops_starts_worker_for_configured_split_topology_without_legacy_host(self):
        gobject_module = MagicMock()
        service = SimpleNamespace(
            _start_io_worker=MagicMock(),
            _ensure_dbus_introspection_worker_process=MagicMock(),
            _time_now=MagicMock(return_value=456.0),
            _start_control_api_server=MagicMock(),
            _start_companion_dbus_bridge=MagicMock(),
            topology_configured=True,
            host_configured=False,
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
        service._ensure_dbus_introspection_worker_process.assert_called_once_with(456.0)
        service._start_control_api_server.assert_called_once_with()
        service._start_companion_dbus_bridge.assert_called_once_with()

    def test_initialize_dbus_service_uses_device_instance_in_name(self):
        service = SimpleNamespace(service_name="com.victronenergy.evcharger", deviceinstance=60)
        controller = self._controller(service)

        with patch("venus_evcharger.bootstrap.controller.GatewayDbusServiceProxy", return_value="dbus-service") as factory:
            controller.initialize_dbus_service()

        factory.assert_called_once_with("com.victronenergy.evcharger.http_60")
        self.assertEqual(service._dbusservice, "dbus-service")

    def test_publish_dbus_service_registers_initialized_dbus_shell(self):
        dbus_service = MagicMock()
        service = SimpleNamespace(_dbusservice=dbus_service)
        controller = self._controller(service)

        controller.publish_dbus_service()

        dbus_service.register.assert_called_once_with()

    def test_publish_dbus_service_reraises_name_exists_from_gateway_proxy(self):
        class NameExistsException(Exception):
            pass

        dbus_service = MagicMock()
        dbus_service.register.side_effect = NameExistsException("exists")
        dbus_service.name = "com.victronenergy.evcharger.http_60"
        service = SimpleNamespace(_dbusservice=dbus_service)
        controller = self._controller(service)
        controller._dbus_service_owned_by_current_process = MagicMock(return_value=True)

        with self.assertRaises(NameExistsException):
            controller.publish_dbus_service()

        controller._dbus_service_owned_by_current_process.assert_not_called()

    def test_publish_dbus_service_reraises_name_exists_for_foreign_owner(self):
        class NameExistsException(Exception):
            pass

        dbus_service = MagicMock()
        dbus_service.register.side_effect = NameExistsException("exists")
        service = SimpleNamespace(_dbusservice=dbus_service)
        controller = self._controller(service)
        controller._dbus_service_owned_by_current_process = MagicMock(return_value=False)

        with self.assertRaises(NameExistsException):
            controller.publish_dbus_service()
        controller._dbus_service_owned_by_current_process.assert_not_called()

    def test_publish_dbus_service_reraises_non_name_exists_exception(self):
        dbus_service = MagicMock()
        dbus_service.register.side_effect = RuntimeError("boom")
        service = SimpleNamespace(_dbusservice=dbus_service)
        controller = self._controller(service)

        with self.assertRaisesRegex(RuntimeError, "boom"):
            controller.publish_dbus_service()

    def test_dbus_service_owned_by_current_process_never_queries_bus(self):
        bus_proxy = MagicMock()
        bus_proxy.GetNameOwner.return_value = ":1.42"
        bus_proxy.GetConnectionUnixProcessID.return_value = 1234
        dbus_conn = MagicMock()
        dbus_conn.get_object.return_value = bus_proxy
        dbus_service = SimpleNamespace(_dbusconn=dbus_conn, name="com.victronenergy.evcharger.http_60")
        controller = self._controller(SimpleNamespace())

        with patch("venus_evcharger.bootstrap.controller.os.getpid", return_value=1234):
            self.assertFalse(controller._dbus_service_owned_by_current_process(dbus_service))

        dbus_conn.get_object.assert_not_called()
        bus_proxy.GetNameOwner.assert_not_called()
        bus_proxy.GetConnectionUnixProcessID.assert_not_called()

    def test_dbus_service_owned_by_current_process_returns_false_without_connection(self):
        controller = self._controller(SimpleNamespace())
        dbus_service = SimpleNamespace(name="com.victronenergy.evcharger.http_60")

        self.assertFalse(controller._dbus_service_owned_by_current_process(dbus_service))

    def test_dbus_service_owned_by_current_process_returns_false_when_owner_lookup_fails(self):
        bus_proxy = MagicMock()
        bus_proxy.GetNameOwner.side_effect = RuntimeError("dbus down")
        dbus_conn = MagicMock()
        dbus_conn.get_object.return_value = bus_proxy
        dbus_service = SimpleNamespace(_dbusconn=dbus_conn, name="com.victronenergy.evcharger.http_60")
        controller = self._controller(SimpleNamespace())

        self.assertFalse(controller._dbus_service_owned_by_current_process(dbus_service))

    def test_register_paths_logs_and_reraises_add_path_failures(self):
        class _BrokenDbusService(_FakeDbusService):
            def add_path(self, path, value, **kwargs):
                if path == "/Mode":
                    raise RuntimeError("boom")
                return super().add_path(path, value, **kwargs)

        service = SimpleNamespace(
            _dbusservice=_BrokenDbusService(),
            connection_name="Shelly RPC",
            deviceinstance=60,
            product_name="Venus EV Charger Service",
            custom_name="Wallbox",
            firmware_version="1.0",
            hardware_version="Shelly 1PM Gen4",
            serial="ABC123",
            position=1,
            min_current=6.0,
            max_current=16.0,
            virtual_set_current=16.0,
            virtual_autostart=1,
            virtual_mode=0,
            virtual_startstop=1,
            virtual_enable=1,
            _last_health_reason="init",
            _last_health_code=0,
            _handle_write=MagicMock(),
        )
        controller = self._controller(service)

        with patch("venus_evcharger.bootstrap.controller.logging.error") as error_mock:
            with self.assertRaises(RuntimeError):
                controller.register_paths()

        error_mock.assert_called_once()

# SPDX-License-Identifier: GPL-3.0-or-later
from tests.venus_evcharger_bootstrap_controller_support import (
    MagicMock,
    ModuleType,
    ServiceBootstrapControllerTestCase,
    _enable_fault_diagnostics,
    _install_signal_logging,
    _request_mainloop_quit,
    _run_service_loop,
    patch,
    run_service_main,
    sys,
)


class TestServiceBootstrapControllerLifecycle(ServiceBootstrapControllerTestCase):
    def test_initialize_service_runs_full_startup_sequence(self):
        service = MagicMock()
        controller = self._controller(service)
        calls = []
        controller.load_runtime_configuration = MagicMock(side_effect=lambda: calls.append("config"))
        controller.initialize_controllers = MagicMock(side_effect=lambda: calls.append("controllers"))
        controller.initialize_virtual_state = MagicMock(side_effect=lambda: calls.append("virtual"))
        controller.restore_runtime_state = MagicMock(side_effect=lambda: calls.append("restore"))
        controller.apply_device_metadata = MagicMock(side_effect=lambda: calls.append("metadata"))
        controller.start_runtime_loops = MagicMock(side_effect=lambda: calls.append("loops"))
        controller.initialize_dbus_service = MagicMock(side_effect=lambda: calls.append("dbus"))
        controller.register_paths = MagicMock(side_effect=lambda: calls.append("paths"))
        controller.publish_dbus_service = MagicMock(side_effect=lambda: calls.append("publish"))

        controller.initialize_service()

        self.assertEqual(
            calls,
            ["config", "controllers", "virtual", "restore", "metadata", "dbus", "paths", "publish", "loops"],
        )

    def test_initialize_service_does_not_start_runtime_loops_when_publish_fails(self):
        service = MagicMock()
        controller = self._controller(service)
        controller.load_runtime_configuration = MagicMock()
        controller.initialize_controllers = MagicMock()
        controller.initialize_virtual_state = MagicMock()
        controller.restore_runtime_state = MagicMock()
        controller.apply_device_metadata = MagicMock()
        controller.initialize_dbus_service = MagicMock()
        controller.register_paths = MagicMock()
        controller.publish_dbus_service = MagicMock(side_effect=RuntimeError("boom"))
        controller.start_runtime_loops = MagicMock()

        with self.assertRaises(RuntimeError):
            controller.initialize_service()

        controller.initialize_dbus_service.assert_called_once_with()
        controller.register_paths.assert_called_once_with()
        controller.publish_dbus_service.assert_called_once_with()
        controller.start_runtime_loops.assert_not_called()

    def test_request_mainloop_quit_uses_idle_add_when_available_and_falls_back(self):
        mainloop = MagicMock()
        gobject_module = MagicMock()

        _request_mainloop_quit(gobject_module, mainloop)
        gobject_module.idle_add.assert_called_once_with(mainloop.quit)

        gobject_module = MagicMock()
        gobject_module.idle_add.side_effect = RuntimeError("nope")
        _request_mainloop_quit(gobject_module, mainloop)
        mainloop.quit.assert_called()

        gobject_module = object()
        _request_mainloop_quit(gobject_module, mainloop)
        self.assertTrue(mainloop.quit.called)

    def test_run_service_loop_instantiates_service_and_runs_mainloop(self):
        mainloop = MagicMock()
        gobject_module = MagicMock()
        gobject_module.MainLoop.return_value = mainloop
        service_factory = MagicMock()

        with patch("venus_evcharger.bootstrap.controller._install_signal_logging") as install_signal_logging:
            _run_service_loop(service_factory, gobject_module)

        service_factory.assert_called_once_with()
        install_signal_logging.assert_called_once()
        mainloop.run.assert_called_once_with()

    def test_enable_fault_diagnostics_swallows_failures(self):
        with patch("venus_evcharger.bootstrap.controller.faulthandler.enable", side_effect=RuntimeError("nope")):
            _enable_fault_diagnostics()

    def test_setup_dbus_mainloop_initializes_threads_and_tolerates_missing_threads_init(self):
        dbus_module = ModuleType("dbus")
        mainloop_module = ModuleType("dbus.mainloop")
        glib_module = ModuleType("dbus.mainloop.glib")
        glib_module.DBusGMainLoop = MagicMock()
        glib_module.threads_init = MagicMock()

        with patch.dict(
            sys.modules,
            {
                "dbus": dbus_module,
                "dbus.mainloop": mainloop_module,
                "dbus.mainloop.glib": glib_module,
            },
            clear=False,
        ):
            import dbus as imported_dbus

            imported_dbus.mainloop = mainloop_module
            mainloop_module.glib = glib_module
            from venus_evcharger.bootstrap.controller import _setup_dbus_mainloop

            _setup_dbus_mainloop()

        glib_module.threads_init.assert_called_once_with()
        glib_module.DBusGMainLoop.assert_called_once_with(set_as_default=True)

        glib_module = ModuleType("dbus.mainloop.glib")
        glib_module.DBusGMainLoop = MagicMock()
        glib_module.threads_init = MagicMock(side_effect=AttributeError("missing"))
        with patch.dict(
            sys.modules,
            {
                "dbus": dbus_module,
                "dbus.mainloop": mainloop_module,
                "dbus.mainloop.glib": glib_module,
            },
            clear=False,
        ):
            import dbus as imported_dbus

            imported_dbus.mainloop = mainloop_module
            mainloop_module.glib = glib_module
            _setup_dbus_mainloop()

        glib_module.DBusGMainLoop.assert_called_once_with(set_as_default=True)

    def test_run_service_main_runs_loop_and_logs_critical_on_failure(self):
        gobject_module = MagicMock()
        with patch("venus_evcharger.bootstrap.controller._enable_fault_diagnostics") as enable_faults:
            with patch("venus_evcharger.bootstrap.controller._setup_dbus_mainloop") as setup_loop:
                with patch("venus_evcharger.bootstrap.controller._run_service_loop") as run_loop:
                    run_service_main(lambda: None, "/tmp/does-not-matter.ini", gobject_module)

        enable_faults.assert_called_once_with()
        setup_loop.assert_called_once_with()
        run_loop.assert_called_once()

        with patch("venus_evcharger.bootstrap.controller._setup_dbus_mainloop", side_effect=RuntimeError("boom")):
            with patch("venus_evcharger.bootstrap.controller.logging.critical") as critical_mock:
                with self.assertRaises(RuntimeError):
                    run_service_main(lambda: None, "/tmp/does-not-matter.ini", gobject_module)
        critical_mock.assert_called_once()

    def test_install_signal_logging_requests_clean_shutdown(self):
        handlers = {}
        quit_calls = []

        def _capture_handler(signum, handler):
            handlers[signum] = handler

        with patch("venus_evcharger.bootstrap.controller.signal.signal", side_effect=_capture_handler):
            _install_signal_logging(lambda: quit_calls.append("quit"))

        self.assertTrue(handlers)
        handlers[next(iter(handlers))](15, None)
        self.assertEqual(quit_calls, ["quit"])

    def test_install_signal_logging_handles_missing_callback_and_registration_failures(self):
        handlers = {}

        def _capture_handler(signum, handler):
            if not handlers:
                raise RuntimeError("nope")
            handlers[signum] = handler

        with patch("venus_evcharger.bootstrap.controller.signal.signal", side_effect=_capture_handler):
            with patch("venus_evcharger.bootstrap.controller.logging.debug") as debug_mock:
                _install_signal_logging()

        debug_mock.assert_called()

        handlers = {}
        with patch(
            "venus_evcharger.bootstrap.controller.signal.signal",
            side_effect=lambda signum, handler: handlers.setdefault(signum, handler),
        ):
            _install_signal_logging(None)

        self.assertTrue(handlers)
        handlers[next(iter(handlers))](15, None)

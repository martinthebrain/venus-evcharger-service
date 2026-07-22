# SPDX-License-Identifier: GPL-3.0-or-later
from collections.abc import Callable

from tests.venus_evcharger_bootstrap_controller_support import (
    MagicMock,
    ServiceBootstrapControllerTestCase,
    _enable_fault_diagnostics,
    _install_signal_logging,
    _request_mainloop_quit,
    _run_service_loop,
    patch,
    run_service_main,
    tempfile,
)


class TestServiceBootstrapControllerLifecycle(ServiceBootstrapControllerTestCase):
    def test_initialize_service_runs_full_startup_sequence(self) -> None:
        service = MagicMock()
        controller = self._controller(service)
        calls: list[str] = []
        with patch.object(
            controller,
            "load_runtime_configuration",
            side_effect=lambda: calls.append("config"),
        ), patch.object(
            controller,
            "prepare_runtime_state",
            side_effect=lambda: calls.append("prepare"),
        ), patch.object(
            controller,
            "initialize_controllers",
            side_effect=lambda: calls.append("controllers"),
        ), patch.object(
            controller,
            "initialize_virtual_state",
            side_effect=lambda: calls.append("virtual"),
        ), patch.object(
            controller,
            "restore_runtime_state",
            side_effect=lambda: calls.append("restore"),
        ), patch.object(
            controller,
            "apply_device_metadata",
            side_effect=lambda: calls.append("metadata"),
        ), patch.object(
            controller,
            "register_evcs_publication",
            side_effect=lambda: calls.append("publication"),
        ), patch.object(
            controller,
            "start_runtime_loops",
            side_effect=lambda: calls.append("loops"),
        ), patch("venus_evcharger.bootstrap.controller.logging.info") as info_mock:
            controller.initialize_service()

        self.assertEqual(
            calls,
            ["config", "prepare", "virtual", "controllers", "restore", "metadata", "publication", "loops"],
        )
        self.assertEqual(
            [call.args for call in info_mock.call_args_list],
            [
                ("Bootstrap step start: %s", "load-runtime-configuration"),
                ("Bootstrap step complete: %s", "load-runtime-configuration"),
                ("Bootstrap step start: %s", "prepare-runtime-state"),
                ("Bootstrap step complete: %s", "prepare-runtime-state"),
                ("Bootstrap step start: %s", "initialize-virtual-state"),
                ("Bootstrap step complete: %s", "initialize-virtual-state"),
                ("Bootstrap step start: %s", "initialize-controllers"),
                ("Bootstrap step complete: %s", "initialize-controllers"),
                ("Bootstrap step start: %s", "restore-runtime-state"),
                ("Bootstrap step complete: %s", "restore-runtime-state"),
                ("Bootstrap step start: %s", "apply-device-metadata"),
                ("Bootstrap step complete: %s", "apply-device-metadata"),
                ("Bootstrap step start: %s", "register-evcs-publication"),
                ("Bootstrap step complete: %s", "register-evcs-publication"),
                ("Bootstrap step start: %s", "start-runtime-loops"),
                ("Bootstrap step complete: %s", "start-runtime-loops"),
            ],
        )

    def test_initialize_service_does_not_start_runtime_loops_when_publication_fails(self) -> None:
        service = MagicMock()
        controller = self._controller(service)
        with patch.object(controller, "load_runtime_configuration"), patch.object(
            controller,
            "prepare_runtime_state",
        ), patch.object(controller, "initialize_controllers"), patch.object(
            controller,
            "initialize_virtual_state",
        ), patch.object(controller, "restore_runtime_state"), patch.object(
            controller,
            "apply_device_metadata",
        ), patch.object(
            controller,
            "register_evcs_publication",
            side_effect=RuntimeError("boom"),
        ) as registration, patch.object(controller, "start_runtime_loops") as start_runtime_loops:
            with self.assertRaises(RuntimeError):
                controller.initialize_service()

        registration.assert_called_once_with()
        start_runtime_loops.assert_not_called()

    def test_request_mainloop_quit_uses_idle_add_when_available_and_falls_back(self) -> None:
        mainloop = MagicMock()
        gobject_module = MagicMock()

        _request_mainloop_quit(gobject_module, mainloop)
        gobject_module.idle_add.assert_called_once_with(mainloop.quit)

        gobject_module = MagicMock()
        gobject_module.idle_add.side_effect = RuntimeError("nope")
        _request_mainloop_quit(gobject_module, mainloop)
        mainloop.quit.assert_called()

        fallback_gobject = object()
        _request_mainloop_quit(fallback_gobject, mainloop)
        self.assertTrue(mainloop.quit.called)

    def test_run_service_loop_instantiates_service_and_runs_mainloop(self) -> None:
        mainloop = MagicMock()
        gobject_module = MagicMock()
        gobject_module.MainLoop.return_value = mainloop
        service_factory = MagicMock()

        with patch("venus_evcharger.bootstrap.controller._install_signal_logging") as install_signal_logging:
            _run_service_loop(service_factory, gobject_module)

        service_factory.assert_called_once_with()
        install_signal_logging.assert_called_once()
        install_signal_logging.call_args.args[0]()
        gobject_module.idle_add.assert_called_once_with(mainloop.quit)
        mainloop.run.assert_called_once_with()

    def test_enable_fault_diagnostics_swallows_failures(self) -> None:
        with patch("venus_evcharger.bootstrap.controller.faulthandler.enable", side_effect=RuntimeError("nope")):
            _enable_fault_diagnostics()

    def test_run_service_main_runs_loop_and_logs_critical_on_failure(self) -> None:
        gobject_module = MagicMock()
        with patch("venus_evcharger.bootstrap.controller._enable_fault_diagnostics") as enable_faults:
            with patch("venus_evcharger.bootstrap.controller._run_service_loop") as run_loop:
                run_service_main(lambda: None, "/tmp/does-not-matter.ini", gobject_module)

        enable_faults.assert_called_once_with()
        run_loop.assert_called_once()

        with patch("venus_evcharger.bootstrap.controller._run_service_loop", side_effect=RuntimeError("boom")):
            with patch("venus_evcharger.bootstrap.controller.logging.critical") as critical_mock:
                with self.assertRaises(RuntimeError):
                    run_service_main(lambda: None, "/tmp/does-not-matter.ini", gobject_module)
        critical_mock.assert_called_once()

    def test_run_service_main_reads_config_and_wires_logging_and_loop_arguments(self) -> None:
        gobject_module = MagicMock()
        service_factory = MagicMock()

        with tempfile.NamedTemporaryFile("w+") as config_file:
            config_file.write("[DEFAULT]\nLogging=warning\n")
            config_file.flush()
            config_path = config_file.name

            with patch("venus_evcharger.bootstrap.controller.os.getpid", return_value=4321):
                with patch("venus_evcharger.bootstrap.controller.logging.basicConfig") as basic_config:
                    with patch("venus_evcharger.bootstrap.controller.logging.info") as info_mock:
                        with patch("venus_evcharger.bootstrap.controller._enable_fault_diagnostics") as enable_faults:
                            with patch("venus_evcharger.bootstrap.controller._run_service_loop") as run_loop:
                                run_service_main(service_factory, config_path, gobject_module)

        basic_config.assert_called_once_with(
            format="%(levelname)s [pid=%(process)d %(threadName)s] %(message)s",
            level="WARNING",
        )
        info_mock.assert_called_once_with("Start Venus EV charger service pid=%s", 4321)
        enable_faults.assert_called_once_with()
        run_loop.assert_called_once_with(service_factory, gobject_module)

    def test_run_service_main_logs_pid_and_exception_object_on_startup_failure(self) -> None:
        gobject_module = MagicMock()
        error = RuntimeError("boom")

        with patch("venus_evcharger.bootstrap.controller.os.getpid", return_value=9876):
            with patch("venus_evcharger.bootstrap.controller._run_service_loop", side_effect=error):
                with patch("venus_evcharger.bootstrap.controller.logging.critical") as critical_mock:
                    with self.assertRaises(RuntimeError) as raised:
                        run_service_main(lambda: None, "/tmp/does-not-matter.ini", gobject_module)

        self.assertIs(raised.exception, error)
        critical_mock.assert_called_once_with("Error at main pid=%s", 9876, exc_info=error)

    def test_install_signal_logging_requests_clean_shutdown(self) -> None:
        handlers: dict[int, Callable[[int, object], None]] = {}
        quit_calls: list[str] = []

        def _capture_handler(signum: int, handler: Callable[[int, object], None]) -> None:
            handlers[signum] = handler

        with patch("venus_evcharger.bootstrap.controller.signal.signal", side_effect=_capture_handler):
            _install_signal_logging(lambda: quit_calls.append("quit"))

        self.assertTrue(handlers)
        handlers[next(iter(handlers))](15, None)
        self.assertEqual(quit_calls, ["quit"])

    def test_install_signal_logging_handles_missing_callback_and_registration_failures(self) -> None:
        handlers: dict[int, Callable[[int, object], None]] = {}

        def _capture_handler(signum: int, handler: Callable[[int, object], None]) -> None:
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

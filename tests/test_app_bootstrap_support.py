# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import configparser
import unittest
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import MagicMock

from venus_evcharger.app import bootstrap_support


class TestAppBootstrapSupport(unittest.TestCase):
    def test_logging_level_uses_default_section_and_uppercases(self) -> None:
        config = configparser.ConfigParser()
        self.assertEqual(bootstrap_support.logging_level_from_config(config), "INFO")
        self.assertEqual(bootstrap_support.logging_level_from_config(config, default="debug"), "DEBUG")

        config["DEFAULT"]["Logging"] = "warning"
        self.assertEqual(bootstrap_support.logging_level_from_config(config), "WARNING")

        empty_config = cast(configparser.ConfigParser, {})
        self.assertEqual(bootstrap_support.logging_level_from_config(empty_config), "INFO")
        self.assertEqual(bootstrap_support.logging_level_from_config(empty_config, default="fallback"), "fallback")
        case_sensitive_config = {"DEFAULT": {"Logging": "error", "logging": "debug", "LOGGING": "critical"}}
        self.assertEqual(
            bootstrap_support.logging_level_from_config(cast(configparser.ConfigParser, case_sensitive_config)),
            "ERROR",
        )

    def test_fault_diagnostics_enable_contract(self) -> None:
        faulthandler = SimpleNamespace(enable=MagicMock())
        logging = SimpleNamespace(debug=MagicMock())

        bootstrap_support.enable_fault_diagnostics(faulthandler, logging)

        faulthandler.enable.assert_called_once_with(all_threads=True)
        logging.debug.assert_not_called()

        faulthandler.enable = MagicMock(side_effect=RuntimeError("disabled"))
        bootstrap_support.enable_fault_diagnostics(faulthandler, logging)

        logging.debug.assert_called_once()
        self.assertEqual(logging.debug.call_args.args[0], "faulthandler.enable() unavailable: %s")
        logged_error = logging.debug.call_args.args[1]
        self.assertIsInstance(logged_error, RuntimeError)
        self.assertEqual(str(logged_error), "disabled")

    def test_signal_logging_registers_available_handlers_and_quits(self) -> None:
        handlers: dict[int, Callable[[int, Any], None]] = {}

        def signal(signum: int, handler: object) -> None:
            handlers[signum] = handler

        signal_module = SimpleNamespace(SIGTERM=15, SIGINT=2, SIGHUP=1, signal=signal)
        logging = SimpleNamespace(warning=MagicMock(), debug=MagicMock())
        os_module = SimpleNamespace(getpid=MagicMock(return_value=1234))
        quit_callback = MagicMock()

        bootstrap_support.install_signal_logging(signal_module, logging, os_module, quit_callback)

        self.assertEqual(set(handlers), {1, 2, 15})
        handlers[15](15, None)
        logging.warning.assert_called_once_with("Received signal %s in pid=%s", 15, 1234)
        quit_callback.assert_called_once_with()
        logging.debug.assert_not_called()

    def test_signal_logging_logs_registration_and_shutdown_errors(self) -> None:
        def signal(signum: int, _handler: object) -> None:
            if signum == 2:
                raise ValueError("unsupported")

        signal_module = SimpleNamespace(SIGTERM=None, SIGINT=2, SIGHUP=1, signal=signal)
        logging = SimpleNamespace(warning=MagicMock(), debug=MagicMock())
        os_module = SimpleNamespace(getpid=MagicMock(return_value=1234))

        bootstrap_support.install_signal_logging(signal_module, logging, os_module)
        logging.debug.assert_called_once()
        self.assertEqual(logging.debug.call_args.args[0], "Unable to install signal handler for %s: %s")
        self.assertEqual(logging.debug.call_args.args[1], 2)
        logged_error = logging.debug.call_args.args[2]
        self.assertIsInstance(logged_error, ValueError)
        self.assertEqual(str(logged_error), "unsupported")

        handlers: dict[int, Callable[[int, Any], None]] = {}
        bootstrap_support.install_signal_logging(SimpleNamespace(signal=handlers.__setitem__), logging, os_module)
        self.assertEqual(handlers, {})

        handlers = {}
        signal_module = SimpleNamespace(SIGTERM=15, SIGINT=None, SIGHUP=None, signal=handlers.__setitem__)
        quit_callback = MagicMock(side_effect=RuntimeError("quit failed"))
        logging.debug.reset_mock()

        bootstrap_support.install_signal_logging(signal_module, logging, os_module, quit_callback)
        handlers[15](15, None)

        logging.debug.assert_called_once()
        self.assertEqual(logging.debug.call_args.args[0], "Unable to request shutdown after signal %s: %s")
        self.assertEqual(logging.debug.call_args.args[1], 15)
        logged_error = logging.debug.call_args.args[2]
        self.assertIsInstance(logged_error, RuntimeError)
        self.assertEqual(str(logged_error), "quit failed")

    def test_dbus_mainloop_setup_is_gateway_owned(self) -> None:
        logging = SimpleNamespace(debug=MagicMock())

        bootstrap_support.setup_dbus_mainloop(logging)

        logging.debug.assert_called_once_with("Skipping DBus mainloop setup in the core service; gateway owns DBus")

    def test_request_mainloop_quit_prefers_idle_add_and_falls_back(self) -> None:
        mainloop = SimpleNamespace(quit=MagicMock())
        gobject = SimpleNamespace(idle_add=MagicMock())
        logging = SimpleNamespace(debug=MagicMock())

        bootstrap_support.request_mainloop_quit(gobject, mainloop, logging)

        gobject.idle_add.assert_called_once_with(mainloop.quit)
        mainloop.quit.assert_not_called()

        gobject.idle_add = MagicMock(side_effect=RuntimeError("idle failed"))
        bootstrap_support.request_mainloop_quit(gobject, mainloop, logging)

        logging.debug.assert_called_once()
        self.assertEqual(logging.debug.call_args.args[0], "Unable to schedule GLib shutdown via idle_add: %s")
        logged_error = logging.debug.call_args.args[1]
        self.assertIsInstance(logged_error, RuntimeError)
        self.assertEqual(str(logged_error), "idle failed")
        mainloop.quit.assert_called_once_with()

        mainloop.quit.reset_mock()
        bootstrap_support.request_mainloop_quit(SimpleNamespace(), mainloop, logging)
        mainloop.quit.assert_called_once_with()

    def test_run_service_loop_wires_shutdown_callback_and_mainloop(self) -> None:
        mainloop = SimpleNamespace(run=MagicMock(), quit=MagicMock())
        gobject = SimpleNamespace(MainLoop=MagicMock(return_value=mainloop))
        service_class = MagicMock()
        callbacks: list[object] = []

        def install_signal_logging_func(callback: object) -> None:
            callbacks.append(callback)

        request_mainloop_quit_func = MagicMock()
        logging = SimpleNamespace(info=MagicMock())

        bootstrap_support.run_service_loop(
            service_class,
            gobject,
            install_signal_logging_func,
            request_mainloop_quit_func,
            logging,
        )

        service_class.assert_called_once_with()
        gobject.MainLoop.assert_called_once_with()
        mainloop.run.assert_called_once_with()
        self.assertEqual(len(callbacks), 1)
        cast(Callable[[], None], callbacks[0])()
        request_mainloop_quit_func.assert_called_once_with(gobject, mainloop)
        self.assertEqual(
            [call.args[0] for call in logging.info.call_args_list],
            [
                "Instantiating Venus EV charger service bootstrap",
                "Service bootstrap completed; preparing GLib main loop",
                "Connected to dbus, and switching over to gobject.MainLoop() (= event based)",
            ],
        )


if __name__ == "__main__":
    unittest.main()

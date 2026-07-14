# SPDX-License-Identifier: GPL-3.0-or-later
import os
import runpy
import sys
import tempfile
import unittest
from importlib import import_module, reload
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock, patch


class TestShellyWallboxEntrypoints(unittest.TestCase):
    @staticmethod
    def _repo_file(name: str) -> str:
        return os.path.join(os.path.dirname(os.path.dirname(__file__)), name)

    @staticmethod
    def _fake_main_module_dependencies():
        bootstrap_module = ModuleType("venus_evcharger.bootstrap.controller")
        bootstrap_module.run_service_main = MagicMock()
        bootstrap_module.ServiceBootstrapController = type(
            "ServiceBootstrapController",
            (),
            {
                "__init__": lambda self, *_args, **_kwargs: None,
                "initialize_service": lambda self: None,
            },
        )

        common_module = ModuleType("venus_evcharger.core.common")
        for name in (
            "_a",
            "_age_seconds",
            "_fresh_charger_retry_reason",
            "_fresh_charger_retry_source",
            "_fresh_charger_transport_reason",
            "_fresh_charger_transport_source",
            "_fresh_confirmed_relay_output",
            "_health_code",
            "_kwh",
            "_status_label",
            "_v",
            "_w",
            "evse_fault_reason",
            "mode_uses_auto_logic",
            "month_in_ranges",
            "month_window",
            "normalize_mode",
            "normalize_phase",
            "parse_hhmm",
            "phase_values",
            "read_version",
        ):
            setattr(common_module, name, lambda *args, **kwargs: args[0] if args else None)

        control_module = ModuleType("venus_evcharger.service.control")

        class StatePublish:
            @staticmethod
            def _config_path():
                return "/tmp/config.venus_evcharger.ini"

        class ControlApi(StatePublish):
            pass

        control_module.ControlApi = ControlApi

        state_module = ModuleType("venus_evcharger.controllers.state")
        state_module.ServiceStateController = type(
            "ServiceStateController",
            (),
            {"__init__": lambda self, *_args, **_kwargs: None},
        )

        fake_glib = MagicMock()
        fake_gi = ModuleType("gi")
        fake_repository = ModuleType("gi.repository")
        fake_repository.GLib = fake_glib
        fake_gi.repository = fake_repository

        return {
            "venus_evcharger.bootstrap.controller": bootstrap_module,
            "venus_evcharger.core.common": common_module,
            "venus_evcharger.service.control": control_module,
            "venus_evcharger.controllers.state": state_module,
            "dbus": MagicMock(),
            "vedbus": MagicMock(),
            "gi": fake_gi,
            "gi.repository": fake_repository,
            "gi.repository.GLib": fake_glib,
        }, bootstrap_module

    def test_main_module_uses_glib_gobject_alias(self):
        module_path = self._repo_file("venus_evcharger_service.py")
        fake_modules, _bootstrap_module = self._fake_main_module_dependencies()

        with patch.dict(sys.modules, fake_modules, clear=False):
            module_globals = runpy.run_path(module_path, run_name="venus_evcharger_service_import_test")

        self.assertIs(module_globals["gobject"], fake_modules["gi.repository"].GLib)

    def test_main_module_main_guard_delegates_to_run_service_main(self):
        module_path = self._repo_file("venus_evcharger_service.py")
        fake_modules, bootstrap_module = self._fake_main_module_dependencies()

        with patch.dict(sys.modules, fake_modules, clear=False):
            with patch.object(sys, "version_info", SimpleNamespace(major=3)):
                module_globals = runpy.run_path(module_path, run_name="__main__")

        bootstrap_module.run_service_main.assert_called_once_with(
            module_globals["ShellyWallboxService"],
            "/tmp/config.venus_evcharger.ini",
            module_globals["gobject"],
        )

    def test_helper_module_main_guard_exits_cleanly(self):
        helper_path = self._repo_file("venus_evcharger_auto_input_helper.py")
        fake_bus = MagicMock()
        fake_bus.add_signal_receiver = MagicMock()
        fake_interface = MagicMock()
        fake_interface.ListNames.return_value = []
        fake_interface.GetValue.return_value = None
        fake_interface.Introspect.return_value = "<node/>"

        fake_dbus = ModuleType("dbus")
        fake_dbus.SystemBus = MagicMock(return_value=fake_bus)
        fake_dbus.SessionBus = MagicMock(return_value=fake_bus)
        fake_dbus.Interface = MagicMock(return_value=fake_interface)
        fake_dbus.__path__ = []

        fake_dbus_mainloop = ModuleType("dbus.mainloop")
        fake_dbus_glib = ModuleType("dbus.mainloop.glib")
        fake_dbus_glib.DBusGMainLoop = MagicMock()
        fake_dbus_mainloop.glib = fake_dbus_glib
        fake_dbus.mainloop = fake_dbus_mainloop

        fake_loop = MagicMock()
        fake_glib = ModuleType("gi.repository.GLib")
        fake_glib.MainLoop = MagicMock(return_value=fake_loop)
        fake_glib.timeout_add = MagicMock()
        fake_glib.idle_add = MagicMock()
        fake_gi = ModuleType("gi")
        fake_repository = ModuleType("gi.repository")
        fake_repository.GLib = fake_glib
        fake_gi.repository = fake_repository

        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as handle:
            handle.write(
                "[DEFAULT]\n"
                "AutoInputSnapshotPath=/tmp/auto-helper-main.json\n"
                "AutoPvService=com.victronenergy.pvinverter.http_40\n"
                "AutoUseDcPv=0\n"
                "AutoBatteryService=\n"
                "AutoBatteryServicePrefix=com.example.none\n"
                "AutoGridL1Path=\n"
                "AutoGridL2Path=\n"
                "AutoGridL3Path=\n"
            )
            config_path = handle.name
        self.addCleanup(lambda: os.path.exists(config_path) and os.unlink(config_path))

        with patch.dict(
            sys.modules,
            {
                "dbus": fake_dbus,
                "dbus.mainloop": fake_dbus_mainloop,
                "dbus.mainloop.glib": fake_dbus_glib,
                "gi": fake_gi,
                "gi.repository": fake_repository,
                "gi.repository.GLib": fake_glib,
            },
            clear=False,
        ):
            with patch.object(sys, "argv", [helper_path, config_path]):
                with self.assertRaises(SystemExit) as raised:
                    runpy.run_path(helper_path, run_name="__main__")

        self.assertEqual(raised.exception.code, 0)

    def test_package_facades_reexport_expected_symbols(self) -> None:
        with patch.dict(
            sys.modules,
            {
                "vedbus": MagicMock(),
                "dbus": MagicMock(),
                "dbus.mainloop.glib": MagicMock(),
                "gi": MagicMock(),
                "gi.repository": MagicMock(),
                "gi.repository.GLib": MagicMock(),
            },
            clear=False,
        ):
            bootstrap_facade = import_module("venus_evcharger.app.bootstrap")
            main_facade = import_module("venus_evcharger.app.main")
            helper_facade = import_module("venus_evcharger.inputs.helper.main")
            runtime_module = import_module("venus_evcharger.runtime")

            self.assertTrue(hasattr(bootstrap_facade, "ServiceBootstrapController"))
            self.assertTrue(hasattr(main_facade, "ShellyWallboxService"))
            self.assertTrue(hasattr(helper_facade, "AutoInputHelper"))
            self.assertTrue(hasattr(helper_facade, "main"))
            self.assertTrue(hasattr(runtime_module, "RuntimeSupportController"))

            with self.assertRaises(AttributeError):
                getattr(runtime_module, "DoesNotExist")

    def test_type_checking_only_import_paths_can_be_executed(self) -> None:
        module_path = self._repo_file("venus_evcharger_service.py")
        fake_modules, _bootstrap_module = self._fake_main_module_dependencies()
        with patch.dict(
            sys.modules,
            {
                **fake_modules,
                "vedbus": MagicMock(),
            },
            clear=False,
        ):
            import typing
            import venus_evcharger.runtime as runtime_module
            import venus_evcharger.service.factory as factory_module

            with patch.object(typing, "TYPE_CHECKING", True):
                module_globals = runpy.run_path(module_path, run_name="venus_evcharger_service_type_checking_test")
                reload(runtime_module)
                reload(factory_module)

            self.assertIn("FormatterMap", module_globals)
            self.assertTrue(hasattr(runtime_module, "RuntimeSupportController"))

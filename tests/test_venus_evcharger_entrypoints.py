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

        facade_modules = {
            name: ModuleType(name)
            for name in (
                "venus_evcharger.service.auto_facade",
                "venus_evcharger.service.control",
                "venus_evcharger.service.controller_owner",
                "venus_evcharger.service.runtime_facade",
                "venus_evcharger.service.state_facade",
                "venus_evcharger.service.update_facade",
            )
        }
        facade_modules["venus_evcharger.service.auto_facade"].ServiceAutoFacade = type(
            "ServiceAutoFacade", (), {}
        )
        facade_modules["venus_evcharger.service.control"].ServiceControlFacade = type(
            "ServiceControlFacade", (), {}
        )
        facade_modules["venus_evcharger.service.controller_owner"].ServiceControllerOwner = type(
            "ServiceControllerOwner", (), {}
        )
        facade_modules["venus_evcharger.service.controller_owner"].ServiceFunctionBundle = type(
            "ServiceFunctionBundle", (), {}
        )
        facade_modules["venus_evcharger.service.runtime_facade"].ServiceRuntimeFacade = type(
            "ServiceRuntimeFacade", (), {}
        )

        class ServiceStateFacade:
            @staticmethod
            def config_path():
                return "/tmp/config.venus_evcharger.ini"

        facade_modules["venus_evcharger.service.state_facade"].ServiceStateFacade = ServiceStateFacade
        facade_modules["venus_evcharger.service.update_facade"].ServiceUpdateFacade = type(
            "ServiceUpdateFacade", (), {}
        )

        fake_glib = MagicMock()
        fake_gi = ModuleType("gi")
        fake_repository = ModuleType("gi.repository")
        fake_repository.GLib = fake_glib
        fake_gi.repository = fake_repository

        return {
            "venus_evcharger.bootstrap.controller": bootstrap_module,
            "venus_evcharger.core.common": common_module,
            **facade_modules,
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
        fake_loop = MagicMock()

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

        with patch.object(sys, "argv", [helper_path, config_path]), patch(
            "venus_evcharger.inputs.helper.glib_runtime.GLIB_RUNTIME.create_main_loop",
            return_value=fake_loop,
        ), patch("venus_evcharger.inputs.helper.glib_runtime.GLIB_RUNTIME.timeout_add"), patch(
            "venus_evcharger.inputs.helper.glib_runtime.GLIB_RUNTIME.idle_add"
        ):
            with self.assertRaises(SystemExit) as raised:
                runpy.run_path(helper_path, run_name="__main__")

        self.assertEqual(raised.exception.code, 0)

    def test_package_entrypoints_export_only_canonical_symbols(self) -> None:
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
            helper_module = import_module("venus_evcharger_auto_input_helper")
            runtime_module = import_module("venus_evcharger.runtime")

            self.assertTrue(hasattr(bootstrap_facade, "ServiceBootstrapController"))
            self.assertTrue(hasattr(main_facade, "ShellyWallboxService"))
            self.assertTrue(hasattr(helper_module, "AutoInputHelper"))
            self.assertTrue(hasattr(helper_module, "main"))
            self.assertTrue(hasattr(runtime_module, "RuntimeSupportController"))

            with self.assertRaises(AttributeError):
                getattr(runtime_module, "DoesNotExist")

    def test_entrypoint_has_no_retired_service_base(self) -> None:
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
            import venus_evcharger.runtime as runtime_module
            module_globals = runpy.run_path(module_path, run_name="venus_evcharger_service_composition_test")
            reload(runtime_module)

            self.assertEqual(module_globals["ShellyWallboxService"].__bases__, (object,))
            self.assertTrue(hasattr(runtime_module, "RuntimeSupportController"))

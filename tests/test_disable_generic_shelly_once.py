"""Unit tests for the one-shot generic Shelly disable helper."""

import os
import runpy
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

sys.modules["dbus"] = MagicMock()

from venus_evcharger.ops import disable_generic_shelly_once  # noqa: E402
from venus_evcharger.ops.disable_generic_shelly_once import disable_matching_device, load_settings, matches_device  # noqa: E402


class TestDisableGenericShellyOnce(unittest.TestCase):
    def test_matches_device_prefers_ip(self):
        self.assertTrue(matches_device("98A316712F3C", "192.168.178.76", "98A316712F3C", "192.168.178.76", ""))
        self.assertFalse(matches_device("98A316712F3C", "192.168.178.77", "98A316712F3C", "192.168.178.76", ""))

    def test_matches_device_uses_mac_without_ip(self):
        self.assertTrue(matches_device("98A316712F3C", "192.168.178.77", "98A316712F3C", "", "98A316712F3C"))
        self.assertTrue(matches_device("98A316712F3C", "192.168.178.77", "98:A3:16:71:2F:3C", "", "98-A3-16-71-2F-3C"))
        self.assertFalse(matches_device("OTHER", "192.168.178.76", "OTHER", "", "98A316712F3C"))
        self.assertFalse(matches_device("OTHER", "192.168.178.76", "OTHER", "", ""))

    def test_as_bool_defaults_none_values(self):
        self.assertTrue(disable_generic_shelly_once._as_bool(None, True))
        self.assertFalse(disable_generic_shelly_once._as_bool(None, False))

    def test_load_settings_uses_host_as_default_ip(self):
        with tempfile.NamedTemporaryFile("w+", suffix=".ini") as handle:
            handle.write("[DEFAULT]\nHost=192.168.178.76\n")
            handle.flush()
            settings = load_settings(handle.name)

        self.assertEqual(settings["target_ip"], "192.168.178.76")
        self.assertEqual(settings["channel"], 1)
        self.assertEqual(settings["delay_seconds"], 180.0)

    def test_load_settings_rejects_missing_or_incomplete_config(self):
        with self.assertRaisesRegex(ValueError, "Unable to read config file"):
            load_settings("/tmp/does-not-exist.ini")

        with tempfile.NamedTemporaryFile("w+", suffix=".ini") as handle:
            handle.write("[DEFAULT]\n")
            handle.flush()
            with self.assertRaisesRegex(ValueError, "DEFAULT Host is required"):
                load_settings(handle.name)

    def test_load_settings_clamps_and_normalizes_optional_values(self):
        with tempfile.NamedTemporaryFile("w+", suffix=".ini") as handle:
            handle.write(
                "[DEFAULT]\n"
                "Host=192.168.178.76\n"
                "DisableGenericShellyDevice=0\n"
                "GenericShellyAllowPersistentDisable=yes\n"
                "GenericShellyService= com.example.shelly \n"
                "GenericShellyDisableIp=\n"
                "GenericShellyDisableMac=aa-bb cc:dd:ee:ff\n"
                "GenericShellyDisableChannel=0\n"
                "GenericShellyDisableDelaySeconds=-5\n"
            )
            handle.flush()
            settings = load_settings(handle.name)

        self.assertFalse(settings["enabled"])
        self.assertTrue(settings["allow_persistent_disable"])
        self.assertEqual(settings["service"], "com.example.shelly")
        self.assertEqual(settings["target_ip"], "")
        self.assertEqual(settings["target_mac"], "AABBCCDDEEFF")
        self.assertEqual(settings["channel"], 1)
        self.assertEqual(settings["delay_seconds"], 0.0)

    def test_load_settings_uses_default_service_when_override_is_blank(self):
        with tempfile.NamedTemporaryFile("w+", suffix=".ini") as handle:
            handle.write(
                "[DEFAULT]\n"
                "Host=192.168.178.76\n"
                "GenericShellyService=   \n"
            )
            handle.flush()
            settings = load_settings(handle.name)

        self.assertEqual(settings["service"], "com.victronenergy.shelly")

    def test_load_settings_falls_back_for_invalid_numeric_values(self):
        with tempfile.NamedTemporaryFile("w+", suffix=".ini") as handle:
            handle.write(
                "[DEFAULT]\n"
                "Host=192.168.178.76\n"
                "GenericShellyDisableChannel=abc\n"
                "GenericShellyDisableDelaySeconds=never\n"
            )
            handle.flush()
            settings = load_settings(handle.name)

        self.assertEqual(settings["channel"], 1)
        self.assertEqual(settings["delay_seconds"], 180.0)

    def test_get_system_bus_prefers_session_bus_when_available(self):
        with patch.dict(os.environ, {"DBUS_SESSION_BUS_ADDRESS": "unix:path=/tmp/test-bus"}, clear=True):
            with patch.object(disable_generic_shelly_once.dbus, "SessionBus", return_value="session") as session_bus:
                self.assertEqual(disable_generic_shelly_once.get_system_bus(), "session")
        session_bus.assert_called_once_with()

        with patch.dict(os.environ, {}, clear=True):
            with patch.object(disable_generic_shelly_once.dbus, "SystemBus", return_value="system") as system_bus:
                self.assertEqual(disable_generic_shelly_once.get_system_bus(), "system")
        system_bus.assert_called_once_with(private=True)

    def test_dbus_helper_functions_cover_bus_item_and_introspection_paths(self):
        bus = MagicMock()
        bus.get_object.return_value = "object"
        iface = MagicMock()

        with patch.object(disable_generic_shelly_once.dbus, "Interface", return_value=iface) as interface_factory:
            iface.GetValue.return_value = 17
            value = disable_generic_shelly_once.get_dbus_value(bus, "svc", "/Value", timeout=2.5)
            self.assertEqual(value, 17)
            bus.get_object.assert_called_with("svc", "/Value", introspect=False)
            interface_factory.assert_called_with("object", "com.victronenergy.BusItem")
            iface.GetValue.assert_called_once_with(timeout=2.5)

            iface.reset_mock()
            interface_factory.reset_mock()
            with patch.object(disable_generic_shelly_once.dbus, "Int32", side_effect=lambda v: f"i{v}") as int32_ctor:
                disable_generic_shelly_once.set_dbus_value(bus, "svc", "/Value", 7, timeout=1.5)
            int32_ctor.assert_called_once_with(7)
            iface.SetValue.assert_called_once_with("i7", timeout=1.5)

            iface.reset_mock()
            disable_generic_shelly_once.set_dbus_value(bus, "svc", "/Value", "raw", timeout=1.0)
            iface.SetValue.assert_called_once_with("raw", timeout=1.0)

            iface.reset_mock()
            interface_factory.reset_mock()
            iface.Introspect.return_value = "<node><node name='a'/><node/><node name='b'/></node>"
            children = disable_generic_shelly_once.get_dbus_child_nodes(bus, "svc", "/Devices", timeout=0.5)
            self.assertEqual(children, ["a", "b"])
            bus.get_object.assert_called_with("svc", "/Devices", introspect=False)
            interface_factory.assert_called_with("object", "org.freedesktop.DBus.Introspectable")
            iface.Introspect.assert_called_once_with(timeout=0.5)

    def test_disable_matching_device_writes_only_when_enabled(self):
        settings = {
            "enabled": True,
            "allow_persistent_disable": True,
            "service": "com.victronenergy.shelly",
            "target_ip": "192.168.178.76",
            "target_mac": "98A316712F3C",
            "channel": 1,
        }

        def fake_get_value(service_name, path):
            values = {
                "/Devices/98A316712F3C/Ip": "192.168.178.76",
                "/Devices/98A316712F3C/Mac": "98A316712F3C",
                "/Devices/98A316712F3C/1/Enabled": 1,
            }
            return values[path]

        set_value = MagicMock()
        result = disable_matching_device(
            settings,
            lambda service_name, path: ["98A316712F3C"],
            fake_get_value,
            set_value,
        )

        self.assertEqual(result, "disabled")
        set_value.assert_called_once_with(
            "com.victronenergy.shelly",
            "/Devices/98A316712F3C/1/Enabled",
            0,
        )

    def test_disable_matching_device_skips_when_already_disabled(self):
        settings = {
            "enabled": True,
            "allow_persistent_disable": True,
            "service": "com.victronenergy.shelly",
            "target_ip": "192.168.178.76",
            "target_mac": "98A316712F3C",
            "channel": 1,
        }

        def fake_get_value(service_name, path):
            values = {
                "/Devices/98A316712F3C/Ip": "192.168.178.76",
                "/Devices/98A316712F3C/Mac": "98A316712F3C",
                "/Devices/98A316712F3C/1/Enabled": 0,
            }
            return values[path]

        set_value = MagicMock()
        result = disable_matching_device(
            settings,
            lambda service_name, path: ["98A316712F3C"],
            fake_get_value,
            set_value,
        )

        self.assertEqual(result, "already-disabled")
        set_value.assert_not_called()

    def test_disable_matching_device_respects_config_flags(self):
        settings = {
            "enabled": True,
            "allow_persistent_disable": False,
            "service": "com.victronenergy.shelly",
            "target_ip": "192.168.178.76",
            "target_mac": "98A316712F3C",
            "channel": 1,
        }

        set_value = MagicMock()
        result = disable_matching_device(
            settings,
            lambda service_name, path: ["98A316712F3C"],
            lambda service_name, path: None,
            set_value,
        )

        self.assertEqual(result, "persistent-disable-blocked")
        set_value.assert_not_called()

    def test_disable_matching_device_handles_disabled_config_missing_target_and_not_found(self):
        logger = MagicMock()
        disabled_result = disable_matching_device(
            {"enabled": False, "allow_persistent_disable": True, "service": "svc"},
            lambda *_args: [],
            lambda *_args: None,
            MagicMock(),
            logger,
        )
        self.assertEqual(disabled_result, "disabled-by-config")

        no_target_result = disable_matching_device(
            {
                "enabled": True,
                "allow_persistent_disable": True,
                "service": "svc",
                "target_ip": "",
                "target_mac": "",
                "channel": 1,
            },
            lambda *_args: [],
            lambda *_args: None,
            MagicMock(),
            logger,
        )
        self.assertEqual(no_target_result, "no-target")

        not_found_result = disable_matching_device(
            {
                "enabled": True,
                "allow_persistent_disable": True,
                "service": "svc",
                "target_ip": "192.168.178.76",
                "target_mac": "",
                "channel": 1,
            },
            lambda *_args: ["SERIAL"],
            lambda _service, path: {"Ip": "192.168.178.99", "Mac": "AABB"}.get(path.rsplit("/", 1)[-1]),
            MagicMock(),
            logger,
        )
        self.assertEqual(not_found_result, "not-found")

    def test_run_once_waits_then_wires_bus_helpers_into_disable_matching_device(self):
        settings = {
            "enabled": True,
            "allow_persistent_disable": True,
            "service": "svc",
            "target_ip": "192.168.178.76",
            "target_mac": "",
            "channel": 1,
            "delay_seconds": 2.0,
        }
        bus = MagicMock()
        set_value = MagicMock()

        def fake_get_value(_bus, _service, path, timeout=1.0):
            values = {
                "/Devices/SERIAL/Ip": "192.168.178.76",
                "/Devices/SERIAL/Mac": "AABBCCDDEEFF",
                "/Devices/SERIAL/1/Enabled": 1,
            }
            return values[path]

        with patch.object(disable_generic_shelly_once, "load_settings", return_value=settings):
            with patch.object(disable_generic_shelly_once, "get_system_bus", return_value=bus):
                with patch.object(disable_generic_shelly_once.time, "sleep") as sleep_mock:
                    with patch.object(disable_generic_shelly_once, "get_dbus_child_nodes", return_value=["SERIAL"]) as list_nodes:
                        with patch.object(disable_generic_shelly_once, "get_dbus_value", side_effect=fake_get_value) as get_value:
                            with patch.object(disable_generic_shelly_once, "set_dbus_value", side_effect=set_value) as set_value_func:
                                result = disable_generic_shelly_once.run_once("/tmp/config.ini")

        self.assertEqual(result, "disabled")
        sleep_mock.assert_called_once_with(2.0)
        list_nodes.assert_called_once_with(bus, "svc", "/Devices", timeout=1.0)
        self.assertEqual(get_value.call_count, 3)
        set_value_func.assert_called_once_with(bus, "svc", "/Devices/SERIAL/1/Enabled", 0, timeout=1.0)

    def test_main_returns_status_for_success_and_failure(self):
        with patch.object(disable_generic_shelly_once, "run_once", return_value="disabled") as run_once:
            with patch.object(disable_generic_shelly_once.logging, "basicConfig") as basic_config:
                with patch.object(disable_generic_shelly_once.logging, "info") as info_log:
                    self.assertEqual(disable_generic_shelly_once.main(["/tmp/example.ini"]), 0)
        basic_config.assert_called_once()
        run_once.assert_called_once_with("/tmp/example.ini")
        info_log.assert_called_with("Generic Shelly one-shot helper finished: %s", "disabled")

        with patch.object(disable_generic_shelly_once, "run_once", side_effect=RuntimeError("boom")):
            with patch.object(disable_generic_shelly_once.logging, "exception") as exception_log:
                self.assertEqual(disable_generic_shelly_once.main(["/tmp/example.ini"]), 1)
        exception_log.assert_called_once()

    def test_main_guard_raises_system_exit(self):
        module_path = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            "venus_evcharger",
            "ops",
            "disable_generic_shelly_once.py",
        )
        with tempfile.NamedTemporaryFile("w", suffix=".ini", delete=False) as handle:
            handle.write(
                "[DEFAULT]\n"
                "Host=192.168.178.76\n"
                "DisableGenericShellyDevice=0\n"
                "GenericShellyDisableDelaySeconds=0\n"
            )
            config_path = handle.name
        self.addCleanup(lambda: os.path.exists(config_path) and os.unlink(config_path))

        with patch.object(sys, "argv", [module_path, config_path]):
            with self.assertRaises(SystemExit) as raised:
                runpy.run_path(module_path, run_name="__main__")

        self.assertEqual(raised.exception.code, 0)

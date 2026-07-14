# SPDX-License-Identifier: GPL-3.0-or-later
import logging
import inspect
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

from venus_evcharger.ops import disable_generic_shelly_once as helper


class _StringValue:
    def __init__(self, value: str) -> None:
        self.value = value

    def __str__(self) -> str:
        return self.value


def _settings(**overrides):
    values = {
        "enabled": True,
        "allow_persistent_disable": True,
        "service": "svc",
        "target_ip": "ip",
        "target_mac": "",
        "channel": 1,
        "delay_seconds": 0.0,
        "gateway_run_dir": "run",
        "gateway_cache_path": "cache",
    }
    values.update(overrides)
    return values


class TestDisableGenericShellyOnceContracts(unittest.TestCase):
    def test_scalar_normalizers_have_exact_defaults_and_boundaries(self) -> None:
        self.assertIs(inspect.signature(helper._as_bool).parameters["default"].default, False)
        for value in ("1", "true", "TRUE", " yes ", "on"):
            self.assertTrue(helper._as_bool(value))
        for value in ("0", "false", "no", "off", "", object()):
            self.assertFalse(helper._as_bool(value))
        self.assertTrue(helper._as_bool(None, True))
        self.assertEqual(helper._as_int("2", 7), 2)
        self.assertEqual(helper._as_int(_StringValue("3"), 7), 3)
        self.assertEqual(helper._as_int("bad", 7), 7)
        self.assertEqual(helper._as_float("2.5", 7.5), 2.5)
        self.assertEqual(helper._as_float(_StringValue("3.5"), 7.5), 3.5)
        self.assertEqual(helper._as_float("bad", 7.5), 7.5)
        self.assertEqual(helper._normalize_mac(" aa:bb-cc dd "), "AABBCCDD")
        self.assertEqual(helper._normalize_mac(None), "")
        self.assertEqual(helper._normalized_channel(1), 1)
        self.assertEqual(helper._normalized_channel(0), 1)
        self.assertEqual(helper._normalized_channel("bad"), 1)
        self.assertEqual(helper._normalized_channel(2), 2)
        self.assertEqual(helper._normalized_delay_seconds(0), 0.0)
        self.assertEqual(helper._normalized_delay_seconds(-0.1), 0.0)
        self.assertEqual(helper._normalized_delay_seconds("bad"), 180.0)
        self.assertEqual(helper._normalized_delay_seconds(0.5), 0.5)

    def test_load_settings_returns_the_complete_normalized_contract(self) -> None:
        with tempfile.NamedTemporaryFile("w+", suffix=".ini") as handle:
            handle.write(
                "[DEFAULT]\n"
                "Host=192.0.2.1\n"
                "DisableGenericShellyDevice=no\n"
                "GenericShellyAllowPersistentDisable=yes\n"
                "GenericShellyService= svc \n"
                "GenericShellyDisableIp=192.0.2.2\n"
                "GenericShellyDisableMac=aa:bb\n"
                "GenericShellyDisableChannel=2\n"
                "GenericShellyDisableDelaySeconds=3.5\n"
                "DbusGatewayRunDir= /tmp/run \n"
                "DbusGatewayCachePath= /tmp/cache.json \n"
            )
            handle.flush()
            self.assertEqual(
                helper.load_settings(handle.name),
                {
                    "enabled": False,
                    "allow_persistent_disable": True,
                    "service": "svc",
                    "target_ip": "192.0.2.2",
                    "target_mac": "AABB",
                    "channel": 2,
                    "delay_seconds": 3.5,
                    "gateway_run_dir": "/tmp/run",
                    "gateway_cache_path": "/tmp/cache.json",
                },
            )

    def test_load_settings_default_contract_and_read_errors_are_exact(self) -> None:
        with tempfile.NamedTemporaryFile("w+", suffix=".ini") as handle:
            handle.write("[DEFAULT]\nHost=192.0.2.1\n")
            handle.flush()
            self.assertEqual(
                helper.load_settings(handle.name),
                {
                    "enabled": True,
                    "allow_persistent_disable": True,
                    "service": helper.DEFAULT_GENERIC_SHELLY_SERVICE,
                    "target_ip": "192.0.2.1",
                    "target_mac": "",
                    "channel": 1,
                    "delay_seconds": 180.0,
                    "gateway_run_dir": "/run/venus-evcharger",
                    "gateway_cache_path": "",
                },
            )
        missing_path = "/tmp/definitely-missing-evcharger-config.ini"
        with self.assertRaisesRegex(ValueError, f"^Unable to read config file: {missing_path}$"):
            helper.load_settings(missing_path)

    def test_load_settings_preserves_explicit_persistent_disable_block(self) -> None:
        with tempfile.NamedTemporaryFile("w+", suffix=".ini") as handle:
            handle.write(
                "[DEFAULT]\n"
                "Host=192.0.2.1\n"
                "GenericShellyAllowPersistentDisable=false\n"
            )
            handle.flush()
            self.assertFalse(helper.load_settings(handle.name)["allow_persistent_disable"])

    def test_required_host_and_device_matching_precedence_are_exact(self) -> None:
        section = {"Host": " host "}
        self.assertEqual(helper._required_host(section), "host")
        with self.assertRaisesRegex(ValueError, "DEFAULT Host is required in the config"):
            helper._required_host({"Host": " "})
        with self.assertRaisesRegex(ValueError, "^DEFAULT Host is required in the config$"):
            helper._required_host({})
        self.assertTrue(helper.matches_device("serial", " ip ", "other", "ip", "serial"))
        self.assertFalse(helper.matches_device("serial", "other", "serial", "ip", "serial"))
        self.assertTrue(helper.matches_device("aa-bb", None, None, "", "AA:BB"))
        self.assertFalse(helper.matches_device("aa-bb", None, None, "", ""))
        self.assertFalse(helper.matches_device("serial", None, None, "XXXX", ""))

    def test_direct_bus_entry_points_are_hard_disabled(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "^Direct DBus access is disabled; use the DBus gateway adapter$"):
            helper.get_system_bus()
        with self.assertRaisesRegex(RuntimeError, "^Direct DBus access is disabled; use the DBus gateway adapter$"):
            helper._bus_item_interface("bus", "service", "/path")

    def test_gateway_read_write_and_introspection_commands_are_exact(self) -> None:
        self.assertEqual(inspect.signature(helper.get_dbus_value).parameters["timeout"].default, 1.0)
        self.assertEqual(inspect.signature(helper.set_dbus_value).parameters["timeout"].default, 1.0)
        self.assertEqual(inspect.signature(helper.get_dbus_child_nodes).parameters["timeout"].default, 1.0)
        paths = SimpleNamespace(cache_path="cache", run_dir="run")
        with (
            patch.object(helper.DbusCacheStore, "load_snapshot", return_value={"snapshot": True}) as load,
            patch.object(helper.DbusCacheStore, "value_entry", side_effect=[{"value": 7}, None]) as entry,
        ):
            self.assertEqual(helper.get_dbus_value("cache", "svc", "/Value", timeout=9.0), 7)
            self.assertIsNone(helper.get_dbus_value("cache", "svc", "/Missing"))
        self.assertEqual(load.call_args_list, [call("cache"), call("cache")])
        self.assertEqual(
            entry.call_args_list,
            [
                call({"snapshot": True}, helper.dbus_path_key("svc", "/Value")),
                call({"snapshot": True}, helper.dbus_path_key("svc", "/Missing")),
            ],
        )

        write_client = MagicMock()
        with (
            patch.object(helper, "gateway_paths", return_value=paths) as write_paths,
            patch.object(helper, "GatewayClient", return_value=write_client) as write_client_type,
        ):
            self.assertTrue(helper.set_dbus_value("run", "svc", "/Value", 8, timeout=4.0))
        write_paths.assert_called_once_with("run")
        write_client_type.assert_called_once_with(paths)
        write_client.enqueue_command.assert_called_once_with(
            {
                "kind": "set_value",
                "source": "disable-generic-shelly-once",
                "service": "svc",
                "path": "/Value",
                "value": 8,
                "priority": "user",
                "coalesce_key": "svc:/Value",
            }
        )

        introspection_client = MagicMock()
        with (
            patch.object(helper, "gateway_paths", return_value=paths) as introspection_paths,
            patch.object(helper, "GatewayClient", return_value=introspection_client) as introspection_client_type,
            patch.object(helper.DbusCacheStore, "load_snapshot", return_value={"snapshot": True}) as introspection_load,
            patch.object(
                helper.DbusCacheStore,
                "value_entry",
                side_effect=[None, {"value": "<node><node name='a'/><node/><node name='b'/></node>"}],
            ) as introspection_entry,
        ):
            self.assertEqual(helper.get_dbus_child_nodes("run", "svc", "/Devices"), [])
            self.assertEqual(helper.get_dbus_child_nodes("run", "svc", "/Devices"), ["a", "b"])
        self.assertEqual(introspection_paths.call_args_list, [call("run"), call("run")])
        self.assertEqual(introspection_client_type.call_args_list, [call(paths)])
        self.assertEqual(introspection_load.call_args_list, [call("cache"), call("cache")])
        self.assertEqual(
            introspection_entry.call_args_list,
            [
                call({"snapshot": True}, "introspection:svc:/Devices"),
                call({"snapshot": True}, "introspection:svc:/Devices"),
            ],
        )
        introspection_client.enqueue_command.assert_called_once_with(
            {
                "kind": "introspect",
                "source": "disable-generic-shelly-once",
                "service": "svc",
                "path": "/Devices",
                "priority": "discovery",
                "coalesce_key": "introspect:svc:/Devices",
            }
        )
        with (
            patch.object(helper, "gateway_paths", return_value=paths),
            patch.object(helper.DbusCacheStore, "load_snapshot", return_value={"snapshot": True}),
            patch.object(helper.DbusCacheStore, "value_entry", return_value={}),
        ):
            with self.assertRaises(KeyError):
                helper.get_dbus_child_nodes("run", "svc", "/Devices")

    def test_preconditions_have_exact_results_and_logs(self) -> None:
        logger = MagicMock()
        cases = (
            (_settings(enabled=False), "disabled-by-config", call("Generic Shelly one-shot helper disabled by config")),
            (
                _settings(allow_persistent_disable=False),
                "persistent-disable-blocked",
                call("Generic Shelly one-shot helper blocked by config"),
            ),
        )
        for settings, expected, expected_log in cases:
            logger.reset_mock()
            self.assertEqual(helper._disable_precondition_result(settings, logger), expected)
            self.assertEqual(logger.info.call_args, expected_log)
        logger.reset_mock()
        settings = _settings(target_ip="", target_mac="")
        self.assertEqual(helper._disable_precondition_result(settings, logger), "no-target")
        logger.warning.assert_called_once_with("Generic Shelly one-shot helper has no target IP or MAC configured")
        self.assertIsNone(
            helper._disable_precondition_result(
                _settings(target_ip="192.0.2.1"),
                logger,
            )
        )
        self.assertTrue(helper._has_no_disable_target(_settings(target_ip="", target_mac="")))
        self.assertFalse(helper._has_no_disable_target(_settings(target_ip="", target_mac="aa")))
        self.assertFalse(helper._has_no_disable_target(_settings(target_ip="ip")))
        logger.reset_mock()
        self.assertIsNone(
            helper._disable_precondition_result(
                _settings(target_ip="ip"),
                logger,
            )
        )

    def test_device_helpers_forward_exact_paths_values_and_logs(self) -> None:
        settings = _settings(target_ip="ip", target_mac="mac", channel=2)
        get_value = MagicMock(side_effect=["ip", "mac"])
        with patch.object(helper, "matches_device", return_value=True) as matches:
            self.assertTrue(helper._device_matches_target(settings, "svc", "serial", get_value))
        self.assertEqual(
            get_value.call_args_list,
            [call("svc", "/Devices/serial/Ip"), call("svc", "/Devices/serial/Mac")],
        )
        matches.assert_called_once_with("serial", "ip", "mac", "ip", "mac")
        with patch.object(helper, "matches_device", return_value=False) as matches_defaults:
            self.assertFalse(
                helper._device_matches_target(
                    _settings(target_ip="", target_mac=""),
                    "svc",
                    "serial",
                    MagicMock(return_value=None),
                )
            )
        matches_defaults.assert_called_once_with("serial", None, None, "", "")
        self.assertEqual(helper._enabled_path(settings, "serial"), "/Devices/serial/2/Enabled")
        disabled_get = MagicMock(return_value=None)
        self.assertTrue(helper._device_already_disabled("svc", "/enabled", disabled_get))
        disabled_get.assert_called_once_with("svc", "/enabled")
        enabled_get = MagicMock(return_value=1)
        self.assertFalse(helper._device_already_disabled("svc", "/enabled", enabled_get))
        enabled_get.assert_called_once_with("svc", "/enabled")

        logger = MagicMock()
        set_value = MagicMock()
        self.assertEqual(
            helper._disable_device_channel(settings, "svc", "serial", MagicMock(return_value=0), set_value, logger),
            "already-disabled",
        )
        logger.info.assert_called_once_with(
            "Generic Shelly device %s already disabled on %s", "serial", "/Devices/serial/2/Enabled"
        )
        set_value.assert_not_called()
        logger.reset_mock()
        enabled_channel_get = MagicMock(return_value=1)
        self.assertEqual(
            helper._disable_device_channel(settings, "svc", "serial", enabled_channel_get, set_value, logger),
            "disabled",
        )
        enabled_channel_get.assert_called_once_with("svc", "/Devices/serial/2/Enabled")
        set_value.assert_called_once_with("svc", "/Devices/serial/2/Enabled", 0)
        logger.info.assert_called_once_with(
            "Disabled generic Shelly device %s on %s", "serial", "/Devices/serial/2/Enabled"
        )

    def test_disable_matching_device_short_circuits_and_stops_at_first_match(self) -> None:
        settings = _settings()
        list_nodes = MagicMock(return_value=["first", "second"])
        logger = MagicMock()
        get_value_callback = MagicMock()
        set_value_callback = MagicMock()
        with (
            patch.object(helper, "_disable_precondition_result", return_value=None) as precondition,
            patch.object(helper, "_device_matches_target", side_effect=[False, True]) as matches,
            patch.object(helper, "_disable_device_channel", return_value="disabled") as disable,
        ):
            self.assertEqual(
                helper.disable_matching_device(
                    settings,
                    list_nodes,
                    get_value_callback,
                    set_value_callback,
                    logger,
                ),
                "disabled",
            )
        precondition.assert_called_once_with(settings, logger)
        list_nodes.assert_called_once_with("svc", "/Devices")
        self.assertEqual(
            matches.call_args_list,
            [
                call(settings, "svc", "first", get_value_callback),
                call(settings, "svc", "second", get_value_callback),
            ],
        )
        self.assertEqual(disable.call_args.args[:3], (settings, "svc", "second"))
        self.assertIs(disable.call_args.args[3], get_value_callback)
        self.assertIs(disable.call_args.args[4], set_value_callback)
        self.assertIs(disable.call_args.args[5], logger)

        logger.reset_mock()
        with patch.object(helper, "_disable_precondition_result", return_value="blocked"):
            self.assertEqual(helper.disable_matching_device(settings, list_nodes, MagicMock(), MagicMock(), logger), "blocked")
        logger.assert_not_called()

        list_nodes = MagicMock(return_value=[])
        with patch.object(helper, "_disable_precondition_result", return_value=None):
            self.assertEqual(helper.disable_matching_device(settings, list_nodes, MagicMock(), MagicMock(), logger), "not-found")
        logger.info.assert_called_once_with(
            "No matching generic Shelly device found for IP %s MAC %s", "ip", ""
        )

    def test_run_once_wires_delayed_gateway_callbacks_exactly(self) -> None:
        self.assertEqual(inspect.signature(helper.run_once).parameters["config_path"].default, helper.DEFAULT_CONFIG_PATH)
        settings = _settings(delay_seconds=2.0)

        def exercise_callbacks(_settings, list_nodes, get_value, set_value):
            self.assertEqual(list_nodes("svc", "/nodes"), ["n"])
            self.assertEqual(get_value("svc", "/value"), 3)
            self.assertEqual(set_value("svc", "/value", 4), True)
            return "done"

        with (
            patch.object(helper, "load_settings", return_value=settings) as load,
            patch.object(helper.time, "sleep") as sleep,
            patch.object(helper.logging, "info") as info,
            patch.object(helper, "get_dbus_child_nodes", return_value=["n"]) as nodes,
            patch.object(helper, "get_dbus_value", return_value=3) as get_value,
            patch.object(helper, "set_dbus_value", return_value=True) as set_value,
            patch.object(helper, "disable_matching_device", side_effect=exercise_callbacks) as disable,
        ):
            self.assertEqual(helper.run_once("config"), "done")
        load.assert_called_once_with("config")
        info.assert_called_once_with("Waiting %.0f seconds before generic Shelly one-shot check", 2.0)
        sleep.assert_called_once_with(2.0)
        nodes.assert_called_once_with("run", "svc", "/nodes", timeout=1.0)
        get_value.assert_called_once_with("cache", "svc", "/value", timeout=1.0)
        set_value.assert_called_once_with("run", "svc", "/value", 4, timeout=1.0)
        disable.assert_called_once()

        no_delay_settings = _settings(gateway_run_dir="", gateway_cache_path="")
        default_paths = SimpleNamespace(cache_path="default-cache")
        with (
            patch.object(helper, "load_settings", return_value=no_delay_settings),
            patch.object(helper.time, "sleep") as no_sleep,
            patch.object(helper, "gateway_paths", return_value=default_paths) as paths,
            patch.object(helper, "disable_matching_device", return_value="done") as no_delay_disable,
        ):
            self.assertEqual(helper.run_once("config"), "done")
        no_sleep.assert_not_called()
        paths.assert_called_once_with("/run/venus-evcharger")
        callbacks = no_delay_disable.call_args.args
        self.assertIs(callbacks[0], no_delay_settings)

        short_delay_settings = _settings(delay_seconds=0.5)
        with (
            patch.object(helper, "load_settings", return_value=short_delay_settings),
            patch.object(helper.time, "sleep") as short_sleep,
            patch.object(helper, "disable_matching_device", return_value="done"),
        ):
            self.assertEqual(helper.run_once("config"), "done")
        short_sleep.assert_called_once_with(0.5)

    def test_main_uses_default_or_explicit_config_and_exact_logging(self) -> None:
        with (
            patch.object(sys, "argv", ["helper"]),
            patch.object(helper, "run_once", return_value="done") as run,
            patch.object(helper.logging, "basicConfig") as basic,
            patch.object(helper.logging, "info") as info,
        ):
            self.assertEqual(helper.main(), 0)
        run.assert_called_once_with(helper.DEFAULT_CONFIG_PATH)
        basic.assert_called_once_with(level=logging.INFO, format="%(levelname)s %(message)s")
        info.assert_called_once_with("Generic Shelly one-shot helper finished: %s", "done")

        with (
            patch.object(sys, "argv", ["helper", "from-sys-argv"]),
            patch.object(helper, "run_once", return_value="done") as argv_run,
        ):
            self.assertEqual(helper.main(), 0)
        argv_run.assert_called_once_with("from-sys-argv")

        error = RuntimeError("failed")
        with (
            patch.object(helper, "run_once", side_effect=error) as failed_run,
            patch.object(helper.logging, "exception") as exception,
        ):
            self.assertEqual(helper.main(["explicit"]), 1)
        failed_run.assert_called_once_with("explicit")
        exception.assert_called_once_with("Generic Shelly one-shot helper failed: %s", error)


if __name__ == "__main__":
    unittest.main()

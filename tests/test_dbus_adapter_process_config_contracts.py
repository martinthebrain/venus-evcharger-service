#!/usr/bin/env python3
"""Behavioral contracts for DBus adapter configuration builders."""

from __future__ import annotations

import tempfile
import unittest
from configparser import SectionProxy
from pathlib import Path

import venus_evcharger.dbus_adapter.process.config as config
from venus_evcharger.dbus_adapter.jsonl import DEFAULT_COMMAND_LIFECYCLE_MAX_BYTES, DEFAULT_HEALTH_HISTORY_MAX_BYTES
from venus_evcharger.dbus_gateway import gateway_paths


def defaults(values: dict[str, str] | None = None) -> SectionProxy:
    parser = config.CasePreservingConfigParser()
    parser["DEFAULT"] = values or {}
    return parser["DEFAULT"]


class DbusAdapterProcessConfigContractTests(unittest.TestCase):
    def test_load_config_is_fail_fast_and_preserves_option_case(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "config.ini"
            path.write_text("[DEFAULT]\nAutoPvPath=/Ac/Power\nautopvpath=lower\n", encoding="utf-8")
            loaded = config.load_adapter_config(str(path))
            self.assertEqual(loaded["DEFAULT"]["AutoPvPath"], "/Ac/Power")
            self.assertEqual(loaded["DEFAULT"]["autopvpath"], "lower")
        with self.assertRaisesRegex(ValueError, "Unable to read config file: /missing/config.ini"):
            config.load_adapter_config("/missing/config.ini")

    def test_identity_defaults_custom_values_and_invalid_instances(self) -> None:
        self.assertEqual(config.configured_device_instance(defaults()), 60)
        self.assertEqual(config.evcharger_service_name(defaults()), "com.victronenergy.evcharger.http_60")
        self.assertEqual(config.configured_device_instance(defaults({"DeviceInstance": " 61 "})), 61)
        self.assertEqual(config.configured_device_instance(defaults({"DeviceInstance": ""})), 60)
        self.assertEqual(config.configured_device_instance(defaults({"DeviceInstance": "bad"})), 60)
        self.assertEqual(
            config.evcharger_service_name(defaults({"ServiceName": " com.example.ev ", "DeviceInstance": "7"})),
            "com.example.ev.http_7",
        )
        self.assertEqual(config.evcharger_service_name(defaults({"ServiceName": ""})), "com.victronenergy.evcharger.http_60")

    def test_truthy_and_battery_service_normalization_are_explicit(self) -> None:
        for value in ("1", " true ", "YES", "On"):
            self.assertTrue(config._truthy(value))
        for value in (None, "", "0", "false", "off", "maybe"):
            self.assertFalse(config._truthy(value))
        self.assertEqual(config._battery_service(defaults()), "")
        self.assertEqual(config._battery_service(defaults({"AutoBatteryService": " battery.service "})), "battery.service")
        self.assertEqual(config._battery_service(defaults({"AutoBatteryService": "battery.example"})), "")

    def test_default_read_specs_are_complete_and_semantic(self) -> None:
        self.assertEqual(
            config.configured_read_specs(defaults()),
            {
                "grid_power_w": {
                    "service": "com.victronenergy.system",
                    "paths": ["/Ac/Grid/L1/Power", "/Ac/Grid/L2/Power", "/Ac/Grid/L3/Power"],
                    "interval": 2.0,
                    "aggregate": "sum",
                    "priority": "read",
                },
                "pv_power_w": {
                    "service": "",
                    "prefix": "com.victronenergy.pvinverter",
                    "path": "/Ac/Power",
                    "dc_service": "com.victronenergy.system",
                    "dc_path": "/Dc/Pv/Power",
                    "use_dc_pv": True,
                    "interval": 2.0,
                    "aggregate": "pv-total",
                    "priority": "read",
                    "optional_zero_on_error": True,
                    "optional_confidence": 0.2,
                },
                "battery_soc": {
                    "service": "",
                    "prefix": "com.victronenergy.battery",
                    "path": "/Dc/Battery/Soc",
                    "aggregate": "first-service",
                    "interval": 2.0,
                    "priority": "read",
                },
            },
        )

    def test_custom_read_specs_strip_values_filter_paths_and_pin_battery(self) -> None:
        source = defaults(
            {
                "AutoGridService": " grid.service ",
                "AutoGridL1Path": " /L1 ",
                "AutoGridL2Path": "",
                "AutoGridL3Path": " /L3 ",
                "AutoPvService": " pv.service ",
                "AutoPvServicePrefix": " pv.prefix ",
                "AutoPvPath": " /Pv ",
                "AutoDcPvService": " dc.service ",
                "AutoDcPvPath": " /Dc ",
                "AutoUseDcPv": "off",
                "AutoBatteryService": " battery.service ",
                "AutoBatteryServicePrefix": " battery.prefix ",
                "AutoBatterySocPath": " /Soc ",
            }
        )
        specs = config.configured_read_specs(source)
        self.assertEqual(specs["grid_power_w"]["service"], "grid.service")
        self.assertEqual(specs["grid_power_w"]["paths"], ["/L1", "/L3"])
        self.assertEqual(
            specs["pv_power_w"],
            {
                "service": "pv.service",
                "prefix": "pv.prefix",
                "path": "/Pv",
                "dc_service": "dc.service",
                "dc_path": "/Dc",
                "use_dc_pv": False,
                "interval": 2.0,
                "aggregate": "pv-total",
                "priority": "read",
                "optional_zero_on_error": True,
                "optional_confidence": 0.2,
            },
        )
        self.assertEqual(
            specs["battery_soc"],
            {
                "service": "battery.service",
                "prefix": "battery.prefix",
                "path": "/Soc",
                "aggregate": "",
                "interval": 2.0,
                "priority": "read",
            },
        )

    def test_rate_timing_and_slo_defaults_and_clamps(self) -> None:
        self.assertEqual(config.rate_settings(defaults()), config.GatewayRateSettings(0.25, 0.35, 2.0))
        self.assertEqual(config.timing_settings(defaults()), config.GatewayTimingSettings(0.2, 1.0, 900.0, 0.0))
        self.assertEqual(config.slo_settings(defaults()), config.GatewaySloSettings(2.0, 5.0, 10.0, 500.0))
        custom = defaults(
            {
                "DbusGatewayReadIntervalSeconds": "0.11",
                "DbusGatewayWriteIntervalSeconds": "0.22",
                "DbusGatewayIntrospectionIntervalSeconds": "0.33",
                "DbusGatewayTickSeconds": "0.01",
                "DbusGatewayMinTickSeconds": "0.01",
                "DbusGatewayMaxTickSeconds": "0.02",
                "DbusGatewayServiceListIntervalSeconds": "42",
                "DbusGatewayCachePublishIntervalSeconds": "-1",
                "DbusGatewaySloGuiMaxAgeSeconds": "0",
                "DbusGatewaySloCoreReadMaxAgeSeconds": "0.2",
                "DbusGatewaySloQueueMaxAgeSeconds": "0",
                "DbusGatewaySloMainloopGapMaxMs": "1",
            }
        )
        self.assertEqual(config.rate_settings(custom), config.GatewayRateSettings(0.11, 0.22, 0.33))
        self.assertEqual(config.timing_settings(custom), config.GatewayTimingSettings(0.05, 0.05, 42.0, 0.0))
        self.assertEqual(config.slo_settings(custom), config.GatewaySloSettings(0.1, 0.2, 0.1, 10.0))
        self.assertEqual(
            config.timing_settings(defaults({"DbusGatewayTickSeconds": "0.7"})),
            config.GatewayTimingSettings(0.7, 1.0, 900.0, 0.0),
        )
        self.assertEqual(
            config.timing_settings(
                defaults(
                    {
                        "DbusGatewayTickSeconds": "0.7",
                        "DbusGatewayMinTickSeconds": "0.3",
                        "DbusGatewayMaxTickSeconds": "0.8",
                        "DbusGatewayCachePublishIntervalSeconds": "0.4",
                    }
                )
            ),
            config.GatewayTimingSettings(0.3, 0.8, 900.0, 0.4),
        )

    def test_file_and_introspection_settings_cover_defaults_custom_and_clamps(self) -> None:
        paths = gateway_paths("/run/test")
        self.assertEqual(
            config.file_settings(defaults(), paths),
            config.GatewayFileSettings(
                "/run/test/dbus-command-lifecycle.jsonl",
                DEFAULT_COMMAND_LIFECYCLE_MAX_BYTES,
                "/run/test/dbus-health-history.jsonl",
                10.0,
                DEFAULT_HEALTH_HISTORY_MAX_BYTES,
            ),
        )
        self.assertEqual(
            config.introspection_settings(defaults(), 61),
            config.GatewayIntrospectionSettings(
                "/run/dbus-venus-evcharger-dbus-map-61.json",
                "/run/dbus-venus-evcharger-dbus-map-requests-61.json",
                True,
            ),
        )
        custom = defaults(
            {
                "DbusGatewayCommandLifecyclePath": " /tmp/lifecycle.jsonl ",
                "DbusGatewayCommandLifecycleMaxBytes": "-1",
                "DbusGatewayHealthLogPath": " /tmp/health.jsonl ",
                "DbusGatewayHealthLogIntervalSeconds": "-1",
                "DbusGatewayHealthLogMaxBytes": "-1",
                "DbusIntrospectionSnapshotPath": " /tmp/snapshot.json ",
                "DbusIntrospectionRequestPath": " /tmp/request.json ",
                "DbusIntrospectionEnabled": "false",
            }
        )
        self.assertEqual(
            config.file_settings(custom, paths),
            config.GatewayFileSettings("/tmp/lifecycle.jsonl", 0, "/tmp/health.jsonl", 0.0, 0),
        )
        self.assertEqual(
            config.introspection_settings(custom, 61),
            config.GatewayIntrospectionSettings("/tmp/snapshot.json", "/tmp/request.json", False),
        )

        for key, value in (
            ("DbusGatewayCommandLifecyclePath", "relative.jsonl"),
            ("DbusGatewayHealthLogPath", "None"),
        ):
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, key):
                config.file_settings(defaults({key: value}), paths)
        for key, value in (
            ("DbusIntrospectionSnapshotPath", "."),
            ("DbusIntrospectionRequestPath", "/tmp/request.txt"),
        ):
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, key):
                config.introspection_settings(defaults({key: value}), 61)

    def test_adapter_settings_composes_all_sections_and_respects_explicit_paths(self) -> None:
        paths = gateway_paths("/run/explicit")
        settings = config.adapter_settings(defaults({"DbusGatewayStaleAfterSeconds": "12.5"}), explicit_paths=paths)
        empty = defaults()
        self.assertEqual(
            settings,
            config.GatewayAdapterSettings(
                paths=paths,
                service_name="com.victronenergy.evcharger.http_60",
                device_instance=60,
                read_specs=config.configured_read_specs(empty),
                rates=config.rate_settings(empty),
                timing=config.timing_settings(empty),
                slo=config.slo_settings(empty),
                files=config.file_settings(empty, paths),
                introspection=config.introspection_settings(empty, 60),
                stale_after_seconds=12.5,
            ),
        )
        self.assertEqual(config.adapter_settings(empty, explicit_paths=paths).stale_after_seconds, 10.0)
        self.assertEqual(config.adapter_settings(empty).paths, gateway_paths())
        configured_paths = config.adapter_settings(defaults({"DbusGatewayRunDir": "/run/configured"})).paths
        self.assertEqual(configured_paths.run_dir, "/run/configured")


if __name__ == "__main__":
    unittest.main()

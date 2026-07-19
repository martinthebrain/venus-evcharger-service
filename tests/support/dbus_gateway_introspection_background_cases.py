# SPDX-License-Identifier: GPL-3.0-or-later
"""Gateway adapter background introspection scheduling contracts."""

from __future__ import annotations

from tests.support.dbus_gateway_adapter_harness import (
    DbusAdapter,
    GatewayAdapterContractCase,
    MagicMock,
    Path,
    gateway_paths,
    install_mock,
    tempfile,
)


class GatewayIntrospectionBackgroundCases(GatewayAdapterContractCase):
    """Exercise background introspection scheduling contracts."""

    def test_gateway_background_introspection_spec_contracts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text(
                "[DEFAULT]\n"
                "DbusIntrospectionFullScanIntervalSeconds=1\n"
                "AutoGridService= com.victronenergy.system \n"
                "AutoGridL1Path= /Ac/Grid/L1/Power \n"
                "AutoGridL2Path=\n"
                "AutoGridL3Path=/Ac/Grid/L3/Power\n"
                "AutoBatteryServicePrefix=com.victronenergy.battery\n"
                "AutoPvServicePrefix=com.victronenergy.pvinverter\n",
                encoding="utf-8",
            )
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            adapter.cache.update_services(
                [
                    "com.victronenergy.battery.tty2",
                    "com.victronenergy.battery.tty1",
                    *(f"com.victronenergy.pvinverter.http_{index:02d}" for index in range(12)),
                    "com.victronenergy.system",
                ]
            )

            self.assertFalse(adapter.background_introspection_due(59.9))
            self.assertTrue(adapter.background_introspection_due(60.0))
            adapter._last_introspection_full_scan_at = 30.0
            self.assertFalse(adapter.background_introspection_due(40.0))
            adapter._last_introspection_full_scan_at = 0.0
            adapter.dbus_introspection_enabled = False
            self.assertFalse(adapter.background_introspection_due(61.0))
            adapter.dbus_introspection_enabled = True
            allows_priority = install_mock(adapter.circuit, "allows_priority", MagicMock(return_value=False))
            self.assertFalse(adapter.background_introspection_due(61.0))
            allows_priority.assert_called_with("discovery")

            self.assertEqual(
                adapter.grid_introspection_specs(),
                [
                    ("com.victronenergy.system", "/Ac/Grid/L1/Power", 80, "grid", "configured-grid-path"),
                    ("com.victronenergy.system", "/Ac/Grid/L3/Power", 80, "grid", "configured-grid-path"),
                ],
            )
            self.assertEqual(
                adapter.battery_introspection_specs(),
                [
                    ("com.victronenergy.battery.tty1", "/Soc", 70, "battery", "battery-service-discovery"),
                    ("com.victronenergy.battery.tty2", "/Soc", 70, "battery", "battery-service-discovery"),
                ],
            )
            pv_specs = adapter.pv_introspection_specs()
            self.assertEqual(len(pv_specs), 10)
            self.assertEqual(
                pv_specs[0], ("com.victronenergy.pvinverter.http_00", "/Ac/Power", 30, "pv", "pv-service-discovery")
            )
            self.assertEqual(pv_specs[-1][0], "com.victronenergy.pvinverter.http_09")

            adapter.config["DEFAULT"]["AutoBatteryService"] = "com.victronenergy.battery.tty2"
            self.assertEqual(
                adapter.configured_or_prefixed_services("AutoBatteryService", "Ignored", "x"),
                ["com.victronenergy.battery.tty2"],
            )

            default_config = Path(temp_dir) / "defaults.ini"
            default_config.write_text("[DEFAULT]\n", encoding="utf-8")
            default_adapter = DbusAdapter(
                str(default_config), paths=gateway_paths(str(Path(temp_dir) / "run-defaults"))
            )
            default_adapter.cache.update_services(["com.victronenergy.pvinverter.http_default"])
            self.assertFalse(default_adapter.background_introspection_due(21599.9))
            self.assertTrue(default_adapter.background_introspection_due(21600.0))
            self.assertEqual(
                default_adapter.grid_introspection_specs(),
                [
                    ("com.victronenergy.system", "/Ac/Grid/L1/Power", 80, "grid", "configured-grid-path"),
                    ("com.victronenergy.system", "/Ac/Grid/L2/Power", 80, "grid", "configured-grid-path"),
                    ("com.victronenergy.system", "/Ac/Grid/L3/Power", 80, "grid", "configured-grid-path"),
                ],
            )
            self.assertEqual(
                default_adapter.pv_introspection_specs(),
                [("com.victronenergy.pvinverter.http_default", "/Ac/Power", 30, "pv", "pv-service-discovery")],
            )

            custom_config = Path(temp_dir) / "custom.ini"
            custom_config.write_text(
                "[DEFAULT]\n"
                "AutoGridService=custom.grid\n"
                "AutoGridL1Path=/Custom/L1\n"
                "AutoGridL2Path=/Custom/L2\n"
                "AutoGridL3Path=/Custom/L3\n"
                "AutoBatterySocPath=/Custom/Soc\n"
                "AutoPvPath=/Custom/Pv\n",
                encoding="utf-8",
            )
            custom = DbusAdapter(str(custom_config), paths=gateway_paths(str(Path(temp_dir) / "run-custom")))
            custom.cache.update_services(["custom.battery.1", "custom.pv.1"])
            custom.config["DEFAULT"]["AutoBatteryServicePrefix"] = "custom.battery"
            custom.config["DEFAULT"]["AutoPvServicePrefix"] = "custom.pv"
            self.assertEqual(
                custom.grid_introspection_specs(),
                [
                    ("custom.grid", "/Custom/L1", 80, "grid", "configured-grid-path"),
                    ("custom.grid", "/Custom/L2", 80, "grid", "configured-grid-path"),
                    ("custom.grid", "/Custom/L3", 80, "grid", "configured-grid-path"),
                ],
            )
            self.assertEqual(
                custom.battery_introspection_specs(),
                [("custom.battery.1", "/Custom/Soc", 70, "battery", "battery-service-discovery")],
            )
            self.assertEqual(
                custom.pv_introspection_specs(), [("custom.pv.1", "/Custom/Pv", 30, "pv", "pv-service-discovery")]
            )

            explicit_config = Path(temp_dir) / "explicit.ini"
            explicit_config.write_text(
                "[DEFAULT]\n"
                "AutoBatteryService=custom.battery.explicit\n"
                "AutoBatteryServicePrefix=custom.battery\n"
                "AutoPvService=custom.pv.explicit\n"
                "AutoPvServicePrefix=custom.pv\n",
                encoding="utf-8",
            )
            explicit = DbusAdapter(str(explicit_config), paths=gateway_paths(str(Path(temp_dir) / "run-explicit")))
            explicit.cache.update_services(
                [
                    "custom.battery.explicit",
                    "custom.battery.other",
                    "custom.pv.explicit",
                    "custom.pv.other",
                ]
            )
            self.assertEqual(
                explicit.battery_introspection_specs(),
                [("custom.battery.explicit", "/Soc", 70, "battery", "battery-service-discovery")],
            )
            self.assertEqual(
                explicit.pv_introspection_specs(),
                [("custom.pv.explicit", "/Ac/Power", 30, "pv", "pv-service-discovery")],
            )

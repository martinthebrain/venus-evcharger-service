# SPDX-License-Identifier: GPL-3.0-or-later
from tests.companion_dbus_bridge_cases_common import *


class _CompanionDbusBridgeCoreCases:
    def test_bridge_register_service_writes_exact_common_and_specific_paths(self) -> None:
        import platform

        service = SimpleNamespace(
            dbus_gateway_run_dir="/run/evcharger",
            connection_name="HTTP",
            custom_name="EV Charger",
            firmware_version="FW-1",
            hardware_version="HW-1",
            serial="SERIAL",
        )
        bridge = EnergyCompanionDbusBridge(service, "/opt/venus-evcharger/service.py")

        with (
            patch("venus_evcharger.companion.dbus_bridge.gateway_paths", return_value="gateway-paths") as paths_factory,
            patch("venus_evcharger.companion.dbus_bridge.GatewayDbusServiceProxy", _FakeVeDbusService),
        ):
            registered = bridge._register_service(
                "com.victronenergy.example.external_42",
                42,
                "External Example",
                {"/Specific": 12.5},
            )

        paths_factory.assert_called_once_with("/run/evcharger")
        self.assertTrue(registered.registered)
        self.assertEqual(registered.name, "com.victronenergy.example.external_42")
        self.assertEqual(
            registered.paths,
            {
                "/Mgmt/ProcessName": "/opt/venus-evcharger/service.py",
                "/Mgmt/ProcessVersion": "Unknown version, and running on Python " + platform.python_version(),
                "/Mgmt/Connection": "HTTP",
                "/DeviceInstance": 42,
                "/ProductId": 0xFFFF,
                "/ProductName": "External Example",
                "/CustomName": "EV Charger External Example",
                "/FirmwareVersion": "FW-1",
                "/HardwareVersion": "HW-1",
                "/Serial": "SERIAL",
                "/Connected": 0,
                "/UpdateIndex": 0,
                "/Specific": 12.5,
            },
        )

    def test_bridge_register_service_uses_default_identity_and_gateway_paths_without_run_dir(self) -> None:
        bridge = EnergyCompanionDbusBridge(SimpleNamespace(dbus_gateway_run_dir=""), "/svc.py")

        with (
            patch("venus_evcharger.companion.dbus_bridge.gateway_paths", return_value="gateway-paths") as paths_factory,
            patch("venus_evcharger.companion.dbus_bridge.GatewayDbusServiceProxy", _FakeVeDbusService),
        ):
            registered = bridge._register_service("com.victronenergy.example.external_7", 7, "External Example", {})

        paths_factory.assert_called_once_with(None)
        self.assertEqual(registered.paths["/Mgmt/Connection"], "External energy companion")
        self.assertEqual(registered.paths["/CustomName"], "Venus EV Charger External Example")
        self.assertEqual(registered.paths["/FirmwareVersion"], "")
        self.assertEqual(registered.paths["/HardwareVersion"], "")
        self.assertEqual(registered.paths["/Serial"], "")

    def test_bridge_enabled_helper_requires_explicit_truthy_flag(self) -> None:
        self.assertFalse(EnergyCompanionDbusBridge._companion_bridge_enabled(SimpleNamespace()))
        self.assertFalse(EnergyCompanionDbusBridge._companion_bridge_enabled(SimpleNamespace(companion_dbus_bridge_enabled=False)))
        self.assertFalse(EnergyCompanionDbusBridge._companion_bridge_enabled(SimpleNamespace(companion_dbus_bridge_enabled=None)))
        self.assertTrue(EnergyCompanionDbusBridge._companion_bridge_enabled(SimpleNamespace(companion_dbus_bridge_enabled=True)))

        default_source_bridge = EnergyCompanionDbusBridge(SimpleNamespace(), "/tmp/service.py")
        disabled_source_bridge = EnergyCompanionDbusBridge(SimpleNamespace(companion_source_services_enabled=False), "/tmp/service.py")
        enabled_grid_bridge = EnergyCompanionDbusBridge(SimpleNamespace(companion_source_grid_services_enabled=True), "/tmp/service.py")
        missing_grid_bridge = EnergyCompanionDbusBridge(SimpleNamespace(), "/tmp/service.py")
        self.assertTrue(default_source_bridge._source_services_enabled())
        self.assertFalse(disabled_source_bridge._source_services_enabled())
        self.assertTrue(enabled_grid_bridge._source_grid_services_enabled())
        self.assertFalse(missing_grid_bridge._source_grid_services_enabled())

    def test_bridge_start_uses_base_device_instance_for_default_companion_services(self) -> None:
        service = SimpleNamespace(
            deviceinstance=20,
            companion_dbus_bridge_enabled=True,
            companion_battery_service_enabled=True,
            companion_pvinverter_service_enabled=True,
            companion_grid_service_enabled=True,
            connection_name="HTTP",
            custom_name="EV Charger",
            firmware_version="FW-1",
            hardware_version="HW-1",
            serial="SERIAL",
        )

        with patch("venus_evcharger.companion.dbus_bridge.GatewayDbusServiceProxy", _FakeVeDbusService):
            bridge = EnergyCompanionDbusBridge(service, "/tmp/service.py")
            bridge.start()

        self.assertEqual(bridge._battery_service.name, "com.victronenergy.battery.external_20")
        self.assertEqual(bridge._battery_service.paths["/DeviceInstance"], 60)
        self.assertEqual(bridge._pvinverter_service.name, "com.victronenergy.pvinverter.external_20")
        self.assertEqual(bridge._pvinverter_service.paths["/DeviceInstance"], 61)
        self.assertEqual(bridge._grid_service.name, "com.victronenergy.grid.external_20")
        self.assertEqual(bridge._grid_service.paths["/DeviceInstance"], 62)

    def test_bridge_start_defaults_base_device_instance_to_zero(self) -> None:
        service = SimpleNamespace(
            companion_dbus_bridge_enabled=True,
            companion_battery_service_enabled=True,
            companion_pvinverter_service_enabled=True,
            companion_grid_service_enabled=True,
        )

        with patch("venus_evcharger.companion.dbus_bridge.GatewayDbusServiceProxy", _FakeVeDbusService):
            bridge = EnergyCompanionDbusBridge(service, "/tmp/service.py")
            bridge.start()

        self.assertEqual(bridge._battery_service.name, "com.victronenergy.battery.external_0")
        self.assertEqual(bridge._battery_service.paths["/DeviceInstance"], 40)
        self.assertEqual(bridge._pvinverter_service.paths["/DeviceInstance"], 41)
        self.assertEqual(bridge._grid_service.paths["/DeviceInstance"], 42)

    def test_bridge_start_defaults_battery_and_pvinverter_enabled_but_grid_disabled(self) -> None:
        service = SimpleNamespace(companion_dbus_bridge_enabled=True, deviceinstance=3)

        with patch("venus_evcharger.companion.dbus_bridge.GatewayDbusServiceProxy", _FakeVeDbusService):
            bridge = EnergyCompanionDbusBridge(service, "/tmp/service.py")
            bridge.start()

        self.assertEqual(bridge._battery_service.name, "com.victronenergy.battery.external_3")
        self.assertEqual(bridge._battery_service.paths["/DeviceInstance"], 43)
        self.assertEqual(bridge._battery_service.paths["/ProductName"], "External Energy Battery")
        self.assertEqual(bridge._battery_service.paths["/Soc"], None)
        self.assertEqual(bridge._battery_service.paths["/Dc/0/Power"], 0.0)
        self.assertEqual(bridge._battery_service.paths["/Capacity"], None)
        self.assertEqual(bridge._pvinverter_service.name, "com.victronenergy.pvinverter.external_3")
        self.assertEqual(bridge._pvinverter_service.paths["/DeviceInstance"], 44)
        self.assertEqual(bridge._pvinverter_service.paths["/ProductName"], "External Energy PV")
        self.assertEqual(bridge._pvinverter_service.paths["/Ac/Power"], 0.0)
        self.assertEqual(bridge._pvinverter_service.paths["/Ac/L1/Power"], 0.0)
        self.assertEqual(bridge._pvinverter_service.paths["/Ac/L2/Power"], 0.0)
        self.assertEqual(bridge._pvinverter_service.paths["/Ac/L3/Power"], 0.0)
        self.assertIsNone(bridge._grid_service)

    def test_bridge_start_honors_all_explicit_main_service_names_and_device_instances(self) -> None:
        service = SimpleNamespace(
            companion_dbus_bridge_enabled=True,
            companion_battery_service_enabled=True,
            companion_pvinverter_service_enabled=True,
            companion_grid_service_enabled=True,
            companion_battery_service_name="com.victronenergy.battery.custom",
            companion_pvinverter_service_name="com.victronenergy.pvinverter.custom",
            companion_grid_service_name="com.victronenergy.grid.custom",
            companion_battery_deviceinstance=101,
            companion_pvinverter_deviceinstance=102,
            companion_grid_deviceinstance=103,
        )

        with patch("venus_evcharger.companion.dbus_bridge.GatewayDbusServiceProxy", _FakeVeDbusService):
            bridge = EnergyCompanionDbusBridge(service, "/tmp/service.py")
            bridge.start()

        self.assertEqual(bridge._battery_service.name, "com.victronenergy.battery.custom")
        self.assertEqual(bridge._battery_service.paths["/DeviceInstance"], 101)
        self.assertEqual(bridge._pvinverter_service.name, "com.victronenergy.pvinverter.custom")
        self.assertEqual(bridge._pvinverter_service.paths["/DeviceInstance"], 102)
        self.assertEqual(bridge._grid_service.name, "com.victronenergy.grid.custom")
        self.assertEqual(bridge._grid_service.paths["/DeviceInstance"], 103)
        self.assertEqual(bridge._grid_service.paths["/ProductName"], "External Energy Grid")
        self.assertEqual(bridge._grid_service.paths["/Ac/Power"], 0.0)
        self.assertEqual(bridge._grid_service.paths["/Ac/L1/Power"], 0.0)
        self.assertEqual(bridge._grid_service.paths["/Ac/L2/Power"], 0.0)
        self.assertEqual(bridge._grid_service.paths["/Ac/L3/Power"], 0.0)

    def test_bridge_start_does_not_replace_already_registered_main_services(self) -> None:
        bridge = EnergyCompanionDbusBridge(
            SimpleNamespace(
                companion_battery_service_enabled=True,
                companion_pvinverter_service_enabled=True,
                companion_grid_service_enabled=True,
            ),
            "/tmp/service.py",
        )
        bridge._battery_service = _FakeVeDbusService("battery-existing")
        bridge._pvinverter_service = _FakeVeDbusService("pvinverter-existing")
        bridge._grid_service = _FakeVeDbusService("grid-existing")

        bridge._ensure_battery_service(1)
        bridge._ensure_pvinverter_service(1)
        bridge._ensure_grid_service(1)

        self.assertEqual(bridge._battery_service.name, "battery-existing")
        self.assertEqual(bridge._pvinverter_service.name, "pvinverter-existing")
        self.assertEqual(bridge._grid_service.name, "grid-existing")

    def test_bridge_services_helpers_define_source_contracts(self) -> None:
        service = SimpleNamespace(
            companion_source_battery_deviceinstance_base=10,
            companion_source_pvinverter_deviceinstance_base=20,
            companion_source_grid_deviceinstance_base=30,
            companion_source_battery_service_prefix=" battery.prefix. ",
            companion_source_pvinverter_service_prefix=" pvinverter.prefix. ",
            companion_source_grid_service_prefix=" grid.prefix. ",
        )
        bridge = EnergyCompanionDbusBridge(service, "/tmp/service.py")

        self.assertEqual(bridge._source_device_instance("battery", 2), 12)
        self.assertEqual(bridge._source_device_instance("pvinverter", 2), 22)
        self.assertEqual(bridge._source_device_instance("grid", 2), 32)
        self.assertEqual(bridge._source_default_service_prefix("battery"), "com.victronenergy.battery.external")
        self.assertEqual(bridge._source_default_service_prefix("pvinverter"), "com.victronenergy.pvinverter.external")
        self.assertEqual(bridge._source_default_service_prefix("grid"), "com.victronenergy.grid.external")
        self.assertEqual(bridge._source_configured_service_prefix("battery"), "battery.prefix.")
        self.assertEqual(bridge._source_configured_service_prefix("pvinverter"), "pvinverter.prefix.")
        self.assertEqual(bridge._source_configured_service_prefix("grid"), "grid.prefix.")
        self.assertEqual(bridge._source_service_name("battery", "My Source!", 44), "battery.prefix.my_source")
        self.assertEqual(bridge._source_service_name("battery", "id", 44), "battery.prefix.id")
        self.assertEqual(bridge._source_service_name("battery", "", 44), "battery.prefix.44")
        self.assertEqual(bridge._source_product_label({"source_id": " main "}, "Battery"), "External Energy main Battery")
        self.assertEqual(bridge._source_product_label({}, "Grid"), "External Energy source Grid")
        self.assertEqual(bridge._sanitize_source_id(" A/B ++ C "), "a_b_c")
        self.assertEqual(bridge._sanitize_source_id(" X/A/X "), "x_a_x")
        self.assertEqual(bridge._sanitize_source_id(" !!! "), "source")
        default_prefix_bridge = EnergyCompanionDbusBridge(SimpleNamespace(), "/tmp/service.py")
        self.assertEqual(default_prefix_bridge._source_device_instance("battery", 5), 5)
        self.assertEqual(
            default_prefix_bridge._source_configured_service_prefix("battery"),
            "com.victronenergy.battery.external",
        )
        prefix_x_bridge = EnergyCompanionDbusBridge(
            SimpleNamespace(companion_source_battery_service_prefix="battery.prefixX."),
            "/tmp/service.py",
        )
        self.assertEqual(prefix_x_bridge._source_service_name("battery", "id", 44), "battery.prefixX.id")
        with self.assertRaisesRegex(ValueError, "Unsupported source service kind"):
            bridge._source_device_instance("unknown", 2)
        with self.assertRaisesRegex(ValueError, "Unsupported source service kind"):
            bridge._source_default_service_prefix("unknown")
        with self.assertRaisesRegex(ValueError, "Unsupported source service kind"):
            bridge._source_configured_service_prefix("unknown")

    def test_bridge_services_helpers_filter_sources_and_roles_exactly(self) -> None:
        snapshot = {
            "battery_sources": [
                "bad",
                {"source_id": ""},
                {"source_id": " battery ", "role": " battery "},
                {"source_id": "hybrid", "role": "hybrid-inverter", "grid_interaction_w": -1.0},
                {"source_id": "pv", "role": "inverter", "grid_interaction_w": "bad"},
            ],
        }

        normalized = EnergyCompanionDbusBridge._normalized_source_snapshots(snapshot)

        self.assertEqual([source["source_id"] for source in normalized], [" battery ", "hybrid", "pv"])
        normalized[0]["source_id"] = "changed"
        self.assertEqual(snapshot["battery_sources"][2]["source_id"], " battery ")
        self.assertEqual(EnergyCompanionDbusBridge._normalized_source_snapshots({}), ())
        self.assertTrue(EnergyCompanionDbusBridge._source_supports_battery_service({"role": " battery "}))
        self.assertTrue(EnergyCompanionDbusBridge._source_supports_battery_service({"role": "hybrid-inverter"}))
        self.assertFalse(EnergyCompanionDbusBridge._source_supports_battery_service({"role": "inverter"}))
        self.assertTrue(EnergyCompanionDbusBridge._source_supports_pvinverter_service({"role": "hybrid-inverter"}))
        self.assertTrue(EnergyCompanionDbusBridge._source_supports_pvinverter_service({"role": "inverter"}))
        self.assertFalse(EnergyCompanionDbusBridge._source_supports_pvinverter_service({"role": "battery"}))
        self.assertTrue(EnergyCompanionDbusBridge._source_supports_grid_service({"grid_interaction_w": 0.0}))
        self.assertFalse(EnergyCompanionDbusBridge._source_supports_grid_service({"grid_interaction_w": "0"}))
        self.assertEqual(EnergyCompanionDbusBridge._source_role({}), "")

    def test_bridge_ensure_source_services_reuse_and_register_exact_payloads(self) -> None:
        service = SimpleNamespace(
            companion_source_battery_deviceinstance_base=10,
            companion_source_pvinverter_deviceinstance_base=20,
            companion_source_grid_deviceinstance_base=30,
            companion_source_battery_service_prefix="battery.prefix",
            companion_source_pvinverter_service_prefix="pvinverter.prefix",
            companion_source_grid_service_prefix="grid.prefix",
        )
        bridge = EnergyCompanionDbusBridge(service, "/tmp/service.py")
        source = {"source_id": " my-source ", "role": "hybrid-inverter"}

        with patch("venus_evcharger.companion.dbus_bridge.GatewayDbusServiceProxy", _FakeVeDbusService):
            battery = bridge._ensure_source_battery_service(source, 2)
            pvinverter = bridge._ensure_source_pvinverter_service(source, 3)
            grid = bridge._ensure_source_grid_service(source, 4)

        self.assertIs(bridge._ensure_source_battery_service(source, 9), battery)
        self.assertIs(bridge._ensure_source_pvinverter_service(source, 9), pvinverter)
        self.assertIs(bridge._ensure_source_grid_service(source, 9), grid)
        self.assertEqual(battery.name, "battery.prefix.my_source")
        self.assertEqual(battery.paths["/DeviceInstance"], 12)
        self.assertEqual(battery.paths["/ProductName"], "External Energy my-source Battery")
        self.assertEqual(battery.paths["/Soc"], None)
        self.assertEqual(battery.paths["/Dc/0/Power"], 0.0)
        self.assertEqual(battery.paths["/Capacity"], None)
        self.assertEqual(pvinverter.name, "pvinverter.prefix.my_source")
        self.assertEqual(pvinverter.paths["/DeviceInstance"], 23)
        self.assertEqual(pvinverter.paths["/ProductName"], "External Energy my-source PV")
        self.assertEqual(pvinverter.paths["/Ac/Power"], 0.0)
        self.assertEqual(pvinverter.paths["/Ac/L1/Power"], 0.0)
        self.assertEqual(pvinverter.paths["/Ac/L2/Power"], 0.0)
        self.assertEqual(pvinverter.paths["/Ac/L3/Power"], 0.0)
        self.assertEqual(grid.name, "grid.prefix.my_source")
        self.assertEqual(grid.paths["/DeviceInstance"], 34)
        self.assertEqual(grid.paths["/ProductName"], "External Energy my-source Grid")
        self.assertEqual(grid.paths["/Ac/Power"], 0.0)
        self.assertEqual(grid.paths["/Ac/L1/Power"], 0.0)
        self.assertEqual(grid.paths["/Ac/L2/Power"], 0.0)
        self.assertEqual(grid.paths["/Ac/L3/Power"], 0.0)

    def test_bridge_ensure_source_services_use_device_instance_fallback_when_source_id_is_empty(self) -> None:
        service = SimpleNamespace(
            companion_source_battery_deviceinstance_base=10,
            companion_source_pvinverter_deviceinstance_base=20,
            companion_source_grid_deviceinstance_base=30,
            companion_source_battery_service_prefix="battery.prefix",
            companion_source_pvinverter_service_prefix="pvinverter.prefix",
            companion_source_grid_service_prefix="grid.prefix",
        )
        bridge = EnergyCompanionDbusBridge(service, "/tmp/service.py")

        with patch("venus_evcharger.companion.dbus_bridge.GatewayDbusServiceProxy", _FakeVeDbusService):
            battery = bridge._ensure_source_battery_service({}, 2)
            pvinverter = bridge._ensure_source_pvinverter_service({}, 3)
            grid = bridge._ensure_source_grid_service({}, 4)

        self.assertEqual(battery.name, "battery.prefix.12")
        self.assertEqual(pvinverter.name, "pvinverter.prefix.23")
        self.assertEqual(grid.name, "grid.prefix.34")

    def test_bridge_source_publish_returns_explicit_false_without_sources(self) -> None:
        bridge = EnergyCompanionDbusBridge(SimpleNamespace(), "/tmp/service.py")
        self.assertIs(bridge._publish_source_snapshots({"battery_sources": []}, 100.0), False)

    def test_bridge_publish_queues_when_called_outside_mainloop_thread(self) -> None:
        service = SimpleNamespace(
            companion_dbus_bridge_enabled=True,
            _dbus_publish_direct_allowed=MagicMock(return_value=False),
            _enqueue_companion_dbus_publish=MagicMock(return_value=True),
        )
        bridge = EnergyCompanionDbusBridge(service, "/tmp/service.py")

        self.assertTrue(bridge.publish(100.0))

        service._enqueue_companion_dbus_publish.assert_called_once_with(100.0)

    def test_bridge_publish_queue_decision_time_and_snapshot_helpers_are_explicit(self) -> None:
        enqueue = MagicMock(return_value=True)
        direct_false = SimpleNamespace(_dbus_publish_direct_allowed=lambda: False, _enqueue_companion_dbus_publish=enqueue)
        direct_true = SimpleNamespace(_dbus_publish_direct_allowed=lambda: True, _enqueue_companion_dbus_publish=enqueue)
        missing_enqueue = SimpleNamespace(_dbus_publish_direct_allowed=lambda: False)
        missing_direct = SimpleNamespace(_enqueue_companion_dbus_publish=enqueue)

        self.assertTrue(EnergyCompanionDbusBridge._companion_publish_should_enqueue(direct_false))
        self.assertFalse(EnergyCompanionDbusBridge._companion_publish_should_enqueue(direct_true))
        self.assertTrue(EnergyCompanionDbusBridge._companion_publish_should_enqueue(missing_enqueue))
        self.assertFalse(EnergyCompanionDbusBridge._companion_publish_should_enqueue(missing_direct))
        self.assertEqual(EnergyCompanionDbusBridge._companion_publish_time(12), 12.0)
        self.assertEqual(EnergyCompanionDbusBridge._companion_publish_time(12.5), 12.5)

        with patch("venus_evcharger.companion.dbus_bridge.time.monotonic", return_value=99.25):
            self.assertEqual(EnergyCompanionDbusBridge._companion_publish_time(None), 99.25)

        mapping_snapshot = {"battery_source_count": 1}
        snapshot_service = SimpleNamespace(_get_worker_snapshot=lambda: mapping_snapshot)
        normalized = EnergyCompanionDbusBridge._companion_worker_snapshot(snapshot_service)
        self.assertEqual(normalized, mapping_snapshot)
        self.assertIsNot(normalized, mapping_snapshot)
        self.assertEqual(EnergyCompanionDbusBridge._companion_worker_snapshot(SimpleNamespace(_get_worker_snapshot=lambda: [])), {})
        self.assertEqual(EnergyCompanionDbusBridge._companion_worker_snapshot(SimpleNamespace()), {})

    def test_bridge_direct_publish_uses_dbus_thread_guard(self) -> None:
        service = SimpleNamespace(
            companion_dbus_bridge_enabled=True,
            _dbus_publish_direct_allowed=MagicMock(return_value=True),
            _assert_dbus_mainloop_thread=MagicMock(side_effect=RuntimeError("wrong thread")),
            _get_worker_snapshot=MagicMock(return_value={}),
        )
        bridge = EnergyCompanionDbusBridge(service, "/tmp/service.py")
        bridge._battery_service = _FakeVeDbusService("battery")

        with self.assertRaisesRegex(RuntimeError, "wrong thread"):
            bridge.publish(100.0)

        service._assert_dbus_mainloop_thread.assert_called_once()

    def test_bridge_starts_and_publishes_battery_and_pvinverter_services(self) -> None:
        snapshot = {
            "battery_combined_soc": 62.0,
            "battery_combined_usable_capacity_wh": 15000.0,
            "battery_combined_net_power_w": 400.0,
            "battery_combined_ac_power_w": 1800.0,
            "battery_combined_pv_input_power_w": 2600.0,
            "battery_combined_grid_interaction_w": -350.0,
            "battery_source_count": 2,
            "battery_online_source_count": 2,
        }
        service = SimpleNamespace(
            companion_dbus_bridge_enabled=True,
            companion_battery_service_enabled=True,
            companion_pvinverter_service_enabled=True,
            companion_grid_service_enabled=True,
            companion_grid_authoritative_source="",
            companion_grid_hold_seconds=5.0,
            companion_grid_smoothing_alpha=1.0,
            companion_battery_service_name="com.victronenergy.battery.external_100",
            companion_pvinverter_service_name="com.victronenergy.pvinverter.external_101",
            companion_grid_service_name="com.victronenergy.grid.external_102",
            companion_battery_deviceinstance=100,
            companion_pvinverter_deviceinstance=101,
            companion_grid_deviceinstance=102,
            connection_name="HTTP",
            custom_name="EV Charger",
            firmware_version="FW-1",
            hardware_version="HW-1",
            serial="SERIAL",
            _get_worker_snapshot=lambda: dict(snapshot),
        )

        with patch("venus_evcharger.companion.dbus_bridge.GatewayDbusServiceProxy", _FakeVeDbusService):
            bridge = EnergyCompanionDbusBridge(service, "/tmp/service.py")
            bridge.start()
            changed = bridge.publish(100.0)

        self.assertTrue(changed)
        self.assertEqual(bridge._battery_service.name, "com.victronenergy.battery.external_100")
        self.assertEqual(bridge._battery_service.paths["/Connected"], 1)
        self.assertEqual(bridge._battery_service.paths["/Soc"], 62.0)
        self.assertEqual(bridge._battery_service.paths["/Dc/0/Power"], 400.0)
        self.assertEqual(bridge._battery_service.paths["/Capacity"], 15000.0)
        self.assertEqual(bridge._pvinverter_service.paths["/Connected"], 1)
        self.assertEqual(bridge._pvinverter_service.paths["/Ac/Power"], 2600.0)
        self.assertEqual(bridge._pvinverter_service.paths["/Ac/L1/Power"], 2600.0)
        self.assertEqual(bridge._pvinverter_service.paths["/Ac/L2/Power"], 0.0)
        self.assertEqual(bridge._pvinverter_service.paths["/UpdateIndex"], 1)
        self.assertEqual(bridge._grid_service.name, "com.victronenergy.grid.external_102")
        self.assertEqual(bridge._grid_service.paths["/Connected"], 1)
        self.assertEqual(bridge._grid_service.paths["/Ac/Power"], -350.0)
        self.assertEqual(bridge._grid_service.paths["/Ac/L1/Power"], -350.0)
        self.assertEqual(bridge._grid_service.paths["/UpdateIndex"], 1)

    def test_bridge_publishes_per_source_companion_services_for_battery_hybrid_and_inverter_roles(self) -> None:
        snapshot = {
            "battery_sources": [
                {
                    "source_id": "victron-main",
                    "role": "battery",
                    "soc": 58.0,
                    "usable_capacity_wh": 10000.0,
                    "net_battery_power_w": 450.0,
                    "online": True,
                },
                {
                    "source_id": "hybrid-1",
                    "role": "hybrid-inverter",
                    "soc": 64.0,
                    "usable_capacity_wh": 8000.0,
                    "net_battery_power_w": -300.0,
                    "pv_input_power_w": 2100.0,
                    "ac_power_w": 1900.0,
                    "grid_interaction_w": -500.0,
                    "online": True,
                },
                {
                    "source_id": "roof-pv",
                    "role": "inverter",
                    "pv_input_power_w": 3200.0,
                    "grid_interaction_w": 150.0,
                    "online": False,
                },
            ],
        }
        service = SimpleNamespace(
            companion_dbus_bridge_enabled=True,
            companion_battery_service_enabled=False,
            companion_pvinverter_service_enabled=False,
            companion_source_services_enabled=True,
            companion_source_grid_services_enabled=True,
            companion_source_grid_hold_seconds=5.0,
            companion_source_grid_smoothing_alpha=1.0,
            companion_source_battery_deviceinstance_base=200,
            companion_source_pvinverter_deviceinstance_base=300,
            companion_source_grid_deviceinstance_base=400,
            companion_source_battery_service_prefix="com.victronenergy.battery.external",
            companion_source_pvinverter_service_prefix="com.victronenergy.pvinverter.external",
            companion_source_grid_service_prefix="com.victronenergy.grid.external",
            connection_name="HTTP",
            custom_name="EV Charger",
            firmware_version="FW-1",
            hardware_version="HW-1",
            serial="SERIAL",
            _get_worker_snapshot=lambda: dict(snapshot),
        )

        with patch("venus_evcharger.companion.dbus_bridge.GatewayDbusServiceProxy", _FakeVeDbusService):
            bridge = EnergyCompanionDbusBridge(service, "/tmp/service.py")
            bridge.start()
            changed = bridge.publish(100.0)

        self.assertTrue(changed)
        self.assertEqual(set(bridge._source_battery_services), {"victron-main", "hybrid-1"})
        self.assertEqual(set(bridge._source_pvinverter_services), {"hybrid-1", "roof-pv"})
        self.assertEqual(set(bridge._source_grid_services), {"hybrid-1", "roof-pv"})
        self.assertEqual(
            bridge._source_battery_services["victron-main"].name,
            "com.victronenergy.battery.external.victron_main",
        )
        self.assertEqual(
            bridge._source_pvinverter_services["hybrid-1"].name,
            "com.victronenergy.pvinverter.external.hybrid_1",
        )
        self.assertEqual(
            bridge._source_grid_services["hybrid-1"].name,
            "com.victronenergy.grid.external.hybrid_1",
        )
        self.assertEqual(bridge._source_battery_services["victron-main"].paths["/Soc"], 58.0)
        self.assertEqual(bridge._source_battery_services["hybrid-1"].paths["/Dc/0/Power"], -300.0)
        self.assertEqual(bridge._source_pvinverter_services["hybrid-1"].paths["/Ac/Power"], 2100.0)
        self.assertEqual(bridge._source_pvinverter_services["roof-pv"].paths["/Connected"], 0)
        self.assertEqual(bridge._source_pvinverter_services["roof-pv"].paths["/Ac/L1/Power"], 3200.0)
        self.assertEqual(bridge._source_pvinverter_services["roof-pv"].paths["/UpdateIndex"], 1)
        self.assertEqual(bridge._source_grid_services["hybrid-1"].paths["/Connected"], 1)
        self.assertEqual(bridge._source_grid_services["hybrid-1"].paths["/Ac/Power"], -500.0)
        self.assertEqual(bridge._source_grid_services["roof-pv"].paths["/Connected"], 0)
        self.assertEqual(bridge._source_grid_services["roof-pv"].paths["/Ac/L1/Power"], 150.0)

    def test_bridge_is_noop_when_disabled(self) -> None:
        service = SimpleNamespace(companion_dbus_bridge_enabled=False)
        bridge = EnergyCompanionDbusBridge(service, "/tmp/service.py")

        bridge.start()

        self.assertFalse(bridge.publish(100.0))

    def test_bridge_stop_clears_registered_services(self) -> None:
        service = SimpleNamespace(
            companion_dbus_bridge_enabled=True,
            companion_battery_service_enabled=True,
            companion_pvinverter_service_enabled=True,
            companion_grid_service_enabled=True,
            companion_grid_hold_seconds=5.0,
            companion_grid_smoothing_alpha=1.0,
            _get_worker_snapshot=lambda: {},
        )

        with patch("venus_evcharger.companion.dbus_bridge.GatewayDbusServiceProxy", _FakeVeDbusService):
            bridge = EnergyCompanionDbusBridge(service, "/tmp/service.py")
            bridge.start()
            bridge.stop()

        self.assertIsNone(bridge._battery_service)
        self.assertIsNone(bridge._pvinverter_service)
        self.assertIsNone(bridge._grid_service)
        self.assertEqual(bridge._source_battery_services, {})
        self.assertEqual(bridge._source_pvinverter_services, {})
        self.assertEqual(bridge._source_grid_services, {})
        self.assertEqual(bridge._published_values, {})
        self.assertEqual(bridge._grid_hold_state, {})

    def test_bridge_publish_service_values_checks_thread_and_updates_only_on_change(self) -> None:
        thread_guard = MagicMock()
        service = SimpleNamespace(_assert_dbus_mainloop_thread=thread_guard)
        bridge = EnergyCompanionDbusBridge(service, "/tmp/service.py")
        dbus_service = _FakeVeDbusService("example")
        dbus_service.add_path("/UpdateIndex", 0)
        dbus_service.add_path("/Value", 0)

        self.assertTrue(bridge._publish_service_values("example", dbus_service, {"/Value": 1}))
        self.assertIs(bridge._publish_service_values("example", dbus_service, {"/Value": 1}), False)
        self.assertEqual(dbus_service.paths["/UpdateIndex"], 1)
        self.assertEqual(dbus_service.paths["/Value"], 1)
        self.assertEqual(
            [call.args[0] for call in thread_guard.call_args_list],
            ["companion DBus publish example", "companion DBus publish example"],
        )

    def test_bridge_can_publish_only_pvinverter_and_reuse_existing_values(self) -> None:
        service = SimpleNamespace(
            companion_dbus_bridge_enabled=True,
            companion_battery_service_enabled=False,
            companion_pvinverter_service_enabled=True,
            connection_name="HTTP",
            custom_name="EV Charger",
            firmware_version="FW-1",
            hardware_version="HW-1",
            serial="SERIAL",
            _get_worker_snapshot=lambda: {
                "battery_combined_ac_power_w": 900.0,
                "battery_source_count": 0,
                "battery_online_source_count": 0,
            },
        )

        with patch("venus_evcharger.companion.dbus_bridge.GatewayDbusServiceProxy", _FakeVeDbusService):
            bridge = EnergyCompanionDbusBridge(service, "/tmp/service.py")
            bridge.start()
            first_changed = bridge.publish(100.0)
            second_changed = bridge.publish(101.0)

        self.assertTrue(first_changed)
        self.assertFalse(second_changed)
        self.assertIsNone(bridge._battery_service)
        self.assertEqual(bridge._pvinverter_service.paths["/Connected"], 1)
        self.assertEqual(bridge._pvinverter_service.paths["/Ac/Power"], 900.0)
        self.assertEqual(bridge._pvinverter_service.paths["/UpdateIndex"], 1)

    def test_bridge_publish_returns_false_without_active_services_or_mapping_snapshot(self) -> None:
        service = SimpleNamespace(
            companion_dbus_bridge_enabled=True,
            companion_battery_service_enabled=False,
            companion_pvinverter_service_enabled=False,
            _get_worker_snapshot=lambda: ["not-a-mapping"],
        )

        with patch("venus_evcharger.companion.dbus_bridge.GatewayDbusServiceProxy", _FakeVeDbusService):
            bridge = EnergyCompanionDbusBridge(service, "/tmp/service.py")
            bridge.start()
            changed = bridge.publish(100.0)

        self.assertFalse(changed)

    def test_bridge_pvinverter_helpers_fall_back_to_battery_state_and_zero_power(self) -> None:
        snapshot = {
            "battery_source_count": 1,
            "battery_online_source_count": 1,
        }

        self.assertEqual(EnergyCompanionDbusBridge._pvinverter_connected(snapshot), 1)
        self.assertEqual(EnergyCompanionDbusBridge._pvinverter_power_w(snapshot), 0.0)

    def test_bridge_snapshot_publishers_write_exact_default_payloads(self) -> None:
        bridge = EnergyCompanionDbusBridge(SimpleNamespace(), "/tmp/service.py")
        bridge._battery_service = _FakeVeDbusService("battery")
        bridge._battery_service.add_path("/UpdateIndex", 0)
        bridge._pvinverter_service = _FakeVeDbusService("pvinverter")
        bridge._pvinverter_service.add_path("/UpdateIndex", 0)
        bridge._grid_service = _FakeVeDbusService("grid")
        bridge._grid_service.add_path("/UpdateIndex", 0)

        self.assertTrue(bridge._publish_battery_snapshot({}))
        self.assertTrue(bridge._publish_pvinverter_snapshot({}))
        self.assertTrue(bridge._publish_grid_snapshot({}, 100.0))
        self.assertEqual(
            bridge._battery_service.paths,
            {
                "/UpdateIndex": 1,
                "/Connected": 0,
                "/Soc": None,
                "/Dc/0/Power": 0.0,
                "/Capacity": None,
            },
        )
        self.assertEqual(
            bridge._pvinverter_service.paths,
            {
                "/UpdateIndex": 1,
                "/Connected": 0,
                "/Ac/Power": 0.0,
                "/Ac/L1/Power": 0.0,
                "/Ac/L2/Power": 0.0,
                "/Ac/L3/Power": 0.0,
            },
        )
        self.assertEqual(
            bridge._grid_service.paths,
            {
                "/UpdateIndex": 1,
                "/Connected": 0,
                "/Ac/Power": 0.0,
                "/Ac/L1/Power": 0.0,
                "/Ac/L2/Power": 0.0,
                "/Ac/L3/Power": 0.0,
            },
        )
        self.assertEqual(set(bridge._published_values), {"battery", "pvinverter", "grid"})

    def test_bridge_source_publishers_use_stable_service_keys_and_skip_unsupported_roles(self) -> None:
        bridge = EnergyCompanionDbusBridge(SimpleNamespace(companion_source_grid_services_enabled=True), "/tmp/service.py")
        battery_service = _FakeVeDbusService("battery")
        pvinverter_service = _FakeVeDbusService("pvinverter")
        grid_service = _FakeVeDbusService("grid")
        source = {"source_id": "hybrid-a", "role": "hybrid-inverter", "online": True, "grid_interaction_w": 12.0}

        with patch.object(bridge, "_ensure_source_battery_service", return_value=battery_service), patch.object(
            bridge, "_ensure_source_pvinverter_service", return_value=pvinverter_service
        ), patch.object(bridge, "_ensure_source_grid_service", return_value=grid_service), patch.object(
            bridge, "_publish_service_values", return_value=True
        ) as publish_values:
            self.assertTrue(bridge._publish_battery_source_service(source, 0))
            self.assertTrue(bridge._publish_pvinverter_source_service(source, 1))
            self.assertTrue(bridge._publish_grid_source_service(source, 2, 100.0))

        self.assertEqual(
            [call.args[0] for call in publish_values.call_args_list],
            ["source-battery:hybrid-a", "source-pvinverter:hybrid-a", "source-grid:hybrid-a"],
        )
        self.assertIs(bridge._publish_battery_source_service({"source_id": "pv", "role": "inverter"}, 0), False)
        self.assertIs(bridge._publish_pvinverter_source_service({"source_id": "battery", "role": "battery"}, 0), False)
        self.assertIs(bridge._publish_grid_source_service({"source_id": "gridless", "role": "battery"}, 0, 100.0), False)
        disabled_grid_bridge = EnergyCompanionDbusBridge(SimpleNamespace(companion_source_grid_services_enabled=False), "/tmp/service.py")
        self.assertIs(
            disabled_grid_bridge._publish_grid_source_service({"source_id": "grid", "grid_interaction_w": 10.0}, 0, 100.0),
            False,
        )

        bridge._source_grid_services[""] = grid_service
        with patch.object(bridge, "_publish_service_values", return_value=True) as existing_grid_publish:
            self.assertTrue(bridge._publish_grid_source_service({"grid_interaction_w": 5.0}, 0, 100.0))
        self.assertEqual(existing_grid_publish.call_args.args[0], "source-grid:")

    def test_bridge_connection_and_power_helpers_cover_edge_cases(self) -> None:
        self.assertEqual(EnergyCompanionDbusBridge._battery_connected({"battery_source_count": 1, "battery_online_source_count": 1}), 1)
        self.assertEqual(EnergyCompanionDbusBridge._battery_connected({"battery_source_count": 0, "battery_online_source_count": 1}), 0)
        self.assertEqual(EnergyCompanionDbusBridge._battery_connected({"battery_source_count": 1, "battery_online_source_count": 0}), 0)
        self.assertEqual(EnergyCompanionDbusBridge._battery_connected({"battery_source_count": "2", "battery_online_source_count": "1"}), 1)

        self.assertEqual(EnergyCompanionDbusBridge._pvinverter_connected({"battery_combined_pv_input_power_w": 1.0}), 1)
        self.assertEqual(EnergyCompanionDbusBridge._pvinverter_connected({"battery_combined_pv_input_power_w": 0.0}), 0)
        self.assertEqual(EnergyCompanionDbusBridge._pvinverter_connected({"battery_combined_pv_input_power_w": 0.0, "battery_combined_ac_power_w": 1.0}), 1)
        self.assertEqual(EnergyCompanionDbusBridge._pvinverter_connected({"battery_combined_ac_power_w": 0.0}), 0)
        self.assertEqual(
            EnergyCompanionDbusBridge._pvinverter_connected(
                {"battery_combined_pv_input_power_w": -1.0, "battery_source_count": 1, "battery_online_source_count": 1}
            ),
            1,
        )
        self.assertEqual(EnergyCompanionDbusBridge._pvinverter_connected({"battery_combined_ac_power_w": -1.0}), 0)
        self.assertEqual(EnergyCompanionDbusBridge._pvinverter_power_w({"battery_combined_pv_input_power_w": 12.5}), 12.5)
        self.assertEqual(EnergyCompanionDbusBridge._pvinverter_power_w({"battery_combined_pv_input_power_w": -12.5}), 0.0)
        self.assertEqual(EnergyCompanionDbusBridge._pvinverter_power_w({"battery_combined_pv_input_power_w": "bad", "battery_combined_ac_power_w": 8.0}), 8.0)
        self.assertEqual(EnergyCompanionDbusBridge._pvinverter_power_w({"battery_combined_ac_power_w": -8.0}), 0.0)

    def test_bridge_skips_per_source_publication_when_feature_is_disabled(self) -> None:
        service = SimpleNamespace(
            companion_dbus_bridge_enabled=True,
            companion_battery_service_enabled=False,
            companion_pvinverter_service_enabled=False,
            companion_source_services_enabled=False,
            companion_source_grid_services_enabled=True,
            _get_worker_snapshot=lambda: {
                "battery_sources": [
                    {"source_id": "battery-a", "role": "battery", "online": True, "grid_interaction_w": 100.0}
                ]
            },
        )

        with patch("venus_evcharger.companion.dbus_bridge.GatewayDbusServiceProxy", _FakeVeDbusService):
            bridge = EnergyCompanionDbusBridge(service, "/tmp/service.py")
            bridge.start()
            changed = bridge.publish(100.0)

        self.assertFalse(changed)
        self.assertEqual(bridge._source_battery_services, {})
        self.assertEqual(bridge._source_pvinverter_services, {})
        self.assertEqual(bridge._source_grid_services, {})

    def test_bridge_reuses_existing_source_services_and_skips_invalid_source_entries(self) -> None:
        snapshot = {
            "battery_sources": [
                "bad-source",
                {"source_id": "", "role": "battery", "online": True},
                {"source_id": "hybrid-a", "role": "hybrid-inverter", "ac_power_w": 1500.0, "online": True},
            ],
        }
        service = SimpleNamespace(
            companion_dbus_bridge_enabled=True,
            companion_battery_service_enabled=False,
            companion_pvinverter_service_enabled=False,
            companion_source_services_enabled=True,
            companion_source_grid_services_enabled=True,
            companion_source_battery_deviceinstance_base=200,
            companion_source_pvinverter_deviceinstance_base=300,
            companion_source_grid_deviceinstance_base=400,
            companion_source_battery_service_prefix="",
            companion_source_pvinverter_service_prefix="",
            companion_source_grid_service_prefix="",
            connection_name="HTTP",
            custom_name="EV Charger",
            firmware_version="FW-1",
            hardware_version="HW-1",
            serial="SERIAL",
            _get_worker_snapshot=lambda: dict(snapshot),
        )

        with patch("venus_evcharger.companion.dbus_bridge.GatewayDbusServiceProxy", _FakeVeDbusService):
            bridge = EnergyCompanionDbusBridge(service, "/tmp/service.py")
            bridge.start()
            first_changed = bridge.publish(100.0)
            second_changed = bridge.publish(101.0)

        self.assertTrue(first_changed)
        self.assertFalse(second_changed)
        self.assertEqual(set(bridge._source_battery_services), {"hybrid-a"})
        self.assertEqual(set(bridge._source_pvinverter_services), {"hybrid-a"})
        self.assertEqual(bridge._source_grid_services, {})

    def test_bridge_source_pvinverter_helper_falls_back_to_ac_power_and_zero(self) -> None:
        self.assertEqual(
            EnergyCompanionDbusBridge._battery_source_values(
                {
                    "online": True,
                    "soc": 51.0,
                    "net_battery_power_w": -220.0,
                    "usable_capacity_wh": 9000.0,
                }
            ),
            {
                "/Connected": 1,
                "/Soc": 51.0,
                "/Dc/0/Power": -220.0,
                "/Capacity": 9000.0,
            },
        )
        self.assertEqual(
            EnergyCompanionDbusBridge._battery_source_values({}),
            {
                "/Connected": 0,
                "/Soc": None,
                "/Dc/0/Power": 0.0,
                "/Capacity": None,
            },
        )
        self.assertEqual(
            EnergyCompanionDbusBridge._pvinverter_source_values(
                {"online": True, "pv_input_power_w": 1200.0, "ac_power_w": 900.0}
            ),
            {
                "/Connected": 1,
                "/Ac/Power": 1200.0,
                "/Ac/L1/Power": 1200.0,
                "/Ac/L2/Power": 0.0,
                "/Ac/L3/Power": 0.0,
            },
        )
        self.assertEqual(
            EnergyCompanionDbusBridge._pvinverter_source_values({"online": False, "pv_input_power_w": -12.0}),
            {
                "/Connected": 0,
                "/Ac/Power": 0.0,
                "/Ac/L1/Power": 0.0,
                "/Ac/L2/Power": 0.0,
                "/Ac/L3/Power": 0.0,
            },
        )
        self.assertEqual(
            EnergyCompanionDbusBridge._source_pvinverter_power_w({"ac_power_w": 1200.0}),
            1200.0,
        )
        self.assertEqual(EnergyCompanionDbusBridge._source_pvinverter_power_w({"pv_input_power_w": 1300.0}), 1300.0)
        self.assertEqual(EnergyCompanionDbusBridge._source_pvinverter_power_w({"pv_input_power_w": -1300.0}), 0.0)
        self.assertEqual(EnergyCompanionDbusBridge._source_pvinverter_power_w({"ac_power_w": -1200.0}), 0.0)
        self.assertEqual(EnergyCompanionDbusBridge._source_pvinverter_power_w({}), 0.0)

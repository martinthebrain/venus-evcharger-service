# SPDX-License-Identifier: GPL-3.0-or-later
from tests.companion_dbus_bridge_cases_common import *


class _CompanionDbusBridgeCoreCases:
    def test_bridge_publish_queues_when_called_outside_mainloop_thread(self) -> None:
        service = SimpleNamespace(
            companion_dbus_bridge_enabled=True,
            _dbus_publish_direct_allowed=MagicMock(return_value=False),
            _enqueue_companion_dbus_publish=MagicMock(return_value=True),
        )
        bridge = EnergyCompanionDbusBridge(service, "/tmp/service.py")

        self.assertTrue(bridge.publish(100.0))

        service._enqueue_companion_dbus_publish.assert_called_once_with(100.0)

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

        with patch("venus_evcharger.companion.dbus_bridge.VeDbusService", _FakeVeDbusService):
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

        with patch("venus_evcharger.companion.dbus_bridge.VeDbusService", _FakeVeDbusService):
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

        with patch("venus_evcharger.companion.dbus_bridge.VeDbusService", _FakeVeDbusService):
            bridge = EnergyCompanionDbusBridge(service, "/tmp/service.py")
            bridge.start()
            bridge.stop()

        self.assertIsNone(bridge._battery_service)
        self.assertIsNone(bridge._pvinverter_service)
        self.assertIsNone(bridge._grid_service)
        self.assertEqual(bridge._published_values, {})

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

        with patch("venus_evcharger.companion.dbus_bridge.VeDbusService", _FakeVeDbusService):
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

        with patch("venus_evcharger.companion.dbus_bridge.VeDbusService", _FakeVeDbusService):
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

        with patch("venus_evcharger.companion.dbus_bridge.VeDbusService", _FakeVeDbusService):
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

        with patch("venus_evcharger.companion.dbus_bridge.VeDbusService", _FakeVeDbusService):
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
            EnergyCompanionDbusBridge._source_pvinverter_power_w({"ac_power_w": 1200.0}),
            1200.0,
        )
        self.assertEqual(EnergyCompanionDbusBridge._source_pvinverter_power_w({}), 0.0)

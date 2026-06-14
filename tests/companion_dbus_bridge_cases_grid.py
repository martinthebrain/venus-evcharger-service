# SPDX-License-Identifier: GPL-3.0-or-later
from tests.companion_dbus_bridge_cases_common import *


class _CompanionDbusBridgeGridCases:
    def test_bridge_can_publish_only_grid_services_when_enabled(self) -> None:
        service = SimpleNamespace(
            companion_dbus_bridge_enabled=True,
            companion_battery_service_enabled=False,
            companion_pvinverter_service_enabled=False,
            companion_grid_service_enabled=True,
            companion_grid_authoritative_source="",
            companion_grid_hold_seconds=5.0,
            companion_grid_smoothing_alpha=1.0,
            connection_name="HTTP",
            custom_name="EV Charger",
            firmware_version="FW-1",
            hardware_version="HW-1",
            serial="SERIAL",
            _get_worker_snapshot=lambda: {
                "battery_combined_grid_interaction_w": 250.0,
                "battery_online_source_count": 1,
            },
        )

        with patch("venus_evcharger.companion.dbus_bridge.GatewayDbusServiceProxy", _FakeVeDbusService):
            bridge = EnergyCompanionDbusBridge(service, "/tmp/service.py")
            bridge.start()
            changed = bridge.publish(100.0)

        self.assertTrue(changed)
        self.assertIsNone(bridge._battery_service)
        self.assertIsNone(bridge._pvinverter_service)
        self.assertEqual(bridge._grid_service.paths["/Connected"], 1)
        self.assertEqual(bridge._grid_service.paths["/Ac/Power"], 250.0)

    def test_grid_service_holds_last_good_value_during_short_outage(self) -> None:
        snapshots = iter(
            (
                {
                    "battery_combined_grid_interaction_w": 250.0,
                    "battery_online_source_count": 1,
                },
                {
                    "battery_combined_grid_interaction_w": None,
                    "battery_online_source_count": 0,
                },
                {
                    "battery_combined_grid_interaction_w": None,
                    "battery_online_source_count": 0,
                },
            )
        )
        service = SimpleNamespace(
            companion_dbus_bridge_enabled=True,
            companion_battery_service_enabled=False,
            companion_pvinverter_service_enabled=False,
            companion_grid_service_enabled=True,
            companion_grid_authoritative_source="",
            companion_grid_hold_seconds=5.0,
            companion_grid_smoothing_alpha=1.0,
            connection_name="HTTP",
            custom_name="EV Charger",
            firmware_version="FW-1",
            hardware_version="HW-1",
            serial="SERIAL",
            _get_worker_snapshot=lambda: dict(next(snapshots)),
        )

        with patch("venus_evcharger.companion.dbus_bridge.GatewayDbusServiceProxy", _FakeVeDbusService):
            bridge = EnergyCompanionDbusBridge(service, "/tmp/service.py")
            bridge.start()
            first_changed = bridge.publish(100.0)
            second_changed = bridge.publish(103.0)
            third_changed = bridge.publish(107.0)

        self.assertTrue(first_changed)
        self.assertFalse(second_changed)
        self.assertTrue(third_changed)
        self.assertEqual(bridge._grid_service.paths["/Ac/Power"], 0.0)
        self.assertEqual(bridge._grid_service.paths["/Connected"], 0)
        self.assertEqual(bridge._grid_service.paths["/UpdateIndex"], 2)

    def test_grid_service_applies_optional_smoothing(self) -> None:
        snapshots = iter(
            (
                {
                    "battery_combined_grid_interaction_w": 100.0,
                    "battery_online_source_count": 1,
                },
                {
                    "battery_combined_grid_interaction_w": 300.0,
                    "battery_online_source_count": 1,
                },
            )
        )
        service = SimpleNamespace(
            companion_dbus_bridge_enabled=True,
            companion_battery_service_enabled=False,
            companion_pvinverter_service_enabled=False,
            companion_grid_service_enabled=True,
            companion_grid_authoritative_source="",
            companion_grid_hold_seconds=5.0,
            companion_grid_smoothing_alpha=0.5,
            connection_name="HTTP",
            custom_name="EV Charger",
            firmware_version="FW-1",
            hardware_version="HW-1",
            serial="SERIAL",
            _get_worker_snapshot=lambda: dict(next(snapshots)),
        )

        with patch("venus_evcharger.companion.dbus_bridge.GatewayDbusServiceProxy", _FakeVeDbusService):
            bridge = EnergyCompanionDbusBridge(service, "/tmp/service.py")
            bridge.start()
            bridge.publish(100.0)
            bridge.publish(101.0)

        self.assertEqual(bridge._grid_service.paths["/Ac/Power"], 200.0)

    def test_grid_service_skips_smoothing_for_large_jump_when_threshold_is_set(self) -> None:
        snapshots = iter(
            (
                {
                    "battery_combined_grid_interaction_w": 100.0,
                    "battery_online_source_count": 1,
                },
                {
                    "battery_combined_grid_interaction_w": 300.0,
                    "battery_online_source_count": 1,
                },
            )
        )
        service = SimpleNamespace(
            companion_dbus_bridge_enabled=True,
            companion_battery_service_enabled=False,
            companion_pvinverter_service_enabled=False,
            companion_grid_service_enabled=True,
            companion_grid_authoritative_source="",
            companion_grid_hold_seconds=5.0,
            companion_grid_smoothing_alpha=0.5,
            companion_grid_smoothing_max_jump_watts=50.0,
            connection_name="HTTP",
            custom_name="EV Charger",
            firmware_version="FW-1",
            hardware_version="HW-1",
            serial="SERIAL",
            _get_worker_snapshot=lambda: dict(next(snapshots)),
        )

        with patch("venus_evcharger.companion.dbus_bridge.GatewayDbusServiceProxy", _FakeVeDbusService):
            bridge = EnergyCompanionDbusBridge(service, "/tmp/service.py")
            bridge.start()
            bridge.publish(100.0)
            bridge.publish(101.0)

        self.assertEqual(bridge._grid_service.paths["/Ac/Power"], 300.0)

    def test_grid_service_still_smooths_small_jump_when_threshold_is_set(self) -> None:
        snapshots = iter(
            (
                {
                    "battery_combined_grid_interaction_w": 100.0,
                    "battery_online_source_count": 1,
                },
                {
                    "battery_combined_grid_interaction_w": 130.0,
                    "battery_online_source_count": 1,
                },
            )
        )
        service = SimpleNamespace(
            companion_dbus_bridge_enabled=True,
            companion_battery_service_enabled=False,
            companion_pvinverter_service_enabled=False,
            companion_grid_service_enabled=True,
            companion_grid_authoritative_source="",
            companion_grid_hold_seconds=5.0,
            companion_grid_smoothing_alpha=0.5,
            companion_grid_smoothing_max_jump_watts=50.0,
            connection_name="HTTP",
            custom_name="EV Charger",
            firmware_version="FW-1",
            hardware_version="HW-1",
            serial="SERIAL",
            _get_worker_snapshot=lambda: dict(next(snapshots)),
        )

        with patch("venus_evcharger.companion.dbus_bridge.GatewayDbusServiceProxy", _FakeVeDbusService):
            bridge = EnergyCompanionDbusBridge(service, "/tmp/service.py")
            bridge.start()
            bridge.publish(100.0)
            bridge.publish(101.0)

        self.assertEqual(bridge._grid_service.paths["/Ac/Power"], 115.0)

    def test_grid_service_can_pin_authoritative_source(self) -> None:
        snapshot = {
            "battery_combined_grid_interaction_w": 999.0,
            "battery_online_source_count": 2,
            "battery_sources": [
                {
                    "source_id": "huawei",
                    "role": "hybrid-inverter",
                    "grid_interaction_w": -420.0,
                    "online": True,
                },
                {
                    "source_id": "opendtu",
                    "role": "inverter",
                    "grid_interaction_w": 150.0,
                    "online": True,
                },
            ],
        }
        service = SimpleNamespace(
            companion_dbus_bridge_enabled=True,
            companion_battery_service_enabled=False,
            companion_pvinverter_service_enabled=False,
            companion_grid_service_enabled=True,
            companion_grid_authoritative_source="huawei",
            companion_source_services_enabled=False,
            companion_grid_hold_seconds=5.0,
            companion_grid_smoothing_alpha=1.0,
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
        self.assertEqual(bridge._grid_service.paths["/Connected"], 1)
        self.assertEqual(bridge._grid_service.paths["/Ac/Power"], -420.0)

    def test_grid_service_authoritative_source_does_not_fallback_to_combined_grid(self) -> None:
        snapshots = iter(
            (
                {
                    "battery_combined_grid_interaction_w": 999.0,
                    "battery_online_source_count": 2,
                    "battery_sources": [
                        {
                            "source_id": "huawei",
                            "role": "hybrid-inverter",
                            "grid_interaction_w": -420.0,
                            "online": True,
                        }
                    ],
                },
                {
                    "battery_combined_grid_interaction_w": 888.0,
                    "battery_online_source_count": 1,
                    "battery_sources": [
                        {
                            "source_id": "huawei",
                            "role": "hybrid-inverter",
                            "online": False,
                        }
                    ],
                },
                {
                    "battery_combined_grid_interaction_w": 777.0,
                    "battery_online_source_count": 1,
                    "battery_sources": [
                        {
                            "source_id": "huawei",
                            "role": "hybrid-inverter",
                            "online": False,
                        }
                    ],
                },
            )
        )
        service = SimpleNamespace(
            companion_dbus_bridge_enabled=True,
            companion_battery_service_enabled=False,
            companion_pvinverter_service_enabled=False,
            companion_grid_service_enabled=True,
            companion_grid_authoritative_source="huawei",
            companion_source_services_enabled=False,
            companion_grid_hold_seconds=5.0,
            companion_grid_smoothing_alpha=1.0,
            connection_name="HTTP",
            custom_name="EV Charger",
            firmware_version="FW-1",
            hardware_version="HW-1",
            serial="SERIAL",
            _get_worker_snapshot=lambda: dict(next(snapshots)),
        )

        with patch("venus_evcharger.companion.dbus_bridge.GatewayDbusServiceProxy", _FakeVeDbusService):
            bridge = EnergyCompanionDbusBridge(service, "/tmp/service.py")
            bridge.start()
            first_changed = bridge.publish(100.0)
            second_changed = bridge.publish(103.0)
            third_changed = bridge.publish(107.0)

        self.assertTrue(first_changed)
        self.assertFalse(second_changed)
        self.assertTrue(third_changed)
        self.assertEqual(bridge._grid_service.paths["/Ac/Power"], 0.0)
        self.assertEqual(bridge._grid_service.paths["/Connected"], 0)

    def test_source_grid_service_holds_last_good_value_during_short_outage(self) -> None:
        snapshots = iter(
            (
                {
                    "battery_sources": [
                        {
                            "source_id": "huawei-grid",
                            "role": "hybrid-inverter",
                            "grid_interaction_w": -420.0,
                            "online": True,
                        }
                    ]
                },
                {
                    "battery_sources": [
                        {
                            "source_id": "huawei-grid",
                            "role": "hybrid-inverter",
                            "online": False,
                        }
                    ]
                },
                {
                    "battery_sources": [
                        {
                            "source_id": "huawei-grid",
                            "role": "hybrid-inverter",
                            "online": False,
                        }
                    ]
                },
            )
        )
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
            _get_worker_snapshot=lambda: dict(next(snapshots)),
        )

        with patch("venus_evcharger.companion.dbus_bridge.GatewayDbusServiceProxy", _FakeVeDbusService):
            bridge = EnergyCompanionDbusBridge(service, "/tmp/service.py")
            bridge.start()
            first_changed = bridge.publish(100.0)
            second_changed = bridge.publish(103.0)
            second_update_index = bridge._source_grid_services["huawei-grid"].paths["/UpdateIndex"]
            third_changed = bridge.publish(107.0)

        self.assertTrue(first_changed)
        self.assertTrue(second_changed)
        self.assertEqual(second_update_index, 1)
        self.assertTrue(third_changed)
        self.assertEqual(bridge._source_grid_services["huawei-grid"].paths["/Ac/Power"], 0.0)
        self.assertEqual(bridge._source_grid_services["huawei-grid"].paths["/Connected"], 0)
        self.assertEqual(bridge._source_grid_services["huawei-grid"].paths["/UpdateIndex"], 2)

    def test_source_grid_service_skips_smoothing_for_large_jump_when_threshold_is_set(self) -> None:
        snapshots = iter(
            (
                {
                    "battery_sources": [
                        {
                            "source_id": "meter-a",
                            "role": "hybrid-inverter",
                            "grid_interaction_w": 100.0,
                            "online": True,
                        }
                    ]
                },
                {
                    "battery_sources": [
                        {
                            "source_id": "meter-a",
                            "role": "hybrid-inverter",
                            "grid_interaction_w": 260.0,
                            "online": True,
                        }
                    ]
                },
            )
        )
        service = SimpleNamespace(
            companion_dbus_bridge_enabled=True,
            companion_battery_service_enabled=False,
            companion_pvinverter_service_enabled=False,
            companion_source_services_enabled=True,
            companion_source_grid_services_enabled=True,
            companion_source_grid_hold_seconds=5.0,
            companion_source_grid_smoothing_alpha=0.5,
            companion_source_grid_smoothing_max_jump_watts=50.0,
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
            _get_worker_snapshot=lambda: dict(next(snapshots)),
        )

        with patch("venus_evcharger.companion.dbus_bridge.GatewayDbusServiceProxy", _FakeVeDbusService):
            bridge = EnergyCompanionDbusBridge(service, "/tmp/service.py")
            bridge.start()
            bridge.publish(100.0)
            bridge.publish(101.0)

        self.assertEqual(bridge._source_grid_services["meter-a"].paths["/Ac/Power"], 260.0)

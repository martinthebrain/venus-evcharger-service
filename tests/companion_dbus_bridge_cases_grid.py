# SPDX-License-Identifier: GPL-3.0-or-later
from typing import cast

from venus_evcharger.companion import dbus_bridge_grid as bridge_grid_mod

from tests.companion_dbus_bridge_cases_common import *


class _CompanionDbusBridgeGridCases:
    def test_grid_service_prefers_canonical_fusion_over_legacy_authoritative_source(self) -> None:
        service = SimpleNamespace(companion_grid_authoritative_source="huawei")
        bridge = EnergyCompanionDbusBridge(service, "/tmp/service.py")
        snapshot = {
            "grid_fusion_enabled": True,
            "grid_power": -125.0,
            "battery_combined_grid_interaction_w": 999.0,
            "battery_sources": [
                {"source_id": "huawei", "grid_interaction_w": -900.0, "online": True},
            ],
        }

        self.assertEqual(bridge._aggregate_grid_input(snapshot), (-125.0, True))
        snapshot["grid_power"] = None
        self.assertEqual(bridge._aggregate_grid_input(snapshot), (None, False))

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

    def test_grid_direct_helpers_cover_authoritative_and_aggregate_inputs(self) -> None:
        service = SimpleNamespace(
            companion_grid_authoritative_source="missing",
            companion_grid_hold_seconds=5.0,
            companion_grid_smoothing_alpha=1.0,
            companion_grid_smoothing_max_jump_watts=0.0,
        )
        bridge = EnergyCompanionDbusBridge(service, "/tmp/service.py")
        missing_source_snapshot = {
            "battery_combined_grid_interaction_w": 500.0,
            "battery_online_source_count": 1,
            "battery_sources": [{"source_id": "other", "grid_interaction_w": 123.0, "online": True}],
        }

        self.assertEqual(bridge._grid_connected(missing_source_snapshot, 100.0), 0)
        self.assertEqual(bridge._grid_power_w(missing_source_snapshot, 100.0), 0.0)
        self.assertIsNone(bridge._find_source_snapshot(missing_source_snapshot, "missing"))
        self.assertEqual(bridge._aggregate_grid_input(missing_source_snapshot), (None, False))

        service.companion_grid_authoritative_source = ""
        aggregate_snapshot = {
            "battery_combined_grid_interaction_w": 120.0,
            "battery_online_source_count": 1,
        }
        self.assertEqual(bridge._grid_connected(aggregate_snapshot, 101.0), 1)
        self.assertEqual(bridge._grid_power_w(aggregate_snapshot, 101.0), 120.0)

    def test_grid_aggregate_defaults_to_combined_value_when_authoritative_source_is_absent(self) -> None:
        bridge = EnergyCompanionDbusBridge(SimpleNamespace(), "/tmp/service.py")

        self.assertEqual(
            bridge._aggregate_grid_input(
                {
                    "battery_combined_grid_interaction_w": 321.0,
                    "battery_online_source_count": 1,
                    "battery_sources": [{"source_id": "None", "grid_interaction_w": -99.0, "online": True}],
                }
            ),
            (321.0, True),
        )
        self.assertEqual(bridge._grid_snapshot_values({"battery_combined_grid_interaction_w": 321.0}, 3.0)["value"], 321.0)
        self.assertIn("aggregate-grid", bridge._grid_hold_state)
        self.assertNotIn(None, bridge._grid_hold_state)

    def test_grid_aggregate_default_hold_is_disabled_and_zero_jump_threshold_disables_limit(self) -> None:
        service = SimpleNamespace(
            companion_grid_authoritative_source="",
            companion_grid_smoothing_alpha=0.5,
            companion_grid_smoothing_max_jump_watts=0.0,
        )
        bridge = EnergyCompanionDbusBridge(service, "/tmp/service.py")

        self.assertEqual(
            bridge._grid_snapshot_values({"battery_combined_grid_interaction_w": 100.0}, 10.0),
            {"connected": False, "value": 100.0},
        )
        self.assertEqual(
            bridge._grid_snapshot_values({"battery_combined_grid_interaction_w": 300.0}, 11.0),
            {"connected": False, "value": 200.0},
        )
        self.assertEqual(
            bridge._grid_snapshot_values({"battery_combined_grid_interaction_w": None}, 11.5),
            {"connected": False, "value": 0.0},
        )

    def test_grid_authoritative_source_missing_online_defaults_to_offline(self) -> None:
        service = SimpleNamespace(
            companion_grid_authoritative_source="meter-a",
            companion_grid_hold_seconds=0.0,
            companion_grid_smoothing_alpha=1.0,
            companion_grid_smoothing_max_jump_watts=0.0,
        )
        bridge = EnergyCompanionDbusBridge(service, "/tmp/service.py")
        snapshot = {"battery_sources": [{"source_id": "meter-a", "grid_interaction_w": 17.0}]}

        self.assertEqual(bridge._aggregate_grid_input(snapshot), (17.0, False))
        self.assertEqual(bridge._grid_snapshot_values(snapshot, 10.0), {"connected": False, "value": 17.0})

    def test_grid_source_lookup_requires_explicit_source_id_key(self) -> None:
        bridge = EnergyCompanionDbusBridge(SimpleNamespace(), "/tmp/service.py")
        snapshot = {"battery_sources": [{None: "None", "grid_interaction_w": 100.0, "online": True}]}

        self.assertIsNone(bridge._find_source_snapshot(snapshot, "None"))

    def test_grid_source_helpers_have_exact_defaults_and_payload_shape(self) -> None:
        bridge = EnergyCompanionDbusBridge(SimpleNamespace(), "/tmp/service.py")

        self.assertEqual(
            bridge._grid_source_hold_config(),
            {
                "hold_seconds": 0.0,
                "smoothing_alpha": 1.0,
                "smoothing_max_jump_watts": 0.0,
            },
        )
        self.assertEqual(
            bridge._grid_source_payload({"value": -12.5, "connected": False}),
            {
                "/Connected": 0,
                "/Ac/Power": -12.5,
                "/Ac/L1/Power": -12.5,
                "/Ac/L2/Power": 0.0,
                "/Ac/L3/Power": 0.0,
            },
        )
        self.assertEqual(
            bridge._grid_hold_config(),
            {
                "hold_seconds": 0.0,
                "smoothing_alpha": 1.0,
                "smoothing_max_jump_watts": 0.0,
            },
        )
        self.assertEqual(bridge._float_service_attr("missing_value", 2.0), 2.0)
        setattr(bridge.service, "explicit_one", 1.0)
        self.assertEqual(bridge._float_service_attr("explicit_one", 2.0), 1.0)
        self.assertEqual(bridge._source_id_value({}), "")
        self.assertFalse(bridge._online_count_positive({}))
        self.assertFalse(bridge._online_count_positive({"battery_online_source_count": 0}))
        self.assertTrue(bridge._online_count_positive({"battery_online_source_count": 1}))

    def test_grid_source_helpers_use_configured_hold_values_and_stable_state_key(self) -> None:
        service = SimpleNamespace(
            companion_source_grid_hold_seconds=7.0,
            companion_source_grid_smoothing_alpha=0.25,
            companion_source_grid_smoothing_max_jump_watts=80.0,
        )
        bridge = EnergyCompanionDbusBridge(service, "/tmp/service.py")

        self.assertEqual(
            bridge._grid_source_hold_config(),
            {
                "hold_seconds": 7.0,
                "smoothing_alpha": 0.25,
                "smoothing_max_jump_watts": 80.0,
            },
        )
        payload = bridge._grid_source_values({"source_id": "", "grid_interaction_w": 250.0, "online": True}, 10.0)

        self.assertEqual(payload["/Connected"], 1)
        self.assertEqual(payload["/Ac/Power"], 250.0)
        self.assertIn("source-grid:source", bridge._grid_hold_state)

    def test_grid_source_default_hold_is_disabled_and_zero_jump_threshold_disables_limit(self) -> None:
        service = SimpleNamespace(
            companion_source_grid_smoothing_alpha=0.5,
            companion_source_grid_smoothing_max_jump_watts=0.0,
        )
        bridge = EnergyCompanionDbusBridge(service, "/tmp/service.py")

        self.assertEqual(
            bridge._grid_source_values({"source_id": "meter-a", "grid_interaction_w": 100.0}, 10.0),
            {
                "/Connected": 0,
                "/Ac/Power": 100.0,
                "/Ac/L1/Power": 100.0,
                "/Ac/L2/Power": 0.0,
                "/Ac/L3/Power": 0.0,
            },
        )
        self.assertEqual(bridge._grid_source_values({"source_id": "meter-a", "grid_interaction_w": 300.0}, 11.0)["/Ac/Power"], 200.0)
        self.assertEqual(
            bridge._grid_source_values({"source_id": "meter-a"}, 11.5),
            {
                "/Connected": 0,
                "/Ac/Power": 0.0,
                "/Ac/L1/Power": 0.0,
                "/Ac/L2/Power": 0.0,
                "/Ac/L3/Power": 0.0,
            },
        )

    def test_grid_source_values_use_declared_source_id_before_unrelated_keys(self) -> None:
        bridge = EnergyCompanionDbusBridge(SimpleNamespace(), "/tmp/service.py")
        payload = bridge._grid_source_values(
            {None: "wrong", "source_id": " meter-a ", "grid_interaction_w": 12.0, "online": True},
            10.0,
        )

        self.assertEqual(payload["/Ac/Power"], 12.0)
        self.assertIn("source-grid:meter-a", bridge._grid_hold_state)
        self.assertNotIn("source-grid:wrong", bridge._grid_hold_state)

    def test_grid_numeric_smoothing_and_hold_window_contracts(self) -> None:
        grid = bridge_grid_mod._EnergyCompanionDbusBridgeGrid

        self.assertIsNone(grid._grid_numeric_value("250"))
        self.assertEqual(grid._grid_numeric_value(250), 250.0)
        self.assertEqual(grid._grid_normalized_alpha(-1.0), 0.0)
        self.assertEqual(grid._grid_normalized_alpha(0.25), 0.25)
        self.assertEqual(grid._grid_normalized_alpha(5.0), 1.0)
        self.assertEqual(grid._grid_smoothed_value(200.0, None, 0.5, 0.0), 200.0)
        self.assertEqual(grid._grid_smoothed_value(200.0, 100.0, 0.0, 0.0), 200.0)
        self.assertEqual(grid._grid_smoothed_value(200.0, 100.0, 1.0, 0.0), 200.0)
        self.assertEqual(grid._grid_smoothed_value(102.0, 100.0, 0.5, 1.0), 102.0)
        self.assertEqual(grid._grid_smoothed_value(200.0, 100.0, 0.5, 50.0), 200.0)
        self.assertEqual(grid._grid_smoothed_value(150.0, 100.0, 0.5, 50.0), 125.0)
        self.assertEqual(grid._grid_smoothed_value(130.0, 100.0, 0.5, 50.0), 115.0)
        self.assertFalse(grid._grid_within_hold_window({}, 100.0, 5.0))
        self.assertFalse(grid._grid_within_hold_window({"last_good_at": "99"}, 100.0, 5.0))
        self.assertFalse(grid._grid_within_hold_window({"last_good_at": 99.0}, 100.0, 0.0))
        self.assertFalse(grid._grid_within_hold_window({"last_good_at": 100.0}, 100.0, 0.0))
        self.assertTrue(grid._grid_within_hold_window({"last_good_at": 99.0}, 100.0, 1.0))
        self.assertFalse(grid._grid_within_hold_window({"last_good_at": 98.9}, 100.0, 1.0))

    def test_grid_resolved_payload_rejects_non_boolean_connection_state(self) -> None:
        grid = bridge_grid_mod._EnergyCompanionDbusBridgeGrid

        with self.assertRaises(TypeError) as context:
            grid._grid_resolved_payload(1.0, cast(bool, None), None)
        self.assertEqual(str(context.exception), "connected must be bool")

    def test_resolved_grid_value_expires_hold_state_after_window(self) -> None:
        bridge = EnergyCompanionDbusBridge(SimpleNamespace(), "/tmp/service.py")

        self.assertEqual(
            bridge._resolved_grid_value(
                "aggregate-grid",
                raw_value=42.0,
                online=True,
                now=10.0,
                hold_seconds=5.0,
                smoothing_alpha=1.0,
                smoothing_max_jump_watts=0.0,
            ),
            {"value": 42.0, "connected": True, "last_good_at": 10.0},
        )
        self.assertEqual(
            bridge._resolved_grid_value(
                "aggregate-grid",
                raw_value=None,
                online=False,
                now=14.0,
                hold_seconds=5.0,
                smoothing_alpha=1.0,
                smoothing_max_jump_watts=0.0,
            ),
            {"value": 42.0, "connected": True, "last_good_at": 10.0},
        )
        self.assertEqual(
            bridge._resolved_grid_value(
                "aggregate-grid",
                raw_value=None,
                online=False,
                now=16.0,
                hold_seconds=5.0,
                smoothing_alpha=1.0,
                smoothing_max_jump_watts=0.0,
            ),
            {"value": 0.0, "connected": False, "last_good_at": None},
        )
        self.assertNotIn("aggregate-grid", bridge._grid_hold_state)

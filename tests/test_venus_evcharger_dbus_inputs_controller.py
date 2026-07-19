# SPDX-License-Identifier: GPL-3.0-or-later
"""Component scenarios for the composed DBus input controller."""

from __future__ import annotations

import unittest
from typing import cast
from unittest.mock import patch

from tests.support.dbus_inputs import (
    DbusInputServiceFake,
    EnergyServiceResolverFake,
    GatewayReaderFake,
    SourceHealthFake,
)
from venus_evcharger.dbus_gateway import BATTERY_SOC_READ_KEY, GRID_POWER_READ_KEY, PV_POWER_READ_KEY
from venus_evcharger.energy import EnergyClusterSnapshot, EnergyLearningProfile, EnergySourceDefinition, EnergySourceSnapshot
from venus_evcharger.inputs.dbus import DbusInputController
from venus_evcharger.inputs.pv import PvInputReader, _service_name_list, _service_name_or_none
from venus_evcharger.inputs.storage import StorageInputReader
from venus_evcharger.inputs.storage_support import EnergyServiceResolver
from venus_evcharger.inputs.supervisor_snapshot_values import is_object_list
from venus_evcharger.ports.dbus import DbusInputPort


class TestDbusInputController(unittest.TestCase):
    def test_pv_discovery_payload_normalization_rejects_invalid_shapes(self) -> None:
        self.assertIsNone(_service_name_or_none(object()))
        self.assertIsNone(_service_name_or_none("  "))
        self.assertEqual(_service_name_or_none("  pv.one  "), "pv.one")
        self.assertIsNone(_service_name_list("pv.one"))
        self.assertEqual(_service_name_list([object(), " ", " pv.one "]), ["pv.one"])

    def test_controller_facade_owns_and_binds_each_component(self) -> None:
        service = DbusInputServiceFake(auto_pv_service="pv.explicit")
        port = DbusInputPort(service)
        controller = DbusInputController(port)
        self.assertEqual(controller.resolve_auto_pv_services(), ["pv.explicit"])
        controller.invalidate_auto_pv_services()
        controller.invalidate_auto_battery_service()
        self.assertEqual(service._resolved_auto_pv_services, [])
        self.assertIsNone(service._resolved_auto_battery_service)

    def test_pv_reader_resolves_override_cache_scan_and_invalidation(self) -> None:
        service = DbusInputServiceFake(auto_pv_service="pv.explicit")
        port = DbusInputPort(service)
        gateway = GatewayReaderFake(services=["pv.z", "pv.a", "other"])
        health = SourceHealthFake()
        reader = PvInputReader(port, gateway, health)
        self.assertEqual(reader.resolve_auto_pv_services(), ["pv.explicit"])

        service.auto_pv_service = ""
        service.auto_pv_service_prefix = "pv."
        with patch("venus_evcharger.inputs.pv.time.time", return_value=100.0):
            self.assertEqual(reader.resolve_auto_pv_services(), ["pv.a", "pv.z"])
        self.assertEqual(service._auto_pv_last_scan, 100.0)
        with patch("venus_evcharger.inputs.pv.time.time", return_value=101.0):
            self.assertEqual(reader.resolve_auto_pv_services(), ["pv.a", "pv.z"])
        self.assertEqual(gateway.service_list_calls, 1)
        reader.invalidate_auto_pv_services()
        self.assertEqual(service._resolved_auto_pv_services, [])

        gateway.services = []
        with patch("venus_evcharger.inputs.pv.time.time", return_value=200.0):
            with self.assertRaisesRegex(ValueError, "No DBus service"):
                reader.resolve_auto_pv_services()

    def test_pv_reader_applies_retry_success_and_failure_policy(self) -> None:
        service = DbusInputServiceFake()
        port = DbusInputPort(service)
        gateway = GatewayReaderFake(semantic_results={PV_POWER_READ_KEY: 2345.0})
        health = SourceHealthFake()
        reader = PvInputReader(port, gateway, health)
        self.assertEqual(reader.get_pv_power(), 2345.0)
        self.assertEqual(health.recoveries[-1][0], "pv")

        health.ready["pv"] = False
        self.assertIsNone(reader.get_pv_power())
        health.ready["pv"] = True
        gateway.semantic_results[PV_POWER_READ_KEY] = None
        self.assertIsNone(reader.get_pv_power())
        self.assertEqual(health.failures[-1][0], "pv")

    def test_energy_resolver_uses_explicit_primary_and_cached_discovered_sources(self) -> None:
        primary = EnergySourceDefinition("primary", service_name="battery.a", soc_path="/Soc")
        secondary = EnergySourceDefinition("secondary", service_prefix="hybrid.", soc_path="/Soc")
        service = DbusInputServiceFake(auto_energy_sources=(primary, secondary))
        port = DbusInputPort(service)
        gateway = GatewayReaderFake(
            services=["hybrid.z", "hybrid.a"],
            raw_results={
                ("battery.a", "/Soc"): [55.0],
                ("hybrid.a", "/Soc"): [60.0],
                ("hybrid.z", "/Soc"): [None],
            },
        )
        resolver = EnergyServiceResolver(port, gateway)
        self.assertEqual(resolver.primary_energy_source(), primary)
        self.assertEqual(resolver.resolve_auto_battery_service(), "battery.a")
        with patch("venus_evcharger.inputs.storage_support.time.time", return_value=100.0):
            self.assertEqual(resolver.resolve_energy_source_service(secondary), "hybrid.a")
        self.assertEqual(service._resolved_auto_energy_services["secondary"], "hybrid.a")
        with patch("venus_evcharger.inputs.storage_support.time.time", return_value=101.0):
            self.assertEqual(resolver.resolve_energy_source_service(secondary), "hybrid.a")
        resolver.invalidate_auto_battery_service()
        self.assertIsNone(service._resolved_auto_battery_service)

    def test_energy_resolver_scans_primary_and_rejects_unresolvable_sources(self) -> None:
        service = DbusInputServiceFake(auto_battery_service_prefix="battery.")
        port = DbusInputPort(service)
        gateway = GatewayReaderFake(
            services=["battery.a"],
            raw_results={("battery.a", "/Soc"): [50.0]},
        )
        resolver = EnergyServiceResolver(port, gateway)
        self.assertEqual(resolver.resolve_auto_battery_service(), "battery.a")
        self.assertEqual(service._resolved_auto_battery_service, "battery.a")

        missing = EnergySourceDefinition("missing")
        with self.assertRaisesRegex(ValueError, "No readable DBus service"):
            resolver.resolve_energy_source_service(missing)
        prefixed = EnergySourceDefinition("prefixed", service_prefix="none.")
        with self.assertRaisesRegex(ValueError, "No DBus service"):
            resolver.resolve_energy_source_service(prefixed)

    def test_energy_resolver_fake_honors_conditional_invalidation(self) -> None:
        source = EnergySourceDefinition("source-a")
        resolver = EnergyServiceResolverFake(source, {"source-a": "battery.a"})

        self.assertFalse(
            resolver.invalidate_energy_source_service(
                "source-a",
                expected_service="battery.old",
            )
        )
        self.assertEqual(resolver.services, {"source-a": "battery.a"})
        self.assertTrue(
            resolver.invalidate_energy_source_service(
                "source-a",
                expected_service="battery.a",
            )
        )
        self.assertFalse(resolver.invalidate_energy_source_service("source-a"))
        self.assertEqual(
            resolver.energy_invalidations,
            [
                ("source-a", "battery.old"),
                ("source-a", "battery.a"),
                ("source-a", None),
            ],
        )

    def test_battery_and_grid_semantic_reads_apply_bounds_and_health(self) -> None:
        source = EnergySourceDefinition("primary", service_name="battery.a")
        service = DbusInputServiceFake(auto_energy_sources=(source,))
        port = DbusInputPort(service)
        gateway = GatewayReaderFake(
            semantic_results={BATTERY_SOC_READ_KEY: 55.0, GRID_POWER_READ_KEY: -123.0}
        )
        health = SourceHealthFake()
        resolver = EnergyServiceResolverFake(source, {"primary": "battery.a"})
        reader = StorageInputReader(port, gateway, health, resolver)
        with patch("venus_evcharger.inputs.storage.time.time", return_value=50.0):
            self.assertEqual(reader.get_battery_soc(), 55.0)
            self.assertEqual(reader.get_grid_power(), -123.0)
        self.assertEqual(
            gateway.semantic_reads,
            [
                (BATTERY_SOC_READ_KEY, "main semantic battery SOC read"),
                (GRID_POWER_READ_KEY, "main semantic grid power read"),
            ],
        )
        self.assertEqual(
            health.recoveries,
            [("battery", "Battery SOC readings recovered", ()), ("grid", "Grid readings recovered", ())],
        )

        gateway.semantic_results[BATTERY_SOC_READ_KEY] = 101.0
        gateway.semantic_results[GRID_POWER_READ_KEY] = None
        with patch("venus_evcharger.inputs.storage.time.time", return_value=51.0):
            self.assertIsNone(reader.get_battery_soc())
            self.assertIsNone(reader.get_grid_power())
        self.assertEqual(
            health.failures,
            [
                (
                    "battery",
                    51.0,
                    "battery-missing",
                    30.0,
                    "Auto mode could not read battery SOC from the DBus gateway read contract.",
                    (),
                ),
                (
                    "grid",
                    51.0,
                    "grid-missing",
                    30.0,
                    "Auto mode could not read grid power from %s.",
                    ("com.victronenergy.system",),
                ),
            ],
        )

        health.ready.update(battery=False, grid=False)
        with patch("venus_evcharger.inputs.storage.time.time", return_value=52.0):
            self.assertIsNone(reader.get_battery_soc())
            self.assertIsNone(reader.get_grid_power())
        self.assertEqual(len(gateway.semantic_reads), 4)
        self.assertEqual(
            health.retry_checks,
            [("battery", 50.0), ("grid", 50.0), ("battery", 51.0), ("grid", 51.0), ("battery", 52.0), ("grid", 52.0)],
        )

    def test_battery_snapshot_reads_one_or_multiple_sources_and_stores_cluster(self) -> None:
        primary = EnergySourceDefinition(
            "primary",
            service_name="battery.a",
            soc_path="/Soc",
            usable_capacity_wh=10000.0,
            battery_power_path="/Power",
        )
        secondary = EnergySourceDefinition(
            "secondary",
            role="hybrid-inverter",
            service_name="hybrid.a",
            soc_path="/Soc",
            usable_capacity_wh=5000.0,
            battery_power_path="/Power",
        )
        service = DbusInputServiceFake(auto_energy_sources=(primary, secondary))
        port = DbusInputPort(service)
        gateway = GatewayReaderFake(
            raw_results={
                ("battery.a", "/Soc"): [60.0],
                ("battery.a", "/Power"): [-1000.0],
                ("hybrid.a", "/Soc"): [30.0],
                ("hybrid.a", "/Power"): [500.0],
            }
        )
        health = SourceHealthFake()
        resolver = EnergyServiceResolverFake(
            primary,
            {"primary": "battery.a", "secondary": "hybrid.a"},
        )
        reader = StorageInputReader(port, gateway, health, resolver)
        snapshot = reader.get_battery_snapshot()
        self.assertEqual(snapshot["battery_source_count"], 2)
        self.assertEqual(snapshot["battery_hybrid_inverter_source_count"], 1)
        self.assertEqual(snapshot["battery_source_count"], 2)
        battery_sources = snapshot["battery_sources"]
        self.assertTrue(is_object_list(battery_sources))
        assert is_object_list(battery_sources)
        self.assertEqual(len(battery_sources), 2)
        self.assertEqual(service._last_energy_cluster["battery_soc"], snapshot["battery_soc"])
        self.assertEqual(health.recoveries, [("battery", "Battery SOC readings recovered", ())])

        service.auto_use_combined_battery_soc = False
        self.assertEqual(reader.get_battery_snapshot()["battery_soc"], 60.0)

    def test_battery_snapshot_failure_and_retry_paths_are_bounded(self) -> None:
        source = EnergySourceDefinition("primary", service_name="battery.a", soc_path="/Soc")
        service = DbusInputServiceFake(
            auto_energy_sources=(source,),
            auto_battery_service="",
            auto_battery_service_prefix="battery.prefix.",
        )
        port = DbusInputPort(service)
        gateway = GatewayReaderFake(raw_results={("battery.a", "/Soc"): [OSError("down"), None]})
        health = SourceHealthFake()
        resolver = EnergyServiceResolverFake(source, {"primary": "battery.a"})
        reader = StorageInputReader(port, gateway, health, resolver)
        error = OSError("down")
        gateway.raw_results[("battery.a", "/Soc")] = [error, None]
        with patch("venus_evcharger.inputs.storage.time.time", return_value=75.0):
            snapshot = reader.get_battery_snapshot()
        self.assertEqual(snapshot, StorageInputReader._empty_battery_snapshot_payload(None))
        self.assertEqual(resolver.invalidations, 1)
        self.assertEqual(len(health.failures), 1)
        failure = health.failures[0]
        self.assertEqual(failure[:5], (
            "battery", 75.0, "battery-missing", 30.0,
            "Auto mode could not read battery SOC from %s %s: %s",
        ))
        self.assertEqual(failure[5][:2], ("battery.prefix.", "/Soc"))
        self.assertIsInstance(failure[5][2], TypeError)
        self.assertEqual(str(failure[5][2]), "Battery SOC is not numeric")

        health.ready["battery"] = False
        with patch("venus_evcharger.inputs.storage.time.time", return_value=76.0):
            self.assertEqual(
                reader.get_battery_snapshot(),
                StorageInputReader._empty_battery_snapshot_payload(None),
            )
        self.assertEqual(health.retry_checks, [("battery", 75.0), ("battery", 76.0)])

    def test_introspection_skip_avoids_optional_raw_read(self) -> None:
        source = EnergySourceDefinition(
            "primary",
            service_name="battery.a",
            soc_path="/Soc",
            battery_power_path="/Power",
        )
        service = DbusInputServiceFake(auto_energy_sources=(source,))
        gateway = GatewayReaderFake(raw_results={("battery.a", "/Soc"): [50.0]})
        health = SourceHealthFake()
        resolver = EnergyServiceResolverFake(
            source,
            {"primary": "battery.a"},
            skipped_paths={("battery.a", "/Power")},
        )
        reader = StorageInputReader(DbusInputPort(service), gateway, health, resolver)
        snapshot = reader.get_battery_snapshot()
        self.assertIsNotNone(snapshot["battery_soc"])
        self.assertNotIn(("battery.a", "/Power"), gateway.raw_reads)

    def test_semantic_battery_soc_accepts_both_physical_boundaries(self) -> None:
        source = EnergySourceDefinition("primary")
        gateway = GatewayReaderFake(semantic_results={BATTERY_SOC_READ_KEY: 0.0})
        health = SourceHealthFake()
        reader = StorageInputReader(
            DbusInputPort(DbusInputServiceFake()), gateway, health,
            EnergyServiceResolverFake(source, {"primary": "battery.a"}),
        )
        self.assertEqual(reader.get_battery_soc(), 0.0)
        gateway.semantic_results[BATTERY_SOC_READ_KEY] = 100.0
        self.assertEqual(reader.get_battery_soc(), 100.0)
        self.assertEqual([item[0] for item in health.recoveries], ["battery", "battery"])

    def test_storage_source_snapshot_preserves_every_read_field(self) -> None:
        source = EnergySourceDefinition(
            "source-a",
            role="hybrid-inverter",
            service_name="configured.a",
            soc_path="/Soc",
            usable_capacity_wh=12345.0,
            battery_power_path="/BatteryPower",
            ac_power_path="/AcPower",
            pv_power_path="/PvPower",
            grid_interaction_path="/GridPower",
            operating_mode_path="/Mode",
        )
        service = DbusInputServiceFake(auto_energy_sources=(source,))
        gateway = GatewayReaderFake(
            raw_results={
                ("resolved.a", "/Soc"): [42.5],
                ("resolved.a", "/BatteryPower"): [-701.0],
                ("resolved.a", "/AcPower"): [702.0],
                ("resolved.a", "/PvPower"): [703.0],
                ("resolved.a", "/GridPower"): [-704.0],
                ("resolved.a", "/Mode"): [cast(float, "  self-consumption  ")],
            }
        )
        resolver = EnergyServiceResolverFake(source, {"source-a": "resolved.a"})
        reader = StorageInputReader(DbusInputPort(service), gateway, SourceHealthFake(), resolver)

        snapshot = reader._dbus_energy_source_snapshot(source, 123.25)

        self.assertEqual(
            snapshot,
            EnergySourceSnapshot(
                source_id="source-a",
                role="hybrid-inverter",
                service_name="resolved.a",
                soc=42.5,
                usable_capacity_wh=12345.0,
                net_battery_power_w=-701.0,
                ac_power_w=702.0,
                pv_input_power_w=703.0,
                grid_interaction_w=-704.0,
                operating_mode="self-consumption",
                online=True,
                confidence=1.0,
                captured_at=123.25,
            ),
        )
        self.assertEqual(
            gateway.raw_reads,
            [
                ("resolved.a", "/Soc"),
                ("resolved.a", "/BatteryPower"),
                ("resolved.a", "/AcPower"),
                ("resolved.a", "/PvPower"),
                ("resolved.a", "/GridPower"),
                ("resolved.a", "/Mode"),
            ],
        )

    def test_storage_snapshot_payload_has_one_exact_public_schema(self) -> None:
        source = EnergySourceSnapshot("source-a", "battery", "battery.a", soc=55.0)
        cluster = EnergyClusterSnapshot(
            combined_soc=1.0,
            combined_usable_capacity_wh=2.0,
            combined_charge_power_w=3.0,
            combined_discharge_power_w=4.0,
            combined_net_battery_power_w=5.0,
            combined_ac_power_w=6.0,
            combined_pv_input_power_w=7.0,
            combined_grid_interaction_w=8.0,
            average_confidence=9.0,
            source_count=10,
            online_source_count=11,
            valid_soc_source_count=12,
            battery_source_count=13,
            hybrid_inverter_source_count=14,
            inverter_source_count=15,
            sources=(source,),
        )
        profile = EnergyLearningProfile("source-a")
        service = DbusInputServiceFake(_last_energy_learning_profiles={"source-a": profile})
        reader = StorageInputReader(
            DbusInputPort(service),
            GatewayReaderFake(),
            SourceHealthFake(),
            EnergyServiceResolverFake(EnergySourceDefinition("source-a"), {"source-a": "battery.a"}),
        )
        payload = reader._battery_snapshot_payload(
            16.0,
            cluster,
            {
                "battery_headroom_charge_w": 17.0,
                "battery_headroom_discharge_w": 18.0,
                "expected_near_term_export_w": 19.0,
                "expected_near_term_import_w": 20.0,
            },
            {
                "mode": "balanced",
                "target_distribution_mode": "weighted",
                "error_w": 21.0,
                "max_abs_error_w": 22.0,
                "total_discharge_w": 23.0,
                "eligible_source_count": 24,
                "active_source_count": 25,
            },
            {
                "control_candidate_count": 26,
                "control_ready_count": 27,
                "supported_control_source_count": 28,
                "experimental_control_source_count": 29,
            },
            [{"source_id": "source-a", "marker": 30}],
            updated_profiles={"source-a": profile},
        )
        self.assertEqual(
            payload,
            {
                "battery_soc": 16.0,
                "battery_combined_soc": 1.0,
                "battery_combined_usable_capacity_wh": 2.0,
                "battery_combined_charge_power_w": 3.0,
                "battery_combined_discharge_power_w": 4.0,
                "battery_combined_net_power_w": 5.0,
                "battery_combined_ac_power_w": 6.0,
                "battery_combined_pv_input_power_w": 7.0,
                "battery_combined_grid_interaction_w": 8.0,
                "battery_headroom_charge_w": 17.0,
                "battery_headroom_discharge_w": 18.0,
                "expected_near_term_export_w": 19.0,
                "expected_near_term_import_w": 20.0,
                "battery_discharge_balance_mode": "balanced",
                "battery_discharge_balance_target_distribution_mode": "weighted",
                "battery_discharge_balance_error_w": 21.0,
                "battery_discharge_balance_max_abs_error_w": 22.0,
                "battery_discharge_balance_total_discharge_w": 23.0,
                "battery_discharge_balance_eligible_source_count": 24,
                "battery_discharge_balance_active_source_count": 25,
                "battery_discharge_balance_control_candidate_count": 26,
                "battery_discharge_balance_control_ready_count": 27,
                "battery_discharge_balance_supported_control_source_count": 28,
                "battery_discharge_balance_experimental_control_source_count": 29,
                "battery_average_confidence": 9.0,
                "battery_source_count": 10,
                "battery_online_source_count": 11,
                "battery_valid_soc_source_count": 12,
                "battery_battery_source_count": 13,
                "battery_hybrid_inverter_source_count": 14,
                "battery_inverter_source_count": 15,
                "battery_sources": [{"source_id": "source-a", "marker": 30}],
                "battery_learning_profiles": {"source-a": profile.as_dict()},
            },
        )

    def test_storage_empty_snapshot_payload_has_one_exact_public_schema(self) -> None:
        payload = StorageInputReader._empty_battery_snapshot_payload(-1.0)
        expected_none = {
            "battery_combined_soc",
            "battery_combined_usable_capacity_wh",
            "battery_combined_charge_power_w",
            "battery_combined_discharge_power_w",
            "battery_combined_net_power_w",
            "battery_combined_ac_power_w",
            "battery_combined_pv_input_power_w",
            "battery_combined_grid_interaction_w",
            "battery_headroom_charge_w",
            "battery_headroom_discharge_w",
            "expected_near_term_export_w",
            "expected_near_term_import_w",
            "battery_discharge_balance_error_w",
            "battery_discharge_balance_max_abs_error_w",
            "battery_discharge_balance_total_discharge_w",
            "battery_average_confidence",
        }
        expected_zero = {
            "battery_discharge_balance_eligible_source_count",
            "battery_discharge_balance_active_source_count",
            "battery_discharge_balance_control_candidate_count",
            "battery_discharge_balance_control_ready_count",
            "battery_discharge_balance_supported_control_source_count",
            "battery_discharge_balance_experimental_control_source_count",
            "battery_source_count",
            "battery_online_source_count",
            "battery_valid_soc_source_count",
            "battery_battery_source_count",
            "battery_hybrid_inverter_source_count",
            "battery_inverter_source_count",
        }
        self.assertEqual(payload["battery_soc"], -1.0)
        self.assertEqual({key for key, value in payload.items() if value is None}, expected_none)
        self.assertEqual({key for key, value in payload.items() if value == 0}, expected_zero)
        self.assertEqual(payload["battery_discharge_balance_mode"], "")
        self.assertEqual(payload["battery_discharge_balance_target_distribution_mode"], "")
        self.assertEqual(payload["battery_sources"], [])
        self.assertEqual(payload["battery_learning_profiles"], {})
        self.assertEqual(len(payload), 33)


if __name__ == "__main__":
    unittest.main()

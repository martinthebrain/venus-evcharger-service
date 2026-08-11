# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import math
import unittest

from venus_evcharger.dbus_adapter.read.discovery import DbusEnergyDiscoveryManager
from venus_evcharger.dbus_adapter.read.semantic import energy_inputs_snapshot
from venus_evcharger.dbus_adapter.read.spec import ReadSpecs
from venus_evcharger.ipc.energy import MeasuredValue


def _specs() -> ReadSpecs:
    return {
        "grid_power_w": {
            "service": "com.victronenergy.system",
            "paths": ["/Ac/Grid/L1/Power", "/Ac/Grid/L2/Power"],
        },
        "pv_power_w": {
            "prefix": "com.victronenergy.pvinverter",
            "path": "/Ac/Power",
            "dc_service": "com.victronenergy.system",
            "dc_path": "/Dc/Pv/Power",
            "use_dc_pv": True,
        },
        "battery_soc": {
            "prefix": "com.victronenergy.battery",
            "path": "/Soc",
        },
        "battery_net_power_w": {
            "service": "com.victronenergy.system",
            "path": "/Dc/Battery/Power",
        },
    }


class GatewayEnergyDiscoveryContracts(unittest.TestCase):
    def setUp(self) -> None:
        self.discovery = DbusEnergyDiscoveryManager(_specs(), max_prefix_services=2)

    def test_discovery_is_stable_bounded_and_opaque(self) -> None:
        self.assertEqual(self.discovery.generation, 0)
        self.discovery.update_services(
            [
                "com.victronenergy.pvinverter.z",
                "com.victronenergy.battery.b",
                "com.victronenergy.pvinverter.a",
                "com.victronenergy.battery.a",
                "com.victronenergy.pvinverter.b",
                "com.victronenergy.system",
                " ",
                "com.victronenergy.system",
            ],
            captured_at=10.0,
        )
        self.assertEqual(self.discovery.generation, 1)
        self.discovery.record_pv_value("com.victronenergy.pvinverter.a", "/Ac/Power", 100.0)
        self.discovery.record_pv_value("com.victronenergy.pvinverter.b", "/Ac/Power", 200.0)
        self.discovery.record_pv_value("com.victronenergy.system", "/Dc/Pv/Power", 300.0)
        topology = self.discovery.topology_snapshot(captured_at=11.0)
        self.assertEqual(topology.generation, 4)
        self.assertEqual(topology.captured_at, 11.0)
        self.assertEqual([source.kind for source in topology.sources], ["grid", "pv_ac", "pv_ac", "pv_dc", "battery", "battery"])
        self.assertTrue(all("com.victronenergy" not in source.source_id for source in topology.sources))
        self.assertEqual(len({source.source_id for source in topology.sources}), len(topology.sources))
        self.assertTrue(all(source.state == "online" for source in topology.sources))

        self.discovery.update_services(list(reversed([
            "com.victronenergy.pvinverter.z",
            "com.victronenergy.battery.b",
            "com.victronenergy.pvinverter.a",
            "com.victronenergy.battery.a",
            "com.victronenergy.pvinverter.b",
            "com.victronenergy.system",
        ])), captured_at=12.0)
        self.assertEqual(self.discovery.generation, 4)
        self.assertEqual(self.discovery.topology_snapshot(captured_at=11.0).captured_at, 12.0)

    def test_explicit_service_and_missing_prefix_selection(self) -> None:
        explicit = {"service": " explicit.service ", "prefix": "ignored"}
        self.assertEqual(self.discovery.services_for(explicit), ["explicit.service"])
        self.assertEqual(self.discovery.first_service(explicit), "explicit.service")
        self.assertEqual(self.discovery.services_for({}), [])
        self.assertIsNone(self.discovery.first_service({}))

        bounded = DbusEnergyDiscoveryManager(_specs(), max_prefix_services=0)
        bounded.update_services(
            ["com.victronenergy.pvinverter.a", "com.victronenergy.pvinverter.b"],
            captured_at=1.0,
        )
        self.assertEqual(bounded.services_for(_specs()["pv_power_w"]), ["com.victronenergy.pvinverter.a"])

    def test_unknown_offline_and_online_source_states(self) -> None:
        unknown = self.discovery.topology_snapshot(captured_at=1.0)
        self.assertEqual([item.state for item in unknown.sources], ["unknown"])
        self.discovery.update_services(["other.service"], captured_at=2.0)
        offline = self.discovery.topology_snapshot(captured_at=2.0)
        self.assertEqual([item.state for item in offline.sources], ["offline"])

    def test_refresh_keys_are_resolved_from_opaque_source_identity(self) -> None:
        self.discovery.update_services(
            [
                "com.victronenergy.system",
                "com.victronenergy.pvinverter.a",
                "com.victronenergy.battery.a",
            ],
            captured_at=1.0,
        )
        self.discovery.record_pv_value("com.victronenergy.pvinverter.a", "/Ac/Power", 100.0)
        self.discovery.record_pv_value("com.victronenergy.system", "/Dc/Pv/Power", 200.0)
        sources = self.discovery.topology_snapshot(captured_at=1.0).sources
        by_kind = {source.kind: source.source_id for source in sources}
        self.assertEqual(self.discovery.read_keys_for_source(by_kind["grid"]), ("grid_power_w",))
        self.assertEqual(self.discovery.read_keys_for_source(by_kind["pv_ac"]), ("pv_power_w",))
        self.assertEqual(self.discovery.read_keys_for_source(by_kind["pv_dc"]), ("pv_power_w",))
        self.assertEqual(
            self.discovery.read_keys_for_source(by_kind["battery"]),
            ("battery_soc", "battery_net_power_w"),
        )
        self.assertEqual(self.discovery.read_keys_for_source("missing"), ())

    def test_introspection_targets_remain_adapter_private(self) -> None:
        self.discovery.update_services(
            [
                "com.victronenergy.system",
                "com.victronenergy.pvinverter.a",
                "com.victronenergy.battery.a",
            ],
            captured_at=1.0,
        )
        targets = self.discovery.introspection_targets()
        self.assertEqual(
            [(item.source, item.priority, item.reason) for item in targets],
            [
                ("grid", 80, "configured-grid-field"),
                ("grid", 80, "configured-grid-field"),
                ("battery", 70, "discovered-battery-field"),
                ("battery", 70, "configured-battery-power-field"),
                ("pv", 30, "discovered-ac-pv-field"),
                ("pv", 30, "configured-dc-pv-field"),
            ],
        )
        members = self.discovery.pv_members(_specs()["pv_power_w"])
        self.assertEqual(
            members,
            [
                ("com.victronenergy.pvinverter.a", "/Ac/Power"),
                ("com.victronenergy.system", "/Dc/Pv/Power"),
            ],
        )

    def test_disabled_or_invalid_dc_source_is_not_discovered(self) -> None:
        specs = _specs()
        specs["pv_power_w"]["use_dc_pv"] = False
        disabled = DbusEnergyDiscoveryManager(specs)
        self.assertEqual([item.kind for item in disabled.topology_snapshot(captured_at=1.0).sources], ["grid"])
        self.assertEqual(
            [target.reason for target in disabled.introspection_targets()],
            [
                "configured-grid-field",
                "configured-grid-field",
                "configured-battery-power-field",
            ],
        )
        specs["pv_power_w"]["use_dc_pv"] = True
        specs["pv_power_w"]["dc_path"] = ""
        invalid = DbusEnergyDiscoveryManager(specs)
        self.assertEqual([item.kind for item in invalid.topology_snapshot(captured_at=1.0).sources], ["grid"])
        self.assertNotIn(
            "configured-dc-pv-field",
            [target.reason for target in invalid.introspection_targets()],
        )

        without_grid = _specs()
        without_grid["grid_power_w"] = {}
        dc_only = DbusEnergyDiscoveryManager(without_grid)
        self.assertEqual(dc_only.topology_snapshot(captured_at=1.0).sources, ())
        dc_only.update_services(["com.victronenergy.system"], captured_at=1.0)
        dc_only.record_pv_value("com.victronenergy.system", "/Dc/Pv/Power", 0.0)
        self.assertEqual(
            dc_only.topology_snapshot(captured_at=1.0).sources[0].kind,
            "pv_dc",
        )


class GatewayEnergySnapshotContracts(unittest.TestCase):
    def setUp(self) -> None:
        self.discovery = DbusEnergyDiscoveryManager(_specs())
        self.discovery.update_services(
            [
                "com.victronenergy.system",
                "com.victronenergy.pvinverter.a",
                "com.victronenergy.battery.a",
            ],
            captured_at=90.0,
        )
        self.discovery.record_pv_value("com.victronenergy.pvinverter.a", "/Ac/Power", 100.0)
        self.discovery.record_pv_value("com.victronenergy.system", "/Dc/Pv/Power", 200.0)

    def test_snapshot_projects_values_quality_and_opaque_sources(self) -> None:
        snapshot = energy_inputs_snapshot(
            {
                "grid_power_w": {"value": -20, "updated_at": 98.0, "updated_monotonic": 98.0, "status": "fresh", "confidence": 1.0},
                "pv_power_w": {"value": 500.5, "confirmed_at": 97.0, "confirmed_monotonic": 97.0, "status": "stale", "confidence": 0.7},
                "battery_soc": {"value": 75.0, "updated_at": 99.0, "updated_monotonic": 99.0, "status": "fresh", "confidence": 2.0},
                "battery_net_power_w": {
                    "value": 1730.0,
                    "updated_at": 99.0,
                    "updated_monotonic": 99.0,
                    "status": "fresh",
                    "confidence": 1.0,
                    "reason_code": "native-battery-power",
                },
            },
            self.discovery,
            sequence=4,
            captured_at=100.0,
            captured_monotonic=100.0,
        )
        self.assertEqual(snapshot.sequence, 4)
        self.assertEqual(snapshot.topology_generation, 3)
        self.assertEqual(snapshot.grid_power_w.value, -20.0)
        self.assertEqual(snapshot.grid_power_w.reason_code, "")
        self.assertEqual(snapshot.pv_power_w.status, "stale")
        self.assertEqual(snapshot.pv_power_w.reason_code, "observation-stale")
        self.assertEqual(snapshot.pv_power_w.observed_at, 97.0)
        self.assertEqual(snapshot.pv_power_w.confidence, 0.7)
        self.assertEqual(snapshot.battery_soc.confidence, 1.0)
        self.assertEqual(snapshot.battery_net_power_w.value, -1730.0)
        self.assertEqual(snapshot.battery_net_power_w.status, "fresh")
        self.assertEqual(snapshot.battery_net_power_w.reason_code, "native-battery-power")
        self.assertEqual(snapshot.grid_power_w.source_ids, self.discovery.source_ids("grid"))
        self.assertEqual(
            snapshot.pv_power_w.source_ids,
            (*self.discovery.source_ids("pv_ac"), *self.discovery.source_ids("pv_dc")),
        )
        self.assertEqual(snapshot.battery_soc.source_ids, self.discovery.source_ids("battery"))
        self.assertEqual(snapshot.battery_net_power_w.source_ids, self.discovery.source_ids("battery"))
        self.assertTrue(all("com.victronenergy" not in item for item in snapshot.pv_power_w.source_ids))

    def test_missing_invalid_and_error_entries_are_normalized(self) -> None:
        snapshot = energy_inputs_snapshot(
            {
                "pv_power_w": {"value": math.nan, "status": "fresh", "confidence": -1.0},
                "battery_soc": {"value": 60.0, "updated_at": "bad", "status": "error", "confidence": "bad"},
            },
            self.discovery,
            sequence=-3,
            captured_at=100.0,
            captured_monotonic=100.0,
        )
        self.assertEqual(snapshot.sequence, 0)
        self.assertEqual(
            snapshot.grid_power_w,
            MeasuredValue(
                value=None,
                observed_at=0.0,
                observed_monotonic=0.0,
                status="unknown",
                confidence=0.0,
                source_ids=self.discovery.source_ids("grid"),
                reason_code="not-observed",
            ),
        )
        self.assertEqual(snapshot.pv_power_w.status, "unavailable")
        self.assertEqual(snapshot.pv_power_w.reason_code, "source-unavailable")
        self.assertEqual(snapshot.battery_soc.status, "error")
        self.assertEqual(snapshot.battery_soc.reason_code, "source-error")
        self.assertEqual(snapshot.battery_soc.observed_at, 0.0)
        self.assertEqual(snapshot.battery_soc.confidence, 0.0)

    def test_unavailable_value_and_unknown_numeric_value_have_distinct_reasons(self) -> None:
        unavailable = energy_inputs_snapshot(
            {
                "grid_power_w": {"value": None, "status": "unavailable"},
                "pv_power_w": {"value": 0, "updated_at": 1, "updated_monotonic": 1, "status": "other"},
                "battery_soc": {"value": True, "status": "error"},
            },
            self.discovery,
            sequence=1,
            captured_at=2.0,
            captured_monotonic=2.0,
        )
        self.assertEqual(unavailable.grid_power_w.reason_code, "source-unavailable")
        self.assertEqual(unavailable.pv_power_w.status, "unknown")
        self.assertEqual(unavailable.pv_power_w.reason_code, "")
        self.assertEqual(unavailable.battery_soc.value, None)
        self.assertEqual(unavailable.battery_soc.reason_code, "source-unavailable")

        unknown_missing = energy_inputs_snapshot(
            {
                "grid_power_w": {"value": None, "status": "other"},
                "pv_power_w": {"value": 0.0, "updated_at": 1.0, "updated_monotonic": 1.0, "status": "fresh"},
                "battery_soc": {"value": 50.0, "updated_at": 1.0, "updated_monotonic": 1.0, "status": "fresh"},
            },
            self.discovery,
            sequence=1,
            captured_at=2.0,
            captured_monotonic=2.0,
        )
        self.assertEqual(unknown_missing.grid_power_w.reason_code, "not-observed")

    def test_stale_missing_value_and_subunit_metadata_are_normalized_exactly(self) -> None:
        snapshot = energy_inputs_snapshot(
            {
                "grid_power_w": {
                    "value": 10.0,
                    "updated_at": 0.5,
                    "updated_monotonic": 0.5,
                    "status": "fresh",
                    "confidence": 0.25,
                    "reason_code": 7,
                },
                "pv_power_w": {
                    "value": None,
                    "updated_at": 0.5,
                    "updated_monotonic": 0.5,
                    "status": "stale",
                    "confidence": 0.25,
                },
            },
            self.discovery,
            sequence=1,
            captured_at=2.0,
            captured_monotonic=2.0,
        )

        self.assertEqual(snapshot.grid_power_w.observed_at, 0.5)
        self.assertEqual(snapshot.grid_power_w.confidence, 0.25)
        self.assertEqual(snapshot.grid_power_w.reason_code, "")
        self.assertEqual(snapshot.pv_power_w.value, None)
        self.assertEqual(snapshot.pv_power_w.status, "unavailable")
        self.assertEqual(snapshot.pv_power_w.reason_code, "source-unavailable")


if __name__ == "__main__":
    unittest.main()

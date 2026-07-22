# SPDX-License-Identifier: GPL-3.0-or-later
"""Mutation-strength contracts for adapter-owned energy discovery."""

from __future__ import annotations

import hashlib
import unittest
from typing import cast

from venus_evcharger.dbus_adapter.read.discovery import (
    DbusEnergyDiscoveryManager,
    IntrospectionTarget,
)
from venus_evcharger.dbus_adapter.read.spec import ReadSpec, ReadSpecs
from venus_evcharger.ipc.energy import EnergySourceDescriptor


GRID_SERVICE = "com.victronenergy.system"
AC_PV_A = "com.victronenergy.pvinverter.a"
AC_PV_B = "com.victronenergy.pvinverter.b"
BATTERY_A = "com.victronenergy.battery.a"


def _source_id(kind: str, service: str) -> str:
    digest = hashlib.sha256(service.encode("utf-8")).hexdigest()[:10]
    return f"{kind}-{digest}"


def _specs() -> ReadSpecs:
    return {
        "grid_power_w": {
            "service": GRID_SERVICE,
            "paths": ["/Ac/Grid/L1/Power", "/Ac/Grid/L2/Power"],
        },
        "pv_power_w": {
            "prefix": "com.victronenergy.pvinverter.",
            "path": "/Ac/Power",
            "dc_service": GRID_SERVICE,
            "dc_path": "/Dc/Pv/Power",
            "use_dc_pv": True,
        },
        "battery_soc": {
            "prefix": "com.victronenergy.battery.",
            "path": "/Soc",
        },
    }


class DbusAdapterDiscoveryMutationContracts(unittest.TestCase):
    def test_default_limit_and_explicit_service_precedence_are_exact(self) -> None:
        manager = DbusEnergyDiscoveryManager({})
        names = [f"source.{index:02d}" for index in range(12, -1, -1)]
        manager.update_services(names, now=3.0)

        self.assertEqual(manager.services_for({"prefix": "source."}), [f"source.{index:02d}" for index in range(10)])
        self.assertEqual(
            manager.services_for({"service": "  explicit.service  ", "prefix": "source."}),
            ["explicit.service"],
        )

        bounded = DbusEnergyDiscoveryManager({}, max_prefix_services=0)
        bounded.update_services(names, now=3.0)
        self.assertEqual(bounded.services_for({"prefix": "source."}), ["source.00"])

    def test_service_normalization_generation_and_capture_time_are_exact(self) -> None:
        manager = DbusEnergyDiscoveryManager({"pv_power_w": {"prefix": "pv."}}, max_prefix_services=3)
        manager.update_services([" pv.b ", "pv.a", "pv.a", "", "   ", "other"], now=-4.0)

        self.assertEqual(manager.generation, 1)
        self.assertEqual(manager.services_for({"prefix": "pv."}), ["pv.a", "pv.b"])
        self.assertEqual(manager.topology_snapshot(now=0.25).captured_at, 0.25)

        manager.update_services(["other", "pv.a", "pv.b"], now=4.5)
        self.assertEqual(manager.generation, 1)
        self.assertEqual(manager.topology_snapshot(now=2.0).captured_at, 4.5)

        manager.update_services(["pv.c", "pv.b", "pv.a", "other"], now=5.0)
        self.assertEqual(manager.generation, 2)
        self.assertEqual(manager.services_for({"prefix": "pv."}), ["pv.a", "pv.b", "pv.c"])

    def test_topology_descriptor_contract_is_complete_and_ordered(self) -> None:
        manager = DbusEnergyDiscoveryManager(_specs(), max_prefix_services=1)
        initial = manager.topology_snapshot(now=0.5)
        self.assertEqual(initial.captured_at, 0.5)
        self.assertEqual(
            initial.sources,
            (
                EnergySourceDescriptor("grid-primary", "grid", "unknown", ("power",)),
                EnergySourceDescriptor(_source_id("pv-dc", GRID_SERVICE), "pv_dc", "unknown", ("power",)),
            ),
        )

        manager.update_services([AC_PV_B, BATTERY_A, GRID_SERVICE, AC_PV_A], now=1.0)
        snapshot = manager.topology_snapshot(now=2.0)
        self.assertEqual(snapshot.generation, 1)
        self.assertEqual(snapshot.captured_at, 2.0)
        self.assertEqual(
            snapshot.sources,
            (
                EnergySourceDescriptor("grid-primary", "grid", "online", ("power",)),
                EnergySourceDescriptor(_source_id("pv-ac", AC_PV_A), "pv_ac", "online", ("power",)),
                EnergySourceDescriptor(_source_id("pv-dc", GRID_SERVICE), "pv_dc", "online", ("power",)),
                EnergySourceDescriptor(_source_id("battery", BATTERY_A), "battery", "online", ("soc",)),
            ),
        )

        manager.update_services(["unrelated.service"], now=3.0)
        self.assertEqual(
            manager.topology_snapshot(now=3.0).sources,
            (
                EnergySourceDescriptor("grid-primary", "grid", "offline", ("power",)),
                EnergySourceDescriptor(_source_id("pv-dc", GRID_SERVICE), "pv_dc", "offline", ("power",)),
            ),
        )

    def test_source_identity_queries_preserve_semantic_kinds(self) -> None:
        manager = DbusEnergyDiscoveryManager(_specs())
        manager.update_services([GRID_SERVICE, AC_PV_A, BATTERY_A], now=1.0)

        expected_ids = {
            "grid": "grid-primary",
            "pv_ac": _source_id("pv-ac", AC_PV_A),
            "pv_dc": _source_id("pv-dc", GRID_SERVICE),
            "battery": _source_id("battery", BATTERY_A),
        }
        for kind, source_id in expected_ids.items():
            with self.subTest(kind=kind):
                self.assertEqual(manager.source_ids(kind), (source_id,))

        self.assertEqual(manager.source_ids("missing"), ())
        self.assertEqual(manager.read_keys_for_source(expected_ids["grid"]), ("grid_power_w",))
        self.assertEqual(manager.read_keys_for_source(expected_ids["pv_ac"]), ("pv_power_w",))
        self.assertEqual(manager.read_keys_for_source(expected_ids["pv_dc"]), ("pv_power_w",))
        self.assertEqual(manager.read_keys_for_source(expected_ids["battery"]), ("battery_soc",))
        self.assertEqual(manager.read_keys_for_source("missing"), ())

    def test_introspection_targets_have_exact_order_and_metadata(self) -> None:
        manager = DbusEnergyDiscoveryManager(_specs())
        manager.update_services([AC_PV_A, BATTERY_A], now=1.0)

        self.assertEqual(
            manager.introspection_targets(),
            [
                IntrospectionTarget(GRID_SERVICE, "/Ac/Grid/L1/Power", 80, "grid", "configured-grid-field"),
                IntrospectionTarget(GRID_SERVICE, "/Ac/Grid/L2/Power", 80, "grid", "configured-grid-field"),
                IntrospectionTarget(BATTERY_A, "/Soc", 70, "battery", "discovered-battery-field"),
                IntrospectionTarget(AC_PV_A, "/Ac/Power", 30, "pv", "discovered-ac-pv-field"),
                IntrospectionTarget(GRID_SERVICE, "/Dc/Pv/Power", 30, "pv", "configured-dc-pv-field"),
            ],
        )

    def test_missing_fields_and_non_boolean_dc_flag_never_create_targets(self) -> None:
        malformed_bool = cast(
            ReadSpec,
            {
                "dc_service": GRID_SERVICE,
                "dc_path": "/Dc/Pv/Power",
                "use_dc_pv": 1,
            },
        )
        missing_dc_service: ReadSpec = {"dc_path": "/Dc/Pv/Power", "use_dc_pv": True}
        missing_dc_path: ReadSpec = {"dc_service": GRID_SERVICE, "use_dc_pv": True}

        for pv_spec in (malformed_bool, missing_dc_service, missing_dc_path):
            with self.subTest(pv_spec=pv_spec):
                manager = DbusEnergyDiscoveryManager({"pv_power_w": pv_spec})
                self.assertEqual(manager.topology_snapshot(now=1.0).sources, ())
                self.assertEqual(manager.introspection_targets(), [])

        malformed_text = cast(ReadSpec, {"service": 7, "prefix": "source."})
        manager = DbusEnergyDiscoveryManager({})
        manager.update_services(["source.a"], now=1.0)
        self.assertEqual(manager.services_for(malformed_text), ["source.a"])

    def test_blank_paths_and_services_are_filtered_without_partial_targets(self) -> None:
        specs: ReadSpecs = {
            "grid_power_w": {"service": " ", "paths": ["/Grid"]},
            "battery_soc": {"service": BATTERY_A, "path": " "},
            "pv_power_w": {
                "service": AC_PV_A,
                "path": " ",
                "dc_service": GRID_SERVICE,
                "dc_path": " ",
                "use_dc_pv": True,
            },
        }
        manager = DbusEnergyDiscoveryManager(specs)

        self.assertEqual(
            manager.topology_snapshot(now=1.0).sources,
            (
                EnergySourceDescriptor(_source_id("pv-ac", AC_PV_A), "pv_ac", "unknown", ("power",)),
                EnergySourceDescriptor(_source_id("battery", BATTERY_A), "battery", "unknown", ("soc",)),
            ),
        )
        self.assertEqual(manager.introspection_targets(), [])

        configured_grid_without_path = DbusEnergyDiscoveryManager(
            {"grid_power_w": {"service": GRID_SERVICE, "paths": [""]}}
        )
        self.assertEqual(configured_grid_without_path.introspection_targets(), [])

    def test_absent_pv_spec_has_no_sources_or_introspection_targets(self) -> None:
        manager = DbusEnergyDiscoveryManager({})

        self.assertEqual(manager.topology_snapshot(now=0.5).sources, ())
        self.assertEqual(manager.introspection_targets(), [])


if __name__ == "__main__":
    unittest.main()

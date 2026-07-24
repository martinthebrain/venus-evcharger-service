# SPDX-License-Identifier: GPL-3.0-or-later
"""Mutation-strength contracts for adapter-owned energy discovery."""

from __future__ import annotations

import hashlib
import unittest

from venus_evcharger.dbus_adapter.read.discovery import (
    DbusEnergyDiscoveryManager,
    IntrospectionTarget,
)
from venus_evcharger.dbus_adapter.read.pv_discovery import PvSourceRegistry
from venus_evcharger.dbus_adapter.read.spec import ReadSpecs, read_spec_from_mapping
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


class _Clock:
    def __init__(self, monotonic_at: float = 0.0, wall_clock_at: float = 0.0) -> None:
        self.monotonic_at = monotonic_at
        self.wall_clock_at = wall_clock_at

    def monotonic(self) -> float:
        return self.monotonic_at

    def wall_clock(self) -> float:
        return self.wall_clock_at


class DbusAdapterDiscoveryMutationContracts(unittest.TestCase):
    def test_default_limit_and_explicit_service_precedence_are_exact(self) -> None:
        manager = DbusEnergyDiscoveryManager({})
        names = [f"source.{index:02d}" for index in range(12, -1, -1)]
        manager.update_services(names, captured_at=3.0)

        self.assertEqual(manager.services_for({"prefix": "source."}), [f"source.{index:02d}" for index in range(10)])
        self.assertEqual(
            manager.services_for({"service": "  explicit.service  ", "prefix": "source."}),
            ["explicit.service"],
        )

        bounded = DbusEnergyDiscoveryManager({}, max_prefix_services=0)
        bounded.update_services(names, captured_at=3.0)
        self.assertEqual(bounded.services_for({"prefix": "source."}), ["source.00"])

    def test_service_normalization_generation_and_capture_time_are_exact(self) -> None:
        manager = DbusEnergyDiscoveryManager({"pv_power_w": {"prefix": "pv."}}, max_prefix_services=3)
        manager.update_services([" pv.b ", "pv.a", "pv.a", "", "   ", "other"], captured_at=-4.0)

        self.assertEqual(manager.generation, 1)
        self.assertEqual(manager.pv_observation_count, 0)
        self.assertEqual(manager.services_for({"prefix": "pv."}), ["pv.a", "pv.b"])
        self.assertEqual(manager.first_service({"prefix": "pv."}), "pv.a")
        self.assertIsNone(manager.first_service({"prefix": "missing."}))
        self.assertEqual(manager.topology_snapshot(captured_at=0.25).captured_at, 0.25)

        manager.update_services(["other", "pv.a", "pv.b"], captured_at=4.5)
        self.assertEqual(manager.generation, 1)
        self.assertEqual(manager.topology_snapshot(captured_at=2.0).captured_at, 4.5)

        manager.update_services(["pv.c", "pv.b", "pv.a", "other"], captured_at=5.0)
        self.assertEqual(manager.generation, 2)
        self.assertEqual(manager.services_for({"prefix": "pv."}), ["pv.a", "pv.b", "pv.c"])

    def test_topology_descriptor_contract_is_complete_and_ordered(self) -> None:
        manager = DbusEnergyDiscoveryManager(_specs(), max_prefix_services=1)
        initial = manager.topology_snapshot(captured_at=0.5)
        self.assertEqual(initial.captured_at, 0.5)
        self.assertEqual(
            initial.sources,
            (
                EnergySourceDescriptor("grid-primary", "grid", "unknown", ("power",)),
            ),
        )

        manager.update_services([AC_PV_B, BATTERY_A, GRID_SERVICE, AC_PV_A], captured_at=1.0)
        unvalidated = manager.topology_snapshot(captured_at=1.5)
        self.assertFalse(any(source.kind in {"pv_ac", "pv_dc"} for source in unvalidated.sources))

        manager.record_pv_value(AC_PV_A, "/Ac/Power", 120.0)
        manager.record_pv_value(GRID_SERVICE, "/Dc/Pv/Power", 0.0)
        snapshot = manager.topology_snapshot(captured_at=2.0)
        self.assertEqual(snapshot.generation, 3)
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

        manager.update_services(["unrelated.service"], captured_at=3.0)
        self.assertEqual(
            manager.topology_snapshot(captured_at=3.0).sources,
            (
                EnergySourceDescriptor("grid-primary", "grid", "offline", ("power",)),
                EnergySourceDescriptor(_source_id("pv-ac", AC_PV_A), "pv_ac", "offline", ("power",)),
                EnergySourceDescriptor(_source_id("pv-dc", GRID_SERVICE), "pv_dc", "offline", ("power",)),
            ),
        )

    def test_source_identity_queries_preserve_semantic_kinds(self) -> None:
        manager = DbusEnergyDiscoveryManager(_specs())
        manager.update_services([GRID_SERVICE, AC_PV_A, BATTERY_A], captured_at=1.0)
        manager.record_pv_value(AC_PV_A, "/Ac/Power", 100.0)
        manager.record_pv_value(GRID_SERVICE, "/Dc/Pv/Power", 0.0)

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
        manager.update_services([AC_PV_A, BATTERY_A, GRID_SERVICE], captured_at=1.0)

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
        with self.assertRaisesRegex(TypeError, "use_dc_pv must be bool"):
            read_spec_from_mapping(
                {
                    "dc_service": GRID_SERVICE,
                    "dc_path": "/Dc/Pv/Power",
                    "use_dc_pv": 1,
                }
            )
        missing_dc_service = read_spec_from_mapping(
            {"dc_path": "/Dc/Pv/Power", "use_dc_pv": True}
        )
        missing_dc_path = read_spec_from_mapping(
            {"dc_service": GRID_SERVICE, "use_dc_pv": True}
        )

        for pv_spec in (missing_dc_service, missing_dc_path):
            with self.subTest(pv_spec=pv_spec):
                manager = DbusEnergyDiscoveryManager({"pv_power_w": pv_spec})
                self.assertEqual(manager.topology_snapshot(captured_at=1.0).sources, ())
                self.assertEqual(manager.introspection_targets(), [])

        with self.assertRaisesRegex(TypeError, "service must be str"):
            read_spec_from_mapping({"service": 7, "prefix": "source."})

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
            manager.topology_snapshot(captured_at=1.0).sources,
            (
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

        manager.record_pv_value(AC_PV_A, "/Ac/Power", 100.0)
        manager.record_pv_error(AC_PV_A, "/Ac/Power", RuntimeError("NoReply"))

        self.assertEqual(manager.generation, 0)
        self.assertEqual(manager.dormant_source_ids(), ())
        self.assertEqual(manager.topology_snapshot(captured_at=0.5).sources, ())
        self.assertEqual(manager.introspection_targets(), [])

    def test_pv_observations_match_only_configured_ac_and_dc_targets(self) -> None:
        specs = _specs()
        manager = DbusEnergyDiscoveryManager(specs)
        manager.update_services([AC_PV_A, GRID_SERVICE], captured_at=1.0)
        initial_generation = manager.generation
        self.assertEqual(
            manager.pv_members(specs["pv_power_w"]),
            [
                (AC_PV_A, "/Ac/Power"),
                (GRID_SERVICE, "/Dc/Pv/Power"),
            ],
        )
        manager.record_pv_value(AC_PV_A, "/Ac/Power", None)
        self.assertEqual(manager.generation, initial_generation)

        for service, path in (
            ("unrelated.service", "/Ac/Power"),
            (AC_PV_A, "/wrong"),
            (GRID_SERVICE, "/wrong"),
        ):
            with self.subTest(service=service, path=path):
                manager.record_pv_error(service, path, "inverter sleeping")
                manager.record_pv_value(service, path, 0.0)

        self.assertEqual(manager.generation, initial_generation)
        self.assertEqual(manager.dormant_source_ids(), ())

        manager.record_pv_error(AC_PV_A, "/Ac/Power", "inverter sleeping")
        ac_source_id = _source_id("pv-ac", AC_PV_A)
        self.assertEqual(manager.generation, initial_generation + 1)
        self.assertEqual(manager.dormant_source_ids(), (ac_source_id,))
        self.assertEqual(
            manager.source_unavailability_reasons(),
            {ac_source_id: "pv-sleep-confirmed"},
        )

        manager.record_pv_value(GRID_SERVICE, "/Dc/Pv/Power", 0.0)
        dc_source_id = _source_id("pv-dc", GRID_SERVICE)
        self.assertEqual(manager.generation, initial_generation + 2)
        self.assertEqual(
            tuple(item.source_id for item in manager.dormant_evidence()),
            (ac_source_id,),
        )

        manager.record_pv_error(GRID_SERVICE, "/Dc/Pv/Power", "NoReply")
        self.assertEqual(manager.generation, initial_generation + 3)
        self.assertEqual(
            tuple(item.source_id for item in manager.dormant_evidence()),
            (ac_source_id,),
        )
        manager.record_pv_error(GRID_SERVICE, "/Dc/Pv/Power", "inverter standby")
        self.assertEqual(manager.generation, initial_generation + 4)
        self.assertEqual(manager.dormant_source_ids(), (ac_source_id, dc_source_id))

    def test_member_backoff_uses_each_opaque_source_identity(self) -> None:
        clock = _Clock()
        specs = _specs()
        manager = DbusEnergyDiscoveryManager(
            specs,
            monotonic=clock.monotonic,
            wall_clock=clock.wall_clock,
        )
        manager.update_services([AC_PV_A, GRID_SERVICE], captured_at=1.0)
        spec = specs["pv_power_w"]

        manager.record_pv_error(AC_PV_A, "/Ac/Power", "NoReply")
        self.assertEqual(manager.pv_members(spec), [(GRID_SERVICE, "/Dc/Pv/Power")])
        clock.monotonic_at = 299.999
        self.assertEqual(manager.pv_members(spec), [(GRID_SERVICE, "/Dc/Pv/Power")])
        clock.monotonic_at = 300.0
        self.assertEqual(
            manager.pv_members(spec),
            [
                (AC_PV_A, "/Ac/Power"),
                (GRID_SERVICE, "/Dc/Pv/Power"),
            ],
        )

    def test_registry_capacity_and_initial_revision_are_exact(self) -> None:
        clock = _Clock()
        registry = PvSourceRegistry(
            _specs()["pv_power_w"],
            max_observations=1,
            monotonic=clock.monotonic,
            wall_clock=clock.wall_clock,
        )
        advertising = frozenset({AC_PV_A, AC_PV_B})
        self.assertEqual(registry.revision, 0)

        registry.record_value(
            AC_PV_A,
            "/Ac/Power",
            10.0,
            advertising_services=advertising,
        )
        self.assertEqual(registry.revision, 1)
        clock.monotonic_at = 1.0
        registry.record_error(
            AC_PV_B,
            "/Ac/Power",
            "inverter sleeping",
            advertising_services=advertising,
        )
        self.assertEqual(registry.observation_count, 1)
        self.assertEqual(
            tuple(source.source_id for source in registry.descriptors(
                advertising,
                dormant_source_ids=frozenset(),
            )),
            (_source_id("pv-ac", AC_PV_B),),
        )

    def test_registry_capacity_protects_current_members_for_values_and_errors(self) -> None:
        for error in (None, "inverter sleeping"):
            with self.subTest(error=error):
                clock = _Clock()
                registry = PvSourceRegistry(
                    _specs()["pv_power_w"],
                    max_observations=2,
                    monotonic=clock.monotonic,
                    wall_clock=clock.wall_clock,
                )
                first_members = frozenset({AC_PV_A, "com.victronenergy.pvinverter.c"})
                registry.record_value(
                    AC_PV_A,
                    "/Ac/Power",
                    10.0,
                    advertising_services=first_members,
                )
                clock.monotonic_at = 1.0
                registry.record_value(
                    "com.victronenergy.pvinverter.c",
                    "/Ac/Power",
                    20.0,
                    advertising_services=first_members,
                )

                current_members = frozenset({AC_PV_A, AC_PV_B})
                clock.monotonic_at = 2.0
                if error is None:
                    registry.record_value(
                        AC_PV_B,
                        "/Ac/Power",
                        30.0,
                        advertising_services=current_members,
                    )
                else:
                    registry.record_error(
                        AC_PV_B,
                        "/Ac/Power",
                        error,
                        advertising_services=current_members,
                    )

                self.assertEqual(registry.observation_count, 2)
                self.assertEqual(
                    tuple(source.source_id for source in registry.descriptors(
                        current_members,
                        dormant_source_ids=frozenset(),
                    )),
                    (
                        _source_id("pv-ac", AC_PV_A),
                        _source_id("pv-ac", AC_PV_B),
                    ),
                )

    def test_explicit_ac_service_does_not_accept_other_prefix_members(self) -> None:
        specs = _specs()
        specs["pv_power_w"]["service"] = AC_PV_A
        manager = DbusEnergyDiscoveryManager(specs)
        manager.update_services([AC_PV_A, AC_PV_B], captured_at=1.0)
        initial_generation = manager.generation

        manager.record_pv_error(AC_PV_B, "/Ac/Power", "inverter sleeping")
        self.assertEqual(manager.generation, initial_generation)
        self.assertEqual(manager.dormant_source_ids(), ())

        manager.record_pv_error(AC_PV_A, "/Ac/Power", "inverter sleeping")
        self.assertEqual(manager.generation, initial_generation + 1)
        self.assertEqual(
            manager.dormant_source_ids(),
            (_source_id("pv-ac", AC_PV_A),),
        )

    def test_pv_observation_state_overrides_advertising_but_not_missing_service(self) -> None:
        manager = DbusEnergyDiscoveryManager(_specs())
        manager.update_services([AC_PV_A, GRID_SERVICE], captured_at=1.0)
        ac_source_id = _source_id("pv-ac", AC_PV_A)
        dc_source_id = _source_id("pv-dc", GRID_SERVICE)
        manager.record_pv_value(AC_PV_A, "/Ac/Power", 100.0)
        manager.record_pv_value(GRID_SERVICE, "/Dc/Pv/Power", 50.0)
        stable_generation = manager.generation
        manager.record_pv_value(AC_PV_A, "/Ac/Power", 100.0)
        self.assertEqual(manager.generation, stable_generation)

        states = {source.source_id: source.state for source in manager.topology_snapshot(captured_at=1.0).sources}
        self.assertEqual(states[ac_source_id], "online")
        self.assertEqual(states[dc_source_id], "online")
        self.assertEqual(manager.source_unavailability_reasons(), {})

        manager.record_pv_error(AC_PV_A, "/Ac/Power", RuntimeError("NoReply"))
        manager.record_pv_error(GRID_SERVICE, "/Dc/Pv/Power", RuntimeError("offline"))
        failed_generation = manager.generation
        states = {source.source_id: source.state for source in manager.topology_snapshot(captured_at=2.0).sources}
        self.assertEqual(states[ac_source_id], "offline")
        self.assertEqual(states[dc_source_id], "offline")
        self.assertEqual(manager.dormant_source_ids(), ())
        self.assertEqual(
            manager.source_unavailability_reasons(),
            {
                ac_source_id: "source-path-unreadable",
                dc_source_id: "source-path-unreadable",
            },
        )

        manager.record_pv_value(AC_PV_A, "/Ac/Power", 100.0)
        manager.record_pv_value(GRID_SERVICE, "/Dc/Pv/Power", 50.0)
        self.assertEqual(manager.generation, failed_generation + 2)
        states = {source.source_id: source.state for source in manager.topology_snapshot(captured_at=3.0).sources}
        self.assertEqual(states[ac_source_id], "online")
        self.assertEqual(states[dc_source_id], "online")

        manager.update_services([AC_PV_A], captured_at=4.0)
        states = {source.source_id: source.state for source in manager.topology_snapshot(captured_at=4.0).sources}
        self.assertEqual(states[ac_source_id], "online")
        self.assertEqual(states[dc_source_id], "offline")
        self.assertEqual(
            manager.source_unavailability_reasons(),
            {dc_source_id: "source-not-advertising"},
        )

    def test_dormancy_expiry_advances_generation_once(self) -> None:
        specs = _specs()
        specs["pv_power_w"]["service"] = AC_PV_A
        clock = _Clock(monotonic_at=2.0, wall_clock_at=1002.0)
        manager = DbusEnergyDiscoveryManager(
            specs,
            monotonic=clock.monotonic,
            wall_clock=clock.wall_clock,
        )
        manager.update_services([AC_PV_A], captured_at=1002.0)
        manager.record_pv_error(AC_PV_A, "/Ac/Power", "inverter standby")
        source_id = _source_id("pv-ac", AC_PV_A)
        self.assertEqual(manager.dormant_evidence()[0].observed_at, 1002.0)

        generation_with_evidence = manager.generation
        clock.monotonic_at = 64801.99
        self.assertEqual(manager.dormant_source_ids(), (source_id,))
        self.assertEqual(manager.generation, generation_with_evidence)
        clock.monotonic_at = 64802.0
        self.assertEqual(manager.dormant_source_ids(), ())
        self.assertEqual(manager.generation, generation_with_evidence + 1)
        clock.monotonic_at = 70000.0
        self.assertEqual(manager.dormant_source_ids(), ())
        self.assertEqual(manager.generation, generation_with_evidence + 1)

    def test_unadvertised_observations_cannot_create_phantom_sources(self) -> None:
        manager = DbusEnergyDiscoveryManager(_specs())

        manager.record_pv_value(GRID_SERVICE, "/Dc/Pv/Power", 42.0)
        manager.record_pv_error(AC_PV_A, "/Ac/Power", "inverter sleeping")

        self.assertEqual(manager.source_ids("pv_ac"), ())
        self.assertEqual(manager.source_ids("pv_dc"), ())
        self.assertTrue(manager.needs_early_pv_rescan())

    def test_early_rescan_requires_every_validated_source_to_be_absent(self) -> None:
        manager = DbusEnergyDiscoveryManager(_specs())
        manager.update_services([AC_PV_A, GRID_SERVICE], captured_at=1.0)
        manager.record_pv_value(AC_PV_A, "/Ac/Power", 100.0)
        manager.record_pv_value(GRID_SERVICE, "/Dc/Pv/Power", 50.0)
        self.assertFalse(manager.needs_early_pv_rescan())

        manager.update_services([], captured_at=2.0)
        self.assertTrue(manager.needs_early_pv_rescan())

    def test_inactive_validated_source_is_pruned_after_retention(self) -> None:
        clock = _Clock(monotonic_at=1.0, wall_clock_at=1001.0)
        manager = DbusEnergyDiscoveryManager(
            _specs(),
            max_pv_observations=1,
            monotonic=clock.monotonic,
            wall_clock=clock.wall_clock,
        )
        manager.update_services([AC_PV_A, AC_PV_B], captured_at=1001.0)
        manager.record_pv_value(AC_PV_A, "/Ac/Power", 100.0)
        clock.monotonic_at = 2.0
        manager.record_pv_value(AC_PV_B, "/Ac/Power", 200.0)
        self.assertEqual(manager.pv_observation_count, 1)
        self.assertEqual(manager.source_ids("pv_ac"), (_source_id("pv-ac", AC_PV_B),))

        clock.monotonic_at = 86402.0
        manager.update_services([], captured_at=87401.0)

        self.assertEqual(manager.pv_observation_count, 0)
        self.assertEqual(manager.source_ids("pv_ac"), ())


if __name__ == "__main__":
    unittest.main()

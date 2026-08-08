# SPDX-License-Identifier: GPL-3.0-or-later
"""Contracts for adapter-owned AC/DC PV dormancy evidence."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import venus_evcharger.dbus_adapter.read.pv_dormancy as pv_dormancy
from tests.dbus_adapter_venus_stubs import install_venus_adapter_stubs
from tests.support.async_dbus import install_read_responder

install_venus_adapter_stubs()

from venus_evcharger.dbus_adapter.process.adapter import DbusAdapter
from venus_evcharger.dbus_adapter.read.discovery import DbusEnergyDiscoveryManager
from venus_evcharger.dbus_adapter.read.pv_dormancy import (
    PvDormancyPolicy,
    PvDormancyTracker,
    explicit_dormancy_error,
)
from venus_evcharger.dbus_adapter.read.spec import ReadSpecs
from venus_evcharger.dbus_gateway import gateway_paths

_AC_SERVICE = "com.victronenergy.pvinverter.test"
_SYSTEM_SERVICE = "com.victronenergy.system"
_AC_PATH = "/Ac/Power"
_DC_PATH = "/Dc/Pv/Power"


def _specs(*, use_ac: bool = True, use_dc: bool = True) -> ReadSpecs:
    return {
        "pv_power_w": {
            "prefix": "com.victronenergy.pvinverter.",
            "path": _AC_PATH if use_ac else "",
            "dc_service": _SYSTEM_SERVICE,
            "dc_path": _DC_PATH,
            "use_dc_pv": use_dc,
            "aggregate": "pv-total",
        }
    }


class _Clock:
    def __init__(self, monotonic_at: float = 0.0, wall_clock_at: float = 0.0) -> None:
        self.monotonic_at = monotonic_at
        self.wall_clock_at = wall_clock_at

    def monotonic(self) -> float:
        return self.monotonic_at

    def wall_clock(self) -> float:
        return self.wall_clock_at


class PvDormancyTrackerContractsTests(unittest.TestCase):
    def test_only_explicit_sleep_language_is_immediate_evidence(self) -> None:
        for message in (
            "device sleeping",
            "PV is asleep",
            "source dormant",
            "inverter standby",
        ):
            with self.subTest(message=message):
                self.assertTrue(explicit_dormancy_error(message))

        for message in (
            "org.freedesktop.DBus.Error.NoReply",
            "offline",
            "connection timed out",
            "not sleeping",
            "not asleep",
            "not dormant",
            "not in standby",
            "",
        ):
            with self.subTest(message=message):
                self.assertFalse(explicit_dormancy_error(message))

    def test_successful_zero_is_available_and_cannot_become_sleep_evidence(self) -> None:
        clock = _Clock()
        tracker = PvDormancyTracker(
            monotonic=clock.monotonic,
            wall_clock=clock.wall_clock,
        )
        self.assertEqual(tracker.revision, 0)
        self.assertIsNone(tracker._evidence_cache_key)
        self.assertEqual(tracker._evidence_cache, ())
        self.assertTrue(tracker.record_value("pv", 0.0))
        self.assertEqual(tracker.revision, 1)
        self.assertFalse(tracker.record_value("pv", 0.0))
        self.assertEqual(tracker.revision, 1)
        self.assertEqual(tracker.evidence(frozenset({"pv"})), ())
        self.assertTrue(tracker.probe_allowed("pv"))

        clock.monotonic_at = 3.0
        self.assertTrue(tracker.record_error("pv", "NoReply"))
        self.assertEqual(tracker.evidence(frozenset({"pv"})), ())
        self.assertIs(tracker.source_available("pv"), False)
        self.assertEqual(tracker.source_failure_reason("pv"), "source-path-unreadable")
        self.assertFalse(tracker.probe_allowed("pv"))

        clock.monotonic_at = 303.0
        self.assertTrue(tracker.probe_allowed("pv"))
        self.assertTrue(tracker.record_value("pv", 20.0))
        self.assertEqual(tracker.evidence(frozenset({"pv"})), ())
        self.assertIs(tracker.source_available("pv"), True)
        self.assertIsNone(tracker.source_failure_reason("pv"))

    def test_generic_transport_error_is_not_dormancy_and_explicit_evidence_expires(self) -> None:
        clock = _Clock(monotonic_at=1.0, wall_clock_at=101.0)
        tracker = PvDormancyTracker(
            policy=PvDormancyPolicy(evidence_ttl_seconds=10.0),
            monotonic=clock.monotonic,
            wall_clock=clock.wall_clock,
        )

        self.assertTrue(tracker.record_error("pv", RuntimeError("NoReply")))
        self.assertEqual(tracker.evidence(frozenset({"pv"})), ())
        self.assertIs(tracker.source_available("pv"), False)

        clock.monotonic_at = 2.0
        clock.wall_clock_at = 102.0
        self.assertTrue(tracker.record_error("pv", RuntimeError("device dormant")))
        evidence = tracker.evidence(frozenset({"pv"}))
        self.assertEqual(evidence[0].reason, "explicit-dormant-state")
        self.assertEqual(evidence[0].observed_at, 102.0)
        self.assertIsNone(tracker.source_failure_reason("pv"))

        clock.monotonic_at = 11.999
        self.assertFalse(tracker.record_error("pv", RuntimeError("NoReply")))
        self.assertFalse(tracker.maintain(frozenset({"pv"})))
        self.assertEqual(
            tracker.evidence(frozenset({"pv"}))[0].reason,
            "explicit-dormant-state",
        )

        clock.monotonic_at = 12.0
        self.assertTrue(tracker.maintain(frozenset({"pv"})))
        self.assertEqual(tracker.evidence(frozenset({"pv"})), ())
        self.assertEqual(tracker.source_failure_reason("pv"), "source-path-unreadable")

    def test_non_numeric_samples_cannot_create_or_clear_evidence(self) -> None:
        tracker = PvDormancyTracker()

        self.assertFalse(tracker.record_value("pv", None))
        self.assertFalse(tracker.record_value("pv", True))
        self.assertFalse(tracker.record_value("pv", float("nan")))
        self.assertIsNone(tracker.source_available("pv"))

    def test_inactive_observations_are_pruned_and_capacity_is_bounded(self) -> None:
        clock = _Clock()
        tracker = PvDormancyTracker(
            policy=PvDormancyPolicy(
                evidence_ttl_seconds=2.0,
                observation_retention_seconds=3.0,
                max_observations=2,
            ),
            monotonic=clock.monotonic,
            wall_clock=clock.wall_clock,
        )
        self.assertTrue(tracker.record_value("old", 1.0))
        clock.monotonic_at = 1.0
        self.assertTrue(tracker.record_value("active", 2.0))
        clock.monotonic_at = 2.0
        self.assertTrue(
            tracker.record_value(
                "new",
                3.0,
                active_source_ids=frozenset({"active", "new"}),
            )
        )
        self.assertEqual(tracker.observation_count, 2)
        self.assertFalse(tracker.source_validated("old"))
        self.assertTrue(tracker.source_validated("active"))
        self.assertTrue(tracker.source_validated("new"))

        clock.monotonic_at = 5.0
        self.assertTrue(tracker.maintain(frozenset({"new"})))
        self.assertEqual(tracker.observation_count, 1)
        self.assertFalse(tracker.source_validated("active"))

    def test_constructor_clamps_are_exact_and_keep_one_observation(self) -> None:
        clock = _Clock()
        tracker = PvDormancyTracker(
            policy=PvDormancyPolicy(
                evidence_ttl_seconds=0.0,
                observation_retention_seconds=0.0,
                max_observations=0,
                error_backoff_seconds=-1.0,
            ),
            monotonic=clock.monotonic,
            wall_clock=clock.wall_clock,
        )
        self.assertTrue(tracker.record_error("first", "inverter sleeping"))
        self.assertEqual(tracker.evidence(frozenset({"first"}))[0].observed_at, 0.0)
        self.assertTrue(tracker.probe_allowed("first"))
        self.assertIs(tracker.maintain(frozenset({"first"})), False)

        clock.monotonic_at = 0.999
        self.assertIs(tracker.maintain(frozenset({"first"})), False)
        clock.monotonic_at = 1.0
        self.assertIs(tracker.maintain(frozenset({"first"})), True)
        self.assertEqual(tracker.revision, 2)

        self.assertTrue(
            tracker.record_value(
                "second",
                1.0,
                active_source_ids=frozenset({"second"}),
            )
        )
        self.assertEqual(tracker.observation_count, 1)
        self.assertFalse(tracker.source_validated("first"))
        self.assertTrue(tracker.source_validated("second"))

    def test_error_capacity_prefers_active_sources(self) -> None:
        clock = _Clock()
        tracker = PvDormancyTracker(
            policy=PvDormancyPolicy(max_observations=2),
            monotonic=clock.monotonic,
            wall_clock=clock.wall_clock,
        )
        self.assertTrue(tracker.record_value("active", 1.0))
        clock.monotonic_at = 1.0
        self.assertTrue(tracker.record_value("inactive", 2.0))
        clock.monotonic_at = 2.0
        self.assertTrue(
            tracker.record_error(
                "new",
                "NoReply",
                active_source_ids=frozenset({"active", "new"}),
            )
        )
        self.assertTrue(tracker.source_validated("active"))
        self.assertFalse(tracker.source_validated("inactive"))
        self.assertIs(tracker.source_available("new"), False)

    def test_expiry_scans_all_observations_in_insertion_order(self) -> None:
        clock = _Clock()
        tracker = PvDormancyTracker(
            policy=PvDormancyPolicy(evidence_ttl_seconds=10.0),
            monotonic=clock.monotonic,
            wall_clock=clock.wall_clock,
        )
        self.assertTrue(tracker.record_value("plain", 1.0))
        self.assertTrue(tracker.record_error("first", "inverter sleeping"))
        self.assertTrue(tracker.record_error("second", "inverter sleeping"))
        clock.monotonic_at = 5.0
        self.assertFalse(tracker.record_error("first", "inverter sleeping"))
        clock.monotonic_at = 10.0

        self.assertIs(tracker.maintain(frozenset({"first", "second"})), True)
        self.assertEqual(
            tuple(item.source_id for item in tracker.evidence(frozenset({"first", "second"}))),
            ("first",),
        )
        self.assertEqual(tracker.source_failure_reason("second"), "source-path-unreadable")

    def test_evidence_cache_avoids_recomputing_unchanged_observations(self) -> None:
        tracker = PvDormancyTracker()
        self.assertTrue(tracker.record_error("pv", "inverter sleeping"))
        with patch.object(
            pv_dormancy,
            "_observation_evidence",
            wraps=pv_dormancy._observation_evidence,
        ) as observation_evidence:
            expected = tracker.evidence(frozenset({"pv"}))
            self.assertEqual(tracker.evidence(frozenset({"pv"})), expected)
            self.assertEqual(observation_evidence.call_count, 1)

            self.assertTrue(tracker.record_value("pv", 1.0))
            self.assertEqual(tracker.evidence(frozenset({"pv"})), ())
            self.assertEqual(observation_evidence.call_count, 2)


class PvDormancyProductionChainTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.root = Path(self.temporary_directory.name)

    def adapter(self, *, use_dc: bool, clock: _Clock) -> DbusAdapter:
        config_path = self.root / f"config-{int(use_dc)}.ini"
        config_path.write_text(
            "[DEFAULT]\n"
            f"AutoUseDcPv={int(use_dc)}\n"
            "DbusIntrospectionEnabled=0\n",
            encoding="utf-8",
        )
        adapter = DbusAdapter(
            str(config_path),
            paths=gateway_paths(str(self.root / f"run-{int(use_dc)}")),
        )
        adapter.energy_discovery = DbusEnergyDiscoveryManager(
            adapter.read_scheduler.specs,
            monotonic=clock.monotonic,
            wall_clock=clock.wall_clock,
        )
        return adapter

    def test_explicit_ac_sleep_flows_from_reader_to_health_and_diagnostics(self) -> None:
        clock = _Clock(monotonic_at=10.0, wall_clock_at=10.0)
        adapter = self.adapter(use_dc=False, clock=clock)
        adapter.energy_discovery.update_services([_AC_SERVICE], captured_at=1.0)
        spec = adapter.read_scheduler.specs["pv_power_w"]

        install_read_responder(
            adapter,
            lambda _service, _path: (_ for _ in ()).throw(
                RuntimeError("inverter sleeping")
            ),
        )
        self.assertEqual(adapter.read_executor.poll_read_spec("pv_power_w", spec), "applied")
        source_id = adapter.energy_discovery.source_ids("pv_ac")[0]

        adapter.energy_discovery.update_services([], captured_at=10.5)
        with patch("venus_evcharger.dbus_adapter.process.health.time.time", return_value=11.0):
            health = adapter.health_snapshot()
        self.assertEqual(health["dormant_energy_source_ids"], [source_id])
        self.assertEqual(
            health["dormant_energy_source_evidence"],
            [
                {
                    "source_id": source_id,
                    "reason": "explicit-dormant-state",
                    "observed_at": 10.0,
                }
            ],
        )

        snapshot = adapter.diagnostics_role.gateway_diagnostics_snapshot(
            health=health,
            topology=adapter.energy_discovery.topology_snapshot(captured_at=11.0),
            captured_at=11.0,
        )
        source = next(item for item in snapshot.discovery.sources if item.source_id == source_id)
        self.assertEqual(source.availability, "dormant")
        self.assertEqual(source.reason_code, "pv-sleep-confirmed")

    def test_ac_noreply_then_service_disappearance_is_not_retained_as_dormant(self) -> None:
        clock = _Clock(monotonic_at=10.0, wall_clock_at=10.0)
        adapter = self.adapter(use_dc=False, clock=clock)
        adapter.energy_discovery.update_services([_AC_SERVICE], captured_at=1.0)
        spec = adapter.read_scheduler.specs["pv_power_w"]

        install_read_responder(
            adapter,
            lambda _service, _path: (_ for _ in ()).throw(
                RuntimeError("org.freedesktop.DBus.Error.NoReply")
            ),
        )
        self.assertEqual(adapter.read_executor.poll_read_spec("pv_power_w", spec), "applied")

        adapter.energy_discovery.update_services([], captured_at=10.5)
        with patch("venus_evcharger.dbus_adapter.process.health.time.time", return_value=11.0):
            health = adapter.health_snapshot()
        topology = adapter.energy_discovery.topology_snapshot(captured_at=11.0)

        self.assertEqual(health["dormant_energy_source_ids"], [])
        self.assertEqual(health["dormant_energy_source_evidence"], [])
        self.assertFalse(any(source.kind == "pv_ac" for source in topology.sources))

    def test_unproven_dc_noreply_does_not_create_a_phantom_source(self) -> None:
        clock = _Clock(monotonic_at=10.0, wall_clock_at=10.0)
        adapter = self.adapter(use_dc=True, clock=clock)
        adapter.energy_discovery.update_services([_SYSTEM_SERVICE], captured_at=1.0)
        spec = adapter.read_scheduler.specs["pv_power_w"]

        install_read_responder(
            adapter,
            lambda _service, _path: (_ for _ in ()).throw(
                RuntimeError("org.freedesktop.DBus.Error.NoReply")
            ),
        )
        self.assertEqual(adapter.read_executor.poll_read_spec("pv_power_w", spec), "applied")

        with patch("venus_evcharger.dbus_adapter.process.health.time.time", return_value=10.0):
            health = adapter.health_snapshot()
        self.assertEqual(health["dormant_energy_source_ids"], [])
        self.assertEqual(health["dormant_energy_source_evidence"], [])
        self.assertEqual(adapter.energy_discovery.source_ids("pv_dc"), ())
        self.assertTrue(adapter.energy_discovery.needs_early_pv_rescan())

    def test_explicit_dc_standby_flows_to_dormant_diagnostics(self) -> None:
        clock = _Clock(monotonic_at=10.0, wall_clock_at=10.0)
        adapter = self.adapter(use_dc=True, clock=clock)
        adapter.energy_discovery.update_services([_SYSTEM_SERVICE], captured_at=1.0)
        spec = adapter.read_scheduler.specs["pv_power_w"]

        install_read_responder(
            adapter,
            lambda _service, _path: (_ for _ in ()).throw(
                RuntimeError("DC inverter standby")
            ),
        )
        self.assertEqual(adapter.read_executor.poll_read_spec("pv_power_w", spec), "applied")

        with patch("venus_evcharger.dbus_adapter.process.health.time.time", return_value=10.0):
            health = adapter.health_snapshot()
        source_id = adapter.energy_discovery.source_ids("pv_dc")[0]
        self.assertEqual(health["dormant_energy_source_ids"], [source_id])
        self.assertEqual(
            health["dormant_energy_source_evidence"],
            [
                {
                    "source_id": source_id,
                    "reason": "explicit-dormant-state",
                    "observed_at": 10.0,
                }
            ],
        )

        snapshot = adapter.diagnostics_role.gateway_diagnostics_snapshot(
            health=health,
            topology=adapter.energy_discovery.topology_snapshot(captured_at=10.0),
            captured_at=10.0,
        )
        source = next(item for item in snapshot.discovery.sources if item.source_id == source_id)
        self.assertEqual(source.availability, "dormant")
        self.assertEqual(source.reason_code, "pv-sleep-confirmed")

    def test_dc_zero_then_noreply_remains_unavailable_not_dormant(self) -> None:
        clock = _Clock(monotonic_at=9.0, wall_clock_at=9.0)
        adapter = self.adapter(use_dc=True, clock=clock)
        adapter.energy_discovery.update_services([_SYSTEM_SERVICE], captured_at=1.0)
        spec = adapter.read_scheduler.specs["pv_power_w"]

        install_read_responder(adapter, lambda _service, _path: 0.0)
        self.assertEqual(adapter.read_executor.poll_read_spec("pv_power_w", spec), "applied")

        clock.monotonic_at = 10.0
        clock.wall_clock_at = 10.0
        install_read_responder(
            adapter,
            lambda _service, _path: (_ for _ in ()).throw(
                RuntimeError("org.freedesktop.DBus.Error.NoReply")
            ),
        )
        self.assertEqual(adapter.read_executor.poll_read_spec("pv_power_w", spec), "applied")

        with patch("venus_evcharger.dbus_adapter.process.health.time.time", return_value=10.0):
            health = adapter.health_snapshot()
        source_id = adapter.energy_discovery.source_ids("pv_dc")[0]
        self.assertEqual(health["dormant_energy_source_ids"], [])
        self.assertEqual(health["dormant_energy_source_evidence"], [])

        snapshot = adapter.diagnostics_role.gateway_diagnostics_snapshot(
            health=health,
            topology=adapter.energy_discovery.topology_snapshot(captured_at=10.0),
            captured_at=10.0,
        )
        source = next(item for item in snapshot.discovery.sources if item.source_id == source_id)
        self.assertEqual(source.availability, "unavailable")
        self.assertEqual(source.reason_code, "source-path-unreadable")


if __name__ == "__main__":
    unittest.main()

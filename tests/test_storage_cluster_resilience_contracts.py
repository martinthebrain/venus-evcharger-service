# SPDX-License-Identifier: GPL-3.0-or-later
"""Transactional resilience contracts for storage-cluster snapshots."""

from __future__ import annotations

import unittest
from collections.abc import Mapping
from typing import cast
from unittest.mock import patch

from tests.support.dbus_inputs import (
    DbusInputServiceFake,
    EnergyServiceResolverFake,
    GatewayReaderFake,
    SourceHealthFake,
)
from venus_evcharger.energy import (
    EnergyClusterSnapshot,
    EnergyLearningProfile,
    EnergySourceDefinition,
    EnergySourceSnapshot,
)
from venus_evcharger.inputs.storage import StorageInputReader
from venus_evcharger.ports.dbus import DbusInputPort


class _EnergyServiceResolver(EnergyServiceResolverFake):
    def invalidate_energy_source_service(
        self,
        source_id: str,
        *,
        expected_service: str | None = None,
    ) -> bool:
        del source_id, expected_service
        return True


class _PrimaryFailoverResolver(_EnergyServiceResolver):
    def __init__(
        self,
        primary: EnergySourceDefinition,
        failed_service: str,
        replacement_service: str,
    ) -> None:
        super().__init__(primary, {primary.source_id: failed_service})
        self._replacement_service = replacement_service

    def invalidate_auto_battery_service(self) -> None:
        super().invalidate_auto_battery_service()
        self.services[self.primary.source_id] = self._replacement_service


def _reader(
    service: DbusInputServiceFake,
    gateway: GatewayReaderFake,
    primary: EnergySourceDefinition,
    services: dict[str, str],
) -> tuple[StorageInputReader, SourceHealthFake]:
    health = SourceHealthFake()
    resolver = _EnergyServiceResolver(primary, services)
    return StorageInputReader(DbusInputPort(service), gateway, health, resolver), health


class StorageClusterResilienceContractTests(unittest.TestCase):
    def test_empty_primary_read_is_invalidated_and_retried_once_with_replacement(self) -> None:
        primary = EnergySourceDefinition("primary", service_prefix="battery.")
        service = DbusInputServiceFake(auto_energy_sources=(primary,))
        gateway = GatewayReaderFake(
            raw_results={
                ("battery.stale", "/Soc"): [None],
                ("battery.replacement", "/Soc"): [63.0],
            }
        )
        resolver = _PrimaryFailoverResolver(
            primary,
            "battery.stale",
            "battery.replacement",
        )
        health = SourceHealthFake()
        reader = StorageInputReader(DbusInputPort(service), gateway, health, resolver)

        with patch("venus_evcharger.inputs.storage.time.time", return_value=90.0):
            payload = reader.get_battery_snapshot()

        self.assertEqual(payload["battery_soc"], 63.0)
        self.assertEqual(resolver.invalidations, 1)
        self.assertEqual(
            gateway.raw_reads,
            [("battery.stale", "/Soc"), ("battery.replacement", "/Soc")],
        )
        sources = cast(list[Mapping[str, object]], payload["battery_sources"])
        self.assertEqual(sources[0]["service_name"], "battery.replacement")
        self.assertIs(sources[0]["online"], True)
        self.assertEqual(health.recoveries, [("battery", "Battery SOC readings recovered", ())])
        self.assertEqual(health.failures, [])

    def test_empty_primary_retry_is_bounded_and_keeps_failed_service_identity(self) -> None:
        primary = EnergySourceDefinition(
            "primary",
            role="battery",
            service_prefix="battery.",
            usable_capacity_wh=9_500.0,
            battery_chemistry="nmc",
        )
        service = DbusInputServiceFake(auto_energy_sources=(primary,))
        gateway = GatewayReaderFake(raw_results={("battery.unresponsive", "/Soc"): [None]})
        resolver = _EnergyServiceResolver(primary, {"primary": "battery.unresponsive"})
        reader = StorageInputReader(DbusInputPort(service), gateway, SourceHealthFake(), resolver)

        snapshot = reader._dbus_energy_source_snapshot(primary, 90.5)

        self.assertEqual(
            snapshot,
            EnergySourceSnapshot(
                source_id="primary",
                role="battery",
                service_name="battery.unresponsive",
                usable_capacity_wh=9_500.0,
                battery_chemistry="nmc",
                online=False,
                confidence=0.0,
                captured_at=90.5,
            ),
        )
        self.assertEqual(resolver.invalidations, 1)
        self.assertEqual(
            gateway.raw_reads,
            [("battery.unresponsive", "/Soc"), ("battery.unresponsive", "/Soc")],
        )

    def test_snapshot_source_failure_preserves_complete_offline_definition(self) -> None:
        source = EnergySourceDefinition(
            "primary",
            role="hybrid-inverter",
            service_name="battery.configured",
            service_prefix="battery.prefix.",
            usable_capacity_wh=8_400.0,
            battery_chemistry="nmc",
        )
        service = DbusInputServiceFake(auto_energy_sources=(source,))
        reader, _ = _reader(
            service,
            GatewayReaderFake(),
            source,
            {"primary": "battery.configured"},
        )

        with patch(
            "venus_evcharger.inputs.storage.read_energy_source_snapshot",
            side_effect=OSError("snapshot transport failed"),
        ):
            snapshot = reader._battery_snapshot_source(source, 92.0)

        self.assertEqual(
            snapshot,
            EnergySourceSnapshot(
                source_id="primary",
                role="hybrid-inverter",
                service_name="battery.configured",
                usable_capacity_wh=8_400.0,
                battery_chemistry="nmc",
                online=False,
                confidence=0.0,
                captured_at=92.0,
            ),
        )
        explicit = reader._offline_energy_source_snapshot(source, 93.0, "battery.failed")
        self.assertEqual(explicit.service_name, "battery.failed")
        self.assertEqual(explicit.usable_capacity_wh, 8_400.0)
        self.assertEqual(explicit.battery_chemistry, "nmc")
        self.assertIs(explicit.online, False)
        self.assertEqual(explicit.confidence, 0.0)
        self.assertEqual(explicit.captured_at, 93.0)

    def test_payload_uses_stored_learning_profiles_when_no_update_is_supplied(self) -> None:
        primary = EnergySourceDefinition("primary", service_name="battery.primary")
        stored_profile = EnergyLearningProfile(source_id="primary", sample_count=7)
        service = DbusInputServiceFake(
            auto_energy_sources=(primary,),
            _last_energy_learning_profiles={"primary": stored_profile},
        )
        reader, _ = _reader(
            service,
            GatewayReaderFake(),
            primary,
            {"primary": "battery.primary"},
        )
        forecast: dict[str, object] = {
            "battery_headroom_charge_w": None,
            "battery_headroom_discharge_w": None,
            "expected_near_term_export_w": None,
            "expected_near_term_import_w": None,
        }

        payload = reader._battery_snapshot_payload(
            None,
            EnergyClusterSnapshot(),
            forecast,
            {},
            {},
            [],
        )

        self.assertEqual(
            payload["battery_learning_profiles"],
            {"primary": stored_profile.as_dict()},
        )

    def test_primary_only_soc_policy_rejects_secondary_only_soc_without_recovery(self) -> None:
        primary = EnergySourceDefinition("primary", role="battery", service_name="battery.primary")
        secondary = EnergySourceDefinition(
            "secondary",
            role="hybrid-inverter",
            service_name="hybrid.secondary",
        )
        service = DbusInputServiceFake(
            auto_energy_sources=(primary, secondary),
            auto_use_combined_battery_soc=False,
            _last_energy_cluster={"battery_soc": 77.0, "stale": True},
        )
        gateway = GatewayReaderFake(
            raw_results={
                ("battery.primary", "/Soc"): [None],
                ("hybrid.secondary", "/Soc"): [58.0],
            }
        )
        reader, health = _reader(
            service,
            gateway,
            primary,
            {"primary": "battery.primary", "secondary": "hybrid.secondary"},
        )

        with patch("venus_evcharger.inputs.storage.time.time", return_value=91.0):
            payload = reader.get_battery_snapshot()

        self.assertEqual(payload, StorageInputReader._empty_battery_snapshot_payload(None))
        self.assertEqual(service._last_energy_cluster, payload)
        self.assertNotIn("stale", service._last_energy_cluster)
        self.assertEqual(
            gateway.raw_reads,
            [
                ("battery.primary", "/Soc"),
                ("battery.primary", "/Soc"),
                ("hybrid.secondary", "/Soc"),
            ],
        )
        self.assertEqual(health.recoveries, [])
        self.assertEqual(len(health.failures), 1)
        self.assertIsInstance(health.failures[0][5][2], TypeError)
        self.assertEqual(str(health.failures[0][5][2]), "Battery SOC is not numeric")

    def test_offline_secondary_source_does_not_discard_healthy_sources(self) -> None:
        primary = EnergySourceDefinition(
            "primary",
            role="battery",
            service_name="battery.primary",
            usable_capacity_wh=10_000.0,
        )
        secondary = EnergySourceDefinition(
            "secondary",
            role="hybrid-inverter",
            service_name="hybrid.secondary",
            usable_capacity_wh=8_000.0,
        )
        service = DbusInputServiceFake(auto_energy_sources=(primary, secondary))
        gateway = GatewayReaderFake(
            raw_results={
                ("battery.primary", "/Soc"): [64.0],
                ("hybrid.secondary", "/Soc"): [OSError("secondary offline")],
            }
        )
        reader, health = _reader(
            service,
            gateway,
            primary,
            {"primary": "battery.primary", "secondary": "hybrid.secondary"},
        )

        with patch("venus_evcharger.inputs.storage.time.time", return_value=100.0):
            payload = reader.get_battery_snapshot()

        self.assertEqual(payload["battery_soc"], 64.0)
        self.assertEqual(payload["battery_source_count"], 2)
        self.assertEqual(payload["battery_hybrid_inverter_source_count"], 1)
        self.assertEqual(payload["battery_battery_source_count"], 1)
        self.assertEqual(payload["battery_online_source_count"], 1)
        self.assertEqual(payload["battery_valid_soc_source_count"], 1)
        sources = cast(list[Mapping[str, object]], payload["battery_sources"])
        self.assertEqual([source["source_id"] for source in sources], ["primary", "secondary"])
        self.assertEqual(sources[0]["soc"], 64.0)
        self.assertIs(sources[0]["online"], True)
        self.assertIsNone(sources[1]["soc"])
        self.assertIsNone(sources[1]["net_battery_power_w"])
        self.assertIsNone(sources[1]["ac_power_w"])
        self.assertIsNone(sources[1]["pv_input_power_w"])
        self.assertIsNone(sources[1]["grid_interaction_w"])
        self.assertIs(sources[1]["online"], False)
        self.assertEqual(sources[1]["confidence"], 0.0)
        self.assertEqual(sources[1]["captured_at"], 100.0)
        self.assertEqual(service._last_energy_cluster, payload)
        self.assertEqual(health.recoveries, [("battery", "Battery SOC readings recovered", ())])
        self.assertEqual(health.failures, [])

    def test_failure_and_retry_publish_the_same_complete_empty_schema(self) -> None:
        primary = EnergySourceDefinition("primary", service_name="battery.primary")
        service = DbusInputServiceFake(
            auto_energy_sources=(primary,),
            _last_energy_cluster={"battery_soc": 71.0, "stale": True},
        )
        reader, health = _reader(service, GatewayReaderFake(), primary, {"primary": "battery.primary"})
        expected = StorageInputReader._empty_battery_snapshot_payload(None)

        with (
            patch("venus_evcharger.inputs.storage.time.time", return_value=200.0),
            patch.object(
                reader,
                "_successful_battery_snapshot_payload",
                side_effect=ValueError("late snapshot failure"),
            ),
        ):
            failed_payload = reader.get_battery_snapshot()

        self.assertEqual(failed_payload, expected)
        self.assertEqual(service._last_energy_cluster, expected)
        self.assertNotIn("stale", service._last_energy_cluster)
        self.assertEqual(len(failed_payload), 33)
        self.assertEqual(failed_payload["battery_sources"], [])
        self.assertEqual(failed_payload["battery_learning_profiles"], {})
        self.assertEqual(len(health.failures), 1)

        service._last_energy_cluster = {"battery_soc": 72.0, "stale": True}
        health.ready["battery"] = False
        with patch("venus_evcharger.inputs.storage.time.time", return_value=201.0):
            retry_payload = reader.get_battery_snapshot()

        self.assertEqual(retry_payload, failed_payload)
        self.assertEqual(service._last_energy_cluster, expected)
        self.assertNotIn("stale", service._last_energy_cluster)
        self.assertEqual(len(health.failures), 1)

    def test_late_payload_failure_does_not_partially_commit_learning_or_recovery(self) -> None:
        primary = EnergySourceDefinition("primary", service_name="battery.primary")
        source_snapshot = EnergySourceSnapshot(
            "primary",
            "battery",
            "battery.primary",
            soc=55.0,
            online=True,
            confidence=1.0,
            captured_at=300.0,
        )
        cluster = EnergyClusterSnapshot(effective_soc=55.0, sources=(source_snapshot,))
        old_profile = EnergyLearningProfile(source_id="primary", sample_count=7)
        old_profiles = {"primary": old_profile}
        service = DbusInputServiceFake(
            auto_energy_sources=(primary,),
            _last_energy_learning_profiles=old_profiles,
            _last_energy_cluster={"battery_soc": 54.0},
        )
        reader, health = _reader(service, GatewayReaderFake(), primary, {"primary": "battery.primary"})

        with (
            patch("venus_evcharger.inputs.storage.time.time", return_value=300.0),
            patch.object(reader, "_battery_snapshot_cluster", return_value=(cluster, (primary,))),
            patch.object(reader, "_battery_snapshot_payload", side_effect=ValueError("payload failed")),
        ):
            payload = reader.get_battery_snapshot()

        self.assertEqual(payload, StorageInputReader._empty_battery_snapshot_payload(None))
        self.assertEqual(service._last_energy_learning_profiles, old_profiles)
        self.assertEqual(service._last_energy_cluster, payload)
        self.assertEqual(health.recoveries, [])
        self.assertEqual(len(health.failures), 1)


if __name__ == "__main__":
    unittest.main()

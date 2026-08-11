# SPDX-License-Identifier: GPL-3.0-or-later
"""Complete behavioral contracts for semantic bootstrap and companion publication."""

from __future__ import annotations

import platform
import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch

from tests.gateway_diagnostics_fixtures import gateway_diagnostics_snapshot
from venus_evcharger.auto.policy import AutoPolicy
from venus_evcharger.auto.policy_settings import auto_policy_control_values
from venus_evcharger.bootstrap.publication import EvcsPublicationOwner, accepted_publication_fields
from venus_evcharger.companion.grid_projection import (
    GridProjection,
    GridProjectionConfig,
    GridProjector,
    aggregate_grid_input,
)
from venus_evcharger.companion.publication import EnergyCompanionPublisher
from venus_evcharger.ports.gateway_publication import (
    CompanionServiceIdentity,
    EvcsServiceIdentity,
    PublicationPriority,
    PublicationReceipt,
)
from venus_evcharger.ports.gateway_diagnostic_health import GatewayPublicationSummary
from venus_evcharger.ports.gateway_diagnostics import (
    GatewayDiagnosticsSnapshot,
    GatewayDiagnosticsUnavailable,
)


class _PublicationRecorder:
    def __init__(self) -> None:
        self.accept_registration = True
        self.accept_publication = True
        self.evcs_registrations: list[tuple[EvcsServiceIdentity, dict[str, object]]] = []
        self.companion_registrations: list[tuple[CompanionServiceIdentity, dict[str, object]]] = []
        self.companion_publications: list[tuple[str, dict[str, object], PublicationPriority]] = []

    def register_evcs(
        self,
        identity: EvcsServiceIdentity,
        initial_fields: dict[str, object],
    ) -> PublicationReceipt:
        self.evcs_registrations.append((identity, dict(initial_fields)))
        return PublicationReceipt(self.accept_registration, "evcs")

    def publish_evcs_fields(
        self,
        fields: dict[str, object],
        *,
        priority: PublicationPriority,
    ) -> PublicationReceipt:
        del fields, priority
        return PublicationReceipt(self.accept_publication, "evcs-fields")

    def register_companion(
        self,
        identity: CompanionServiceIdentity,
        initial_fields: dict[str, object],
    ) -> PublicationReceipt:
        self.companion_registrations.append((identity, dict(initial_fields)))
        return PublicationReceipt(self.accept_registration, identity.service_id)

    def publish_companion_fields(
        self,
        service_id: str,
        fields: dict[str, object],
        *,
        priority: PublicationPriority,
    ) -> PublicationReceipt:
        self.companion_publications.append((service_id, dict(fields), priority))
        return PublicationReceipt(self.accept_publication, service_id)


class _Runtime:
    def __init__(self, snapshot: object) -> None:
        self.snapshot = snapshot

    def worker_snapshot(self) -> object:
        return self.snapshot


class _DiagnosticsReader:
    def __init__(
        self,
        snapshot: GatewayDiagnosticsSnapshot | None = None,
        *,
        unavailable: bool = False,
    ) -> None:
        self.snapshot = snapshot or gateway_diagnostics_snapshot()
        self.unavailable = unavailable
        self.calls = 0

    def read_snapshot(self) -> GatewayDiagnosticsSnapshot:
        self.calls += 1
        if self.unavailable:
            raise GatewayDiagnosticsUnavailable("offline")
        return self.snapshot


def _unregistered_snapshot(
    *,
    captured_at: float = 100.0,
    captured_monotonic: float = 10.0,
    health_stale: bool = False,
) -> GatewayDiagnosticsSnapshot:
    snapshot = gateway_diagnostics_snapshot(
        captured_at=captured_at,
        captured_monotonic=captured_monotonic,
        health_stale=health_stale,
    )
    return replace(
        snapshot,
        publication=GatewayPublicationSummary(False, 0.0, True),
    )


def _expected_initial_fields(**overrides: object) -> dict[str, object]:
    fields: dict[str, object] = {
        "connected": 0,
        "status": 0,
        "mode": 0,
        "auto_start": 0,
        "start_stop": 0,
        "enable": 0,
        "min_current": 0.0,
        "max_current": 0.0,
        "set_current": 0.0,
        "phase_selection": "P1",
        "phase_selection_active": "P1",
        "supported_phase_selections": "P1",
        "auto_health": "init",
        "auto_health_code": 0,
        "auto_state": "idle",
        "auto_state_code": 0,
        "auto_status_source": "unknown",
        "auto_start_delay_seconds": 0.0,
        "auto_stop_delay_seconds": 0.0,
        "auto_scheduled_enabled_days": "",
        "auto_scheduled_fallback_delay_seconds": 0.0,
        "auto_scheduled_latest_end_time": "06:30",
        "auto_scheduled_night_current": 0.0,
        "auto_dbus_backoff_base_seconds": 0.0,
        "auto_dbus_backoff_max_seconds": 0.0,
    }
    fields.update(auto_policy_control_values(AutoPolicy()))
    fields.update(overrides)
    return fields


def _identity_service(publication: _PublicationRecorder) -> SimpleNamespace:
    return SimpleNamespace(
        gateway_publication=publication,
        auto_policy=AutoPolicy(),
        product_name="EVCS",
        custom_name="Garage",
        firmware_version="1.2.3",
        hardware_version="meter-and-switch",
        serial="serial-1",
        connection_name="LAN",
        topology_configured=False,
    )


def _companion_service(
    publication: _PublicationRecorder,
    snapshot: object,
    **overrides: object,
) -> SimpleNamespace:
    values: dict[str, object] = {
        "gateway_publication": publication,
        "runtime": _Runtime(snapshot),
        "companion_publication_enabled": True,
        "companion_battery_service_enabled": True,
        "companion_pvinverter_service_enabled": True,
        "companion_grid_service_enabled": False,
        "companion_source_services_enabled": True,
        "companion_source_grid_services_enabled": False,
        "custom_name": "EVCS",
        "firmware_version": "1.2.3",
        "hardware_version": "energy-worker",
        "serial": "serial-1",
        "connection_name": "IPC",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class BootstrapPublicationContractTests(unittest.TestCase):
    def test_registration_publishes_complete_semantic_identity_and_initial_state(self) -> None:
        publication = _PublicationRecorder()
        service = _identity_service(publication)
        service.topology_configured = True
        service.last_status = 2
        service.virtual_mode = 1
        service.virtual_autostart = 1
        service.virtual_startstop = 1
        service.virtual_enable = 1
        service.min_current = 6
        service.max_current = 16
        service.virtual_set_current = 10
        service.requested_phase_selection = "P3"
        service.active_phase_selection = "P3"
        service.supported_phase_selections = ("P1", "P3")
        service._last_health_reason = "ready"
        service._last_health_code = 4
        service._last_auto_state = "charging"
        service._last_auto_state_code = 3
        service._last_status_source = "meter"
        service.auto_start_delay_seconds = 12
        service.auto_stop_delay_seconds = 34
        service.auto_scheduled_enabled_days = "1,2,3"
        service.auto_scheduled_night_start_delay_seconds = 45
        service.auto_scheduled_latest_end_time = "07:15"
        service.auto_scheduled_night_current_amps = 8
        service.auto_dbus_backoff_base_seconds = 2
        service.auto_dbus_backoff_max_seconds = 30
        registrar = EvcsPublicationOwner(service, script_path="/opt/evcs/service.py")

        registrar.register()

        self.assertEqual(len(publication.evcs_registrations), 1)
        identity, fields = publication.evcs_registrations[0]
        self.assertEqual(
            identity,
            EvcsServiceIdentity(
                product_name="EVCS",
                custom_name="Garage",
                firmware_version="1.2.3",
                hardware_version="meter-and-switch",
                serial="serial-1",
                connection_name="LAN",
                process_name="/opt/evcs/service.py",
                process_version=(
                    "Unknown version, and running on Python " + platform.python_version()
                ),
            ),
        )
        self.assertEqual(
            fields,
            _expected_initial_fields(
                connected=1,
                status=2,
                mode=1,
                auto_start=1,
                start_stop=1,
                enable=1,
                min_current=6.0,
                max_current=16.0,
                set_current=10.0,
                phase_selection="P3",
                phase_selection_active="P3",
                supported_phase_selections="P1,P3",
                auto_health="ready",
                auto_health_code=4,
                auto_state="charging",
                auto_state_code=3,
                auto_status_source="meter",
                auto_start_delay_seconds=12.0,
                auto_stop_delay_seconds=34.0,
                auto_scheduled_enabled_days="1,2,3",
                auto_scheduled_fallback_delay_seconds=45.0,
                auto_scheduled_latest_end_time="07:15",
                auto_scheduled_night_current=8.0,
                auto_dbus_backoff_base_seconds=2.0,
                auto_dbus_backoff_max_seconds=30.0,
            ),
        )

    def test_registration_defaults_topology_and_rejection_are_explicit(self) -> None:
        publication = _PublicationRecorder()
        publication.accept_registration = False
        service = _identity_service(publication)
        service.topology_configured = False
        service.host_configured = True
        registrar = EvcsPublicationOwner(service, script_path="service.py")

        fields = registrar.initial_fields()
        self.assertEqual(fields, _expected_initial_fields())
        with self.assertRaises(RuntimeError) as rejected:
            registrar.register()
        self.assertEqual(str(rejected.exception), "Gateway rejected EVCS registration")

    def test_identity_and_mapping_boundaries_reject_missing_data_and_copy_keys(self) -> None:
        publication = _PublicationRecorder()
        service = _identity_service(publication)
        service.serial = None
        with self.assertRaises(TypeError) as missing:
            EvcsPublicationOwner(service, script_path="service.py").identity()
        self.assertEqual(
            str(missing.exception),
            "EVCS identity attribute serial is missing",
        )
        delattr(service, "serial")
        with self.assertRaises(TypeError) as absent:
            EvcsPublicationOwner(service, script_path="service.py").identity()
        self.assertEqual(
            str(absent.exception),
            "EVCS identity attribute serial is missing",
        )

        source = {1: "numeric", "mode": 2}
        copied = accepted_publication_fields(source)
        self.assertEqual(copied, {"1": "numeric", "mode": 2})
        self.assertIsNot(copied, source)

    def test_runtime_owner_recovers_once_from_fresh_unregistered_gateway(self) -> None:
        publication = _PublicationRecorder()
        service = _identity_service(publication)
        service.virtual_mode = 2
        monotonic_values = iter((10.0, 14.999, 15.0))
        owner = EvcsPublicationOwner(
            service,
            script_path="service.py",
            monotonic=lambda: next(monotonic_values),
        )
        reader = _DiagnosticsReader(_unregistered_snapshot())

        self.assertTrue(owner.maintain_registration(reader))
        self.assertFalse(owner.maintain_registration(reader))
        self.assertEqual(reader.calls, 1)
        self.assertEqual(len(publication.evcs_registrations), 1)
        self.assertEqual(publication.evcs_registrations[0][1]["mode"], 2)

        reader.snapshot = gateway_diagnostics_snapshot(
            captured_at=100.2,
            captured_monotonic=15.0,
        )
        self.assertFalse(owner.maintain_registration(reader))
        self.assertEqual(reader.calls, 2)
        self.assertEqual(len(publication.evcs_registrations), 1)

    def test_runtime_owner_checks_immediately_at_monotonic_origin(self) -> None:
        publication = _PublicationRecorder()
        owner = EvcsPublicationOwner(
            _identity_service(publication),
            script_path="service.py",
            monotonic=lambda: 1.0,
        )

        self.assertTrue(
            owner.maintain_registration(
                _DiagnosticsReader(_unregistered_snapshot(captured_monotonic=1.0)),
            )
        )
        self.assertEqual(len(publication.evcs_registrations), 1)

    def test_bootstrap_acceptance_defers_the_first_runtime_health_check(self) -> None:
        publication = _PublicationRecorder()
        monotonic_values = iter((10.0, 14.999, 15.0))
        owner = EvcsPublicationOwner(
            _identity_service(publication),
            script_path="service.py",
            monotonic=lambda: next(monotonic_values),
        )
        reader = _DiagnosticsReader(_unregistered_snapshot())

        owner.register()
        self.assertFalse(owner.maintain_registration(reader))
        self.assertEqual(reader.calls, 0)
        self.assertTrue(owner.maintain_registration(reader))
        self.assertEqual(reader.calls, 1)
        self.assertEqual(len(publication.evcs_registrations), 2)

    def test_runtime_owner_ignores_unavailable_stale_future_and_registered_health(self) -> None:
        cases = (
            (_DiagnosticsReader(unavailable=True), 10.0),
            (_DiagnosticsReader(_unregistered_snapshot(health_stale=True)), 10.0),
            (
                _DiagnosticsReader(
                    _unregistered_snapshot(
                        captured_at=89.9,
                        captured_monotonic=1.0,
                    )
                ),
                20.0,
            ),
            (
                _DiagnosticsReader(
                    _unregistered_snapshot(
                        captured_at=100.1,
                        captured_monotonic=10.1,
                    )
                ),
                10.0,
            ),
            (
                _DiagnosticsReader(
                    gateway_diagnostics_snapshot(captured_monotonic=10.0)
                ),
                10.0,
            ),
        )
        for reader, monotonic_at in cases:
            with self.subTest(snapshot=reader.snapshot, unavailable=reader.unavailable):
                publication = _PublicationRecorder()
                owner = EvcsPublicationOwner(
                    _identity_service(publication),
                    script_path="service.py",
                    monotonic=lambda: monotonic_at,
                )
                self.assertFalse(owner.maintain_registration(reader))
                self.assertEqual(publication.evcs_registrations, [])

    def test_runtime_owner_reports_rejected_registration_recovery(self) -> None:
        publication = _PublicationRecorder()
        publication.accept_registration = False
        owner = EvcsPublicationOwner(
            _identity_service(publication),
            script_path="service.py",
            monotonic=lambda: 10.0,
        )

        with self.assertLogs(level="WARNING") as captured:
            accepted = owner.maintain_registration(
                _DiagnosticsReader(_unregistered_snapshot()),
            )

        self.assertFalse(accepted)
        self.assertEqual(
            captured.output,
            ["WARNING:root:Gateway rejected EVCS registration recovery"],
        )


class GridProjectionContractTests(unittest.TestCase):
    def test_projection_covers_smoothing_jump_limits_hold_and_clear(self) -> None:
        projector = GridProjector()
        smooth = GridProjectionConfig(hold_seconds=5, smoothing_alpha=0.25)

        self.assertEqual(
            projector.project("grid", raw_value=100, online=False, now=10, config=smooth),
            GridProjection(100.0, False),
        )
        self.assertEqual(
            projector.project("grid", raw_value=200, online=True, now=11, config=smooth),
            GridProjection(125.0, True),
        )
        self.assertEqual(
            projector.project("grid", raw_value=None, online=False, now=16, config=smooth),
            GridProjection(125.0, True),
        )
        self.assertEqual(
            projector.project("grid", raw_value=None, online=False, now=16.01, config=smooth),
            GridProjection(0.0, False),
        )

        projector.project("grid", raw_value=100, online=True, now=20, config=smooth)
        self.assertEqual(
            projector.project(
                "grid",
                raw_value=200,
                online=True,
                now=21,
                config=GridProjectionConfig(smoothing_alpha=0.5, smoothing_max_jump_watts=50),
            ).value_w,
            200.0,
        )
        for alpha in (-1.0, 2.0):
            with self.subTest(alpha=alpha):
                self.assertEqual(
                    projector.project(
                        f"alpha-{alpha}",
                        raw_value=10,
                        online=True,
                        now=1,
                        config=GridProjectionConfig(smoothing_alpha=alpha),
                    ).value_w,
                    10.0,
                )
                self.assertEqual(
                    projector.project(
                        f"alpha-{alpha}",
                        raw_value=20,
                        online=True,
                        now=2,
                        config=GridProjectionConfig(smoothing_alpha=alpha),
                    ).value_w,
                    20.0,
                )

        projector.clear()
        self.assertEqual(
            projector.project("grid", raw_value=True, online=True, now=22, config=smooth),
            GridProjection(0.0, False),
        )

    def test_grid_input_selection_has_explicit_fused_authoritative_and_combined_semantics(self) -> None:
        sources = (
            {"source_id": None, "grid_interaction_w": 1, "online": True},
            {"source_id": " meter-a ", "grid_interaction_w": 123.0, "online": False},
        )
        self.assertEqual(
            aggregate_grid_input(
                {"grid_fusion_enabled": True, "grid_power": 44.0},
                sources,
                authoritative_source_id="meter-a",
            ),
            (44.0, True),
        )
        self.assertEqual(
            aggregate_grid_input(
                {"grid_fusion_enabled": True, "grid_power": "invalid"},
                sources,
                authoritative_source_id="",
            ),
            ("invalid", False),
        )
        self.assertEqual(aggregate_grid_input({}, sources, authoritative_source_id="meter-a"), (123.0, False))
        self.assertEqual(aggregate_grid_input({}, sources, authoritative_source_id="missing"), (None, False))

        combined_values = ((1, True), ("2", True), (True, False), ("bad", False), (object(), False))
        for online_count, expected_online in combined_values:
            with self.subTest(online_count=online_count):
                self.assertEqual(
                    aggregate_grid_input(
                        {
                            "battery_combined_grid_interaction_w": -50.0,
                            "battery_online_source_count": online_count,
                        },
                        sources,
                        authoritative_source_id="",
                    ),
                    (-50.0, expected_online),
                )


class CompanionPublicationContractTests(unittest.TestCase):
    def test_disabled_publisher_is_side_effect_free(self) -> None:
        publication = _PublicationRecorder()
        service = _companion_service(publication, {}, companion_publication_enabled=False)
        publisher = EnergyCompanionPublisher(service, "service.py")

        publisher.start()
        self.assertFalse(publisher.publish(now=1))
        self.assertEqual(publication.companion_registrations, [])

    def test_start_flags_register_only_enabled_aggregates_and_stop_resets_state(self) -> None:
        publication = _PublicationRecorder()
        service = _companion_service(
            publication,
            {"battery_online_source_count": 1, "battery_combined_grid_interaction_w": 12},
            companion_battery_service_enabled=False,
            companion_pvinverter_service_enabled=False,
            companion_grid_service_enabled=True,
        )
        publisher = EnergyCompanionPublisher(service, "service.py")

        publisher.start()
        self.assertEqual(
            [identity.service_id for identity, _fields in publication.companion_registrations],
            ["aggregate-grid"],
        )
        self.assertTrue(publisher.publish(now=1))
        publisher.stop()
        service.companion_grid_service_enabled = False
        service.companion_battery_service_enabled = True
        publisher.start()
        self.assertEqual(publication.companion_registrations[-1][0].service_id, "aggregate-battery")

    def test_lazy_publication_rejects_missing_gateway_and_uses_monotonic_time(self) -> None:
        service = SimpleNamespace(
            companion_publication_enabled=True,
            companion_source_services_enabled=True,
            runtime=_Runtime({}),
        )
        with self.assertRaisesRegex(RuntimeError, "Semantic gateway publication is not configured"):
            EnergyCompanionPublisher(service, "service.py").publish(now=1)

        publication = _PublicationRecorder()
        publisher = EnergyCompanionPublisher(_companion_service(publication, {}), "service.py")
        with patch("venus_evcharger.companion.publication.time.monotonic", return_value=91.0) as monotonic:
            self.assertFalse(publisher.publish())
        monotonic.assert_called_once_with()

    def test_registration_and_publication_rejections_retry_without_false_acceptance(self) -> None:
        publication = _PublicationRecorder()
        publication.accept_registration = False
        service = _companion_service(publication, {"battery_source_count": 1, "battery_online_source_count": 1})
        publisher = EnergyCompanionPublisher(service, "service.py")

        publisher.start()
        self.assertEqual(len(publication.companion_registrations), 2)
        publication.accept_registration = True
        publisher.start()
        self.assertEqual(len(publication.companion_registrations), 4)

        service.runtime.snapshot = {"battery_source_count": 1, "battery_online_source_count": 1}
        self.assertTrue(publisher.publish(now=1))
        publication.accept_publication = False
        service.runtime.snapshot = {
            "battery_source_count": 1,
            "battery_online_source_count": 1,
            "battery_combined_soc": 55,
        }
        self.assertFalse(publisher.publish(now=2))
        rejected_count = len(publication.companion_publications)
        publication.accept_publication = True
        self.assertTrue(publisher.publish(now=3))
        self.assertGreater(len(publication.companion_publications), rejected_count)
        self.assertFalse(publisher.publish(now=4))

    def test_aggregate_grid_sources_and_fallback_power_are_published_semantically(self) -> None:
        publication = _PublicationRecorder()
        snapshot = {
            "battery_source_count": 2,
            "battery_online_source_count": "2",
            "battery_combined_soc": 60,
            "battery_combined_net_power_w": -400,
            "battery_combined_usable_capacity_wh": 5000,
            "battery_combined_pv_input_power_w": "missing",
            "battery_combined_ac_power_w": -20,
            "battery_combined_grid_interaction_w": 321,
            "battery_sources": [
                {
                    "source_id": "hybrid-a",
                    "role": "hybrid-inverter",
                    "online": True,
                    "soc": 61,
                    "net_battery_power_w": -350,
                    "usable_capacity_wh": 4800,
                    "pv_input_power_w": "missing",
                    "ac_power_w": 700,
                    "grid_interaction_w": 12,
                },
                {
                    "source_id": "battery-b",
                    "role": "battery",
                    "online": False,
                    "grid_interaction_w": None,
                },
                {"source_id": "pv-c", "role": "inverter", "online": True, "ac_power_w": -3},
                {"source_id": "other", "role": "unknown", "online": True, "grid_interaction_w": 4},
                {"source_id": "", "role": "battery"},
                "invalid",
            ],
        }
        service = _companion_service(
            publication,
            snapshot,
            companion_grid_service_enabled=True,
            companion_source_grid_services_enabled=True,
            companion_grid_smoothing_alpha="invalid",
            companion_grid_hold_seconds="invalid",
            companion_grid_smoothing_max_jump_watts="invalid",
            custom_name="",
            custom_name_override="Site",
            connection_name="",
        )
        publisher = EnergyCompanionPublisher(service, "service.py")

        publisher.start()
        self.assertTrue(publisher.publish(now=10))

        registrations = {identity.service_id: (identity, fields) for identity, fields in publication.companion_registrations}
        aggregate_updates = {
            service_id: fields for service_id, fields, _priority in publication.companion_publications
        }
        self.assertEqual(aggregate_updates["aggregate-battery"]["connected"], 1)
        self.assertEqual(registrations["aggregate-pv"][1]["ac_power_w"], 0.0)
        self.assertEqual(registrations["aggregate-grid"][1]["connected"], 0)
        source_entries = [entry for key, entry in registrations.items() if key.startswith("source-")]
        self.assertEqual(len(source_entries), 6)
        self.assertTrue(all(identity.custom_name.startswith("Site External Energy") for identity, _ in source_entries))
        self.assertTrue(all(identity.connection_name == "External energy companion" for identity, _ in source_entries))
        source_fields = [fields for _identity, fields in source_entries]
        self.assertTrue(any(fields.get("ac_power_w") == 700.0 for fields in source_fields))
        self.assertTrue(any(fields.get("ac_power_w") == 0.0 for fields in source_fields))

        for source_count in (True, "bad", object()):
            with self.subTest(source_count=source_count):
                service.runtime.snapshot = {
                    "battery_source_count": source_count,
                    "battery_online_source_count": source_count,
                    "battery_sources": [
                        {
                            "source_id": "hybrid-a",
                            "role": "hybrid-inverter",
                            "online": True,
                            "soc": 62,
                            "pv_input_power_w": 701,
                            "grid_interaction_w": 13,
                        }
                    ],
                }
                publisher.publish(now=11)

        updated_source_fields = [
            fields
            for service_id, fields, _priority in publication.companion_publications
            if service_id.startswith("source-")
        ]
        self.assertTrue(any(fields.get("soc_percent") == 62 for fields in updated_source_fields))

    def test_source_publication_can_be_disabled_and_runtime_snapshot_must_be_a_mapping(self) -> None:
        publication = _PublicationRecorder()
        service = _companion_service(
            publication,
            {"battery_sources": [{"source_id": "battery", "role": "battery", "online": True}]},
            companion_source_services_enabled=False,
        )
        publisher = EnergyCompanionPublisher(service, "service.py")
        publisher.start()
        self.assertFalse(publisher.publish(now=1))
        self.assertEqual(len(publication.companion_registrations), 2)

        service.runtime.snapshot = ["not", "a", "mapping"]
        self.assertFalse(publisher.publish(now=2))
        service.runtime = object()
        self.assertFalse(publisher.publish(now=3))


if __name__ == "__main__":
    unittest.main()

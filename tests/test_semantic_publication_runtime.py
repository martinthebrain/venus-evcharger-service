# SPDX-License-Identifier: GPL-3.0-or-later
"""Focused behavior tests for the transport-neutral publication boundary."""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from tests.gateway_diagnostics_fixtures import gateway_diagnostics_reader
from venus_evcharger.companion import EnergyCompanionPublisher
from venus_evcharger.companion.grid_projection import GridProjectionConfig, GridProjector
from venus_evcharger.ports.gateway_publication import (
    CompanionServiceIdentity,
    EvcsServiceIdentity,
    PublicationPriority,
    PublicationReceipt,
)
from venus_evcharger.publish.dbus_core import DbusPublishCore
from venus_evcharger.publish.dbus_shared import DbusPublishContext


class _PublicationRecorder:
    def __init__(self, *, accepted: bool = True) -> None:
        self.accepted = accepted
        self.evcs: list[tuple[dict[str, object], PublicationPriority]] = []
        self.companion_registrations: list[tuple[CompanionServiceIdentity, dict[str, object]]] = []
        self.companion_publications: list[tuple[str, dict[str, object], PublicationPriority]] = []

    def register_evcs(
        self,
        identity: EvcsServiceIdentity,
        initial_fields: dict[str, object],
    ) -> PublicationReceipt:
        del identity, initial_fields
        return PublicationReceipt(self.accepted, "evcs-registration")

    def publish_evcs_fields(
        self,
        fields: dict[str, object],
        *,
        priority: PublicationPriority,
    ) -> PublicationReceipt:
        self.evcs.append((dict(fields), priority))
        return PublicationReceipt(self.accepted, "evcs-fields")

    def register_companion(
        self,
        identity: CompanionServiceIdentity,
        initial_fields: dict[str, object],
    ) -> PublicationReceipt:
        self.companion_registrations.append((identity, dict(initial_fields)))
        return PublicationReceipt(self.accepted, identity.service_id)

    def publish_companion_fields(
        self,
        service_id: str,
        fields: dict[str, object],
        *,
        priority: PublicationPriority,
    ) -> PublicationReceipt:
        self.companion_publications.append((service_id, dict(fields), priority))
        return PublicationReceipt(self.accepted, service_id)


class _Runtime:
    def __init__(self, snapshot: dict[str, object] | None = None) -> None:
        self._snapshot = snapshot or {}
        self.failures: list[str] = []

    def worker_snapshot(self) -> dict[str, object]:
        return dict(self._snapshot)

    def mark_failure(self, key: str) -> None:
        self.failures.append(key)

    def warning_throttled(self, *_args: object, **_kwargs: object) -> None:
        return None

    def source_retry_remaining(self, _key: str, _now: float | None = None) -> int:
        return 0

    def update_is_stale(self, _now: float | None = None) -> bool:
        return False


class SemanticPublicationRuntimeTests(unittest.TestCase):
    def test_core_publishes_fields_without_paths_or_local_readback_claim(self) -> None:
        recorder = _PublicationRecorder()
        service = SimpleNamespace(
            gateway_publication=recorder,
            runtime=_Runtime(),
            _dbus_live_publish_interval_seconds=1.0,
            _dbus_slow_publish_interval_seconds=5.0,
        )
        core = DbusPublishCore(
            DbusPublishContext(service, lambda _timestamp, _now: 0, gateway_diagnostics_reader())
        )

        self.assertTrue(core.publish_field("mode", 2, now=10.0, force=True))
        self.assertEqual(recorder.evcs, [({"mode": 2}, "critical")])
        self.assertEqual(core.last_accepted_field("mode"), 2)
        self.assertFalse(core.publish_field("mode", 2, now=11.0))
        self.assertFalse(any(field.startswith("/") for field in recorder.evcs[0][0]))

    def test_rejected_publication_is_not_cached_as_accepted(self) -> None:
        recorder = _PublicationRecorder(accepted=False)
        runtime = _Runtime()
        service = SimpleNamespace(gateway_publication=recorder, runtime=runtime)
        core = DbusPublishCore(
            DbusPublishContext(service, lambda _timestamp, _now: 0, gateway_diagnostics_reader())
        )

        self.assertFalse(core.publish_field("ac_power_w", 1200.0, now=10.0, force=True))
        self.assertIsNone(core.last_accepted_field("ac_power_w"))

    def test_companion_uses_distinct_opaque_services_and_semantic_fields(self) -> None:
        recorder = _PublicationRecorder()
        runtime = _Runtime(
            {
                "battery_source_count": 1,
                "battery_online_source_count": 1,
                "battery_combined_soc": 72.0,
                "battery_combined_pv_input_power_w": 900.0,
                "battery_sources": [
                    {
                        "source_id": "roof inverter/A",
                        "role": "hybrid-inverter",
                        "online": True,
                        "soc": 71.0,
                        "pv_input_power_w": 850.0,
                    }
                ],
            }
        )
        service = SimpleNamespace(
            gateway_publication=recorder,
            runtime=runtime,
            companion_publication_enabled=True,
            companion_battery_service_enabled=True,
            companion_pvinverter_service_enabled=True,
            companion_grid_service_enabled=False,
            companion_source_services_enabled=True,
            companion_source_grid_services_enabled=False,
            custom_name="EVCS",
        )
        publisher = EnergyCompanionPublisher(service, "/opt/evcs/service.py")

        publisher.start()
        self.assertTrue(publisher.publish(10.0))

        identities = [identity for identity, _fields in recorder.companion_registrations]
        ids = {identity.service_id for identity in identities}
        self.assertIn("aggregate-battery", ids)
        self.assertIn("aggregate-pv", ids)
        source_ids = {service_id for service_id in ids if service_id.startswith("source-")}
        self.assertEqual(len(source_ids), 2)
        self.assertNotIn("roof inverter/A", source_ids)
        self.assertTrue(all(not key.startswith("/") for _, fields in recorder.companion_registrations for key in fields))
        self.assertTrue(
            all(not key.startswith("/") for _, fields, _priority in recorder.companion_publications for key in fields)
        )

    def test_grid_projection_holds_then_expires_missing_sample(self) -> None:
        projector = GridProjector()
        config = GridProjectionConfig(hold_seconds=5.0, smoothing_alpha=0.5)

        first = projector.project("grid", raw_value=100.0, online=True, now=10.0, config=config)
        smoothed = projector.project("grid", raw_value=200.0, online=True, now=11.0, config=config)
        held = projector.project("grid", raw_value=None, online=False, now=15.0, config=config)
        expired = projector.project("grid", raw_value=None, online=False, now=17.0, config=config)

        self.assertEqual(first.value_w, 100.0)
        self.assertEqual(smoothed.value_w, 150.0)
        self.assertEqual(held, type(held)(value_w=150.0, connected=True))
        self.assertEqual(expired, type(expired)(value_w=0.0, connected=False))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

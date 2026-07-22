# SPDX-License-Identifier: GPL-3.0-or-later
"""Adapter-side contracts for the semantic gateway diagnostics producer."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.dbus_adapter_venus_stubs import install_venus_adapter_stubs

install_venus_adapter_stubs()

from venus_evcharger.dbus_adapter.process import diagnostics as diagnostics_module
from venus_evcharger.dbus_adapter.process.adapter import DbusAdapter
from venus_evcharger.dbus_adapter.publication.registry import PublicationFieldObservation
from venus_evcharger.dbus_gateway import gateway_paths
from venus_evcharger.ipc.energy import EnergySourceDescriptor, EnergyTopologySnapshot
from venus_evcharger.ipc.gateway_diagnostics import (
    GATEWAY_DIAGNOSTICS_FILENAME,
    GatewayDiagnosticsFileReader,
)
from venus_evcharger.ipc.gateway_publication import parse_register_evcs, register_evcs_command
from venus_evcharger.ports.gateway_publication import EvcsServiceIdentity

_STALE_CONFIDENCE = 0.5


def _health(**changes: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "state": "ok",
        "timeouts_60s": 1,
        "avg_latency_ms": 4.0,
        "max_latency_ms": 9.0,
        "pending_command_count": 2,
        "core_command_count": 3,
        "mainloop_heartbeat_age_s": 0.1,
        "last_tick_at": 99.0,
        "last_success_at": 99.5,
        "last_error": "",
        "discovery_last_error": "",
        "eventloop": {"max_tick_gap_ms_60s": 25.0},
    }
    payload.update(changes)
    return payload


def _topology(*, generation: int = 1) -> EnergyTopologySnapshot:
    return EnergyTopologySnapshot(
        generation=generation,
        captured_at=100.0,
        sources=(
            EnergySourceDescriptor("grid-primary", "grid", "online", ("power",)),
            EnergySourceDescriptor("pv-roof", "pv_ac", "offline", ("power",)),
        ),
    )


def _identity() -> EvcsServiceIdentity:
    return EvcsServiceIdentity(
        product_name="EVCS",
        custom_name="Garage",
        firmware_version="1.0",
        hardware_version="relay",
        serial="evcs-60",
        connection_name="Local controller",
        process_name="venus_evcharger_service.py",
        process_version="Python",
    )


class GatewayDiagnosticsAdapterContractsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.root = Path(self.temporary_directory.name)
        config_path = self.root / "config.ini"
        config_path.write_text("[DEFAULT]\nDbusIntrospectionEnabled=0\n", encoding="utf-8")
        self.adapter = DbusAdapter(
            str(config_path),
            paths=gateway_paths(str(self.root / "run")),
        )

    def register_evcs(self, fields: dict[str, object]) -> None:
        registration = parse_register_evcs(register_evcs_command(_identity(), fields))
        self.assertIsNotNone(registration)
        assert registration is not None
        with patch("venus_evcharger.dbus_adapter.publication.registry.time.time", return_value=100.0):
            self.assertEqual(self.adapter.publication_registry.register_evcs(registration), "applied")

    def test_unregistered_evcs_has_complete_unknown_surface(self) -> None:
        snapshot = self.adapter.gateway_diagnostics_snapshot(
            health=_health(),
            topology=_topology(generation=0),
            captured_at=100.0,
        )

        self.assertEqual(snapshot.health.state, "ok")
        self.assertEqual(snapshot.discovery.state, "unknown")
        self.assertEqual(snapshot.discovery.discovered_source_count, 2)
        self.assertEqual(snapshot.discovery.unusable_source_count, 1)
        self.assertTrue(all(sample.status == "unknown" for sample in snapshot.ev_charger))
        self.assertEqual(len(snapshot.ev_charger), 10)

    def test_registered_fields_map_to_semantic_samples_and_staleness(self) -> None:
        self.register_evcs(
            {
                "mode": 2,
                "start_stop": 0,
                "enable": 1,
                "auto_start": 1,
                "ac_power_w": 2200.5,
                "status": 2,
                "auto_decision_reason": "scheduled-window",
                "auto_decision_state": "charging",
                "auto_health": "healthy",
                "auto_runtime_overrides_active": 1,
            }
        )

        fresh = self.adapter.gateway_diagnostics_snapshot(health=_health(), topology=_topology(), captured_at=100.5)
        self.assertEqual(fresh.sample("operating_mode").value, 2)
        self.assertIs(fresh.sample("charging_enabled").value, False)
        self.assertIs(fresh.sample("auto_start_enabled").value, True)
        self.assertEqual(fresh.sample("ac_power_w").value, 2200.5)
        self.assertEqual(fresh.sample("charger_state_code").value, 2)
        self.assertEqual(fresh.sample("decision_reason").value, "scheduled-window")
        self.assertEqual(fresh.sample("decision_state").value, "charging")
        self.assertEqual(fresh.sample("last_health_reason").value, "healthy")
        self.assertIs(fresh.sample("runtime_overrides_active").value, True)
        self.assertEqual(fresh.sample("runtime_overrides_source").value, "runtime-overrides")

        stale = self.adapter.gateway_diagnostics_snapshot(
            health=_health(),
            topology=_topology(),
            captured_at=100.0 + self.adapter.slo_gui_max_age_seconds + 0.1,
        )
        self.assertTrue(all(sample.status == "stale" for sample in stale.ev_charger))
        self.assertTrue(all(sample.confidence == _STALE_CONFIDENCE for sample in stale.ev_charger))
        self.assertTrue(all(sample.reason_code == "publication-stale" for sample in stale.ev_charger))

    def test_static_override_source_and_invalid_publication_are_explicit(self) -> None:
        self.register_evcs(
            {
                "mode": 7,
                "start_stop": "invalid",
                "auto_runtime_overrides_active": 0,
            }
        )
        snapshot = self.adapter.gateway_diagnostics_snapshot(health=_health(), topology=_topology(), captured_at=100.5)

        self.assertEqual(snapshot.sample("operating_mode").status, "error")
        self.assertEqual(snapshot.sample("operating_mode").reason_code, "invalid-publication-value")
        self.assertEqual(snapshot.sample("charging_enabled").status, "error")
        self.assertEqual(
            snapshot.sample("runtime_overrides_source").value,
            "static-configuration",
        )

    def test_health_and_discovery_are_bounded_semantic_summaries(self) -> None:
        self.adapter._introspection_queue_depth = 2
        running = self.adapter.gateway_diagnostics_snapshot(
            health=_health(
                avg_latency_ms=12.0,
                max_latency_ms=4.0,
                last_error="org.freedesktop.DBus.Error.NoReply: hidden target",
            ),
            topology=_topology(),
            captured_at=100.0,
        )
        self.assertEqual(running.health.maximum_latency_ms, 12.0)
        self.assertEqual(running.health.last_error_code, "timeout")
        self.assertEqual(running.discovery.state, "running")
        self.assertNotIn("hidden target", json.dumps(running.to_payload()))

        self.adapter._introspection_queue_depth = 0
        degraded = self.adapter.gateway_diagnostics_snapshot(
            health=_health(state="degraded", last_error="connection closed"),
            topology=_topology(),
            captured_at=100.0,
        )
        self.assertEqual(degraded.health.last_error_code, "connection-failed")
        self.assertEqual(degraded.discovery.state, "degraded")

        protective = self.adapter.gateway_diagnostics_snapshot(
            health=_health(state="protective", mainloop_heartbeat_age_s=5.0),
            topology=_topology(),
            captured_at=100.0,
        )
        self.assertTrue(protective.health.stale)
        self.assertEqual(protective.discovery.state, "protective")

        failed = self.adapter.gateway_diagnostics_snapshot(
            health=_health(last_error="operation failed", discovery_last_error="scan failed"),
            topology=_topology(),
            captured_at=100.0,
        )
        self.assertEqual(failed.health.last_error_code, "gateway-operation-failed")
        self.assertEqual(failed.discovery.state, "error")

    def test_publish_cache_writes_canonical_document_without_changing_health_shape(self) -> None:
        self.register_evcs({"mode": 0, "start_stop": 1, "ac_power_w": 800.0})
        self.adapter._last_tick_at = 100.0
        self.adapter._last_tick_monotonic = 1.0

        self.adapter.publish_cache()

        diagnostics_path = Path(self.adapter.paths.run_dir) / GATEWAY_DIAGNOSTICS_FILENAME
        self.assertTrue(diagnostics_path.is_file())
        snapshot = GatewayDiagnosticsFileReader(str(diagnostics_path)).read_snapshot()
        self.assertEqual(snapshot.sequence, self.adapter.cache.sequence)
        self.assertEqual(snapshot.sample("operating_mode").value, 0)

        health_payload = json.loads(Path(self.adapter.paths.health_path).read_text(encoding="utf-8"))
        self.assertEqual(set(health_payload), {"schema_version", "sequence", "captured_at", "dbus_health"})
        self.assertNotIn("ev_charger", health_payload)

    def test_private_normalizers_fail_closed(self) -> None:
        self.assertEqual(diagnostics_module._health_state("invalid"), "unknown")
        self.assertEqual(diagnostics_module._health_state(1), "unknown")
        self.assertEqual(diagnostics_module._last_error_code(None), "")
        self.assertEqual(diagnostics_module._mapping([]), {})
        self.assertEqual(diagnostics_module._non_negative_int(True), 0)
        self.assertEqual(diagnostics_module._non_negative_int(-2), 0)
        self.assertEqual(diagnostics_module._non_negative_float(-2.0), 0.0)
        self.assertEqual(diagnostics_module._non_negative_float(float("inf")), 0.0)
        self.assertEqual(diagnostics_module._non_negative_float("2"), 0.0)
        self.assertEqual(diagnostics_module._non_negative_integer("2"), 2)
        with self.assertRaises(TypeError):
            diagnostics_module._non_negative_integer(object())
        self.assertIs(diagnostics_module._boolean(True), True)
        with self.assertRaises(TypeError):
            diagnostics_module._boolean(2)
        with self.assertRaises(TypeError):
            diagnostics_module._finite_float(True)
        with self.assertRaises(ValueError):
            diagnostics_module._finite_float(float("nan"))
        with self.assertRaises(ValueError):
            diagnostics_module._non_negative_integer(-1)

    def test_missing_field_and_charging_fallback_keep_quality_explicit(self) -> None:
        unavailable = diagnostics_module._observed_sample(
            "operating_mode",
            None,
            diagnostics_module._mode,
            10.0,
            2.0,
        )
        self.assertEqual(unavailable.status, "unavailable")
        self.assertEqual(unavailable.reason_code, "field-unavailable")

        fallback = diagnostics_module._charging_enabled_sample(
            lambda field: PublicationFieldObservation(1, 9.0) if field == "enable" else None,
            10.0,
            2.0,
        )
        self.assertIs(fallback.value, True)
        self.assertEqual(fallback.status, "fresh")

        unknown_source = diagnostics_module._runtime_overrides_source(
            diagnostics_module._unknown_sample("runtime_overrides_active")
        )
        self.assertEqual(unknown_source.status, "unknown")

    def test_registry_refreshes_timestamp_when_value_is_republished(self) -> None:
        self.register_evcs({"mode": 1})
        first = self.adapter.publication_registry.evcs_field_observation("mode")
        self.assertIsNotNone(first)
        assert first is not None
        registration = parse_register_evcs(register_evcs_command(_identity(), {"mode": 1}))
        self.assertIsNotNone(registration)
        assert registration is not None

        with patch("venus_evcharger.dbus_adapter.publication.registry.time.time", return_value=110.0):
            self.assertEqual(self.adapter.publication_registry.register_evcs(registration), "applied")

        refreshed = self.adapter.publication_registry.evcs_field_observation("mode")
        self.assertEqual(refreshed, PublicationFieldObservation(1, 110.0))
        self.assertEqual(first.observed_at, 100.0)
        self.assertIsNone(self.adapter.publication_registry.evcs_field_observation("not-a-field"))


if __name__ == "__main__":
    unittest.main()

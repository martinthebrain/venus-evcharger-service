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

from venus_evcharger.dbus_adapter.process import diagnostics_summary, diagnostics_values
from venus_evcharger.dbus_adapter.process.adapter import DbusAdapter
from venus_evcharger.dbus_adapter.publication.registry import PublicationFieldObservation
from venus_evcharger.dbus_gateway import gateway_paths
from venus_evcharger.ipc.energy import EnergySourceDescriptor, EnergyTopologySnapshot
from venus_evcharger.ipc.gateway_diagnostics import (
    GATEWAY_DIAGNOSTICS_FILENAME,
    GatewayDiagnosticsFileReader,
)
from venus_evcharger.ipc.gateway_publication import parse_register_evcs, register_evcs_command
from venus_evcharger.ports.gateway_diagnostic_values import (
    GatewayDiagnosticApplicability,
    GatewayDiagnosticSample,
)
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


def _samples_with_applicability(
    samples: tuple[GatewayDiagnosticSample, ...],
    applicability: GatewayDiagnosticApplicability,
) -> tuple[GatewayDiagnosticSample, ...]:
    return tuple(sample for sample in samples if sample.applicability == applicability)


def _assert_stale_samples(
    test_case: unittest.TestCase,
    samples: tuple[GatewayDiagnosticSample, ...],
) -> None:
    for sample in samples:
        test_case.assertEqual(sample.status, "stale")
        test_case.assertEqual(sample.confidence, _STALE_CONFIDENCE)
        test_case.assertEqual(sample.reason_code, "publication-stale")


def _assert_inactive_samples(
    test_case: unittest.TestCase,
    samples: tuple[GatewayDiagnosticSample, ...],
) -> None:
    for sample in samples:
        test_case.assertEqual(sample.status, "inactive")


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

    def register_evcs(
        self,
        fields: dict[str, object],
        *,
        observed_at: float = 100.0,
    ) -> None:
        registration = parse_register_evcs(register_evcs_command(_identity(), fields))
        self.assertIsNotNone(registration)
        assert registration is not None
        with patch(
            "venus_evcharger.dbus_adapter.publication.registry.time.time",
            return_value=observed_at,
        ):
            self.assertEqual(self.adapter.publication_registry.register_evcs(registration), "applied")

    def register_semantic_evcs(self) -> None:
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

    def test_unregistered_evcs_has_complete_unknown_surface(self) -> None:
        snapshot = self.adapter.diagnostics_role.gateway_diagnostics_snapshot(
            health=_health(),
            topology=_topology(generation=0),
            captured_at=100.0,
        )

        self.assertEqual(snapshot.health.state, "ok")
        self.assertEqual(snapshot.discovery.state, "unknown")
        self.assertEqual(snapshot.discovery.discovered_source_count, 2)
        self.assertEqual(snapshot.discovery.unusable_source_count, 1)
        self.assertEqual(snapshot.discovery.dormant_source_count, 0)
        self.assertEqual(snapshot.discovery.sources[1].availability, "unavailable")
        self.assertFalse(snapshot.publication.registered)
        self.assertTrue(all(sample.status == "unknown" for sample in snapshot.ev_charger))
        self.assertEqual(len(snapshot.ev_charger), 10)

    def test_pv_is_dormant_only_with_explicit_sleep_evidence(self) -> None:
        snapshot = self.adapter.diagnostics_role.gateway_diagnostics_snapshot(
            health=_health(dormant_energy_source_ids=["pv-roof"]),
            topology=_topology(),
            captured_at=100.0,
        )

        self.assertEqual(snapshot.discovery.unusable_source_count, 0)
        self.assertEqual(snapshot.discovery.dormant_source_count, 1)
        self.assertEqual(snapshot.discovery.sources[1].availability, "dormant")
        self.assertEqual(snapshot.discovery.sources[1].reason_code, "pv-sleep-confirmed")

    def test_registered_fields_map_to_semantic_samples(self) -> None:
        self.register_semantic_evcs()
        fresh = self.adapter.diagnostics_role.gateway_diagnostics_snapshot(
            health=_health(),
            topology=_topology(),
            captured_at=100.5,
        )
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
        for name in (
            "decision_reason",
            "decision_state",
            "last_health_reason",
            "runtime_overrides_active",
            "runtime_overrides_source",
        ):
            self.assertEqual(fresh.sample(name).status, "inactive")
            self.assertEqual(fresh.sample(name).applicability, "not-applicable")
            self.assertEqual(fresh.sample(name).reason_code, "operating-mode-not-auto")
        self.assertEqual(fresh.publication.heartbeat_at, 100.0)
        self.assertFalse(fresh.publication.stale)

    def test_registered_fields_expose_staleness_without_changing_inactive_fields(self) -> None:
        self.register_semantic_evcs()
        stale = self.adapter.diagnostics_role.gateway_diagnostics_snapshot(
            health=_health(),
            topology=_topology(),
            captured_at=100.0 + self.adapter.slo_gui_max_age_seconds + 0.1,
        )
        applicable = _samples_with_applicability(stale.ev_charger, "applicable")
        inactive = _samples_with_applicability(stale.ev_charger, "not-applicable")
        _assert_stale_samples(self, applicable)
        _assert_inactive_samples(self, inactive)
        self.assertTrue(stale.publication.stale)

    def test_resource_pressure_extends_the_publication_freshness_window(self) -> None:
        self.register_semantic_evcs()
        pressure_adjusted = self.adapter.diagnostics_role.gateway_diagnostics_snapshot(
            health=_health(
                adaptive_tick_seconds=1.0,
                backpressure={"state": "protective"},
                resources={"state": "constrained"},
            ),
            topology=_topology(),
            captured_at=107.0,
        )
        pressure_applicable = tuple(
            sample
            for sample in pressure_adjusted.ev_charger
            if sample.applicability == "applicable"
        )
        self.assertTrue(all(sample.status == "fresh" for sample in pressure_applicable))
        self.assertFalse(pressure_adjusted.publication.stale)

    def test_static_override_source_and_invalid_publication_are_explicit(self) -> None:
        self.register_evcs(
            {
                "mode": 7,
                "start_stop": "invalid",
                "auto_runtime_overrides_active": 0,
            }
        )
        snapshot = self.adapter.diagnostics_role.gateway_diagnostics_snapshot(
            health=_health(),
            topology=_topology(),
            captured_at=100.5,
        )

        self.assertEqual(snapshot.sample("operating_mode").status, "error")
        self.assertEqual(snapshot.sample("operating_mode").reason_code, "invalid-publication-value")
        self.assertEqual(snapshot.sample("charging_enabled").status, "error")
        self.assertEqual(
            snapshot.sample("runtime_overrides_source").value,
            "static-configuration",
        )

    def test_health_and_discovery_are_bounded_semantic_summaries(self) -> None:
        self.adapter._introspection_queue_depth = 2
        running = self.adapter.diagnostics_role.gateway_diagnostics_snapshot(
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
        degraded = self.adapter.diagnostics_role.gateway_diagnostics_snapshot(
            health=_health(state="degraded", last_error="connection closed"),
            topology=_topology(),
            captured_at=100.0,
        )
        self.assertEqual(degraded.health.last_error_code, "connection-failed")
        self.assertEqual(degraded.discovery.state, "degraded")

        protective = self.adapter.diagnostics_role.gateway_diagnostics_snapshot(
            health=_health(state="protective", mainloop_heartbeat_age_s=5.0),
            topology=_topology(),
            captured_at=100.0,
        )
        self.assertTrue(protective.health.stale)
        self.assertEqual(protective.discovery.state, "protective")

        failed = self.adapter.diagnostics_role.gateway_diagnostics_snapshot(
            health=_health(last_error="operation failed", discovery_last_error="scan failed"),
            topology=_topology(),
            captured_at=100.0,
        )
        self.assertEqual(failed.health.last_error_code, "gateway-operation-failed")
        self.assertEqual(failed.discovery.state, "error")

    def test_health_summary_preserves_every_semantic_field_and_stale_boundary(self) -> None:
        payload = _health(
            state="degraded",
            avg_latency_ms=4.0,
            max_latency_ms=9.0,
            mainloop_heartbeat_age_s=1.0,
            last_tick_at=99.0,
            last_error="connection closed",
        )
        self.assertEqual(
            diagnostics_summary.health_summary(payload, max_tick_seconds=0.5).to_payload(),
            {
                "state": "degraded",
                "stale": False,
                "timeouts_60s": 1,
                "average_latency_ms": 4.0,
                "maximum_latency_ms": 9.0,
                "pending_gateway_commands": 2,
                "pending_core_commands": 3,
                "maximum_event_loop_gap_ms_60s": 25.0,
                "last_success_at": 99.5,
                "last_error_code": "connection-failed",
            },
        )

        payload["mainloop_heartbeat_age_s"] = 1.001
        self.assertTrue(
            diagnostics_summary.health_summary(
                payload,
                max_tick_seconds=0.5,
            ).stale
        )
        payload["mainloop_heartbeat_age_s"] = 0.0
        payload["last_tick_at"] = 0.0
        self.assertTrue(
            diagnostics_summary.health_summary(
                payload,
                max_tick_seconds=0.5,
            ).stale
        )

    def test_discovery_summary_preserves_source_reasons_and_count_boundaries(self) -> None:
        topology = EnergyTopologySnapshot(
            generation=2,
            captured_at=100.0,
            sources=(
                EnergySourceDescriptor("grid", "grid", "online", ("power",)),
                EnergySourceDescriptor("sleeping", "pv_ac", "offline", ("power",)),
                EnergySourceDescriptor("missing", "pv_dc", "offline", ("power",)),
                EnergySourceDescriptor("uncertain", "battery", "unknown", ("soc",)),
            ),
        )
        summary = diagnostics_summary.discovery_summary(
            _health(
                dormant_energy_source_ids=["sleeping"],
                energy_source_unavailability_reasons={
                    "missing": "source-path-unreadable",
                },
            ),
            topology,
            pending_work=-1,
        )

        self.assertEqual(
            summary.to_payload(),
            {
                "enabled": True,
                "state": "idle",
                "pending_work": 0,
                "discovered_source_count": 4,
                "unusable_source_count": 2,
                "dormant_source_count": 1,
                "sources": [
                    {
                        "source_id": "grid",
                        "kind": "grid",
                        "availability": "available",
                        "reason_code": "",
                    },
                    {
                        "source_id": "sleeping",
                        "kind": "pv_ac",
                        "availability": "dormant",
                        "reason_code": "pv-sleep-confirmed",
                    },
                    {
                        "source_id": "missing",
                        "kind": "pv_dc",
                        "availability": "unavailable",
                        "reason_code": "source-path-unreadable",
                    },
                    {
                        "source_id": "uncertain",
                        "kind": "battery",
                        "availability": "unknown",
                        "reason_code": "source-state-unknown",
                    },
                ],
            },
        )

        running = diagnostics_summary.discovery_summary(
            _health(),
            topology,
            pending_work=1,
        )
        self.assertEqual(running.state, "running")
        self.assertEqual(running.pending_work, 1)

    def test_publication_summary_uses_exact_registration_and_age_boundaries(self) -> None:
        registry = self.adapter.publication_registry
        self.assertEqual(
            diagnostics_summary.publication_summary(
                registry,
                captured_at=10.0,
                stale_after_seconds=2.0,
            ).to_payload(),
            {"registered": False, "heartbeat_at": 0.0, "stale": True},
        )

        self.register_evcs({"mode": 0})
        at_deadline = diagnostics_summary.publication_summary(
            registry,
            captured_at=102.0,
            stale_after_seconds=2.0,
        )
        self.assertEqual(
            at_deadline.to_payload(),
            {"registered": True, "heartbeat_at": 100.0, "stale": False},
        )
        self.assertTrue(
            diagnostics_summary.publication_summary(
                registry,
                captured_at=102.001,
                stale_after_seconds=2.0,
            ).stale
        )

    def test_diagnostic_freshness_deadline_covers_each_pressure_input(self) -> None:
        for state, expected in (
            ("ok", 4.0),
            ("busy", 6.0),
            ("congested", 6.0),
            ("slow", 10.0),
            ("degraded", 10.0),
            ("constrained", 16.0),
            ("protective", 16.0),
            ("unknown", 4.0),
        ):
            with self.subTest(state=state):
                health = {
                    "state": state,
                    "adaptive_tick_seconds": 2.0,
                    "backpressure": {"state": "ok"},
                    "resources": {"state": "ok"},
                }
                self.assertEqual(
                    diagnostics_summary.diagnostic_freshness_deadline(
                        health,
                        1.0,
                    ),
                    expected,
                )

        self.assertEqual(
            diagnostics_summary.diagnostic_freshness_deadline(
                {
                    "state": "ok",
                    "adaptive_tick_seconds": 2.0,
                    "backpressure": {"state": "slow"},
                    "resources": {"state": "constrained"},
                },
                20.0,
            ),
            20.0,
        )
        self.assertEqual(
            diagnostics_summary.diagnostic_freshness_deadline(
                {"adaptive_tick_seconds": -1.0},
                -1.0,
            ),
            0.0,
        )

    def test_health_staleness_uses_positive_tick_time_and_scaled_heartbeat_boundary(self) -> None:
        positive_tick = diagnostics_summary.health_summary(
            _health(last_tick_at=0.5, mainloop_heartbeat_age_s=0.0),
            max_tick_seconds=0.5,
        )
        self.assertFalse(positive_tick.stale)

        scaled_threshold = diagnostics_summary.health_summary(
            _health(last_tick_at=1.0, mainloop_heartbeat_age_s=2.1),
            max_tick_seconds=2.0,
        )
        self.assertFalse(scaled_threshold.stale)

    def test_publication_staleness_handles_zero_heartbeat_and_clamped_deadline(self) -> None:
        registry = self.adapter.publication_registry
        self.register_evcs({"mode": 0}, observed_at=0.0)
        missing_heartbeat = diagnostics_summary.publication_summary(
            registry,
            captured_at=0.0,
            stale_after_seconds=10.0,
        )
        self.assertFalse(missing_heartbeat.registered)
        self.assertEqual(missing_heartbeat.heartbeat_at, 0.0)
        self.assertTrue(missing_heartbeat.stale)

        self.register_evcs({"mode": 0}, observed_at=0.5)
        self.assertFalse(
            diagnostics_summary.publication_summary(
                registry,
                captured_at=0.5,
                stale_after_seconds=10.0,
            ).stale
        )
        self.assertTrue(
            diagnostics_summary.publication_summary(
                registry,
                captured_at=1.0,
                stale_after_seconds=-1.0,
            ).stale
        )

    def test_freshness_deadline_honors_backpressure_and_resource_state_independently(self) -> None:
        for pressure_key in ("backpressure", "resources"):
            with self.subTest(pressure_key=pressure_key):
                health: dict[str, object] = {
                    "state": "ok",
                    "adaptive_tick_seconds": 2.0,
                    "backpressure": {"state": "ok"},
                    "resources": {"state": "ok"},
                }
                health[pressure_key] = {"state": "protective"}
                self.assertEqual(
                    diagnostics_summary.diagnostic_freshness_deadline(health, 1.0),
                    16.0,
                )

    def test_discovery_handles_dormant_dc_pv_and_default_unavailability_reason(self) -> None:
        topology = EnergyTopologySnapshot(
            generation=1,
            captured_at=100.0,
            sources=(
                EnergySourceDescriptor("dc-array", "pv_dc", "offline", ("power",)),
                EnergySourceDescriptor("meter", "grid", "offline", ("power",)),
            ),
        )
        summary = diagnostics_summary.discovery_summary(
            _health(
                dormant_energy_source_ids=["dc-array"],
                energy_source_unavailability_reasons={"meter": ""},
            ),
            topology,
            pending_work=0,
        )

        self.assertEqual(summary.sources[0].availability, "dormant")
        self.assertEqual(summary.sources[0].reason_code, "pv-sleep-confirmed")
        self.assertEqual(summary.sources[1].availability, "unavailable")
        self.assertEqual(summary.sources[1].reason_code, "source-not-advertising")

    def test_health_error_codes_require_each_supported_marker(self) -> None:
        for message, expected in (
            ("request timeout", "timeout"),
            ("service sent no reply", "timeout"),
            ("service noreply", "timeout"),
            ("peer disconnected", "connection-failed"),
        ):
            with self.subTest(message=message):
                summary = diagnostics_summary.health_summary(
                    _health(last_error=message),
                    max_tick_seconds=0.5,
                )
                self.assertEqual(summary.last_error_code, expected)

    def test_dynamic_health_collections_reject_non_text_and_empty_members(self) -> None:
        self.assertEqual(
            diagnostics_summary._text_values(["pv", "", 1, None]),
            frozenset({"pv"}),
        )
        topology = EnergyTopologySnapshot(
            generation=1,
            captured_at=100.0,
            sources=(
                EnergySourceDescriptor("meter", "grid", "offline", ("power",)),
            ),
        )
        summary = diagnostics_summary.discovery_summary(
            _health(energy_source_unavailability_reasons={"meter": 7}),
            topology,
            pending_work=0,
        )
        self.assertEqual(summary.sources[0].reason_code, "source-not-advertising")

    def test_publish_cache_writes_canonical_document_without_changing_health_shape(self) -> None:
        self.register_evcs({"mode": 0, "start_stop": 1, "ac_power_w": 800.0})
        self.adapter._last_tick_at = 100.0
        self.adapter._last_tick_monotonic = 1.0

        self.adapter.io_role.publish_cache()

        diagnostics_path = Path(self.adapter.paths.run_dir) / GATEWAY_DIAGNOSTICS_FILENAME
        self.assertTrue(diagnostics_path.is_file())
        snapshot = GatewayDiagnosticsFileReader(str(diagnostics_path)).read_snapshot()
        self.assertEqual(snapshot.sequence, self.adapter.cache.sequence)
        self.assertEqual(snapshot.sample("operating_mode").value, 0)

        health_payload = json.loads(Path(self.adapter.paths.health_path).read_text(encoding="utf-8"))
        self.assertEqual(set(health_payload), {"schema_version", "sequence", "captured_at", "dbus_health"})
        self.assertNotIn("ev_charger", health_payload)

    def test_private_normalizers_fail_closed(self) -> None:
        self.assertEqual(diagnostics_summary._health_state("invalid"), "unknown")
        self.assertEqual(diagnostics_summary._health_state(1), "unknown")
        self.assertEqual(diagnostics_summary._last_error_code(None), "")
        self.assertEqual(diagnostics_summary._mapping([]), {})
        self.assertEqual(diagnostics_summary._non_negative_int(True), 0)
        self.assertEqual(diagnostics_summary._non_negative_int(-2), 0)
        self.assertEqual(diagnostics_summary._non_negative_float(-2.0), 0.0)
        self.assertEqual(diagnostics_summary._non_negative_float(float("inf")), 0.0)
        self.assertEqual(diagnostics_summary._non_negative_float("2"), 0.0)
        self.assertEqual(diagnostics_values._non_negative_integer("2"), 2)
        with self.assertRaises(TypeError):
            diagnostics_values._non_negative_integer(object())
        self.assertIs(diagnostics_values._boolean(True), True)
        with self.assertRaises(TypeError):
            diagnostics_values._boolean(2)
        with self.assertRaises(TypeError):
            diagnostics_values._finite_float(True)
        with self.assertRaises(ValueError):
            diagnostics_values._finite_float(float("nan"))
        with self.assertRaises(ValueError):
            diagnostics_values._non_negative_integer(-1)

    def test_missing_field_and_charging_fallback_keep_quality_explicit(self) -> None:
        unavailable = diagnostics_values._observed_sample(
            "operating_mode",
            None,
            diagnostics_values._mode,
            10.0,
            2.0,
        )
        self.assertEqual(unavailable.status, "unavailable")
        self.assertEqual(unavailable.reason_code, "field-unavailable")

        fallback = diagnostics_values._charging_enabled_sample(
            lambda field: PublicationFieldObservation(1, 8.0, 9.0, 9.5)
            if field == "enable"
            else None,
            10.0,
            2.0,
        )
        self.assertIs(fallback.value, True)
        self.assertEqual(fallback.status, "fresh")

        unknown_source = diagnostics_values._runtime_overrides_source(
            diagnostics_values._unknown_sample("runtime_overrides_active")
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
        self.assertEqual(refreshed, PublicationFieldObservation(1, 100.0, 110.0, 110.0))
        self.assertEqual(first.changed_at, 100.0)
        self.assertEqual(first.observed_at, 100.0)
        self.assertIsNone(self.adapter.publication_registry.evcs_field_observation("not-a-field"))

    def test_auto_diagnostics_remain_applicable_and_use_field_confirmation(self) -> None:
        self.register_evcs(
            {
                "mode": 1,
                "auto_decision_reason": "surplus",
                "auto_decision_state": "charging",
                "auto_health": "healthy",
                "auto_runtime_overrides_active": 0,
            }
        )
        snapshot = self.adapter.diagnostics_role.gateway_diagnostics_snapshot(
            health=_health(),
            topology=_topology(),
            captured_at=100.5,
        )
        self.assertEqual(snapshot.sample("decision_state").status, "fresh")
        self.assertEqual(snapshot.sample("decision_state").applicability, "applicable")
        self.assertEqual(snapshot.sample("decision_state").changed_at, 100.0)
        self.assertEqual(snapshot.sample("decision_state").confirmed_at, 100.0)


if __name__ == "__main__":
    unittest.main()

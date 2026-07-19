# SPDX-License-Identifier: GPL-3.0-or-later
"""Edge-case behavior contracts for the gateway-backed input layer."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, call, patch

from tests.input_storage_support_cases import StorageSupportMutationContractTests

from tests.support.auto_input_supervisor import (
    AutoInputSupervisorServiceFake,
    HelperProcessFake,
    SnapshotRefreshFake,
    valid_snapshot,
)
from tests.support.dbus_inputs import (
    DbusInputServiceFake,
    EnergyServiceResolverFake,
    GatewayReaderFake,
    SourceHealthFake,
)
from venus_evcharger.energy import EnergyClusterSnapshot, EnergySourceDefinition, EnergySourceSnapshot
from venus_evcharger.inputs import storage as storage_module
from venus_evcharger.inputs.dbus import DbusInputController
from venus_evcharger.inputs.storage import StorageInputReader
from venus_evcharger.inputs.storage_support import EnergyServiceResolver, _energy_cache_entry
from venus_evcharger.inputs.supervisor import AutoInputSupervisor
from venus_evcharger.inputs.supervisor_process import AutoInputProcessLifecycle
from venus_evcharger.inputs.supervisor_snapshot_runtime import AutoInputSnapshotRuntime
from venus_evcharger.inputs.supervisor_snapshot_validation import AutoInputSnapshotValidator
from venus_evcharger.ports.dbus import DbusInputPort

__all__ = ("StorageSupportMutationContractTests",)


def _storage_reader(
    service: DbusInputServiceFake,
    gateway: GatewayReaderFake,
    resolver: EnergyServiceResolverFake,
) -> tuple[StorageInputReader, SourceHealthFake]:
    health = SourceHealthFake()
    return StorageInputReader(DbusInputPort(service), gateway, health, resolver), health


class DbusInputEdgeContractTests(unittest.TestCase):
    def test_controller_delegates_battery_snapshot_to_storage_component(self) -> None:
        controller = DbusInputController(DbusInputPort(DbusInputServiceFake()))
        expected = {"battery_soc": 42.0}

        with patch.object(controller.storage, "get_battery_snapshot", return_value=expected) as get_snapshot:
            self.assertEqual(controller.get_battery_snapshot(), expected)

        get_snapshot.assert_called_once_with()

    def test_optional_energy_text_honors_empty_skipped_and_normalized_values(self) -> None:
        source = EnergySourceDefinition("primary", service_name="battery.a")
        service = DbusInputServiceFake(auto_energy_sources=(source,))
        gateway = GatewayReaderFake(
            raw_results={
                ("battery.a", "/Missing"): [None],
                ("battery.a", "/Mode"): [2],
            }
        )
        resolver = EnergyServiceResolverFake(
            source,
            {"primary": "battery.a"},
            skipped_paths={("battery.a", "/Skipped")},
        )
        reader, _ = _storage_reader(service, gateway, resolver)

        self.assertEqual(reader._read_optional_energy_text("battery.a", ""), "")
        self.assertEqual(reader._read_optional_energy_text("battery.a", "/Skipped"), "")
        self.assertEqual(reader._read_optional_energy_text("battery.a", "/Missing"), "")
        self.assertEqual(reader._read_optional_energy_text("battery.a", "/Mode"), "2")
        self.assertEqual(gateway.raw_reads, [("battery.a", "/Missing"), ("battery.a", "/Mode")])

    def test_secondary_source_read_errors_yield_offline_snapshot_and_invalidate_cache(self) -> None:
        primary = EnergySourceDefinition("primary", service_name="battery.a")
        secondary = EnergySourceDefinition("secondary", service_name="hybrid.a")
        service = DbusInputServiceFake(auto_energy_sources=(primary, secondary))
        gateway = GatewayReaderFake(raw_results={("hybrid.a", "/Soc"): [OSError("offline")]})
        resolver = EnergyServiceResolverFake(
            primary,
            {"primary": "battery.a", "secondary": "hybrid.a"},
        )
        reader, _ = _storage_reader(service, gateway, resolver)

        snapshot = reader._dbus_energy_source_snapshot(secondary, 100.0)

        self.assertEqual(snapshot.source_id, "secondary")
        self.assertEqual(snapshot.service_name, "hybrid.a")
        self.assertFalse(snapshot.online)
        self.assertEqual(snapshot.confidence, 0.0)
        self.assertEqual(resolver.invalidations, 0)
        self.assertEqual(resolver.energy_invalidations, [("secondary", "hybrid.a")])

    def test_source_snapshot_rejects_out_of_range_soc(self) -> None:
        source = EnergySourceDefinition("primary", service_name="battery.a")
        service = DbusInputServiceFake(auto_energy_sources=(source,))
        gateway = GatewayReaderFake(raw_results={("battery.a", "/Soc"): [101.0, 0.0, 100.0]})
        resolver = EnergyServiceResolverFake(source, {"primary": "battery.a"})
        reader, _ = _storage_reader(service, gateway, resolver)

        snapshot = reader._dbus_energy_source_snapshot(source, 100.0)

        self.assertIsNone(snapshot.soc)
        self.assertTrue(snapshot.online)
        self.assertEqual(reader._dbus_energy_source_snapshot(source, 101.0).soc, 0.0)
        self.assertEqual(reader._dbus_energy_source_snapshot(source, 102.0).soc, 100.0)

    def test_snapshot_sources_fall_back_to_primary_resolver_contract(self) -> None:
        primary = EnergySourceDefinition("primary", service_name="battery.a")
        service = DbusInputServiceFake()
        resolver = EnergyServiceResolverFake(primary, {"primary": "battery.a"})
        reader, _ = _storage_reader(service, GatewayReaderFake(), resolver)

        self.assertEqual(reader._battery_snapshot_sources(), (primary,))


class StorageInputMutationContractTests(unittest.TestCase):
    def test_optional_reads_forward_the_exact_introspection_priority(self) -> None:
        source = EnergySourceDefinition("primary", service_name="battery.a")
        resolver = EnergyServiceResolverFake(source, {"primary": "battery.a"})
        reader, _ = _storage_reader(
            DbusInputServiceFake(auto_energy_sources=(source,)),
            GatewayReaderFake(raw_results={("battery.a", "/Soc"): [52.0], ("battery.a", "/Mode"): [1]}),
            resolver,
        )
        with patch.object(resolver, "introspection_says_skip", wraps=resolver.introspection_says_skip) as skip:
            self.assertEqual(reader._read_optional_energy_value("battery.a", "/Soc"), 52.0)
            self.assertEqual(reader._read_optional_energy_text("battery.a", "/Mode"), "1")
        self.assertEqual(
            skip.call_args_list,
            [call("battery.a", "/Soc", priority=85), call("battery.a", "/Mode", priority=85)],
        )

    def test_primary_read_failure_re_resolves_and_populates_the_retried_snapshot(self) -> None:
        source = EnergySourceDefinition(
            "primary",
            role="battery",
            soc_path="/Soc",
            usable_capacity_wh=9000.0,
            battery_power_path="/Power",
            ac_power_path="/Ac",
            pv_power_path="/Pv",
            grid_interaction_path="/Grid",
            operating_mode_path="/Mode",
        )
        resolver = MagicMock()
        resolver.resolve_energy_source_service.side_effect = ["battery.old", "battery.new"]
        resolver.primary_energy_source.return_value = source
        resolver.introspection_says_skip.return_value = False
        gateway = GatewayReaderFake(
            raw_results={
                ("battery.old", "/Soc"): [OSError("old service gone")],
                ("battery.new", "/Soc"): [51.0],
                ("battery.new", "/Power"): [-601.0],
                ("battery.new", "/Ac"): [602.0],
                ("battery.new", "/Pv"): [603.0],
                ("battery.new", "/Grid"): [-604.0],
                ("battery.new", "/Mode"): [2],
            }
        )
        reader = StorageInputReader(DbusInputPort(DbusInputServiceFake()), gateway, SourceHealthFake(), resolver)
        snapshot = reader._dbus_energy_source_snapshot(source, 123.0)
        self.assertEqual(
            snapshot,
            EnergySourceSnapshot(
                "primary",
                "battery",
                "battery.new",
                soc=51.0,
                usable_capacity_wh=9000.0,
                net_battery_power_w=-601.0,
                ac_power_w=602.0,
                pv_input_power_w=603.0,
                grid_interaction_w=-604.0,
                operating_mode="2",
                online=True,
                confidence=1.0,
                captured_at=123.0,
            ),
        )
        self.assertEqual(resolver.resolve_energy_source_service.call_args_list, [call(source), call(source)])
        resolver.invalidate_auto_battery_service.assert_called_once_with()
        self.assertEqual(gateway.raw_reads[0], ("battery.old", "/Soc"))
        self.assertEqual(gateway.raw_reads[1:], [("battery.new", path) for path in ("/Soc", "/Power", "/Ac", "/Pv", "/Grid", "/Mode")])

    def test_soc_validation_requires_effective_soc(self) -> None:
        StorageInputReader._battery_snapshot_validate_soc(0.0)
        with self.assertRaisesRegex(TypeError, "Battery SOC is not numeric"):
            StorageInputReader._battery_snapshot_validate_soc(None)

    def test_learning_bundle_and_control_forward_exact_domain_inputs(self) -> None:
        source = EnergySourceSnapshot("a", "battery", "battery.a", soc=50.0)
        definition = EnergySourceDefinition("a", service_name="battery.a")
        cluster = EnergyClusterSnapshot(
            combined_charge_power_w=1.0,
            combined_discharge_power_w=2.0,
            combined_charge_limit_power_w=3.0,
            combined_discharge_limit_power_w=4.0,
            combined_grid_interaction_w=5.0,
            sources=(source,),
        )
        service = DbusInputServiceFake(_last_energy_learning_profiles={"raw": "profile"})
        reader, _ = _storage_reader(service, GatewayReaderFake(), EnergyServiceResolverFake(definition, {"a": "battery.a"}))
        with (
            patch.object(storage_module, "learning_profiles", return_value={"old": "profile"}) as normalize,
            patch.object(storage_module, "update_energy_learning_profiles", return_value={"new": "profile"}) as update,
            patch.object(storage_module, "summarize_energy_learning_profiles", return_value={"summary": 6}) as summarize,
            patch.object(storage_module, "derive_discharge_balance_metrics", return_value={"balance": 7}) as balance,
            patch.object(storage_module, "derive_energy_forecast", return_value={"forecast": 8}) as forecast,
            patch.object(storage_module, "derive_discharge_control_metrics", return_value={"control": 9}) as control,
        ):
            self.assertEqual(
                reader._battery_snapshot_learning_bundle(cluster, 10.0),
                ({"new": "profile"}, {"summary": 6}, {"balance": 7}, {"forecast": 8}),
            )
            self.assertEqual(reader._battery_snapshot_discharge_control(cluster, (definition,)), {"control": 9})
        normalize.assert_called_once_with({"raw": "profile"})
        update.assert_called_once_with({"old": "profile"}, (source,), 10.0)
        summarize.assert_called_once_with({"new": "profile"})
        balance.assert_called_once_with((source,), {"new": "profile"})
        forecast.assert_called_once_with(
            {
                "battery_combined_charge_power_w": 1.0,
                "battery_combined_discharge_power_w": 2.0,
                "BATTERY_COMBINED_CHARGE_LIMIT_POWER_W": 3.0,
                "battery_combined_discharge_limit_power_w": 4.0,
                "battery_combined_grid_interaction_w": 5.0,
            },
            {"summary": 6},
        )
        control.assert_called_once_with((source,), {"a": definition})
        self.assertEqual(service._last_energy_learning_profiles, {"raw": "profile"})

    def test_source_payload_merges_balance_and_control_by_source_id(self) -> None:
        source_a = EnergySourceSnapshot("a", "battery", "battery.a", soc=50.0)
        source_b = EnergySourceSnapshot("b", "inverter", "inverter.b", soc=60.0)
        payloads = StorageInputReader._battery_snapshot_source_payloads(
            EnergyClusterSnapshot(sources=(source_a, source_b)),
            {"sources": {"a": {"balance_marker": 1}, "b": {"balance_marker": 2}}},
            {"sources": {"a": {"control_marker": 3}, "b": {"control_marker": 4}}},
        )
        self.assertEqual([(item["source_id"], item["balance_marker"], item["control_marker"]) for item in payloads], [("a", 1, 3), ("b", 2, 4)])

    def test_snapshot_payload_defaults_missing_balance_and_control_counts_to_zero(self) -> None:
        source = EnergySourceDefinition("a")
        reader, _ = _storage_reader(DbusInputServiceFake(), GatewayReaderFake(), EnergyServiceResolverFake(source, {"a": "battery.a"}))
        payload = reader._battery_snapshot_payload(
            None,
            EnergyClusterSnapshot(),
            {
                "battery_headroom_charge_w": None,
                "battery_headroom_discharge_w": None,
                "expected_near_term_export_w": None,
                "expected_near_term_import_w": None,
            },
            {},
            {},
            [],
        )
        count_keys = [key for key in payload if key.endswith("_count")]
        self.assertEqual(count_keys, [
            "battery_discharge_balance_eligible_source_count", "battery_discharge_balance_active_source_count",
            "battery_discharge_balance_control_candidate_count", "battery_discharge_balance_control_ready_count",
            "battery_discharge_balance_supported_control_source_count", "battery_discharge_balance_experimental_control_source_count",
            "battery_source_count", "battery_online_source_count", "battery_valid_soc_source_count",
            "battery_battery_source_count", "battery_hybrid_inverter_source_count", "battery_inverter_source_count",
        ])
        self.assertEqual([payload[key] for key in count_keys], [0] * len(count_keys))

    def test_successful_snapshot_orchestrates_each_stage_with_the_same_inputs(self) -> None:
        source = EnergySourceDefinition("a")
        cluster = EnergyClusterSnapshot(effective_soc=44.0)
        service = DbusInputServiceFake()
        reader, health = _storage_reader(service, GatewayReaderFake(), EnergyServiceResolverFake(source, {"a": "battery.a"}))
        with (
            patch.object(reader, "_battery_snapshot_cluster", return_value=(cluster, (source,))) as build_cluster,
            patch.object(reader, "_battery_snapshot_effective_soc", return_value=44.0) as effective_soc,
            patch.object(reader, "_battery_snapshot_validate_soc") as validate,
            patch.object(
                reader,
                "_battery_snapshot_learning_bundle",
                return_value=({"updated": "profile"}, {}, {"balance": 1}, {"forecast": 2}),
            ) as learn,
            patch.object(reader, "_battery_snapshot_discharge_control", return_value={"control": 3}) as control,
            patch.object(reader, "_battery_snapshot_source_payloads", return_value=[{"source_id": "a"}]) as source_payloads,
            patch.object(reader, "_battery_snapshot_payload", return_value={"battery_soc": 44.0}) as payload,
        ):
            self.assertEqual(reader._successful_battery_snapshot_payload(123.0), {"battery_soc": 44.0})
        build_cluster.assert_called_once_with(123.0)
        effective_soc.assert_called_once_with(cluster)
        validate.assert_called_once_with(44.0)
        learn.assert_called_once_with(cluster, 123.0)
        control.assert_called_once_with(cluster, (source,))
        source_payloads.assert_called_once_with(cluster, {"balance": 1}, {"control": 3})
        payload.assert_called_once_with(
            44.0,
            cluster,
            {"forecast": 2},
            {"balance": 1},
            {"control": 3},
            [{"source_id": "a"}],
            {"updated": "profile"},
        )
        self.assertEqual(health.recoveries, [("battery", "Battery SOC readings recovered", ())])
        self.assertEqual(service._last_energy_learning_profiles, {"updated": "profile"})
        self.assertEqual(service._last_energy_cluster, {"battery_soc": 44.0})
        service.auto_battery_service = "battery.explicit"
        error = ValueError("unreadable")
        reader._failed_battery_snapshot_payload(124.0, error)
        self.assertEqual(health.failures[-1][5], ("battery.explicit", "/Soc", error))

    def test_cluster_builder_passes_capture_time_to_every_source(self) -> None:
        source = EnergySourceDefinition("a")
        reader, _ = _storage_reader(DbusInputServiceFake(auto_energy_sources=(source,)), GatewayReaderFake(), EnergyServiceResolverFake(source, {"a": "battery.a"}))
        snapshot = EnergySourceSnapshot("a", "battery", "battery.a")
        with (
            patch.object(storage_module, "read_energy_source_snapshot", return_value=snapshot) as read,
            patch.object(storage_module, "aggregate_energy_sources", return_value=EnergyClusterSnapshot()) as aggregate,
        ):
            reader._battery_snapshot_cluster(321.0)
        read.assert_called_once_with(reader, source, 321.0)
        aggregate.assert_called_once_with([snapshot])


class EnergyServiceResolverEdgeContractTests(unittest.TestCase):
    def test_malformed_boolean_energy_cache_timestamp_is_rejected(self) -> None:
        service = DbusInputServiceFake(
            _resolved_auto_energy_services={"secondary": "hybrid.a"},
        )
        object.__setattr__(service, "_auto_energy_last_scan", {"secondary": True})

        self.assertIsNone(_energy_cache_entry(service, "secondary"))

    def test_service_probes_request_introspection_after_read_errors(self) -> None:
        service = DbusInputServiceFake()
        gateway = GatewayReaderFake(
            raw_results={
                ("battery.a", "/Soc"): [OSError("soc offline")],
                ("battery.a", "/Mode"): [RuntimeError("mode offline")],
            }
        )
        resolver = EnergyServiceResolver(DbusInputPort(service), gateway)

        with (
            patch.object(resolver, "introspection_says_skip", return_value=False),
            patch.object(resolver, "_request_introspection") as request_introspection,
        ):
            self.assertFalse(resolver._battery_service_has_soc("battery.a"))
            self.assertFalse(resolver._energy_service_has_readable_field("battery.a", "/Mode"))

        self.assertEqual(request_introspection.call_count, 2)
        self.assertEqual(request_introspection.call_args_list[0].kwargs["reason"], "battery SOC probe failed")
        self.assertEqual(
            request_introspection.call_args_list[1].kwargs["reason"],
            "energy-source field probe failed",
        )

    def test_introspection_skip_short_circuits_battery_and_field_probes(self) -> None:
        service = DbusInputServiceFake()
        gateway = GatewayReaderFake()
        resolver = EnergyServiceResolver(DbusInputPort(service), gateway)

        with patch.object(resolver, "introspection_says_skip", return_value=True):
            self.assertFalse(resolver._battery_service_has_soc("battery.a"))
            self.assertFalse(resolver._energy_service_has_readable_field("battery.a", "/Mode"))

        self.assertEqual(gateway.raw_reads, [])

    def test_introspection_skip_forwards_one_typed_refresh_request(self) -> None:
        service = DbusInputServiceFake()
        port = DbusInputPort(service)
        resolver = EnergyServiceResolver(port, GatewayReaderFake())

        with (
            patch.object(port, "path_unusable", return_value=(True, "missing path")),
            patch.object(port, "request_introspection", return_value=True) as request_introspection,
        ):
            self.assertTrue(resolver.introspection_says_skip("battery.a", "/Soc", priority=91))

        request_introspection.assert_called_once_with(
            "battery.a",
            "/Soc",
            priority=91,
            reason="known-unusable input path",
            source="evcharger-inputs",
        )

    def test_cache_validation_rejects_invalid_and_expired_entries(self) -> None:
        service = DbusInputServiceFake(
            _resolved_auto_battery_service="battery.cached",
            _auto_battery_last_scan=99.0,
            _resolved_auto_energy_services={"secondary": "hybrid.cached"},
            _auto_energy_last_scan={"secondary": 1.0},
        )
        resolver = EnergyServiceResolver(DbusInputPort(service), GatewayReaderFake())

        object.__setattr__(service, "_auto_battery_last_scan", True)
        self.assertIsNone(resolver._cached_auto_battery_service(100.0))
        service._auto_battery_last_scan = 1.0
        self.assertIsNone(resolver._cached_auto_battery_service(100.0))
        self.assertIsNone(resolver._energy_cache_valid("secondary", 100.0))

    def test_primary_configured_and_cached_resolution_paths_are_distinct(self) -> None:
        primary = EnergySourceDefinition("primary", service_name="battery.fixed")
        secondary = EnergySourceDefinition("secondary", service_name="hybrid.fixed")
        service = DbusInputServiceFake(auto_energy_sources=(primary, secondary))
        gateway = GatewayReaderFake(
            raw_results={
                ("battery.fixed", "/Soc"): [51.0],
                ("hybrid.fixed", "/Soc"): [61.0],
            }
        )
        resolver = EnergyServiceResolver(DbusInputPort(service), gateway)

        with patch("venus_evcharger.inputs.storage_support.time.time", return_value=100.0):
            self.assertEqual(resolver.resolve_energy_source_service(primary), "battery.fixed")
            self.assertEqual(resolver.resolve_energy_source_service(secondary), "hybrid.fixed")
        self.assertEqual(service._resolved_auto_energy_services["secondary"], "hybrid.fixed")

        cached_service = DbusInputServiceFake(
            auto_battery_service_prefix="battery.",
            _resolved_auto_battery_service="battery.cached",
            _auto_battery_last_scan=99.0,
        )
        cached_resolver = EnergyServiceResolver(DbusInputPort(cached_service), GatewayReaderFake())
        with patch("venus_evcharger.inputs.storage_support.time.time", return_value=100.0):
            self.assertEqual(cached_resolver.resolve_auto_battery_service(), "battery.cached")

    def test_primary_scan_reports_missing_prefix_and_missing_service_separately(self) -> None:
        no_prefix = DbusInputServiceFake(auto_battery_service_prefix="")
        no_prefix_resolver = EnergyServiceResolver(DbusInputPort(no_prefix), GatewayReaderFake())
        with self.assertRaisesRegex(ValueError, "No DBus service prefix"):
            no_prefix_resolver.resolve_auto_battery_service()

        no_match = DbusInputServiceFake(auto_battery_service_prefix="battery.")
        no_match_resolver = EnergyServiceResolver(DbusInputPort(no_match), GatewayReaderFake(services=[]))
        with self.assertRaisesRegex(ValueError, "No DBus service found with prefix"):
            no_match_resolver.resolve_auto_battery_service()


class AutoInputSupervisorEdgeContractTests(unittest.TestCase):
    def test_facade_delegates_stop_and_spawn_to_process_lifecycle(self) -> None:
        supervisor = AutoInputSupervisor(
            AutoInputSupervisorServiceFake(),
            config_path="/config.ini",
            helper_path="/helper.py",
        )

        with (
            patch.object(supervisor.process_lifecycle, "stop_helper") as stop_helper,
            patch.object(supervisor.process_lifecycle, "spawn_helper") as spawn_helper,
        ):
            supervisor.stop_helper(force=True)
            supervisor.spawn_helper(123.0)

        stop_helper.assert_called_once_with(True)
        spawn_helper.assert_called_once_with(123.0)

    def test_helper_snapshot_age_is_unknown_before_start_or_first_snapshot(self) -> None:
        service = AutoInputSupervisorServiceFake(
            _auto_input_helper_last_start_at=0.0,
            _auto_input_snapshot_last_seen=None,
            _auto_input_snapshot_seen_for_current_helper=False,
        )
        lifecycle = AutoInputProcessLifecycle(
            service,
            SnapshotRefreshFake(),
            config_path="/config.ini",
            helper_path="/helper.py",
        )

        self.assertIsNone(lifecycle._helper_snapshot_age(100.0))

    def test_stale_helper_waits_for_restart_grace_before_force_kill(self) -> None:
        process = HelperProcessFake()
        service = AutoInputSupervisorServiceFake(
            _auto_input_helper_process=process,
            _auto_input_helper_restart_requested_at=98.0,
        )
        lifecycle = AutoInputProcessLifecycle(
            service,
            SnapshotRefreshFake(),
            config_path="/config.ini",
            helper_path="/helper.py",
        )

        self.assertTrue(lifecycle._handle_stale_running_helper(process, 100.0, 30.0))
        self.assertEqual(process.terminate_calls, 0)
        self.assertEqual(process.kill_calls, 0)

    def test_snapshot_with_no_freshness_timestamp_is_not_future(self) -> None:
        service = AutoInputSupervisorServiceFake()
        validator = AutoInputSnapshotValidator(service, AutoInputSupervisor.SCHEMA)
        runtime = AutoInputSnapshotRuntime(service, AutoInputSupervisor.SCHEMA, validator)

        self.assertTrue(runtime._snapshot_freshness_not_future("/tmp/snapshot.json", None, 100.0))
        self.assertEqual(service.runtime.warnings, [])

    def test_snapshot_requires_numeric_captured_and_heartbeat_timestamps(self) -> None:
        service = AutoInputSupervisorServiceFake()
        validator = AutoInputSnapshotValidator(service, AutoInputSupervisor.SCHEMA)

        self.assertIsNone(validator.validate("/tmp/snapshot.json", valid_snapshot(captured_at=None)))
        self.assertEqual(service.runtime.warnings[-1][0], "auto-input-helper-schema-invalid")
        self.assertIn("requires numeric captured_at and heartbeat_at", service.runtime.warnings[-1][2])


if __name__ == "__main__":
    unittest.main()

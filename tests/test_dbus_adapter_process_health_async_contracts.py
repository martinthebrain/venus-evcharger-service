# SPDX-License-Identifier: GPL-3.0-or-later
"""Exact process-health contracts for the asynchronous DBus gateway."""

from __future__ import annotations

from contextlib import ExitStack
from unittest.mock import MagicMock, call, patch

from tests.support.dbus_gateway_adapter_harness import (
    DbusAdapter,
    GatewayAdapterContractCase,
    Path,
    gateway_paths,
    process_health_module,
    process_health_regulation_module,
    tempfile,
)
from venus_evcharger.dbus_adapter.health.slo import SloThresholds
from venus_evcharger.dbus_adapter.process.health_regulation import GatewayControlSnapshot
from venus_evcharger.dbus_adapter.read.pv_dormancy import PvDormancyEvidence


def _adapter(temp_dir: str) -> DbusAdapter:
    config_path = Path(temp_dir) / "config.ini"
    config_path.write_text("[DEFAULT]\n", encoding="utf-8")
    return DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))


def _thresholds() -> SloThresholds:
    return SloThresholds(
        gui_max_age_seconds=2.0,
        core_read_max_age_seconds=5.0,
        queue_max_age_seconds=11.0,
        mainloop_gap_max_ms=450.0,
        publication_scheduler_tolerance_seconds=0.25,
    )


def _patch_result(stack: ExitStack, name: str, value: object) -> MagicMock:
    mock = MagicMock(return_value=value)
    stack.enter_context(patch.object(process_health_module, name, mock))
    return mock


class DbusAdapterProcessHealthAsyncContracts(GatewayAdapterContractCase):
    """Pin every health boundary that controls asynchronous gateway work."""

    def test_tick_control_snapshot_reuses_only_one_health_heartbeat_interval(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            adapter = _adapter(temp_dir)
            with (
                patch.object(process_health_module.time, "monotonic", return_value=10.0),
                patch.object(process_health_module.time, "time", return_value=100.0),
            ):
                first = adapter.health_role.control_snapshot()

            with (
                patch.object(
                    process_health_module.time,
                    "monotonic",
                    side_effect=(10.999, 11.0),
                ),
                patch.object(
                    adapter.health_role,
                    "control_snapshot",
                    MagicMock(return_value=first),
                ) as rebuild,
            ):
                self.assertIs(adapter.health_role.control_snapshot_for_tick(), first)
                self.assertIs(adapter.health_role.control_snapshot_for_tick(), first)

            rebuild.assert_called_once_with()

    def test_control_snapshot_composes_exact_sources_and_time_domains(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            adapter = _adapter(temp_dir)
            physical = [
                ("physical-a.json", {"queue_class": "remote-write"}),
                ("physical-b.json", {"queue_class": "diagnostic"}),
            ]
            effective = [("effective.json", {"queue_class": "remote-write"})]
            core_pending = [("core.json", {"command": "set-mode"})]
            pending_snapshot = MagicMock()
            pending_snapshot.physical_list.return_value = physical
            pending_snapshot.effective_list.return_value = effective
            resources = {"state": "resource-busy", "available_mb": 17.0}
            eventloop = {
                "max_glib_callback_lateness_ms_60s": 12.5,
                "max_tick_gap_ms_60s": 91.0,
                "max_blocking_time_ms_60s": 33.5,
                "max_tick_duration_ms_60s": 92.0,
                "sample_count_60s": 7,
            }
            scheduler_health = {
                "processed_commands_60s": 9,
                "last_processed_at": 1900.0,
            }
            queue_metrics = {
                "oldest_slo_command_age_s": 31.25,
                "queue_marker": "queue-health",
            }
            freshness = {
                "grid_power_w_age_s": 13.5,
                "freshness_marker": "cache",
            }
            slo = {"state": "violated", "violated": ["queue_age_ok"]}
            circuit_health = {"state": "degraded", "circuit_marker": 41}
            backpressure = {"state": "slow", "reason": "queue-age"}
            queue_classes = {"remote-write": {"pending": 1}}
            broker_health = {"state": "busy", "operation_id": 73}
            evidence = (
                PvDormancyEvidence("pv-source-a", "explicit-dormant-state", 1700.0),
                PvDormancyEvidence("pv-source-b", "explicit-dormant-state", 1800.0),
            )
            unavailable = {
                "pv-source-a": "pv-sleep-confirmed",
                "pv-source-c": "source-not-advertising",
            }
            adapter._last_tick_at = 1980.0
            adapter._last_tick_monotonic = 990.0
            adapter._last_tick_duration_ms = 44.5
            adapter.tick_seconds = 0.4
            adapter.min_tick_seconds = 0.1
            adapter.max_tick_seconds = 2.5
            adapter.discovery.last_success_at = 1880.0
            adapter.discovery.last_error = "temporary discovery error"
            adapter.discovery.next_scan_at = 2040.0
            adapter.discovery.next_scan_monotonic = 1025.0
            adapter.discovery.active_interval_seconds = 45.0
            adapter.write_scheduler.pending_snapshot = MagicMock(return_value=pending_snapshot)
            adapter.core_command_mailbox.load_pending = MagicMock(return_value=core_pending)
            adapter.resource_monitor.snapshot = MagicMock(return_value=resources)
            adapter.tick_health.snapshot = MagicMock(return_value=eventloop)
            adapter.write_scheduler.health = MagicMock(return_value=scheduler_health)
            adapter.health_role.cache_freshness_snapshot = MagicMock(return_value=freshness)
            adapter.health_role.slo_thresholds = MagicMock(return_value=_thresholds())
            adapter.health_role.slo_snapshot = MagicMock(return_value=slo)
            adapter.circuit.health = MagicMock(return_value=circuit_health)
            adapter.energy_discovery.dormant_evidence = MagicMock(return_value=evidence)
            adapter.energy_discovery.source_unavailability_reasons = MagicMock(return_value=unavailable)
            adapter.operation_broker.health = MagicMock(return_value=broker_health)

            with ExitStack() as stack:
                stack.enter_context(patch.object(process_health_module.time, "time", return_value=2000.0))
                stack.enter_context(patch.object(process_health_module.time, "monotonic", return_value=1000.0))
                queue_health = _patch_result(stack, "queue_health", queue_metrics)
                queue_class_health = _patch_result(stack, "queue_class_health", queue_classes)
                backpressure_snapshot = _patch_result(stack, "backpressure_snapshot", backpressure)
                runtime_pressure_state = _patch_result(stack, "runtime_pressure_state", "slow")
                operational_health_state = _patch_result(stack, "operational_health_state", "degraded")
                performance_health_state = _patch_result(stack, "performance_health_state", "protective")
                effective_gui_max_age_seconds = _patch_result(stack, "effective_gui_max_age_seconds", 8.25)
                max_core_read_age = _patch_result(stack, "max_core_read_age", 13.5)
                stale_core_read_keys = _patch_result(stack, "stale_core_read_keys", {"pv_power_w", "grid_power_w"})
                snapshot = adapter.health_role.control_snapshot()

            self.assertEqual(snapshot.captured_at, 2000.0)
            self.assertEqual(snapshot.monotonic_at, 1000.0)
            self.assertEqual(snapshot.queue_age_seconds, 31.25)
            self.assertEqual(snapshot.core_read_age_seconds, 13.5)
            self.assertEqual(snapshot.eventloop_gap_ms, 12.5)
            self.assertEqual(snapshot.eventloop_max_duration_ms, 33.5)
            self.assertEqual(snapshot.resource_state, "resource-busy")
            self.assertEqual(snapshot.pressure_state, "slow")
            self.assertEqual(snapshot.stale_core_reads, ("grid_power_w", "pv_power_w"))
            self.assertEqual(snapshot.critical_read_operations, 3)
            self.assertEqual(snapshot.critical_queue_operations, 2)
            self.assertEqual(snapshot.operation_p95_ms, 0.0)
            self.assertEqual(
                snapshot.health,
                {
                    "state": "protective",
                    "circuit_marker": 41,
                    "operational_state": "degraded",
                    "performance_state": "protective",
                    "resource_state": "resource-busy",
                    "resource_evidence": None,
                    "protective_cause": "recovery-hold",
                    "state_changed_at": 2000.0,
                    "state_recovery_pending": False,
                    "pending_command_count": 1,
                    "physical_command_count": 2,
                    "core_command_count": 1,
                    "registered_path_count": 0,
                    "last_tick_at": 1980.0,
                    "tick_duration_ms": 44.5,
                    "discovery_last_success_at": 1880.0,
                    "discovery_last_error": "temporary discovery error",
                    "discovery_next_scan_at": 2040.0,
                    "discovery_next_scan_in_s": 25.0,
                    "discovery_active_interval_s": 45.0,
                    "dormant_energy_source_ids": ["pv-source-a", "pv-source-b"],
                    "dormant_energy_source_evidence": [
                        {"source_id": "pv-source-a", "reason": "explicit-dormant-state", "observed_at": 1700.0},
                        {"source_id": "pv-source-b", "reason": "explicit-dormant-state", "observed_at": 1800.0},
                    ],
                    "energy_source_unavailability_reasons": unavailable,
                    "mainloop_heartbeat_age_s": 10.0,
                    "queues": queue_metrics,
                    "queue_classes": queue_classes,
                    "write_scheduler": scheduler_health,
                    "async_dbus": broker_health,
                    "cache_freshness": freshness,
                    "slo": slo,
                    "backpressure": backpressure,
                    "resources": resources,
                    "publication_freshness_deadline_s": 8.25,
                    "adaptive_tick_seconds": 0.4,
                    "min_tick_seconds": 0.1,
                    "max_tick_seconds": 2.5,
                    "tick_demand": {
                        "critical_read_operations": 3,
                        "critical_queue_operations": 2,
                        "operation_p95_ms": 0.0,
                    },
                    "eventloop": {
                        "last_tick_at": 1980.0,
                        "tick_duration_ms": 44.5,
                        "mainloop_heartbeat_age_s": 10.0,
                        **eventloop,
                    },
                },
            )
            adapter.write_scheduler.pending_snapshot.assert_called_once_with()
            pending_snapshot.physical_list.assert_called_once_with()
            pending_snapshot.effective_list.assert_called_once_with()
            adapter.core_command_mailbox.load_pending.assert_called_once_with()
            adapter.resource_monitor.snapshot.assert_called_once_with()
            self.assertIs(adapter._last_resource_snapshot, resources)
            adapter.tick_health.snapshot.assert_called_once_with(now=1000.0)
            adapter.write_scheduler.health.assert_called_once_with(now=2000.0)
            queue_health.assert_called_once_with(
                effective,
                core_pending,
                2000.0,
                physical_count=2,
                write_scheduler_health=scheduler_health,
            )
            adapter.health_role.cache_freshness_snapshot.assert_called_once_with(2000.0)
            adapter.health_role.slo_thresholds.assert_called_once_with()
            adapter.health_role.slo_snapshot.assert_called_once_with(
                queue_health=queue_metrics,
                cache_freshness=freshness,
                current_monotonic=1000.0,
                eventloop=eventloop,
                thresholds=_thresholds(),
            )
            adapter.circuit.health.assert_called_once_with()
            backpressure_snapshot.assert_called_once_with(
                circuit_state="degraded",
                queue_health=queue_metrics,
                slo=slo,
                queue_max_age_seconds=adapter.slo_queue_max_age_seconds,
            )
            runtime_pressure_state.assert_called_once_with("resource-busy", "slow")
            operational_health_state.assert_called_once_with("degraded")
            performance_health_state.assert_called_once_with(
                slo_state="violated",
                resource_state="resource-busy",
                backpressure_state="slow",
                resource_protective=False,
            )
            adapter.energy_discovery.dormant_evidence.assert_called_once_with()
            adapter.energy_discovery.source_unavailability_reasons.assert_called_once_with(
                dormant_source_ids=frozenset(("pv-source-a", "pv-source-b"))
            )
            queue_class_health.assert_called_once_with(effective, 2000.0)
            adapter.operation_broker.health.assert_called_once_with(now=1000.0)
            effective_gui_max_age_seconds.assert_called_once_with(_thresholds())
            max_core_read_age.assert_called_once_with(freshness)
            stale_core_read_keys.assert_called_once_with(
                freshness,
                process_health_module.CORE_ENERGY_READ_KEYS,
                max_age_seconds=adapter.slo_core_read_max_age_seconds,
            )

            adapter.circuit.health.return_value = {}
            adapter.health_role.slo_snapshot.return_value = {}
            adapter.discovery.next_scan_monotonic = 900.0
            with (
                patch.object(process_health_module.time, "time", return_value=2000.0),
                patch.object(process_health_module.time, "monotonic", return_value=1000.0),
                patch.object(process_health_module, "backpressure_snapshot", return_value={"state": "ok"}) as defaults,
                patch.object(process_health_module, "operational_health_state", return_value="ok") as operational,
                patch.object(process_health_module, "performance_health_state", return_value="ok") as performance,
            ):
                missing_states = adapter.health_role.control_snapshot()

            defaults.assert_called_once_with(
                circuit_state="ok",
                queue_health=missing_states.health["queues"],
                slo={},
                queue_max_age_seconds=adapter.slo_queue_max_age_seconds,
            )
            operational.assert_called_once_with("ok")
            performance.assert_called_once_with(
                slo_state="violated",
                resource_state="resource-busy",
                backpressure_state="ok",
                resource_protective=False,
            )
            self.assertEqual(missing_states.health["discovery_next_scan_in_s"], 0.0)

    def test_slo_observed_preserves_field_groups_and_metric_sources(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            adapter = _adapter(temp_dir)
            eventloop = {
                "max_glib_callback_lateness_ms_60s": 71.0,
                "max_tick_gap_ms_60s": 99.0,
            }
            cache_freshness = {"cache": "sentinel"}
            queue_metrics = {"oldest_slo_command_age_s": 17.0}
            session_fields = {"session_time_s", "session_energy_kwh"}
            all_fields = {"all-health-fields"}
            adapter.health_role.max_publication_field_age = MagicMock(side_effect=(1.0, 2.0, 3.0))
            adapter.health_role.gui_session_freshness_fields = MagicMock(return_value=session_fields)
            adapter.health_role.gui_freshness_fields = MagicMock(return_value=all_fields)
            adapter.health_role.missing_publication_field_count = MagicMock(side_effect=(4.0, 5.0, 6.0, 7.0))
            adapter.tick_health.snapshot = MagicMock()

            with ExitStack() as stack:
                max_core_read_age = _patch_result(stack, "max_core_read_age", 8.0)
                core_read_missing_count = _patch_result(stack, "core_read_missing_count", 9.0)
                core_read_nonfresh_count = _patch_result(stack, "core_read_nonfresh_count", 10.0)
                observed = adapter.health_role.slo_observed(
                    queue_metrics,
                    cache_freshness,
                    1234.0,
                    eventloop=eventloop,
                )

            self.assertEqual(
                observed,
                {
                    "gui_max_age_s": 3.0,
                    "gui_measurement_max_age_s": 1.0,
                    "gui_control_max_age_s": 2.0,
                    "gui_session_max_age_s": 3.0,
                    "gui_missing_field_count": 4.0,
                    "gui_measurement_missing_field_count": 5.0,
                    "gui_control_missing_field_count": 6.0,
                    "gui_session_missing_field_count": 7.0,
                    "core_read_max_age_s": 8.0,
                    "core_read_missing_count": 9.0,
                    "core_read_nonfresh_count": 10.0,
                    "queue_oldest_age_s": 17.0,
                    "mainloop_max_gap_ms_60s": 71.0,
                },
            )
            self.assertEqual(
                adapter.health_role.max_publication_field_age.call_args_list,
                [
                    call(process_health_module.GUI_MEASUREMENT_FRESHNESS_FIELDS, 1234.0),
                    call(
                        process_health_module.GUI_CONTROL_FRESHNESS_FIELDS,
                        1234.0,
                        service_heartbeat_fields=(process_health_module.GUI_CONTROL_FRESHNESS_FIELDS),
                    ),
                    call(session_fields, 1234.0),
                ],
            )
            adapter.health_role.gui_session_freshness_fields.assert_called_once_with(1234.0)
            adapter.health_role.gui_freshness_fields.assert_called_once_with(1234.0)
            self.assertEqual(
                adapter.health_role.missing_publication_field_count.call_args_list,
                [
                    call(all_fields),
                    call(process_health_module.GUI_MEASUREMENT_FRESHNESS_FIELDS),
                    call(process_health_module.GUI_CONTROL_FRESHNESS_FIELDS),
                    call(session_fields),
                ],
            )
            max_core_read_age.assert_called_once_with(cache_freshness)
            core_read_missing_count.assert_called_once_with(cache_freshness)
            core_read_nonfresh_count.assert_called_once_with(cache_freshness)
            adapter.tick_health.snapshot.assert_not_called()

    def test_slo_observed_samples_tick_health_when_eventloop_is_omitted(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            adapter = _adapter(temp_dir)
            adapter.tick_health.snapshot = MagicMock(return_value={"max_tick_gap_ms_60s": 19.0})
            adapter.health_role.max_publication_field_age = MagicMock(return_value=0.0)
            adapter.health_role.missing_publication_field_count = MagicMock(return_value=0.0)

            observed = adapter.health_role.slo_observed({}, {}, 456.0)

            adapter.tick_health.snapshot.assert_called_once_with(now=456.0)
            self.assertEqual(observed["mainloop_max_gap_ms_60s"], 19.0)

    def test_fresh_field_uses_strict_deadline_and_exact_observation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            adapter = _adapter(temp_dir)
            observation = object()
            adapter.publication_registry.evcs_field_observation = MagicMock(return_value=observation)
            adapter.health_role.slo_thresholds = MagicMock(return_value=_thresholds())

            publication_field_age = MagicMock(side_effect=(8.25, 8.26))
            with ExitStack() as stack:
                stack.enter_context(patch.object(process_health_module, "publication_field_age", publication_field_age))
                effective_gui_max_age_seconds = _patch_result(stack, "effective_gui_max_age_seconds", 8.25)
                publication_field_float = _patch_result(stack, "publication_field_float", 42.5)
                self.assertEqual(adapter.health_role.fresh_evcs_field_float("ac_power_w", 100.0), 42.5)
                self.assertEqual(adapter.health_role.fresh_evcs_field_float("ac_power_w", 101.0), 0.0)

            self.assertEqual(
                adapter.publication_registry.evcs_field_observation.call_args_list,
                [call("ac_power_w"), call("ac_power_w")],
            )
            self.assertEqual(
                publication_field_age.call_args_list,
                [call(observation, 100.0), call(observation, 101.0)],
            )
            self.assertEqual(
                effective_gui_max_age_seconds.call_args_list,
                [call(_thresholds()), call(_thresholds())],
            )
            publication_field_float.assert_called_once_with(observation)

    def test_regulation_forwards_exact_control_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            adapter = _adapter(temp_dir)
            control = GatewayControlSnapshot(
                captured_at=2000.0,
                monotonic_at=1000.0,
                health={"operational_state": "degraded", "state": "protective"},
                queue_age_seconds=17.0,
                core_read_age_seconds=8.0,
                eventloop_gap_ms=71.0,
                eventloop_max_duration_ms=33.0,
                resource_state="resource-busy",
                pressure_state="slow",
                stale_core_reads=("battery_soc", "pv_power_w"),
                critical_read_operations=2,
                critical_queue_operations=3,
                operation_p95_ms=17.0,
            )
            adapter.health_role.slo_thresholds = MagicMock(return_value=_thresholds())
            adapter.write_scheduler.set_dynamic_local_publish_burst = MagicMock()
            adapter.read_scheduler.expedite_healthy = MagicMock()
            adapter.health_role.suspend_advisory_work = MagicMock()

            with patch.object(
                process_health_regulation_module,
                "regulated_publish_burst",
                return_value=4,
            ) as regulated_publish_burst:
                returned = adapter.health_role.apply_slo_regulation(control)

            self.assertIs(returned, control)
            regulated_publish_burst.assert_called_once_with(
                queue_age=17.0,
                eventloop_gap_ms=71.0,
                base_burst=adapter.write_scheduler.local_publish_burst_limit,
                thresholds=_thresholds(),
                pressure_state="slow",
            )
            adapter.write_scheduler.set_dynamic_local_publish_burst.assert_called_once_with(
                4,
                pressure_state="slow",
            )
            adapter.read_scheduler.expedite_healthy.assert_called_once_with(("battery_soc", "pv_power_w"))
            adapter.health_role.suspend_advisory_work.assert_called_once_with(
                monotonic_at=1000.0,
                captured_at=2000.0,
            )

            adapter.health_role.apply_slo_regulation(control)
            regulated_publish_burst.assert_called_once()
            adapter.read_scheduler.expedite_healthy.assert_called_once()
            adapter.health_role.suspend_advisory_work.assert_called_once()

    def test_regulation_uses_fallback_state_and_preserves_healthy_work(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            adapter = _adapter(temp_dir)
            adapter.write_scheduler.set_dynamic_local_publish_burst = MagicMock()
            adapter.read_scheduler.expedite_healthy = MagicMock()
            adapter.health_role.suspend_advisory_work = MagicMock()
            fallback = GatewayControlSnapshot(
                captured_at=20.0,
                monotonic_at=10.0,
                health={"state": "degraded"},
                queue_age_seconds=0.0,
                core_read_age_seconds=0.0,
                eventloop_gap_ms=0.0,
                eventloop_max_duration_ms=0.0,
                resource_state="ok",
                pressure_state="ok",
                stale_core_reads=(),
                critical_read_operations=0,
                critical_queue_operations=0,
                operation_p95_ms=0.0,
            )
            healthy = GatewayControlSnapshot(
                captured_at=40.0,
                monotonic_at=30.0,
                health={},
                queue_age_seconds=0.0,
                core_read_age_seconds=0.0,
                eventloop_gap_ms=0.0,
                eventloop_max_duration_ms=0.0,
                resource_state="ok",
                pressure_state="ok",
                stale_core_reads=(),
                critical_read_operations=0,
                critical_queue_operations=0,
                operation_p95_ms=0.0,
            )

            adapter.health_role.apply_slo_regulation(fallback)
            adapter.health_role.apply_slo_regulation(healthy)

            adapter.read_scheduler.expedite_healthy.assert_not_called()
            adapter.health_role.suspend_advisory_work.assert_called_once_with(
                monotonic_at=10.0,
                captured_at=20.0,
            )

    def test_advisory_suspension_preserves_the_in_flight_command_owner(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            adapter = _adapter(temp_dir)
            owned = {"queue_class": "diagnostic", "kind": "introspect"}
            removable = {"queue_class": "discovery", "kind": "refresh"}
            required = {"queue_class": "remote-write", "kind": "set-value"}
            pending = MagicMock(
                physical=(
                    ("owned.json", owned),
                    ("removable.json", removable),
                    ("required.json", required),
                )
            )
            adapter.write_scheduler.pending_snapshot = MagicMock(return_value=pending)
            adapter.operation_broker.owns_path = MagicMock(
                side_effect=lambda path: path == "owned.json"
            )
            adapter.write_scheduler.remove_pending = MagicMock(return_value=True)
            adapter.write_scheduler.record_lifecycle = MagicMock()

            adapter.health_role.suspend_advisory_work(
                monotonic_at=100.0,
                captured_at=200.0,
            )

            self.assertEqual(
                adapter.operation_broker.owns_path.call_args_list,
                [call("owned.json"), call("removable.json"), call("required.json")],
            )
            adapter.write_scheduler.remove_pending.assert_called_once_with(
                "removable.json",
                removable,
            )
            adapter.write_scheduler.record_lifecycle.assert_called_once_with(
                removable,
                "dropped",
            )

    def test_publication_age_forwards_service_heartbeat_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            adapter = _adapter(temp_dir)
            fields = {"mode", "status"}
            heartbeat_fields = {"mode"}

            with patch.object(
                process_health_module,
                "max_publication_field_age",
                return_value=6.5,
            ) as max_publication_field_age:
                result = adapter.health_role.max_publication_field_age(
                    fields,
                    300.0,
                    service_heartbeat_fields=heartbeat_fields,
                )

            self.assertEqual(result, 6.5)
            max_publication_field_age.assert_called_once_with(
                adapter.publication_registry,
                fields,
                300.0,
                service_heartbeat_fields=heartbeat_fields,
            )

    def test_resource_protective_classification_uses_causal_evidence(self) -> None:
        load_only = {"active": True, "causes": ["load"]}
        cpu_pressure = {"active": True, "causes": ["cpu"]}
        memory_pressure = {"active": True, "causes": ["memory"]}

        self.assertFalse(
            process_health_module._resource_pressure_is_protective(
                "constrained",
                load_only,
            )
        )
        self.assertTrue(
            process_health_module._resource_pressure_is_protective(
                "constrained",
                cpu_pressure,
            )
        )
        self.assertTrue(
            process_health_module._resource_pressure_is_protective(
                "constrained",
                memory_pressure,
            )
        )
        self.assertEqual(
            process_health_module._protective_cause(
                aggregate_state="protective",
                operational_state="ok",
                backpressure_state="ok",
                resource_protective=True,
                resource_evidence=memory_pressure,
            ),
            "resource-memory",
        )
        self.assertEqual(
            process_health_module._protective_cause(
                aggregate_state="degraded",
                operational_state="ok",
                backpressure_state="ok",
                resource_protective=False,
                resource_evidence=load_only,
            ),
            "",
        )

# SPDX-License-Identifier: GPL-3.0-or-later
"""Exact asynchronous discovery and cache-publication IO contracts."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from tests.support.dbus_gateway_adapter_harness import (
    GatewayAdapterContractCase,
    install_mock,
    process_io_module,
)
from venus_evcharger.dbus_adapter.process.health import GatewayControlSnapshot
from venus_evcharger.ipc.energy import EnergyTopologySnapshot


def _topology(generation: int, captured_at: float) -> EnergyTopologySnapshot:
    return EnergyTopologySnapshot(
        generation=generation,
        captured_at=captured_at,
        sources=(),
    )


def _control_snapshot(
    *,
    captured_at: float = 125.0,
    monotonic_at: float = 45.0,
) -> GatewayControlSnapshot:
    return GatewayControlSnapshot(
        captured_at=captured_at,
        monotonic_at=monotonic_at,
        health={"state": "ok", "marker": 17},
        queue_age_seconds=1.0,
        core_read_age_seconds=2.0,
        eventloop_gap_ms=3.0,
        eventloop_max_duration_ms=4.0,
        resource_state="ok",
        pressure_state="ok",
        stale_core_reads=(),
    )


class DbusAdapterProcessIoAsyncContracts(GatewayAdapterContractCase):
    """Pin IO arguments that determine scheduling, health, and freshness."""

    def test_discovery_submits_exact_async_dbus_operation(self) -> None:
        with self.adapter_scenario() as scenario:
            adapter = scenario.adapter
            due = install_mock(adapter.discovery, "due", MagicMock(return_value=True))
            submit = install_mock(adapter.operation_broker, "submit", MagicMock(return_value=7))

            with patch.object(process_io_module.time, "monotonic", return_value=41.25):
                self.assertTrue(adapter.io_role.refresh_services_if_due_once())

            due.assert_called_once_with(
                monotonic_at=41.25,
                priority_allowed=adapter.circuit.allows_priority,
            )
            operation = submit.call_args.args[0]
            self.assertEqual(operation.rate_kind, "read")
            self.assertEqual(operation.metric_kind, "discovery")
            self.assertEqual(operation.source, "org.freedesktop.DBus/ListNames")
            self.assertEqual(operation.priority, "discovery")
            self.assertEqual(operation.timeout_seconds, 1.0)
            self.assertFalse(operation.optional_failure)
            self.assertEqual(operation.on_success, adapter.io_role._complete_service_discovery)
            self.assertEqual(operation.on_error, adapter.io_role._fail_service_discovery)

    def test_immediate_discovery_transport_failure_is_handled_as_performed_work(self) -> None:
        with self.adapter_scenario() as scenario:
            adapter = scenario.adapter
            install_mock(adapter.discovery, "due", MagicMock(return_value=True))
            error = OSError("transport unavailable")
            install_mock(
                adapter.operation_broker,
                "submit",
                MagicMock(side_effect=error),
            )
            fail = install_mock(
                adapter.io_role,
                "_fail_service_discovery",
                MagicMock(),
            )

            self.assertTrue(adapter.io_role.refresh_services_if_due_once())

            fail.assert_called_once_with(error)

    def test_discovery_success_forwards_one_coherent_timestamp_pair(self) -> None:
        with self.adapter_scenario() as scenario:
            adapter = scenario.adapter
            update_cache = install_mock(adapter.cache, "update_services", MagicMock())
            update_energy = install_mock(adapter.energy_discovery, "update_services", MagicMock())
            early_rescan = install_mock(
                adapter.energy_discovery,
                "needs_early_pv_rescan",
                MagicMock(return_value=True),
            )
            record_success = install_mock(adapter.discovery, "record_success", MagicMock())
            record_error = install_mock(adapter.discovery, "record_error", MagicMock())

            with (
                patch.object(process_io_module.time, "time", return_value=170.5),
                patch.object(process_io_module.time, "monotonic", return_value=70.25),
            ):
                adapter.io_role._complete_service_discovery(("svc.a", b"svc.b"))

            services = ["svc.a", "b'svc.b'"]
            update_cache.assert_called_once_with(services, now=170.5)
            update_energy.assert_called_once_with(services, captured_at=170.5)
            early_rescan.assert_called_once_with()
            record_success.assert_called_once_with(
                monotonic_at=70.25,
                captured_at=170.5,
                needs_early_rescan=True,
            )
            record_error.assert_not_called()

    def test_invalid_discovery_reply_records_exact_callback_timestamps(self) -> None:
        with self.adapter_scenario() as scenario:
            adapter = scenario.adapter
            update_cache = install_mock(adapter.cache, "update_services", MagicMock())
            update_energy = install_mock(adapter.energy_discovery, "update_services", MagicMock())
            record_success = install_mock(adapter.discovery, "record_success", MagicMock())
            record_error = install_mock(adapter.discovery, "record_error", MagicMock())

            with (
                patch.object(process_io_module.time, "time", return_value=171.5),
                patch.object(process_io_module.time, "monotonic", return_value=71.25),
            ):
                adapter.io_role._complete_service_discovery("not-a-service-list")

            error = record_error.call_args.args[0]
            self.assertIsInstance(error, TypeError)
            self.assertEqual(
                str(error),
                "DBus ListNames returned a non-iterable service list",
            )
            record_error.assert_called_once_with(
                error,
                monotonic_at=71.25,
                captured_at=171.5,
            )
            update_cache.assert_not_called()
            update_energy.assert_not_called()
            record_success.assert_not_called()

    def test_publish_cache_preserves_snapshot_identity_through_every_stage(self) -> None:
        with self.adapter_scenario() as scenario:
            adapter = scenario.adapter
            control = _control_snapshot()
            energy = _topology(1, control.captured_at)
            topology = _topology(2, control.captured_at)
            health = _topology(3, control.captured_at)
            publish_energy = install_mock(
                adapter.io_role,
                "_publish_energy_if_due",
                MagicMock(return_value=energy),
            )
            publish_topology = install_mock(
                adapter.io_role,
                "_publish_topology_if_changed",
                MagicMock(return_value=topology),
            )
            publish_health = install_mock(
                adapter.io_role,
                "_publish_health_if_due",
                MagicMock(return_value=health),
            )
            publish_full = install_mock(
                adapter.io_role,
                "_publish_full_cache_if_due",
                MagicMock(),
            )

            with patch.object(process_io_module.time, "monotonic", return_value=55.5):
                adapter.io_role.publish_cache(control)

            self.assertEqual(adapter.cache.health["marker"], 17)
            publish_energy.assert_called_once_with(55.5, 125.0)
            publish_topology.assert_called_once_with(energy, 125.0)
            publish_health.assert_called_once_with(
                topology,
                control=control,
                now=55.5,
            )
            publish_full.assert_called_once_with(
                health,
                captured_at=125.0,
                now=55.5,
            )

    def test_changed_topology_is_created_once_with_capture_time(self) -> None:
        with self.adapter_scenario() as scenario:
            adapter = scenario.adapter
            expected = _topology(9, 130.0)
            adapter.energy_discovery._generation = 9
            adapter._last_topology_generation = 8
            snapshot = install_mock(
                adapter.energy_discovery,
                "topology_snapshot",
                MagicMock(return_value=expected),
            )
            set_snapshot = install_mock(
                adapter.cache,
                "set_energy_topology_snapshot",
                MagicMock(),
            )
            write_snapshot = install_mock(
                adapter.cache,
                "write_energy_topology_snapshot",
                MagicMock(),
            )

            result = adapter.io_role._publish_topology_if_changed(None, 130.0)

            self.assertIs(result, expected)
            snapshot.assert_called_once_with(captured_at=130.0)
            set_snapshot.assert_called_once_with(expected)
            write_snapshot.assert_called_once_with()
            self.assertEqual(adapter._last_topology_generation, 9)

    def test_health_publication_forwards_exact_snapshot_contract(self) -> None:
        with self.adapter_scenario() as scenario:
            adapter = scenario.adapter
            control = _control_snapshot(captured_at=135.0, monotonic_at=75.0)
            expected = _topology(4, control.captured_at)
            adapter._last_health_publish_monotonic = 0.0
            snapshot = install_mock(
                adapter.energy_discovery,
                "topology_snapshot",
                MagicMock(return_value=expected),
            )
            write_health = install_mock(adapter.cache, "write_health_snapshot", MagicMock())
            diagnostics = install_mock(
                adapter.diagnostics_role,
                "write_gateway_diagnostics",
                MagicMock(),
            )
            append_health = install_mock(adapter.health_role, "append_health_log", MagicMock())

            result = adapter.io_role._publish_health_if_due(
                None,
                control=control,
                now=80.0,
            )

            self.assertIs(result, expected)
            snapshot.assert_called_once_with(captured_at=135.0)
            write_health.assert_called_once_with(now=135.0)
            diagnostics.assert_called_once_with(
                health=control.health,
                topology=expected,
                captured_at=135.0,
                captured_monotonic=75.0,
            )
            append_health.assert_called_once_with(control.health)
            self.assertEqual(adapter._last_health_publish_monotonic, 80.0)

    def test_full_cache_publication_commits_exact_snapshot_and_cursor(self) -> None:
        with self.adapter_scenario() as scenario:
            adapter = scenario.adapter
            expected = _topology(5, 140.0)
            install_mock(adapter.io_role, "_full_cache_publish_due", MagicMock(return_value=True))
            snapshot = install_mock(
                adapter.energy_discovery,
                "topology_snapshot",
                MagicMock(return_value=expected),
            )
            set_snapshot = install_mock(
                adapter.cache,
                "set_energy_topology_snapshot",
                MagicMock(),
            )
            write_cache = install_mock(adapter.cache, "write_cache_snapshot", MagicMock())
            write_introspection = install_mock(
                adapter.introspection_snapshot_role,
                "write_introspection_snapshot",
                MagicMock(),
            )

            adapter.io_role._publish_full_cache_if_due(
                None,
                captured_at=140.0,
                now=90.0,
            )

            snapshot.assert_called_once_with(captured_at=140.0)
            set_snapshot.assert_called_once_with(expected)
            write_cache.assert_called_once_with(now=140.0)
            write_introspection.assert_called_once_with()
            self.assertEqual(adapter._last_cache_publish_monotonic, 90.0)
            self.assertEqual(adapter._last_cache_publish_sequence, adapter.cache.sequence)

    def test_publication_deadlines_are_inclusive_and_zero_means_unpublished(self) -> None:
        self.assertTrue(process_io_module._publish_due(0.0, 0.0, 10.0))
        self.assertFalse(process_io_module._publish_due(1.0, 1.0, 10.0))
        self.assertTrue(process_io_module._publish_due(7.0, 2.0, 5.0))
        self.assertFalse(process_io_module._publish_due(6.999, 2.0, 5.0))

        with self.adapter_scenario() as scenario:
            adapter = scenario.adapter
            adapter.cache_publish_interval_seconds = 10.0
            adapter.cache_dirty_publish_interval_seconds = 2.0
            adapter._last_cache_publish_sequence = adapter.cache.sequence
            adapter._last_cache_publish_monotonic = 0.0
            self.assertTrue(adapter.io_role._full_cache_publish_due(0.5))

            adapter._last_cache_publish_monotonic = 1.0
            self.assertFalse(adapter.io_role._full_cache_publish_due(1.0))

            adapter._last_cache_publish_monotonic = 5.0
            self.assertTrue(adapter.io_role._full_cache_publish_due(15.0))

            adapter._last_cache_publish_sequence = adapter.cache.sequence - 1
            self.assertTrue(adapter.io_role._full_cache_publish_due(7.0))
            self.assertFalse(adapter.io_role._full_cache_publish_due(6.999))


if __name__ == "__main__":
    import unittest

    unittest.main()

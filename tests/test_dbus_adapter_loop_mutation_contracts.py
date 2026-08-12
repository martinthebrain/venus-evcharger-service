# SPDX-License-Identifier: GPL-3.0-or-later
"""Mutation contracts for the DBus adapter work-loop boundary."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, call, patch

from tests.support.dbus_gateway_adapter_harness import (
    DbusAdapter,
    gateway_paths,
    install_mock,
    process_loop_module,
)
from venus_evcharger.dbus_adapter.process.health import GatewayControlSnapshot
from venus_evcharger.dbus_adapter.tick_policy import TickDemand


def _control(*, resource_state: str = "ok", max_duration_ms: float = 0.0) -> GatewayControlSnapshot:
    return GatewayControlSnapshot(
        captured_at=1.0,
        monotonic_at=2.0,
        health={},
        queue_age_seconds=0.0,
        core_read_age_seconds=0.0,
        eventloop_gap_ms=0.0,
        eventloop_max_duration_ms=max_duration_ms,
        resource_state=resource_state,
        pressure_state="ok",
        stale_core_reads=(),
        critical_read_operations=0,
        critical_queue_operations=0,
        operation_p95_ms=0.0,
    )


class DbusAdapterLoopMutationContracts(unittest.TestCase):
    def _adapter(self, temp_dir: str, name: str = "run") -> DbusAdapter:
        config_path = Path(temp_dir) / f"{name}.ini"
        config_path.write_text("[DEFAULT]\n", encoding="utf-8")
        return DbusAdapter(
            str(config_path),
            paths=gateway_paths(str(Path(temp_dir) / name)),
        )

    def test_tick_transaction_has_one_stable_order_and_true_result(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            adapter = self._adapter(temp_dir)
            adapter._next_work_tick_monotonic = 0.0
            events: list[str] = []
            control = _control()

            def capture_snapshot() -> GatewayControlSnapshot:
                events.append("snapshot")
                return control

            def record_adaptive(_snapshot: GatewayControlSnapshot | None) -> None:
                events.append("adaptive")

            def record_publish(_snapshot: GatewayControlSnapshot) -> None:
                events.append("publish")

            def record_health(
                *,
                duration_ms: float,
                expected_interval_s: float,
                scheduled_at: float,
                now: float,
            ) -> None:
                del duration_ms, expected_interval_s, scheduled_at, now
                events.append("record")

            install_mock(
                adapter.write_scheduler,
                "begin_tick",
                MagicMock(side_effect=lambda: events.append("begin")),
            )
            install_mock(
                adapter.loop_role,
                "process_one_dbus_operation_once",
                MagicMock(side_effect=lambda: events.append("operation")),
            )
            install_mock(
                adapter.health_role,
                "control_snapshot",
                MagicMock(side_effect=capture_snapshot),
            )
            update_adaptive_tick = install_mock(
                adapter.loop_role,
                "update_adaptive_tick",
                MagicMock(side_effect=record_adaptive),
            )
            publish_cache = install_mock(
                adapter.io_role,
                "publish_cache",
                MagicMock(side_effect=record_publish),
            )
            install_mock(
                adapter.write_scheduler,
                "end_tick",
                MagicMock(side_effect=lambda: events.append("end")),
            )
            install_mock(
                adapter.tick_health,
                "record",
                MagicMock(side_effect=record_health),
            )

            with (
                patch.object(process_loop_module.time, "time", return_value=50.0),
                patch.object(
                    process_loop_module.time,
                    "monotonic",
                    side_effect=(10.0, 10.004, 10.006),
                ),
            ):
                result = adapter.tick()

            self.assertIs(result, True)
            self.assertEqual(
                events,
                ["begin", "operation", "snapshot", "adaptive", "publish", "end", "record"],
            )
            update_adaptive_tick.assert_called_once_with(control)
            publish_cache.assert_called_once_with(control)
            self.assertEqual(adapter._last_tick_at, 50.0)
            self.assertEqual(adapter._last_tick_monotonic, 10.0)
            self.assertAlmostEqual(adapter._last_tick_duration_ms, 4.0)
            self.assertAlmostEqual(
                adapter._next_work_tick_monotonic,
                10.0 + adapter.tick_seconds,
            )

    def test_tick_return_contract_distinguishes_stop_wait_and_stop_during_work(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            stopped = self._adapter(temp_dir, "stopped")
            stopped._stop = True
            close_socket = install_mock(stopped.socket_role, "close_socket", MagicMock())
            stopped_begin_tick = install_mock(stopped.write_scheduler, "begin_tick", MagicMock())
            with patch.object(process_loop_module.time, "monotonic", return_value=1.0):
                self.assertIs(stopped.tick(), False)
            close_socket.assert_called_once_with()
            stopped_begin_tick.assert_not_called()

            waiting = self._adapter(temp_dir, "waiting")
            waiting._next_work_tick_monotonic = 2.0
            waiting_begin_tick = install_mock(waiting.write_scheduler, "begin_tick", MagicMock())
            with patch.object(process_loop_module.time, "monotonic", return_value=1.0):
                self.assertIs(waiting.tick(), True)
            waiting_begin_tick.assert_not_called()

            stopping = self._adapter(temp_dir, "stopping")
            stopping._next_work_tick_monotonic = 0.0
            control = _control()
            events: list[str] = []

            def capture_snapshot() -> GatewayControlSnapshot:
                events.append("snapshot")
                return control

            def record_adaptive(_snapshot: GatewayControlSnapshot | None) -> None:
                events.append("adaptive")

            def record_publish(_snapshot: GatewayControlSnapshot) -> None:
                events.append("publish")

            def record_health(
                *,
                duration_ms: float,
                expected_interval_s: float,
                scheduled_at: float,
                now: float,
            ) -> None:
                del duration_ms, expected_interval_s, scheduled_at, now
                events.append("record")

            install_mock(
                stopping.write_scheduler,
                "begin_tick",
                MagicMock(side_effect=lambda: events.append("begin")),
            )

            def stop_during_operation() -> None:
                events.append("operation")
                stopping._stop = True

            install_mock(
                stopping.loop_role,
                "process_one_dbus_operation_once",
                MagicMock(side_effect=stop_during_operation),
            )
            install_mock(
                stopping.health_role,
                "control_snapshot",
                MagicMock(side_effect=capture_snapshot),
            )
            install_mock(
                stopping.loop_role,
                "update_adaptive_tick",
                MagicMock(side_effect=record_adaptive),
            )
            install_mock(
                stopping.io_role,
                "publish_cache",
                MagicMock(side_effect=record_publish),
            )
            install_mock(
                stopping.write_scheduler,
                "end_tick",
                MagicMock(side_effect=lambda: events.append("end")),
            )
            install_mock(
                stopping.tick_health,
                "record",
                MagicMock(side_effect=record_health),
            )
            with patch.object(
                process_loop_module.time,
                "monotonic",
                side_effect=(3.0, 3.001, 3.002),
            ):
                self.assertIs(stopping.tick(), False)
            self.assertEqual(
                events,
                ["begin", "operation", "snapshot", "adaptive", "publish", "end", "record"],
            )

    def test_failed_begin_never_ends_transaction_but_still_records_tick(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            adapter = self._adapter(temp_dir)
            adapter._next_work_tick_monotonic = 0.0
            error = RuntimeError("begin failed")
            events: list[str] = []

            def record_health(
                *,
                duration_ms: float,
                expected_interval_s: float,
                scheduled_at: float,
                now: float,
            ) -> None:
                del duration_ms, expected_interval_s, scheduled_at, now
                events.append("record")

            install_mock(
                adapter.write_scheduler,
                "begin_tick",
                MagicMock(side_effect=error),
            )
            end_tick = install_mock(
                adapter.write_scheduler,
                "end_tick",
                MagicMock(side_effect=lambda: events.append("end")),
            )
            process_operation = install_mock(
                adapter.loop_role,
                "process_one_dbus_operation_once",
                MagicMock(side_effect=lambda: events.append("operation")),
            )
            install_mock(
                adapter.tick_health,
                "record",
                MagicMock(side_effect=record_health),
            )
            with (
                patch.object(process_loop_module.logging, "exception") as log_exception,
                patch.object(
                    process_loop_module.time,
                    "monotonic",
                    side_effect=(4.0, 4.001, 4.002),
                ),
            ):
                self.assertIs(adapter.tick(), True)

            self.assertEqual(events, ["record"])
            end_tick.assert_not_called()
            process_operation.assert_not_called()
            log_exception.assert_called_once_with(
                "Gateway work tick failed outside the DBus circuit: %s",
                error,
            )

    def test_adaptive_tick_seconds_obeys_exact_floors_caps_and_precedence(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            adapter = self._adapter(temp_dir)
            cases = (
                (0.2, 1.0, "ok", "ok", 0.2),
                (0.2, 1.0, "protective", "ok", 1.0),
                (0.2, 1.0, "ok", "constrained", 1.0),
                (0.6, 1.0, "ok", "constrained", 1.0),
                (0.2, 0.4, "ok", "constrained", 0.4),
                (0.1, 1.0, "degraded", "ok", 0.5),
                (0.4, 2.0, "degraded", "ok", 1.0),
                (0.4, 0.75, "degraded", "ok", 0.75),
                (0.1, 1.0, "ok", "busy", 0.3),
                (0.4, 2.0, "ok", "busy", 0.6),
                (0.4, 0.5, "ok", "busy", 0.5),
            )
            for minimum, maximum, circuit, resource, expected in cases:
                with self.subTest(
                    minimum=minimum,
                    maximum=maximum,
                    circuit=circuit,
                    resource=resource,
                ):
                    adapter.min_tick_seconds = minimum
                    adapter.max_tick_seconds = maximum
                    self.assertAlmostEqual(
                        adapter.loop_role.adaptive_tick_seconds(
                            circuit_state=circuit,
                            resource_state=resource,
                        ),
                        expected,
                    )

            adapter.min_tick_seconds = 0.2
            adapter.max_tick_seconds = 1.0
            adapter.slo_core_read_max_age_seconds = 5.0
            adapter.slo_queue_max_age_seconds = 10.0
            self.assertAlmostEqual(
                adapter.loop_role.adaptive_tick_seconds(
                    circuit_state="ok",
                    resource_state="constrained",
                    demand=TickDemand(
                        critical_read_operations=2,
                        core_read_age_seconds=4.0,
                        operation_p95_ms=100.0,
                    ),
                ),
                0.3,
            )
            self.assertEqual(
                adapter.loop_role.adaptive_tick_seconds(
                    circuit_state="protective",
                    resource_state="constrained",
                    demand=TickDemand(
                        critical_queue_operations=10,
                        queue_age_seconds=20.0,
                    ),
                ),
                1.0,
            )
            self.assertEqual(
                adapter.loop_role.adaptive_tick_seconds(
                    circuit_state="ok",
                    resource_state="constrained",
                    demand=TickDemand(
                        critical_queue_operations=1,
                        queue_age_seconds=9.0,
                    ),
                ),
                0.8,
            )

    def test_adaptive_update_forwards_snapshot_and_uses_strict_busy_threshold(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            adapter = self._adapter(temp_dir)
            adapter.min_tick_seconds = 0.2
            adapter.max_tick_seconds = 1.0
            adapter.slo_mainloop_gap_max_ms = 100.0
            boundary = _control(max_duration_ms=100.0)
            over_limit = _control(max_duration_ms=100.001)
            apply_regulation = install_mock(
                adapter.health_role,
                "apply_slo_regulation",
                MagicMock(side_effect=(boundary, over_limit)),
            )

            adapter.loop_role.update_adaptive_tick(boundary)
            self.assertEqual(adapter.tick_seconds, 0.2)
            adapter.loop_role.update_adaptive_tick(over_limit)
            self.assertAlmostEqual(adapter.tick_seconds, 0.3)
            self.assertEqual(
                apply_regulation.call_args_list,
                [call(boundary), call(over_limit)],
            )

    def test_adaptive_update_forwards_complete_tick_demand(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            adapter = self._adapter(temp_dir)
            control = GatewayControlSnapshot(
                captured_at=1.0,
                monotonic_at=2.0,
                health={},
                queue_age_seconds=7.0,
                core_read_age_seconds=3.0,
                eventloop_gap_ms=0.0,
                eventloop_max_duration_ms=0.0,
                resource_state="constrained",
                pressure_state="slow",
                stale_core_reads=("grid_power_w",),
                critical_read_operations=2,
                critical_queue_operations=4,
                operation_p95_ms=125.0,
            )
            install_mock(
                adapter.health_role,
                "apply_slo_regulation",
                MagicMock(return_value=control),
            )
            install_mock(adapter.circuit, "state", MagicMock(return_value="degraded"))
            choose_tick = install_mock(
                adapter.loop_role,
                "adaptive_tick_seconds",
                MagicMock(return_value=0.45),
            )

            adapter.loop_role.update_adaptive_tick(control)

            self.assertEqual(adapter.tick_seconds, 0.45)
            choose_tick.assert_called_once()
            self.assertEqual(
                choose_tick.call_args.kwargs["circuit_state"],
                "degraded",
            )
            self.assertEqual(
                choose_tick.call_args.kwargs["resource_state"],
                "constrained",
            )
            demand = choose_tick.call_args.kwargs["demand"]
            self.assertEqual(
                (
                    demand.critical_read_operations,
                    demand.critical_queue_operations,
                    demand.core_read_age_seconds,
                    demand.queue_age_seconds,
                    demand.operation_p95_ms,
                ),
                (2, 4, 3.0, 7.0, 125.0),
            )


if __name__ == "__main__":
    unittest.main()

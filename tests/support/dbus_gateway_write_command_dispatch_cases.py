# SPDX-License-Identifier: GPL-3.0-or-later
"""Semantic gateway command dispatch and persistence scenarios."""

from __future__ import annotations

from tests.support.dbus_gateway_adapter_harness import (
    DbusOperationDeferred,
    GatewayAdapterContractCase,
    MagicMock,
    Path,
    builtins,
    evcs_publication,
    install_mock,
    patch,
    time,
    unittest,
)
from venus_evcharger.ipc.gateway_operations import gx_relay_refresh_command


class GatewayWriteCommandDispatchCases(GatewayAdapterContractCase):
    """Exercise semantic dispatch, retry, and persistence contracts."""

    def test_process_one_defers_protected_and_failed_commands(self) -> None:
        with self.adapter_scenario() as scenario:
            adapter = scenario.adapter
            adapter.circuit.protective_until = time.time() + 10.0
            command_path = adapter.commands.enqueue(evcs_publication({"connected": 1}, priority="diagnostic"))

            self.assertTrue(adapter.write_scheduler.process_one())
            self.assertTrue(Path(command_path).exists())
            adapter.write_scheduler.prune_budget(time.time() + 2.0)

            adapter.circuit.protective_until = 0.0
            process_command = install_mock(
                adapter.write_scheduler,
                "process_command",
                MagicMock(side_effect=DbusOperationDeferred("write")),
            )
            self.assertTrue(adapter.write_scheduler.process_one())
            self.assertTrue(Path(command_path).exists())

            process_command.side_effect = RuntimeError("boom")
            adapter.write_scheduler.prune_budget(time.time() + 2.0)
            self.assertTrue(adapter.write_scheduler.process_one())
            self.assertTrue(Path(command_path).exists())

    def test_process_one_can_skip_local_publication_for_a_semantic_operation(self) -> None:
        with self.adapter_scenario() as scenario:
            adapter = scenario.adapter
            local_path = adapter.commands.enqueue(evcs_publication({"mode": 1}))
            operation_path = adapter.commands.enqueue(gx_relay_refresh_command(0))
            process_command = install_mock(
                adapter.write_scheduler,
                "process_command",
                MagicMock(return_value="applied"),
            )

            self.assertTrue(adapter.write_scheduler.process_one(include_local_publish=False))

            processed = process_command.call_args.args[0]
            self.assertEqual(processed["kind"], "gx_relay_refresh")
            self.assertTrue(Path(local_path).exists())
            self.assertFalse(Path(operation_path).exists())

    def test_process_command_routes_semantic_publications_and_operations(self) -> None:
        with self.adapter_scenario() as scenario:
            adapter = scenario.adapter
            scheduler = adapter.write_scheduler
            install_mock(adapter.circuit, "allows_priority", MagicMock(return_value=True))
            process_publication = install_mock(scheduler, "process_publication", MagicMock(return_value="applied"))
            process_operation = install_mock(
                scheduler,
                "process_semantic_operation",
                MagicMock(return_value="deferred"),
            )
            process_non_write = install_mock(
                adapter,
                "process_non_write_command",
                MagicMock(return_value="dropped"),
            )
            publication = evcs_publication({"mode": 2})
            operation = gx_relay_refresh_command(1)

            self.assertEqual(scheduler.process_command(publication, command_file="publish.json"), "applied")
            process_publication.assert_called_once_with(publication)
            self.assertEqual(scheduler.process_command(operation, command_file="relay.json"), "deferred")
            process_operation.assert_called_once_with(operation, command_file="relay.json")
            unknown = {"kind": "unknown"}
            self.assertEqual(scheduler.process_command(unknown), "dropped")
            process_non_write.assert_called_once_with(unknown)

    def test_process_command_uses_diagnostic_priority_and_empty_file_defaults(self) -> None:
        with self.adapter_scenario() as scenario:
            scheduler = scenario.adapter.write_scheduler
            allows_priority = install_mock(
                scenario.adapter.circuit,
                "allows_priority",
                MagicMock(return_value=True),
            )
            dispatch = install_mock(
                scheduler,
                "_dispatch_command",
                MagicMock(return_value="applied"),
            )
            command = {"kind": "unknown"}

            self.assertEqual(scheduler.process_command(command), "applied")

            allows_priority.assert_called_once_with("diagnostic")
            dispatch.assert_called_once_with(command, command_file="")

    def test_command_kind_accepts_type_fallback_and_rejects_missing_identity(self) -> None:
        with self.adapter_scenario() as scenario:
            command_kind = scenario.adapter.write_scheduler._command_kind

            self.assertEqual(command_kind({"type": "fallback"}), "fallback")
            self.assertEqual(command_kind({}), "")

    def test_circuit_breaker_blocks_before_semantic_dispatch(self) -> None:
        with self.adapter_scenario() as scenario:
            scheduler = scenario.adapter.write_scheduler
            allows_priority = install_mock(
                scenario.adapter.circuit,
                "allows_priority",
                MagicMock(return_value=False),
            )
            process_publication = install_mock(
                scheduler,
                "process_publication",
                MagicMock(return_value="applied"),
            )
            command = evcs_publication({"mode": 2}, priority="critical")

            self.assertEqual(scheduler.process_command(command, command_file="publish.json"), "deferred")

            allows_priority.assert_called_once_with("safety")
            process_publication.assert_not_called()

    def test_lifecycle_log_failures_do_not_break_scheduling(self) -> None:
        with self.adapter_scenario() as scenario:
            adapter = scenario.adapter
            adapter.command_lifecycle_path = ""
            adapter.write_scheduler.record_lifecycle(evcs_publication(), "queued")
            health = adapter.write_scheduler.health(now=time.time())
            lifecycle_counts = health["lifecycle_counts"]
            self.assertIsInstance(lifecycle_counts, dict)
            assert isinstance(lifecycle_counts, dict)
            self.assertEqual(lifecycle_counts["queued"], 1)

            adapter.command_lifecycle_path = str(scenario.root / "lifecycle.jsonl")
            with patch.object(builtins, "open", side_effect=OSError("full")):
                adapter.write_scheduler.record_lifecycle(evcs_publication(), "dropped")

            adapter.command_lifecycle_path = "lifecycle-without-dir.jsonl"
            lifecycle_handle = unittest.mock.mock_open()
            with patch.object(builtins, "open", lifecycle_handle):
                adapter.write_scheduler.record_lifecycle(evcs_publication(), "queued")
            lifecycle_handle.assert_not_called()
